from __future__ import annotations

import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta
from threading import Barrier, Event, get_ident
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import event as sqlalchemy_event
from sqlalchemy import func, inspect, select, text
from sqlalchemy.exc import IntegrityError

import tiktok_bot_core.llm.client as legacy_client
from tiktok_bot_core.llm.providers import (
    LLMCompletion,
    LLMProviderConfig,
    LLMProviderError,
    OpenAICompatibleProvider,
    classify_provider_exception,
)
from tiktok_bot_core.llm.router import (
    CIRCUIT_FAILURE_THRESHOLD,
    CircuitState,
    LLMRouteError,
    LLMRouter,
)
from tiktok_bot_core.models.entities import LLMRequestLog, LLMRoute
from tiktok_bot_core.settings import Settings
from tiktok_bot_core.storage.database import Database
from tiktok_bot_core.storage.llm_store import (
    LLMProviderConflictError,
    LLMProviderInUseError,
    LLMRouteValidationError,
    LLMStore,
    LLMWriteTransactionError,
    seed_legacy_llm_config,
)


ROUTE_KEYS = {
    "collection",
    "qualification",
    "strategy",
    "iteration",
    "default",
}


@pytest.fixture
def db(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'llm.db'}")
    database.init()
    yield database
    database.engine.dispose()


@pytest.fixture
def empty_db(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'llm-empty.db'}")
    from tiktok_bot_core.models.entities import Base

    Base.metadata.create_all(database.engine)
    yield database
    database.engine.dispose()


def provider_payload(name: str = "provider-main") -> dict:
    return {
        "name": name,
        "display_name": "Provider Main",
        "protocol": "openai_chat",
        "base_url": "https://llm.example.test/v1",
        "default_model": "model-main",
        "api_key_env": "PROVIDER_MAIN_API_KEY",
        "enabled": True,
        "timeout_seconds": 30,
    }


def test_llm_tables_are_created(db):
    tables = set(inspect(db.engine).get_table_names())

    assert {"llm_providers", "llm_routes", "llm_request_logs"} <= tables


def test_sqlite_foreign_keys_are_enabled_and_reject_orphan_routes(empty_db):
    with empty_db.engine.connect() as connection:
        assert connection.scalar(text("PRAGMA foreign_keys")) == 1

    with pytest.raises(IntegrityError):
        with empty_db.session() as session:
            session.add(
                LLMRoute(
                    route_key="default",
                    provider_id="missing-provider",
                    priority=10,
                    enabled=True,
                )
            )


def test_database_init_seeds_default_routes_without_secret_values(db):
    store = LLMStore()
    with db.session() as session:
        providers = store.list_providers(session)
        routes = store.list_routes(session)
        assert len(providers) == 1
        assert providers[0].name == "legacy-default"
        assert providers[0].api_key_env == "LLM_API_KEY"
        assert {route.route_key for route in routes} == ROUTE_KEYS
    provider_columns = {
        column["name"]
        for column in inspect(db.engine).get_columns("llm_providers")
    }
    assert "api_key" not in provider_columns


def test_default_routes_are_seeded_from_legacy_settings(empty_db):
    settings = Settings(
        llm_api_key="must-not-be-persisted",
        llm_base_url="https://legacy.example.test/v1",
        llm_model="legacy-model",
    )

    seed_legacy_llm_config(empty_db, settings)

    store = LLMStore()
    with empty_db.session() as session:
        providers = store.list_providers(session)
        routes = store.list_routes(session)
        assert len(providers) == 1
        assert providers[0].base_url == "https://legacy.example.test/v1"
        assert providers[0].default_model == "legacy-model"
        assert providers[0].api_key_env == "LLM_API_KEY"
        assert "must-not-be-persisted" not in repr(providers[0])
        assert {route.route_key for route in routes} == ROUTE_KEYS


def test_llm_seed_is_idempotent(empty_db):
    settings = Settings()

    seed_legacy_llm_config(empty_db, settings)
    seed_legacy_llm_config(empty_db, settings)

    store = LLMStore()
    with empty_db.session() as session:
        assert store.count_providers(session) == 1
        assert len(store.list_routes(session)) == len(ROUTE_KEYS)


def test_llm_seed_is_concurrency_safe(empty_db):
    barrier = Barrier(2)

    def seed():
        barrier.wait()
        seed_legacy_llm_config(empty_db, Settings())

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(seed) for _ in range(2)]
        for future in futures:
            future.result()

    store = LLMStore()
    with empty_db.session() as session:
        assert store.count_providers(session) == 1
        assert len(store.list_routes(session)) == len(ROUTE_KEYS)


def test_create_provider_concurrency_maps_duplicate_to_stable_conflict(
    empty_db,
):
    barrier = Barrier(2)
    store = LLMStore()

    def create():
        try:
            with empty_db.session() as session:
                session.execute(text("SELECT 1"))
                barrier.wait()
                store.create_provider(session, **provider_payload())
            return "created"
        except LLMProviderConflictError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: create(), range(2)))

    assert sorted(results) == ["conflict", "created"]


def test_write_after_read_still_acquires_begin_immediate(empty_db):
    statements: list[str] = []

    def capture_statement(
        _connection,
        _cursor,
        statement,
        _parameters,
        _context,
        _executemany,
    ):
        statements.append(statement)

    sqlalchemy_event.listen(
        empty_db.engine,
        "before_cursor_execute",
        capture_statement,
    )
    try:
        with empty_db.session() as session:
            session.execute(text("SELECT 1"))
            LLMStore().create_provider(session, **provider_payload())
    finally:
        sqlalchemy_event.remove(
            empty_db.engine,
            "before_cursor_execute",
            capture_statement,
        )

    assert any(
        statement.strip().upper() == "BEGIN IMMEDIATE"
        for statement in statements
    )


def test_unmanaged_active_sqlite_transaction_fails_fast(empty_db):
    with empty_db.session() as session:
        session.execute(text("BEGIN"))
        with pytest.raises(LLMWriteTransactionError):
            LLMStore().create_provider(session, **provider_payload())


@pytest.mark.parametrize(
    "api_key_env",
    ["", "lower_case", "1STARTS_WITH_NUMBER", "API-KEY", " API_KEY "],
)
def test_create_provider_rejects_invalid_api_key_env(empty_db, api_key_env):
    payload = provider_payload()
    payload["api_key_env"] = api_key_env

    with empty_db.session() as session:
        with pytest.raises(ValueError, match="api_key_env"):
            LLMStore().create_provider(session, **payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("enabled", "false"),
        ("enabled", 1),
        ("timeout_seconds", float("inf")),
        ("timeout_seconds", float("nan")),
        ("timeout_seconds", True),
    ],
)
def test_create_provider_rejects_ambiguous_or_non_finite_values(
    empty_db,
    field,
    value,
):
    payload = provider_payload()
    payload[field] = value

    with empty_db.session() as session:
        with pytest.raises(ValueError, match=field):
            LLMStore().create_provider(session, **payload)


def test_provider_name_is_unique_and_update_preserves_identity(empty_db):
    store = LLMStore()
    with empty_db.session() as session:
        provider = store.create_provider(session, **provider_payload())
        provider_id = provider.id
        with pytest.raises(LLMProviderConflictError):
            store.create_provider(session, **provider_payload())
        updated = store.update_provider(
            session,
            provider_id,
            display_name="Renamed",
            default_model="model-v2",
        )

        assert updated.id == provider_id
        assert updated.display_name == "Renamed"
        assert updated.default_model == "model-v2"


def test_replace_route_chain_is_ordered_validated_and_atomic(empty_db):
    store = LLMStore()
    with empty_db.session() as session:
        first = store.create_provider(
            session,
            **provider_payload("provider-first"),
        )
        second_payload = provider_payload("provider-second")
        second_payload["api_key_env"] = "PROVIDER_SECOND_API_KEY"
        second = store.create_provider(session, **second_payload)
        store.replace_route_chain(
            session,
            "strategy",
            [
                {"provider_id": second.id, "priority": 20},
                {
                    "provider_id": first.id,
                    "priority": 10,
                    "model_override": "strategy-model",
                },
            ],
        )

        chain = store.get_route_chain(session, "strategy")
        assert [entry.provider_id for entry in chain] == [
            first.id,
            second.id,
        ]
        assert chain[0].model_override == "strategy-model"
        assert chain[1].model_override is None

        with pytest.raises(LLMRouteValidationError):
            store.replace_route_chain(
                session,
                "strategy",
                [{"provider_id": "missing-provider", "priority": 1}],
            )
        assert [
            entry.provider_id
            for entry in store.get_route_chain(session, "strategy")
        ] == [first.id, second.id]

        with pytest.raises(LLMRouteValidationError):
            store.replace_route_chain(session, "made-up-route", [])


@pytest.mark.parametrize(
    "entry",
    [
        {"provider_id": "placeholder", "priority": 1.9},
        {"provider_id": "placeholder", "priority": True},
        {
            "provider_id": "placeholder",
            "priority": 10,
            "enabled": "false",
        },
    ],
)
def test_replace_route_chain_rejects_ambiguous_types(empty_db, entry):
    store = LLMStore()
    with empty_db.session() as session:
        provider = store.create_provider(session, **provider_payload())
        entry["provider_id"] = provider.id

        with pytest.raises(LLMRouteValidationError):
            store.replace_route_chain(session, "default", [entry])


def test_delete_provider_fails_while_route_references_it(empty_db):
    store = LLMStore()
    with empty_db.session() as session:
        provider = store.create_provider(session, **provider_payload())
        store.replace_route_chain(
            session,
            "default",
            [{"provider_id": provider.id, "priority": 10}],
        )

        with pytest.raises(LLMProviderInUseError):
            store.delete_provider(session, provider.id)

        store.replace_route_chain(session, "default", [])
        store.delete_provider(session, provider.id)
        assert store.list_providers(session) == []


def test_request_log_records_metadata_only_and_usage_summary(empty_db):
    store = LLMStore()
    now = datetime.utcnow()
    with empty_db.session() as session:
        provider = store.create_provider(session, **provider_payload())
        store.record_request(
            session,
            route_key="qualification",
            provider_id=provider.id,
            model="model-main",
            status="success",
            input_tokens=100,
            output_tokens=40,
            latency_ms=250,
            fallback_used=False,
            created_at=now - timedelta(seconds=1),
        )
        store.record_request(
            session,
            route_key="qualification",
            provider_id=provider.id,
            model="model-main",
            status="failed",
            error_category="rate_limit",
            latency_ms=750,
            fallback_used=True,
            created_at=now,
        )
        summary = store.usage_summary(
            session,
            route_key="qualification",
        )

    assert summary == {
        "request_count": 2,
        "success_count": 1,
        "failure_count": 1,
        "input_tokens": 100,
        "output_tokens": 40,
        "total_tokens": 140,
        "fallback_count": 1,
        "average_latency_ms": 500.0,
    }
    log_columns = {
        column["name"]
        for column in inspect(empty_db.engine).get_columns("llm_request_logs")
    }
    assert {
        "prompt",
        "response",
        "api_key",
        "error_body",
    }.isdisjoint(log_columns)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("fallback_used", "false"),
        ("latency_ms", float("inf")),
        ("latency_ms", float("nan")),
        ("input_tokens", 1.5),
        ("output_tokens", True),
    ],
)
def test_request_log_rejects_ambiguous_or_non_finite_usage(
    empty_db,
    field,
    value,
):
    store = LLMStore()
    with empty_db.session() as session:
        provider = store.create_provider(session, **provider_payload())
        payload = {
            "route_key": "default",
            "provider_id": provider.id,
            "model": "model-main",
            "status": "success",
            field: value,
        }
        with pytest.raises(ValueError, match=field):
            store.record_request(session, **payload)


def test_route_replace_and_provider_delete_cannot_leave_orphans(empty_db):
    store = LLMStore()
    with empty_db.session() as session:
        provider = store.create_provider(session, **provider_payload())
        provider_id = provider.id

    barrier = Barrier(2)

    def replace():
        try:
            with empty_db.session() as session:
                session.execute(text("SELECT 1"))
                barrier.wait()
                store.replace_route_chain(
                    session,
                    "default",
                    [{"provider_id": provider_id, "priority": 10}],
                )
            return "replaced"
        except LLMRouteValidationError:
            return "provider_missing"

    def remove():
        try:
            with empty_db.session() as session:
                session.execute(text("SELECT 1"))
                barrier.wait()
                store.delete_provider(session, provider_id)
            return "deleted"
        except LLMProviderInUseError:
            return "in_use"

    with ThreadPoolExecutor(max_workers=2) as executor:
        replace_future = executor.submit(replace)
        delete_future = executor.submit(remove)
        outcomes = {replace_future.result(), delete_future.result()}

    assert outcomes in (
        {"replaced", "in_use"},
        {"provider_missing", "deleted"},
    )
    with empty_db.session() as session:
        orphan_count = session.scalar(
            select(func.count(LLMRoute.id)).where(
                LLMRoute.provider_id == provider_id
            )
        )
        provider_exists = store.list_providers(session)
        if provider_exists:
            assert orphan_count == 1
        else:
            assert orphan_count == 0


class FakeClock:
    def __init__(self) -> None:
        self.value = 1_000.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def configure_router(
    database,
    *,
    route_key: str = "strategy",
    provider_count: int = 2,
):
    store = LLMStore()
    adapters = {}
    provider_ids = []
    with database.session() as session:
        for index in range(provider_count):
            payload = provider_payload(f"router-provider-{index + 1}")
            payload["api_key_env"] = f"ROUTER_PROVIDER_{index + 1}_KEY"
            provider = store.create_provider(session, **payload)
            provider_ids.append(provider.id)
            adapters[provider.id] = SimpleNamespace(chat=AsyncMock())
        store.replace_route_chain(
            session,
            route_key,
            [
                {
                    "provider_id": provider_id,
                    "priority": (index + 1) * 10,
                }
                for index, provider_id in enumerate(provider_ids)
            ],
        )
    clock = FakeClock()
    router = LLMRouter(
        database=database,
        provider_factory=lambda config: adapters[config.id],
        clock=clock,
    )
    return router, provider_ids, adapters, clock


def completion(
    text_value: str,
    *,
    model: str = "model-main",
    input_tokens: int = 5,
    output_tokens: int = 3,
) -> LLMCompletion:
    return LLMCompletion(
        text=text_value,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
    )


@pytest.mark.asyncio
async def test_router_uses_route_priority(empty_db):
    router, provider_ids, adapters, _clock = configure_router(empty_db)
    adapters[provider_ids[0]].chat.return_value = completion("first")
    adapters[provider_ids[1]].chat.return_value = completion("second")

    result = await router.chat(route="strategy", prompt="hello")

    assert result == "first"
    adapters[provider_ids[0]].chat.assert_awaited_once()
    adapters[provider_ids[1]].chat.assert_not_awaited()


@pytest.mark.asyncio
async def test_retryable_failure_falls_back_and_records_usage(empty_db):
    router, provider_ids, adapters, _clock = configure_router(empty_db)
    adapters[provider_ids[0]].chat.side_effect = LLMProviderError(
        "timeout",
        retryable=True,
        category="timeout",
    )
    adapters[provider_ids[1]].chat.return_value = completion("backup")

    result = await router.chat(route="strategy", prompt="private prompt")

    assert result == "backup"
    adapters[provider_ids[1]].chat.assert_awaited_once()
    assert await router.flush_telemetry()
    with empty_db.session() as session:
        logs = list(
            session.scalars(
                select(LLMRequestLog).order_by(LLMRequestLog.id)
            )
        )
        assert [log.status for log in logs] == ["failed", "success"]
        assert [log.fallback_used for log in logs] == [False, True]
        assert logs[0].error_category == "timeout"
        assert sum(log.total_tokens for log in logs) == 8
        assert all(
            "private prompt" not in repr(log)
            for log in logs
        )


@pytest.mark.asyncio
async def test_auth_failure_does_not_fallback(empty_db):
    router, provider_ids, adapters, _clock = configure_router(empty_db)
    adapters[provider_ids[0]].chat.side_effect = LLMProviderError(
        "unauthorized",
        retryable=False,
        category="authentication",
    )

    with pytest.raises(LLMRouteError) as raised:
        await router.chat(route="strategy", prompt="hello")

    assert raised.value.error_category == "authentication"
    adapters[provider_ids[1]].chat.assert_not_awaited()


@pytest.mark.asyncio
async def test_router_attempts_at_most_three_providers(empty_db):
    router, provider_ids, adapters, _clock = configure_router(
        empty_db,
        provider_count=4,
    )
    for provider_id in provider_ids:
        adapters[provider_id].chat.side_effect = LLMProviderError(
            "upstream_unavailable",
            retryable=True,
            category="upstream_unavailable",
        )

    with pytest.raises(LLMRouteError):
        await router.chat(route="strategy", prompt="hello")

    assert [
        adapters[provider_id].chat.await_count
        for provider_id in provider_ids
    ] == [1, 1, 1, 0]


@pytest.mark.asyncio
async def test_circuit_opens_after_three_retryable_failures(empty_db):
    router, provider_ids, adapters, _clock = configure_router(empty_db)
    primary_id, backup_id = provider_ids
    adapters[primary_id].chat.side_effect = LLMProviderError(
        "timeout",
        retryable=True,
        category="timeout",
    )
    adapters[backup_id].chat.return_value = completion("backup")

    for _ in range(3):
        assert (
            await router.chat(route="strategy", prompt="hello")
        ) == "backup"

    assert router.circuits[primary_id].state == "open"
    await router.chat(route="strategy", prompt="hello")
    assert adapters[primary_id].chat.await_count == 3
    assert adapters[backup_id].chat.await_count == 4


@pytest.mark.asyncio
async def test_half_open_probe_closes_circuit_after_cooldown(empty_db):
    router, provider_ids, adapters, clock = configure_router(empty_db)
    primary_id, backup_id = provider_ids
    adapters[primary_id].chat.side_effect = LLMProviderError(
        "timeout",
        retryable=True,
        category="timeout",
    )
    adapters[backup_id].chat.return_value = completion("backup")
    for _ in range(3):
        await router.chat(route="strategy", prompt="hello")
    adapters[primary_id].chat.side_effect = None
    adapters[primary_id].chat.return_value = completion("recovered")
    clock.advance(60)

    result = await router.chat(route="strategy", prompt="hello")

    assert result == "recovered"
    assert router.circuits[primary_id].state == "closed"
    assert router.circuits[primary_id].consecutive_failures == 0


@pytest.mark.asyncio
async def test_json_completion_records_parse_failure_without_content(
    empty_db,
):
    router, provider_ids, adapters, _clock = configure_router(empty_db)
    adapters[provider_ids[0]].chat.return_value = completion(
        "not valid json and must stay out of logs"
    )

    with pytest.raises(LLMRouteError) as raised:
        await router.json_completion(
            route="strategy",
            prompt="secret request",
        )

    assert raised.value.error_category == "invalid_json"
    adapters[provider_ids[1]].chat.assert_not_awaited()
    assert await router.flush_telemetry()
    with empty_db.session() as session:
        log = session.scalar(select(LLMRequestLog))
        assert log is not None
        assert log.status == "failed"
        assert log.error_category == "invalid_json"
        assert "not valid json" not in repr(log)
        assert "secret request" not in repr(log)


@pytest.mark.parametrize(
    ("error", "retryable", "category"),
    [
        (SimpleNamespace(status_code=408), True, "timeout"),
        (SimpleNamespace(status_code=429), True, "rate_limit"),
        (SimpleNamespace(status_code=503), True, "upstream_server"),
        (SimpleNamespace(status_code=401), False, "authentication"),
        (SimpleNamespace(status_code=400), False, "invalid_request"),
        (ValueError("bad local config"), False, "invalid_request"),
        (ConnectionError("network"), True, "network"),
    ],
)
def test_provider_failure_classification(error, retryable, category):
    classified = classify_provider_exception(error)

    assert classified.retryable is retryable
    assert classified.category == category
    assert str(error) not in repr(classified)


@pytest.mark.asyncio
async def test_openai_provider_reads_env_and_rotates_cached_client(
    monkeypatch,
):
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="ok"),
            )
        ],
        model="upstream-model",
        usage=SimpleNamespace(
            prompt_tokens=7,
            completion_tokens=4,
            total_tokens=11,
        ),
    )
    clients = []

    def client_factory(**kwargs):
        client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(
                    create=AsyncMock(return_value=response),
                )
            )
        )
        clients.append((kwargs, client))
        return client

    config = LLMProviderConfig(
        id="provider-1",
        name="provider-main",
        base_url="https://llm.example.test/v1",
        default_model="model-main",
        api_key_env="PROVIDER_MAIN_API_KEY",
        timeout_seconds=30,
        updated_at=datetime.utcnow(),
    )
    provider = OpenAICompatibleProvider(
        config,
        client_factory=client_factory,
    )
    monkeypatch.setenv("PROVIDER_MAIN_API_KEY", "first-secret")
    first = await provider.chat(
        prompt="hello",
        system="system",
        model="model-main",
    )
    monkeypatch.setenv("PROVIDER_MAIN_API_KEY", "rotated-secret")
    second = await provider.chat(
        prompt="hello",
        system=None,
        model="model-main",
    )

    assert first.text == second.text == "ok"
    assert first.total_tokens == 11
    assert len(clients) == 2
    assert clients[0][0]["api_key"] == "first-secret"
    assert clients[1][0]["api_key"] == "rotated-secret"
    assert all(kwargs["max_retries"] == 0 for kwargs, _client in clients)
    assert "first-secret" not in repr(provider)
    assert "rotated-secret" not in repr(provider)


@pytest.mark.asyncio
async def test_openai_provider_missing_secret_is_non_retryable(
    monkeypatch,
):
    monkeypatch.delenv("PROVIDER_MAIN_API_KEY", raising=False)
    provider = OpenAICompatibleProvider(
        LLMProviderConfig(
            id="provider-1",
            name="provider-main",
            base_url="https://llm.example.test/v1",
            default_model="model-main",
            api_key_env="PROVIDER_MAIN_API_KEY",
            timeout_seconds=30,
            updated_at=datetime.utcnow(),
        )
    )

    with pytest.raises(LLMProviderError) as raised:
        await provider.chat(
            prompt="hello",
            system=None,
            model="model-main",
        )

    assert raised.value.retryable is False
    assert raised.value.category == "configuration"


@pytest.mark.asyncio
async def test_only_one_half_open_probe_runs_concurrently(empty_db):
    router, provider_ids, adapters, clock = configure_router(empty_db)
    primary_id, backup_id = provider_ids
    adapters[primary_id].chat.side_effect = LLMProviderError(
        "private upstream timeout",
        retryable=True,
        category="timeout",
    )
    adapters[backup_id].chat.return_value = completion("backup")
    for _ in range(3):
        assert await router.chat("hello", route="strategy") == "backup"

    probe_started = asyncio.Event()
    release_probe = asyncio.Event()

    async def probe(**_kwargs):
        probe_started.set()
        await release_probe.wait()
        return completion("recovered")

    adapters[primary_id].chat.side_effect = probe
    clock.advance(60)
    first = asyncio.create_task(router.chat("hello", route="strategy"))
    await probe_started.wait()
    second = asyncio.create_task(router.chat("hello", route="strategy"))
    assert await second == "backup"
    assert adapters[primary_id].chat.await_count == 4

    release_probe.set()
    assert await first == "recovered"
    assert router.circuits[primary_id].state == "closed"


class StatusProviderError(RuntimeError):
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__("private upstream response body")


@pytest.mark.parametrize(
    ("status_code", "category"),
    [
        (400, "invalid_request"),
        (401, "authentication"),
        (403, "authentication"),
        (404, "invalid_request"),
        (422, "invalid_request"),
    ],
)
@pytest.mark.asyncio
async def test_non_retryable_http_status_never_falls_back(
    empty_db,
    status_code,
    category,
):
    router, provider_ids, adapters, _clock = configure_router(empty_db)
    adapters[provider_ids[0]].chat.side_effect = StatusProviderError(
        status_code
    )

    with pytest.raises(LLMRouteError) as raised:
        await router.chat("secret prompt", route="strategy")

    assert raised.value.error_category == category
    assert "private upstream response body" not in repr(raised.value)
    assert "secret prompt" not in repr(raised.value)
    adapters[provider_ids[1]].chat.assert_not_awaited()


@pytest.mark.asyncio
async def test_openai_provider_rebuilds_client_when_config_changes(
    monkeypatch,
):
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
        model="model-main",
        usage=None,
    )
    clients = []

    def client_factory(**kwargs):
        client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(
                    create=AsyncMock(return_value=response),
                )
            )
        )
        clients.append((kwargs, client))
        return client

    config = LLMProviderConfig(
        id="provider-1",
        name="provider-main",
        base_url="https://first.example.test/v1",
        default_model="model-main",
        api_key_env="PROVIDER_MAIN_API_KEY",
        timeout_seconds=30,
        updated_at=datetime.utcnow(),
    )
    monkeypatch.setenv("PROVIDER_MAIN_API_KEY", "private-key")
    provider = OpenAICompatibleProvider(
        config,
        client_factory=client_factory,
    )
    await provider.chat(prompt="hello", system=None, model="model-main")

    provider.configure(
        replace(
            config,
            base_url="https://second.example.test/v1",
            timeout_seconds=45,
            updated_at=config.updated_at + timedelta(seconds=1),
        )
    )
    await provider.chat(prompt="hello", system=None, model="model-main")

    assert len(clients) == 2
    assert clients[0][0]["base_url"] == "https://first.example.test/v1"
    assert clients[1][0]["base_url"] == "https://second.example.test/v1"
    assert clients[1][0]["timeout"] == 45
    assert "private-key" not in repr(provider)


def test_legacy_client_constructor_and_getter_share_router(
    monkeypatch,
):
    expected = object()
    monkeypatch.setattr(legacy_client, "get_llm_router", lambda: expected)

    assert legacy_client.LLMClient() is expected
    assert legacy_client.get_llm_client() is expected


def test_legacy_client_getter_honors_temporary_injection(monkeypatch):
    injected = object()
    monkeypatch.setattr(legacy_client, "_client", injected)

    assert legacy_client.get_llm_client() is injected


@pytest.mark.asyncio
async def test_json_completion_accepts_positional_prompt(empty_db):
    router, provider_ids, adapters, _clock = configure_router(
        empty_db,
        route_key="default",
    )
    adapters[provider_ids[0]].chat.return_value = completion(
        '{"qualified": true}'
    )

    result = await router.json_completion("private prompt")

    assert result == {"qualified": True}
    adapters[provider_ids[0]].chat.assert_awaited_once()


@pytest.mark.asyncio
async def test_non_retryable_failure_resets_retryable_failure_streak(
    empty_db,
):
    router, provider_ids, adapters, _clock = configure_router(empty_db)
    primary_id, backup_id = provider_ids
    adapters[backup_id].chat.return_value = completion("backup")
    adapters[primary_id].chat.side_effect = LLMProviderError(
        retryable=True,
        category="timeout",
    )
    for _ in range(2):
        assert await router.chat("hello", route="strategy") == "backup"
    assert router.circuits[primary_id].consecutive_failures == 2

    adapters[primary_id].chat.side_effect = LLMProviderError(
        retryable=False,
        category="invalid_request",
    )
    with pytest.raises(LLMRouteError):
        await router.chat("hello", route="strategy")
    assert router.circuits[primary_id].state == "closed"
    assert router.circuits[primary_id].consecutive_failures == 0

    adapters[primary_id].chat.side_effect = LLMProviderError(
        retryable=True,
        category="timeout",
    )
    assert await router.chat("hello", route="strategy") == "backup"
    assert router.circuits[primary_id].state == "closed"
    assert router.circuits[primary_id].consecutive_failures == 1


@pytest.mark.asyncio
async def test_open_primary_marks_next_provider_as_fallback(empty_db):
    router, provider_ids, adapters, _clock = configure_router(empty_db)
    primary_id, backup_id = provider_ids
    adapters[primary_id].chat.side_effect = LLMProviderError(
        retryable=True,
        category="timeout",
    )
    adapters[backup_id].chat.return_value = completion("backup")
    for _ in range(3):
        await router.chat("hello", route="strategy")

    assert await router.chat("hello", route="strategy") == "backup"
    assert await router.flush_telemetry()

    with empty_db.session() as session:
        latest = session.scalar(
            select(LLMRequestLog).order_by(LLMRequestLog.id.desc())
        )
        assert latest is not None
        assert latest.provider_id == backup_id
        assert latest.fallback_used is True


def test_provider_error_default_category_is_safe():
    error = LLMProviderError(
        "private upstream response body",
        retryable=True,
    )

    assert error.category == "provider_error"
    assert "private upstream response body" not in str(error)
    assert "private upstream response body" not in repr(error)


@pytest.mark.asyncio
async def test_provider_client_fingerprint_does_not_store_raw_api_key(
    monkeypatch,
):
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
        model="model-main",
        usage=None,
    )

    def client_factory(**_kwargs):
        return SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(
                    create=AsyncMock(return_value=response),
                )
            )
        )

    config = LLMProviderConfig(
        id="provider-1",
        name="provider-main",
        base_url="https://llm.example.test/v1",
        default_model="model-main",
        api_key_env="PROVIDER_MAIN_API_KEY",
        timeout_seconds=30,
        updated_at=datetime.utcnow(),
    )
    monkeypatch.setenv(
        "PROVIDER_MAIN_API_KEY",
        "raw-key-must-not-enter-fingerprint",
    )
    provider = OpenAICompatibleProvider(
        config,
        client_factory=client_factory,
    )

    await provider.chat(prompt="hello", system=None, model="model-main")

    assert (
        "raw-key-must-not-enter-fingerprint"
        not in repr(provider._client_fingerprint)
    )


@pytest.mark.asyncio
async def test_skipped_circuits_do_not_consume_actual_attempt_budget(
    empty_db,
):
    router, provider_ids, adapters, clock = configure_router(
        empty_db,
        provider_count=5,
    )
    router.circuits[provider_ids[0]] = CircuitState(
        state="open",
        consecutive_failures=CIRCUIT_FAILURE_THRESHOLD,
        opened_at=clock(),
    )
    router.circuits[provider_ids[1]] = CircuitState(
        state="half_open",
        consecutive_failures=CIRCUIT_FAILURE_THRESHOLD,
        probe_in_flight=True,
    )
    for provider_id in provider_ids[2:4]:
        adapters[provider_id].chat.side_effect = LLMProviderError(
            retryable=True,
            category="timeout",
        )
    adapters[provider_ids[4]].chat.return_value = completion("fifth")

    result = await router.chat("hello", route="strategy")

    assert result == "fifth"
    assert [
        adapters[provider_id].chat.await_count
        for provider_id in provider_ids
    ] == [0, 0, 1, 1, 1]
    assert await router.flush_telemetry()
    with empty_db.session() as session:
        latest = session.scalar(
            select(LLMRequestLog).order_by(LLMRequestLog.id.desc())
        )
        assert latest is not None
        assert latest.provider_id == provider_ids[4]
        assert latest.fallback_used is True


@pytest.mark.parametrize("status_code", [409, 418, 501])
@pytest.mark.asyncio
async def test_other_http_status_is_non_retryable_and_never_falls_back(
    empty_db,
    status_code,
):
    router, provider_ids, adapters, _clock = configure_router(empty_db)
    adapters[provider_ids[0]].chat.side_effect = StatusProviderError(
        status_code
    )

    with pytest.raises(LLMRouteError) as raised:
        await router.chat("private prompt", route="strategy")

    assert raised.value.error_category == "http_error"
    adapters[provider_ids[1]].chat.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancelled_half_open_probe_reopens_without_log_or_fallback(
    empty_db,
):
    router, provider_ids, adapters, clock = configure_router(empty_db)
    primary_id, backup_id = provider_ids
    router.circuits[primary_id] = CircuitState(
        state="open",
        consecutive_failures=CIRCUIT_FAILURE_THRESHOLD,
        opened_at=clock() - 60,
    )
    adapters[primary_id].chat.side_effect = asyncio.CancelledError()
    adapters[backup_id].chat.return_value = completion("backup")

    with pytest.raises(asyncio.CancelledError):
        await router.chat("hello", route="strategy")

    circuit = router.circuits[primary_id]
    assert circuit.state == "open"
    assert circuit.consecutive_failures == CIRCUIT_FAILURE_THRESHOLD
    assert circuit.probe_in_flight is False
    adapters[backup_id].chat.assert_not_awaited()
    with empty_db.session() as session:
        assert session.scalar(
            select(func.count()).select_from(LLMRequestLog)
        ) == 0


@pytest.mark.asyncio
async def test_half_open_non_retryable_failure_reopens_circuit(
    empty_db,
):
    router, provider_ids, adapters, clock = configure_router(empty_db)
    primary_id, backup_id = provider_ids
    router.circuits[primary_id] = CircuitState(
        state="open",
        consecutive_failures=CIRCUIT_FAILURE_THRESHOLD,
        opened_at=clock() - 60,
    )
    adapters[primary_id].chat.side_effect = LLMProviderError(
        retryable=False,
        category="authentication",
    )

    with pytest.raises(LLMRouteError) as raised:
        await router.chat("hello", route="strategy")

    assert raised.value.error_category == "authentication"
    circuit = router.circuits[primary_id]
    assert circuit.state == "open"
    assert circuit.consecutive_failures == CIRCUIT_FAILURE_THRESHOLD
    assert circuit.opened_at == clock()
    assert circuit.probe_in_flight is False
    adapters[backup_id].chat.assert_not_awaited()


@pytest.mark.asyncio
async def test_half_open_invalid_json_reopens_without_fallback(empty_db):
    router, provider_ids, adapters, clock = configure_router(empty_db)
    primary_id, backup_id = provider_ids
    router.circuits[primary_id] = CircuitState(
        state="open",
        consecutive_failures=CIRCUIT_FAILURE_THRESHOLD,
        opened_at=clock() - 60,
    )
    adapters[primary_id].chat.return_value = completion("invalid json")

    with pytest.raises(LLMRouteError) as raised:
        await router.json_completion("hello", route="strategy")

    assert raised.value.error_category == "invalid_json"
    circuit = router.circuits[primary_id]
    assert circuit.state == "open"
    assert circuit.consecutive_failures == CIRCUIT_FAILURE_THRESHOLD
    assert circuit.opened_at == clock()
    assert circuit.probe_in_flight is False
    adapters[backup_id].chat.assert_not_awaited()


@pytest.mark.asyncio
async def test_stale_closed_result_cannot_steal_half_open_probe(empty_db):
    router, provider_ids, adapters, clock = configure_router(empty_db)
    primary_id, backup_id = provider_ids
    old_started = asyncio.Event()
    release_old = asyncio.Event()
    probe_started = asyncio.Event()
    release_probe = asyncio.Event()

    async def primary_chat(*, prompt, **_kwargs):
        if prompt == "old":
            old_started.set()
            await release_old.wait()
            return completion("old-success")
        if prompt.startswith("fail-"):
            raise LLMProviderError(
                retryable=True,
                category="timeout",
            )
        if prompt == "probe":
            probe_started.set()
            await release_probe.wait()
            return completion("probe-success")
        return completion("unexpected-primary")

    adapters[primary_id].chat.side_effect = primary_chat
    adapters[backup_id].chat.return_value = completion("backup")

    old_task = asyncio.create_task(
        router.chat("old", route="strategy")
    )
    await old_started.wait()
    for index in range(3):
        assert (
            await router.chat(f"fail-{index}", route="strategy")
        ) == "backup"
    assert router.circuits[primary_id].state == "open"

    clock.advance(60)
    probe_task = asyncio.create_task(
        router.chat("probe", route="strategy")
    )
    await probe_started.wait()
    release_old.set()
    assert await old_task == "old-success"

    circuit = router.circuits[primary_id]
    assert circuit.state == "half_open"
    assert circuit.probe_in_flight is True
    new_result = await router.chat("new", route="strategy")

    release_probe.set()
    assert await probe_task == "probe-success"
    assert new_result == "backup"
    assert adapters[primary_id].chat.await_count == 5
    assert router.circuits[primary_id].state == "closed"


@pytest.mark.asyncio
async def test_telemetry_failure_cannot_change_success_control_flow(
    empty_db,
    caplog,
):
    router, provider_ids, adapters, _clock = configure_router(empty_db)
    adapters[provider_ids[0]].chat.return_value = completion("primary")

    def broken_record(*_args, **_kwargs):
        raise RuntimeError("private database exception body")

    router.store.record_request = broken_record
    with caplog.at_level(logging.WARNING):
        result = await router.chat("secret prompt", route="strategy")
        assert await router.flush_telemetry()

    assert result == "primary"
    adapters[provider_ids[1]].chat.assert_not_awaited()
    assert router.circuits[provider_ids[0]].consecutive_failures == 0
    assert router.telemetry_failures == 1
    assert "private database exception body" not in caplog.text
    assert "secret prompt" not in caplog.text


@pytest.mark.asyncio
async def test_telemetry_failure_does_not_block_normal_fallback(
    empty_db,
):
    router, provider_ids, adapters, _clock = configure_router(empty_db)
    adapters[provider_ids[0]].chat.side_effect = LLMProviderError(
        retryable=True,
        category="timeout",
    )
    adapters[provider_ids[1]].chat.return_value = completion("backup")

    def broken_record(*_args, **_kwargs):
        raise RuntimeError("private database exception body")

    router.store.record_request = broken_record

    assert await router.chat("secret prompt", route="strategy") == "backup"
    assert await router.flush_telemetry()
    assert router.circuits[provider_ids[0]].consecutive_failures == 1
    assert router.circuits[provider_ids[1]].consecutive_failures == 0
    assert router.telemetry_failures == 2


@pytest.mark.asyncio
async def test_route_database_read_does_not_block_event_loop(empty_db):
    router, provider_ids, adapters, _clock = configure_router(empty_db)
    adapters[provider_ids[0]].chat.return_value = completion("ok")
    original_get = router.store.get_route_chain
    started = Event()
    worker_threads = []

    def slow_get(*args, **kwargs):
        worker_threads.append(get_ident())
        started.set()
        time.sleep(0.2)
        return original_get(*args, **kwargs)

    router.store.get_route_chain = slow_get
    loop_thread = get_ident()
    started_at = asyncio.get_running_loop().time()
    task = asyncio.create_task(router.chat("hello", route="strategy"))
    while not started.is_set():
        await asyncio.sleep(0)
    observed_delay = asyncio.get_running_loop().time() - started_at

    assert await task == "ok"
    assert observed_delay < 0.12
    assert worker_threads and worker_threads[0] != loop_thread


@pytest.mark.asyncio
async def test_telemetry_database_write_does_not_block_event_loop(
    empty_db,
):
    router, provider_ids, adapters, _clock = configure_router(empty_db)
    adapters[provider_ids[0]].chat.return_value = completion("ok")
    original_record = router.store.record_request
    started = Event()
    worker_threads = []

    def slow_record(*args, **kwargs):
        worker_threads.append(get_ident())
        started.set()
        time.sleep(0.2)
        return original_record(*args, **kwargs)

    router.store.record_request = slow_record
    loop_thread = get_ident()
    started_at = asyncio.get_running_loop().time()
    task = asyncio.create_task(router.chat("hello", route="strategy"))
    while not started.is_set():
        await asyncio.sleep(0)
    observed_delay = asyncio.get_running_loop().time() - started_at

    assert await task == "ok"
    assert observed_delay < 0.12
    assert worker_threads and worker_threads[0] != loop_thread


@pytest.mark.asyncio
async def test_provider_rotation_does_not_close_in_flight_client(
    monkeypatch,
):
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    clients = []
    factory_kwargs = []

    def response(text_value):
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=text_value),
                )
            ],
            model="model-main",
            usage=None,
        )

    def client_factory(**kwargs):
        index = len(clients)
        factory_kwargs.append(kwargs)

        async def create(**_request):
            if index == 0:
                first_started.set()
                await release_first.wait()
                return response("first")
            return response("second")

        client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=create)
            ),
            close=AsyncMock(),
        )
        clients.append(client)
        return client

    config = LLMProviderConfig(
        id="provider-1",
        name="provider-main",
        base_url="https://llm.example.test/v1",
        default_model="model-main",
        api_key_env="PROVIDER_MAIN_API_KEY",
        timeout_seconds=30,
        updated_at=datetime.utcnow(),
    )
    monkeypatch.setenv("PROVIDER_MAIN_API_KEY", "first-key")
    provider = OpenAICompatibleProvider(
        config,
        client_factory=client_factory,
    )
    first_task = asyncio.create_task(
        provider.chat(prompt="one", system=None, model="model-main")
    )
    await first_started.wait()

    monkeypatch.setenv("PROVIDER_MAIN_API_KEY", "second-key")
    second = await provider.chat(
        prompt="two",
        system=None,
        model="model-main",
    )

    assert second.text == "second"
    first_close_count_while_in_flight = clients[0].close.await_count
    assert all(kwargs["max_retries"] == 0 for kwargs in factory_kwargs)

    release_first.set()
    first = await first_task
    assert first.text == "first"
    assert first_close_count_while_in_flight == 0
    assert clients[0].close.await_count == 1
    assert clients[1].close.await_count == 0

    await provider.aclose()
    assert clients[1].close.await_count == 1


@pytest.mark.asyncio
async def test_provider_aclose_waits_for_in_flight_and_rejects_new_calls(
    monkeypatch,
):
    started = asyncio.Event()
    release = asyncio.Event()

    async def create(**_kwargs):
        started.set()
        await release.wait()
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="done"),
                )
            ],
            model="model-main",
            usage=None,
        )

    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=create)
        ),
        close=AsyncMock(),
    )
    monkeypatch.setenv("PROVIDER_MAIN_API_KEY", "key")
    provider = OpenAICompatibleProvider(
        LLMProviderConfig(
            id="provider-1",
            name="provider-main",
            base_url="https://llm.example.test/v1",
            default_model="model-main",
            api_key_env="PROVIDER_MAIN_API_KEY",
            timeout_seconds=30,
            updated_at=datetime.utcnow(),
        ),
        client_factory=lambda **_kwargs: client,
    )
    chat_task = asyncio.create_task(
        provider.chat(prompt="hello", system=None, model="model-main")
    )
    await started.wait()
    close_method = getattr(provider, "aclose", None)
    if not callable(close_method):
        release.set()
        await chat_task
        pytest.fail("provider.aclose is missing")
    close_task = asyncio.create_task(close_method())
    await asyncio.sleep(0)
    assert client.close.await_count == 0

    release.set()
    await chat_task
    await close_task
    assert client.close.await_count == 1
    with pytest.raises(LLMProviderError) as raised:
        await provider.chat(
            prompt="after-close",
            system=None,
            model="model-main",
        )
    assert raised.value.retryable is False


@pytest.mark.asyncio
async def test_provider_aclose_waits_for_retired_client_close(
    monkeypatch,
):
    request_started = asyncio.Event()
    release_request = asyncio.Event()
    retired_close_started = asyncio.Event()
    release_retired_close = asyncio.Event()
    clients = []

    def response(text_value):
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=text_value),
                )
            ],
            model="model-main",
            usage=None,
        )

    def client_factory(**_kwargs):
        index = len(clients)

        async def create(**_request):
            if index == 0:
                request_started.set()
                await release_request.wait()
            return response(str(index))

        async def close():
            if index == 0:
                retired_close_started.set()
                await release_retired_close.wait()

        client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=create)
            ),
            close=AsyncMock(side_effect=close),
        )
        clients.append(client)
        return client

    monkeypatch.setenv("PROVIDER_MAIN_API_KEY", "first-key")
    provider = OpenAICompatibleProvider(
        LLMProviderConfig(
            id="provider-1",
            name="provider-main",
            base_url="https://llm.example.test/v1",
            default_model="model-main",
            api_key_env="PROVIDER_MAIN_API_KEY",
            timeout_seconds=30,
            updated_at=datetime.utcnow(),
        ),
        client_factory=client_factory,
    )
    first_task = asyncio.create_task(
        provider.chat(prompt="first", system=None, model="model-main")
    )
    await request_started.wait()
    monkeypatch.setenv("PROVIDER_MAIN_API_KEY", "second-key")
    await provider.chat(
        prompt="second",
        system=None,
        model="model-main",
    )

    release_request.set()
    await retired_close_started.wait()
    close_task = asyncio.create_task(provider.aclose())
    await asyncio.sleep(0)
    close_returned_early = close_task.done()

    release_retired_close.set()
    await first_task
    await close_task
    assert close_returned_early is False
    assert clients[0].close.await_count == 1
    assert clients[1].close.await_count == 1


@pytest.mark.asyncio
async def test_router_prunes_only_database_deleted_cached_provider(
    empty_db,
):
    store = LLMStore()
    with empty_db.session() as session:
        first = store.create_provider(
            session,
            **provider_payload("cached-first"),
        )
        second_payload = provider_payload("cached-second")
        second_payload["api_key_env"] = "CACHED_SECOND_API_KEY"
        second = store.create_provider(session, **second_payload)
        first_id = first.id
        second_id = second.id
        store.replace_route_chain(
            session,
            "strategy",
            [{"provider_id": first_id, "priority": 10}],
        )
        store.replace_route_chain(
            session,
            "collection",
            [{"provider_id": second_id, "priority": 10}],
        )

    class CachedProvider:
        def __init__(self):
            self.chat = AsyncMock(return_value=completion("ok"))
            self.aclose = AsyncMock()

        def configure(self, _config):
            return None

    first_cached = CachedProvider()
    second_cached = CachedProvider()
    router = LLMRouter(database=empty_db)
    router._providers = {
        first_id: first_cached,
        second_id: second_cached,
    }

    assert await router.chat("hello", route="strategy") == "ok"
    second_cached.aclose.assert_not_awaited()
    assert second_id in router._providers

    with empty_db.session() as session:
        store.replace_route_chain(session, "collection", [])
        store.delete_provider(session, second_id)

    assert await router.chat("hello", route="strategy") == "ok"
    for _ in range(20):
        if second_id not in router._providers:
            break
        await asyncio.sleep(0)
    second_cached.aclose.assert_awaited_once()
    assert first_id in router._providers
    assert second_id not in router._providers


@pytest.mark.asyncio
async def test_stale_provider_close_never_blocks_route_snapshot(empty_db):
    close_started = asyncio.Event()
    release_close = asyncio.Event()

    async def close():
        close_started.set()
        await release_close.wait()

    router = LLMRouter(database=empty_db)
    router._providers["deleted-provider"] = SimpleNamespace(aclose=close)
    try:
        await asyncio.wait_for(
            router._prune_provider_cache(frozenset()),
            timeout=0.05,
        )
        await close_started.wait()
        assert "deleted-provider" in router._providers
    finally:
        release_close.set()
        await router.aclose()

    assert "deleted-provider" not in router._providers


@pytest.mark.asyncio
async def test_router_aclose_closes_cache_and_rejects_new_calls(empty_db):
    router = LLMRouter(database=empty_db)
    cached = SimpleNamespace(aclose=AsyncMock())
    router._providers["provider-1"] = cached

    await router.aclose()
    await router.aclose()

    cached.aclose.assert_awaited_once()
    assert router._providers == {}
    with pytest.raises(LLMRouteError) as raised:
        await router.chat("hello", route="strategy")
    assert raised.value.error_category == "router_closed"


@pytest.mark.asyncio
async def test_aclose_global_router_does_not_create_one(monkeypatch):
    import tiktok_bot_core.llm.router as router_module

    close_global = getattr(router_module, "aclose_llm_router", None)
    assert callable(close_global)
    cached = SimpleNamespace(aclose=AsyncMock())
    monkeypatch.setattr(router_module, "_router", cached)

    await close_global()
    assert router_module._router is None
    cached.aclose.assert_awaited_once()

    await close_global()
    cached.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancelled_rotation_releases_reserved_client_and_finishes_retired_close(
    monkeypatch,
):
    retired_close_started = asyncio.Event()
    release_retired_close = asyncio.Event()
    retired_close_finished = asyncio.Event()
    second_request_started = asyncio.Event()
    clients = []

    def response(text_value):
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=text_value),
                )
            ],
            model="model-main",
            usage=None,
        )

    def client_factory(**_kwargs):
        index = len(clients)

        async def create(**_request):
            if index == 1:
                second_request_started.set()
                await asyncio.Event().wait()
            return response(str(index))

        async def close():
            if index == 0:
                retired_close_started.set()
                await release_retired_close.wait()
                retired_close_finished.set()

        client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=create)
            ),
            close=AsyncMock(side_effect=close),
        )
        clients.append(client)
        return client

    monkeypatch.setenv("PROVIDER_MAIN_API_KEY", "first-key")
    provider = OpenAICompatibleProvider(
        LLMProviderConfig(
            id="provider-1",
            name="provider-main",
            base_url="https://llm.example.test/v1",
            default_model="model-main",
            api_key_env="PROVIDER_MAIN_API_KEY",
            timeout_seconds=30,
            updated_at=datetime.utcnow(),
        ),
        client_factory=client_factory,
    )
    assert (
        await provider.chat(
            prompt="first",
            system=None,
            model="model-main",
        )
    ).text == "0"

    monkeypatch.setenv("PROVIDER_MAIN_API_KEY", "second-key")
    rotated = asyncio.create_task(
        provider.chat(
            prompt="second",
            system=None,
            model="model-main",
        )
    )
    await retired_close_started.wait()
    rotated.cancel()
    with pytest.raises(asyncio.CancelledError):
        await rotated

    release_retired_close.set()
    await asyncio.wait_for(provider.aclose(), timeout=0.5)

    assert retired_close_finished.is_set()
    assert clients[0].close.await_count == 1
    assert clients[1].close.await_count == 1
    assert second_request_started.is_set()


@pytest.mark.asyncio
async def test_cancelled_provider_aclose_keeps_close_running_for_retry(
    monkeypatch,
):
    close_started = asyncio.Event()
    release_close = asyncio.Event()
    close_finished = asyncio.Event()

    async def close():
        close_started.set()
        await release_close.wait()
        close_finished.set()

    response = SimpleNamespace(
        choices=[
            SimpleNamespace(message=SimpleNamespace(content="ok"))
        ],
        model="model-main",
        usage=None,
    )
    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=AsyncMock(return_value=response)
            )
        ),
        close=AsyncMock(side_effect=close),
    )
    monkeypatch.setenv("PROVIDER_MAIN_API_KEY", "key")
    provider = OpenAICompatibleProvider(
        LLMProviderConfig(
            id="provider-1",
            name="provider-main",
            base_url="https://llm.example.test/v1",
            default_model="model-main",
            api_key_env="PROVIDER_MAIN_API_KEY",
            timeout_seconds=30,
            updated_at=datetime.utcnow(),
        ),
        client_factory=lambda **_kwargs: client,
    )
    await provider.chat(prompt="hello", system=None, model="model-main")

    first_close = asyncio.create_task(provider.aclose())
    await close_started.wait()
    first_close.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first_close
    assert not close_finished.is_set()

    release_close.set()
    await asyncio.wait_for(provider.aclose(), timeout=0.5)

    assert close_finished.is_set()
    assert client.close.await_count == 1
    assert provider._slots == []


@pytest.mark.asyncio
async def test_failed_provider_close_remains_reachable_and_retries(
    monkeypatch,
):
    attempts = 0

    async def close():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("private close failure")

    response = SimpleNamespace(
        choices=[
            SimpleNamespace(message=SimpleNamespace(content="ok"))
        ],
        model="model-main",
        usage=None,
    )
    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=AsyncMock(return_value=response)
            )
        ),
        close=AsyncMock(side_effect=close),
    )
    monkeypatch.setenv("PROVIDER_MAIN_API_KEY", "key")
    provider = OpenAICompatibleProvider(
        LLMProviderConfig(
            id="provider-1",
            name="provider-main",
            base_url="https://llm.example.test/v1",
            default_model="model-main",
            api_key_env="PROVIDER_MAIN_API_KEY",
            timeout_seconds=30,
            updated_at=datetime.utcnow(),
        ),
        client_factory=lambda **_kwargs: client,
    )
    await provider.chat(prompt="hello", system=None, model="model-main")

    with pytest.raises(RuntimeError, match="private close failure"):
        await provider.aclose()
    assert provider._slots

    await provider.aclose()

    assert client.close.await_count == 2
    assert provider._slots == []


@pytest.mark.asyncio
async def test_cancelled_router_aclose_reuses_tracked_provider_close(
    empty_db,
):
    close_started = asyncio.Event()
    release_close = asyncio.Event()
    close_finished = asyncio.Event()

    async def close():
        close_started.set()
        await release_close.wait()
        close_finished.set()

    cached = SimpleNamespace(aclose=AsyncMock(side_effect=close))
    router = LLMRouter(database=empty_db)
    router._providers["provider-1"] = cached

    first_close = asyncio.create_task(router.aclose())
    await close_started.wait()
    first_close.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first_close
    assert router._providers["provider-1"] is cached

    release_close.set()
    await asyncio.wait_for(router.aclose(), timeout=0.5)

    assert close_finished.is_set()
    assert cached.aclose.await_count == 1
    assert router._providers == {}


@pytest.mark.asyncio
async def test_stale_provider_close_failure_stays_cached_until_retry(
    empty_db,
):
    store = LLMStore()
    with empty_db.session() as session:
        active = store.create_provider(
            session,
            **provider_payload("active-provider"),
        )
        stale_payload = provider_payload("stale-provider")
        stale_payload["api_key_env"] = "STALE_PROVIDER_API_KEY"
        stale = store.create_provider(session, **stale_payload)
        store.replace_route_chain(
            session,
            "strategy",
            [{"provider_id": active.id, "priority": 10}],
        )
        active_id = active.id
        stale_id = stale.id
        store.delete_provider(session, stale_id)

    active_cached = SimpleNamespace(
        chat=AsyncMock(return_value=completion("ok")),
        configure=lambda _config: None,
        aclose=AsyncMock(),
    )
    stale_cached = SimpleNamespace(
        aclose=AsyncMock(
            side_effect=[RuntimeError("private close failure"), None]
        )
    )
    router = LLMRouter(database=empty_db)
    router._providers = {
        active_id: active_cached,
        stale_id: stale_cached,
    }

    assert await router.chat("hello", route="strategy") == "ok"
    for _ in range(20):
        close_task = router._provider_close_tasks.get(stale_id)
        if close_task is not None and close_task.done():
            break
        await asyncio.sleep(0)
    assert router._providers[stale_id] is stale_cached

    assert await router.chat("hello", route="strategy") == "ok"
    for _ in range(20):
        if stale_id not in router._providers:
            break
        await asyncio.sleep(0)
    assert stale_id not in router._providers
    assert stale_cached.aclose.await_count == 2


@pytest.mark.asyncio
async def test_global_router_reference_survives_cancelled_close(monkeypatch):
    import tiktok_bot_core.llm.router as router_module

    first_started = asyncio.Event()
    release_first = asyncio.Event()

    async def close():
        first_started.set()
        await release_first.wait()

    cached = SimpleNamespace(aclose=AsyncMock(side_effect=close))
    monkeypatch.setattr(router_module, "_router", cached)

    first_close = asyncio.create_task(
        router_module.aclose_llm_router()
    )
    await first_started.wait()
    first_close.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first_close

    assert router_module._router is cached
    release_first.set()
    await router_module.aclose_llm_router()
    assert router_module._router is None
    assert cached.aclose.await_count == 2


@pytest.mark.asyncio
async def test_blocked_telemetry_does_not_delay_retryable_fallback(
    empty_db,
):
    router, provider_ids, adapters, _clock = configure_router(empty_db)
    adapters[provider_ids[0]].chat.side_effect = LLMProviderError(
        retryable=True,
        category="timeout",
    )
    adapters[provider_ids[1]].chat.return_value = completion("backup")
    record_started = Event()
    release_record = Event()
    original_record = router.store.record_request

    def blocked_record(*args, **kwargs):
        record_started.set()
        release_record.wait()
        return original_record(*args, **kwargs)

    router.store.record_request = blocked_record
    request = asyncio.create_task(
        router.chat("secret prompt", route="strategy")
    )
    try:
        while not record_started.is_set():
            await asyncio.sleep(0)
        assert await asyncio.wait_for(
            asyncio.shield(request),
            timeout=0.1,
        ) == "backup"
        queued_metadata = list(router._telemetry_queue._queue)
        assert "secret prompt" not in repr(queued_metadata)
        assert "backup" not in repr(queued_metadata)
    finally:
        release_record.set()
        await request
        await router.flush_telemetry()


@pytest.mark.asyncio
async def test_router_shutdown_bounds_blocked_telemetry_worker(empty_db):
    router, provider_ids, adapters, _clock = configure_router(empty_db)
    router._telemetry_flush_timeout = 0.05
    adapters[provider_ids[0]].chat.return_value = completion("ok")
    record_started = Event()
    release_record = Event()

    def blocked_record(*_args, **_kwargs):
        record_started.set()
        release_record.wait()

    router.store.record_request = blocked_record
    request = asyncio.create_task(
        router.chat("hello", route="strategy")
    )
    try:
        while not record_started.is_set():
            await asyncio.sleep(0)
        assert await asyncio.wait_for(
            asyncio.shield(request),
            timeout=0.1,
        ) == "ok"

        started_at = asyncio.get_running_loop().time()
        await router.aclose()
        elapsed = asyncio.get_running_loop().time() - started_at

        assert elapsed < 0.2
        assert (
            router._telemetry_worker is None
            or router._telemetry_worker.done()
        )
    finally:
        release_record.set()
        await request


@pytest.mark.asyncio
async def test_full_telemetry_queue_drops_metadata_without_delaying_chat(
    empty_db,
):
    router, provider_ids, adapters, _clock = configure_router(empty_db)
    router._telemetry_queue = asyncio.Queue(maxsize=1)
    adapters[provider_ids[0]].chat.return_value = completion("ok")
    record_started = Event()
    release_record = Event()

    def blocked_record(*_args, **_kwargs):
        record_started.set()
        release_record.wait()

    router.store.record_request = blocked_record
    assert await router.chat("first secret", route="strategy") == "ok"
    while not record_started.is_set():
        await asyncio.sleep(0)

    assert await router.chat("second secret", route="strategy") == "ok"
    assert await router.chat("third secret", route="strategy") == "ok"
    assert router.telemetry_failures == 1
    assert "secret" not in repr(list(router._telemetry_queue._queue))

    release_record.set()
    assert await router.flush_telemetry()
