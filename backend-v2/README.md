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

**启动（server 侧 env/.env 控制仓库路径）：**

```bash
# 真实仓库（主 checkout）：
REPOS_ROOT=D:/project/CodeRagAgent/data/repo DEFAULT_REPO=rocketmq uv run python -m app.mcp_servers.code_server
# fixture 测试：
REPOS_ROOT=tests/fixtures DEFAULT_REPO=mini_repo uv run python -m app.mcp_servers.code_server

# 一键拉起（backend + code-mcp）：
uv run python scripts/dev_up.py
```

**冒烟验证（另起终端）：**

```bash
# 真实仓库（默认 MAX_RECONSUME_TIMES）：
uv run python scripts/smoke_mcp.py
# fixture 指定参数：
uv run python scripts/smoke_mcp.py --pattern MAX_RETRY_TIMES --repo mini_repo
```

工具：`grep_code`（output_mode: content/files_with_matches/count）/ `glob_files` /
`read_file` / `list_directory` / `find_symbol`（全部只读，
路径限制在 REPOS_ROOT 内）。stdio 传输（测试/同机）：加 `--stdio`。
