"""Compatibility facade for the database-backed LLM Router."""

from __future__ import annotations

from typing import Any

from .router import LLMRouter, extract_json, get_llm_router


_client: Any | None = None


class LLMClient:
    """Legacy constructor and JSON helper retained during Router migration."""

    def __new__(cls) -> LLMRouter:
        return get_llm_router()

    @staticmethod
    def _extract_json(text: str) -> dict[str, Any]:
        return extract_json(text)


def get_llm_client() -> LLMRouter:
    """Return the single Router facade used by legacy business call sites."""

    if _client is not None:
        return _client
    return get_llm_router()
