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

## 前端（frontend-v2，仓库根目录）

React 18 + Vite，dev 端口 **5174**（`vite.config` strictPort），代理 `/api` → `http://localhost:8010`（后端不变）。
首次运行需先 `pnpm install` 一次。

```bash
cd frontend-v2
pnpm install        # 仅首次（或依赖变更后）
pnpm dev            # http://localhost:5174
pnpm typecheck && pnpm build   # 回归门
```

一键拉起（backend + 四个 MCP server + frontend）：

```bash
uv run python scripts/dev_up.py --with-frontend
```

### M6 联调冒烟清单（手动，七步）

前置：基础设施 `docker compose -f D:/project/CodeRagAgent/docker-compose.yml up -d`、
`uv run alembic upgrade head`、且已有入库 repo。任一步不过 → 修复后重来。

1. `uv run python scripts/dev_up.py --with-frontend`；`curl http://localhost:8010/health` → `"status":"ok"`（或 degraded 但四组件非 down）
2. 浏览器 http://localhost:5174 ：四页路由可达、侧栏仅 4 项、⌘K 仅导航
3. ChatPage：repo 选已入库仓库，问一个代码问题（如 `CommitLog putMessage`）→ token 流式出字、引用卡片出现（code 卡带行号）、点卡片右侧面板出代码窗口且高亮行区间
4. DocumentsPage：列表出已入库文档、抽屉出章节
5. GraphPage：搜索类名 → 调用图渲染（cytoscape 布局）、切「模块依赖」出模块图
6. SyncPage：事件表出 `pipeline_events` 行（无则先手动 webhook 一次）；「模拟 Push」提交后 20s 内出新行
7. 反馈按钮点击无报错（Network 200）

