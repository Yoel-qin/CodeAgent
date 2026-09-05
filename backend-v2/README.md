# CodeRAG backend-v2（Agentic + MCP）

方案二架构：代码走 grep/AST 只读工具精确检索（MCP server 暴露），文档走向量/BM25，
PG 存调用图。详见 `docs/superpowers/specs/2026-08-28-backend-v2-agentic-mcp-design.md`。

特性：MCP 代码工具检索 · 文档向量/BM25 · 调用图 · 多 Agent 编排（ReAct）·
可观测 trace · **评测**（V2-M8）· **RBAC**（V2-M9）· **WEB_SEARCH 联网检索**（V2-M9）。

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

React 18 + Vite，dev 端口 **5300**（`vite.config` strictPort），代理 `/api` → `http://localhost:8010`（后端不变）。
首次运行需先 `pnpm install` 一次。

```bash
cd frontend-v2
pnpm install        # 仅首次（或依赖变更后）
pnpm dev            # http://localhost:5300
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
2. 浏览器 http://localhost:5300 ：五页路由可达、侧栏仅 5 项（问答/文档/调用图/同步/监控）、⌘K 仅导航
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

## 评测（V2-M8）

golden set 在 `eval/golden_set.yaml`（锚点语法：code = `Class.method` / `Class`，
doc = `doc_name#anchor`）。跑批走真实 Agent 图（不落业务表），结果落 `eval_runs`：

```bash
# 锚点校准（不跑批；unresolved 的 case 会在跑批中被跳过并在报告中列出）
uv run python scripts/eval_run.py --validate
# 单 baseline 跑批 + QA 4 维 LLM 评判
uv run python scripts/eval_run.py --judge
# A/B：轮数减半 vs 禁图工具（要 baseline 对照需另跑一次无参默认）
uv run python scripts/eval_run.py --ab r4:rounds_code=4,rounds_doc=2 --ab nograph:code_no_graph=1
```

REST：`POST /v1/eval/run`（variants/judge 同 CLI）+ `GET /v1/eval/runs[/{id}]`；前端
「评测」页可视化（变体对比 + 历史 + 逐 case 明细）。指标：代码定位命中率 / 引用准确率 /
轮次分布 / 延迟分位 / 均 Token。变体旋钮：`rounds_code/rounds_doc/code_no_graph/
model_reasoning/top_k`（缺席 = 生产默认）。

## RBAC（V2-M9，默认关闭）

```bash
# 1. 根 .env 或 backend-v2/.env：RBAC_ENABLED=1 + JWT_SECRET=<随机串>
# 2. 建用户（角色 4 选 1：admin/developer/ops/external）
uv run python scripts/create_user.py alice <password> developer
```

- 登录：`POST /v1/auth/login` → JWT（前端自动带；过期自动回登录页）
- 两权限维度：`roles.allowed_scopes`（repo 可见性 + code/doc 读域——external 不见
  代码）、`endpoint_classes`（router 归口）
- 门控三路：router 级类门（403）；图内（无 code 权限不路由 CodeNav、retrieve 按域
  跳路、code 域工具防御返回无权提示）；repo 可见性（chat 403 / 列表过滤 / 详情 404）
- `RBAC_ENABLED=0`（默认）→ 匿名透传，全部端点零行为变更

## WEB_SEARCH 联网检索（V2-M9，默认关闭）

`WEB_MCP_SERVERS` 配置远程 MCP server（web 检索/抓取），Router 的 web intent 路由到
WebSearch Agent：

```
WEB_MCP_SERVERS=[{"name":"tavily","url":"http://localhost:9144/sse","transport":"sse"}]
```

未配置/不可达 → web intent 自动落 retrieve 兜底（不崩、无死路）；web 结果只发
agent_step 轨迹不发 citation（非 KB chunk）。

## 图片视觉描述（默认关闭）

ingest 期对 docx/md 等文档中的 IMAGE 元素调 SiliconFlow 视觉模型生成中文描述，
描述作为 `kind="image"` 的 doc_section 入库（向量 + BM25 均可召回），
`media_chunks.description` 同步填充。无 key / 调用失败 → 空描述（= 现状），软失败。

| 配置键 | 默认 | 说明 |
| --- | --- | --- |
| `VISION_DESC_ENABLED` | `false` | 图片视觉描述总开关（on 时 ingest 逐图调视觉模型，描述进 doc_sections 可检索） |
| `VISION_MODEL` | `PaddlePaddle/PaddleOCR-VL-1.5` | SiliconFlow 视觉模型（OCR/文档理解特化，免费） |
| `VISION_BASE_URL` | `""` | 空 → 回落 `EMBEDDING_BASE_URL` |
| `VISION_API_KEY` | `""` | 空 → 回落 `EMBEDDING_API_KEY` |
| `VISION_MAX_IMAGES_PER_DOC` | `50` | 单文档描述图片数上限（成本护栏，超出记 `parse_meta.vision_skipped`） |

注意：开关 on **不回填已入库文档**——ingest 的 hash 幂等跳过发生在解析之前，文件未变即整篇跳过，
视觉描述自然不会补上；要给既有文档补描述须 `--reindex` 重灌（`python scripts/ingest_docs.py --reindex`）。

