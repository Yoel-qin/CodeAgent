# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

CodeRAG — a RAG knowledge base over large Java codebases. Fuses semantic vectors + GNN graph vectors + 3-stage reranking + GraphRAG community summaries behind a FastAPI backend, a React frontend, and 9 planned LangGraph agents. Currently at **Phase 1**: an end-to-end minimal loop is working (parse → chunk → index → 4-path recall → streaming LLM → SSE → chat UI).

Repo comments and all root-level design docs are in **Chinese**. Code comments frequently reference design sections like "§10" (DDL) or "§11" (retrieval) — these point into `coderag后端设计方案.md` / `coderag前端设计方案 .md`.

## Run architecture (read this first — docs are partly stale)

There are **two run tiers**, and the README/Makefile predate the current split:

- **Docker = infrastructure only.** `docker-compose.yml` runs postgres / redis / minio / elasticsearch / etcd / milvus / attu. There is **no** `backend`, `frontend`, `nginx`, or `model_server` service in compose. All infra ports are mapped to `localhost` (PG 5432, Redis 6379, MinIO 9000, ES 9200, Milvus 19530).
- **App processes run locally on the host.** The split was made to cut Docker memory.

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

Missing API keys **degrade gracefully, they don't crash**: empty `LLM_API_KEY` → retrieval + citations still return, generation is skipped with a notice; empty `EMBEDDING_API_KEY` → vector recall path silently returns empty. Default providers: LLM = DeepSeek (httpx, OpenAI-compatible `/chat/completions`), embedding = SiliconFlow `BAAI/bge-m3` (1024-d, OpenAI-compatible `/embeddings`). No local GPU is used.

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

**Chat request flow** (`POST /v1/chat/completions`, SSE): `chat.py` → `chat_service.stream_chat` → `retrieval.pipeline.recall` (4-path merge) → `build_context` → `llm.stream_tokens` → emits SSE events `retrieval` → `citation`(s) → `token`(s) → `done`.

**Retrieval pipeline** (`retrieval/pipeline.py`) — four independent recall paths, each wrapped in its own try/except so a failing path degrades to empty rather than breaking the request:

1. **Vector** (`vector_search` → Milvus `coderag_vectors`, HNSW COSINE 1024-d) — needs embedding key.
2. **BM25** (`bm25_search` → Elasticsearch) — falls back to **PG lexical** (`lexical_search`) if ES returns nothing.
3. **Graph traversal** (`graph_traverse` → PG `call_graph`), seeded from the top code hits of paths 1+2.
4. (Stage-0 query understanding: jieba for Chinese + camelCase split, in `query_understanding`.)

Results are merged by `chunk_id` with weighted linear scoring (code boost, vector weight, graph bonus). **RRF is not yet implemented** — current scoring is a linear approximation; real RRF is a planned replacement. Recall meta returned in the `retrieval` SSE event shows which paths fired (`bm25`, `vector_on`, counts).

**PG is the source of truth**; Milvus (vectors) and ES (BM25) are derived indexes. The cross-store join key is `chunk_id` (a string PK on `code_chunks` / `doc_chunks`). `embedding_synced` on chunks flags whether a chunk has been pushed to Milvus.

## DB / Alembic patterns

- Models live in `app/db/models/{code,doc,graph,history,relation,system}.py`. **`app/db/models/__init__.py` imports every model** so Alembic's `target_metadata = Base.metadata` sees them — a new model file that isn't imported there is invisible to `alembic revision --autogenerate`.
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

## Where things are documented

- `docs/项目状态.md` — concise status snapshot (trust this over README for current state).
- `开发清单.md` — granular phase-by-phase progress ledger (the authoritative "what's done" list).
- `开发实施计划与方案.md` — implementation plan / phase breakdown.
- `待确认问题清单.md` — open design decisions.
- `coderag后端设计方案.md` / `coderag前端设计方案 .md` — full backend/frontend design (the §10 DDL, §11 retrieval referenced in code).
- `api接口清单.md` — API spec for all planned modules.
