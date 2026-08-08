"""Atomic creation boundary for Hermes acquisition pipeline jobs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from tiktok_bot_core.models.entities import (
    AcquisitionCampaign,
    AcquisitionKeyword,
    PipelineJob,
)
from tiktok_bot_core.models.pipeline_states import PIPELINE_STAGES
from tiktok_bot_core.services.pipeline_jobs import PipelineJobService
from tiktok_bot_core.storage.acquisition_store import AcquisitionStore
from tiktok_bot_core.storage.database import Database, get_db


@dataclass(frozen=True)
class AcquisitionJobBundle:
    """The three persisted parts returned by one atomic creation."""

    job: PipelineJob
    campaign: AcquisitionCampaign
    keywords: tuple[AcquisitionKeyword, ...]


_CAMEL_CASE_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_KEY_WORD = re.compile(r"[a-z0-9]+")
_TOKEN_METRIC_WORDS = frozenset(
    {
        "token",
        "tokens",
        "max",
        "min",
        "input",
        "output",
        "total",
        "count",
        "budget",
        "limit",
        "usage",
        "window",
    }
)
_COOKIE_POLICY_WORDS = frozenset(
    {
        "cookie",
        "cookies",
        "policy",
        "consent",
        "enabled",
        "same",
        "site",
        "domain",
        "max",
        "age",
    }
)
_PASSWORD_POLICY_WORDS = frozenset(
    {
        "password",
        "policy",
        "min",
        "max",
        "length",
        "requirements",
        "required",
        "rotation",
        "expiry",
        "days",
        "managed",
        "externally",
    }
)


def validate_acquisition_stages(stages: Sequence[str]) -> tuple[str, ...]:
    """Require an ordered, duplicate-free acquisition Pipeline subsequence."""

    ordered = tuple(stages)
    positions = {stage: index for index, stage in enumerate(PIPELINE_STAGES)}
    valid = bool(ordered) and "collect" in ordered
    previous = -1
    if valid:
        for stage in ordered:
            if not isinstance(stage, str) or stage not in positions:
                valid = False
                break
            position = positions[stage]
            if position <= previous:
                valid = False
                break
            previous = position
    if not valid:
        raise ValueError(
            "Acquisition stages must be an ordered subsequence of "
            "PIPELINE_STAGES and include collect"
        )
    return ordered


def _config_key_words(key: str) -> tuple[str, ...]:
    separated = _CAMEL_CASE_BOUNDARY.sub(" ", key)
    return tuple(_KEY_WORD.findall(separated.casefold()))


def _is_sensitive_config_key(key: str) -> bool:
    words = _config_key_words(key)
    if not words:
        return False
    word_set = frozenset(words)

    if (
        word_set & {"auth", "authentication", "authorization"}
        and word_set
        & {
            "header",
            "headers",
            "value",
            "key",
            "token",
            "cookie",
            "password",
            "secret",
            "credential",
            "credentials",
        }
    ):
        return True

    token_words = word_set & {"token", "tokens"}
    if token_words:
        if len(word_set) > 1 and word_set <= _TOKEN_METRIC_WORDS:
            return False
        return True

    cookie_words = word_set & {"cookie", "cookies"}
    if cookie_words:
        if len(word_set) > 1 and word_set <= _COOKIE_POLICY_WORDS:
            return False
        return True

    if "password" in word_set:
        if len(word_set) > 1 and word_set <= _PASSWORD_POLICY_WORDS:
            return False
        return True

    compact = "".join(words)
    if compact.endswith(
        (
            "token",
            "cookie",
            "cookies",
            "password",
            "secret",
            "credential",
            "credentials",
        )
    ):
        return True
    if any(
        marker in compact
        for marker in (
            "apikey",
            "authorization",
            "clientsecret",
            "privatekey",
            "credential",
        )
    ):
        return True
    return "secret" in word_set


def _is_sensitive_auth_structure(key: str, value: Any) -> bool:
    container_name = "".join(_config_key_words(key))
    if container_name not in {"auth", "authentication", "oauth", "oauth2"}:
        return False
    scheme_values = {
        "apikey",
        "basic",
        "bearer",
        "digest",
        "jwt",
        "oauth",
        "oauth2",
    }
    if isinstance(value, str):
        first_word = re.split(
            r"[\s:_-]+", value.strip(), maxsplit=1
        )[0]
        normalized = first_word.casefold().replace("_", "").replace("-", "")
        return normalized in scheme_values
    if not isinstance(value, Mapping):
        return False

    child_values: dict[str, Any] = {}
    for child_key, child_value in value.items():
        if not isinstance(child_key, str):
            continue
        child_values["".join(_config_key_words(child_key))] = child_value

    secret_slots = {
        "value",
        "header",
        "headers",
        "token",
        "credential",
        "credentials",
        "password",
        "secret",
        "key",
    }
    present_secret_slots = secret_slots & child_values.keys()
    if present_secret_slots & (secret_slots - {"value"}):
        return True
    has_credential_scheme = any(
        str(child_values.get(field, ""))
        .strip()
        .casefold()
        .replace("_", "")
        .replace("-", "")
        in scheme_values
        for field in ("type", "scheme", "method")
    )
    return "value" in present_secret_slots and has_credential_scheme


def validate_acquisition_config_snapshot(
    config_snapshot: Mapping[str, Any],
) -> None:
    """Recursively reject credential-bearing keys without echoing input."""

    if not isinstance(config_snapshot, Mapping):
        raise ValueError("config_snapshot must be a mapping")
    pending: list[Any] = [config_snapshot]
    seen_containers: set[int] = set()
    while pending:
        current = pending.pop()
        if isinstance(current, Mapping):
            identity = id(current)
            if identity in seen_containers:
                continue
            seen_containers.add(identity)
            for key, value in current.items():
                if not isinstance(key, str):
                    raise ValueError("config_snapshot keys must be strings")
                if _is_sensitive_config_key(
                    key
                ) or _is_sensitive_auth_structure(key, value):
                    raise ValueError(
                        "config_snapshot contains sensitive credential fields"
                    )
                pending.append(value)
        elif isinstance(current, (list, tuple)):
            identity = id(current)
            if identity in seen_containers:
                continue
            seen_containers.add(identity)
            pending.extend(current)


class AcquisitionJobService:
    """Create a runnable Job only when its campaign is fully persisted."""

    def __init__(
        self,
        *,
        database: Database | None = None,
        pipeline_jobs: PipelineJobService | None = None,
        acquisition_store: AcquisitionStore | None = None,
    ) -> None:
        self.database = database or get_db()
        self.pipeline_jobs = pipeline_jobs or PipelineJobService(
            database=self.database
        )
        self.acquisition_store = acquisition_store or AcquisitionStore()

    async def create_job(
        self,
        *,
        platform: str,
        account_mode: str,
        account_id: int | None,
        stages: list[str],
        config_snapshot: Mapping[str, Any],
        campaign: Mapping[str, Any],
        keywords: Sequence[Mapping[str, Any]],
    ) -> AcquisitionJobBundle:
        validated_stages = validate_acquisition_stages(stages)
        validate_acquisition_config_snapshot(config_snapshot)
        if not 1 <= len(keywords) <= 100:
            raise ValueError("Acquisition job requires 1 to 100 keywords")
        self._validate_unique_keywords(keywords)

        # Provider/account checks may await and must finish before the write
        # transaction starts. Once the transaction opens, no queued Job is
        # committed until its complete acquisition definition is present.
        await self.pipeline_jobs.preflight_job(
            platform=platform,
            account_mode=account_mode,
            account_id=account_id,
        )

        snapshot = dict(config_snapshot)
        snapshot["businessMode"] = "ai_acquisition"
        snapshot["acquisitionSchemaVersion"] = "1.0"

        with self.database.session() as session:
            job = await self.pipeline_jobs.create_job(
                platform=platform,
                account_mode=account_mode,
                account_id=account_id,
                stages=list(validated_stages),
                trigger_type="manual",
                config_snapshot=snapshot,
                _session=session,
                _preflighted=True,
            )
            stored_campaign = self.acquisition_store.create_campaign(
                session,
                job_id=job.id,
                platform=job.platform,
                countries=campaign.get("countries"),
                languages=campaign.get("languages"),
                industries=campaign.get("industries"),
                products=campaign.get("products"),
                customer_roles=campaign.get("customer_roles"),
                hard_conditions=campaign.get("hard_conditions"),
                preference_conditions=campaign.get("preference_conditions"),
                excluded_targets=campaign.get("excluded_targets"),
                search_budget=campaign.get("search_budget"),
                keyword_mix=campaign.get("keyword_mix"),
            )
            stored_keywords = tuple(
                self.acquisition_store.create_keyword(
                    session,
                    job_id=job.id,
                    platform=job.platform,
                    text=str(keyword.get("text", "")),
                    language=str(keyword.get("language", "")),
                    keyword_type=str(keyword.get("keyword_type", "industry")),
                    source=str(keyword.get("source", "manual")),
                    status=str(keyword.get("status", "new")),
                )
                for keyword in keywords
            )

            # Keep the returned values usable after the context manager commits
            # and closes its Session. All writes have already been flushed.
            list(job.stages)
            for keyword in stored_keywords:
                session.expunge(keyword)
            session.expunge(stored_campaign)
            session.expunge(job)
            bundle = AcquisitionJobBundle(
                job=job,
                campaign=stored_campaign,
                keywords=stored_keywords,
            )
        return bundle

    @staticmethod
    def _validate_unique_keywords(
        keywords: Sequence[Mapping[str, Any]],
    ) -> None:
        seen: set[tuple[str, str]] = set()
        for keyword in keywords:
            normalized_text = " ".join(
                str(keyword.get("text", "")).split()
            ).casefold()
            if not normalized_text:
                raise ValueError("keyword text must not be blank")
            normalized = (
                normalized_text,
                str(keyword.get("language", "")).strip().casefold(),
            )
            if normalized in seen:
                raise ValueError("Acquisition keywords must be unique")
            seen.add(normalized)


__all__ = [
    "AcquisitionJobBundle",
    "AcquisitionJobService",
    "validate_acquisition_config_snapshot",
    "validate_acquisition_stages",
]
