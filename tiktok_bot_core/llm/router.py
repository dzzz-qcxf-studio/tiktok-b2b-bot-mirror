"""Database-backed LLM routing with bounded failover and circuit breaking."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from tiktok_bot_core.llm.providers import (
    LLMCompletion,
    LLMProviderConfig,
    LLMProviderError,
    OpenAICompatibleProvider,
    classify_provider_exception,
)
from tiktok_bot_core.storage.database import Database, get_db
from tiktok_bot_core.storage.llm_store import LLMStore


logger = logging.getLogger(__name__)
DEFAULT_SYSTEM = "你是一个 B2B 外贸业务拓展专家。"
DEFAULT_JSON_SYSTEM = f"{DEFAULT_SYSTEM}请严格返回 JSON 格式。"
MAX_PROVIDER_ATTEMPTS = 3
CIRCUIT_FAILURE_THRESHOLD = 3
CIRCUIT_COOLDOWN_SECONDS = 60.0
TELEMETRY_QUEUE_MAX = 256
TELEMETRY_FLUSH_TIMEOUT_SECONDS = 1.0


class LLMRouteError(RuntimeError):
    """Safe public Router error that never stores upstream response content."""

    def __init__(self, *, route: str, error_category: str) -> None:
        self.route = route
        self.error_category = str(error_category or "llm_route_failed")
        super().__init__(
            f"LLM route {route!r} failed ({self.error_category})"
        )

    def __repr__(self) -> str:
        return (
            "LLMRouteError("
            f"route={self.route!r}, error_category={self.error_category!r})"
        )


@dataclass
class CircuitState:
    state: str = "closed"
    consecutive_failures: int = 0
    opened_at: float = 0.0
    probe_in_flight: bool = False
    generation: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)


@dataclass(frozen=True)
class CircuitLease:
    provider_id: str
    generation: int
    half_open: bool


@dataclass(frozen=True)
class _RouteAttempt:
    config: LLMProviderConfig
    model: str


@dataclass(frozen=True)
class _RouteSnapshot:
    attempts: tuple[_RouteAttempt, ...]
    provider_ids: frozenset[str]


def extract_json(text: str) -> dict[str, Any]:
    """Extract a JSON object without including generated content in errors."""

    candidate = text.strip()
    try:
        parsed = json.loads(candidate)
        if isinstance(parsed, dict):
            return parsed
    except (json.JSONDecodeError, TypeError):
        pass
    if candidate.startswith("```"):
        candidate = "\n".join(
            line
            for line in candidate.splitlines()
            if not line.strip().startswith("```")
        ).strip()
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass
    match = re.search(r"\{.*\}", candidate, re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group())
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass
    raise ValueError("invalid_json")


class LLMRouter:
    """Resolve a fixed business route to an ordered Provider chain."""

    def __init__(
        self,
        *,
        database: Database,
        store: LLMStore | None = None,
        provider_factory: Callable[[LLMProviderConfig], Any] | None = None,
        clock: Callable[[], float] = time.monotonic,
        telemetry_queue_size: int = TELEMETRY_QUEUE_MAX,
        telemetry_flush_timeout: float = (
            TELEMETRY_FLUSH_TIMEOUT_SECONDS
        ),
    ) -> None:
        if telemetry_queue_size < 1:
            raise ValueError("telemetry_queue_size must be positive")
        if telemetry_flush_timeout <= 0:
            raise ValueError("telemetry_flush_timeout must be positive")
        self.database = database
        self.store = store or LLMStore()
        self.clock = clock
        self.circuits: dict[str, CircuitState] = {}
        self._provider_factory = provider_factory
        self._providers: dict[str, OpenAICompatibleProvider] = {}
        self._provider_close_tasks: dict[str, asyncio.Task[None]] = {}
        self._provider_cache_lock = asyncio.Lock()
        self._closed = False
        self.telemetry_failures = 0
        self._telemetry_queue: asyncio.Queue[dict[str, Any]] = (
            asyncio.Queue(maxsize=telemetry_queue_size)
        )
        self._telemetry_worker: asyncio.Task[None] | None = None
        self._telemetry_accepting = True
        self._telemetry_flush_timeout = telemetry_flush_timeout

    def __repr__(self) -> str:
        return "LLMRouter(database_backed=True)"

    async def _provider_for(
        self,
        config: LLMProviderConfig,
        *,
        route: str,
    ) -> Any:
        async with self._provider_cache_lock:
            if self._closed:
                raise LLMRouteError(
                    route=route,
                    error_category="router_closed",
                )
            if self._provider_factory is not None:
                return self._provider_factory(config)
            provider = self._providers.get(config.id)
            if provider is None:
                provider = OpenAICompatibleProvider(config)
                self._providers[config.id] = provider
            else:
                provider.configure(config)
            return provider

    def _route_snapshot_sync(self, route: str) -> _RouteSnapshot:
        with self.database.session() as session:
            chain = self.store.get_route_chain(
                session,
                route,
                enabled_only=True,
            )
            attempts: list[_RouteAttempt] = []
            seen: set[str] = set()
            for entry in chain:
                provider = entry.provider
                if provider.id in seen:
                    continue
                seen.add(provider.id)
                config = LLMProviderConfig(
                    id=provider.id,
                    name=provider.name,
                    protocol=provider.protocol,
                    base_url=provider.base_url,
                    default_model=provider.default_model,
                    api_key_env=provider.api_key_env,
                    timeout_seconds=provider.timeout_seconds,
                    updated_at=provider.updated_at,
                )
                attempts.append(
                    _RouteAttempt(
                        config=config,
                        model=entry.model_override or provider.default_model,
                    )
                )
            provider_ids = frozenset(
                provider.id
                for provider in self.store.list_providers(session)
            )
            return _RouteSnapshot(
                attempts=tuple(attempts),
                provider_ids=provider_ids,
            )

    def _provider_ids_sync(self) -> frozenset[str]:
        with self.database.session() as session:
            return frozenset(
                provider.id
                for provider in self.store.list_providers(session)
            )

    async def _close_provider(self, provider: Any) -> None:
        close = getattr(provider, "aclose", None)
        if not callable(close):
            return
        result = close()
        if inspect.isawaitable(result):
            await result

    @staticmethod
    def _consume_provider_close_result(
        task: asyncio.Task[None],
    ) -> None:
        if task.cancelled():
            logger.warning("LLM provider cache close interrupted")
            return
        try:
            error = task.exception()
        except BaseException:
            logger.warning("LLM provider cache close interrupted")
            return
        if error is not None:
            logger.warning("LLM provider cache close failed")

    async def _close_cached_provider(
        self,
        provider_id: str,
        provider: Any,
    ) -> None:
        await self._close_provider(provider)
        async with self._provider_cache_lock:
            if self._providers.get(provider_id) is provider:
                self._providers.pop(provider_id, None)
            task = self._provider_close_tasks.get(provider_id)
            if task is asyncio.current_task():
                self._provider_close_tasks.pop(provider_id, None)

    def _schedule_provider_close_locked(
        self,
        provider_id: str,
        provider: Any,
    ) -> asyncio.Task[None]:
        """Start or reuse a close task while holding provider cache lock."""

        task = self._provider_close_tasks.get(provider_id)
        if task is not None and not task.done():
            return task
        task = asyncio.create_task(
            self._close_cached_provider(provider_id, provider)
        )
        task.add_done_callback(self._consume_provider_close_result)
        self._provider_close_tasks[provider_id] = task
        return task

    async def _prune_provider_cache(
        self,
        existing_provider_ids: frozenset[str],
    ) -> None:
        async with self._provider_cache_lock:
            candidates = (
                set(self._providers) - set(existing_provider_ids)
            )
        if not candidates:
            return
        confirmed_ids = await asyncio.to_thread(self._provider_ids_sync)
        stale_ids = candidates - set(confirmed_ids)
        async with self._provider_cache_lock:
            for provider_id in stale_ids:
                provider = self._providers.get(provider_id)
                if provider is not None:
                    self._schedule_provider_close_locked(
                        provider_id,
                        provider,
                    )

    async def _route_snapshot(self, route: str) -> _RouteSnapshot:
        snapshot = await asyncio.to_thread(
            self._route_snapshot_sync,
            route,
        )
        await self._prune_provider_cache(snapshot.provider_ids)
        return snapshot

    async def _ensure_open(self, route: str) -> None:
        async with self._provider_cache_lock:
            if self._closed:
                raise LLMRouteError(
                    route=route,
                    error_category="router_closed",
                )

    async def _claim_circuit(
        self,
        provider_id: str,
    ) -> CircuitLease | None:
        circuit = self.circuits.setdefault(provider_id, CircuitState())
        async with circuit.lock:
            if circuit.state == "closed":
                return CircuitLease(
                    provider_id=provider_id,
                    generation=circuit.generation,
                    half_open=False,
                )
            if circuit.state == "open":
                if (
                    self.clock() - circuit.opened_at
                    < CIRCUIT_COOLDOWN_SECONDS
                ):
                    return None
                circuit.state = "half_open"
            if circuit.probe_in_flight:
                return None
            circuit.probe_in_flight = True
            return CircuitLease(
                provider_id=provider_id,
                generation=circuit.generation,
                half_open=True,
            )

    async def _provider_succeeded(self, lease: CircuitLease) -> None:
        circuit = self.circuits.setdefault(
            lease.provider_id,
            CircuitState(),
        )
        async with circuit.lock:
            if lease.generation != circuit.generation:
                return
            if lease.half_open:
                if (
                    circuit.state != "half_open"
                    or not circuit.probe_in_flight
                ):
                    return
                circuit.generation += 1
            circuit.state = "closed"
            circuit.consecutive_failures = 0
            circuit.opened_at = 0.0
            circuit.probe_in_flight = False

    async def _provider_failed(
        self,
        lease: CircuitLease,
        *,
        retryable: bool,
    ) -> None:
        circuit = self.circuits.setdefault(
            lease.provider_id,
            CircuitState(),
        )
        async with circuit.lock:
            if lease.generation != circuit.generation:
                return
            if lease.half_open:
                if (
                    circuit.state != "half_open"
                    or not circuit.probe_in_flight
                ):
                    return
                circuit.generation += 1
                circuit.state = "open"
                circuit.consecutive_failures = CIRCUIT_FAILURE_THRESHOLD
                circuit.opened_at = self.clock()
                circuit.probe_in_flight = False
                return
            if not retryable:
                circuit.state = "closed"
                circuit.consecutive_failures = 0
                circuit.opened_at = 0.0
                circuit.probe_in_flight = False
                return
            circuit.consecutive_failures += 1
            if circuit.consecutive_failures >= CIRCUIT_FAILURE_THRESHOLD:
                circuit.generation += 1
                circuit.state = "open"
                circuit.opened_at = self.clock()
            circuit.probe_in_flight = False

    async def _provider_aborted(self, lease: CircuitLease) -> None:
        circuit = self.circuits.setdefault(
            lease.provider_id,
            CircuitState(),
        )
        async with circuit.lock:
            if lease.generation != circuit.generation:
                return
            if (
                lease.half_open
                and circuit.state == "half_open"
                and circuit.probe_in_flight
            ):
                circuit.generation += 1
                circuit.state = "open"
                circuit.consecutive_failures = CIRCUIT_FAILURE_THRESHOLD
                circuit.opened_at = self.clock()
                circuit.probe_in_flight = False

    def _record_sync(
        self,
        *,
        route: str,
        attempt: _RouteAttempt,
        status: str,
        error_category: str = "",
        model: str | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        total_tokens: int = 0,
        latency_ms: float,
        fallback_used: bool,
    ) -> None:
        with self.database.session() as session:
            self.store.record_request(
                session,
                route_key=route,
                provider_id=attempt.config.id,
                model=model or attempt.model,
                status=status,
                error_category=error_category,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                latency_ms=max(0.0, latency_ms),
                fallback_used=fallback_used,
            )

    def _start_telemetry_worker(self) -> None:
        worker = self._telemetry_worker
        if worker is not None and not worker.done():
            return
        worker = asyncio.create_task(self._drain_telemetry())
        self._telemetry_worker = worker

    def _enqueue_record(self, **kwargs: Any) -> None:
        if not self._telemetry_accepting:
            self.telemetry_failures += 1
            logger.warning("LLM telemetry event dropped")
            return
        try:
            self._telemetry_queue.put_nowait(kwargs)
        except asyncio.QueueFull:
            self.telemetry_failures += 1
            logger.warning("LLM telemetry event dropped")
            return
        self._start_telemetry_worker()

    async def _drain_telemetry(self) -> None:
        current = asyncio.current_task()
        try:
            while True:
                try:
                    kwargs = self._telemetry_queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                try:
                    await asyncio.to_thread(self._record_sync, **kwargs)
                except asyncio.CancelledError:
                    self.telemetry_failures += 1
                    raise
                except Exception:
                    self.telemetry_failures += 1
                    logger.warning("LLM telemetry write failed")
                finally:
                    self._telemetry_queue.task_done()
        finally:
            if self._telemetry_worker is current:
                self._telemetry_worker = None
            if (
                self._telemetry_accepting
                and not self._telemetry_queue.empty()
            ):
                self._start_telemetry_worker()

    async def flush_telemetry(
        self,
        timeout: float | None = None,
    ) -> bool:
        """Bounded wait for queued metadata writes without cancelling them."""

        if not self._telemetry_queue.empty():
            self._start_telemetry_worker()
        effective_timeout = (
            self._telemetry_flush_timeout
            if timeout is None
            else max(0.0, timeout)
        )
        try:
            await asyncio.wait_for(
                self._telemetry_queue.join(),
                timeout=effective_timeout,
            )
        except asyncio.TimeoutError:
            return False
        await asyncio.sleep(0)
        return True

    async def _shutdown_telemetry(self) -> None:
        self._telemetry_accepting = False
        drained = await self.flush_telemetry(
            timeout=self._telemetry_flush_timeout
        )
        if drained:
            return
        while True:
            try:
                self._telemetry_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            else:
                self.telemetry_failures += 1
                self._telemetry_queue.task_done()
        worker = self._telemetry_worker
        if worker is not None and not worker.done():
            worker.cancel()
            try:
                await worker
            except BaseException:
                pass
        if self._telemetry_worker is worker:
            self._telemetry_worker = None

    async def aclose(self) -> None:
        async with self._provider_cache_lock:
            self._closed = True
            close_tasks = [
                self._schedule_provider_close_locked(
                    provider_id,
                    provider,
                )
                for provider_id, provider in self._providers.items()
            ]
        for task in close_tasks:
            await asyncio.shield(task)
        await self._shutdown_telemetry()

    async def _completion(
        self,
        *,
        route: str,
        prompt: str,
        system: str | None,
        parse_json: bool,
    ) -> str | dict[str, Any]:
        await self._ensure_open(route)
        try:
            snapshot = await self._route_snapshot(route)
        except Exception:
            raise LLMRouteError(
                route=route,
                error_category="configuration",
            ) from None
        attempts = snapshot.attempts
        if not attempts:
            raise LLMRouteError(
                route=route,
                error_category="configuration",
            )

        last_category = "all_providers_failed"
        actual_attempts = 0
        for route_index, attempt in enumerate(attempts):
            if actual_attempts >= MAX_PROVIDER_ATTEMPTS:
                break
            lease = await self._claim_circuit(attempt.config.id)
            if lease is None:
                continue
            actual_attempts += 1
            fallback_used = route_index > 0
            started = self.clock()
            try:
                provider = await self._provider_for(
                    attempt.config,
                    route=route,
                )
                completion = await provider.chat(
                    prompt=prompt,
                    system=system,
                    model=attempt.model,
                )
                if parse_json:
                    try:
                        result = extract_json(completion.text)
                    except ValueError:
                        await self._provider_failed(
                            lease,
                            retryable=False,
                        )
                        self._enqueue_record(
                            route=route,
                            attempt=attempt,
                            status="failed",
                            error_category="invalid_json",
                            model=completion.model,
                            input_tokens=completion.input_tokens,
                            output_tokens=completion.output_tokens,
                            total_tokens=completion.total_tokens,
                            latency_ms=(self.clock() - started) * 1000,
                            fallback_used=fallback_used,
                        )
                        raise LLMRouteError(
                            route=route,
                            error_category="invalid_json",
                        ) from None
                else:
                    result = completion.text
                await self._provider_succeeded(lease)
                self._enqueue_record(
                    route=route,
                    attempt=attempt,
                    status="success",
                    model=completion.model,
                    input_tokens=completion.input_tokens,
                    output_tokens=completion.output_tokens,
                    total_tokens=completion.total_tokens,
                    latency_ms=(self.clock() - started) * 1000,
                    fallback_used=fallback_used,
                )
                return result
            except asyncio.CancelledError:
                await self._provider_aborted(lease)
                raise
            except LLMRouteError:
                raise
            except Exception as error:
                classified = classify_provider_exception(error)
                last_category = classified.category
                await self._provider_failed(
                    lease,
                    retryable=classified.retryable,
                )
                self._enqueue_record(
                    route=route,
                    attempt=attempt,
                    status="failed",
                    error_category=classified.category,
                    latency_ms=(self.clock() - started) * 1000,
                    fallback_used=fallback_used,
                )
                if not classified.retryable:
                    raise LLMRouteError(
                        route=route,
                        error_category=classified.category,
                    ) from None
            except BaseException:
                await self._provider_aborted(lease)
                raise
        raise LLMRouteError(
            route=route,
            error_category=last_category,
        )

    async def chat(
        self,
        prompt: str,
        system: str | None = None,
        *,
        route: str = "default",
    ) -> str:
        result = await self._completion(
            route=route,
            prompt=prompt,
            system=system or DEFAULT_SYSTEM,
            parse_json=False,
        )
        return str(result)

    async def json_completion(
        self,
        prompt: str,
        system: str | None = None,
        *,
        route: str = "default",
    ) -> dict[str, Any]:
        result = await self._completion(
            route=route,
            prompt=prompt,
            system=system or DEFAULT_JSON_SYSTEM,
            parse_json=True,
        )
        if not isinstance(result, dict):
            raise LLMRouteError(
                route=route,
                error_category="invalid_json",
            )
        return result


_router: LLMRouter | None = None


def get_llm_router() -> LLMRouter:
    global _router
    if _router is None:
        _router = LLMRouter(database=get_db())
    return _router


async def aclose_llm_router() -> None:
    global _router
    router = _router
    if router is not None:
        await router.aclose()
        if _router is router:
            _router = None
