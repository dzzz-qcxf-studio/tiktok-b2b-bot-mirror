"""LLM 客户端 — 抽象多种 LLM 提供商"""

import json
import logging
import re
from typing import Any

from openai import AsyncOpenAI

from tiktok_bot_core.settings import get_settings

logger = logging.getLogger(__name__)


class LLMClient:
    """LLM 客户端

    通过 OpenAI 兼容协议访问 DeepSeek/OpenAI/Anthropic 等。
    所有 LLM 调用经由此处，方便统一管理 prompt、限流、日志。
    """

    def __init__(self):
        s = get_settings()
        self.client = AsyncOpenAI(
            api_key=s.llm_api_key,
            base_url=s.llm_base_url,
        )
        self.model = s.llm_model
        self.temperature = s.llm_temperature

    async def chat(self, prompt: str, system: str = "你是一个 B2B 外贸业务拓展专家。") -> str:
        """普通文本 completion"""
        try:
            resp = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                temperature=self.temperature,
            )
            return resp.choices[0].message.content
        except Exception as e:
            logger.error(f"LLM 调用失败: {e}")
            raise

    async def json_completion(self, prompt: str, system: str | None = None) -> dict[str, Any]:
        """期望返回 JSON 的 completion

        自动从 markdown code block 提取 JSON。
        """
        sys_msg = system or "你是一个 B2B 外贸业务拓展专家。请严格返回 JSON 格式。"
        raw = await self.chat(prompt, sys_msg)
        return self._extract_json(raw)

    @staticmethod
    def _extract_json(text: str) -> dict:
        """从 LLM 输出提取 JSON

        支持：
        - 纯 JSON
        - 包裹在 ```json ... ``` 中
        - 混合在自然语言中（提取第一个 {...} 块）
        """
        text = text.strip()

        # 1. 直接尝试解析
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # 2. 去除 markdown code block
        if text.startswith("```"):
            # 去掉首尾 ``` 和可能的语言标识
            lines = text.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines).strip()
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                pass

        # 3. 提取第一个 {...} 块
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass

        raise ValueError(f"无法从 LLM 输出提取 JSON: {text[:200]}")


_client: LLMClient | None = None


def get_llm_client() -> LLMClient:
    global _client
    if _client is None:
        _client = LLMClient()
    return _client
