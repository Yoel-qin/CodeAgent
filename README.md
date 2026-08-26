# CodeRAG — 代码智能知识库 RAG 系统

融合 **可切换双编码器语义向量（unified BGE-M3 / dual CodeBERT+BGE-M3）+ BM25 + 图遍历（PG `call_graph`）+ 三阶段精排** 的代码/文档智能知识库，通过多 Agent 体系实现代码理解、文档问答、变更影响、缺陷诊断、代码审查、测试生成、文档维护、联网检索八大场景，支持增量更新与代码回滚。（GNN 图向量 / GraphRAG 社区摘要**已弃用**。）

> **权威文档**：`docs/项目状态.md`（M1–M29）与 `docs/重构开发清单.md`（M30+ 重构台账）为按里程碑权威记录，README 与 `coderag*设计方案.md` 冲突时以 docs 为准。

## 技术栈

- **前端**：React 18 + TypeScript + Vite + Ant Design 5 + Zustand + React Router 6
- **后端 API**：FastAPI + Uvicorn + sse-starlette（SSE）
- **Agent**：LangGraph + langchain-core/openai/community
- **数据**：PostgreSQL 16（真相源）/ Milvus（向量）/ Elasticsearch（BM25）/ Redis 7 / MinIO
- **嵌入**：`EMBEDDING_STRATEGY=unified`（默认，代码+文档均走 SiliconFlow BGE-M3 API，零 GPU）/ `dual`（代码走本地 CodeBERT model_server + BGE-M3 镜像，文档走 BGE-M3 API，需 GPU）
- **模型**：DeepSeek（LLM）/ SiliconFlow bge-reranker-v2-m3（精排）/ CodeBERT（dual 模式代码嵌入，本地 GPU）

## 运行架构

系统分为**两层**：基础设施跑在 Docker，应用跑在本地主机。

- **Docker = 基础设施 only。** `docker-compose.yml` 拉起 7 个服务：postgres（5432）、redis（6379）、minio（9000）、elasticsearch（9200）、etcd、milvus（19530）、attu。**compose 中没有** backend / frontend / nginx / model_server 服务——这些全部跑在主机本地。
- **应用进程跑在主机本地。** 两个必备进程：**backend**（`backend/`，:8000）和 **frontend**（`frontend/`，:5173）。可选第三个进程——**model_server**（`model_server/`，:8100，FastAPI）——仅在 `EMBEDDING_STRATEGY=dual` 时需要，加载 CodeBERT 嵌入模型；默认 `unified` 模式不启动它。

> `README.md`、`Makefile`、`coderag*设计方案.md` 仍描述旧的全栈 Docker 布局——以 `docker-compose.yml` 和 `docs/` 为准。

## 快速开始

### 前置要求

- Docker Desktop（WSL2 后端）
- Node 20+ / pnpm 9+（前端本地开发）
- Python 3.11+ / uv（后端本地开发）
- 可选：NVIDIA GPU（仅 dual 嵌入模式 + 本地 vLLM 需要）

### 1. 配置环境变量

```bash
cp backend/.env.example backend/.env     # 后端配置（DB/Redis/Milvus/ES/MinIO/LLM Key）
cp frontend/.env.example frontend/.env   # 前端配置（API 地址）
# 编辑 .env 填入 LLM API Key 等（缺失 key 降级不崩）
```

### 2. 启动基础设施

```bash
docker compose up -d                     # 拉起 PG/Redis/MinIO/ES/Etcd/Milvus/Attu
docker compose ps                       # 等待全部 7 个服务 healthy
```

### 3. 数据库迁移

```bash
cd backend && uv run alembic upgrade head
```

### 4. 启动应用

```bash
# 后端（终端 1）
cd backend && uv run uvicorn app.main:app --reload --port 8000
# 验证：curl http://localhost:8000/health（5 组件健康检查）

# 前端（终端 2）
cd frontend && pnpm dev                # http://localhost:5173（代理 /api -> :8000）

# 可选：model_server（终端 3，仅 dual 模式）
cd model_server && EMBED_MODEL=D:/project/CodeRagAgent/data/models/codebert-base uv run uvicorn main:app --port 8100
```

### 5. 访问

- 前端：http://localhost:5173
- 后端 API 文档：http://localhost:8000/docs
- 健康检查：http://localhost:8000/health

## Makefile 说明

Makefile 是旧全栈 Docker 布局的遗留产物，**仅以下目标仍有效**：

```bash
make up            # docker compose up -d（基础设施）
make dev           # 同上
make down          # docker compose down
make ps            # docker compose ps
```

其余目标（`migrate`、`test`、`lint`、`ingest`、`backend-shell`、`restart`、`logs`）均执行 `docker compose exec backend …`，因 compose 中不再有 backend 服务而**失效**。这些操作需在 `backend/` 目录下用 `uv run` 本地执行：

```bash
cd backend
uv run pytest -q                      # 测试
uv run ruff check .                    # lint
cd backend && uv run alembic upgrade head   # 迁移
```

## 目录结构

```
CodeRagAgent/
├── docker-compose.yml  # 基础设施编排（7 服务）
├── backend/            # FastAPI 应用（API/服务/检索/Agent/eval）
├── model_server/       # CodeBERT 嵌入服务（仅 dual 模式，可选 GPU）
├── frontend/           # React 应用
├── domain_packs/       # 领域知识包（RocketMQ 首包）
├── data/               # 持久化卷（gitignore）
├── docs/               # 设计文档 + 里程碑台账（gitignore）
└── *.md                # README / 设计方案 / CLAUDE.md / AGENTS.md
```

## 开发进度与里程碑

系统已完成 M1–M46 共 46 个里程碑，覆盖：

- **核心 RAG 管线**（M1–M9）：解析 → 分块 → 向量/BM25/图 4 路召回 → RRF 融合 → 3 阶段精排 → 流式 LLM → SSE → 聊天 UI
- **文档维护弧线**（M15–M21）：腐化检测 → LLM 重写 → 人工审批 → 写回 → git PR
- **Agent 层**（M33–M35）：AgentRegistry + CitationEnforcer + 多 Agent 协作
- **领域包**（M36–M38）：通用底座 + 可插拔 RocketMQ 包（链路追踪/故障诊断/性能调优）
- **评估体系**（M23/M27–M29/M39–M40）：检索 Recall/MRR/NDCG + A/B 消融 + QA/幻觉 LLMJudge + 诊断 eval + CI 回归
- **工程能力**（M22/M26/M41–M43）：监控、⌘K 全局搜索、Trace 全链路、CostController、Redis 缓存、反馈闭环
- **模型 + RBAC**（M44–M45）：三档 ModelRouter + User/Role 鉴权 + 检索过滤
- **数据地基**（M46）：RocketMQ 4.9.8 全量入库 + 跨类调用图 14801 边
- **检索增强**（M31–M32）：ES IK 分词、注释增强、图多跳、交叉链接第 5 路（均 opt-in，默认 off）

> 详细进度见 `docs/项目状态.md`（M1–M29）与 `docs/重构开发清单.md`（M30+）。
