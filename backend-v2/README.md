# CodeRAG backend-v2（Agentic + MCP）

方案二架构：代码走 grep/AST 只读工具精确检索（MCP server 暴露），文档走向量/BM25，
PG 存调用图。详见 `docs/superpowers/specs/2026-08-28-backend-v2-agentic-mcp-design.md`。

## 快速开始（backend-v2/ 下）

```bash
uv sync --extra dev
cp .env.example .env            # 确保 POSTGRES_DB=coderag_v2
uv run python scripts/ensure_db.py
uv run alembic upgrade head
uv run uvicorn app.main:app --port 8010
curl http://localhost:8010/health
```

## MCP server（code-mcp）

```bash
uv run python -m app.mcp_servers.code_server          # streamable-http :8110/mcp
uv run python scripts/dev_up.py                       # backend + 全部 MCP server 一键拉起
uv run python scripts/smoke_mcp.py                    # 冒烟：tools/list + grep MAX_RECONSUME_TIMES
```

工具：`grep_code` / `read_file` / `list_directory` / `find_symbol`（全部只读，
路径限制在 REPOS_ROOT 内）。stdio 传输（测试/同机）：加 `--stdio`。
