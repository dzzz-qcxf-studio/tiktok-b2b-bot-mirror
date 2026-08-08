"""LLM 管理 API 契约测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest
from dotenv import dotenv_values

from tiktok_bot_core.models.entities import Base
from tiktok_bot_core.storage.database import Database
from tiktok_bot_core.storage.llm_store import LLMStore


@pytest.fixture
def llm_database(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'llm-api.db'}")
    Base.metadata.create_all(database.engine)
    yield database
    database.engine.dispose()


@pytest.fixture
def api_app(llm_database):
    from tiktok_bot_api import main as api_main

    original = api_main.app.state.pipeline_database
    api_main.app.state.pipeline_database = llm_database
    try:
        yield api_main
    finally:
        api_main.app.state.pipeline_database = original


def async_client(app, *, authenticated: bool = True):
    headers = {}
    if authenticated:
        from tiktok_bot_api.auth import create_token

        headers["Authorization"] = f"Bearer {create_token('llm-test-user')}"
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
        headers=headers,
    )


def provider_payload(name: str = "deepseek-main") -> dict:
    return {
        "name": name,
        "displayName": "DeepSeek",
        "protocol": "openai_chat",
        "baseUrl": "https://api.deepseek.com/v1",
        "defaultModel": "deepseek-chat",
        "apiKeyEnv": "DEEPSEEK_API_KEY",
        "enabled": True,
        "timeoutSeconds": 12,
    }


def test_api_runtime_allows_only_one_worker(api_app, tmp_path):
    lock_path = tmp_path / "api-worker.lock"
    first = api_app._acquire_api_worker_lock(lock_path)
    try:
        with pytest.raises(RuntimeError, match="single API worker"):
            api_app._acquire_api_worker_lock(lock_path)
    finally:
        api_app._release_api_worker_lock(first)

    second = api_app._acquire_api_worker_lock(lock_path)
    api_app._release_api_worker_lock(second)


@pytest.mark.asyncio
async def test_llm_management_requires_authentication(api_app):
    requests = [
        ("GET", "/api/llm/providers", None),
        ("POST", "/api/llm/providers", provider_payload("unauthorized")),
        ("PUT", "/api/llm/providers/missing", {"displayName": "Blocked"}),
        ("DELETE", "/api/llm/providers/missing", None),
        ("POST", "/api/llm/providers/missing/test", None),
        ("PUT", "/api/llm/providers/missing/secret", {"apiKey": "blocked"}),
        ("GET", "/api/llm/routes", None),
        ("PUT", "/api/llm/routes/strategy", {"providers": []}),
        ("GET", "/api/llm/usage", None),
    ]

    async with async_client(api_app.app, authenticated=False) as client:
        responses = [
            await client.request(method, path, json=payload)
            for method, path, payload in requests
        ]

    assert [response.status_code for response in responses] == [401] * len(requests)


@pytest.mark.asyncio
async def test_llm_cors_allows_local_ui_and_rejects_untrusted_origin(api_app):
    headers = {"Access-Control-Request-Method": "GET"}
    async with async_client(api_app.app, authenticated=False) as client:
        trusted = await client.options(
            "/api/llm/providers",
            headers={**headers, "Origin": "http://localhost:5173"},
        )
        untrusted = await client.options(
            "/api/llm/providers",
            headers={**headers, "Origin": "https://attacker.example"},
        )

    assert trusted.status_code == 200
    assert trusted.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "access-control-allow-origin" not in untrusted.headers


@pytest.mark.asyncio
async def test_provider_crud_never_returns_secret(
    api_app,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        api_app,
        "LLM_ENV_PATH",
        tmp_path / ".env",
        raising=False,
    )
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    async with async_client(api_app.app) as client:
        created = await client.post("/api/llm/providers", json=provider_payload())
        assert created.status_code == 201, created.text
        provider = created.json()
        assert provider["configured"] is False
        assert "apiKey" not in provider

        listed = await client.get("/api/llm/providers")
        assert listed.status_code == 200
        assert listed.json() == [provider]

        updated = await client.put(
            f"/api/llm/providers/{provider['id']}",
            json={"displayName": "DeepSeek production"},
        )
        assert updated.status_code == 200
        assert updated.json()["displayName"] == "DeepSeek production"
        assert "apiKey" not in updated.json()

        deleted = await client.delete(f"/api/llm/providers/{provider['id']}")
        assert deleted.status_code == 204


@pytest.mark.asyncio
async def test_replace_route_chain_is_atomic_and_blocks_provider_delete(
    api_app,
    llm_database,
):
    store = LLMStore()
    with llm_database.session() as session:
        first = store.create_provider(
            session,
            **{
                "name": "first",
                "display_name": "First",
                "base_url": "https://first.example/v1",
                "default_model": "first-model",
                "api_key_env": "FIRST_API_KEY",
            },
        )
        second = store.create_provider(
            session,
            **{
                "name": "second",
                "display_name": "Second",
                "base_url": "https://second.example/v1",
                "default_model": "second-model",
                "api_key_env": "SECOND_API_KEY",
            },
        )
        first_id, second_id = first.id, second.id

    async with async_client(api_app.app) as client:
        response = await client.put(
            "/api/llm/routes/strategy",
            json={
                "providers": [
                    {"providerId": first_id, "priority": 10},
                    {"providerId": second_id, "priority": 20},
                ]
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["routeKey"] == "strategy"
        assert [entry["providerId"] for entry in response.json()["providers"]] == [
            first_id,
            second_id,
        ]

        listed = await client.get("/api/llm/routes")
        assert listed.status_code == 200
        assert next(
            route
            for route in listed.json()
            if route["routeKey"] == "strategy"
        ) == response.json()

        blocked = await client.delete(f"/api/llm/providers/{first_id}")
        assert blocked.status_code == 409
        assert blocked.json()["detail"]["code"] == "llm_provider_in_use"


@pytest.mark.asyncio
async def test_connection_test_runs_server_side(api_app, llm_database, monkeypatch):
    store = LLMStore()
    with llm_database.session() as session:
        provider = store.create_provider(
            session,
            name="probe",
            display_name="Probe",
            base_url="https://probe.example/v1",
            default_model="probe-model",
            api_key_env="PROBE_API_KEY",
        )
        provider_id = provider.id

    probe = AsyncMock(return_value={"reachable": True, "latencyMs": 3.5})
    monkeypatch.setattr(api_app, "_probe_llm_provider", probe, raising=False)

    async with async_client(api_app.app) as client:
        response = await client.post(f"/api/llm/providers/{provider_id}/test")

    assert response.status_code == 200, response.text
    assert response.json() == {"reachable": True, "latencyMs": 3.5}
    probe.assert_awaited_once()
    assert probe.await_args.args[0].id == provider_id


@pytest.mark.asyncio
async def test_secret_update_writes_provider_env_without_echo(
    api_app,
    llm_database,
    tmp_path,
    monkeypatch,
):
    store = LLMStore()
    with llm_database.session() as session:
        provider = store.create_provider(
            session,
            name="secret",
            display_name="Secret",
            base_url="https://secret.example/v1",
            default_model="secret-model",
            api_key_env="SECRET_PROVIDER_API_KEY",
        )
        provider_id = provider.id

    env_path = tmp_path / ".env"
    env_path.write_text("UNRELATED=value\n", encoding="utf-8")
    monkeypatch.setattr(api_app, "LLM_ENV_PATH", env_path, raising=False)
    monkeypatch.delenv("SECRET_PROVIDER_API_KEY", raising=False)

    async with async_client(api_app.app) as client:
        response = await client.put(
            f"/api/llm/providers/{provider_id}/secret",
            json={"apiKey": "sk-test-secret-value"},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body == {"status": "ok", "configured": True, "envVar": "SECRET_PROVIDER_API_KEY"}
    assert "sk-test-secret-value" not in response.text
    assert dotenv_values(env_path)["SECRET_PROVIDER_API_KEY"] == (
        "sk-test-secret-value"
    )

    monkeypatch.delenv("SECRET_PROVIDER_API_KEY", raising=False)
    async with async_client(api_app.app) as client:
        listed = await client.get("/api/llm/providers")
    assert listed.status_code == 200
    assert listed.json()[0]["configured"] is True


@pytest.mark.asyncio
async def test_secret_update_rejects_line_break_injection(
    api_app,
    llm_database,
    tmp_path,
    monkeypatch,
):
    store = LLMStore()
    with llm_database.session() as session:
        provider = store.create_provider(
            session,
            name="safe-secret",
            display_name="Safe secret",
            base_url="https://safe.example/v1",
            default_model="safe-model",
            api_key_env="SAFE_SECRET_API_KEY",
        )
        provider_id = provider.id

    env_path = tmp_path / ".env"
    env_path.write_text("UNCHANGED=value\n", encoding="utf-8")
    monkeypatch.setattr(api_app, "LLM_ENV_PATH", env_path, raising=False)

    async with async_client(api_app.app) as client:
        response = await client.put(
            f"/api/llm/providers/{provider_id}/secret",
            json={"apiKey": "safe\nINJECTED=value"},
        )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "request_validation_error"
    assert env_path.read_text(encoding="utf-8") == "UNCHANGED=value\n"


@pytest.mark.asyncio
async def test_provider_create_and_update_reject_invalid_api_key_env(api_app):
    malicious_env = "SAFE=bad\nINJECTED"
    async with async_client(api_app.app) as client:
        rejected_create = await client.post(
            "/api/llm/providers",
            json={**provider_payload("invalid-env"), "apiKeyEnv": malicious_env},
        )
        created = await client.post(
            "/api/llm/providers",
            json=provider_payload("safe-env"),
        )
        rejected_update = await client.put(
            f"/api/llm/providers/{created.json()['id']}",
            json={"apiKeyEnv": malicious_env},
        )

    assert rejected_create.status_code == 422
    assert rejected_create.json()["detail"]["code"] == "invalid_llm_configuration"
    assert created.status_code == 201
    assert rejected_update.status_code == 422
    assert rejected_update.json()["detail"]["code"] == "invalid_llm_configuration"


@pytest.mark.asyncio
async def test_usage_and_strict_request_errors_use_camel_case(api_app):
    async with async_client(api_app.app) as client:
        usage = await client.get("/api/llm/usage")
        invalid = await client.post(
            "/api/llm/providers",
            json={**provider_payload(), "unexpected": True},
        )

    assert usage.status_code == 200
    assert usage.json() == {
        "requestCount": 0,
        "successCount": 0,
        "failureCount": 0,
        "inputTokens": 0,
        "outputTokens": 0,
        "totalTokens": 0,
        "fallbackCount": 0,
        "averageLatencyMs": 0.0,
    }
    assert invalid.status_code == 422
    assert invalid.json()["detail"]["code"] == "request_validation_error"
