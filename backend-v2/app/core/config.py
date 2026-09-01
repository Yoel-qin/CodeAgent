"""v2 配置。env_file=("../.env", ".env")：pydantic-settings v2 last-wins，
backend-v2/.env 排在末尾 = 最高优先级，覆盖根 .env 的旧系统值。"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=("../.env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"
    app_name: str = "CodeRAG-v2"
    log_level: str = "INFO"

    # 基础设施（变量名与旧系统一致，共用根 .env）
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "coderag_v2"  # ← 默认值也是 v2，双保险
    postgres_user: str = "coderag"
    postgres_password: str = "coderag"
    redis_url: str = "redis://localhost:6379/0"
    milvus_host: str = "localhost"
    milvus_port: int = 19530
    es_url: str = "http://localhost:9200"

    # Embedding（SiliconFlow BGE-M3）
    embedding_base_url: str = "https://api.siliconflow.cn/v1"
    embedding_api_key: str = ""
    embedding_model: str = "BAAI/bge-m3"

    # ES IK 分词
    es_ik_enabled: bool = False

    # LLM（三档路由 Plan 3 落地，M0 只做配置探活）
    llm_provider: str = "deepseek"
    llm_api_key: str = ""
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_model: str = "deepseek-chat"

    # 源码镜像（只读 git 工作树）与 MCP
    repos_root: str = "../data/repo"
    default_repo: str = "rocketmq"
    mcp_host: str = "127.0.0.1"
    mcp_code_port: int = 8110
    mcp_doc_port: int = 8111

    @property
    def postgres_dsn(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def postgres_dsn_sync(self) -> str:
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
