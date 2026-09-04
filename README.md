# CodeRAG — 代码智能知识库（v2：Agentic + MCP）

**当前唯一后端是 `backend-v2/`**（方案二：Agentic 工具检索 + 文档向量库 + MCP 协议层，API :8010）；前端 `frontend-v2/`（:5174）。

检索范式与 v1 根本不同：**代码不切块不做向量**——Agent 经 MCP 只读工具（grep/glob/read_file/find_symbol + PG 调用图）精确导航源码；**文档走向量 + BM25 RRF 混检**（BGE-M3 / Milvus + ES）。Router 按意图分发 CodeNav / DocQA / WEB_SEARCH 等 ReAct Agent，DeepSeek 三档可配（routing/extraction/reasoning，可混部本地 vLLM）。

> **权威文档**：`backend-v2/README.md`（v2 运行与功能细节：MCP server、监控、评测、RBAC、WEB_SEARCH、冒烟矩阵）；`docs/`（gitignored）内的设计 spec 与 v1 里程碑台账（`项目状态.md` / `重构开发清单.md`）为历史参考。
>
> **v1 已退役（2026-09-03）**：`backend/`（传统 RAG 管线，M1–M46）与 `model_server/`（CodeBERT）已删除（git 历史可找回）；`frontend/` 为 v1 遗留暂保留，不在 v2 链路中。

## 技术栈（v2）

- **后端**：FastAPI + LangGraph（Agentic 编排）+ FastMCP（4 个工具 server：code :8110 / doc :8111 / graph :8112 / common :8113）
- **数据**：PostgreSQL 16（真相源）/ Milvus（文档向量）/ Elasticsearch（BM25）/ Redis（Stream 同步管道）/ MinIO
- **前端**：React 18 + TypeScript + Vite + Ant Design 5 + Zustand（chat / documents / graph / sync / monitor / eval 六页 + 登录）
- **模型**：DeepSeek（`MODEL_ROUTES` 三档路由，缺失 key 软降级不崩）

## 运行架构

基础设施跑 Docker（`docker-compose.yml` 7 服务：postgres / redis / minio / elasticsearch / etcd / milvus / attu），**应用进程跑主机本地**：

| 进程 | 端口 | 启动 |
|---|---|---|
| backend-v2 API | 8010 | `uvicorn app.main:app`（或 dev_up） |
| 4 个 MCP server | 8110–8113 | dev_up 拉起（或 `python -m app.mcp_servers.<name>_server`） |
| frontend-v2 | 5174 | `pnpm dev`（代理 /api → :8010） |

## 快速开始

```bash
# 1. 基础设施（等 7 服务 healthy）
docker compose up -d && docker compose ps

# 2. 后端（backend-v2/ 下）
cd backend-v2
uv sync --extra dev
cp .env.example .env                # 确保 POSTGRES_DB=coderag_v2；填 LLM_API_KEY / EMBEDDING_API_KEY
uv run python scripts/ensure_db.py  # 建库 + 防呆（连错库即拒）
uv run alembic upgrade head         # head = v2_0008
uv run python scripts/dev_up.py     # 一键拉起 backend + 四个 MCP server

# 3. 前端（另一终端，仓库根）
cd frontend-v2 && pnpm install && pnpm dev   # http://localhost:5174

# 4. 验证
curl http://localhost:8010/health   # 组件健康
uv run python scripts/smoke_mcp.py  # code-mcp 冒烟（退出码严格）
```

真实仓库入库（code / docs）与 smoke 矩阵（`smoke_doc` / `smoke_graph` / `smoke_agent` / `smoke_pipe`）、监控、评测、RBAC、WEB_SEARCH 各功能开关 → 见 **`backend-v2/README.md`**。

## Makefile

仅两类目标：基础设施 compose 代理（`up` / `dev` / `down` / `ps`）与 backend-v2 本地命令代理（`migrate` / `migrate-new` / `test` / `lint` / `dev-up`）。`make help` 查看全部。

## 目录结构

```
CodeRagAgent/
├── docker-compose.yml   # 基础设施编排（7 服务）
├── backend-v2/          # v2 后端（FastAPI + Agent + 4 MCP server + 管道 worker）
├── frontend-v2/         # v2 前端（React，:5174）
├── frontend/            # v1 前端遗留（退役，暂保留）
├── data/                # 持久化卷 + 本地模型（gitignored）
├── docs/                # 设计 spec + v1 里程碑台账（gitignored）
└── *.md                 # README / CLAUDE.md / AGENTS.md
```

## 里程碑（v2）

V2-M0 骨架 → M1 code-mcp → M2 文档链路 → M3 调用图 → M4 Agent 编排 → M5 同步管道 → M6 前端 → M7 可观测（trace）→ M8 评测 → M9 RBAC / WEB_SEARCH / 收官（**已全部完成**，2026-09）。CI（`.github/workflows/ci.yaml`）只测 backend-v2。
