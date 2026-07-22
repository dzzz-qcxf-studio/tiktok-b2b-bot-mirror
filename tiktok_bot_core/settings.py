"""Settings — Pydantic Settings 配置管理

所有配置从环境变量读取，绝不硬编码敏感信息。
可通过 .env 文件覆盖默认值。
"""

from pathlib import Path
from typing import Literal
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """全局配置"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # === LLM ===
    llm_provider: Literal["deepseek", "openai", "anthropic"] = "deepseek"
    llm_api_key: str = ""
    llm_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-v4-pro"
    llm_temperature: float = 0.7

    # === Telegram ===
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # === 存储 ===
    sqlite_url: str = ""  # 空则使用默认路径
    chroma_persist_dir: str = ""  # 空则使用默认路径

    # === TikTok 操作参数 ===
    tiktok_keywords: list[str] = Field(default_factory=lambda: ["wholesale", "importer", "distributor"])
    comment_interval_min: int = 3
    comment_interval_max: int = 10
    dm_interval_min: int = 5
    dm_interval_max: int = 15
    daily_comment_limit: int = 25
    daily_dm_limit: int = 12

    # === 浏览器 ===
    browser_headless: bool = True
    browser_user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )

    # === Pipeline ===
    pipeline_stages: list[str] = Field(default_factory=lambda: ["collect", "filter", "strategy", "outreach", "report"])

    @property
    def data_dir(self) -> Path:
        """数据目录"""
        d = Path(__file__).resolve().parents[2] / "data"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def reports_dir(self) -> Path:
        d = self.data_dir / "reports"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def logs_dir(self) -> Path:
        d = self.data_dir / "logs"
        d.mkdir(parents=True, exist_ok=True)
        return d


_settings: Settings | None = None


def get_settings() -> Settings:
    """获取全局配置单例"""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reload_settings() -> Settings:
    """重新加载配置（修改 .env 后调用）"""
    global _settings
    _settings = Settings()
    return _settings
