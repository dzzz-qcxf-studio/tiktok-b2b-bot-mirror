"""Authentication startup configuration regression tests."""

from pathlib import Path


def test_jwt_secret_falls_back_to_project_env_when_process_env_is_missing(
    tmp_path: Path,
    monkeypatch,
):
    from tiktok_bot_api import auth

    expected = "stable-test-secret-that-survives-api-restarts"
    env_path = tmp_path / ".env"
    env_path.write_text(
        f"JWT_SECRET={expected}\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("JWT_SECRET", raising=False)

    assert auth._resolve_jwt_secret(env_path) == expected
