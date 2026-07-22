"""向量存储 — ChromaDB 语义搜索/经验记忆"""

from pathlib import Path
from typing import Optional
import chromadb


class VectorStore:
    """ChromaDB 向量数据库

    三个集合：
    - user_profiles: 用户画像向量
    - message_strategies: 话术效果向量
    - experience_memory: 经验记忆向量（环节 6 沉淀）
    """

    def __init__(self, persist_dir: str | Path | None = None):
        if persist_dir is None:
            base = Path(__file__).resolve().parents[3]
            persist_dir = base / "data" / "chroma_db"
        persist_dir = Path(persist_dir)
        persist_dir.mkdir(parents=True, exist_ok=True)

        self.client = chromadb.PersistentClient(path=str(persist_dir))
        self.user_profiles = self._get_or_create(
            "user_profiles",
            "用户画像向量（bio + 视频描述的 embedding）",
        )
        self.message_strategies = self._get_or_create(
            "message_strategies",
            "话术效果向量（评论/私信 + 效果数据）",
        )
        self.experience_memory = self._get_or_create(
            "experience_memory",
            "经验记忆向量（环节 6 沉淀的经验总结）",
        )

    def _get_or_create(self, name: str, description: str):
        return self.client.get_or_create_collection(
            name=name,
            metadata={"hnsw:space": "cosine", "description": description},
        )

    # ===== User Profiles =====

    def add_user_profile(self, user_id: str, document: str, metadata: dict | None = None) -> None:
        self.user_profiles.add(
            ids=[user_id],
            documents=[document],
            metadatas=[metadata or {}],
        )

    def search_similar_users(self, query: str, n_results: int = 5) -> dict:
        return self.user_profiles.query(query_texts=[query], n_results=n_results)

    # ===== Message Strategies =====

    def add_strategy_embedding(self, strategy_id: str, documents: list[str], metadata: dict | None = None) -> None:
        self.message_strategies.add(
            ids=[strategy_id],
            documents=documents,
            metadatas=[metadata or {}],
        )

    def search_similar_strategies(self, query: str, n_results: int = 5) -> dict:
        return self.message_strategies.query(query_texts=[query], n_results=n_results)

    # ===== Experience Memory =====

    def add_experience(self, exp_id: str, document: str, metadata: dict | None = None) -> None:
        self.experience_memory.add(
            ids=[exp_id],
            documents=[document],
            metadatas=[metadata or {}],
        )

    def search_experience(self, query: str, n_results: int = 5) -> dict:
        return self.experience_memory.query(query_texts=[query], n_results=n_results)

    # ===== 统计 =====

    def stats(self) -> dict:
        return {
            "user_profiles": self.user_profiles.count(),
            "message_strategies": self.message_strategies.count(),
            "experience_memory": self.experience_memory.count(),
        }
