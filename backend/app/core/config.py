"""集中配置：从环境变量读取（docker-compose 注入，或本地 .env）。"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field, model_validator
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

    # ---- 嵌入策略（可切换双框架，见 docs/嵌入向量方案.md）----
    # unified: 代码与文档统一用上面的 embedding API(BGE-M3 1024d)，单 collection + kind 过滤。
    # dual   : 方案一——代码/注释用 CodeBERT(768d, 本地 model_server)、文档用 BGE-M3(1024d API)，
    #          双路召回后由统一 reranker(Stage3) 重排（屏蔽两嵌入空间分数不可比）。
    embedding_strategy: str = "unified"  # unified | dual
    # 框架二（dual）代码侧 CodeBERT（model_server 提供 /embeddings，768d）：
    code_embedding_enabled: bool = True
    code_embedding_model: str = "microsoft/codebert-base"
    code_embedding_dim: int = 768
    # M25：dual 模式是否给代码额外建 BGE-M3 镜像索引（code_vectors_bge）并双路检索。
    # 修 dual 向量对中文 NL 代码查询召回弱（CodeBERT 无中文）——BGE-M3 多语言，能找回中文 query 对应的代码。
    # 开 → ingest 代码双写 + 查询侧用 BGE-M3 向量额外检索 code_vectors_bge；关 → 回退 M24（仅 CodeBERT 代码路）。
    # 默认开：未 reindex 时 code_vectors_bge 为空 → 检索返空 no-op，故上线即安全，reindex 后自动生效。
    dual_code_bgem3_enabled: bool = True

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

    # ---- Agent 编排（Phase 7 地基）----
    # rag_engine：检索流水线执行引擎。legacy=现有 stream_chat（默认，零行为变更）；
    #             langgraph=走 app/agent 的 StateGraph（query_analysis→retrieve→generate→post_process）。
    # langgraph_checkpoint：LangGraph checkpointer 实现。memory=进程内 MemorySaver（仅单 worker dev）；
    #             postgres=AsyncPostgresSaver（断点续跑，跨重启存活；setup() 自建四表；M8 已落地）。
    #             仅 rag_engine=langgraph 时生效；Windows 需 SelectorEventLoop（见 main.py 顶部 + CLAUDE.md 平台坑段）。
    rag_engine: str = "legacy"          # legacy | langgraph
    langgraph_checkpoint: str = "memory"  # memory | postgres
    # 自动 Agent（create_react_agent）工具调用轮上限；recursion_limit = 该值*2+3。
    # 默认 6：DeepSeek 倾向多轮（搜+读多段+拉佐证），4 偏紧会触超限兜底；超限仍触发 _degrade 兜底。
    agent_max_iterations: int = 6
    # 跨轮对话记忆：携带的先前消息条数（user/assistant 各算一条）；0=禁用（每轮无记忆，同旧行为）。
    # 历史 来源=chat_messages（真相源），非 checkpointer——见 chat_service.load_conversation_history。
    conversation_history_turns: int = 6

    # M34 CitationEnforcer（回答幻觉校验，opt-in；默认 off = 零行为变更）
    citation_enforce_enabled: bool = False
    citation_enforce_min_unverified: int = 1
    citation_enforce_max_listed: int = 10

    # M35 多 Agent 协作（LangGraph 三层诊断 DAG + WorkingMemory，opt-in；默认 off = 零行为变更）
    multi_agent_collab_enabled: bool = False
    collab_max_llm_calls: int = 9        # 单次协作总 LLM ainvoke 上限（≈3 层 × 3 轮）
    collab_max_tool_calls: int = 12      # 单次协作总工具调用上限
    collab_max_rounds_per_layer: int = 3 # 每层 tool-cycling 轮数硬界

    # ---- 联网 MCP 工具（让场景 Agent 调用远程/在线 MCP server）----
    # 仅 rag_engine=langgraph 生效（新增 WEB_SEARCH 场景 Agent）。mcp_servers 为 JSON 数组字符串：
    # [{"name":"...","url":"http://host:port/sse","transport":"sse|streamable_http"}]。
    # 关闭/空/连接失败 → web 意图回落 KB retrieve，后端不崩（与缺 API key 同一套降级惯例）。
    mcp_enabled: bool = False
    mcp_servers: str = ""

    # M36 领域知识包（DomainPackRegistry）：domain_packs_dir 为扫描目录（相对 backend/ 运行目录）；
    # domain_pack_default_repo 为会话 target_repo 未绑定时的回落仓库标识。
    # 无 opt-in 开关——机制默认就绪，空目录即 no-op（无包激活=零行为变更）。
    domain_packs_dir: str = "domain_packs"
    domain_pack_default_repo: str = ""   # 空 → resolve 时回落 settings.repo_path

    # ---- 入库 / 向量化补偿 ----
    # embedding_client / milvus_client 均无批处理（整列表一次请求），在 pipeline.indexing 层切片。
    embed_batch_size: int = 32            # embedding + Milvus upsert 批大小
    ingest_resync_enabled: bool = False   # lifespan 内是否跑 embedding_synced 补偿后台循环（dev 默认关）
    ingest_resync_interval_seconds: int = 3600  # 补偿循环间隔（秒）
    ingest_resync_batch_limit: int = 500  # 每次补偿扫描的 chunk 上限（避免长事务）
    # M20 eager 重嵌入：approve 写回 doc_chunks 后即时重嵌入该 chunk 并 upsert Milvus（向量检索立即可见）。
    # 失败/无密钥 → 留 embedding_synced=false，由上面 resync 补偿兜底。opt-out（关→回退纯懒模式）。
    eager_reembed_enabled: bool = True

    # ---- 仓库 / Git 增量同步（§13 增量更新 + §18 回滚）----
    # 后端/CLI/API 默认指向的仓库（相对 backend/ 运行目录）；CLI/API 仍可显式覆盖。
    repo_path: str = "../data/repo/sample"
    git_default_branch: str = "main"
    git_poll_interval_seconds: int = 300        # 预留：定时轮询间隔（本期不启用 cron）
    sync_full_fallback_on_no_cursor: bool = True   # 无 COMPLETED 同步游标时自动回退 FULL
    sync_rebuild_relations_on_full: bool = True    # FULL 同步时是否 build_all（全局重建关联/调用图）

    # ---- 运营加固：检查点清理 / HITL 超时（M14）----
    # 后台维护循环（lifespan 内，仅 rag_engine=langgraph 启用；dev 默认关，仿 ingest_resync）。
    # 每轮：① 过期超时未 resume 的 interrupted 消息 → status='expired'（postgres 模式顺带清其 thread checkpoint）；
    #       ② 清理「最新 checkpoint 早于保留期」的整个 thread（防 checkpoints 三表无限膨胀）。
    # 保留期应远大于超时（默认 30d ≫ 24h），故待审批 interrupt 早已过期、不会误删活 thread。
    maintenance_enabled: bool = False            # 总闸
    maintenance_interval_seconds: int = 3600     # 维护循环间隔（秒）
    hitl_interrupt_timeout_hours: int = 24       # >0 启用中断超时过期；0=禁用
    checkpoint_retention_days: int = 30          # >0 启用整 thread 清理；0=禁用（仅 postgres 检查点生效）

    # ---- 文档维护：PR 生成（M15）----
    # DOC_MAINTAIN 审批通过后，apply 节点据代码 LLM 重写过时段落 → 工件写回 MinIO（本前缀）+
    # 落 doc_update_proposals 表（status=PENDING_PUSH）。**仅产出 PR 载荷，不执行真实 git**（扩展点）。
    doc_update_artifact_prefix: str = "doc-updates"
    # ---- 真实 git/PR 落地（M21）----
    # approve 写回 KB 后 post-commit best-effort：读磁盘文档 → original→rewritten 替换 → 隔离 worktree
    # 建分支+提交 →（可选）推送 → 回填 commit_sha/pr_url、状态翻 PUSHED/COMMITTED（失败→PUSH_FAILED，KB 已写回）。
    # 经隔离 git worktree，不变异主工作区。推送（outward-facing）默认关——只产本地分支+提交（可逆：删分支），
    # 需显式 opt-in + 可达 remote。关 doc_git_enabled → 回退纯 KB 写回（pre-M21 行为）。
    doc_git_enabled: bool = True                # 总闸（approve 是否执行 git）
    doc_git_push_enabled: bool = False          # 推送 opt-in（仅本地分支+提交为安全默认）
    doc_git_remote: str = "origin"              # 推送目标 remote 名
    doc_git_author_name: str = "CodeRAG"        # commit 身份（-c 注入，不依赖全局 git config）
    doc_git_author_email: str = "noreply@coderag.local"

    # ---- 主动腐化巡检（M16）----
    # 后台巡检循环（lifespan 内；仅 staleness_sweep_enabled 启用，**不要求 rag_engine=langgraph**——
    # 巡检纯 PG，legacy 模式亦可用，不同于 M14 维护循环）。
    # 每轮：枚举非过时 DOC↔CODE 关系，据 change_history 启发式判定（代码侧 MODIFIED/DELETED 且晚于
    # 关系 updated_at）→ 自动标 is_stale=True（stale_reason="SWEEP:..."，仿 soft_delete 的非-HITL 标记）。
    # 重写仍走 M15 HITL；GET /v1/staleness/report 暴露结果。
    staleness_sweep_enabled: bool = False          # 总闸
    staleness_sweep_interval_seconds: int = 7200   # 巡检间隔（秒）
    staleness_sweep_batch_size: int = 200          # 每轮最多扫描/标记的关系数
    # ---- SWEEP 批量重写（M17）----
    # POST /v1/staleness/sweep-rewrite：为 top-N SWEEP 标记的过时 doc 批量生成重写提案（复用 M15
    # generate_doc_update/create_doc_pr，落 doc_update_proposals PENDING 行＝审批队列），再逐项
    # approve→APPROVED / reject→REJECTED（状态翻转；真写回 + 真实 git 延后）。顺序执行 N 次
    # （async LLM + 同步 MinIO），故 cap 低、N 放大会阻塞事件循环。
    sweep_rewrite_top_n_default: int = 10          # 默认处理关系数
    sweep_rewrite_top_n_max: int = 50              # 硬上限

    @model_validator(mode="after")
    def _resolve_domain_pack_default_repo(self) -> Settings:
        """空 domain_pack_default_repo 回落 repo_path（spec §10：默认同 repo_path）。"""
        if not self.domain_pack_default_repo:
            self.domain_pack_default_repo = self.repo_path
        return self

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
    def postgres_dsn(self) -> str:
        """原生 psycopg DSN（无 SQLAlchemy 驱动前缀）。

        供 langgraph AsyncPostgresSaver / psycopg_pool——它们用的是裸 ``postgresql://``，
        不能复用上面的 ``database_url_sync``（其 ``+psycopg`` 是 SQLAlchemy 专用前缀）。
        """
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def is_dev(self) -> bool:
        return self.app_env.lower() in {"development", "dev", "local"}


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
