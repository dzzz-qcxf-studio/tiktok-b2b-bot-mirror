"""LLM Provider adapters and safe upstream error classification."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from dotenv import dotenv_values
from openai import AsyncOpenAI


logger = logging.getLogger(__name__)
RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504})
NON_RETRYABLE_STATUS = frozenset({400, 401, 403, 404, 422})
DEFAULT_LLM_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"


def resolve_llm_api_key(
    env_var: str,
    *,
    env_path: Path | None = None,
) -> str:
    """Read one Provider secret without exporting the whole .env file."""

    runtime_value = os.environ.get(env_var, "")
    if runtime_value:
        return runtime_value
    values = dotenv_values(env_path or DEFAULT_LLM_ENV_PATH)
    persisted_value = values.get(env_var)
    return str(persisted_value or "")


@dataclass(frozen=True)
class LLMProviderConfig:
    """Credential-free Provider snapshot loaded from the database."""

    id: str
    name: str
    base_url: str
    default_model: str
    api_key_env: str
    timeout_seconds: float
    updated_at: datetime
    protocol: str = "openai_chat"


@dataclass(frozen=True)
class LLMCompletion:
    """Provider result; generated content is deliberately omitted from repr."""

    text: str = field(repr=False)
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


class LLMProviderError(RuntimeError):
    """Sanitized Provider failure safe for logs and exception repr."""

    def __init__(
        self,
        _upstream_message: object = None,
        *,
        retryable: bool,
        category: str = "provider_error",
    ) -> None:
        self.retryable = bool(retryable)
        self.category = str(category or "provider_error")
        super().__init__(self.category)

    def __repr__(self) -> str:
        return f"LLMProviderError(retryable={self.retryable!r})"


def _status_code(error: object) -> int | None:
    status = getattr(error, "status_code", None)
    if status is None:
        status = getattr(getattr(error, "response", None), "status_code", None)
    try:
        return int(status) if status is not None else None
    except (TypeError, ValueError):
        return None


def classify_provider_exception(error: object) -> LLMProviderError:
    """Map arbitrary SDK failures to a small credential-free taxonomy."""

    if isinstance(error, LLMProviderError):
        return error
    status = _status_code(error)
    if status == 408:
        return LLMProviderError(retryable=True, category="timeout")
    if status == 429:
        return LLMProviderError(retryable=True, category="rate_limit")
    if status in {500, 502, 503, 504}:
        return LLMProviderError(retryable=True, category="upstream_server")
    if status in {401, 403}:
        return LLMProviderError(retryable=False, category="authentication")
    if status in {400, 404, 422}:
        return LLMProviderError(retryable=False, category="invalid_request")
    if status is not None:
        return LLMProviderError(retryable=False, category="http_error")
    if isinstance(error, (ValueError, TypeError, KeyError)):
        return LLMProviderError(retryable=False, category="invalid_request")
    if isinstance(error, (TimeoutError, asyncio.TimeoutError)):
        return LLMProviderError(retryable=True, category="timeout")
    if isinstance(error, (ConnectionError, OSError)):
        return LLMProviderError(retryable=True, category="network")
    return LLMProviderError(retryable=True, category="network")


@dataclass(eq=False)
class _ClientSlot:
    client: Any
    fingerprint: tuple[object, ...]
    in_flight: int = 0
    retired: bool = False
    closed: bool = False
    idle: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    close_complete: asyncio.Event = field(
        default_factory=asyncio.Event,
        repr=False,
    )
    close_task: asyncio.Task[None] | None = field(
        default=None,
        repr=False,
    )

    def __post_init__(self) -> None:
        self.idle.set()


class OpenAICompatibleProvider:
    """OpenAI Chat Completions adapter with environment-backed credentials."""

    def __init__(
        self,
        config: LLMProviderConfig,
        *,
        client_factory: Callable[..., Any] = AsyncOpenAI,
    ) -> None:
        self._config = config
        self._client_factory = client_factory
        self._client: Any | None = None
        self._client_fingerprint: tuple[object, ...] | None = None
        self._active_slot: _ClientSlot | None = None
        self._slots: list[_ClientSlot] = []
        self._closed = False
        self._client_lock = asyncio.Lock()

    @property
    def config(self) -> LLMProviderConfig:
        return self._config

    def configure(self, config: LLMProviderConfig) -> None:
        if config != self._config:
            self._config = config

    def __repr__(self) -> str:
        return (
            "OpenAICompatibleProvider("
            f"id={self._config.id!r}, name={self._config.name!r})"
        )

    @staticmethod
    def _consume_close_result(task: asyncio.Task[None]) -> None:
        if task.cancelled():
            logger.warning("LLM provider client close interrupted")
            return
        try:
            error = task.exception()
        except BaseException:
            logger.warning("LLM provider client close interrupted")
            return
        if error is not None:
            logger.warning("LLM provider client close failed")

    def _schedule_slot_close_locked(
        self,
        slot: _ClientSlot,
    ) -> asyncio.Task[None] | None:
        """Start or reuse an independent close while holding client lock."""

        if slot.closed:
            return None
        task = slot.close_task
        if task is not None and not task.done():
            return task
        task = asyncio.create_task(self._close_slot(slot))
        task.add_done_callback(self._consume_close_result)
        slot.close_task = task
        return task

    async def _close_slot(self, slot: _ClientSlot) -> None:
        """Close one idle slot, retaining it until close really succeeds."""

        await slot.idle.wait()
        async with self._client_lock:
            if slot.closed:
                return
        await self._close_client(slot.client)
        async with self._client_lock:
            if slot.closed:
                return
            slot.closed = True
            if self._active_slot is slot:
                self._active_slot = None
            if self._client is slot.client:
                self._client = None
                self._client_fingerprint = None
            if slot in self._slots:
                self._slots.remove(slot)
            slot.close_complete.set()

    async def _close_client(self, client: Any) -> None:
        close = getattr(client, "close", None)
        if not callable(close):
            return
        result = close()
        if inspect.isawaitable(result):
            await result

    async def _acquire_client(self) -> _ClientSlot:
        async with self._client_lock:
            if self._closed:
                raise LLMProviderError(
                    retryable=False,
                    category="configuration",
                )
            config = self._config
            api_key = resolve_llm_api_key(config.api_key_env)
            if not api_key:
                raise LLMProviderError(
                    retryable=False,
                    category="configuration",
                )
            fingerprint = (
                config.base_url,
                config.timeout_seconds,
                config.updated_at,
                hashlib.sha256(api_key.encode("utf-8")).digest(),
            )
            slot = self._active_slot
            if slot is None or slot.fingerprint != fingerprint:
                previous_slot = slot
                client = self._client_factory(
                    api_key=api_key,
                    base_url=config.base_url,
                    timeout=config.timeout_seconds,
                    max_retries=0,
                )
                slot = _ClientSlot(
                    client=client,
                    fingerprint=fingerprint,
                )
                self._slots.append(slot)
                self._active_slot = slot
                self._client = client
                self._client_fingerprint = fingerprint
                if previous_slot is not None:
                    previous_slot.retired = True
                    self._schedule_slot_close_locked(previous_slot)
            slot.in_flight += 1
            slot.idle.clear()
        return slot

    async def _release_client(self, slot: _ClientSlot) -> None:
        async with self._client_lock:
            if slot.in_flight > 0:
                slot.in_flight -= 1
            if slot.in_flight == 0:
                slot.idle.set()
                if slot.retired or self._closed:
                    self._schedule_slot_close_locked(slot)

    async def aclose(self) -> None:
        async with self._client_lock:
            self._closed = True
            slots = list(self._slots)
            close_tasks: list[asyncio.Task[None]] = []
            for slot in slots:
                slot.retired = True
                task = self._schedule_slot_close_locked(slot)
                if task is not None:
                    close_tasks.append(task)
        for task in close_tasks:
            await asyncio.shield(task)

    async def chat(
        self,
        *,
        prompt: str,
        system: str | None,
        model: str,
    ) -> LLMCompletion:
        slot: _ClientSlot | None = None
        try:
            slot = await self._acquire_client()
            client = slot.client
            messages: list[dict[str, str]] = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})
            response = await client.chat.completions.create(
                model=model,
                messages=messages,
            )
            text = response.choices[0].message.content
            if not isinstance(text, str):
                raise LLMProviderError(
                    retryable=False,
                    category="invalid_response",
                )
            usage = getattr(response, "usage", None)
            input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
            output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
            total_tokens = int(
                getattr(usage, "total_tokens", input_tokens + output_tokens)
                or input_tokens + output_tokens
            )
            response_model = str(getattr(response, "model", "") or model)
            return LLMCompletion(
                text=text,
                model=response_model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
            )
        except LLMProviderError:
            raise
        except Exception as error:
            raise classify_provider_exception(error) from None
        finally:
            if slot is not None:
                release_task = asyncio.create_task(
                    self._release_client(slot)
                )
                await asyncio.shield(release_task)
