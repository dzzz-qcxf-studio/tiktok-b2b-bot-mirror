"""旧 API Key 兼容入口的认证、隔离和不泄密契约。"""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest
from dotenv import dotenv_values

from tiktok_bot_core.models.entities import Base
from tiktok_bot_core.storage.database import Database
from tiktok_bot_core.storage.llm_store import LLMStore


@pytest.fixture
def legacy_api_app(tmp_path, monkeypatch):
    from tiktok_bot_api import main as api_main

    database = Database(f"sqlite:///{tmp_path / 'legacy-api-key.db'}")
    Base.metadata.create_all(database.engine)
    with database.session() as session:
        LLMStore().create_provider(
            session,
            name="legacy-test",
            display_name="Legacy test",
            base_url="https://example.invalid/v1",
            default_model="test-model",
            api_key_env="LEGACY_TEST_API_KEY",
        )

    env_path = tmp_path / ".env"
    env_path.write_text("UNCHANGED=value\n", encoding="utf-8")
    monkeypatch.setattr(api_main, "LLM_ENV_PATH", env_path)
    monkeypatch.setattr(api_main, "reload_settings", lambda: None)
    monkeypatch.setattr(
        api_main,
        "aclose_llm_router",
        AsyncMock(),
    )
    monkeypatch.delenv("LEGACY_TEST_API_KEY", raising=False)

    original = api_main.app.state.pipeline_database
    api_main.app.state.pipeline_database = database
    try:
        yield api_main, env_path
    finally:
        api_main.app.state.pipeline_database = original
        database.engine.dispose()


def _client(api_main, *, authenticated: bool):
    headers = {}
    if authenticated:
        from tiktok_bot_api.auth import create_token

        headers["Authorization"] = (
            f"Bearer {create_token('legacy-api-key-test')}"
        )
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api_main.app),
        base_url="http://testserver",
        headers=headers,
    )


@pytest.mark.asyncio
async def test_legacy_apikey_requires_authentication(legacy_api_app):
    api_main, env_path = legacy_api_app
    async with _client(api_main, authenticated=False) as client:
        response = await client.post(
            "/api/config/apikey",
            json={"api_key": "blocked-test-secret"},
        )

    assert response.status_code == 401
    assert env_path.read_text(encoding="utf-8") == "UNCHANGED=value\n"


@pytest.mark.asyncio
async def test_legacy_apikey_uses_isolated_provider_secret_without_echo(
    legacy_api_app,
):
    api_main, env_path = legacy_api_app
    secret = "isolated-test-secret"
    async with _client(api_main, authenticated=True) as client:
        response = await client.post(
            "/api/config/apikey",
            json={"api_key": secret},
        )
        config_response = await client.get("/api/config")

    assert response.status_code == 200, response.text
    assert response.json() == {
        "status": "ok",
        "configured": True,
        "envVar": "LEGACY_TEST_API_KEY",
    }
    assert secret not in response.text
    assert dotenv_values(env_path)["LEGACY_TEST_API_KEY"] == secret

    assert config_response.status_code == 200
    assert config_response.json()["has_api_key"] is True
    assert config_response.json()["llm_api_key"] == "***"
    assert secret not in config_response.text


@pytest.mark.asyncio
async def test_legacy_apikey_rejects_line_break_injection(legacy_api_app):
    api_main, env_path = legacy_api_app
    async with _client(api_main, authenticated=True) as client:
        response = await client.post(
            "/api/config/apikey",
            json={"api_key": "safe\nINJECTED=value"},
        )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == (
        "request_validation_error"
    )
    assert env_path.read_text(encoding="utf-8") == "UNCHANGED=value\n"
