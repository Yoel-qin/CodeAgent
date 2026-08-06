# CodeRAG — 代码智能知识库 RAG 系统

融合 **可切换双编码器语义向量（unified BGE-M3 / dual CodeBERT+BGE-M3）+ BM25 + 图遍历（PG `call_graph`）+ 三阶段精排** 的代码/文档智能知识库，通过多 Agent 体系实现代码理解、文档问答、变更影响、缺陷诊断、代码审查、测试生成、文档维护、新人引导八大场景，支持增量更新与代码回滚。（GNN 图向量 / GraphRAG 社区摘要**已弃用**。）

> 设计文档见仓库根目录 `*.md`；实施计划见 [开发实施计划与方案.md](./开发实施计划与方案.md)。

## 技术栈

- **前端**：React 18 + TypeScript + Vite + Ant Design 5 + Zustand + React Router 6
- **后端 API**：FastAPI + Uvicorn + sse-starlette（SSE）
- **Agent**：LangGraph + langchain-core/openai/community
- **数据**：PostgreSQL 16（真相源）/ Milvus（向量）/ Elasticsearch（BM25）/ Redis 7 / MinIO
- **任务**：Celery + Redis
- **模型**：CodeBERT/UniXcoder + BGE-M3（嵌入）/ bge-reranker-base + v2-m3（重排）/ R-GCN（图嵌入）— 本地 GPU；LLM 走 API（DeepSeek/Qwen）

## 快速开始

### 前置要求

- Docker Desktop（WSL2 后端）+ NVIDIA Container Toolkit（GPU 可选，见下）
- Node 20+ / pnpm 9+（前端本地开发）
- Python 3.11+ / uv（后端本地开发）

### 1. 配置环境变量

```bash
cp backend/.env.example backend/.env     # 后端配置（DB/Redis/Milvus/ES/MinIO/LLM Key）
cp frontend/.env.example frontend/.env   # 前端配置（API 地址）
# 编辑 .env 填入 LLM API Key 等
```

### 2. 启动基础设施 + 应用

```bash
docker compose up -d                     # 拉起 PG/Redis/MinIO/ES/Milvus/Nginx + 后端 + 前端
# 或仅基础设施用于本地开发：
docker compose up -d postgres redis minio elasticsearch milvus
```

### 3. 数据库迁移

```bash
docker compose exec backend uv run alembic upgrade head
```

### 4. 访问

- 前端：http://localhost
- 后端 API 文档：http://localhost/api/docs
- 健康检查：http://localhost/api/health

## GPU（可选）

本地 GPU（RTX 4050 6GB）用于嵌入/重排/GNN。验证 Docker GPU 透传：

```bash
docker run --rm --gpus all ubuntu nvidia-smi   # 能看到 GPU 即透传正常
```

- 透传正常：`docker compose --profile gpu up -d` 启用 model_server（本地模型）。
- 不可用：默认 profile 下嵌入/重排走兼容 API，GNN 走 CPU/Node2Vec。

## 便捷命令（需 make）

```bash
make up            # 启动全部
make dev           # 仅基础设施（本地跑前后端）
make migrate       # 执行迁移
make logs          # 查看日志
make down          # 停止
```

## 目录结构

```
CodeRagAgent/
├── docker/          # 编排、Dockerfile、nginx、init-sql
├── backend/         # FastAPI 应用
├── model_server/    # 嵌入/重排/GNN 模型服务（GPU 可选）
├── frontend/        # React 应用
├── data/            # 持久化卷（gitignore）
└── *.md             # 设计文档 + 开发计划/清单/问题
```

## 开发进度

见 [开发清单.md](./开发清单.md)。当前阶段：**Phase 0 — 基础设施与脚手架**。
