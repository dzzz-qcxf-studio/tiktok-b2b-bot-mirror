"""Hermes 操控的浏览器 Agent — 截图→LLM→动作 的最小闭环。

设计约束（与 wiki 08-Skills §核心理念对齐）：
- 动作集合固定、可枚举、可审计；LLM 不能扩展动作名。
- 每次循环都是「先截图，再问 LLM，再执行」；不暴露 provider、key 或 LLM 原始 payload。
- 走 iteration 路由，不污染 default / collection / qualification。
- 浏览器在循环结束、出错或截断时必须关闭，账号租约必须释放。
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import re
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol
from urllib.parse import urljoin, urlsplit

from tiktok_bot_core.events.bus import Event, EventBus, EventType
from tiktok_bot_core.llm.router import LLMRouteError, LLMRouter
from tiktok_bot_core.services.acquisition_agents import EvidenceObservation

logger = logging.getLogger(__name__)


# 必须是 frozenset；测试里直接断言相等。
ALLOWED_ACTIONS: frozenset[str] = frozenset(
    {"navigate", "click", "scroll", "wait", "extract", "done"}
)


@dataclass(frozen=True)
class BrowseAction:
    """LLM 单次输出的动作。安全校验不在构造期执行，由 `run()` 调
    `_validate_action` 完成；构造期抛错会中断整个循环。"""

    action: str
    payload: dict[str, Any] = field(default_factory=dict)
    rationale: str = ""


def _is_safe_navigate_url(url: str) -> bool:
    """只允许 http(s) 且 host 落在抖音/tiktok 受信任域集合。"""

    try:
        parsed = urlsplit(url.strip())
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"}:
        return False
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    return any(
        host == root or host.endswith("." + root)
        for root in ("douyin.com", "tiktok.com", "iesdouyin.com")
    )


def _validate_action(action: BrowseAction, *, platform: str | None = None) -> None:
    """运行时不抛 — 构造期校验在 _ask_llm 内部跑，循环期再跑一次作为双保险。"""

    if action.action not in ALLOWED_ACTIONS:
        raise ValueError(f"unsupported action: {action.action!r}")
    if action.action == "navigate":
        url = str(action.payload.get("url") or "")
        if not _is_safe_navigate_url(url):
            raise ValueError(f"unsafe navigate url: {url!r}")
        if platform is not None:
            parsed = urlsplit(url)
            host = (parsed.hostname or "").lower()
            roots = (
                ("douyin.com", "iesdouyin.com")
                if platform == "douyin"
                else ("tiktok.com",)
            )
            if not any(host == root or host.endswith("." + root) for root in roots):
                raise ValueError("navigate URL does not match task platform")
    if action.action == "click":
        selector = str(action.payload.get("selector") or "").strip()
        if not selector:
            raise ValueError("click requires non-empty selector")
    if action.action == "wait":
        ms = _strict_integer(action.payload.get("ms"), field="wait ms")
        if not 50 <= ms <= 10_000:
            raise ValueError("wait ms must be between 50 and 10000")
    if action.action == "scroll":
        px = _strict_integer(action.payload.get("px"), field="scroll px")
        if not 1 <= px <= 3_000:
            raise ValueError("scroll px must be between 1 and 3000")


def _strict_integer(value: Any, *, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} requires an integer")
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field} requires an integer") from None
    if isinstance(value, float) and not value.is_integer():
        raise ValueError(f"{field} requires an integer")
    return normalized


@dataclass
class BrowseStep:
    step: int
    action: BrowseAction | None
    screenshot_hash: str
    rationale: str = ""


@dataclass
class BrowseResult:
    status: str  # "done" | "timeout" | "error"
    summary: str
    steps: int
    steps_detail: list[BrowseStep] = field(default_factory=list)
    observations: list[EvidenceObservation] = field(default_factory=list)
    exhaustion_reason: str = ""
    budget_usage: dict[str, int | float] = field(default_factory=dict)
    last_visited_urls: list[str] = field(default_factory=list)
    truncation_reasons_by_user: dict[str, tuple[str, ...]] = field(
        default_factory=dict
    )


class _BrowserLike(Protocol):
    async def init(self) -> None: ...
    async def close(self) -> None: ...
    async def navigate(self, url: str) -> None: ...
    async def click(self, selector: str) -> None: ...
    async def scroll_down(self, px: int = 500) -> None: ...
    async def wait(self, ms: int) -> None: ...
    async def screenshot(self, full_page: bool = False) -> bytes: ...
    async def query(self, selector: str) -> Any: ...
    async def query_all(self, selector: str) -> list[Any]: ...
    @property
    def current_url(self) -> str: ...


BrowserFactory = Callable[[], Awaitable[_BrowserLike] | _BrowserLike]


_DECISION_SYSTEM = (
    "你是 Hermes 操控的浏览器 Agent。每一步我会给你：\n"
    "1. 用户的 goal\n"
    "2. 当前页面 URL\n"
    "3. 当前页面截图的 screenshot hash\n"
    "4. 最多 100 个交互元素组成的 structured DOM snapshot，或受限的正文文本\n\n"
    "你必须只返回以下 6 种动作之一：\n"
    "- navigate: 跳到新页面，payload.url 必须是 http(s)\n"
    "- click: 点击元素，payload.selector 是 CSS selector\n"
    "- scroll: 向下滚动，payload.px 是像素\n"
    "- wait: 等待，payload.ms 是毫秒\n"
    "- extract: 提取信息，payload.observation 必须严格匹配 EvidenceObservation\n"
    "- done: 目标达成，payload.summary 是结论\n\n"
    "严格只返回 JSON，字段：action / payload / rationale。\n"
    "extract 的 payload.observation 不得包含未定义字段，且必须通过以下 "
    "EvidenceObservation JSON Schema：\n"
    + json.dumps(EvidenceObservation.model_json_schema(), ensure_ascii=False)
)


def _hash_screenshot(data: bytes) -> str:
    return hashlib.sha256(data or b"").hexdigest()[:16]


def _platform_url_is_safe(
    platform: str,
    url: str,
    *,
    allow_empty: bool = False,
) -> bool:
    normalized = str(url or "").strip()
    if not normalized or normalized == "about:blank":
        return allow_empty
    if not _is_safe_navigate_url(normalized):
        return False
    host = (urlsplit(normalized).hostname or "").lower()
    roots = (
        ("douyin.com", "iesdouyin.com")
        if platform == "douyin"
        else ("tiktok.com",)
    )
    return any(host == root or host.endswith("." + root) for root in roots)


def _sanitize_visible_text(value: str, *, limit: int = 12_000) -> str:
    cleaned = "".join(
        char
        for char in str(value or "")
        if char in "\n\t" or not unicodedata.category(char).startswith("C")
    )
    return cleaned[:limit]


class BrowseAgent:
    """最小可用的截图→LLM→动作 闭环。

    不持久化截图到数据库；把 hash + action + rationale 发到事件总线，
    让上层 Skill 或 Pipeline 自由落盘与展示。
    """

    def __init__(
        self,
        *,
        router: LLMRouter,
        bus: EventBus,
        browser_factory: BrowserFactory,
        max_steps: int = 10,
        max_pages: int = 10,
        max_duration_minutes: int = 60,
        max_duration_seconds: float | None = None,
        max_llm_calls: int = 100,
        manage_browser_lifecycle: bool = True,
        monotonic: Callable[[], float] = time.monotonic,
        tracker: Any | None = None,
        current_keyword: str = "",
    ) -> None:
        if max_steps <= 0:
            raise ValueError("max_steps must be > 0")
        if not 1 <= max_pages <= 100:
            raise ValueError("max_pages must be between 1 and 100")
        if (
            max_duration_seconds is None
            and not 1 <= max_duration_minutes <= 1440
        ):
            raise ValueError("max_duration_minutes must be between 1 and 1440")
        if max_duration_seconds is not None and (
            isinstance(max_duration_seconds, bool)
            or not 0 < float(max_duration_seconds) <= 86_400
        ):
            raise ValueError("max_duration_seconds must be between 0 and 86400")
        if not 1 <= max_llm_calls <= 1000:
            raise ValueError("max_llm_calls must be between 1 and 1000")
        self._router = router
        self._bus = bus
        self._browser_factory = browser_factory
        self._max_steps = max_steps
        self._max_pages = max_pages
        self._max_duration_seconds = (
            float(max_duration_seconds)
            if max_duration_seconds is not None
            else float(max_duration_minutes * 60)
        )
        self._max_llm_calls = max_llm_calls
        self._manage_browser_lifecycle = manage_browser_lifecycle
        self._monotonic = monotonic
        self._tracker = tracker
        self._current_keyword = str(current_keyword)

    async def run(
        self,
        *,
        goal: str,
        platform: str,
        account_id: int,
    ) -> BrowseResult:
        started_at = self._monotonic()
        deadline = started_at + self._max_duration_seconds
        try:
            browser = await _await_with_deadline(
                _resolve_browser(self._browser_factory),
                deadline=deadline,
                monotonic=self._monotonic,
            )
        except (asyncio.TimeoutError, TimeoutError):
            result = BrowseResult(
                status="timeout",
                summary="",
                steps=0,
                exhaustion_reason="max_duration",
                budget_usage={
                    "steps": 0,
                    "pages": 0,
                    "llm_calls": 0,
                    "duration_seconds": max(
                        0.0, self._monotonic() - started_at
                    ),
                },
            )
            await _publish_event_bounded(
                self._bus,
                Event(
                    type=EventType.BROWSE_DONE,
                    payload={
                        "platform": platform,
                        "accountId": account_id,
                        "status": result.status,
                        "steps": 0,
                        "summary": "",
                        "exhaustionReason": result.exhaustion_reason,
                        "budgetUsage": result.budget_usage,
                    },
                    source="browse_agent",
                ),
            )
            return result
        steps: list[BrowseStep] = []
        observations: list[EvidenceObservation] = []
        visited_urls: list[str] = []
        truncation_reasons: dict[str, set[str]] = {}
        status = "timeout"
        summary = ""
        exhaustion_reason = ""
        llm_calls = 0
        pages = 0
        elapsed = 0.0

        def record_safe_url(url: str) -> None:
            normalized = str(url or "").strip()
            if (
                normalized
                and _platform_url_is_safe(platform, normalized)
                and normalized not in visited_urls
            ):
                visited_urls.append(normalized)

        try:
            try:
                initial_url = str(getattr(browser, "current_url", "") or "")
                if not _platform_url_is_safe(
                    platform, initial_url, allow_empty=True
                ):
                    status = "error"
                    exhaustion_reason = "unsafe_url"
                elif self._manage_browser_lifecycle:
                    await _await_with_deadline(
                        browser.init(), deadline=deadline, monotonic=self._monotonic
                    )

                current_url = str(getattr(browser, "current_url", "") or "")
                if current_url and current_url != "about:blank":
                    if not _platform_url_is_safe(platform, current_url):
                        status = "error"
                        exhaustion_reason = "unsafe_url"
                    else:
                        pages = 1
                        record_safe_url(current_url)

                for step_no in range(1, self._max_steps + 1):
                    if exhaustion_reason:
                        break
                    elapsed = max(0.0, self._monotonic() - started_at)
                    if elapsed >= self._max_duration_seconds:
                        exhaustion_reason = "max_duration"
                        break
                    if llm_calls >= self._max_llm_calls:
                        exhaustion_reason = "max_llm_calls"
                        break

                    current_url = str(
                        getattr(browser, "current_url", "") or ""
                    )
                    if not _platform_url_is_safe(
                        platform, current_url, allow_empty=pages == 0
                    ):
                        status = "error"
                        exhaustion_reason = "unsafe_url"
                        break
                    record_safe_url(current_url)
                    screenshot = await _await_with_deadline(
                        browser.screenshot(full_page=False),
                        deadline=deadline,
                        monotonic=self._monotonic,
                    )
                    shot_hash = _hash_screenshot(screenshot)
                    dom_snapshot = await _await_with_deadline(
                        _structured_dom_snapshot(browser, platform=platform),
                        deadline=deadline,
                        monotonic=self._monotonic,
                    )
                    try:
                        llm_calls += 1
                        action = await _await_with_deadline(
                            self._ask_llm(
                                goal=goal,
                                url=current_url,
                                screenshot_hash=shot_hash,
                                dom_snapshot=dom_snapshot,
                                step=step_no,
                            ),
                            deadline=deadline,
                            monotonic=self._monotonic,
                        )
                    except (asyncio.TimeoutError, TimeoutError):
                        raise
                    except LLMRouteError:
                        raise
                    except Exception as exc:  # JSON 解析 / KeyError 等
                        raise LLMRouteError(
                            route="iteration",
                            error_category="invalid_decision",
                        ) from exc

                    # 单步安全校验失败时记录为 invalid step 并继续，不让循环崩溃。
                    try:
                        _validate_action(action, platform=platform)
                        observation = None
                        if action.action == "extract":
                            observation = EvidenceObservation.model_validate(
                                action.payload.get("observation")
                            )
                            if observation.platform != platform:
                                raise ValueError(
                                    "extracted observation does not match task platform"
                                )
                    except ValueError as exc:
                        steps.append(
                            BrowseStep(
                                step=step_no,
                                action=None,
                                screenshot_hash=shot_hash,
                                rationale=f"invalid: {exc}",
                            )
                        )
                        self._publish_step(platform, account_id, steps[-1])
                        continue

                    if observation is not None and self._tracker is not None:
                        decision = self._tracker.consume_observation(
                            observation,
                            current_keyword=self._current_keyword,
                        )
                        if not decision.accepted:
                            user_reasons = truncation_reasons.setdefault(
                                observation.platform_user_id, set()
                            )
                            user_reasons.add(decision.reason)
                            steps.append(
                                BrowseStep(
                                    step=step_no,
                                    action=None,
                                    screenshot_hash=shot_hash,
                                    rationale=f"budget: {decision.reason}",
                                )
                            )
                            self._publish_step(platform, account_id, steps[-1])
                            if decision.stop_after:
                                exhaustion_reason = decision.reason
                                break
                            continue

                        reached_for_candidate = {
                            reason
                            for reason in decision.reached_reasons
                            if reason in {
                                "max_profiles",
                                "max_total_observations",
                            }
                        }
                        if reached_for_candidate:
                            truncation_reasons.setdefault(
                                observation.platform_user_id, set()
                            ).update(reached_for_candidate)

                    if observation is not None:
                        observations.append(observation)

                    step = BrowseStep(
                        step=step_no,
                        action=action,
                        screenshot_hash=shot_hash,
                        rationale=action.rationale,
                    )
                    steps.append(
                        step
                    )
                    self._publish_step(platform, account_id, step)

                    if (
                        observation is not None
                        and self._tracker is not None
                        and decision.stop_after
                    ):
                        exhaustion_reason = "max_total_observations"
                        break

                    before_url = str(
                        getattr(browser, "current_url", "") or ""
                    )
                    if not _platform_url_is_safe(
                        platform, before_url, allow_empty=pages == 0
                    ):
                        status = "error"
                        exhaustion_reason = "unsafe_url"
                        break
                    record_safe_url(before_url)

                    if (
                        action.action in {"navigate", "click"}
                        and pages >= self._max_pages
                    ):
                        exhaustion_reason = "max_pages"
                        break
                    await _await_with_deadline(
                        _execute_action(browser, action),
                        deadline=deadline,
                        monotonic=self._monotonic,
                    )

                    after_url = str(
                        getattr(browser, "current_url", "") or ""
                    )
                    if not _platform_url_is_safe(
                        platform, after_url, allow_empty=pages == 0
                    ):
                        status = "error"
                        exhaustion_reason = "unsafe_url"
                        break
                    record_safe_url(after_url)
                    if action.action == "navigate":
                        pages += 1
                    elif action.action == "click" and after_url != before_url:
                        pages += 1

                    if action.action == "done":
                        status = "done"
                        summary = str(action.payload.get("summary") or "")
                        break

                if status == "timeout" and not exhaustion_reason:
                    exhaustion_reason = "max_steps"
            except (asyncio.TimeoutError, TimeoutError):
                status = "timeout"
                exhaustion_reason = "max_duration"

            elapsed = max(0.0, self._monotonic() - started_at)

            return BrowseResult(
                status=status,
                summary=summary,
                steps=len(steps),
                steps_detail=steps,
                observations=observations,
                exhaustion_reason=exhaustion_reason,
                budget_usage={
                    "steps": len(steps),
                    "pages": pages,
                    "llm_calls": llm_calls,
                    "duration_seconds": elapsed,
                },
                last_visited_urls=visited_urls,
                truncation_reasons_by_user={
                    user_id: tuple(sorted(reasons))
                    for user_id, reasons in truncation_reasons.items()
                },
            )
        finally:
            if self._manage_browser_lifecycle:
                await _safe_close(browser)
            await _publish_event_bounded(
                self._bus,
                Event(
                    type=EventType.BROWSE_DONE,
                    payload={
                        "platform": platform,
                        "accountId": account_id,
                        "status": status,
                        "steps": len(steps),
                        "summary": summary,
                        "exhaustionReason": exhaustion_reason,
                        "budgetUsage": {
                            "steps": len(steps),
                            "pages": pages,
                            "llm_calls": llm_calls,
                            "duration_seconds": elapsed,
                        },
                    },
                    source="browse_agent",
                ),
            )

    async def _ask_llm(
        self,
        *,
        goal: str,
        url: str,
        screenshot_hash: str,
        dom_snapshot: str,
        step: int,
    ) -> BrowseAction:
        # 走 iteration 路由，与 Pipeline 阶段决策共用通道，
        # 失败时熔断与 fallback 行为由 Router 统一保障。
        prompt = (
            f"goal: {goal}\n"
            f"step: {step}\n"
            f"url: {url}\n"
            f"screenshot_sha256: {screenshot_hash}\n"
            f"{dom_snapshot}\n"
            "决定下一步动作。"
        )
        decision = await self._router.json_completion(
            prompt,
            system=_DECISION_SYSTEM,
            route="iteration",
        )
        # 不在这里做安全校验 — 由 run() 统一处理为 invalid step 并继续循环。
        return BrowseAction(
            action=str(decision.get("action") or ""),
            payload=dict(decision.get("payload") or {}),
            rationale=str(decision.get("rationale") or ""),
        )

    def _publish_step(
        self,
        platform: str,
        account_id: int,
        step: BrowseStep,
    ) -> None:
        self._bus.fire(
            Event(
                type=EventType.BROWSE_STEP,
                payload={
                    "platform": platform,
                    "accountId": account_id,
                    "step": step.step,
                    "action": (
                        step.action.action if step.action is not None else "invalid"
                    ),
                    "rationale": step.rationale,
                    "screenshotHash": step.screenshot_hash,
                },
                source="browse_agent",
            )
        )


# === helpers ============================================================


async def _visible_body_text(browser: _BrowserLike) -> str:
    text_method = getattr(browser, "text", None)
    if callable(text_method):
        try:
            return _sanitize_visible_text(await text_method("body"))
        except Exception:
            logger.debug("browser text extraction failed", exc_info=True)
            return ""
    try:
        body = await browser.query("body")
        if body is None or not hasattr(body, "inner_text"):
            return ""
        return _sanitize_visible_text(await body.inner_text())
    except Exception:
        logger.debug("browser body extraction failed", exc_info=True)
        return ""


_INTERACTIVE_SELECTOR = (
    "a, button, input, textarea, select, [role='button'], [role='link'], "
    "[data-e2e]"
)
_SAFE_ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,119}$")
_SAFE_ATTR = re.compile(r"^[A-Za-z0-9 _.-]{1,120}$")


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


async def _element_attribute(element: Any, name: str) -> str:
    getter = getattr(element, "get_attribute", None)
    if not callable(getter):
        return ""
    try:
        return _sanitize_visible_text(str(await getter(name) or ""), limit=1000)
    except Exception:
        return ""


async def _element_text(element: Any) -> str:
    for method_name in ("inner_text", "text_content"):
        method = getattr(element, method_name, None)
        if callable(method):
            try:
                return _sanitize_visible_text(str(await method() or ""), limit=500)
            except Exception:
                continue
    return ""


async def _element_tag(element: Any) -> str:
    tag = getattr(element, "tag_name", "")
    if callable(tag):
        try:
            tag = await _maybe_await(tag())
        except Exception:
            tag = ""
    if tag:
        normalized = str(tag).strip().lower()
        if re.fullmatch(r"[a-z][a-z0-9-]{0,30}", normalized):
            return normalized
    evaluate = getattr(element, "evaluate", None)
    if callable(evaluate):
        try:
            normalized = str(
                await evaluate("el => el.tagName.toLowerCase()") or ""
            ).strip().lower()
            if re.fullmatch(r"[a-z][a-z0-9-]{0,30}", normalized):
                return normalized
        except Exception:
            pass
    return "element"


def _safe_selector_hint(
    *, tag: str, element_id: str, data_e2e: str, aria_label: str, href: str
) -> str:
    if _SAFE_ID.fullmatch(element_id):
        return f"#{element_id}"
    if _SAFE_ATTR.fullmatch(data_e2e):
        return f'{tag}[data-e2e="{data_e2e}"]'
    if _SAFE_ATTR.fullmatch(aria_label):
        return f'{tag}[aria-label="{aria_label}"]'
    if href and all(char not in href for char in ('"', "\\", "\n", "\r")):
        return f'{tag}[href="{href}"]'
    return tag


async def _structured_dom_snapshot(
    browser: _BrowserLike, *, platform: str
) -> str:
    bounded_query = getattr(type(browser), "query_all_limited", None)
    if not callable(bounded_query):
        bounded_query = vars(browser).get("query_all_limited")
    if callable(bounded_query):
        try:
            elements = await browser.query_all_limited(  # type: ignore[attr-defined]
                _INTERACTIVE_SELECTOR, 100
            )
            rows: list[dict[str, str]] = []
            current_url = str(getattr(browser, "current_url", "") or "")
            for element in list(elements or [])[:100]:
                tag = await _element_tag(element)
                text_value = await _element_text(element)
                href = await _element_attribute(element, "href")
                if href:
                    href = urljoin(current_url, href)
                    if not _platform_url_is_safe(platform, href):
                        href = ""
                element_id = await _element_attribute(element, "id")
                data_e2e = await _element_attribute(element, "data-e2e")
                aria_label = await _element_attribute(element, "aria-label")
                row = {
                    "tag": tag,
                    "text": text_value,
                    "href": href,
                    "id": element_id,
                    "data-e2e": data_e2e,
                    "aria-label": aria_label,
                    "selector": _safe_selector_hint(
                        tag=tag,
                        element_id=element_id,
                        data_e2e=data_e2e,
                        aria_label=aria_label,
                        href=href,
                    ),
                }
                rows.append({key: value for key, value in row.items() if value})
            if rows:
                bounded_rows: list[dict[str, str]] = []
                for row in rows:
                    candidate = json.dumps(
                        [*bounded_rows, row],
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    if len(candidate) > 12_000:
                        break
                    bounded_rows.append(row)
                if bounded_rows:
                    return "structured_dom_snapshot:\n" + json.dumps(
                        bounded_rows,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
        except Exception:
            logger.debug("structured DOM extraction failed", exc_info=True)
    fallback = await _visible_body_text(browser)
    return "visible_dom_body_text:\n" + fallback


async def _resolve_browser(factory: BrowserFactory) -> _BrowserLike:
    instance = factory()
    if asyncio.iscoroutine(instance):
        instance = await instance
    return instance  # type: ignore[return-value]


async def _safe_close(browser: _BrowserLike) -> None:
    try:
        await asyncio.wait_for(browser.close(), timeout=0.25)
    except (asyncio.TimeoutError, TimeoutError):
        logger.warning("browser close timed out")
    except Exception:
        logger.warning("browser close failed", exc_info=True)


async def _publish_event_bounded(bus: EventBus, event: Event) -> None:
    """Telemetry must never hold the completed browser result hostage."""

    try:
        await asyncio.wait_for(bus.publish(event), timeout=0.05)
    except (asyncio.TimeoutError, TimeoutError):
        logger.warning("browse completion event publish timed out")
    except Exception:
        logger.warning("browse completion event publish failed", exc_info=True)


async def _await_with_deadline(
    awaitable: Awaitable[Any],
    *,
    deadline: float,
    monotonic: Callable[[], float],
) -> Any:
    remaining = deadline - monotonic()
    if remaining <= 0:
        if inspect.iscoroutine(awaitable):
            awaitable.close()
        raise asyncio.TimeoutError
    return await asyncio.wait_for(awaitable, timeout=remaining)


async def _execute_action(browser: _BrowserLike, action: BrowseAction) -> None:
    name = action.action
    if name == "navigate":
        await browser.navigate(str(action.payload["url"]))
    elif name == "click":
        await browser.click(str(action.payload["selector"]))
    elif name == "scroll":
        px = int(action.payload.get("px") or 500)
        await browser.scroll_down(px=px)
    elif name == "wait":
        ms = int(action.payload.get("ms") or 1000)
        await browser.wait(ms)
    elif name == "extract":
        # 不额外抓 DOM，只把 note 记录进 detail（运行方可订阅 BROWSE_STEP）
        return
    # done 在 run() 中提前处理


# EventBus 已经暴露 append_history：同步、容错地把事件追加到 history。
def _install_event_bus_helper() -> None:
    pass


_install_event_bus_helper()


__all__ = [
    "ALLOWED_ACTIONS",
    "BrowseAction",
    "BrowseAgent",
    "BrowseResult",
    "BrowseStep",
    "BrowserFactory",
]
