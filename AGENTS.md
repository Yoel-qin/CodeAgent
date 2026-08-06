# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## What this is

CodeRAG — a RAG knowledge base over large Java codebases. Fuses a **switchable dual-encoder embedding** (unified BGE-M3, or dual: CodeBERT for code + BGE-M3 for docs, per `docs/嵌入向量方案.md` 方案一) + 3-stage reranking, behind a FastAPI backend, a React frontend, and 9 planned LangGraph agents. **Graph *vectors* (GNN / path C) have been dropped**; graph *traversal* (PG `call_graph` BFS) is kept. Currently at **Phase 1**: an end-to-end minimal loop is working (parse → chunk → index → 3-path recall → streaming LLM → SSE → chat UI).

Repo comments and all root-level design docs are in **Chinese**. Code comments frequently reference design sections like "§10" (DDL) or "§11" (retrieval) — these point into `coderag后端设计方案.md` / `coderag前端设计方案 .md`.

## Run architecture (read this first — docs are partly stale)

There are **two run tiers**, and the README/Makefile predate the current split:

- **Docker = infrastructure only.** `docker-compose.yml` runs postgres / redis / minio / elasticsearch / etcd / milvus / attu. There is **no** `backend`, `frontend`, `nginx`, or `model_server` service in compose. All infra ports are mapped to `localhost` (PG 5432, Redis 6379, MinIO 9000, ES 9200, Milvus 19530).
- **App processes run locally on the host.** The split was made to cut Docker memory. Two local processes: the **backend** (`backend/`, :8000) and **frontend** (`frontend/`, :5173). An optional third — **`model_server/`** (:8100, FastAPI) — loads **CodeBERT** (`microsoft/codebert-base`, 768-d, via `transformers`) and is called by the backend **only in `EMBEDDING_STRATEGY=dual` mode** to embed code/comments. Default `unified` mode never starts it. Needs torch+transformers; GPU preferred, CPU fallback.

Consequence: **the Makefile's app-level targets are stale** — `make migrate|test|lint|ingest|backend-shell|restart|logs` all do `docker compose exec backend …`, which fails because that service no longer exists. Only `make up|dev|down|ps` still work. For everything else, run locally inside `backend/` (commands below).

The root `*.md` design docs (`README.md`, `Makefile`, `coderag*设计方案.md`) still describe the old full-stack Docker layout — trust `docs/项目状态.md` and `docker-compose.yml` over them when they conflict.

## Common commands

```bash
# Infrastructure (one-time + on every reboot)
docker compose up -d
docker compose ps                      # wait for all 7 services healthy

# Backend (from repo root)
cd backend && uv run uvicorn app.main:app --reload --port 8000
curl http://localhost:8000/health      # 5-component health: PG/Redis/Milvus/ES/LLM-config

# Frontend (separate shell)
cd frontend && pnpm dev                # http://localhost:5173  (proxies /api -> :8000)
cd frontend && pnpm typecheck          # tsc --noEmit
cd frontend && pnpm build              # tsc -b && vite build

# model_server (CodeBERT) — ONLY needed for EMBEDDING_STRATEGY=dual; default unified ignores it
cd model_server && EMBED_MODEL=D:/project/CodeRagAgent/data/models/codebert-base uv run uvicorn main:app --port 8100   # GPU 默认（MODEL_DEVICE=cuda；无 CUDA 时自动回退 cpu）
curl http://localhost:8100/health      # {"status":"ok","dim":768,"device":"cuda"}

# Migrations (must run from backend/ — alembic.ini prepend_sys_path=.)
cd backend && uv run alembic upgrade head
cd backend && uv run alembic revision --autogenerate -m "msg"

# Tests / lint (from backend/)
cd backend && uv run pytest -q
cd backend && uv run pytest tests/path/test_file.py::test_name -q   # single test
cd backend && uv run ruff check .

# Ingest a Java repo into PG (from backend/)
uv run python scripts/ingest_code.py --repo ../data/repo/sample --module demo
uv run python scripts/ingest_docs.py  --repo ../data/repo/sample     # markdown
# Smoketest external providers:
uv run python scripts/llm_ping.py && uv run python scripts/embedding_ping.py
```

### Config / secrets

`app/core/config.py` `Settings` reads `env_file=(".env", "../.env")`, so running from `backend/` picks up **both** `backend/.env` and the **root `.env`** (root `.env` is the single source — it also feeds compose). `.env` is gitignored; `.env.example` files are the templates.

Missing API keys **degrade gracefully, they don't crash**: empty `LLM_API_KEY` → retrieval + citations still return, generation is skipped with a notice; empty `EMBEDDING_API_KEY` → doc/vector recall path silently returns empty; no reranker key (`RERANKER_API_KEY` falls back to `EMBEDDING_API_KEY`) → Stage 2/3 rerank skipped, results stay in RRF order.

**Embedding is a switchable dual framework** (`EMBEDDING_STRATEGY`, default `unified`): `unified` = code+docs both via SiliconFlow `BAAI/bge-m3` (1024-d, OpenAI-compatible `/embeddings`); `dual` = 方案一 — code via local `model_server` **CodeBERT** (768-d), docs via BGE-M3 API, dual Milvus collections, merged by the Stage-3 reranker. LLM = DeepSeek; reranker = SiliconFlow `BAAI/bge-reranker-v2-m3` (Cohere-style `/rerank`). Default `unified` needs no GPU/local model; `dual` requires the `model_server` running (down → code vector path returns empty, retrieval degrades to BM25+graph-traverse).

## Backend architecture

Layered FastAPI app under `backend/app/`:

| Layer | Location | Responsibility |
|---|---|---|
| API routers | `api/v1/` | HTTP + SSE endpoints, mounted via `api/v1/router.py` |
| Schemas | `schemas/` | Pydantic request/response models |
| Services | `services/` | Orchestration (e.g. `chat_service.stream_chat`) |
| Retrieval | `retrieval/` | 4-path recall + merge scoring |
| Pipeline | `pipeline/` | Ingest: parse → chunk → metadata → relations |
| Clients | `clients/` | External I/O: `llm_client`, `embedding_client`, `es_client`, `milvus_client` |
| DB | `db/` | SQLAlchemy 2.0 async engine/session + ORM models |
| Core | `core/` | `config.Settings`, `logging` |

**Chat request flow** (`POST /v1/chat/completions`, SSE): `chat.py` → `chat_service.stream_chat` → `retrieval.pipeline.recall` (4-path recall → RRF → rerank) → `build_context` → `llm.stream_tokens` → emits SSE events `conversation` → `retrieval` → `citation`(s) → `token`(s) → `done`. Each turn is persisted: a `Conversation` (auto-created on first message, title = first query) + `ChatMessage`(s) (user + assistant) + a `RetrievalLog` holding the full recall/rerank funnel, linked from the assistant message.

**Companion REST endpoints** (also under `/v1/chat`, in `conversations.py`): `GET /conversations` + `/conversations/{id}` (list / detail-with-messages), `GET /messages/{id}/retrieval` (replays the persisted funnel as stage1/stage2/stage3), `POST /suggestions` (LLM follow-up questions), `POST /messages/{id}/feedback` (`HELPFUL`/`NOT_HELPFUL`, written onto the `RetrievalLog` to collect Phase 8 LTR data).

**Retrieval pipeline** (`retrieval/pipeline.py`) — three independent recall paths, each wrapped in its own try/except so a failing path degrades to empty rather than breaking the request:

1. **Vector** (`vector_search`): `unified` → query embedded once (BGE-M3) → Milvus `coderag_vectors` (HNSW COSINE 1024-d, kind filter); `dual` → query embedded by **both** CodeBERT (→ `code_vectors` 768-d) and BGE-M3 (→ `doc_vectors` 1024-d), two hit lists merged. Needs embedding key (and, in `dual`, the `model_server`).
2. **BM25** (`bm25_search` → Elasticsearch) — falls back to **PG lexical** (`lexical_search`) if ES returns nothing.
3. **Graph traversal** (`graph_traverse` → PG `call_graph` BFS), seeded from the top code hits of paths 1+2. (Graph *vectors* / path C have been dropped.)

**Stage 0** (`query_understanding`): rule-based term extraction (jieba for Chinese + camelCase split) **+ an LLM query rewrite** (`rewrite_query`: semantic rewrite + extra keywords). If the LLM is unconfigured/fails, it degrades to the original query — never breaks the path. The rewritten query feeds vector+BM25; merged terms feed lexical.

The recall lists flow through a **3-stage ranking funnel**, each stage independently try/except'd:

- **Stage 1 — RRF fusion** (`fusion.rrf_fuse`, k=60; weights: vector 1.0 / lexical 0.8 / graph 1.2). De-dups by `chunk_id`, re-scores by reciprocal rank. *(Graph-vector weight removed; `graph` = path-D traversal.)*
- **Stage 2 — coarse rerank** (optional; only if `RERANKER_COARSE_MODEL` is set — empty by default).
- **Stage 3 — fine rerank** (`RERANKER_FINE_MODEL` = `BAAI/bge-reranker-v2-m3`, via `clients/reranker_client`). In `dual` mode this is 方案一's **unified reranker bridge** — it re-scores the merged code+doc candidates by text, masking the two embedding-space score scales. No reranker key → stays in RRF order (方案二).

The `retrieval` SSE event carries the full funnel as meta: `recall` per-path counts, `rrf_pool`, `coarse`/`fine` counts, `rerank_on`, `rewritten`, `embedding_strategy`, `recall_ms`/`rerank_ms` (plus legacy aliases). Persisted to `retrieval_logs` and replayed by `GET /v1/chat/messages/{id}/retrieval`.

**PG is the source of truth**; Milvus (vectors) and ES (BM25) are derived indexes. The cross-store join key is `chunk_id` (a string PK on `code_chunks` / `doc_chunks`). `embedding_synced` on chunks flags whether a chunk has been pushed to Milvus.

## DB / Alembic patterns

- Models live in `app/db/models/{chat,code,doc,graph,history,relation,system}.py` (`chat.py` = `Conversation` + `ChatMessage`). **`app/db/models/__init__.py` imports every model** so Alembic's `target_metadata = Base.metadata` sees them — a new model file that isn't imported there is invisible to `alembic revision --autogenerate`.
- Naming convention in `app/db/base.py` enforces `idx_/uk_/fk_/pk_` prefixes — match it in any hand-written DDL.
- Alembic runs **synchronous** via `database_url_sync` (psycopg); the app runs **async** via `database_url` (asyncpg). Same DB, two drivers.
- `scripts/ingest_*.py` use a **sync** `Session` (not the async one) — they're CLI tools, not request-scoped.

## Frontend architecture

React 18 + TS + Vite + AntD 5 + Zustand + React Router 6 (`frontend/src/`): `api/client.ts` (axios) + `api/sse.ts` (`@microsoft/fetch-event-source`) → `hooks/useChat.ts` → `pages/ChatPage.tsx` + `components/chat/CitationCard.tsx`, state in `stores/app.ts`. **Vite proxy rewrites `/api` → `` (stripped)**, so the backend sees `/v1/...`, not `/api/v1/...`. Local dev talks to `:8000` directly; only the (now-unused) containerized nginx layout would prefix `/api`.

## Platform gotchas (hard-won, all active)

- **Pin `elasticsearch>=8.11,<9`** — `elasticsearch-py` 9.x is incompatible with the ES 8.11 server (Accept-version error). Already pinned in `backend/pyproject.toml`; don't bump it.
- **Milvus must read MinIO's root creds.** `docker-compose.yml` sets `MINIO_ACCESS_KEY_ID/SECRET` to match `MINIO_ROOT_USER/PASSWORD`. Mismatch → Milvus crashloop.
- **`asyncio.to_thread` / thread-wrapped sync calls can't take keyword-only args** (e.g. `top_k=`). They raise `TypeError`, which the recall-layer try/except **silently swallows** — a recall path then looks "empty" with no error. Pass such args **positionally** (see `es_client.search` / `milvus_client.search`).
- **Chinese Windows = GBK locale.** `configparser`/`alembic` read configs with the locale encoding, so `alembic.ini` is kept **ASCII-only**. Scripts that print Chinese/emoji need `sys.stdout.reconfigure(encoding="utf-8")`, or set `PYTHONUTF8=1`. Python *source* files (.py) with Chinese are fine (interpreter reads UTF-8).
- **pip mirror**: `backend/pyproject.toml` uses the Tsinghua PyPI mirror by default (`tool.uv.index`) for CN network speed.
- **HuggingFace is blocked; `huggingface_hub` rejects the mirror.** `huggingface.co` is unreachable on CN networks. `hf-mirror.com` works via `curl -L`, but recent `huggingface_hub` raises `FileMetadataError` on the mirror's redirect. Workaround: `curl -sSL https://hf-mirror.com/<repo>/resolve/main/<file>` each file into `data/models/<repo>/`, then load with `from_pretrained('/abs/local/path')` (set `EMBED_MODEL` to that path for `model_server`). CodeBERT needs: `config.json vocab.json merges.txt special_tokens_map.json tokenizer_config.json pytorch_model.bin`. The `data/models/`, `data/hf/`, `data/uv_cache/`, `model_server/.venv/` dirs are gitignored.
- **`.env` `MODEL_SERVER_URL` must be `http://localhost:8100`** (model_server runs on the host, not as a docker service). It was previously `http://model_server:8100` (stale docker hostname) — that returns 503.
- **psycopg async + uvicorn on Windows (only matters for `RAG_ENGINE=langgraph LANGGRAPH_CHECKPOINT=postgres`)** — `AsyncPostgresSaver` needs a `SelectorEventLoop`, but **uvicorn 0.51 hard-codes `ProactorEventLoop` on win32**: `Config.get_loop_factory()` returns `asyncio.ProactorEventLoop` directly and `Server.run()` passes it to `asyncio.run(..., loop_factory=...)`, **bypassing the event-loop policy entirely** — so `asyncio.set_event_loop_policy(WindowsSelectorEventLoopPolicy())` has *no effect* under uvicorn. Symptom at startup: `psycopg.InterfaceError: Psycopg cannot use the 'ProactorEventLoop' to run in async mode`. Fix (in `app/main.py`, guarded to opt-in): when postgres checkpoint is enabled on win32, patch `uvicorn.config.Config.get_loop_factory` to return `asyncio.SelectorEventLoop` (module import of `app.main` runs before `server.run()` reads the factory, so the patch takes). Also: build the saver via `AsyncPostgresSaver.from_conn_string(dsn)` (it configures autocommit) — a bare `AsyncConnectionPool` defaults to autocommit=False and `setup()`'s `CREATE INDEX CONCURRENTLY` throws `ActiveSqlTransaction`. The DSN must be a bare `postgresql://` (`settings.postgres_dsn`), not `database_url_sync`'s `postgresql+psycopg://`. Linux prod needs none of this (uvicorn uses epoll/Selector by default).

## Where things are documented

- `docs/项目状态.md` — concise status snapshot (trust this over README for current state).
- `docs/开发清单.md` — granular phase-by-phase progress ledger (the authoritative "what's done" list).
- `docs/开发实施计划与方案.md` — implementation plan / phase breakdown.
- `docs/待确认问题清单.md` — confirmed/open design decisions.
- `docs/嵌入向量方案.md` — the dual-encoder 方案一 analysis (落地说明 at top maps to the `EMBEDDING_STRATEGY` switch).
- `docs/coderag后端设计方案.md` / `docs/coderag前端设计方案 .md` — full backend/frontend design (the §10 DDL, §11 retrieval referenced in code); both carry a 2026-07-27 change banner at top.
- `docs/api接口清单.md` — API spec for all planned modules.
