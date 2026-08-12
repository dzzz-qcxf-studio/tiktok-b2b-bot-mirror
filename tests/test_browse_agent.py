"""测试 Hermes 驱动的浏览器 Agent（截图→LLM→动作 闭环）。

Agent 通过 `LLMRouter.json_completion` 拿决策，每次循环受限于一个受控动作集合。
事件总线订阅者能看到完整步骤流，截图不入日志。
"""

from __future__ import annotations

import asyncio
import itertools
import json
import threading
import time
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from tiktok_bot_core.events.bus import Event, EventBus, EventType
from tiktok_bot_core.llm.router import LLMRouteError
from tiktok_bot_core.services.browse_agent import (
    ALLOWED_ACTIONS,
    BrowseAction,
    BrowseAgent,
    BrowseResult,
    BrowseStep,
)


# === 用 fake LLM 喂出确定的决策序列 ============================================

@dataclass
class FakeDecision:
    action: str
    payload: dict[str, Any]
    rationale: str = ""


def _wrap_decision(decision: FakeDecision) -> dict[str, Any]:
    return {
        "action": decision.action,
        "payload": decision.payload,
        "rationale": decision.rationale,
    }


def make_fake_router(decisions: list[dict[str, Any]]) -> AsyncMock:
    """side_effect 用迭代器；iter() 让超过列表长度时自动重放最后一项。"""
    router = MagicMock()
    queue = list(decisions)
    router.json_completion = AsyncMock(side_effect=itertools.chain(queue, itertools.repeat(queue[-1])))
    return router


class FakeBrowserClient:
    """模拟 BrowserClient，但只记录调用、不真打开浏览器。"""

    def __init__(
        self,
        *,
        screenshots: list[bytes] | None = None,
        url: str = "https://www.douyin.com/",
        elements: dict[str, Any] | None = None,
        click_redirect_url: str | None = None,
    ) -> None:
        self._screenshots = list(screenshots or [b"\xff\xd8\xff"])
        self._url = url
        self._elements = elements or {}
        self._click_redirect_url = click_redirect_url
        self._page = MagicMock()
        self._context = MagicMock()
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.closed = False

    async def init(self) -> None:
        self.calls.append(("init", ()))

    async def close(self) -> None:
        self.calls.append(("close", ()))
        self.closed = True

    async def navigate(self, url: str) -> None:
        self.calls.append(("navigate", (url,)))
        self._url = url

    async def click(self, selector: str) -> None:
        self.calls.append(("click", (selector,)))
        if self._click_redirect_url is not None:
            self._url = self._click_redirect_url

    async def scroll_down(self, px: int = 500) -> None:
        self.calls.append(("scroll_down", (px,)))

    async def wait(self, ms: int) -> None:
        self.calls.append(("wait", (ms,)))

    async def screenshot(self, full_page: bool = False) -> bytes:
        self.calls.append(("screenshot", (full_page,)))
        if not self._screenshots:
            return b"\xff\xd8\xff"
        return self._screenshots.pop(0)

    async def query(self, selector: str) -> Any:
        self.calls.append(("query", (selector,)))
        return self._elements.get(selector)

    async def query_all(self, selector: str) -> list[Any]:
        self.calls.append(("query_all", (selector,)))
        return list(self._elements.get(selector, []))

    async def text(self, selector: str) -> str:
        element = self._elements.get(selector)
        if element is None or not hasattr(element, "inner_text"):
            return ""
        return str(await element.inner_text())

    @property
    def current_url(self) -> str:
        return self._url

    @property
    def _page_obj(self) -> Any:
        return self._page

    @property
    def _ctx(self) -> Any:
        return self._context


class CapturingLiveRecorder:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.browse_calls: list[dict[str, Any]] = []

    def record_browse(self, **kwargs):
        self.browse_calls.append(dict(kwargs))
        if self.fail:
            raise RuntimeError("telemetry unavailable")


class SlowFailingLiveRecorder(CapturingLiveRecorder):
    def __init__(self, delay_seconds: float) -> None:
        super().__init__()
        self.delay_seconds = delay_seconds

    def record_browse(self, **kwargs):
        time.sleep(self.delay_seconds)
        self.browse_calls.append(dict(kwargs))
        raise RuntimeError("telemetry unavailable")


class BlockingLiveRecorder(CapturingLiveRecorder):
    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()

    def record_browse(self, **kwargs):
        self.started.set()
        self.release.wait()
        self.browse_calls.append(dict(kwargs))


# === 动作协议契约 ==============================================================


def test_allowed_actions_is_a_fixed_safe_set():
    """Agent 的动作集合必须固定、不可让 LLM 任意扩展。"""

    assert ALLOWED_ACTIONS == frozenset(
        {"navigate", "click", "scroll", "wait", "extract", "done"}
    )


def test_validate_action_rejects_unknown_kind():
    from tiktok_bot_core.services.browse_agent import _validate_action

    with pytest.raises(ValueError):
        _validate_action(
            BrowseAction(action="exec", payload={"cmd": "rm -rf /"})
        )


def test_validate_action_rejects_unsafe_navigate_url():
    from tiktok_bot_core.services.browse_agent import _validate_action

    with pytest.raises(ValueError):
        _validate_action(
            BrowseAction(action="navigate", payload={"url": "file:///etc/passwd"})
        )


def test_validate_action_rejects_click_without_selector():
    from tiktok_bot_core.services.browse_agent import _validate_action

    with pytest.raises(ValueError):
        _validate_action(BrowseAction(action="click", payload={}))


@pytest.mark.parametrize("ms", [49, 10000.5, 10001, True])
def test_validate_action_rejects_wait_outside_safe_range(ms):
    from tiktok_bot_core.services.browse_agent import _validate_action

    with pytest.raises(ValueError, match="wait"):
        _validate_action(BrowseAction(action="wait", payload={"ms": ms}))


@pytest.mark.parametrize("px", [0, 3000.5, 3001, True])
def test_validate_action_rejects_scroll_outside_safe_range(px):
    from tiktok_bot_core.services.browse_agent import _validate_action

    with pytest.raises(ValueError, match="scroll"):
        _validate_action(BrowseAction(action="scroll", payload={"px": px}))


# === 闭环行为 =================================================================


@pytest.mark.asyncio
async def test_agent_drives_browser_through_llm_decisions_to_done():
    """3 步闭环：navigate → scroll → done。每步都先截图再喂 LLM。"""

    decisions = [
        _wrap_decision(FakeDecision("navigate", {"url": "https://www.douyin.com/user/MS4wLjABAAAA"})),
        _wrap_decision(FakeDecision("scroll", {"px": 800})),
        _wrap_decision(FakeDecision("done", {"summary": "找到 5 个候选"})),
    ]
    router = make_fake_router(decisions)
    browser = FakeBrowserClient()
    bus = EventBus()
    agent = BrowseAgent(
        router=router,
        bus=bus,
        browser_factory=lambda: browser,
        max_steps=10,
    )
    result = await agent.run(goal="找一个批发商账号", platform="douyin", account_id=1)
    # 等 fire-and-forget 派发结束，避免 history 检查丢事件。
    if bus._pending:
        await asyncio.gather(*bus._pending, return_exceptions=True)

    # 验证决策被使用
    assert router.json_completion.await_count == 3

    # 验证浏览器被驱动
    actions = [name for name, _ in browser.calls]
    assert "init" in actions
    assert "navigate" in actions
    assert "scroll_down" in actions
    assert actions[-1] == "close"

    # 验证 BrowserClient 在 navigate 之后没有立即截图（截图发生在决策前）
    # 顺序应是 init → screenshot → navigate → screenshot → scroll_down → screenshot → close
    assert actions == [
        "init",
        "screenshot",  # step1 决策前的截图
        "navigate",    # step1 执行
        "screenshot",  # step2 决策前的截图
        "scroll_down",
        "screenshot",  # step3 决策前的截图
        "close",
    ]

    # 验证结果
    assert isinstance(result, BrowseResult)
    assert result.status == "done"
    assert result.summary == "找到 5 个候选"
    assert result.steps == 3

    # 验证事件流（history 是同步追加、按时间顺序）
    step_events = bus.history(EventType.BROWSE_STEP)
    done_events = bus.history(EventType.BROWSE_DONE)
    assert len(step_events) == 3
    assert len(done_events) == 1
    assert step_events[0].payload["step"] == 1
    assert step_events[0].payload["action"] == "navigate"
    assert step_events[1].payload["step"] == 2
    assert step_events[1].payload["action"] == "scroll"
    assert done_events[0].payload["status"] == "done"


@pytest.mark.asyncio
async def test_action_timeout_is_a_bounded_step_and_preserves_prior_evidence():
    class TimeoutClickBrowser(FakeBrowserClient):
        async def click(self, selector: str) -> None:
            self.calls.append(("click", (selector,)))
            raise PlaywrightTimeoutError("element stayed hidden")

    observation = {
        "platform": "douyin",
        "platform_user_id": "video-author-1",
        "username": "author",
        "source_type": "video_author",
        "author_url": "https://www.douyin.com/user/video-author-1",
        "source_path": ["keyword", "video", "author"],
    }
    router = make_fake_router([
        _wrap_decision(FakeDecision("extract", {"observation": observation})),
        _wrap_decision(FakeDecision("click", {"selector": "button.hidden-comment"})),
        _wrap_decision(FakeDecision("done", {"summary": "bounded recovery"})),
    ])
    browser = TimeoutClickBrowser()
    bus = EventBus()
    recorder = CapturingLiveRecorder()

    result = await BrowseAgent(
        router=router,
        bus=bus,
        browser_factory=lambda: browser,
        max_steps=3,
        event_recorder=recorder,
        job_id="job-timeout",
        stage="collect",
    ).run(goal="recover one action", platform="douyin", account_id=1)

    assert result.status == "done"
    assert result.summary == "bounded recovery"
    assert result.steps == 3
    assert result.steps_detail[1].action is None
    assert result.steps_detail[1].rationale == "retryable: timeout"
    assert [item.platform_user_id for item in result.observations] == [
        "video-author-1"
    ]
    assert router.json_completion.await_count == 3
    step_events = bus.history(EventType.BROWSE_STEP)
    assert [event.payload["action"] for event in step_events] == [
        "extract",
        "invalid",
        "done",
    ]
    action_calls = [
        call for call in recorder.browse_calls
        if call.get("step") == 2
    ]
    assert len(action_calls) == 1
    assert action_calls[0]["action"] == "error"
    assert action_calls[0]["error_code"] == "timeout"


@pytest.mark.asyncio
async def test_job_scoped_browse_events_are_explicit_and_cli_mode_is_memory_only():
    recorder = CapturingLiveRecorder()
    agent = BrowseAgent(
        router=make_fake_router([
            _wrap_decision(FakeDecision("scroll", {"px": 600}, "scan results")),
            _wrap_decision(FakeDecision("done", {"summary": "complete"})),
        ]),
        bus=EventBus(),
        browser_factory=lambda: FakeBrowserClient(),
        event_recorder=recorder,
        job_id="job-alpha",
        stage="collect",
    )

    result = await agent.run(
        goal="find candidates",
        platform="douyin",
        account_id=1,
    )

    assert result.status == "done"
    assert recorder.browse_calls
    assert {
        (call["job_id"], call["stage"])
        for call in recorder.browse_calls
    } == {("job-alpha", "collect")}
    assert {call["action"] for call in recorder.browse_calls} >= {
        "scroll",
        "done",
    }
    step_call = next(
        call for call in recorder.browse_calls if call["action"] == "scroll"
    )
    assert step_call["scroll_px"] == 600
    assert step_call["screenshot_hash"]
    assert len(step_call["screenshot_hash"]) == 64
    assert "screenshot" not in step_call

    calls_before_cli = len(recorder.browse_calls)
    standalone = BrowseAgent(
        router=make_fake_router([
            _wrap_decision(FakeDecision("done", {"summary": "standalone"})),
        ]),
        bus=EventBus(),
        browser_factory=lambda: FakeBrowserClient(),
        event_recorder=recorder,
    )
    standalone_result = await standalone.run(
        goal="standalone browse",
        platform="douyin",
        account_id=2,
    )
    assert standalone_result.status == "done"
    assert len(recorder.browse_calls) == calls_before_cli


@pytest.mark.asyncio
async def test_parallel_job_browse_events_never_mix_job_context():
    recorder = CapturingLiveRecorder()

    async def run_one(job_id: str, account_id: int):
        return await BrowseAgent(
            router=make_fake_router([
                _wrap_decision(
                    FakeDecision("scroll", {"px": 400}, "scan results")
                ),
                _wrap_decision(FakeDecision("done", {"summary": job_id})),
            ]),
            bus=EventBus(),
            browser_factory=lambda: FakeBrowserClient(),
            event_recorder=recorder,
            job_id=job_id,
            stage="collect",
        ).run(
            goal=job_id,
            platform="douyin",
            account_id=account_id,
        )

    first, second = await asyncio.gather(
        run_one("job-one", 11),
        run_one("job-two", 22),
    )

    assert first.summary == "job-one"
    assert second.summary == "job-two"
    grouped = {
        job_id: [
            call for call in recorder.browse_calls if call["job_id"] == job_id
        ]
        for job_id in ("job-one", "job-two")
    }
    assert all(grouped.values())
    assert all(
        call["stage"] == "collect"
        for calls in grouped.values()
        for call in calls
    )


@pytest.mark.asyncio
async def test_parallel_browse_events_persist_to_their_own_jobs(tmp_path):
    from tiktok_bot_core.services.pipeline_live_events import (
        PipelineLiveEventRecorder,
    )
    from tiktok_bot_core.storage.database import Database
    from tiktok_bot_core.storage.pipeline_job_store import PipelineJobStore
    from tiktok_bot_core.storage.pipeline_live_store import PipelineLiveStore

    database = Database(f"sqlite:///{tmp_path / 'browse-live.db'}")
    database.init()
    with database.session() as session:
        first_job = PipelineJobStore().create_job(
            session,
            platform="douyin",
            account_mode="auto",
            account_id=None,
            stages=["collect"],
        ).id
        second_job = PipelineJobStore().create_job(
            session,
            platform="douyin",
            account_mode="auto",
            account_id=None,
            stages=["collect"],
        ).id
    recorder = PipelineLiveEventRecorder(database)

    async def run_one(job_id: str, account_id: int):
        return await BrowseAgent(
            router=make_fake_router([
                _wrap_decision(
                    FakeDecision("scroll", {"px": 400}, "scan results")
                ),
                _wrap_decision(FakeDecision("done", {"summary": job_id})),
            ]),
            bus=EventBus(),
            browser_factory=lambda: FakeBrowserClient(),
            event_recorder=recorder,
            job_id=job_id,
            stage="collect",
        ).run(goal=job_id, platform="douyin", account_id=account_id)

    await asyncio.gather(
        run_one(first_job, 1),
        run_one(second_job, 2),
    )

    with database.session() as session:
        first_events = PipelineLiveStore().list_events(
            session,
            job_id=first_job,
        )
        second_events = PipelineLiveStore().list_events(
            session,
            job_id=second_job,
        )
        assert first_events and second_events
        assert {event.job_id for event in first_events} == {first_job}
        assert {event.job_id for event in second_events} == {second_job}
        assert {event.stage for event in first_events + second_events} == {
            "collect"
        }
        done_events = [
            event
            for event in first_events + second_events
            if event.event_type == "browse.done"
            and event.payload_json.get("summary")
        ]
        assert len(done_events) == 2
        assert all(
            set(event.payload_json["budget"])
            == {"stepsUsed", "pagesUsed", "llmCallsUsed", "durationSeconds"}
            for event in done_events
        )
        persisted_payloads = [
            event.payload_json for event in first_events + second_events
        ]
        assert all(
            not {
                "steps",
                "llm_calls",
                "duration_seconds",
                "cookie",
                "token",
                "auth",
                "api_key",
                "profile",
                "prompt",
                "response",
                "credential",
            }.intersection(payload)
            for payload in persisted_payloads
        )
        persisted_text = json.dumps(persisted_payloads).lower()
        assert not any(
            marker in persisted_text
            for marker in (
                "cookie",
                "bearer ",
                "api_key",
                "apikey",
                "credential",
                "rawprompt",
                "rawresponse",
            )
        )
        step_events = [
            event
            for event in first_events + second_events
            if event.payload_json.get("screenshotHash")
        ]
        assert step_events
        assert all("screenshot" not in event.payload_json for event in step_events)
    database.engine.dispose()


@pytest.mark.asyncio
async def test_live_recorder_failure_does_not_change_result_or_budget():
    decisions = [
        _wrap_decision(FakeDecision("done", {"summary": "safe result"})),
    ]
    baseline = await BrowseAgent(
        router=make_fake_router(decisions),
        bus=EventBus(),
        browser_factory=lambda: FakeBrowserClient(),
        monotonic=lambda: 0.0,
    ).run(goal="baseline", platform="douyin", account_id=1)
    with_failed_recorder = await BrowseAgent(
        router=make_fake_router(decisions),
        bus=EventBus(),
        browser_factory=lambda: FakeBrowserClient(),
        event_recorder=CapturingLiveRecorder(fail=True),
        job_id="job-fail-open",
        stage="collect",
        monotonic=lambda: 0.0,
    ).run(goal="baseline", platform="douyin", account_id=1)

    assert with_failed_recorder == baseline


@pytest.mark.asyncio
async def test_slow_failing_recorder_does_not_consume_browse_deadline_or_leak_tasks():
    def build_agent(recorder=None):
        return BrowseAgent(
            router=make_fake_router([
                _wrap_decision(
                    FakeDecision("scroll", {"px": 400}, "scan results")
                ),
                _wrap_decision(FakeDecision("done", {"summary": "complete"})),
            ]),
            bus=EventBus(),
            browser_factory=lambda: FakeBrowserClient(),
            max_duration_seconds=0.01,
            monotonic=lambda: 0.0,
            event_recorder=recorder,
            job_id="job-slow" if recorder is not None else "",
            stage="collect" if recorder is not None else "",
        )

    baseline = await build_agent().run(
        goal="baseline",
        platform="douyin",
        account_id=1,
    )
    tasks_before = set(asyncio.all_tasks())
    recorder = SlowFailingLiveRecorder(0.03)
    with_recorder = await build_agent(recorder).run(
        goal="baseline",
        platform="douyin",
        account_id=1,
    )
    await asyncio.sleep(0)

    assert with_recorder == baseline
    assert [call["action"] for call in recorder.browse_calls] == [
        "scroll",
        "done",
    ]
    leaked = [
        task
        for task in set(asyncio.all_tasks()) - tasks_before
        if not task.done() and task.get_name().startswith("browse-telemetry-")
    ]
    assert leaked == []


@pytest.mark.asyncio
async def test_blocking_recorder_cannot_block_browse_result_or_leak_tasks():
    recorder = BlockingLiveRecorder()
    agent = BrowseAgent(
        router=make_fake_router([
            _wrap_decision(FakeDecision("done", {"summary": "complete"})),
        ]),
        bus=EventBus(),
        browser_factory=lambda: FakeBrowserClient(),
        event_recorder=recorder,
        job_id="job-blocking-recorder",
        stage="collect",
    )
    tasks_before = set(asyncio.all_tasks())
    task = asyncio.create_task(
        agent.run(goal="baseline", platform="douyin", account_id=1)
    )
    assert await asyncio.to_thread(recorder.started.wait, 1.0)
    try:
        result = await asyncio.wait_for(task, timeout=2.0)
    finally:
        recorder.release.set()
    await asyncio.sleep(0)

    assert result.status == "done"
    leaked = [
        pending
        for pending in set(asyncio.all_tasks()) - tasks_before
        if not pending.done()
        and pending.get_name().startswith("browse-telemetry-")
    ]
    assert leaked == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("decision", "expected_error_code"),
    [
        (
            LLMRouteError(route="iteration", error_category="circuit_open"),
            "circuit_open",
        ),
        ({"action": "done", "payload": 1}, "internal_error"),
    ],
)
async def test_propagated_browse_failure_records_only_one_safe_error(
    decision,
    expected_error_code,
):
    recorder = CapturingLiveRecorder()
    router = MagicMock()
    router.json_completion = AsyncMock(
        side_effect=decision if isinstance(decision, Exception) else None,
        return_value=decision if isinstance(decision, dict) else None,
    )
    agent = BrowseAgent(
        router=router,
        bus=EventBus(),
        browser_factory=lambda: FakeBrowserClient(),
        event_recorder=recorder,
        job_id="job-error",
        stage="collect",
    )

    with pytest.raises(LLMRouteError):
        await agent.run(goal="fail safely", platform="douyin", account_id=1)

    assert len(recorder.browse_calls) == 1
    assert recorder.browse_calls[0]["action"] == "error"
    assert recorder.browse_calls[0]["error_code"] == expected_error_code
    assert "message" not in recorder.browse_calls[0]
    assert "rationale" not in recorder.browse_calls[0]


@pytest.mark.asyncio
async def test_agent_routes_through_iteration_route_not_default():
    """AI 决策必须走 iteration 路由；不允许默认走 default。"""

    router = MagicMock()
    router.json_completion = AsyncMock(
        side_effect=[
            _wrap_decision(FakeDecision("done", {"summary": "ok"})),
        ]
    )
    browser = FakeBrowserClient()

    agent = BrowseAgent(
        router=router,
        bus=EventBus(),
        browser_factory=lambda: browser,
        max_steps=5,
    )
    await agent.run(goal="测试", platform="douyin", account_id=1)

    call_kwargs = router.json_completion.await_args.kwargs
    assert call_kwargs.get("route") == "iteration", call_kwargs


@pytest.mark.asyncio
async def test_agent_prompt_contains_sanitized_visible_dom_url_and_screenshot_hash():
    class Body:
        async def inner_text(self):
            return "Visible buyer demand\x00\n" + ("x" * 20000)

    router = make_fake_router([
        _wrap_decision(FakeDecision("done", {"summary": "ok"})),
    ])
    browser = FakeBrowserClient(
        screenshots=[b"jpeg-visible"],
        elements={"body": Body()},
    )
    agent = BrowseAgent(
        router=router,
        bus=EventBus(),
        browser_factory=lambda: browser,
    )

    await agent.run(goal="extract evidence", platform="douyin", account_id=1)

    prompt = router.json_completion.await_args.args[0]
    system = router.json_completion.await_args.kwargs["system"]
    assert "Visible buyer demand" in prompt
    assert "\x00" not in prompt
    assert "x" * 20000 not in prompt
    assert "https://www.douyin.com/" in prompt
    assert "screenshot_sha256:" in prompt
    assert "payload.observation" in system
    assert "EvidenceObservation" in system


@pytest.mark.asyncio
async def test_agent_caps_loop_at_max_steps_then_returns_timeout():
    """LLM 一直不出 done 时必须被 max_steps 截断，避免无限循环。"""

    decisions = [
        _wrap_decision(FakeDecision("scroll", {"px": 500})),
        _wrap_decision(FakeDecision("scroll", {"px": 500})),
        _wrap_decision(FakeDecision("scroll", {"px": 500})),
    ]
    router = make_fake_router(decisions)
    browser = FakeBrowserClient()
    agent = BrowseAgent(
        router=router,
        bus=EventBus(),
        browser_factory=lambda: browser,
        max_steps=3,
    )
    result = await agent.run(goal="不断滚动", platform="douyin", account_id=1)

    assert result.status == "timeout"
    assert result.steps == 3
    assert browser.closed is True


@pytest.mark.asyncio
async def test_agent_executes_click_using_query_selector():
    """click 必须经 query 拿 selector，再调 browser.click；LLM 不能直接传任意 JS。"""

    decisions = [
        _wrap_decision(
            FakeDecision(
                "click",
                {"selector": "button.follow"},
            )
        ),
        _wrap_decision(FakeDecision("done", {"summary": "已关注"})),
    ]
    router = make_fake_router(decisions)
    browser = FakeBrowserClient()

    agent = BrowseAgent(
        router=router,
        bus=EventBus(),
        browser_factory=lambda: browser,
        max_steps=5,
    )
    await agent.run(goal="关注账号", platform="douyin", account_id=1)

    click_calls = [c for c in browser.calls if c[0] == "click"]
    assert click_calls == [("click", ("button.follow",))]


@pytest.mark.asyncio
async def test_agent_rejects_unsafe_navigate_url():
    """navigate 的 url 必须是 http(s) 且与目标平台同域；file:// 与 javascript: 一律拒绝。"""

    decisions = [
        _wrap_decision(FakeDecision("navigate", {"url": "file:///etc/passwd"})),
        _wrap_decision(FakeDecision("navigate", {"url": "javascript:alert(1)"})),
        _wrap_decision(FakeDecision("done", {"summary": "ok"})),
    ]
    router = make_fake_router(decisions)
    browser = FakeBrowserClient()
    agent = BrowseAgent(
        router=router,
        bus=EventBus(),
        browser_factory=lambda: browser,
        max_steps=5,
    )
    await agent.run(goal="恶意", platform="douyin", account_id=1)

    # 两次不安全导航被拒，没有真的发生 navigate
    navigate_calls = [c for c in browser.calls if c[0] == "navigate"]
    assert navigate_calls == []


@pytest.mark.asyncio
async def test_agent_rejects_malicious_initial_url_before_llm_call():
    router = make_fake_router([
        _wrap_decision(FakeDecision("done", {"summary": "should not run"})),
    ])
    browser = FakeBrowserClient(url="https://evil.example/phish")
    agent = BrowseAgent(
        router=router,
        bus=EventBus(),
        browser_factory=lambda: browser,
    )

    result = await agent.run(goal="safe", platform="douyin", account_id=1)

    assert result.status == "error"
    assert result.exhaustion_reason == "unsafe_url"
    router.json_completion.assert_not_awaited()


@pytest.mark.asyncio
async def test_agent_fails_closed_after_click_redirects_off_platform():
    router = make_fake_router([
        _wrap_decision(FakeDecision("click", {"selector": "a.redirect"})),
        _wrap_decision(FakeDecision("done", {"summary": "should not run"})),
    ])
    browser = FakeBrowserClient(click_redirect_url="https://evil.example/phish")
    agent = BrowseAgent(
        router=router,
        bus=EventBus(),
        browser_factory=lambda: browser,
    )

    result = await agent.run(goal="safe", platform="douyin", account_id=1)

    assert result.status == "error"
    assert result.exhaustion_reason == "unsafe_url"
    assert router.json_completion.await_count == 1


@pytest.mark.asyncio
async def test_click_navigation_exhausts_page_budget_without_counter_overflow():
    router = make_fake_router([
        _wrap_decision(FakeDecision("click", {"selector": "a.next"})),
    ])
    browser = FakeBrowserClient(
        click_redirect_url="https://www.douyin.com/search/next"
    )
    agent = BrowseAgent(
        router=router,
        bus=EventBus(),
        browser_factory=lambda: browser,
        max_pages=1,
    )

    result = await agent.run(goal="bounded", platform="douyin", account_id=1)

    assert result.exhaustion_reason == "max_pages"
    assert result.budget_usage["pages"] == 1
    assert [call for call in browser.calls if call[0] == "click"] == []


@pytest.mark.asyncio
async def test_exact_seconds_deadline_bounds_llm_and_hanging_close():
    async def hang(*_args, **_kwargs):
        await asyncio.Event().wait()

    router = MagicMock()
    router.json_completion = AsyncMock(side_effect=hang)
    browser = FakeBrowserClient()
    browser.close = hang
    agent = BrowseAgent(
        router=router,
        bus=EventBus(),
        browser_factory=lambda: browser,
        max_duration_seconds=0.02,
    )

    result = await asyncio.wait_for(
        agent.run(goal="bounded", platform="douyin", account_id=1),
        timeout=0.5,
    )

    assert result.status == "timeout"
    assert result.exhaustion_reason == "max_duration"
    assert 0 < result.budget_usage["duration_seconds"] < 0.5


def test_exact_seconds_deadline_takes_precedence_over_legacy_minutes():
    agent = BrowseAgent(
        router=MagicMock(),
        bus=EventBus(),
        browser_factory=lambda: FakeBrowserClient(),
        max_duration_minutes=0,
        max_duration_seconds=0.02,
    )

    assert agent._max_duration_seconds == pytest.approx(0.02)


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked_operation", ["init", "screenshot", "action"])
async def test_exact_seconds_deadline_bounds_browser_operations(blocked_operation):
    async def hang(*_args, **_kwargs):
        await asyncio.Event().wait()

    decisions = (
        [_wrap_decision(FakeDecision("navigate", {"url": "https://www.douyin.com/search/x"}))]
        if blocked_operation == "action"
        else [_wrap_decision(FakeDecision("done", {"summary": "unused"}))]
    )
    browser = FakeBrowserClient(url="" if blocked_operation == "init" else "https://www.douyin.com/")
    setattr(browser, blocked_operation if blocked_operation != "action" else "navigate", hang)
    agent = BrowseAgent(
        router=make_fake_router(decisions),
        bus=EventBus(),
        browser_factory=lambda: browser,
        max_duration_seconds=0.02,
    )

    result = await asyncio.wait_for(
        agent.run(goal="bounded", platform="douyin", account_id=1),
        timeout=0.5,
    )

    assert result.status == "timeout"
    assert result.exhaustion_reason == "max_duration"


@pytest.mark.asyncio
async def test_structured_dom_snapshot_exposes_safe_selector_and_href_to_extract():
    class Link:
        tag_name = "a"

        async def inner_text(self):
            return "Buyer profile\x00"

        async def get_attribute(self, name):
            return {
                "href": "https://www.douyin.com/user/safe-buyer",
                "id": "buyer-link",
                "data-e2e": "buyer-card",
                "aria-label": "Open buyer",
            }.get(name)

    class StructuredBrowser(FakeBrowserClient):
        async def query_all_limited(self, selector, limit):
            self.calls.append(("query_all_limited", (selector, limit)))
            return [Link()]

    observation = {
        "platform": "douyin",
        "platform_user_id": "safe-buyer",
        "username": "buyer",
        "source_type": "video_author",
        "author_url": "https://www.douyin.com/user/safe-buyer",
        "source_path": ["keyword", "video", "author"],
    }
    router = make_fake_router([
        _wrap_decision(FakeDecision("extract", {"observation": observation})),
        _wrap_decision(FakeDecision("done", {"summary": "done"})),
    ])
    browser = StructuredBrowser()
    agent = BrowseAgent(
        router=router,
        bus=EventBus(),
        browser_factory=lambda: browser,
    )

    result = await agent.run(
        goal="extract public evidence", platform="douyin", account_id=1
    )

    prompt = router.json_completion.await_args_list[0].args[0]
    system = router.json_completion.await_args_list[0].kwargs["system"]
    assert "structured_dom_snapshot" in prompt
    assert "https://www.douyin.com/user/safe-buyer" in prompt
    assert "#buyer-link" in prompt
    assert "Buyer profile" in prompt
    assert "\x00" not in prompt
    assert len(prompt) < 13_000
    assert "screenshot hash" in system.lower()
    assert "structured DOM" in system
    assert "jpeg" not in system.lower()
    assert [item.platform_user_id for item in result.observations] == ["safe-buyer"]


@pytest.mark.asyncio
async def test_result_records_only_safe_visited_urls():
    router = make_fake_router([
        _wrap_decision(FakeDecision("click", {"selector": "a.redirect"})),
    ])
    browser = FakeBrowserClient(click_redirect_url="https://evil.example/phish")
    agent = BrowseAgent(
        router=router,
        bus=EventBus(),
        browser_factory=lambda: browser,
        max_pages=2,
    )

    result = await agent.run(goal="safe", platform="douyin", account_id=1)

    assert result.last_visited_urls == ["https://www.douyin.com/"]


@pytest.mark.asyncio
async def test_agent_reports_llm_budget_exhaustion_monotonically():
    router = make_fake_router([
        _wrap_decision(FakeDecision("scroll", {"px": 500})),
    ])
    browser = FakeBrowserClient()
    agent = BrowseAgent(
        router=router,
        bus=EventBus(),
        browser_factory=lambda: browser,
        max_steps=10,
        max_llm_calls=1,
    )

    result = await agent.run(goal="bounded", platform="douyin", account_id=1)

    assert result.status == "timeout"
    assert result.exhaustion_reason == "max_llm_calls"
    assert result.budget_usage["llm_calls"] == 1
    assert router.json_completion.await_count == 1


@pytest.mark.asyncio
async def test_agent_reports_page_budget_before_extra_navigation():
    router = make_fake_router([
        _wrap_decision(
            FakeDecision("navigate", {"url": "https://www.douyin.com/search/one"})
        ),
        _wrap_decision(
            FakeDecision("navigate", {"url": "https://www.douyin.com/search/two"})
        ),
    ])
    browser = FakeBrowserClient(url="")
    agent = BrowseAgent(
        router=router,
        bus=EventBus(),
        browser_factory=lambda: browser,
        max_steps=10,
        max_pages=1,
    )

    result = await agent.run(goal="bounded", platform="douyin", account_id=1)

    assert result.exhaustion_reason == "max_pages"
    assert result.budget_usage["pages"] == 1
    assert [call for call in browser.calls if call[0] == "navigate"] == [
        ("navigate", ("https://www.douyin.com/search/one",))
    ]


@pytest.mark.asyncio
async def test_agent_reports_monotonic_duration_budget():
    ticks = itertools.chain([0.0] * 7, itertools.repeat(61.0))
    router = make_fake_router([
        _wrap_decision(FakeDecision("scroll", {"px": 500})),
    ])
    agent = BrowseAgent(
        router=router,
        bus=EventBus(),
        browser_factory=lambda: FakeBrowserClient(),
        max_steps=10,
        max_duration_minutes=1,
        monotonic=lambda: next(ticks),
    )

    result = await agent.run(goal="bounded", platform="douyin", account_id=1)

    assert result.exhaustion_reason == "max_duration"
    assert result.budget_usage["duration_seconds"] >= 60
    assert router.json_completion.await_count == 1


@pytest.mark.asyncio
async def test_async_browser_factory_is_bounded_by_agent_deadline():
    async def hanging_factory():
        await asyncio.Future()

    agent = BrowseAgent(
        router=make_fake_router([
            _wrap_decision(FakeDecision("done", {"summary": "unused"}))
        ]),
        bus=EventBus(),
        browser_factory=hanging_factory,
        max_duration_seconds=0.02,
    )

    result = await asyncio.wait_for(
        agent.run(goal="bounded", platform="douyin", account_id=1),
        timeout=0.15,
    )
    assert result.status == "timeout"
    assert result.exhaustion_reason == "max_duration"


@pytest.mark.asyncio
async def test_browse_done_subscriber_cannot_block_completed_result():
    bus = EventBus()

    async def hanging_publish(_event):
        await asyncio.Future()

    bus.publish = hanging_publish
    agent = BrowseAgent(
        router=make_fake_router([
            _wrap_decision(FakeDecision("done", {"summary": "ok"}))
        ]),
        bus=bus,
        browser_factory=lambda: FakeBrowserClient(),
        max_duration_seconds=0.02,
    )

    result = await asyncio.wait_for(
        agent.run(goal="bounded", platform="douyin", account_id=1),
        timeout=0.15,
    )
    assert result.status == "done"


@pytest.mark.asyncio
async def test_agent_propagates_llm_route_error_with_safe_message():
    """LLM 失败必须抛 LLMRouteError，不暴露 provider 或 key。"""

    from tiktok_bot_core.llm.router import LLMRouteError

    router = MagicMock()
    router.json_completion = AsyncMock(
        side_effect=LLMRouteError(route="iteration", error_category="circuit_open")
    )
    browser = FakeBrowserClient()

    agent = BrowseAgent(
        router=router,
        bus=EventBus(),
        browser_factory=lambda: browser,
        max_steps=3,
    )
    with pytest.raises(LLMRouteError) as exc:
        await agent.run(goal="测试", platform="douyin", account_id=1)

    assert exc.value.error_category == "circuit_open"
    assert "iteration" in str(exc.value)
    assert browser.closed is True


@pytest.mark.asyncio
async def test_agent_retries_invalid_json_within_existing_step_budget():
    """单次非法 JSON 记为无效步骤，下一步可恢复且不会绕过调用预算。"""

    from tiktok_bot_core.llm.router import LLMRouteError

    router = MagicMock()
    router.json_completion = AsyncMock(
        side_effect=[
            LLMRouteError(route="iteration", error_category="invalid_json"),
            {"action": "done", "payload": {"summary": "recovered"}},
        ]
    )
    browser = FakeBrowserClient()
    agent = BrowseAgent(
        router=router,
        bus=EventBus(),
        browser_factory=lambda: browser,
        max_steps=3,
        max_llm_calls=3,
    )

    result = await agent.run(goal="测试", platform="douyin", account_id=1)

    assert result.status == "done"
    assert result.summary == "recovered"
    assert result.steps == 2
    assert result.steps_detail[0].action is None
    assert result.steps_detail[0].rationale == "invalid: invalid_json"
    assert result.budget_usage["llm_calls"] == 2
    assert router.json_completion.await_count == 2
    assert browser.closed is True


@pytest.mark.asyncio
@pytest.mark.parametrize("error_category", ["network", "timeout", "upstream_server"])
async def test_agent_retries_transient_error_within_existing_budget(
    error_category,
):
    """单次瞬时故障不得终止整词搜索，也不得绕过现有预算。"""

    from tiktok_bot_core.llm.router import LLMRouteError

    router = MagicMock()
    router.json_completion = AsyncMock(
        side_effect=[
            LLMRouteError(route="iteration", error_category=error_category),
            {"action": "done", "payload": {"summary": "recovered"}},
        ]
    )
    browser = FakeBrowserClient()
    agent = BrowseAgent(
        router=router,
        bus=EventBus(),
        browser_factory=lambda: browser,
        max_steps=3,
        max_llm_calls=3,
    )

    result = await agent.run(goal="测试", platform="douyin", account_id=1)

    assert result.status == "done"
    assert result.summary == "recovered"
    assert result.steps == 2
    assert result.steps_detail[0].rationale == f"retryable: {error_category}"
    assert result.budget_usage["llm_calls"] == 2
    assert router.json_completion.await_count == 2


@pytest.mark.asyncio
async def test_agent_stops_transient_retries_at_llm_call_budget():
    """连续瞬时故障必须在既有 LLM 调用预算耗尽时停止。"""

    from tiktok_bot_core.llm.router import LLMRouteError

    router = MagicMock()
    router.json_completion = AsyncMock(
        side_effect=LLMRouteError(route="iteration", error_category="network")
    )
    browser = FakeBrowserClient()
    agent = BrowseAgent(
        router=router,
        bus=EventBus(),
        browser_factory=lambda: browser,
        max_steps=5,
        max_llm_calls=2,
    )

    result = await agent.run(goal="测试", platform="douyin", account_id=1)

    assert result.status == "timeout"
    assert result.exhaustion_reason == "max_llm_calls"
    assert result.steps == 2
    assert result.budget_usage["llm_calls"] == 2
    assert router.json_completion.await_count == 2
    assert browser.closed is True


@pytest.mark.asyncio
async def test_agent_closes_browser_even_when_decision_parsing_fails():
    """LLM 返回非法 JSON 时必须清理浏览器，不能泄漏资源。"""

    router = MagicMock()
    router.json_completion = AsyncMock(side_effect=[{"unparseable": "thing"}])
    browser = FakeBrowserClient()
    agent = BrowseAgent(
        router=router,
        bus=EventBus(),
        browser_factory=lambda: browser,
        max_steps=3,
    )
    with pytest.raises(Exception):
        await agent.run(goal="测试", platform="douyin", account_id=1)
    assert browser.closed is True
