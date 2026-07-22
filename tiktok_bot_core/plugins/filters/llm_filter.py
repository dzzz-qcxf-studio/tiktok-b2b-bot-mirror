"""LLM 筛选插件 — 用大模型判断用户是否符合 B2B 外贸目标"""

import logging
import json
import re

from tiktok_bot_core.extensions.registry import FilterPlugin
from tiktok_bot_core.models.entities import User
from tiktok_bot_core.llm.client import get_llm_client

logger = logging.getLogger(__name__)


# Prompt 模板：可被运营人员调优
DEFAULT_PROMPT = """分析以下 TikTok 用户，判断是否为 B2B 外贸潜在客户。

用户信息：
- 用户名: @{username}
- 昵称: {nickname}
- 简介: {bio}
- 粉丝数: {follower_count}
- 分类: {category}

B2B 外贸潜在客户的特征包括但不限于：
- bio 中出现 importer / wholesaler / distributor / retailer / brand / supplier 等关键词
- 视频内容与目标行业相关
- 有明显的商业属性（店铺、联系方式）

请严格返回以下 JSON（不要解释）：
{{
    "is_potential": true/false,
    "confidence": 0.0-1.0,
    "category": "buyer/distributor/manufacturer/competitor/irrelevant",
    "reason": "判断理由（一句话）"
}}"""


class LLMFilter(FilterPlugin):
    """LLM 智能筛选"""

    name = "llm"

    def __init__(self, prompt_template: str | None = None):
        self.prompt_template = prompt_template or DEFAULT_PROMPT

    async def evaluate(self, user: User, config: dict) -> dict:
        """评估单个用户

        Returns:
            {"score": 0-1, "category": str, "reason": str, "is_potential": bool}
        """
        try:
            prompt = self.prompt_template.format(
                username=user.username,
                nickname=user.nickname or "",
                bio=user.bio or "",
                follower_count=user.follower_count,
                category=user.category or "unknown",
            )
            llm = get_llm_client()
            result = await llm.json_completion(prompt)
            return {
                "score": float(result.get("confidence", 0)),
                "category": result.get("category", "unknown"),
                "reason": result.get("reason", ""),
                "is_potential": bool(result.get("is_potential", False)),
            }
        except Exception as e:
            logger.error(f"[LLMFilter] 评估 @{user.username} 失败: {e}")
            return {
                "score": 0.0,
                "category": "unknown",
                "reason": f"LLM 错误: {e}",
                "is_potential": False,
            }
