"""复合筛选器 — 多个 Filter 组合（AND 关系，全部通过才算通过）"""

import logging

from tiktok_bot_core.extensions.registry import FilterPlugin
from tiktok_bot_core.models.entities import User

logger = logging.getLogger(__name__)


# 商业属性关键词
COMMERCIAL_KEYWORDS = [
    "importer", "wholesaler", "distributor", "retailer",
    "brand", "supplier", "manufacturer", "factory",
    "export", "import", "trade", "supply", "supply chain",
    "店铺", "批发", "代理", "分销", "厂家", "工厂",
]


class KeywordPreFilter(FilterPlugin):
    """基于 bio 关键词的快速预筛"""

    name = "keyword_pre"

    async def evaluate(self, user: User, config: dict) -> dict:
        """bio 命中商业关键词则视为潜在

        Returns:
            {"score": 0-1, "category": str, "reason": str, "is_potential": bool}
        """
        bio = (user.bio or "").lower()
        username = (user.username or "").lower()
        text = f"{bio} {username}"

        hit_keywords = [kw for kw in COMMERCIAL_KEYWORDS if kw.lower() in text]

        return {
            "score": 1.0 if hit_keywords else 0.0,
            "category": "unknown",
            "reason": f"命中: {','.join(hit_keywords)}" if hit_keywords else "未命中商业关键词",
            "is_potential": bool(hit_keywords),
        }


class CompositeFilter(FilterPlugin):
    """组合预筛 + LLM 筛选（先 key 过滤掉明显无关，再交给 LLM）"""

    name = "composite"

    def __init__(self):
        self._pre = KeywordPreFilter()

    async def evaluate(self, user: User, config: dict) -> dict:
        """先预筛，命中才送给 LLM

        这样可大幅减少 LLM 调用次数，节省成本。
        """
        pre = await self._pre.evaluate(user, config)
        if not pre["is_potential"]:
            # 预筛未命中，直接淘汰
            return {
                **pre,
                "category": "irrelevant",
                "reason": f"[预筛淘汰] {pre['reason']}",
            }
        # 预筛命中 -> 走 LLM（由 Pipeline 编排时决定，这里仅返回预筛结果）
        # 实际的多 Filter 组合由 Pipeline 层处理
        return pre
