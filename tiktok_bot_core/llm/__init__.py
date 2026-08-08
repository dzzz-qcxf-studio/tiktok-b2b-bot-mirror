"""Database-backed LLM routing and compatibility facade."""

from .client import LLMClient, get_llm_client
from .providers import (
    LLMCompletion,
    LLMProviderConfig,
    LLMProviderError,
    OpenAICompatibleProvider,
    classify_provider_exception,
)
from .router import (
    LLMRouteError,
    LLMRouter,
    aclose_llm_router,
    get_llm_router,
)

__all__ = [
    "LLMClient",
    "LLMCompletion",
    "LLMProviderConfig",
    "LLMProviderError",
    "LLMRouteError",
    "LLMRouter",
    "OpenAICompatibleProvider",
    "aclose_llm_router",
    "classify_provider_exception",
    "get_llm_client",
    "get_llm_router",
]
