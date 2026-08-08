"""关键词搜集器 — 双平台（TikTok + 抖音）

通过 Platform 抽象自动切换 URL 与选择器。
"""

import logging
import math
import time
from urllib.parse import quote, urljoin, urlsplit

from tiktok_bot_core.extensions.registry import CollectorPlugin
from tiktok_bot_core.browser.providers import require_browser_client
from tiktok_bot_core.platforms import PlatformType, get_platform
from tiktok_bot_core.services.acquisition_agents import (
    CandidateObservation,
    EvidenceObservation,
    ExplorationBudget,
    ExplorationBudgetTracker,
)

logger = logging.getLogger(__name__)


class KeywordCollector(CollectorPlugin):
    """关键词搜集（双平台通用）"""

    name = "keyword"

    def __init__(self, *, monotonic=None) -> None:
        self._monotonic = monotonic or time.monotonic

    async def collect(self, config: dict) -> list[dict]:
        """搜集用户

        Args:
            config: {
                "keywords": list[str],         # 搜索关键词
                "max_per_keyword": int,        # 每个关键词最多返回数
                "type": str,                    # "user" / "video"
                "platform": str,                # "tiktok" / "douyin"（默认 tiktok）
                "account": str,                 # 可选：指定登录账号 username
            }

        Returns:
            [{"tiktok_id", "username", "nickname", "bio", "follower_count", "platform", "source", "source_keyword"}, ...]
        """
        keywords = config.get("keywords", [])
        max_per_keyword = config.get("max_per_keyword", 20)
        platform_name = config.get("platform", "tiktok")

        pt = PlatformType.parse(platform_name)
        pf = get_platform(pt)

        if not keywords:
            logger.warning("keywords 为空，跳过关键词搜集")
            return []

        browser = require_browser_client(config, platform=pt.value)

        if config.get("acquisition_mode") is True:
            return await self._collect_acquisition(
                browser=browser,
                keywords=keywords,
                config=config,
                platform=pt,
                pf=pf,
            )

        all_users = []
        for kw in keywords:
            try:
                users = await self._search_one(browser, kw, max_per_keyword, pf, pt)
                for u in users:
                    u["platform"] = pt.value
                    u["source"] = "keyword_search"
                    u["source_keyword"] = kw
                all_users.extend(users)
                logger.info(f"[KeywordCollector:{pt.value}] '{kw}' 找到 {len(users)} 个用户")
            except Exception as e:
                logger.error(f"[KeywordCollector:{pt.value}] 搜索 '{kw}' 失败: {e}")
                continue

        return all_users

    async def _collect_acquisition(
        self,
        *,
        browser,
        keywords: list[str],
        config: dict,
        platform: PlatformType,
        pf,
    ) -> list[dict]:
        """Collect schema-valid candidates, with video/comment paths first."""
        budget_values = dict(config.get("budget") or {})
        allowed_budget_fields = {
            "max_keywords",
            "max_videos_per_keyword",
            "max_comments_per_video",
            "max_profiles",
            "max_total_observations",
            "max_author_videos",
            "max_pages",
            "max_duration_minutes",
            "max_llm_calls",
        }
        budget = ExplorationBudget.model_validate({
            key: value
            for key, value in budget_values.items()
            if key in allowed_budget_fields
        })
        tracker = ExplorationBudgetTracker(budget)
        max_per_keyword = int(config.get("max_per_keyword", budget.max_profiles))
        keyword_ids = {
            str(key): int(value)
            for key, value in dict(config.get("keyword_ids") or {}).items()
            if value is not None
        }
        merged: dict[str, dict] = {}
        collection_metrics = config.setdefault("collection_metrics", {})
        keyword_metrics = collection_metrics.setdefault("keywords", {})
        totals = collection_metrics.setdefault("totals", {
            "pages": 0,
            "llm_calls": 0,
            "duration_seconds": 0.0,
        })
        evidence_agent = config.get("evidence_agent")
        allow_dom_fallback = config.get("allow_dom_fallback") is True
        account_id = int(config.get("account_id") or 0)
        remaining_pages = budget.max_pages
        remaining_llm_calls = budget.max_llm_calls
        deadline = self._monotonic() + budget.max_duration_minutes * 60

        for keyword in keywords:
            if not tracker.allow_keyword(keyword):
                break
            remaining_seconds = deadline - self._monotonic()
            if remaining_pages <= 0:
                break
            if remaining_seconds <= 0:
                break
            keyword_id = keyword_ids.get(keyword)
            usage = {
                "pages": 0,
                "llm_calls": 0,
                "duration_seconds": 0.0,
            }
            exhaustion_reason = ""
            visited_urls: list[str] = []
            try:
                if evidence_agent is not None and remaining_llm_calls > 0:
                    keyword_budget = budget.model_copy(update={
                        "max_pages": remaining_pages,
                        "max_llm_calls": remaining_llm_calls,
                    })
                    observations = await evidence_agent.collect_keyword(
                        browser=browser,
                        keyword=keyword,
                        keyword_id=keyword_id,
                        platform=platform.value,
                        account_id=account_id,
                        budget=keyword_budget,
                        tracker=tracker,
                        max_duration_seconds=remaining_seconds,
                    )
                    primary = [
                        item.model_dump(mode="json")
                        if isinstance(item, CandidateObservation)
                        else CandidateObservation.model_validate(item).model_dump(
                            mode="json"
                        )
                        for item in observations
                    ]
                    raw_usage = getattr(evidence_agent, "last_budget_usage", None)
                    if not isinstance(raw_usage, dict) or not {
                        "pages", "llm_calls", "duration_seconds"
                    }.issubset(raw_usage):
                        raise ValueError("Hermes budget usage metrics are missing")
                    raw_pages = float(raw_usage["pages"])
                    raw_llm_calls = float(raw_usage["llm_calls"])
                    raw_duration = float(raw_usage["duration_seconds"])
                    if (
                        not all(math.isfinite(value) for value in (
                            raw_pages, raw_llm_calls, raw_duration
                        ))
                        or min(raw_pages, raw_llm_calls, raw_duration) < 0
                        or not raw_pages.is_integer()
                        or not raw_llm_calls.is_integer()
                    ):
                        raise ValueError("Hermes budget usage metrics are invalid")
                    usage = {
                        "pages": int(raw_pages),
                        "llm_calls": int(raw_llm_calls),
                        "duration_seconds": raw_duration,
                    }
                    exhaustion_reason = str(
                        getattr(evidence_agent, "last_exhaustion_reason", "")
                        or ""
                    )
                    raw_visited_urls = getattr(
                        evidence_agent, "last_visited_urls", None
                    )
                    if not isinstance(raw_visited_urls, (list, tuple)):
                        raise ValueError("Hermes visited URL metrics are missing")
                    visited_urls = list(raw_visited_urls)
                    if usage["pages"] > remaining_pages:
                        raise ValueError("Hermes exceeded remaining page budget")
                    if usage["llm_calls"] > remaining_llm_calls:
                        raise ValueError("Hermes exceeded remaining LLM budget")
                    if usage["duration_seconds"] > remaining_seconds + 1e-6:
                        raise ValueError("Hermes exceeded remaining duration budget")
                elif evidence_agent is not None:
                    primary = []
                    exhaustion_reason = "max_llm_calls"
                elif allow_dom_fallback:
                    primary = await self._search_video_comment_candidates(
                        browser,
                        keyword,
                        max_per_keyword,
                        pf,
                        platform,
                        tracker=tracker,
                        keyword_id=keyword_id,
                    )
                    usage["pages"] = min(
                        remaining_pages,
                        1 + len({
                            evidence.get("video_id")
                            for candidate in primary
                            for evidence in candidate.get("evidence", [])
                            if evidence.get("video_id")
                        }),
                    )
                else:
                    logger.warning(
                        "[KeywordCollector:%s] Hermes unavailable; bounded DOM "
                        "fallback disabled",
                        platform.value,
                    )
                    primary = []
                remaining_pages -= int(usage["pages"])
                remaining_llm_calls -= int(usage["llm_calls"])
                totals["pages"] += int(usage["pages"])
                totals["llm_calls"] += int(usage["llm_calls"])
                totals["duration_seconds"] += float(usage["duration_seconds"])

                if not exhaustion_reason:
                    if remaining_pages <= 0:
                        exhaustion_reason = "max_pages"
                    elif remaining_llm_calls <= 0:
                        exhaustion_reason = "max_llm_calls"
                    elif (
                        totals["duration_seconds"]
                        >= budget.max_duration_minutes * 60
                        or deadline <= self._monotonic()
                    ):
                        exhaustion_reason = "max_duration_minutes"
                # Observation-level budgets are attributed by BrowseAgent to the
                # exact affected user.  Do not overwrite that precision by
                # downgrading every candidate returned for the keyword.
                per_user_reasons = {
                    "max_profiles",
                    "max_total_observations",
                    "max_videos_per_keyword",
                    "max_comments_per_video",
                    "max_author_videos",
                }
                if exhaustion_reason and exhaustion_reason not in per_user_reasons:
                    primary = [
                        self._mark_candidate_truncated(
                            candidate, exhaustion_reason
                        )
                        for candidate in primary
                    ]
                self._merge_candidates(merged, primary)

                if (
                    exhaustion_reason in {"", "max_llm_calls"}
                    and not tracker.exhausted
                    and remaining_pages > 0
                    and deadline > self._monotonic()
                ):
                    direct_users = await self._search_one(
                        browser, keyword, max_per_keyword, pf, platform
                    )
                    remaining_pages -= 1
                    usage["pages"] += 1
                    totals["pages"] += 1
                    auxiliary = self._direct_user_candidates(
                        direct_users,
                        keyword=keyword,
                        keyword_id=keyword_id,
                        platform=platform,
                        pf=pf,
                        tracker=tracker,
                    )
                    if remaining_pages <= 0:
                        exhaustion_reason = "max_pages"
                        auxiliary = [
                            self._mark_candidate_truncated(
                                candidate, exhaustion_reason
                            )
                            for candidate in auxiliary
                        ]
                    elif deadline <= self._monotonic():
                        exhaustion_reason = "max_duration_minutes"
                        auxiliary = [
                            self._mark_candidate_truncated(
                                candidate, exhaustion_reason
                            )
                            for candidate in auxiliary
                        ]
                    self._merge_candidates(merged, auxiliary)
                self._record_keyword_metrics(
                    keyword_metrics,
                    keyword=keyword,
                    keyword_id=keyword_id,
                    candidates=list(merged.values()),
                    usage=usage,
                    exhaustion_reason=exhaustion_reason,
                    visited_urls=visited_urls,
                    platform=platform,
                )
            except Exception as exc:
                logger.error(
                    "[KeywordCollector:%s] 获客搜索 '%s' 失败: %s",
                    platform.value,
                    keyword,
                    exc,
                )

        # A normally exhausted production run still emits an authoritative
        # zero-usage record for every planned keyword. Collector errors above
        # intentionally remain missing so Pipeline validation fails closed.
        for keyword in keywords:
            keyword_id = keyword_ids.get(keyword)
            metric_key = str(keyword_id) if keyword_id is not None else keyword
            if metric_key in keyword_metrics:
                continue
            terminal_reason = ""
            if remaining_pages <= 0:
                terminal_reason = "max_pages"
            elif (
                totals["duration_seconds"] >= budget.max_duration_minutes * 60
                or deadline <= self._monotonic()
            ):
                terminal_reason = "max_duration_minutes"
            elif tracker.exhaustion_reasons:
                terminal_reason = tracker.exhaustion_reasons[0]
            if not terminal_reason:
                continue
            self._record_keyword_metrics(
                keyword_metrics,
                keyword=keyword,
                keyword_id=keyword_id,
                candidates=list(merged.values()),
                usage={"pages": 0, "llm_calls": 0, "duration_seconds": 0.0},
                exhaustion_reason=terminal_reason,
                visited_urls=[],
                platform=platform,
            )

        return [
            CandidateObservation.model_validate(candidate).model_dump(mode="json")
            for candidate in merged.values()
        ]

    @staticmethod
    def _mark_candidate_truncated(candidate: dict, reason: str) -> dict:
        updated = dict(candidate)
        reasons = set(updated.get("truncation_reasons") or ())
        reasons.add(reason)
        updated["truncation_reasons"] = sorted(reasons)
        updated["discovery_state"] = "needs_more_evidence"
        return updated

    @staticmethod
    def _record_keyword_metrics(
        target: dict,
        *,
        keyword: str,
        keyword_id: int | None,
        candidates: list[dict],
        usage: dict,
        exhaustion_reason: str,
        visited_urls: list[str],
        platform: PlatformType,
    ) -> None:
        evidence = [
            item
            for candidate in candidates
            for item in candidate.get("evidence", [])
            if item.get("keyword_id") == keyword_id
            or (
                item.get("keyword_id") is None
                and item.get("keyword_text") == keyword
            )
        ]
        videos = {item.get("video_id") for item in evidence if item.get("video_id")}
        videos.update(
            video_id
            for url in visited_urls
            if "/video/" in url
            and KeywordCollector._is_allowed_platform_url(url, platform)
            for video_id in (KeywordCollector._path_identity(url, "/video/"),)
            if video_id
        )
        relevant = {
            item.get("video_id")
            for item in evidence
            if item.get("video_id")
            and item.get("relevance_score") is not None
            and float(item["relevance_score"]) >= 0.5
        }
        pages = {
            value
            for item in evidence
            for value in (
                item.get("video_url"),
                item.get("comment_url"),
                item.get("author_url"),
            )
            if value
        }
        matched_candidates = {
            candidate.get("platform_user_id")
            for candidate in candidates
            if any(item in evidence for item in candidate.get("evidence", []))
        }
        reasons = {
            reason
            for candidate in candidates
            if any(item in evidence for item in candidate.get("evidence", []))
            for reason in candidate.get("truncation_reasons", [])
            if reason
        }
        if exhaustion_reason:
            reasons.add(exhaustion_reason)
        target[str(keyword_id) if keyword_id is not None else keyword] = {
            "videos_explored": len(videos),
            "explored_video_ids": sorted(videos),
            "relevant_videos": len(relevant),
            "candidate_count": len(matched_candidates),
            "pages": int(usage.get("pages", len(pages))),
            "llm_calls": int(usage.get("llm_calls", len(evidence))),
            "duration_minutes": float(usage.get("duration_seconds", 0.0)) / 60,
            "author_videos_explored": len({
                item.get("video_id")
                for item in evidence
                if item.get("source_type") == "profile" and item.get("video_id")
            }),
            "total_observations": len(evidence),
            "truncation_reasons": sorted(reasons),
        }

    @staticmethod
    def _merge_candidates(target: dict[str, dict], candidates: list[dict]) -> None:
        for candidate in candidates:
            key = str(candidate.get("platform_user_id") or "")
            if not key:
                continue
            existing = target.get(key)
            if existing is None:
                target[key] = candidate
                continue
            existing["evidence"].extend(candidate.get("evidence") or [])
            reasons = sorted(set(existing.get("truncation_reasons") or ()) | set(
                candidate.get("truncation_reasons") or ()
            ))
            if reasons:
                existing["truncation_reasons"] = reasons
                existing["discovery_state"] = "needs_more_evidence"
            for field in ("nickname", "bio", "follower_count"):
                if not existing.get(field) and candidate.get(field):
                    existing[field] = candidate[field]

    def _direct_user_candidates(
        self,
        users: list[dict],
        *,
        keyword: str,
        keyword_id: int | None,
        platform: PlatformType,
        pf,
        tracker: ExplorationBudgetTracker,
    ) -> list[dict]:
        candidates: list[dict] = []
        for user in users:
            raw_id = str(user.get("tiktok_id") or "")
            platform_user_id = (
                raw_id.split(":", 1)[1] if ":" in raw_id else raw_id
            )
            if not platform_user_id:
                continue
            username = str(user.get("username") or platform_user_id)
            author_url = pf.user_profile_url(platform_user_id)
            evidence = {
                "platform": platform.value,
                "platform_user_id": platform_user_id,
                "username": username,
                "source_type": "direct_user",
                "keyword_id": keyword_id,
                "keyword_text": keyword,
                "author_url": author_url,
                "raw_text": str(user.get("bio") or ""),
                "source_path": ["keyword", "direct_user"],
                "completeness_score": 0.5,
            }
            decision = tracker.consume_observation(
                EvidenceObservation.model_validate(evidence),
                current_keyword=keyword,
            )
            if not decision.accepted:
                continue
            candidate = {
                "platform": platform.value,
                "platform_user_id": platform_user_id,
                "username": username,
                "nickname": str(user.get("nickname") or ""),
                "bio": str(user.get("bio") or ""),
                "follower_count": max(0, int(user.get("follower_count") or 0)),
                "evidence": [evidence],
            }
            if decision.reached_reasons:
                candidate["discovery_state"] = "needs_more_evidence"
                candidate["truncation_reasons"] = list(decision.reached_reasons)
            candidates.append(candidate)
        return candidates

    async def _search_video_comment_candidates(
        self,
        browser,
        keyword: str,
        max_results: int,
        pf,
        platform: PlatformType,
        *,
        tracker: ExplorationBudgetTracker,
        keyword_id: int | None = None,
    ) -> list[dict]:
        """Discover video authors and comment authors within explicit budgets."""
        search_url = (
            f"https://www.tiktok.com/search/video?q={quote(keyword)}"
            if platform is PlatformType.TIKTOK
            else f"https://www.douyin.com/search/{quote(keyword)}?type=video"
        )
        await browser.navigate(search_url)
        await browser.wait(2500)
        await browser.scroll_down(500)
        await browser.wait(500)
        card_limit = min(max_results, tracker.budget.max_videos_per_keyword)
        cards = await self._query_all_limited(
            browser,
            pf.selectors.get("video_card", ""),
            card_limit,
        )

        video_rows: list[dict] = []
        for card in cards:
            video_url = await self._element_href(
                card, 'a[href*="/video/"]', base=search_url
            )
            if not self._is_allowed_platform_url(video_url, platform):
                continue
            video_id = self._path_identity(video_url, "/video/")
            if not video_id or not tracker.allow_video(keyword, video_id):
                continue
            author_link = await self._element_href(
                card, pf.selectors.get("author_link", "a[href]"), base=search_url
            )
            if author_link and not self._is_allowed_platform_url(
                author_link, platform
            ):
                author_link = ""
            author_id = self._author_identity(author_link, platform)
            text = await self._element_text(card)
            video_rows.append({
                "video_id": video_id,
                "video_url": video_url,
                "author_id": author_id,
                "author_url": author_link,
                "raw_text": text,
            })
            if len(video_rows) >= min(
                max_results, tracker.budget.max_videos_per_keyword
            ):
                break

        candidates: dict[str, dict] = {}
        for row in video_rows:
            author_id = row["author_id"]
            if author_id and tracker.allow_profile(author_id):
                self._append_candidate_evidence(
                    candidates,
                    platform=platform,
                    platform_user_id=author_id,
                    username=author_id,
                    evidence={
                        "platform": platform.value,
                        "platform_user_id": author_id,
                        "username": author_id,
                        "source_type": "video_author",
                        "keyword_id": keyword_id,
                        "keyword_text": keyword,
                        "video_id": row["video_id"],
                        "video_url": row["video_url"],
                        "author_url": row["author_url"],
                        "raw_text": row["raw_text"],
                        "source_path": ["keyword", "video", "author"],
                        "completeness_score": 0.6,
                    },
                )
            if tracker.exhausted:
                break
            await browser.navigate(row["video_url"])
            await browser.wait(1500)
            comment_selector = (
                '[data-e2e="comment-level-1"], [data-e2e="comment-item"], '
                'div[class*="comment-item"]'
            )
            comments = await self._query_all_limited(
                browser,
                comment_selector,
                tracker.budget.max_comments_per_video,
            )
            for index, comment in enumerate(comments):
                comment_id = await self._first_attribute(
                    comment, ("data-e2e-comment-id", "data-comment-id", "id")
                ) or f"{row['video_id']}:comment:{index}"
                if not tracker.allow_comment(row["video_id"], comment_id):
                    continue
                author_url = await self._element_href(
                    comment,
                    pf.selectors.get("author_link", "a[href]"),
                    base=row["video_url"],
                )
                if not self._is_allowed_platform_url(author_url, platform):
                    continue
                platform_user_id = self._author_identity(author_url, platform)
                if not platform_user_id or not tracker.allow_profile(platform_user_id):
                    continue
                raw_text = await self._element_text(comment)
                meta = await self._extract_user_meta(
                    comment, platform, fallback_id=platform_user_id
                )
                self._append_candidate_evidence(
                    candidates,
                    platform=platform,
                    platform_user_id=platform_user_id,
                    username=str(meta["username"]),
                    nickname=str(meta["nickname"]),
                    follower_count=int(meta["follower_count"]),
                    bio=str(meta["bio"]),
                    evidence={
                        "platform": platform.value,
                        "platform_user_id": platform_user_id,
                        "username": str(meta["username"]),
                        "source_type": "comment_author",
                        "keyword_id": keyword_id,
                        "keyword_text": keyword,
                        "video_id": row["video_id"],
                        "video_url": row["video_url"],
                        "comment_id": comment_id,
                        "comment_url": row["video_url"],
                        "author_url": author_url,
                        "raw_text": raw_text,
                        "source_path": ["keyword", "video", "comment", "author"],
                        "completeness_score": 0.7,
                    },
                )
                if tracker.exhausted:
                    break
        return list(candidates.values())

    @staticmethod
    def _append_candidate_evidence(
        candidates: dict[str, dict],
        *,
        platform: PlatformType,
        platform_user_id: str,
        username: str,
        evidence: dict,
        nickname: str = "",
        bio: str = "",
        follower_count: int = 0,
    ) -> None:
        candidate = candidates.setdefault(platform_user_id, {
            "platform": platform.value,
            "platform_user_id": platform_user_id,
            "username": username,
            "nickname": nickname,
            "bio": bio,
            "follower_count": follower_count,
            "evidence": [],
        })
        candidate["evidence"].append(evidence)

    @staticmethod
    async def _first_attribute(element, names: tuple[str, ...]) -> str:
        if not hasattr(element, "get_attribute"):
            return ""
        for name in names:
            value = await element.get_attribute(name)
            if value:
                return str(value)
        return ""

    async def _element_href(self, element, selector: str, *, base: str) -> str:
        href = await self._first_attribute(element, ("href",))
        if not href and selector and hasattr(element, "query_selector"):
            nested = await element.query_selector(selector)
            if nested is not None:
                href = await self._first_attribute(nested, ("href",))
        return urljoin(base, href) if href else ""

    @staticmethod
    async def _query_all_limited(browser, selector: str, limit: int) -> list:
        if not selector or limit <= 0:
            return []
        bounded_query = getattr(type(browser), "query_all_limited", None)
        if callable(bounded_query):
            return await browser.query_all_limited(selector, limit)
        # unittest/test adapters may attach the capability to the instance.
        bounded_query = vars(browser).get("query_all_limited")
        if callable(bounded_query):
            return await bounded_query(selector, limit)
        # Compatibility for small test fakes and legacy provider adapters only.
        return (await browser.query_all(selector))[:limit]

    @staticmethod
    def _is_allowed_platform_url(url: str, platform: PlatformType) -> bool:
        if not url:
            return False
        parsed = urlsplit(url.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return False
        host = parsed.hostname.lower()
        roots = (
            ("douyin.com", "iesdouyin.com")
            if platform is PlatformType.DOUYIN
            else ("tiktok.com",)
        )
        return any(host == root or host.endswith("." + root) for root in roots)

    @staticmethod
    async def _element_text(element) -> str:
        if not hasattr(element, "inner_text"):
            return ""
        try:
            return str(await element.inner_text())[:10000]
        except Exception:
            return ""

    @staticmethod
    def _path_identity(url: str, marker: str) -> str:
        path = urlsplit(url).path
        if marker not in path:
            return ""
        return path.split(marker, 1)[1].split("/", 1)[0]

    def _author_identity(self, url: str, platform: PlatformType) -> str:
        marker = "/user/" if platform is PlatformType.DOUYIN else "/@"
        return self._path_identity(url, marker)

    async def _search_one(
        self,
        browser,
        keyword: str,
        max_results: int,
        pf,
        platform: PlatformType,
    ) -> list[dict]:
        """搜索单个关键词（使用对应平台 URL）"""
        url = pf.search_user_url(keyword)
        await browser.navigate(url)
        await browser.wait(2500)

        for _ in range(3):
            await browser.scroll_down(500)
            await browser.wait(800)

        # 用平台对应的选择器
        card_sel = pf.selectors.get("user_card", "")
        link_sel = pf.selectors.get("user_link", "")

        cards = await self._query_all_limited(
            browser, card_sel, max_results
        ) if card_sel else []
        if not cards:
            cards = await self._query_all_limited(
                browser, link_sel, max_results
            ) if link_sel else []

        users = []
        seen = set()

        for card in cards:
            try:
                username = await self._extract_username(card, platform)
                if not username or username in seen:
                    continue
                seen.add(username)

                meta = await self._extract_user_meta(
                    card, platform, fallback_id=username
                )

                users.append({
                    "tiktok_id": f"{platform.value}:{username}",  # 复合主键避免跨平台冲突
                    "username": meta["username"],
                    "nickname": meta["nickname"],
                    "bio": meta["bio"],
                    "follower_count": meta["follower_count"],
                })
            except Exception:
                continue

        return users

    async def _extract_username(self, card, platform: PlatformType) -> str:
        """从卡片元素提取 stable key（双平台兼容）。

        抖音 web 搜索卡片 href 是 /user/<sec_uid>，不是 @handle。
        把 sec_uid 当 stable key 入 `tiktok_id`，username 字段留给
        `_extract_user_meta` 用 unique_id / 昵称填充。
        """
        # 通用：尝试链接
        href = await card.get_attribute("href") if hasattr(card, "get_attribute") else None
        if href:
            href = urljoin(get_platform(platform).home_url, href)
            if not self._is_allowed_platform_url(href, platform):
                return ""
            # TikTok: /@xxx
            # Douyin: /user/<sec_uid>
            for prefix in ("/@", "/user/"):
                if prefix in href:
                    return href.split(prefix)[1].split("?")[0].strip("/")

        # 兜底
        link_sel = get_platform(platform).selectors.get("user_link", "a[href]")
        link = await card.query_selector(link_sel)
        if link:
            href = await link.get_attribute("href")
            if href:
                href = urljoin(get_platform(platform).home_url, href)
                if not self._is_allowed_platform_url(href, platform):
                    return ""
                for prefix in ("/@", "/user/"):
                    if prefix in href:
                        return href.split(prefix)[1].split("?")[0].strip("/")
        return ""

    async def _extract_user_meta(
        self, card, platform: PlatformType, fallback_id: str
    ) -> dict[str, object]:
        """从搜索卡片 DOM 抽取可读字段。

        抖音搜索结果卡片 DOM 含昵称、粉丝数、签名；TikTok 同理。
        username = unique_id / @handle（缺失时回退 sec_uid，保持主键稳定）；
        nickname / follower_count / bio 由 DOM 直接给。
        """
        meta: dict[str, object] = {
            "username": fallback_id,
            "nickname": "",
            "follower_count": 0,
            "bio": "",
        }
        try:
            text = await card.inner_text() if hasattr(card, "inner_text") else ""
        except Exception:
            text = ""
        # 简单正则：TikTok / Douyin 卡片格式都形如
        #   "昵称\n@handle\n粉丝数 粉丝\n签名"
        # 取首行作为昵称；如出现 `@xxx` 作为 username；粉丝行作为 follower_count。
        lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
        if lines:
            meta["nickname"] = lines[0]
        import re

        handle_match = re.search(r"@([A-Za-z0-9._-]+)", text or "")
        if handle_match:
            meta["username"] = handle_match.group(1)
        # 粉丝：英文 / 中文 都匹配（"1.2K followers" / "1234 粉丝"）
        follower_match = re.search(
            r"([\d,\.]+)\s*([Kk万]?)\s*(?:粉丝|follower)",
            text or "",
        )
        if follower_match:
            raw = follower_match.group(1).replace(",", "")
            unit = follower_match.group(2).lower()
            try:
                value = float(raw)
                if unit in ("k",):
                    value *= 1_000
                elif unit == "万":
                    value *= 10_000
                meta["follower_count"] = int(value)
            except ValueError:
                pass
        # bio：剩余非空文本里的第一段
        bio_match = re.search(r"\n(.+)$", text or "")
        if bio_match and len(lines) > 2:
            meta["bio"] = lines[-1] if lines[-1] != meta["nickname"] else ""
        return meta
