"""集中配置：从环境变量读取（docker-compose 注入，或本地 .env）。"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # 宿主机从 backend/ 运行时读 backend/.env 与项目根 .env；容器内由 compose 注入的环境变量优先
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- 应用 ----
    app_env: str = Field(default="development")
    app_name: str = Field(default="CodeRAG")
    log_level: str = Field(default="INFO")

    # ---- PostgreSQL ----
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "coderag"
    postgres_user: str = "coderag"
    postgres_password: str = "coderag"

    # ---- Redis ----
    redis_url: str = "redis://localhost:6379/0"

    # ---- MinIO ----
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "coderag"
    minio_secret_key: str = "coderag123"
    minio_secure: bool = False
    minio_bucket: str = "coderag"

    # ---- Milvus ----
    milvus_host: str = "localhost"
    milvus_port: int = 19530

    # ---- Elasticsearch ----
    es_url: str = "http://localhost:9200"

    # ---- LLM（默认走 API）----
    llm_provider: str = "deepseek"
    llm_api_key: str = ""
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_model: str = "deepseek-chat"

    # ---- 嵌入/重排部署形态 ----
    embedding_provider: str = "api"  # api | local
    model_server_url: str = "http://localhost:8100"
    # embedding API（OpenAI 兼容 /embeddings；默认硅基流动 BGE-M3，1024d）
    embedding_base_url: str = "https://api.siliconflow.cn/v1"
    embedding_api_key: str = ""
    embedding_model: str = "BAAI/bge-m3"

    # ---- 精排（reranker）----
    # reranker 走 OpenAI 兼容的 /rerank 端点（Cohere/SiliconFlow 风格）。
    # reranker_api_key 留空时复用 embedding_api_key（同一供应商同一 Key）。
    # 不配置 Key 或 rerank_enabled=False → 管道跳过精排，退回 RRF 排序。
    rerank_enabled: bool = True
    reranker_base_url: str = "https://api.siliconflow.cn/v1"
    reranker_api_key: str = ""
    # Stage 2 粗排模型；置空则跳过粗排（硅基流动无独立轻量 bge 粗排模型——
    # bge-reranker-base 不存在，只有 v2-m3——故默认单阶段精排）。
    # 若本地 model_server 提供独立的粗排模型，可在此填入走两阶段。
    reranker_coarse_model: str = ""
    reranker_fine_model: str = "BAAI/bge-reranker-v2-m3"    # Stage 3 精排；置空则跳过该阶段

    # ---- 检索默认参数（对齐设计 §11）----
    top_k_recall: int = 20       # 每路召回数量
    rerank_pool: int = 60        # RRF 融合后送入粗排的候选上限（设计 §11.4：60~80）
    top_k_coarse: int = 25       # Stage 2 粗排后保留（设计：20~30）
    top_k_fine: int = 8          # Stage 3 精排后保留 = 最终喂给 LLM（设计：5~10）
    rrf_k: int = 60              # RRF 常数 k

    @property
    def database_url(self) -> str:
        """异步 SQLAlchemy 连接串（asyncpg）。"""
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def database_url_sync(self) -> str:
        """同步连接串（Alembic 用，psycopg）。"""
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def is_dev(self) -> bool:
        return self.app_env.lower() in {"development", "dev", "local"}


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
