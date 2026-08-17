# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> **`AGENTS.md` is the Codex-targeted twin of this file and must stay in lockstep.** When you change architecture, run commands, or platform gotchas, update both.

## What this is

CodeRAG — a RAG knowledge base over large Java codebases. Fuses a **switchable dual-encoder embedding** (unified BGE-M3, or dual: CodeBERT for code + BGE-M3 for docs, per `docs/嵌入向量方案.md` 方案一) + BM25 + PG graph traversal + 3-stage reranking, behind a FastAPI backend, a React frontend, and a **LangGraph multi-agent layer** (7 read-only scenario agents — incl. a **WEB_SEARCH** 联网检索 agent backed by remote MCP servers — + **3 pack-driven domain agents** [链路追踪/故障诊断/性能调优, activated by a matching **domain knowledge pack**] + 1 write-action HITL agent). **Graph *vectors* (GNN / path C) and GraphRAG have been dropped**; graph *traversal* (PG `call_graph` BFS) is kept.

Progress is well past the Phase-1 minimal loop (parse → chunk → index → 3-path recall → streaming LLM → SSE → chat UI). As of milestone **M40** the system also has: incremental sync + git rollback, multimodal docs (PDF/Word/txt + structured tables + OCR'd images), a knowledge-graph viewer, the full agent layer with cross-restart checkpointing + HITL, a **document self-healing arc** (detect stale doc↔code anchors → LLM-rewrite → human-approve → write back to PG/MinIO + re-embed + open a git PR), system monitoring, retrieval evaluation (CLI + API + UI), a ⌘K global search, a **pluggable domain-pack layer** (M36–M38) — the system is now a "generic base + domain packs" architecture (a **RocketMQ** first pack supplies trace templates / diagnosis trees / tuning rules / a config-key whitelist), and a **QA/幻觉 eval dimension** (M39) — a generic `LLMJudge` scores *system-generated answers* (the prior eval measured only retrieval recall) on faithfulness / answer-relevance / citation-accuracy / hallucination, plus an unverified-citation rate via the M34 `enforcer`. Newest addition: **诊断 eval + CI 回归门 (M40)** — the same `LLMJudge` scores system-generated *diagnosis* answers on a 4-dim domain rubric (root-cause / code-ref / config-advice / reasoning, per-query weights), gated against the repo-committed `backend/eval/baseline_diag.json` snapshot (any metric degrading > 0.05 → exit 1) by the repo's **first GitHub Actions workflow** (`.github/workflows/eval.yaml`: a deterministic `ci` job [pytest + ruff, zero infra/keys] on every push/PR + a manual `eval-manual` job that stands up infra, ingests RocketMQ 4.9.8, and runs the diag eval vs baseline). `docs/项目状态.md` (M1–M29) and `docs/重构开发清单.md` (the **M30+ refactor ledger**) are the authoritative per-milestone ledgers — trust them over the README.

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

# Retrieval evaluation / A-B ablation / dual-code BGE-M3 mirror (from backend/)
uv run python scripts/eval_retrieval.py --validate                          # check eval-set anchors resolve (no retrieval)
uv run python scripts/eval_retrieval.py --top-k 10 --rewrite off --json     # Recall@K/MRR/NDCG over the real funnel
uv run python scripts/ab_eval.py --pairs rerank multipath_rrf graph         # A/B deltas vs AblationConfig variants
uv run python scripts/reindex_code_bge.py                                  # (dual only) populate the code_vectors_bge mirror

# Diagnosis eval + baseline regression gate (M40; needs RocketMQ ingested + LLM key)
uv run python scripts/diag_eval.py                    # run + persist + compare vs eval/baseline_diag.json (regression → exit 1)
uv run python scripts/diag_eval.py --update-baseline  # refresh the baseline snapshot from this run
```

### Config / secrets

`app/core/config.py` `Settings` reads `env_file=(".env", "../.env")`, so running from `backend/` picks up **both** `backend/.env` and the **root `.env`** (root `.env` is the single source — it also feeds compose). `.env` is gitignored; `.env.example` files are the templates.

Missing API keys **degrade gracefully, they don't crash**: empty `LLM_API_KEY` → retrieval + citations still return, generation is skipped with a notice; empty `EMBEDDING_API_KEY` → doc/vector recall path silently returns empty; no reranker key (`RERANKER_API_KEY` falls back to `EMBEDDING_API_KEY`) → Stage 2/3 rerank skipped, results stay in RRF order.

**Embedding is a switchable dual framework** (`EMBEDDING_STRATEGY`, default `unified`): `unified` = code+docs both via SiliconFlow `BAAI/bge-m3` (1024-d, OpenAI-compatible `/embeddings`); `dual` = 方案一 — code via local `model_server` **CodeBERT** (768-d), docs via BGE-M3 API, dual Milvus collections, merged by the Stage-3 reranker. LLM = DeepSeek; reranker = SiliconFlow `BAAI/bge-reranker-v2-m3` (Cohere-style `/rerank`). Default `unified` needs no GPU/local model; `dual` requires the `model_server` running (down → code vector path returns empty, retrieval degrades to BM25+graph-traverse).

**Agent / runtime behavior switches** (all default to the safe/no-op state; none crash when unset):
- `RAG_ENGINE` = `legacy` (default) | `langgraph`. `legacy` = the original retrieve→generate path. `langgraph` = the StateGraph with intent routing → scenario agents. The default `legacy` means **zero behavior change** unless you opt in — and the SSE event contract is identical either way.
- `LANGGRAPH_CHECKPOINT` = `memory` (default) | `postgres`. `postgres` uses `AsyncPostgresSaver` so thread state (`thread_id = conversation_id`) survives process restart — required for HITL resume across restarts. On win32 this triggers the uvicorn loop-factory patch in `app/main.py` (see Platform gotchas).
- `CONVERSATION_HISTORY_TURNS` (default 6, `0` disables) — prior turns loaded from `chat_messages` and injected into the LLM so "它/刚才那个" resolves. History source is `chat_messages`, not the checkpointer.
- `DUAL_CODE_BGEM3_ENABLED` (default on, `dual` only) — the M25 **BGE-M3 code mirror index** `code_vectors_bge` that fixes CodeBERT's Chinese-NL blindness; off → reverts to the pre-M25 behavior.
- `MCP_ENABLED` (default off) + `MCP_SERVERS` (JSON-array string: `[{"name":"...","url":"http://host:port/sse","transport":"sse|streamable_http"}]`) — powers the **WEB_SEARCH** agent by connecting to remote/online MCP servers (web search/fetch) at lifespan startup via `langchain-mcp-adapters` `MultiServerMCPClient`. Off/empty/unreachable → the `web` intent falls back to KB `retrieve` (no dead-end; the backend never crashes on MCP — same opt-in/soft-fail contract as a missing API key).
- `CITATION_ENFORCE_ENABLED` (default off) + `CITATION_ENFORCE_MIN_UNVERIFIED` (1) + `CITATION_ENFORCE_MAX_LISTED` (10) — M34 answer-hallucination check: annotates code identifiers in the LLM answer not verified against retrieved citations (notice appended as a `token` event + metrics into `retrieval_meta.enforcement`). Off = zero behavior change; pure-fn, never breaks the request.
- `MULTI_AGENT_COLLAB_ENABLED` (default off) + `COLLAB_MAX_LLM_CALLS` (9) + `COLLAB_MAX_TOOL_CALLS` (12) + `COLLAB_MAX_ROUNDS_PER_LAYER` (3) — M35 multi-agent collaboration: when on + `needs_collab`, routes complex-diagnosis queries to the 3-layer `collab` subgraph (`diagnose→verify→refine`). Off (default) → `router` never returns `"collab"` → zero behavior change.
- Document-self-healing / ops toggles (all default *off* or safe in dev): `MAINTENANCE_ENABLED`, `HITL_INTERRUPT_TIMEOUT_HOURS`, `CHECKPOINT_RETENTION_DAYS`, `STALENESS_SWEEP_ENABLED`+`_INTERVAL_SECONDS`, `SWEEP_REWRITE_*`, `EAGER_REEMBED_ENABLED` (default on), `DOC_GIT_ENABLED`/`DOC_GIT_PUSH_ENABLED` (real git PR landing is opt-in).
- Domain knowledge packs (`app/domain_packs/`, M36–M38): **no boolean opt-in switch** — the mechanism is inherently opt-in (empty `DOMAIN_PACKS_DIR` / no repo match = no activation = zero behavior change). `DOMAIN_PACKS_DIR` (default `"domain_packs"`, relative to the `backend/` run dir) is the scan dir; `DOMAIN_PACK_DEFAULT_REPO` (default `""`) is the fallback repo identifier when a conversation has no `target_repo` (else it falls back to `REPO_PATH`). Packs load once at lifespan startup; a malformed pack is logged + skipped, never crashes startup.

## Backend architecture

Layered FastAPI app under `backend/app/`:

| Layer | Location | Responsibility |
|---|---|---|
| API routers | `api/v1/` | HTTP + SSE endpoints, mounted via `api/v1/router.py` (11 routers: chat, conversations, sync, documents, resources, graph, agents, staleness, monitor, search, eval) |
| Schemas | `schemas/` | Pydantic request/response models |
| Services | `services/` | Orchestration: `chat_service`, plus `sync`/`document`/`graph`/`monitor`/`search`/`eval_run`/`staleness_sweep`/`sweep_rewrite`/`doc_maintenance`/`doc_pr`/`maintenance`/`agent_stats` |
| Retrieval | `retrieval/` | 4-path recall + merge scoring; `ablation.py` (eval hook) |
| Pipeline | `pipeline/` | Ingest: parse → chunk → metadata → relations; sync (git) + rollback |
| Agent | `agent/` | LangGraph StateGraph: intent routing → scenario agents; tools; streaming bridge; checkpointer |
| Eval | `eval/` | Pure-fn IR metrics + `run_eval`/`run_ab` over the real funnel (cross-cutting, read-only) |
| Clients | `clients/` | External I/O: `llm_client`, `embedding_client`, `es_client`, `milvus_client`, `minio_client`, `reranker_client`, `mcp_client` (remote MCP servers → WEB_SEARCH agent) |
| DB | `db/` | SQLAlchemy 2.0 async engine/session + ORM models |
| Core | `core/` | `config.Settings`, `logging` |

**Chat request flow** (`POST /v1/chat/completions`, SSE): `chat.py` → `chat_service.stream_chat` → `retrieval.pipeline.recall` (4-path recall → RRF → rerank) → `build_context` → `llm.stream_tokens` → emits SSE events `conversation` → `retrieval` → `citation`(s) → `token`(s) → `done`. Each turn is persisted: a `Conversation` (auto-created on first message, title = first query) + `ChatMessage`(s) (user + assistant) + a `RetrievalLog` holding the full recall/rerank funnel, linked from the assistant message. Under `RAG_ENGINE=langgraph`, `stream_chat` instead dispatches to the agent graph (see Agent layer) but persists the same rows.

**Companion REST endpoints** (also under `/v1/chat`, in `conversations.py`): `GET /conversations` + `/conversations/{id}` (list / detail-with-messages), `GET /messages/{id}/retrieval` (replays the persisted funnel as stage1/stage2/stage3), `POST /suggestions` (LLM follow-up questions), `POST /messages/{id}/feedback` (`HELPFUL`/`NOT_HELPFUL`, written onto the `RetrievalLog` to collect Phase 8 LTR data).

**Retrieval pipeline** (`retrieval/pipeline.py`) — three independent recall paths, each wrapped in its own try/except so a failing path degrades to empty rather than breaking the request:

1. **Vector** (`vector_search`): `unified` → query embedded once (BGE-M3) → Milvus `coderag_vectors` (HNSW COSINE 1024-d, kind filter); `dual` → query embedded by **both** CodeBERT (→ `code_vectors` 768-d) and BGE-M3 (→ `doc_vectors` 1024-d), two hit lists merged. In `dual` the already-computed BGE-M3 query vector is **also** searched against a code mirror collection `code_vectors_bge` (M25; gated by `DUAL_CODE_BGEM3_ENABLED`, default on) — this fixes CodeBERT's Chinese-NL blindness, which otherwise left vector-only Recall@10 at ~0.11. Needs embedding key (and, in `dual`, the `model_server`).
2. **BM25** (`bm25_search` → Elasticsearch) — falls back to **PG lexical** (`lexical_search`) if ES returns nothing.
3. **Graph traversal** (`graph_traverse` → PG `call_graph` BFS), seeded from the top code hits of paths 1+2. (Graph *vectors* / path C have been dropped.)

**Stage 0** (`query_understanding`): rule-based term extraction (jieba for Chinese + camelCase split) **+ an LLM query rewrite** (`rewrite_query`: semantic rewrite + extra keywords). If the LLM is unconfigured/fails, it degrades to the original query — never breaks the path. The rewritten query feeds vector+BM25; merged terms feed lexical.

The recall lists flow through a **3-stage ranking funnel**, each stage independently try/except'd:

- **Stage 1 — RRF fusion** (`fusion.rrf_fuse`, k=60; weights: vector 1.0 / lexical 0.8 / graph 1.2). De-dups by `chunk_id`, re-scores by reciprocal rank. *(Graph-vector weight removed; `graph` = path-D traversal.)*
- **Stage 2 — coarse rerank** (optional; only if `RERANKER_COARSE_MODEL` is set — empty by default).
- **Stage 3 — fine rerank** (`RERANKER_FINE_MODEL` = `BAAI/bge-reranker-v2-m3`, via `clients/reranker_client`). In `dual` mode this is 方案一's **unified reranker bridge** — it re-scores the merged code+doc candidates by text, masking the two embedding-space score scales. No reranker key → stays in RRF order (方案二).

The `retrieval` SSE event carries the full funnel as meta: `recall` per-path counts, `rrf_pool`, `coarse`/`fine` counts, `rerank_on`, `rewritten`, `embedding_strategy`, `recall_ms`/`rerank_ms` (plus legacy aliases). Persisted to `retrieval_logs` and replayed by `GET /v1/chat/messages/{id}/retrieval`.

**Ablation hook**: `recall(..., ablation=AblationConfig(...))` (default `None` ≡ all-on ≡ production) can independently disable each of the 4 stages (`vector`/`lexical`/`graph`/`rerank`; see `retrieval/ablation.py`). This is the seam the eval subsystem injects through (`run_eval`'s `recall_fn` DI) to measure each stage's contribution — it is dead code in every production call site.

**PG is the source of truth**; Milvus (vectors) and ES (BM25) are derived indexes. The cross-store join key is `chunk_id` (a string PK on `code_chunks` / `doc_chunks`). `embedding_synced` on chunks flags whether a chunk has been pushed to Milvus.

## Agent layer (Phase 7) — `app/agent/`

A LangGraph StateGraph (`agent/graph.py`) gated behind `RAG_ENGINE` (`legacy` default | `langgraph`). Under `langgraph`, `chat_service.stream_chat` dispatches to `agent.streaming.stream_graph` instead of the legacy retrieve→generate path; the SSE event contract is **identical** (`conversation → retrieval → citation → token → done`, plus `interrupt` for HITL), so the frontend doesn't care which engine ran. `graph.py` is compiled once with a checkpointer from `agent/memory/checkpointer.py`.

**Graph topology**: `query_analysis` (Stage-0 rewrite + LLM intent classify: code/doc/graph/bug/review/test/web/mixed/chitchat (+ trace/diagnose/tune **only when a domain pack is active**, M37), rule fallback if no key; **M35**: also produces `needs_collab`) → `router` (conditional edge; **M33**: delegates to **`AgentRegistry`** — `agent/registry.py` + `registry_data.py`, the single source of truth for agent route metadata; `router.route` = `get_registry().route_target(agent_type, intent)`, preceded by a `collab` guard when `MULTI_AGENT_COLLAB_ENABLED` + `needs_collab`) → either a **scenario-agent node**, the **`collab`** multi-agent subgraph (M35, opt-in), or the `retrieve → generate` fallback → `post_process → END`. Per-request resources (async `session`, `top_k`) travel in `RunnableConfig.configurable`, **not** in checkpointed state.

**7 read-only scenario agents** (`agent/agents/`) — each is a thin wrapper around `_base.run_scenario_agent(state, config, *, agent_name, tools, build_agent, degrade_label)`, the shared skeleton. (Plus **3 pack-driven domain agents** — trace/diagnose/tune — which reuse the same skeleton but build `create_react_agent` per-request with a pack-assembled prompt; see **Domain pack layer** below.) The skeleton runs a nested `langgraph.prebuilt.create_react_agent` via `astream(stream_mode="custom")`, bridges its `agent_step`/`citation`/`token` events up to the parent stream, and on **any** exception or recursion overflow calls `_degrade()` → plain `pipeline.recall` + streaming answer (the request never breaks; no-LLM-key short-circuits straight to degrade). Citations are emitted as stream events and accumulated by the adapter — agents deliberately do **not** use `Command`/write graph state for them.

| Intent | Node | Tools (subset of `agent/tools/`) |
|---|---|---|
| `code` | `code_understand` | search_code, search_symbol, get_call_chain, get_related_docs, read_code |
| `doc` | `doc_answer` | search_docs, read_doc, get_related_code, image_search, table_search |
| `graph` | `change_impact` | code set + get_callers, get_downstream_callers, get_affected_docs, rerank |
| `bug` | `bug_diagnosis` | code set + get_callers, get_recent_changes |
| `review` | `code_review` | code set + get_code_metrics, get_recent_changes, rerank |
| `test` | `test_generation` | code set + get_existing_tests |
| `web` | `web_search` | remote MCP tools loaded at startup (`web_tools.py`); KB-external Qs |

Tools (`agent/tools/{code,doc,maintain,formatting}_tools.py`) follow one shape: a pure async logic fn returning `ToolResult(text, chunks)`, wrapped by `@tool` which pulls `session`/`top_k` from `configurable`, emits citations + an `agent_step` via `get_stream_writer()`, and returns the text observation to the LLM. **Metadata-only tools** (`get_recent_changes`, `get_code_metrics`, `rerank`) emit a step but no citation (avoid duplicating `read_code`). `formatting.py` are pure text formatters, not tools. (`neo4j_query`/`get_javadoc` were explicitly **not** built — no Neo4j in this system, and `read_code` already returns signatures+javadoc.)

**WEB_SEARCH (联网检索, the 7th scenario agent)** answers KB-external questions (latest news, official docs, third-party-library usage) by calling **remote MCP servers**. Unlike the other agents' static module-level `TOOLS`, its tools load once at **lifespan startup**: `clients/mcp_client.py` (`init_mcp_client`/`get_mcp_client`/`close_mcp_client` — a process-level singleton managed in `main.py` lifespan, mirroring the checkpointer pattern) opens a `langchain-mcp-adapters` `MultiServerMCPClient` (transport `sse`/`streamable_http`, **not** stdio); `agent/tools/web_tools.py` then pulls the remote `BaseTool`s, wraps each so a call emits an `agent_step` (a single failed tool degrades to a text notice, not an Agent crash), and caches them in `_web_tools` (sync `get_web_tools()` read at request time). **Web results emit only `agent_step` traces — no citation** (they aren't KB chunks), so the frontend needs zero changes. Degradation chain: MCP off/unreachable/load-fail → `_web_tools=[]` → `get_web_agent()` returns `None` → `router.route` reroutes `web`/`WEB_SEARCH` to `retrieve` instead of `web_search` — no dead-end, no crash.

**Multi-agent collaboration (`collab`, M35, opt-in)**: when `MULTI_AGENT_COLLAB_ENABLED` and `query_analysis` flags `needs_collab` (intent=mixed or complex-diagnosis signals like 堆积/死锁/泄漏/排查), `router` routes to the `collab` node — a compiled 3-layer diagnostic **subgraph** (`agent/collab/subgraph.py`: `diagnose(假设) → verify(代码验证) → refine(文档调优)`, sharing `AgentState`, no own checkpointer). Each layer is a **lightweight node** (`collab/nodes.py::_bounded_tool_loop` — manual bounded tool-calling via `get_chat_model().bind_tools`, **not** `create_react_agent`) that reads upstream `WorkingMemory` and writes structured output into `AgentState` `collab_*` `operator.add` reducer fields (hypotheses/findings/suggestions + llm/tool-call counters). Cost is bounded by state counters + config caps (`COLLAB_MAX_LLM_CALLS=9`/`COLLAB_MAX_TOOL_CALLS=12`/`COLLAB_MAX_ROUNDS_PER_LAYER=3`) with **graceful shutdown** — on budget exhaustion the layer stops and `build_collab_report` summarizes accumulated WorkingMemory (never a bare mid-layer degrade). The `collab` main-graph node is a `collab_node` wrapper that bridges the subgraph's custom events to the parent stream + try/except → `_base._degrade` on whole-subgraph failure; per-layer exceptions are caught and skipped. **Read-only** (no HITL/write). Emits `retrieval_meta.mode="collab"`. Default off → `query_analysis` doesn't set `needs_collab` → zero behavior change.

**Observability**: every tool call → `agent_step` SSE event, accumulated in `streaming.stream_graph`, persisted as `retrieval_logs.agent_steps` (JSONB; migration `b7e2d09af3c1`), and replayed as the `agent` segment of `GET /v1/chat/messages/{id}/retrieval`. The trace is keyed on `agent_steps` being non-empty (not on `meta.mode=="agent"`), so steps taken before a degrade are still shown. The same column feeds the `/v1/agents/stats` KPIs (`services/agent_stats_service.py`).

**Checkpointing & HITL** (`agent/memory/`): `get_checkpointer()` returns `MemorySaver` by default or `AsyncPostgresSaver` when `LANGGRAPH_CHECKPOINT=postgres` (thread state keyed by `thread_id=conversation_id` survives restart — the basis for resume/HITL/cross-turn memory). **`DOC_MAINTAIN` is the one write-action agent** and is structurally different — a 4-node main-graph chain `propose → confirm → apply|reject → post_process` (in `agent/nodes/doc_maintain.py`), routed only by explicit `agent_type=DOC_MAINTAIN` (no intent maps to it). `propose` is itself a ReAct agent; `confirm` calls `interrupt(proposal)` — **the interrupt must live in a main-graph node, not inside the nested agent** (a nested interrupt restarts on resume instead of continuing). On first pass the adapter detects the interrupt via `aget_state`, persists an `interrupted` `ChatMessage`, and emits an `interrupt` event instead of `done`; `POST /v1/chat/resume` (langgraph-only; legacy → 501) sends `Command(resume=decision)` on the same thread. `POST /v1/chat/continue` (M14) is generic recovery via `astream(None)`; `GET /v1/chat/conversations/{id}/state` reports pending interrupts. A maintenance loop in `main.py` lifespan expires stale interrupts and cleans old checkpoints.

## Domain pack layer (M36–M38)

The system is a **"generic base + domain packs"** architecture. A `DomainPack` is a pluggable YAML + markdown resource bundle that, when *activated* for a conversation, powers 3 domain scenario agents and feeds the M34 CitationEnforcer's config-key whitelist. The first real pack is **RocketMQ** (`backend/domain_packs/rocketmq/`). With no active pack the domain intents are never produced, so it is pure opt-in — zero behavior change.

- **Data model** (`app/domain_packs/`): `models.DomainPack` = `Manifest` (`name`/`target_repo`/`version`/`active_agents`/`description`) + 4 optional lists (`trace_templates`/`diagnosis_trees`/`tuning_rules`/`config_registry`) + a `prompts` dict (`prompts/*.md`, keyed by stem = agent name). All domain fields default empty, so skeleton packs load. `loader.load_pack(pack_dir)` requires `manifest.yaml` (else `PackLoadError`); the 4 domain yamls and the prompts dir are optional. `registry.DomainPackRegistry` is a process singleton (mirrors `AgentRegistry`): `active_for_repo(repo)` matches on `manifest.target_repo`; `build_whitelist(pack)` returns a case-insensitive predicate over `config_registry` keys (or `None`).
- **Pack files** live in `backend/domain_packs/<name>/` (manifest.yaml + the 4 yamls + `prompts/{trace,diagnose,tune}.md`). `init_domain_pack_registry()` in `main.py` lifespan scans that dir, loads + registers each pack, and **logs + skips** a malformed one (startup never crashes).
- **Per-request activation**: `stream_graph` → `resolve_active_pack(conv)` resolves `conv.target_repo` → `settings.domain_pack_default_repo` → `settings.repo_path` → `registry.active_for_repo` (no match → `None`), then puts the **pack name** into graph state (`AgentState.active_pack_name`) — *not* into `RunnableConfig.configurable`. `Conversation.target_repo` (migration M36) is the per-conversation repo binding.
- **Double gate (no pack ⇒ zero behavior change)**: (1) `query_analysis` switches to a domain-augmented classify prompt when a pack is active and *only then* can emit `trace`/`diagnose`/`tune`; (2) `router.route` additionally requires `_pack_has_agent` (active pack **and** `manifest.active_agents` contains the intent), else falls back to `retrieve`. Either gate alone suffices; both exist.
- **3 pack-driven agents** (`trace_route`/`diagnose`/`tune` in `agent/agents/`, registered in `registry_data.py` as `TRACE_ROUTE`/`DIAGNOSE`/`TUNE`): unlike the static scenario agents they have **no module singleton** — each builds a fresh `create_react_agent` per request with `build_domain_prompt(name, pack)` = a hardcoded base role + `pack.prompts[name]` + the matching pack knowledge serialized in. They still delegate to `_base.run_scenario_agent` (same skeleton, same `_degrade`) and **reuse existing code tools** (search_code/search_symbol/get_call_chain/get_callers/get_recent_changes/get_code_metrics/read_code) — **no new tools**.
- **Whitelist bridge**: `build_whitelist(active_pack)` is threaded from `stream_graph` into `_enforce_into_stream` → the M34 `CitationEnforcer`, so config identifiers in the answer are verified against the pack's `config_registry` (in addition to retrieved citations).

## Document self-healing arc (staleness → rewrite → approve → writeback → PR)

The flagship feature line (M15→M21). It keeps `doc_chunks` honest against code drift, with a human gate on every write.

1. **Detect** — `services/staleness_sweep_service.run_staleness_sweep` (background loop, `STALENESS_SWEEP_ENABLED`): for each non-stale DOC↔CODE `chunk_relation`, if the code side has a `change_history` row with `change_type IN ('MODIFIED','DELETED')` newer than the relation, mark `is_stale=True, stale_reason='SWEEP:...'`. (Soft-delete already marks DELETED in real time; the sweep catches MODIFIED.) Reactive path: the `DOC_MAINTAIN` agent's `detect_stale_docs` tool finds stale anchors on demand. Exposed via `GET /v1/staleness/report`.
2. **Rewrite (pre-approval)** — `services/sweep_rewrite_service.run_sweep_rewrite` batch-generates LLM rewrites for top-N stale docs → writes an append-only MinIO artifact → inserts a `doc_update_proposals` row (`PENDING_PUSH`/`PENDING_MANUAL`). **Generate is pre-approval-safe**: it writes only append-only artifacts + one INSERT row, never the source of truth or git, so no gate needed. (The reactive DOC_MAINTAIN `apply` path generates the same way, after its own interrupt.)
3. **Approve (the HITL gate)** — `POST /v1/staleness/proposals/{id}/decide` (or DOC_MAINTAIN `/chat/resume`). This is the only gate.
4. **Write back** — `set_proposal_status(APPROVED)`: writes `rewritten_text` into `doc_chunks.content` + recomputes hash/tokens + sets `embedding_synced=false` + clears `is_stale` on the anchored relations, in **one transaction** (so `APPROVED` always means "written").
5. **Re-embed (eager)** — post-commit best-effort, re-embeds the chunk and upserts Milvus immediately (M20; `EAGER_REEMBED_ENABLED`); on failure leaves the lazy `embedding_synced=false` flag for `resync_embeddings`/the lifespan loop.
6. **Land (real git, opt-in)** — `services/doc_pr_service` + `pipeline/sync_git` (M21): in an **isolated `git worktree`** (never mutates the main checkout), splice the rewrite into the on-disk doc, branch+commit, and — if `DOC_GIT_PUSH_ENABLED` — push, backfilling `commit_sha`/`pr_url` and flipping `PUSHED`/`COMMITTED` (fail → `PUSH_FAILED`; the KB writeback is unaffected). Rollback's `close_open_doc_pr_for` deletes branches opened from a since-reverted commit.

Frontend: `pages/StalenessPage.tsx` (report KPIs + approval queue + original/rewrite diff-preview drawer).

## Read-only ops modules: monitor / search / eval

Three independent read-only modules (Phase 8–9). All are **zero new write-path, zero behavior change to retrieval/agents**.

- **Monitor** (`api/v1/monitor.py` + `services/monitor_service.py`): 4 GET endpoints — `retrieval-perf` (latency p50/p95 + funnel means + rerank rate + feedback, raw `percentile_cont` over `retrieval_logs`), `api-usage` (**PG-derived proxy** — there's no client-side usage telemetry, so LLM calls ≈ assistant messages, embedding ≈ retrieval_logs rows, tokens ≈ chars/4; the response `note` states this), `index-stats` (PG/Milvus/ES counts), `resources` (connectivity + storage size per component). Each component is independently try/except'd → never 500s. Frontend: `pages/MonitorPage.tsx`.
- **Search** (`api/v1/search.py` + `services/search_service.py`): `GET /v1/search?q=&kind=&top_k=` — pure-PG lexical (`extract_query_terms` → `lexical_recall`), zero API key / zero vector, returns `{chunk_id, kind, label, snippet, score}`. Powers the ⌘K palette, **not** semantic search (that stays on `/v1/chat`). Frontend: `components/CommandPalette.tsx` + `hooks/useHotkey.ts` (⌘K/Ctrl+K).
- **Eval** (`app/eval/`, `api/v1/eval.py`, `services/eval_run_service.py`): retrieval-quality measurement over the **real** funnel via two DI seams — `run_eval(..., recall_fn=...)` (defaults to `pipeline.recall`) and `recall(..., ablation=AblationConfig(...))`. `eval/metrics.py` = pure-fn Recall@K/MRR/NDCG. `backend/eval/eval_set.yaml` (committed) holds ~80 annotated queries; relevant items resolve at runtime from `Class.method` / class name / literal `chunk_id` (resilient to content change since `chunk_id` embeds `sha256(content)[:8]`). `--rewrite off` bypasses Stage-0 LLM rewrite for reproducible A/B deltas. **Endpoints**: single-run `POST /v1/eval/run` (+ optional `ablation={rerank:false,...}` for a single-variant run, M29) + `GET /runs[/{id}]`, and (M28) A/B ablation `POST /v1/eval/ab` + `GET /ab-runs[/{id}?diagnose=]` — both persist to `eval_runs`, discriminated by `config.kind` (`"single"`/`"ab"`); the kind filter also unifies the history table. CLIs `scripts/eval_retrieval.py` & `scripts/ab_eval.py` (M29) **persist via the same services** (`trigger="cli"`) so CLI runs appear in EvalPage history; `--no-persist` opts out (`ab_eval.py --diagnose` surfaces the dual-vector "returns doc, misses code" failure mode). (M39) **QA/幻觉 eval**: `app/eval/judge.py` generic `LLMJudge` (rubric-param, single `llm.chat` → JSON scores, degrade-on-fail; reused by M40 diagnosis) + `qa_service.run_qa_eval` (per query: legacy generate → M34 `enforce()` for unverified-citation rate → LLMJudge 4 dims → macro-avg) + `run_qa_and_persist(kind="qa")`. Endpoint `POST /v1/eval/qa` + `GET /qa-runs[/{id}]` (kind regex now `^(single|ab|qa)$`). `eval_set_qa.yaml` (committed) holds the QA set (each query's `scoring_hints` inject into the judge prompt). Frontend: `pages/EvalPage.tsx` (Tabs: 单次评测[+消融多选] + A/B 消融[+趋势 Sparkline + 逐 query 配对明细] + **LLM 评判[QA/幻觉 5 维]**). (M40) **诊断 eval**: `diag_service.run_diag_eval` — the `LLMJudge` over a 4-dim diagnosis rubric (`root_cause`/`code_ref`/`config_advice`/`reasoning`) with **per-query weights** (`eval_set_diag.yaml` `rubric_weights_default` + per-query override); each query's `expected` triple (root_cause_hints / relevant_code / config_suggestions) is injected as judge `scoring_hints` (semantic anchoring, not text match); **no M34 enforce** (the diag rubric has no hallucination dim); `overall` = macro-avg of per-query weighted scores (weights differ per query, so global-weight means can't be used). Trigger is **CLI-only** (`scripts/diag_eval.py`; there is no POST endpoint) — the API is read-only (`GET /v1/eval/diag-runs[/{id}]`; kind regex now `^(single|ab|qa|diagnosis)$`), and there is no frontend Tab. `run_diag_and_persist(kind="diagnosis")` mirrors the QA persistence; `qa_service.default_generate` is the shared generate fn. **Baseline regression gate**: `app/eval/baseline.py` pure-fn load/compare/write vs the committed `backend/eval/baseline_diag.json` (threshold 0.05 strict; a metric missing on either side = fail) — wired into `.github/workflows/eval.yaml` (the repo's only workflow): `ci` job on push/PR (deterministic `pytest -q` + `ruff`, zero infra / zero keys, minutes) + `eval-manual` job on workflow_dispatch (compose infra → alembic → clone+ingest RocketMQ 4.9.8 → `diag_eval.py` vs baseline; regression exits 1 and fails the job; needs `LLM_API_KEY`/`EMBEDDING_API_KEY` secrets, ~20–40 min).

## DB / Alembic patterns

- Models live in `app/db/models/{chat,code,doc,eval,graph,history,relation,system}.py` (`chat.py` = `Conversation` + `ChatMessage`; `history.py` = `SyncTask`/`ChangeHistory`/`RollbackHistory`/`DocUpdateProposal`; `eval.py` = `EvalRun`; `system.py` = `RetrievalLog`/`RankingModelConfig`). **`app/db/models/__init__.py` imports every model** so Alembic's `target_metadata = Base.metadata` sees them — a new model file that isn't imported there is invisible to `alembic revision --autogenerate`.
- Naming convention in `app/db/base.py` enforces `idx_/uk_/fk_/pk_` prefixes — match it in any hand-written DDL.
- Alembic runs **synchronous** via `database_url_sync` (psycopg); the app runs **async** via `database_url` (asyncpg). Same DB, two drivers.
- `scripts/ingest_*.py` and `reindex_code_bge.py` use a **sync** `Session` (not the async one) — they're CLI tools, not request-scoped.
- **`env.py` runs with `compare_type=True` and an `include_object` that excludes the four langgraph checkpoint tables** (`checkpoints`/`checkpoint_writes`/`checkpoint_blobs`/`checkpoint_migrations`, created by the saver's `setup()`). Because `compare_type=True` makes autogenerate prone to `alter_column` drift after frequent schema changes, the M27 `eval_runs` migration was **hand-written** (explicit `create_table` + indexes) rather than autogenerated — prefer that pattern for a new table when autogenerate looks noisy.
- Status columns (`SyncTask.status`, `DocUpdateProposal.status`, `EvalRun.status`, `ChatMessage.status`) are plain `String(32)` with **no DB-level enum** — adding a status value never needs a migration.
- `RetrievalLog` carries the full funnel (`recall_results`/`fine_rank_results`) **plus** `agent_steps` (JSONB) for agent runs; `retrieval_logs` is the join table the monitor/eval/agent-stats services all aggregate over.

## Frontend architecture

React 18 + TS + Vite + AntD 5 + Zustand + React Router 6 (`frontend/src/`). `layouts/Workbench.tsx` is the shell (sidebar nav + the persistent right-side `components/ContextPanel.tsx` that any citation click focuses). Routes in `App.tsx` under `<Workbench>`: `chat` (default), `documents`, `graph`, `sync`, `agents`, `staleness`, `monitor`, `eval` (+ placeholder `code`/`settings`).

- **Data layer**: `api/client.ts` (axios) + `api/sse.ts` (`@microsoft/fetch-event-source` — parses the SSE stream incl. `agent_step`/`interrupt`) → `hooks/useChat.ts` (chat state machine, incl. `interrupt`/`resume()`) → `stores/app.ts` (Zustand: conversations, the `focused` context chunk, `cmdkOpen`). One typed `api/*.ts` client per backend module.
- **Pages**: `ChatPage` (sidebar + `CitationCard` + `RetrievalDrawer` rendering the stage1/2/3 funnel **and** the `agent` tool trace), `DocumentsPage` (upload→parse→table preview), `GraphPage` (`react-cytoscapejs` viz), `SyncPage`, `AgentsPage` (KPIs from `/v1/agents/stats`), `StalenessPage` (report + approval queue + rewrite-preview drawer), `MonitorPage`, `EvalPage` (3 Tabs: 单次评测 / A/B 消融 / **LLM 评判 QA·幻觉**; KPIs + history + inline-SVG `Sparkline` trend — **there is no chart library in the repo**).
- **⌘K**: `components/CommandPalette.tsx` (AntD Modal; nav group → `useNavigate`, knowledge-base group → debounced `GET /v1/search` → `setFocused`) + `hooks/useHotkey.ts` (global ⌘K/Ctrl+K), wired to the previously-disabled ⌘K trigger in `Workbench`.

**Vite proxy rewrites `/api` → `` (stripped)**, so the backend sees `/v1/...`, not `/api/v1/...`. Local dev talks to `:8000` directly; only the (now-unused) containerized nginx layout would prefix `/api`. `api/eval.ts`'s `runEval` sets a single-request `timeout: 300_000` — the repo's first override of `client.ts`'s 30s default (eval runs ~80 queries through the reranker).

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
- `docs/开发清单.md` — granular phase-by-phase progress ledger for M1–M29 (the authoritative "what's done" list).
- `docs/重构开发清单.md` — the **M30+ refactor ledger** (six stages: retrieval enhancement, Agent layer evolution [AgentRegistry/CitationEnforcer/collab], domain packs, eval expansion, harness, model+RBAC). The M30 sparse-vector path was empirically disproven (SiliconFlow `/embeddings` returns dense only); M31/M32 are deferred until a real large repo is ingested.
- `docs/开发实施计划与方案.md` — implementation plan / phase breakdown.
- `docs/待确认问题清单.md` — confirmed/open design decisions.
- `docs/嵌入向量方案.md` — the dual-encoder 方案一 analysis (落地说明 at top maps to the `EMBEDDING_STRATEGY` switch).
- `docs/coderag后端设计方案.md` / `docs/coderag前端设计方案 .md` — full backend/frontend design (the §10 DDL, §11 retrieval referenced in code); both carry a 2026-07-27 change banner at top.
- `docs/api接口清单.md` — API spec for all planned modules.
