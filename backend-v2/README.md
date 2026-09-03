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
2. 浏览器 http://localhost:5174 ：五页路由可达、侧栏仅 5 项（问答/文档/调用图/同步/监控）、⌘K 仅导航
3. ChatPage：repo 选已入库仓库，问一个代码问题（如 `CommitLog putMessage`）→ token 流式出字、引用卡片出现（code 卡带行号）、点卡片右侧面板出代码窗口且高亮行区间
4. DocumentsPage：列表出已入库文档、抽屉出章节
5. GraphPage：搜索类名 → 调用图渲染（cytoscape 布局）、切「模块依赖」出模块图
6. SyncPage：事件表出 `pipeline_events` 行（无则先手动 webhook 一次）；「模拟 Push」提交后 20s 内出新行
7. 反馈按钮点击无报错（Network 200）

## 监控（M7）

每个请求采集五层 span 树（`request → route → agent → tool/llm`）并一比一落库，
配套三组只读监控端点 + 前端「系统监控」页。全部只读、组件级软失败，任何一段
失败降级为 null/空段，**永不 500**。

### trace_spans（迁移 `0006_trace_spans`，head=v2_0006）

- 采集器：`app/agent/trace.py` 的 `SpanCollector`（纯件，请求级）——span 形状为
  `{span_id, parent_id, kind, name, ...}`，`kind ∈ request/route/agent/tool/llm/retrieval`；
  开栈缺省父级自动嵌套，调用点零显式传 parent。
- 落库：每次问答按 assistant 消息一比一写一行 `trace_spans`
  （`message_id` UNIQUE FK → `chat_messages.id` ON DELETE CASCADE，随会话级联清理），
  `spans` 为 JSONB 平面列表，`token_usage` 存 CostController 真实用量，`duration_ms`
  为 request span 时长；与消息同事务写入。
- 回放：`GET /v1/monitor/traces/{message_id}` 返回完整 span 树。

### 监控端点（`/v1/monitor`，全只读）

| 端点 | 内容 |
| --- | --- |
| `GET /v1/monitor/overview?window=today\|7d\|all` | 业务总览：请求数 / 时延 p50·p95 / 平均工具轮次 / 平均 token / codenav 命中率 / 路由分布 |
| `GET /v1/monitor/traces?window=&limit=`、`GET /v1/monitor/traces/{message_id}` | 全链路追溯列表（id 倒序 + 窗内总数）；单条 span 树回放（无此行 404） |
| `GET /v1/monitor/pipeline` | 离线管道面：Redis Stream 长度/pending/lag + 死信长度 + PG 事件账本计数（Redis 挂 → 对应段 null） |

### MonitorPage（frontend-v2，「系统监控」侧栏项）

三卡：

1. **概览**——总览指标 Statistic 行；
2. **管道状态**——Stream/死信/事件账本计数；
3. **全链路追溯**——TraceView：span 树（AntD Tree）+ 内联 SVG 瀑布图，列表点行进详情。

> 截图位：MonitorPage 三卡（概览 / 管道状态 / 全链路追溯树+瀑布图）。

