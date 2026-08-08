"""统一 LLM Provider、业务 Route 与请求用量存储。"""

from __future__ import annotations

import math
import re
from datetime import datetime
from typing import Any, Iterable, Mapping, TYPE_CHECKING

from sqlalchemy import case, delete, func, select, text, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from tiktok_bot_core.models.entities import (
    LLMProvider,
    LLMRequestLog,
    LLMRoute,
    LLM_ROUTE_KEYS,
)

if TYPE_CHECKING:
    from tiktok_bot_core.settings import Settings
    from tiktok_bot_core.storage.database import Database


API_KEY_ENV_PATTERN = re.compile(r"[A-Z][A-Z0-9_]*\Z")
ALLOWED_ROUTE_KEYS = frozenset(LLM_ROUTE_KEYS)
ALLOWED_PROVIDER_PROTOCOLS = frozenset({"openai_chat"})
ALLOWED_REQUEST_STATUSES = frozenset({"success", "failed"})


class LLMStoreError(ValueError):
    """LLM 配置存储的可解释业务错误。"""


class LLMProviderConflictError(LLMStoreError):
    """Provider 名称已存在。"""


class LLMProviderNotFoundError(LLMStoreError):
    """Provider 不存在。"""


class LLMProviderInUseError(LLMStoreError):
    """Provider 仍被 Route 引用。"""


class LLMRouteValidationError(LLMStoreError):
    """Route key、链条或 Provider 引用无效。"""


class LLMWriteTransactionError(LLMStoreError):
    """LLM 写入无法取得受控的 SQLite IMMEDIATE 事务。"""


def _validate_api_key_env(value: str) -> str:
    candidate = str(value)
    if not API_KEY_ENV_PATTERN.fullmatch(candidate):
        raise ValueError(
            "api_key_env must match [A-Z][A-Z0-9_]*"
        )
    return candidate


def _validate_route_key(route_key: str) -> str:
    candidate = str(route_key)
    if candidate not in ALLOWED_ROUTE_KEYS:
        raise LLMRouteValidationError(
            f"unsupported LLM route: {candidate}"
        )
    return candidate


def _strict_bool(value: Any, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be a boolean")
    return value


def _strict_integer(
    value: Any,
    field_name: str,
    *,
    minimum: int = 0,
) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{field_name} must be an integer")
    if value < minimum:
        raise ValueError(f"{field_name} must be at least {minimum}")
    return value


def _finite_float(
    value: Any,
    field_name: str,
    *,
    minimum: float = 0.0,
    maximum: float,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a number")
    normalized = float(value)
    if (
        not math.isfinite(normalized)
        or normalized < minimum
        or normalized > maximum
    ):
        raise ValueError(
            f"{field_name} must be finite and between "
            f"{minimum} and {maximum}"
        )
    return normalized


def _begin_sqlite_write(session: Session) -> None:
    bind = session.get_bind()
    if bind.dialect.name != "sqlite":
        return

    connection = session.connection()
    wrapped_connection = connection.connection
    driver_connection = getattr(
        wrapped_connection,
        "driver_connection",
        wrapped_connection,
    )
    database_transaction_active = bool(
        getattr(driver_connection, "in_transaction", False)
    )
    marker = "llm_immediate_transaction"
    if session.info.get(marker):
        if database_transaction_active:
            return
        session.info.pop(marker, None)

    if database_transaction_active:
        raise LLMWriteTransactionError(
            "LLM mutation requires a fresh or managed IMMEDIATE transaction"
        )
    try:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
    except OperationalError as exc:
        raise LLMWriteTransactionError(
            "LLM mutation could not acquire its write transaction"
        ) from exc
    session.info[marker] = True


def _validate_provider_values(values: Mapping[str, Any]) -> dict[str, Any]:
    normalized = dict(values)
    if "name" in normalized:
        normalized["name"] = str(normalized["name"]).strip()
        if not normalized["name"]:
            raise ValueError("provider name must not be empty")
    if "display_name" in normalized:
        normalized["display_name"] = str(
            normalized["display_name"]
        ).strip()
        if not normalized["display_name"]:
            raise ValueError("provider display_name must not be empty")
    if "protocol" in normalized:
        normalized["protocol"] = str(normalized["protocol"])
        if normalized["protocol"] not in ALLOWED_PROVIDER_PROTOCOLS:
            raise ValueError("provider protocol must be openai_chat")
    if "base_url" in normalized:
        normalized["base_url"] = str(normalized["base_url"]).strip()
        if not normalized["base_url"]:
            raise ValueError("provider base_url must not be empty")
    if "default_model" in normalized:
        normalized["default_model"] = str(
            normalized["default_model"]
        ).strip()
        if not normalized["default_model"]:
            raise ValueError("provider default_model must not be empty")
    if "api_key_env" in normalized:
        normalized["api_key_env"] = _validate_api_key_env(
            normalized["api_key_env"]
        )
    if "enabled" in normalized:
        normalized["enabled"] = _strict_bool(
            normalized["enabled"],
            "enabled",
        )
    if "timeout_seconds" in normalized:
        normalized["timeout_seconds"] = _finite_float(
            normalized["timeout_seconds"],
            "timeout_seconds",
            minimum=0.000001,
            maximum=86400.0,
        )
    return normalized


class LLMStore:
    """SQLite 是 Provider、Route 和请求用量的唯一权威源。"""

    _PROVIDER_FIELDS = frozenset(
        {
            "name",
            "display_name",
            "protocol",
            "base_url",
            "default_model",
            "api_key_env",
            "enabled",
            "timeout_seconds",
        }
    )

    def create_provider(
        self,
        session: Session,
        *,
        name: str,
        display_name: str,
        protocol: str = "openai_chat",
        base_url: str,
        default_model: str,
        api_key_env: str,
        enabled: bool = True,
        timeout_seconds: float = 30.0,
    ) -> LLMProvider:
        values = _validate_provider_values(
            {
                "name": name,
                "display_name": display_name,
                "protocol": protocol,
                "base_url": base_url,
                "default_model": default_model,
                "api_key_env": api_key_env,
                "enabled": enabled,
                "timeout_seconds": timeout_seconds,
            }
        )
        _begin_sqlite_write(session)
        if session.scalar(
            select(LLMProvider.id).where(
                LLMProvider.name == values["name"]
            )
        ):
            raise LLMProviderConflictError(
                f"LLM provider name already exists: {values['name']}"
            )
        provider = LLMProvider(**values)
        try:
            with session.begin_nested():
                session.add(provider)
                session.flush()
        except IntegrityError as exc:
            raise LLMProviderConflictError(
                f"LLM provider name already exists: {values['name']}"
            ) from exc
        return provider

    def update_provider(
        self,
        session: Session,
        provider_id: str,
        **changes: Any,
    ) -> LLMProvider:
        unknown = set(changes) - self._PROVIDER_FIELDS
        if unknown:
            raise ValueError(
                f"unsupported provider fields: {sorted(unknown)}"
            )
        _begin_sqlite_write(session)
        provider = session.get(LLMProvider, provider_id)
        if provider is None:
            raise LLMProviderNotFoundError(
                f"LLM provider not found: {provider_id}"
            )
        values = _validate_provider_values(changes)
        requested_name = values.get("name")
        if requested_name is not None and requested_name != provider.name:
            duplicate = session.scalar(
                select(LLMProvider.id).where(
                    LLMProvider.name == requested_name,
                    LLMProvider.id != provider_id,
                )
            )
            if duplicate:
                raise LLMProviderConflictError(
                    f"LLM provider name already exists: {requested_name}"
                )
        try:
            with session.begin_nested():
                for key, value in values.items():
                    setattr(provider, key, value)
                provider.updated_at = datetime.utcnow()
                session.flush()
        except IntegrityError as exc:
            raise LLMProviderConflictError(
                f"LLM provider name already exists: {requested_name}"
            ) from exc
        return provider

    def delete_provider(
        self,
        session: Session,
        provider_id: str,
    ) -> None:
        _begin_sqlite_write(session)
        provider = session.get(LLMProvider, provider_id)
        if provider is None:
            raise LLMProviderNotFoundError(
                f"LLM provider not found: {provider_id}"
            )
        route_count = session.scalar(
            select(func.count(LLMRoute.id)).where(
                LLMRoute.provider_id == provider_id
            )
        )
        if route_count:
            raise LLMProviderInUseError(
                f"LLM provider is referenced by {route_count} route entries"
            )
        session.execute(
            update(LLMRequestLog)
            .where(LLMRequestLog.provider_id == provider_id)
            .values(provider_id=None)
        )
        session.delete(provider)
        session.flush()

    def list_providers(self, session: Session) -> list[LLMProvider]:
        return list(
            session.scalars(
                select(LLMProvider).order_by(
                    LLMProvider.created_at,
                    LLMProvider.name,
                )
            )
        )

    def count_providers(self, session: Session) -> int:
        return int(
            session.scalar(select(func.count(LLMProvider.id))) or 0
        )

    def replace_route_chain(
        self,
        session: Session,
        route_key: str,
        entries: Iterable[Mapping[str, Any]],
    ) -> list[LLMRoute]:
        validated_route = _validate_route_key(route_key)
        normalized_entries: list[dict[str, Any]] = []
        provider_ids: list[str] = []
        for raw_entry in entries:
            provider_id = str(raw_entry.get("provider_id", "")).strip()
            if not provider_id:
                raise LLMRouteValidationError(
                    "route entry provider_id must not be empty"
                )
            if provider_id in provider_ids:
                raise LLMRouteValidationError(
                    "route chain cannot contain duplicate providers"
                )
            try:
                priority = _strict_integer(
                    raw_entry.get("priority", 100),
                    "priority",
                )
            except ValueError as exc:
                raise LLMRouteValidationError(
                    "route entry priority must be an integer"
                ) from exc
            try:
                enabled = _strict_bool(
                    raw_entry.get("enabled", True),
                    "enabled",
                )
            except ValueError as exc:
                raise LLMRouteValidationError(
                    "route entry enabled must be a boolean"
                ) from exc
            raw_model_override = raw_entry.get("model_override")
            model_override = (
                str(raw_model_override).strip()
                if raw_model_override is not None
                else None
            )
            if model_override == "":
                model_override = None
            normalized_entries.append(
                {
                    "route_key": validated_route,
                    "provider_id": provider_id,
                    "priority": priority,
                    "model_override": model_override,
                    "enabled": enabled,
                }
            )
            provider_ids.append(provider_id)

        _begin_sqlite_write(session)
        if provider_ids:
            existing_ids = set(
                session.scalars(
                    select(LLMProvider.id).where(
                        LLMProvider.id.in_(provider_ids)
                    )
                )
            )
            missing = set(provider_ids) - existing_ids
            if missing:
                raise LLMRouteValidationError(
                    f"route references unknown providers: {sorted(missing)}"
                )

        session.execute(
            delete(LLMRoute).where(
                LLMRoute.route_key == validated_route
            )
        )
        routes = [LLMRoute(**entry) for entry in normalized_entries]
        session.add_all(routes)
        session.flush()
        return self.get_route_chain(session, validated_route)

    def get_route_chain(
        self,
        session: Session,
        route_key: str,
        *,
        enabled_only: bool = False,
    ) -> list[LLMRoute]:
        validated_route = _validate_route_key(route_key)
        statement = select(LLMRoute).where(
            LLMRoute.route_key == validated_route
        )
        if enabled_only:
            statement = statement.join(LLMProvider).where(
                LLMRoute.enabled.is_(True),
                LLMProvider.enabled.is_(True),
            )
        statement = statement.order_by(
            LLMRoute.priority,
            LLMRoute.id,
        )
        return list(session.scalars(statement))

    def list_routes(self, session: Session) -> list[LLMRoute]:
        return list(
            session.scalars(
                select(LLMRoute).order_by(
                    LLMRoute.route_key,
                    LLMRoute.priority,
                    LLMRoute.id,
                )
            )
        )

    def record_request(
        self,
        session: Session,
        *,
        route_key: str,
        provider_id: str,
        model: str,
        status: str,
        error_category: str = "",
        input_tokens: int = 0,
        output_tokens: int = 0,
        total_tokens: int | None = None,
        latency_ms: float = 0.0,
        fallback_used: bool = False,
        created_at: datetime | None = None,
    ) -> LLMRequestLog:
        validated_route = _validate_route_key(route_key)
        normalized_input = _strict_integer(
            input_tokens,
            "input_tokens",
        )
        normalized_output = _strict_integer(
            output_tokens,
            "output_tokens",
        )
        normalized_total = (
            normalized_input + normalized_output
            if total_tokens is None
            else _strict_integer(total_tokens, "total_tokens")
        )
        normalized_latency = _finite_float(
            latency_ms,
            "latency_ms",
            maximum=86400000.0,
        )
        normalized_fallback = _strict_bool(
            fallback_used,
            "fallback_used",
        )
        _begin_sqlite_write(session)
        provider = session.get(LLMProvider, provider_id)
        if provider is None:
            raise LLMProviderNotFoundError(
                f"LLM provider not found: {provider_id}"
            )
        normalized_status = str(status)
        if normalized_status not in ALLOWED_REQUEST_STATUSES:
            raise ValueError("request status must be success or failed")
        request_log = LLMRequestLog(
            route_key=validated_route,
            provider_id=provider.id,
            provider_name=provider.name,
            model=str(model).strip(),
            status=normalized_status,
            error_category=str(error_category).strip(),
            input_tokens=normalized_input,
            output_tokens=normalized_output,
            total_tokens=normalized_total,
            latency_ms=normalized_latency,
            fallback_used=normalized_fallback,
            created_at=created_at or datetime.utcnow(),
        )
        session.add(request_log)
        session.flush()
        return request_log

    def usage_summary(
        self,
        session: Session,
        *,
        route_key: str | None = None,
        provider_id: str | None = None,
        since: datetime | None = None,
    ) -> dict[str, int | float]:
        statement = select(
            func.count(LLMRequestLog.id),
            func.sum(
                case((LLMRequestLog.status == "success", 1), else_=0)
            ),
            func.sum(
                case((LLMRequestLog.status == "failed", 1), else_=0)
            ),
            func.sum(LLMRequestLog.input_tokens),
            func.sum(LLMRequestLog.output_tokens),
            func.sum(LLMRequestLog.total_tokens),
            func.sum(
                case((LLMRequestLog.fallback_used.is_(True), 1), else_=0)
            ),
            func.avg(LLMRequestLog.latency_ms),
        )
        if route_key is not None:
            statement = statement.where(
                LLMRequestLog.route_key == _validate_route_key(route_key)
            )
        if provider_id is not None:
            statement = statement.where(
                LLMRequestLog.provider_id == provider_id
            )
        if since is not None:
            statement = statement.where(
                LLMRequestLog.created_at >= since
            )
        row = session.execute(statement).one()
        return {
            "request_count": int(row[0] or 0),
            "success_count": int(row[1] or 0),
            "failure_count": int(row[2] or 0),
            "input_tokens": int(row[3] or 0),
            "output_tokens": int(row[4] or 0),
            "total_tokens": int(row[5] or 0),
            "fallback_count": int(row[6] or 0),
            "average_latency_ms": float(row[7] or 0.0),
        }


def seed_legacy_llm_config(
    database: Database,
    settings: Settings,
) -> None:
    """首次启动时把 legacy Settings 映射成数据库配置，不复制密钥。"""

    store = LLMStore()
    with database.session() as session:
        _begin_sqlite_write(session)
        if store.count_providers(session):
            return
        provider = store.create_provider(
            session,
            name="legacy-default",
            display_name="Legacy default",
            protocol="openai_chat",
            base_url=settings.llm_base_url,
            default_model=settings.llm_model,
            api_key_env="LLM_API_KEY",
            enabled=True,
            timeout_seconds=30.0,
        )
        for route_key in LLM_ROUTE_KEYS:
            session.add(
                LLMRoute(
                    route_key=route_key,
                    provider_id=provider.id,
                    priority=10,
                    model_override=None,
                    enabled=True,
                )
            )
        session.flush()
