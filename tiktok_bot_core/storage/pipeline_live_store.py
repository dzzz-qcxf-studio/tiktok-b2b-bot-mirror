"""Durable storage for job-scoped Pipeline events and decision checkpoints."""

from __future__ import annotations

from datetime import datetime
import json
import math
import re
from types import MappingProxyType
import unicodedata
import uuid
from typing import Any, Mapping, Sequence
from urllib.parse import parse_qsl, urlsplit

from sqlalchemy import func, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import set_committed_value

from tiktok_bot_core.models.entities import (
    PipelineDecisionCheckpoint,
    PipelineJob,
    PipelineJobEvent,
)
from tiktok_bot_core.models.pipeline_states import (
    DISCOVERY_STATUSES,
    JOB_STATUSES,
    JOB_STATUS_FAILED,
    JOB_STATUS_INTERRUPTED,
    JOB_STATUS_PARTIAL_FAILED,
    JOB_USER_STATUSES,
    QUALIFICATION_STATUSES,
    STAGE_STATUSES,
    STAGE_STATUS_FAILED,
)


MAX_EVENT_PAGE_SIZE = 500
MAX_EVENT_PAYLOAD_BYTES = 16_384
MAX_CHECKPOINT_CONTEXT_BYTES = 8_192

EVENT_LEVELS = frozenset({"debug", "info", "warning", "error"})
BROWSE_ACTIONS = frozenset(
    {"navigate", "click", "scroll", "wait", "extract", "done", "error"}
)
CHECKPOINT_STATUSES = frozenset(
    {"pending", "resolved", "expired", "cancelled"}
)
RESOLUTION_SOURCES = frozenset({"human", "timeout", "system"})
JOB_ERROR_EVENT_STATUSES = frozenset(
    {
        JOB_STATUS_FAILED,
        JOB_STATUS_PARTIAL_FAILED,
        JOB_STATUS_INTERRUPTED,
    }
)
STAGE_ERROR_EVENT_STATUSES = frozenset({STAGE_STATUS_FAILED})
CANDIDATE_EVENT_STATUSES = frozenset(
    DISCOVERY_STATUSES | QUALIFICATION_STATUSES | JOB_USER_STATUSES
)
PUBLIC_ERROR_MESSAGES: Mapping[str, str] = MappingProxyType({
    "network": "网络连接暂时不可用",
    "timeout": "操作超时，系统将按安全策略继续",
    "upstream_server": "上游服务暂时不可用",
    "rate_limit": "请求频率受限，请稍后重试",
    "authentication": "服务认证失败，请检查配置",
    "authorization": "当前账号没有执行权限",
    "configuration": "运行配置不完整",
    "circuit_open": "模型服务暂时处于保护状态",
    "session_expired": "社媒账号登录状态已失效",
    "captcha_required": "平台要求人工完成安全验证",
    "risk_control": "平台风控阻止了当前操作",
    "browser_unavailable": "浏览器服务暂时不可用",
    "account_unavailable": "当前社媒账号不可用",
    "budget_exhausted": "本轮探索预算已用完",
    "search_exhausted_without_evidence": "本轮搜索未获得可用证据",
    "internal_error": "任务遇到内部错误",
})

_COMMON_FIELDS = frozenset({"schemaVersion"})
_EVENT_FIELDS = {
    "job.lifecycle": _COMMON_FIELDS
    | {
        "status",
        "previousStatus",
        "triggerType",
        "durationMs",
        "errorCode",
        "message",
    },
    "stage.lifecycle": _COMMON_FIELDS
    | {
        "status",
        "previousStatus",
        "attempt",
        "resultCount",
        "durationMs",
        "errorCode",
        "message",
    },
    "decision.lifecycle": _COMMON_FIELDS
    | {
        "checkpointId",
        "kind",
        "status",
        "defaultOptionKey",
        "deadlineAt",
        "resolutionKey",
        "resolutionSource",
        "message",
    },
    "candidate.lifecycle": _COMMON_FIELDS
    | {
        "userId",
        "status",
        "matchScore",
        "confidenceScore",
        "labels",
        "evidenceCount",
        "missingFields",
        "message",
    },
}
_BROWSE_FIELDS = _COMMON_FIELDS | {
    "action",
    "step",
    "keyword",
    "pageType",
    "url",
    "rationale",
    "screenshotHash",
    "waitMs",
    "scrollPx",
    "evidenceCount",
    "budget",
    "summary",
    "errorCode",
    "message",
    "mergedCount",
}
_SAFE_METRIC_FIELDS = frozenset(
    {
        "stepsUsed",
        "stepsLimit",
        "pagesUsed",
        "pagesLimit",
        "videosUsed",
        "videosLimit",
        "commentsUsed",
        "commentsLimit",
        "candidatesUsed",
        "candidatesLimit",
        "evidenceUsed",
        "evidenceLimit",
        "llmCallsUsed",
        "llmCallsLimit",
        "durationMs",
        "total",
        "qualified",
        "manualReview",
        "needEnrichment",
        "rejected",
    }
)
_CHECKPOINT_CONTEXT_FIELDS = frozenset(
    {
        "schemaVersion",
        "title",
        "question",
        "summary",
        "metrics",
        "warnings",
        "candidateCounts",
        "remainingBudget",
        "defaultReason",
        "blockingReason",
    }
)
_SENSITIVE_KEY_PARTS = frozenset(
    {
        "cookie",
        "token",
        "authorization",
        "authheader",
        "authvalue",
        "apikey",
        "secret",
        "password",
        "credential",
        "profile",
        "prompt",
        "response",
    }
)
_SENSITIVE_VALUE_PATTERN = re.compile(
    r"(?i)(?:\b(?:bearer|basic)\s+[a-z0-9._~+/=-]{6,}|"
    r"\bsk-[a-z0-9_-]{8,}|"
    r"(?:api[_ -]?key|authorization|access[_ -]?token|cookie)\s*[:=])"
)
_OPTION_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,79}$")
_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{0,79}$")
_HEX_PATTERN = re.compile(r"^[0-9a-f]{8,128}$")


class PipelineLiveStoreError(ValueError):
    """Base error with stable text and no rejected input values."""


class PipelineLiveValidationError(PipelineLiveStoreError):
    """A live event/checkpoint payload violates the public contract."""


class CheckpointConflictError(PipelineLiveStoreError):
    """A checkpoint CAS or the single-pending invariant lost a race."""


def _is_pending_checkpoint_conflict(exc: IntegrityError) -> bool:
    message = str(exc.orig).lower()
    return (
        "unique constraint failed: pipeline_decision_checkpoints.job_id"
        in message
    )


def _normalized_key(value: Any) -> str:
    return "".join(
        character
        for character in unicodedata.normalize("NFKC", str(value)).lower()
        if character.isalnum()
    )


def _contains_sensitive_key(key: Any) -> bool:
    normalized = _normalized_key(key)
    return any(part in normalized for part in _SENSITIVE_KEY_PARTS)


def _validate_json_tree(value: Any, *, depth: int = 0) -> None:
    if depth > 6:
        raise PipelineLiveValidationError("live payload nesting is too deep")
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str) or _contains_sensitive_key(key):
                raise PipelineLiveValidationError(
                    "live payload contains prohibited fields"
                )
            _validate_json_tree(child, depth=depth + 1)
        return
    if isinstance(value, (list, tuple)):
        for child in value:
            _validate_json_tree(child, depth=depth + 1)
        return
    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise PipelineLiveValidationError(
                "live payload contains an invalid number"
            )
        return
    if isinstance(value, str):
        if len(value) > 2_000:
            raise PipelineLiveValidationError("live payload text is too long")
        if _SENSITIVE_VALUE_PATTERN.search(value):
            raise PipelineLiveValidationError(
                "live payload contains prohibited values"
            )
        return
    raise PipelineLiveValidationError("live payload contains unsupported values")


def _serialized_copy(
    value: Mapping[str, Any],
    *,
    byte_limit: int,
) -> dict[str, Any]:
    _validate_json_tree(value)
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError):
        raise PipelineLiveValidationError(
            "live payload is not JSON serializable"
        ) from None
    if len(encoded.encode("utf-8")) > byte_limit:
        raise PipelineLiveValidationError("live payload exceeds its size limit")
    return json.loads(encoded)


def _validate_event_payload(
    event_type: str,
    payload: Mapping[str, Any],
    *,
    level: str,
) -> dict[str, Any]:
    if event_type.startswith("browse."):
        action = event_type.split(".", 1)[1]
        if action not in BROWSE_ACTIONS:
            raise PipelineLiveValidationError("unsupported pipeline event type")
        allowed = _BROWSE_FIELDS
    else:
        allowed = _EVENT_FIELDS.get(event_type)
        if allowed is None:
            raise PipelineLiveValidationError("unsupported pipeline event type")
    if not isinstance(payload, Mapping):
        raise PipelineLiveValidationError("event payload must be an object")

    # Detect prohibited nested fields before the public-field check, while
    # deliberately not echoing the rejected key or value.
    copied = _serialized_copy(payload, byte_limit=MAX_EVENT_PAYLOAD_BYTES)
    if set(copied) - allowed:
        raise PipelineLiveValidationError("unsupported event payload fields")
    if copied.get("schemaVersion") != 1:
        raise PipelineLiveValidationError("unsupported event schema version")
    if isinstance(copied.get("schemaVersion"), bool):
        raise PipelineLiveValidationError("unsupported event schema version")

    _validate_optional_text(
        copied,
        "status",
        maximum=64,
        allow_empty=False,
        required=event_type in {
            "job.lifecycle",
            "stage.lifecycle",
            "decision.lifecycle",
            "candidate.lifecycle",
        },
    )
    if "status" in copied:
        allowed_statuses = {
            "job.lifecycle": JOB_STATUSES,
            "stage.lifecycle": STAGE_STATUSES,
            "decision.lifecycle": CHECKPOINT_STATUSES,
            "candidate.lifecycle": CANDIDATE_EVENT_STATUSES,
        }.get(event_type)
        if allowed_statuses is not None and copied["status"] not in allowed_statuses:
            raise PipelineLiveValidationError("unregistered lifecycle status")
    _validate_optional_text(
        copied,
        "previousStatus",
        maximum=64,
        allow_empty=False,
    )
    _validate_optional_code(copied, "triggerType", maximum=32)
    _validate_optional_integer(copied, "durationMs", maximum=86_400_000)
    _validate_optional_text(copied, "message", maximum=500, allow_empty=False)

    if event_type == "stage.lifecycle":
        _validate_optional_integer(copied, "attempt", maximum=1_000_000)
        _validate_optional_integer(copied, "resultCount", maximum=1_000_000_000)
    if event_type.startswith("browse."):
        if copied.get("action") != event_type.split(".", 1)[1]:
            raise PipelineLiveValidationError("browse action does not match event type")
        _validate_optional_code(copied, "action", maximum=16, required=True)
        _validate_optional_integer(copied, "step", maximum=1_000_000_000, required=True)
        _validate_optional_text(copied, "keyword", maximum=300, allow_empty=False)
        _validate_optional_code(copied, "pageType", maximum=64)
        _validate_optional_url(copied, "url", maximum=1_000)
        _validate_optional_text(copied, "rationale", maximum=500, allow_empty=False)
        _validate_optional_hash(copied, "screenshotHash")
        _validate_optional_integer(copied, "waitMs", maximum=10_000)
        _validate_optional_integer(copied, "scrollPx", maximum=3_000)
        _validate_optional_integer(copied, "evidenceCount", maximum=1_000_000_000)
        _validate_optional_text(copied, "summary", maximum=500, allow_empty=False)
        _validate_optional_integer(
            copied,
            "mergedCount",
            minimum=1,
            maximum=1_000_000_000,
        )
        budget = copied.get("budget")
        if budget is not None:
            if not isinstance(budget, dict) or set(budget) - _SAFE_METRIC_FIELDS:
                raise PipelineLiveValidationError(
                    "unsupported event payload fields"
                )
            _validate_metric_mapping(budget, field="budget")
    elif event_type == "decision.lifecycle":
        _validate_optional_text(
            copied,
            "checkpointId",
            maximum=80,
            allow_empty=False,
            required=True,
        )
        _validate_optional_code(copied, "kind", maximum=64, required=True)
        _validate_optional_code(copied, "defaultOptionKey", maximum=80)
        _validate_optional_datetime_text(copied, "deadlineAt")
        _validate_optional_code(copied, "resolutionKey", maximum=80)
        _validate_optional_enum(
            copied,
            "resolutionSource",
            values=RESOLUTION_SOURCES,
        )
    elif event_type == "candidate.lifecycle":
        _validate_optional_integer(
            copied,
            "userId",
            minimum=1,
            maximum=2**63 - 1,
            required=True,
        )
        _validate_optional_score(copied, "matchScore")
        _validate_optional_score(copied, "confidenceScore")
        _validate_optional_string_list(copied, "labels", maximum_items=32)
        _validate_optional_integer(copied, "evidenceCount", maximum=1_000_000_000)
        _validate_optional_string_list(copied, "missingFields", maximum_items=32)

    error_code = copied.get("errorCode")
    requires_error_code = (
        event_type == "job.lifecycle"
        and copied.get("status") in JOB_ERROR_EVENT_STATUSES
    ) or (
        event_type == "stage.lifecycle"
        and copied.get("status") in STAGE_ERROR_EVENT_STATUSES
    )
    if requires_error_code and error_code is None:
        raise PipelineLiveValidationError(
            "error terminal lifecycle status requires a public error code"
        )
    if error_code is not None:
        _validate_optional_code(copied, "errorCode", maximum=80, required=True)
        public_message = PUBLIC_ERROR_MESSAGES.get(error_code)
        if public_message is None:
            raise PipelineLiveValidationError("unregistered public error code")
        if level != "error" or copied.get("message") != public_message:
            raise PipelineLiveValidationError("error event must use public error text")
        if "summary" in copied or "rationale" in copied:
            raise PipelineLiveValidationError("error event contains private diagnostics")
    elif level == "error" or event_type == "browse.error":
        raise PipelineLiveValidationError("error event requires a public error code")
    if event_type.startswith("browse.") and event_type != "browse.error":
        if "errorCode" in copied:
            raise PipelineLiveValidationError("browse error code has an invalid event type")
    return copied


def _validate_context(context: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(context, Mapping):
        raise PipelineLiveValidationError("checkpoint context must be an object")
    copied = _serialized_copy(
        context,
        byte_limit=MAX_CHECKPOINT_CONTEXT_BYTES,
    )
    if set(copied) - _CHECKPOINT_CONTEXT_FIELDS:
        raise PipelineLiveValidationError("unsupported checkpoint context fields")
    if copied.get("schemaVersion") != 1:
        raise PipelineLiveValidationError("unsupported checkpoint schema version")
    if isinstance(copied.get("schemaVersion"), bool):
        raise PipelineLiveValidationError("unsupported checkpoint schema version")
    for field, maximum in (
        ("title", 160),
        ("question", 500),
        ("summary", 500),
        ("defaultReason", 500),
        ("blockingReason", 500),
    ):
        _validate_optional_text(copied, field, maximum=maximum, allow_empty=False)
    for field in ("metrics", "candidateCounts", "remainingBudget"):
        metrics = copied.get(field)
        if metrics is not None:
            if not isinstance(metrics, dict) or set(metrics) - _SAFE_METRIC_FIELDS:
                raise PipelineLiveValidationError(
                    "unsupported checkpoint context fields"
                )
            _validate_metric_mapping(metrics, field=field)
    warnings = copied.get("warnings")
    if warnings is not None:
        _validate_optional_string_list(
            copied,
            "warnings",
            maximum_items=10,
            maximum_length=200,
        )
    return copied


def _validate_optional_text(
    payload: Mapping[str, Any],
    field: str,
    *,
    maximum: int,
    allow_empty: bool = True,
    required: bool = False,
) -> None:
    if field not in payload:
        if required:
            raise PipelineLiveValidationError(f"{field} is required")
        return
    value = payload[field]
    if not isinstance(value, str):
        raise PipelineLiveValidationError(f"{field} must be text")
    if (not allow_empty and not value.strip()) or len(value) > maximum:
        raise PipelineLiveValidationError(f"{field} has an invalid length")
    if "\x00" in value:
        raise PipelineLiveValidationError(f"{field} contains invalid text")


def _validate_optional_code(
    payload: Mapping[str, Any],
    field: str,
    *,
    maximum: int,
    required: bool = False,
) -> None:
    _validate_optional_text(
        payload,
        field,
        maximum=maximum,
        allow_empty=False,
        required=required,
    )
    if field in payload and not _CODE_PATTERN.fullmatch(payload[field]):
        raise PipelineLiveValidationError(f"{field} has an invalid format")


def _validate_optional_integer(
    payload: Mapping[str, Any],
    field: str,
    *,
    minimum: int = 0,
    maximum: int,
    required: bool = False,
) -> None:
    if field not in payload:
        if required:
            raise PipelineLiveValidationError(f"{field} is required")
        return
    value = payload[field]
    if isinstance(value, bool) or not isinstance(value, int):
        raise PipelineLiveValidationError(f"{field} must be an integer")
    if not minimum <= value <= maximum:
        raise PipelineLiveValidationError(f"{field} is outside the allowed range")


def _validate_optional_score(payload: Mapping[str, Any], field: str) -> None:
    if field not in payload:
        return
    value = payload[field]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PipelineLiveValidationError(f"{field} must be a number")
    if not math.isfinite(float(value)) or not 0 <= float(value) <= 100:
        raise PipelineLiveValidationError(f"{field} is outside the allowed range")


def _validate_optional_string_list(
    payload: Mapping[str, Any],
    field: str,
    *,
    maximum_items: int,
    maximum_length: int = 80,
) -> None:
    if field not in payload:
        return
    value = payload[field]
    if not isinstance(value, list) or len(value) > maximum_items:
        raise PipelineLiveValidationError(f"{field} must be a bounded text list")
    for item in value:
        if (
            not isinstance(item, str)
            or not item.strip()
            or len(item) > maximum_length
            or "\x00" in item
        ):
            raise PipelineLiveValidationError(f"{field} must be a bounded text list")


def _validate_metric_mapping(value: Mapping[str, Any], *, field: str) -> None:
    for metric in value.values():
        if isinstance(metric, bool) or not isinstance(metric, (int, float)):
            raise PipelineLiveValidationError(f"{field} metrics must be numeric")
        if (
            not math.isfinite(float(metric))
            or not 0 <= float(metric) <= 1_000_000_000_000
        ):
            raise PipelineLiveValidationError(f"{field} metric is outside the allowed range")


def _validate_optional_hash(payload: Mapping[str, Any], field: str) -> None:
    if field not in payload:
        return
    value = payload[field]
    if not isinstance(value, str) or not _HEX_PATTERN.fullmatch(value.lower()):
        raise PipelineLiveValidationError(f"{field} has an invalid format")


def _validate_optional_url(
    payload: Mapping[str, Any],
    field: str,
    *,
    maximum: int,
) -> None:
    if field not in payload:
        return
    _validate_optional_text(
        payload,
        field,
        maximum=maximum,
        allow_empty=False,
    )
    try:
        parsed = urlsplit(payload[field])
        query_keys = [key for key, _value in parse_qsl(parsed.query)]
    except ValueError:
        raise PipelineLiveValidationError(f"{field} has an invalid format") from None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or any(_contains_sensitive_key(key) for key in query_keys)
    ):
        raise PipelineLiveValidationError(f"{field} has an invalid format")


def _validate_optional_datetime_text(
    payload: Mapping[str, Any],
    field: str,
) -> None:
    if field not in payload:
        return
    _validate_optional_text(payload, field, maximum=64, allow_empty=False)
    try:
        datetime.fromisoformat(payload[field].replace("Z", "+00:00"))
    except ValueError:
        raise PipelineLiveValidationError(f"{field} has an invalid format") from None


def _validate_optional_enum(
    payload: Mapping[str, Any],
    field: str,
    *,
    values: frozenset[str],
) -> None:
    if field not in payload:
        return
    if not isinstance(payload[field], str) or payload[field] not in values:
        raise PipelineLiveValidationError(f"{field} has an invalid value")


def _strict_int(value: Any, *, minimum: int, maximum: int, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise PipelineLiveValidationError(f"{field} must be an integer")
    if not minimum <= value <= maximum:
        raise PipelineLiveValidationError(f"{field} is outside the allowed range")
    return value


def _clean_code(value: Any, *, field: str, maximum: int = 80) -> str:
    if not isinstance(value, str):
        raise PipelineLiveValidationError(f"{field} has an invalid format")
    normalized = value.strip().lower()
    if len(normalized) > maximum or not _CODE_PATTERN.fullmatch(normalized):
        raise PipelineLiveValidationError(f"{field} has an invalid format")
    return normalized


class PipelineLiveStore:
    """Atomic persistence boundary for live events and checkpoints."""

    def append_event(
        self,
        session: Session,
        *,
        job_id: str,
        stage: str,
        event_type: str,
        level: str,
        payload: Mapping[str, Any],
        created_at: datetime | None = None,
    ) -> PipelineJobEvent:
        normalized_job_id = str(job_id or "").strip()
        if not normalized_job_id:
            raise PipelineLiveValidationError("job_id must not be empty")
        if session.get(PipelineJob, normalized_job_id) is None:
            raise PipelineLiveValidationError("pipeline job not found")
        normalized_stage = str(stage or "").strip().lower()
        if len(normalized_stage) > 20:
            raise PipelineLiveValidationError("stage is too long")
        normalized_type = str(event_type or "").strip().lower()
        normalized_level = str(level or "").strip().lower()
        if normalized_level not in EVENT_LEVELS:
            raise PipelineLiveValidationError("unsupported event level")
        safe_payload = _validate_event_payload(
            normalized_type,
            payload,
            level=normalized_level,
        )
        event = PipelineJobEvent(
            job_id=normalized_job_id,
            stage=normalized_stage,
            event_type=normalized_type,
            level=normalized_level,
            payload_json=safe_payload,
            created_at=created_at or datetime.utcnow(),
        )
        session.add(event)
        session.flush()
        return event

    def list_events(
        self,
        session: Session,
        *,
        job_id: str,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> list[PipelineJobEvent]:
        after = _strict_int(
            after_sequence,
            minimum=0,
            maximum=2**63 - 1,
            field="after_sequence",
        )
        page_size = _strict_int(
            limit,
            minimum=1,
            maximum=MAX_EVENT_PAGE_SIZE,
            field="limit",
        )
        normalized_job_id = str(job_id or "").strip()
        if not normalized_job_id:
            raise PipelineLiveValidationError("job_id must not be empty")
        return list(
            session.scalars(
                select(PipelineJobEvent)
                .where(
                    PipelineJobEvent.job_id == normalized_job_id,
                    PipelineJobEvent.sequence > after,
                )
                .order_by(PipelineJobEvent.sequence.asc())
                .limit(page_size)
            )
        )

    def count_events(self, session: Session, *, job_id: str) -> int:
        normalized_job_id = str(job_id or "").strip()
        if not normalized_job_id:
            raise PipelineLiveValidationError("job_id must not be empty")
        return int(
            session.scalar(
                select(func.count(PipelineJobEvent.sequence)).where(
                    PipelineJobEvent.job_id == normalized_job_id
                )
            )
            or 0
        )

    def count_event_types(
        self,
        session: Session,
        *,
        job_id: str,
        event_types: Sequence[str],
    ) -> int:
        normalized_types = tuple(sorted(set(event_types)))
        if not normalized_types:
            return 0
        return int(
            session.scalar(
                select(func.count(PipelineJobEvent.sequence)).where(
                    PipelineJobEvent.job_id == str(job_id).strip(),
                    PipelineJobEvent.event_type.in_(normalized_types),
                )
            )
            or 0
        )

    def get_latest_event(
        self,
        session: Session,
        *,
        job_id: str,
    ) -> PipelineJobEvent | None:
        return session.scalar(
            select(PipelineJobEvent)
            .where(PipelineJobEvent.job_id == str(job_id).strip())
            .order_by(PipelineJobEvent.sequence.desc())
            .limit(1)
        )

    def create_checkpoint(
        self,
        session: Session,
        *,
        job_id: str,
        stage: str,
        kind: str,
        option_keys: Sequence[str],
        default_option_key: str,
        context: Mapping[str, Any],
        deadline_at: datetime,
        version: int = 1,
    ) -> PipelineDecisionCheckpoint:
        normalized_job_id = str(job_id or "").strip()
        if not normalized_job_id:
            raise PipelineLiveValidationError("job_id must not be empty")
        with session.no_autoflush:
            if session.get(PipelineJob, normalized_job_id) is None:
                raise PipelineLiveValidationError("pipeline job not found")
        normalized_stage = _clean_code(stage, field="stage", maximum=20)
        normalized_kind = _clean_code(
            kind,
            field="checkpoint kind",
            maximum=64,
        )
        normalized_version = _strict_int(
            version,
            minimum=1,
            maximum=2**31 - 1,
            field="version",
        )
        if not isinstance(deadline_at, datetime):
            raise PipelineLiveValidationError("deadline_at must be a datetime")
        if isinstance(option_keys, (str, bytes)) or not isinstance(
            option_keys,
            Sequence,
        ):
            raise PipelineLiveValidationError("checkpoint options are invalid")
        if any(not isinstance(value, str) for value in option_keys):
            raise PipelineLiveValidationError("checkpoint options are invalid")
        normalized_options = [value.strip() for value in option_keys]
        if (
            not normalized_options
            or len(normalized_options) > 16
            or len(set(normalized_options)) != len(normalized_options)
            or any(not _OPTION_KEY_PATTERN.fullmatch(value) for value in normalized_options)
        ):
            raise PipelineLiveValidationError("checkpoint options are invalid")
        normalized_default = str(default_option_key or "").strip()
        if normalized_default not in normalized_options:
            raise PipelineLiveValidationError(
                "checkpoint default option is not registered"
            )
        safe_context = _validate_context(context)
        checkpoint_id = str(uuid.uuid4())
        now = datetime.utcnow()
        connection = session.connection()
        savepoint = connection.begin_nested()
        try:
            connection.execute(
                insert(PipelineDecisionCheckpoint).values(
                    id=checkpoint_id,
                    job_id=normalized_job_id,
                    stage=normalized_stage,
                    kind=normalized_kind,
                    version=normalized_version,
                    option_keys_json=normalized_options,
                    default_option_key=normalized_default,
                    context_json=safe_context,
                    status="pending",
                    deadline_at=deadline_at,
                    resolved_at=None,
                    resolution_key=None,
                    resolution_source=None,
                    operator="",
                    reason="",
                    created_at=now,
                    updated_at=now,
                )
            )
            savepoint.commit()
        except IntegrityError as exc:
            if savepoint.is_active:
                savepoint.rollback()
            if _is_pending_checkpoint_conflict(exc):
                raise CheckpointConflictError(
                    "an active checkpoint already exists"
                ) from None
            raise
        except Exception:
            if savepoint.is_active:
                savepoint.rollback()
            raise
        with session.no_autoflush:
            checkpoint = session.get(
                PipelineDecisionCheckpoint,
                checkpoint_id,
            )
        if checkpoint is None:
            raise RuntimeError("created checkpoint could not be loaded")
        return checkpoint

    def get_active_checkpoint(
        self,
        session: Session,
        *,
        job_id: str,
    ) -> PipelineDecisionCheckpoint | None:
        with session.no_autoflush:
            return session.scalar(
                select(PipelineDecisionCheckpoint)
                .where(
                    PipelineDecisionCheckpoint.job_id == str(job_id).strip(),
                    PipelineDecisionCheckpoint.status == "pending",
                )
                .order_by(PipelineDecisionCheckpoint.created_at.desc())
                .limit(1)
            )

    def get_checkpoint(
        self,
        session: Session,
        *,
        job_id: str,
        checkpoint_id: str,
    ) -> PipelineDecisionCheckpoint | None:
        with session.no_autoflush:
            return session.scalar(
                select(PipelineDecisionCheckpoint).where(
                    PipelineDecisionCheckpoint.id == str(checkpoint_id).strip(),
                    PipelineDecisionCheckpoint.job_id == str(job_id).strip(),
                )
            )

    @staticmethod
    def _apply_checkpoint_returning(
        checkpoint: PipelineDecisionCheckpoint,
        row: Mapping[str, Any],
    ) -> PipelineDecisionCheckpoint:
        for field in (
            "status",
            "resolved_at",
            "resolution_key",
            "resolution_source",
            "operator",
            "reason",
            "updated_at",
        ):
            set_committed_value(checkpoint, field, row[field])
        return checkpoint

    def resolve_checkpoint(
        self,
        session: Session,
        *,
        job_id: str,
        checkpoint_id: str,
        option_key: str,
        version: int,
        resolution_source: str,
        operator: str = "",
        reason: str = "",
        resolved_at: datetime | None = None,
    ) -> PipelineDecisionCheckpoint:
        with session.no_autoflush:
            checkpoint = self.get_checkpoint(
                session,
                job_id=job_id,
                checkpoint_id=checkpoint_id,
            )
            if checkpoint is None:
                raise CheckpointConflictError(
                    "checkpoint is not active for this job"
                )
            normalized_version = _strict_int(
                version,
                minimum=1,
                maximum=2**31 - 1,
                field="version",
            )
            normalized_option = str(option_key or "").strip()
            if normalized_option not in checkpoint.option_keys_json:
                raise PipelineLiveValidationError(
                    "checkpoint option is not registered"
                )
            normalized_source = str(resolution_source or "").strip().lower()
            if normalized_source not in RESOLUTION_SOURCES:
                raise PipelineLiveValidationError("resolution source is invalid")
            if not isinstance(operator, str) or not isinstance(reason, str):
                raise PipelineLiveValidationError(
                    "checkpoint resolution text is invalid"
                )
            safe_operator = operator.strip()
            safe_reason = reason.strip()
            _validate_json_tree({"operator": safe_operator, "reason": safe_reason})
            if len(safe_operator) > 200 or len(safe_reason) > 2_000:
                raise PipelineLiveValidationError(
                    "checkpoint resolution text is too long"
                )
            terminal_status = (
                "expired" if normalized_source == "timeout" else "resolved"
            )
            statement = (
                update(PipelineDecisionCheckpoint)
                .where(
                    PipelineDecisionCheckpoint.id == checkpoint.id,
                    PipelineDecisionCheckpoint.job_id == str(job_id).strip(),
                    PipelineDecisionCheckpoint.status == "pending",
                    PipelineDecisionCheckpoint.version == normalized_version,
                )
                .values(
                    status=terminal_status,
                    resolved_at=resolved_at or datetime.utcnow(),
                    resolution_key=normalized_option,
                    resolution_source=normalized_source,
                    operator=safe_operator,
                    reason=safe_reason,
                    updated_at=datetime.utcnow(),
                )
                .returning(
                    PipelineDecisionCheckpoint.status,
                    PipelineDecisionCheckpoint.resolved_at,
                    PipelineDecisionCheckpoint.resolution_key,
                    PipelineDecisionCheckpoint.resolution_source,
                    PipelineDecisionCheckpoint.operator,
                    PipelineDecisionCheckpoint.reason,
                    PipelineDecisionCheckpoint.updated_at,
                )
                .execution_options(synchronize_session=False)
            )
            row = session.execute(statement).mappings().one_or_none()
            if row is None:
                raise CheckpointConflictError("checkpoint resolution conflict")
            return self._apply_checkpoint_returning(checkpoint, row)

    def cancel_checkpoint(
        self,
        session: Session,
        *,
        job_id: str,
        checkpoint_id: str | None = None,
        reason: str = "",
        resolved_at: datetime | None = None,
    ) -> PipelineDecisionCheckpoint | None:
        with session.no_autoflush:
            if not isinstance(reason, str):
                raise PipelineLiveValidationError(
                    "checkpoint resolution text is invalid"
                )
            safe_reason = reason.strip()
            _validate_json_tree({"reason": safe_reason})
            if checkpoint_id is None:
                checkpoint = self.get_active_checkpoint(
                    session,
                    job_id=job_id,
                )
            else:
                checkpoint = self.get_checkpoint(
                    session,
                    job_id=job_id,
                    checkpoint_id=checkpoint_id,
                )
            if checkpoint is None or checkpoint.status != "pending":
                return None
            statement = (
                update(PipelineDecisionCheckpoint)
                .where(
                    PipelineDecisionCheckpoint.id == checkpoint.id,
                    PipelineDecisionCheckpoint.status == "pending",
                )
                .values(
                    status="cancelled",
                    resolved_at=resolved_at or datetime.utcnow(),
                    resolution_key=None,
                    resolution_source="system",
                    operator="",
                    reason=safe_reason,
                    updated_at=datetime.utcnow(),
                )
                .returning(
                    PipelineDecisionCheckpoint.status,
                    PipelineDecisionCheckpoint.resolved_at,
                    PipelineDecisionCheckpoint.resolution_key,
                    PipelineDecisionCheckpoint.resolution_source,
                    PipelineDecisionCheckpoint.operator,
                    PipelineDecisionCheckpoint.reason,
                    PipelineDecisionCheckpoint.updated_at,
                )
                .execution_options(synchronize_session=False)
            )
            row = session.execute(statement).mappings().one_or_none()
            if row is None:
                return None
            return self._apply_checkpoint_returning(checkpoint, row)


__all__ = [
    "BROWSE_ACTIONS",
    "CheckpointConflictError",
    "MAX_EVENT_PAGE_SIZE",
    "PUBLIC_ERROR_MESSAGES",
    "JOB_ERROR_EVENT_STATUSES",
    "STAGE_ERROR_EVENT_STATUSES",
    "PipelineLiveStore",
    "PipelineLiveStoreError",
    "PipelineLiveValidationError",
]
