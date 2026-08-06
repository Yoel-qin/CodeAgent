> ⚠️ **架构变更（2026-07-27）**：嵌入改为可切换双框架（`EMBEDDING_STRATEGY`）；**图向量相关接口/字段（路径 C 图向量检索、graph_embedding）已弃用**，图遍历（路径 D）相关保留。当前**已实现**接口以 [`开发清单.md`](./开发清单.md) 为准（chat completions / conversations / messages retrieval / suggestions / feedback）。

# RocketMQ 智能知识库 — 接口清单

------

## 一、接口总览

| 模块                | 接口数 | 协议             |
| ------------------- | ------ | ---------------- |
| 智能问答            | 6      | REST + WebSocket |
| 代码浏览            | 5      | REST             |
| 知识图谱            | 4      | REST             |
| ~~社区总览 (GraphRAG)~~ | ~~4~~ | ~~REST~~ ❌ 已弃用(2026-07-29) |
| 同步管理            | 6      | REST             |
| Agent 面板          | 5      | REST             |
| 系统监控            | 4      | REST             |
| 全局搜索            | 2      | REST             |
| 用户与设置          | 3      | REST             |
| **合计**            | **39** |                  |

------

## 二、智能问答模块

### 2.1 提交问答请求

```
POST /api/v1/chat/completions
```

| 参数位置 | 参数名          | 类型     | 必填 | 说明                                                         |
| -------- | --------------- | -------- | ---- | ------------------------------------------------------------ |
| Body     | query           | string   | ✅    | 用户提问内容                                                 |
| Body     | agent_type      | string   | ✅    | Agent 类型：`CODE_UNDERSTAND` / `DOC_QA` / `CHANGE_IMPACT` / `BUG_DIAGNOSIS` / `CODE_REVIEW` / `TEST_GEN` / `GLOBAL_QA` |
| Body     | conversation_id | string   | ❌    | 会话 ID，多轮对话时传入                                      |
| Body     | module_filter   | string[] | ❌    | 模块过滤：`["broker","client","store"]`                      |
| Body     | code_context    | string   | ❌    | 用户附加的代码片段                                           |
| Body     | top_k           | int      | ❌    | 返回引用数，默认 8                                           |
| Body     | stream          | boolean  | ❌    | 是否流式返回，默认 true                                      |

**响应（SSE 流式）：**

```
event: token
data: {"content": "事务消息的回查由..."}

event: citation
data: {"type":"code","file":"TransactionalMessageServiceImpl.java","lines":[128,156],"score":0.94}

event: citation
data: {"type":"doc","path":"4.2 事务消息回查机制","score":0.91}

event: done
data: {"conversation_id":"conv_xxx","message_id":"msg_xxx","latency_ms":342}
```

------

### 2.2 获取会话历史

```
GET /api/v1/chat/conversations
```

| 参数位置 | 参数名     | 类型   | 必填 | 说明              |
| -------- | ---------- | ------ | ---- | ----------------- |
| Query    | page       | int    | ❌    | 页码，默认 1      |
| Query    | page_size  | int    | ❌    | 每页条数，默认 20 |
| Query    | agent_type | string | ❌    | 按 Agent 类型过滤 |

**响应：**

```json
{
  "total": 156,
  "items": [
    {
      "conversation_id": "conv_xxx",
      "title": "事务消息回查机制",
      "agent_type": "CODE_UNDERSTAND",
      "message_count": 4,
      "created_at": "2026-07-24T14:32:00Z",
      "updated_at": "2026-07-24T14:35:00Z"
    }
  ]
}
```

------

### 2.3 获取单条会话详情

```
GET /api/v1/chat/conversations/{conversation_id}
```

| 参数位置 | 参数名          | 类型   | 必填 | 说明    |
| -------- | --------------- | ------ | ---- | ------- |
| Path     | conversation_id | string | ✅    | 会话 ID |

**响应：**

```json
{
  "conversation_id": "conv_xxx",
  "agent_type": "CODE_UNDERSTAND",
  "messages": [
    {
      "message_id": "msg_001",
      "role": "user",
      "content": "事务消息的回查机制是怎么实现的？",
      "created_at": "..."
    },
    {
      "message_id": "msg_002",
      "role": "assistant",
      "content": "事务消息的回查由...",
      "citations": [
        {"type":"code","file":"...","lines":[128,156],"score":0.94},
        {"type":"doc","path":"4.2 回查机制","score":0.91}
      ],
      "retrieval_detail": {
        "stage1_recall": {"vector":20,"bm25":18,"graph_traverse":12,"merged":50},
        "stage2_coarse": {"model":"bge-reranker-base","output":25,"latency_ms":89},
        "stage3_fine": {"model":"bge-reranker-v2-m3","output":8,"latency_ms":156}
      },
      "created_at": "..."
    }
  ]
}
```

------

### 2.4 获取检索详情

```
GET /api/v1/chat/messages/{message_id}/retrieval
```

| 参数位置 | 参数名     | 类型   | 必填 | 说明    |
| -------- | ---------- | ------ | ---- | ------- |
| Path     | message_id | string | ✅    | 消息 ID |

**响应：**

```json
{
  "stage1": {
    "latency_ms": 45,
    "channels": [
      {"name":"vector_semantic","count":20,"top_score":0.89},
      {"name":"bm25","count":18,"top_score":0.92},
      {"name":"graph_traverse","count":12}
    ],
    "merged_count": 52
  },
  "stage2": {
    "model": "bge-reranker-base",
    "latency_ms": 89,
    "output_count": 25
  },
  "stage3": {
    "model": "bge-reranker-v2-m3",
    "latency_ms": 156,
    "output_count": 8,
    "results": [
      {"chunk_id":"chunk_001","score":0.94,"type":"code","file":"...","lines":[128,156]},
      {"chunk_id":"chunk_002","score":0.91,"type":"doc","path":"4.2 回查机制"}
    ]
  }
}
```

------

### 2.5 获取追问建议

```
POST /api/v1/chat/suggestions
```

| 参数位置 | 参数名          | 类型   | 必填 | 说明            |
| -------- | --------------- | ------ | ---- | --------------- |
| Body     | conversation_id | string | ✅    | 当前会话 ID     |
| Body     | last_message_id | string | ✅    | 最后一条消息 ID |

**响应：**

```json
{
  "suggestions": [
    "回查次数上限是多少？",
    "half message 存在哪里？",
    "回查超时后消息怎么处理？"
  ]
}
```

------

### 2.6 消息反馈

```
POST /api/v1/chat/messages/{message_id}/feedback
```

| 参数位置 | 参数名     | 类型   | 必填 | 说明                      |
| -------- | ---------- | ------ | ---- | ------------------------- |
| Path     | message_id | string | ✅    | 消息 ID                   |
| Body     | rating     | string | ✅    | `HELPFUL` / `NOT_HELPFUL` |
| Body     | comment    | string | ❌    | 用户补充说明              |

------

## 三、代码浏览模块

### 3.1 获取模块/包树

```
GET /api/v1/code/tree
```

| 参数位置 | 参数名 | 类型   | 必填 | 说明                                                         |
| -------- | ------ | ------ | ---- | ------------------------------------------------------------ |
| Query    | module | string | ❌    | 顶层模块：`broker` / `client` / `namesrv` / `store` / `remoting` |
| Query    | depth  | int    | ❌    | 展开深度，默认 3                                             |

**响应：**

```json
{
  "module": "broker",
  "tree": [
    {
      "name": "org.apache.rocketmq.broker",
      "type": "package",
      "children": [
        {
          "name": "transaction",
          "type": "package",
          "children": [
            {"name":"TransactionalMessageServiceImpl","type":"class","file_path":"...","doc_count":2,"call_count":12,"stale":false},
            {"name":"TransactionalMessageCheckService","type":"class","file_path":"...","doc_count":1,"call_count":5,"stale":true}
          ]
        }
      ]
    }
  ]
}
```

------

### 3.2 获取类详情

```
GET /api/v1/code/classes/{class_id}
```

| 参数位置 | 参数名   | 类型   | 必填 | 说明                        |
| -------- | -------- | ------ | ---- | --------------------------- |
| Path     | class_id | string | ✅    | 类唯一标识（全限定名 hash） |

**响应：**

```json
{
  "class_id": "cls_xxx",
  "name": "TransactionalMessageServiceImpl",
  "package": "org.apache.rocketmq.broker.transaction",
  "module": "broker",
  "file_path": "broker/src/main/java/.../TransactionalMessageServiceImpl.java",
  "implements": ["TransactionalMessageService"],
  "extends": null,
  "javadoc": "事务消息服务实现类...",
  "doc_count": 2,
  "call_count": 12,
  "stale_docs": 0,
  "methods": [
    {
      "method_id": "mtd_001",
      "name": "checkLocalTransaction",
      "signature": "public void checkLocalTransaction(MessageExt msg)",
      "line_start": 128,
      "line_end": 156,
      "doc_count": 1,
      "caller_count": 3,
      "callee_count": 5,
      "last_modified_commit": "a3f8c21",
      "last_modified_at": "2026-07-22T09:30:00Z",
      "stale": true
    }
  ]
}
```

------

### 3.3 获取方法详情

```
GET /api/v1/code/methods/{method_id}
```

| 参数位置 | 参数名    | 类型   | 必填 | 说明         |
| -------- | --------- | ------ | ---- | ------------ |
| Path     | method_id | string | ✅    | 方法唯一标识 |

**响应：**

```json
{
  "method_id": "mtd_001",
  "name": "checkLocalTransaction",
  "class_name": "TransactionalMessageServiceImpl",
  "file_path": "...",
  "line_start": 128,
  "line_end": 156,
  "source_code": "public void checkLocalTransaction(...) {\n    ...\n}",
  "javadoc": "检查本地事务状态...",
  "callers": [
    {"method_id":"mtd_010","name":"scheduleCheck","class_name":"TransactionalMessageCheckService"}
  ],
  "callees": [
    {"method_id":"mtd_020","name":"resolveHalfMsg","class_name":"TransactionalMessageServiceImpl"},
    {"method_id":"mtd_021","name":"endTransaction","class_name":"EndTransactionProcessor"}
  ],
  "related_docs": [
    {"doc_id":"doc_001","path":"4.2 事务消息回查机制","section":"4.2.1 回查流程","stale":true}
  ],
  "change_history": [
    {"commit":"a3f8c21","date":"2026-07-22","message":"优化回查超时判断逻辑","change_type":"MODIFIED"}
  ]
}
```

------

### 3.4 获取方法源码（带行号）

```
GET /api/v1/code/methods/{method_id}/source
```

| 参数位置 | 参数名        | 类型   | 必填 | 说明                   |
| -------- | ------------- | ------ | ---- | ---------------------- |
| Path     | method_id     | string | ✅    | 方法 ID                |
| Query    | context_lines | int    | ❌    | 上下文扩展行数，默认 5 |

**响应：**

```json
{
  "file_path": "broker/src/main/java/.../TransactionalMessageServiceImpl.java",
  "language": "java",
  "line_start": 123,
  "line_end": 161,
  "lines": [
    {"line_no":123,"content":"    /**"},
    {"line_no":124,"content":"     * 检查本地事务状态"},
    {"line_no":128,"content":"    public void checkLocalTransaction(MessageExt msg) {"},
    {"line_no":129,"content":"        // 扫描 half message"}
  ]
}
```

------

### 3.5 搜索代码标识符

```
GET /api/v1/code/search
```

| 参数位置 | 参数名    | 类型   | 必填 | 说明                                       |
| -------- | --------- | ------ | ---- | ------------------------------------------ |
| Query    | q         | string | ✅    | 搜索关键词（类名/方法名/包名）             |
| Query    | type      | string | ❌    | `class` / `method` / `package`，不传则全部 |
| Query    | module    | string | ❌    | 限定模块                                   |
| Query    | page      | int    | ❌    | 页码                                       |
| Query    | page_size | int    | ❌    | 每页条数，默认 20                          |

**响应：**

```json
{
  "total": 8,
  "items": [
    {"type":"class","name":"DefaultMQPushConsumer","module":"client","file_path":"...","doc_count":3},
    {"type":"method","name":"sendMessage","class_name":"DefaultMQProducer","module":"client","line":245}
  ]
}
```

------

## 四、知识图谱模块

### 4.1 获取调用图

```
GET /api/v1/graph/call-graph
```

| 参数位置 | 参数名      | 类型   | 必填 | 说明                                      |
| -------- | ----------- | ------ | ---- | ----------------------------------------- |
| Query    | center_node | string | ✅    | 中心节点 ID（类或方法）                   |
| Query    | depth       | int    | ❌    | 展开跳数，默认 2                          |
| Query    | direction   | string | ❌    | `BOTH` / `CALLERS` / `CALLEES`，默认 BOTH |
| Query    | max_nodes   | int    | ❌    | 最大返回节点数，默认 50                   |

**响应：**

```json
{
  "nodes": [
    {"id":"cls_001","name":"TransactionalMessageServiceImpl","type":"class","module":"broker","stale":false},
    {"id":"mtd_001","name":"checkLocalTransaction","type":"method","stale":true}
  ],
  "edges": [
    {"source":"mtd_010","target":"mtd_001","type":"CALLS","weight":3},
    {"source":"mtd_001","target":"mtd_020","type":"CALLS","weight":1}
  ]
}
```

------

### 4.2 获取代码-文档关联图

```
GET /api/v1/graph/code-doc-relations
```

| 参数位置 | 参数名             | 类型    | 必填 | 说明                           |
| -------- | ------------------ | ------- | ---- | ------------------------------ |
| Query    | center_node        | string  | ✅    | 中心节点 ID                    |
| Query    | depth              | int     | ❌    | 展开跳数，默认 1               |
| Query    | include_stale_only | boolean | ❌    | 是否只返回过期关联，默认 false |

**响应：**

```json
{
  "nodes": [
    {"id":"cls_001","name":"TransactionalMessageServiceImpl","type":"code"},
    {"id":"doc_001","name":"4.2 事务消息回查机制","type":"doc","stale":true}
  ],
  "edges": [
    {"source":"cls_001","target":"doc_001","type":"DOCUMENTED_BY","stale":true,"stale_reason":"代码已修改，文档未更新"}
  ]
}
```

------

### 4.3 获取模块依赖图

```
GET /api/v1/graph/module-dependency
```

| 参数位置 | 参数名      | 类型   | 必填 | 说明                                        |
| -------- | ----------- | ------ | ---- | ------------------------------------------- |
| Query    | granularity | string | ❌    | `MODULE` / `PACKAGE` / `CLASS`，默认 MODULE |

**响应：**

```json
{
  "nodes": [
    {"id":"broker","name":"broker","type":"module","class_count":245},
    {"id":"store","name":"store","type":"module","class_count":128}
  ],
  "edges": [
    {"source":"broker","target":"store","type":"DEPENDS_ON","weight":47}
  ]
}
```

------

### 4.4 图谱节点搜索

```
GET /api/v1/graph/search
```

| 参数位置 | 参数名    | 类型   | 必填 | 说明                                     |
| -------- | --------- | ------ | ---- | ---------------------------------------- |
| Query    | q         | string | ✅    | 搜索关键词                               |
| Query    | node_type | string | ❌    | `class` / `method` / `doc` |
| Query    | limit     | int    | ❌    | 返回数量，默认 10                        |

**响应：**

```json
{
  "items": [
    {"id":"cls_001","name":"TransactionalMessageServiceImpl","type":"class","module":"broker"},
    {"id":"doc_001","name":"4.2 事务消息回查机制","type":"doc"}
  ]
}
```

------

## 五、社区总览模块（GraphRAG）　❌ 已弃用（2026-07-29）

> **已弃用**：随 Phase 6 GraphRAG 社区摘要整体移除，本模块 4 接口（`/communities` 列表/详情/子图/社区提问）不再实现。以下内容仅作历史设计存档，**以 [`开发清单.md`](./开发清单.md) §Phase 6 为准**。

### 5.1 获取社区列表

```
GET /api/v1/communities
```

| 参数位置 | 参数名    | 类型   | 必填 | 说明                                              |
| -------- | --------- | ------ | ---- | ------------------------------------------------- |
| Query    | level     | int    | ❌    | 社区层级：0=全局 / 1=子系统 / 2=模块，默认 1      |
| Query    | sort_by   | string | ❌    | `importance` / `size` / `health`，默认 importance |
| Query    | page      | int    | ❌    | 页码                                              |
| Query    | page_size | int    | ❌    | 每页条数，默认 10                                 |

**响应：**

```json
{
  "total": 12,
  "items": [
    {
      "community_id": "com_001",
      "name": "事务消息子系统",
      "level": 1,
      "tags": ["事务消息","半消息","回查","二阶段提交"],
      "code_node_count": 47,
      "doc_node_count": 12,
      "importance": 5,
      "summary": "该社区覆盖 RocketMQ 事务消息的完整实现...",
      "key_entities": ["TransactionalMessageServiceImpl","CommitLog","half topic"],
      "doc_health_score": 0.82,
      "stale_doc_count": 3
    }
  ]
}
```

------

### 5.2 获取社区详情

```
GET /api/v1/communities/{community_id}
```

| 参数位置 | 参数名       | 类型   | 必填 | 说明    |
| -------- | ------------ | ------ | ---- | ------- |
| Path     | community_id | string | ✅    | 社区 ID |

**响应：**

```json
{
  "community_id": "com_001",
  "name": "事务消息子系统",
  "level": 1,
  "summary_full": "该社区覆盖 RocketMQ 事务消息的完整实现，包括半消息存储、本地事务执行、回查机制和最终提交/回滚...",
  "tags": ["事务消息","半消息","回查"],
  "key_entities": [
    {"id":"cls_001","name":"TransactionalMessageServiceImpl","type":"class","importance":0.95},
    {"id":"cls_002","name":"EndTransactionProcessor","type":"class","importance":0.88}
  ],
  "doc_health": {
    "score": 0.82,
    "total_docs": 12,
    "stale_docs": [
      {"doc_id":"doc_001","path":"4.2 回查机制","stale_reason":"commit a3f8c21 修改了回查超时逻辑","stale_since":"2026-07-22"}
    ]
  },
  "sub_communities": ["com_001_01","com_001_02"],
  "parent_community": null
}
```

------

### 5.3 获取社区子图

```
GET /api/v1/communities/{community_id}/graph
```

| 参数位置 | 参数名       | 类型     | 必填 | 说明                                     |
| -------- | ------------ | -------- | ---- | ---------------------------------------- |
| Path     | community_id | string   | ✅    | 社区 ID                                  |
| Query    | max_nodes    | int      | ❌    | 最大节点数，默认 80                      |
| Query    | node_types   | string[] | ❌    | 过滤节点类型：`["class","method","doc"]` |

**响应：** 同 4.1 调用图格式，增加 `community_id` 字段。

------

### 5.4 社区提问（携带社区上下文）

```
POST /api/v1/communities/{community_id}/ask
```

| 参数位置 | 参数名          | 类型   | 必填 | 说明     |
| -------- | --------------- | ------ | ---- | -------- |
| Path     | community_id    | string | ✅    | 社区 ID  |
| Body     | query           | string | ✅    | 用户提问 |
| Body     | conversation_id | string | ❌    | 会话 ID  |

**响应：** 同 2.1 流式响应格式，但检索范围限定在该社区内。

------

## 六、同步管理模块

### 6.1 获取索引状态

```
GET /api/v1/sync/status
```

无参数。

**响应：**

```json
{
  "status": "HEALTHY",
  "last_sync_at": "2026-07-24T14:55:00Z",
  "last_commit": "a3f8c21",
  "stats": {
    "total_chunks": 12847,
    "code_chunks": 9214,
    "doc_chunks": 3633,
    "stale_docs": 7,
    "total_relations": 8432,
    "total_anchors": 2156
  }
}
```

------

### 6.2 获取同步任务列表

```
GET /api/v1/sync/tasks
```

| 参数位置 | 参数名    | 类型   | 必填 | 说明                                |
| -------- | --------- | ------ | ---- | ----------------------------------- |
| Query    | type      | string | ❌    | `FULL` / `INCREMENTAL` / `ROLLBACK` |
| Query    | status    | string | ❌    | `RUNNING` / `COMPLETED` / `FAILED`  |
| Query    | page      | int    | ❌    | 页码                                |
| Query    | page_size | int    | ❌    | 每页条数，默认 20                   |

**响应：**

```json
{
  "total": 89,
  "items": [
    {
      "task_id": "task_001",
      "type": "INCREMENTAL",
      "commit": "a3f8c21",
      "status": "COMPLETED",
      "changes": {"added":3,"modified":5,"deleted":1},
      "started_at": "2026-07-24T14:55:00Z",
      "finished_at": "2026-07-24T14:55:34Z",
      "duration_ms": 34000,
      "doc_pr_url": "https://github.com/.../pull/1235",
      "doc_pr_status": "OPEN"
    },
    {
      "task_id": "task_002",
      "type": "ROLLBACK",
      "commit": "c1a2b3c",
      "source_commit": "e7f8a9b",
      "status": "COMPLETED",
      "rollback_detail": {
        "chunks_rolled_back": 4,
        "chunks_restored": 2,
        "relations_restored": 6,
        "anchors_restored": 2,
        "stale_anchors_cleared": 1,
        "doc_pr_closed": "https://github.com/.../pull/1234"
      },
      "triggered_by": "AGENT_SUGGESTED",
      "started_at": "2026-07-23T16:40:00Z",
      "finished_at": "2026-07-23T16:40:22Z"
    }
  ]
}
```

------

### 6.3 获取回滚历史

```
GET /api/v1/sync/rollbacks
```

| 参数位置 | 参数名    | 类型 | 必填 | 说明     |
| -------- | --------- | ---- | ---- | -------- |
| Query    | page      | int  | ❌    | 页码     |
| Query    | page_size | int  | ❌    | 每页条数 |

**响应：**

```json
{
  "total": 5,
  "items": [
    {
      "rollback_id": 1,
      "rollback_commit": "c1a2b3c",
      "source_commit": "e7f8a9b",
      "chunks_rolled_back": 4,
      "chunks_restored": 2,
      "chunks_deleted": 0,
      "relations_restored": 6,
      "anchors_restored": 2,
      "stale_anchors_cleared": 1,
      "doc_pr_closed": "https://github.com/.../pull/1234",
      "triggered_by": "AGENT_SUGGESTED",
      "status": "COMPLETED",
      "created_at": "2026-07-23T16:40:00Z"
    }
  ]
}
```

------

### 6.4 手动触发同步

```
POST /api/v1/sync/trigger
```

| 参数位置 | 参数名        | 类型   | 必填 | 说明                                |
| -------- | ------------- | ------ | ---- | ----------------------------------- |
| Body     | type          | string | ✅    | `FULL` / `INCREMENTAL`              |
| Body     | target_commit | string | ❌    | 指定同步到某个 commit，不传则取最新 |

**响应：**

```json
{
  "task_id": "task_090",
  "status": "RUNNING",
  "message": "增量同步已触发，目标 commit: latest"
}
```

------

### 6.5 获取同步任务详情

```
GET /api/v1/sync/tasks/{task_id}
```

| 参数位置 | 参数名  | 类型   | 必填 | 说明    |
| -------- | ------- | ------ | ---- | ------- |
| Path     | task_id | string | ✅    | 任务 ID |

**响应：** 同 6.2 中单条结构，增加 `change_details` 数组：

```json
{
  "task_id": "task_001",
  "change_details": [
    {"chunk_id":"chunk_001","file":"TransactionalMessageServiceImpl.java","change_type":"MODIFIED","rollback_source_commit":null},
    {"chunk_id":"chunk_002","file":"...","change_type":"ROLLBACK","rollback_source_commit":"e7f8a9b"},
    {"chunk_id":"chunk_003","file":"...","change_type":"RESTORED","rollback_source_commit":"e7f8a9b"}
  ]
}
```

------

### 6.6 获取变更历史（按 chunk）

```
GET /api/v1/sync/change-history/{chunk_id}
```

| 参数位置 | 参数名   | 类型   | 必填 | 说明              |
| -------- | -------- | ------ | ---- | ----------------- |
| Path     | chunk_id | string | ✅    | Chunk ID          |
| Query    | limit    | int    | ❌    | 返回条数，默认 10 |

**响应：**

```json
{
  "chunk_id": "chunk_001",
  "file": "TransactionalMessageServiceImpl.java",
  "history": [
    {"commit":"a3f8c21","change_type":"MODIFIED","date":"2026-07-22","old_hash":"abc...","new_hash":"def...","is_rollback_related":false},
    {"commit":"e7f8a9b","change_type":"MODIFIED","date":"2026-07-20","old_hash":"xyz...","new_hash":"abc...","is_rollback_related":false}
  ]
}
```

------

## 七、Agent 面板模块

### 7.1 获取 Agent 列表及状态

```
GET /api/v1/agents
```

无参数。

**响应：**

```json
{
  "agents": [
    {
      "agent_type": "CODE_UNDERSTAND",
      "name": "代码理解 Agent",
      "status": "READY",
      "today_calls": 47,
      "satisfaction_rate": 0.92,
      "description": "理解代码结构、调用关系、设计意图"
    },
    {
      "agent_type": "DOC_MAINTENANCE",
      "name": "文档维护 Agent",
      "status": "BUSY",
      "current_task": "PR #1235 生成中",
      "today_calls": 3,
      "satisfaction_rate": 0.85
    }
  ]
}
```

------

### 7.2 获取 Agent 任务历史

```
GET /api/v1/agents/{agent_type}/tasks
```

| 参数位置 | 参数名     | 类型   | 必填 | 说明       |
| -------- | ---------- | ------ | ---- | ---------- |
| Path     | agent_type | string | ✅    | Agent 类型 |
| Query    | page       | int    | ❌    | 页码       |
| Query    | page_size  | int    | ❌    | 每页条数   |

**响应：**

```json
{
  "total": 23,
  "items": [
    {
      "task_id": "agt_task_001",
      "agent_type": "DOC_MAINTENANCE",
      "trigger": "commit a3f8c21 影响 2 篇文档",
      "action": "创建 PR #1235",
      "status": "COMPLETED",
      "created_at": "2026-07-24T14:20:00Z"
    }
  ]
}
```

------

### 7.3 获取 Agent 任务详情

```
GET /api/v1/agents/tasks/{task_id}
```

| 参数位置 | 参数名  | 类型   | 必填 | 说明    |
| -------- | ------- | ------ | ---- | ------- |
| Path     | task_id | string | ✅    | 任务 ID |

**响应：**

```json
{
  "task_id": "agt_task_001",
  "agent_type": "DOC_MAINTENANCE",
  "trigger": "commit a3f8c21",
  "reasoning_steps": [
    "检测到 TransactionalMessageServiceImpl.checkLocalTransaction 被修改",
    "关联文档: 4.2 事务消息回查机制",
    "对比代码 diff 与文档内容，发现超时判断逻辑描述不一致",
    "生成文档更新 PR"
  ],
  "output": {
    "pr_url": "https://github.com/.../pull/1235",
    "affected_docs": ["4.2 事务消息回查机制"],
    "diff_summary": "更新回查超时判断条件描述"
  },
  "status": "COMPLETED",
  "created_at": "2026-07-24T14:20:00Z"
}
```

------

### 7.4 手动触发 Agent 任务

```
POST /api/v1/agents/{agent_type}/invoke
```

| 参数位置 | 参数名     | 类型   | 必填 | 说明                                     |
| -------- | ---------- | ------ | ---- | ---------------------------------------- |
| Path     | agent_type | string | ✅    | Agent 类型                               |
| Body     | input      | string | ✅    | 输入内容（代码片段/异常堆栈/文档路径等） |
| Body     | context    | object | ❌    | 附加上下文（如指定 commit、指定文件）    |

**响应：**

```json
{
  "task_id": "agt_task_024",
  "status": "RUNNING",
  "message": "缺陷诊断 Agent 已启动"
}
```

------

### 7.5 Agent 任务反馈

```
POST /api/v1/agents/tasks/{task_id}/feedback
```

| 参数位置 | 参数名  | 类型   | 必填 | 说明                      |
| -------- | ------- | ------ | ---- | ------------------------- |
| Path     | task_id | string | ✅    | 任务 ID                   |
| Body     | rating  | string | ✅    | `HELPFUL` / `NOT_HELPFUL` |
| Body     | comment | string | ❌    | 补充说明                  |

------

## 八、系统监控模块

### 8.1 获取检索性能指标

```
GET /api/v1/monitor/retrieval-perf
```

| 参数位置 | 参数名      | 类型   | 必填 | 说明                                  |
| -------- | ----------- | ------ | ---- | ------------------------------------- |
| Query    | time_range  | string | ❌    | `1h` / `24h` / `7d` / `30d`，默认 24h |
| Query    | granularity | string | ❌    | `1m` / `5m` / `1h`，默认 5m           |

**响应：**

```json
{
  "summary": {
    "avg_latency_ms": 342,
    "p95_latency_ms": 520,
    "p99_latency_ms": 890,
    "total_queries": 1247
  },
  "by_stage": {
    "recall": {"avg_ms": 45, "p95_ms": 78},
    "coarse_rank": {"avg_ms": 89, "p95_ms": 134},
    "fine_rank": {"avg_ms": 156, "p95_ms": 245},
    "llm_generation": {"avg_ms": 1850, "p95_ms": 3200}
  },
  "timeline": [
    {"timestamp":"2026-07-24T14:00:00Z","avg_ms":335,"count":52},
    {"timestamp":"2026-07-24T14:05:00Z","avg_ms":348,"count":61}
  ]
}
```

------

### 8.2 获取资源使用情况

```
GET /api/v1/monitor/resources
```

无参数。

**响应：**

```json
{
  "gpu": {
    "total_mb": 8192,
    "used_mb": 3700,
    "models_loaded": [
      {"name":"CodeBERT","memory_mb":500},
      {"name":"BGE-M3","memory_mb":1200},
      {"name":"bge-reranker-base","memory_mb":600},
      {"name":"bge-reranker-v2-m3","memory_mb":1200}
    ]
  },
  "cpu": {"usage_percent": 35},
  "memory": {"total_gb":32,"used_gb":18},
  "disk": {"milvus_gb":2.3,"es_gb":1.8,"pg_gb":0.9}
}
```

------

### 8.3 获取 API 用量统计

```
GET /api/v1/monitor/api-usage
```

| 参数位置 | 参数名     | 类型   | 必填 | 说明                                  |
| -------- | ---------- | ------ | ---- | ------------------------------------- |
| Query    | time_range | string | ❌    | `24h` / `7d` / `30d`，默认 24h        |
| Query    | provider   | string | ❌    | `deepseek` / `qwen` / `all`，默认 all |

**响应：**

```json
{
  "summary": {
    "total_calls": 127,
    "input_tokens": 450000,
    "output_tokens": 120000,
    "estimated_cost_cny": 0.69
  },
  "by_model": [
    {"model":"deepseek-chat","calls":98,"input_tokens":320000,"output_tokens":85000},
    {"model":"qwen3-32b","calls":29,"input_tokens":130000,"output_tokens":35000}
  ],
  "by_scenario": [
    {"scenario":"qa_generation","calls":85},
    {"scenario":"community_summary","calls":12},
    {"scenario":"doc_maintenance","calls":18},
    {"scenario":"rollback_detection","calls":12}
  ],
  "timeline": [
    {"timestamp":"2026-07-24T14:00:00Z","calls":8,"tokens":12000}
  ]
}
```

------

### 8.4 获取索引统计

```
GET /api/v1/monitor/index-stats
```

无参数。

**响应：**

```json
{
  "milvus": {
    "total_vectors": 12847,
    "code_vectors": 9214,
    "doc_vectors": 3633,
    "deleted_vectors": 23,
    "index_type": "IVF_FLAT",
    "dimension": 768
  },
  "elasticsearch": {
    "total_docs": 12847,
    "index_size_mb": 1843
  },
  "postgresql": {
    "chunk_relations": 8432,
    "anchor_mappings": 2156,
    "call_graph_edges": 15678,
    "change_history": 4521,
    "rollback_history": 5
  },
  "gnn": {
    "total_nodes": 6842,
    "total_edges": 18934,
    "embedding_dim": 128,
    "last_train_at": "2026-07-24T03:00:00Z"
  }
}
```

------

## 九、全局搜索模块

### 9.1 全局快捷搜索（⌘K）

```
GET /api/v1/search/global
```

| 参数位置 | 参数名 | 类型     | 必填 | 说明                                             |
| -------- | ------ | -------- | ---- | ------------------------------------------------ |
| Query    | q      | string   | ✅    | 搜索关键词                                       |
| Query    | types  | string[] | ❌    | 限定类型：`["code","doc","community","history"]` |
| Query    | limit  | int      | ❌    | 每类返回数量，默认 5                             |

**响应：**

```json
{
  "code": [
    {"id":"cls_001","name":"DefaultMQPushConsumer","type":"class","module":"client","file_path":"..."}
  ],
  "doc": [
    {"id":"doc_001","name":"5.1 Push 消费者使用指南","path":"用户指南 > 5.1","section":"5.1.2"}
  ],
  "community": [
    {"id":"com_003","name":"消息消费子系统","level":1}
  ],
  "history": [
    {"conversation_id":"conv_088","title":"push consumer 的重试机制","date":"2026-07-23"}
  ]
}
```

------

### 9.2 文档全文搜索

```
GET /api/v1/search/docs
```

| 参数位置 | 参数名    | 类型   | 必填 | 说明       |
| -------- | --------- | ------ | ---- | ---------- |
| Query    | q         | string | ✅    | 搜索关键词 |
| Query    | module    | string | ❌    | 限定模块   |
| Query    | page      | int    | ❌    | 页码       |
| Query    | page_size | int    | ❌    | 每页条数   |

**响应：**

```json
{
  "total": 15,
  "items": [
    {
      "doc_id": "doc_001",
      "title": "事务消息回查机制",
      "path": "设计文档 > 4. 事务消息 > 4.2 回查机制",
      "highlight": "...生产者收到<em>回查</em>请求后，需要检查本地事务的...",
      "score": 12.5,
      "stale": false
    }
  ]
}
```

------

## 十、用户与设置模块

### 10.1 获取系统设置

```
GET /api/v1/settings
```

无参数。

**响应：**

```json
{
  "llm": {
    "provider": "deepseek",
    "model": "deepseek-chat",
    "api_key_configured": true,
    "temperature": 0.7,
    "max_tokens": 4096
  },
  "embedding": {
    "code_model": "codebert",
    "doc_model": "bge-m3",
    "device": "cuda:0"
  },
  "retrieval": {
    "top_k_recall": 20,
    "top_k_coarse": 25,
    "top_k_fine": 8,
    "rrf_k": 60
  },
  "sync": {
    "auto_sync": true,
    "interval_minutes": 5,
    "repo_path": "/data/rocketmq"
  }
}
```

------

### 10.2 更新系统设置

```
PUT /api/v1/settings
```

| 参数位置 | 参数名    | 类型   | 必填 | 说明         |
| -------- | --------- | ------ | ---- | ------------ |
| Body     | llm       | object | ❌    | LLM 配置     |
| Body     | retrieval | object | ❌    | 检索参数配置 |
| Body     | sync      | object | ❌    | 同步配置     |

------

### 10.3 测试 API 连通性

```
POST /api/v1/settings/test-connection
```

| 参数位置 | 参数名   | 类型   | 必填 | 说明                           |
| -------- | -------- | ------ | ---- | ------------------------------ |
| Body     | provider | string | ✅    | `deepseek` / `qwen` / `openai` |
| Body     | api_key  | string | ✅    | API Key                        |
| Body     | model    | string | ✅    | 模型名                         |

**响应：**

```json
{
  "success": true,
  "latency_ms": 230,
  "message": "连接成功，模型: deepseek-chat"
}
```

------

## 十一、WebSocket 实时推送

### 11.1 连接

```
WS /ws/v1/events
```

**连接参数（Query）：**

| 参数名   | 类型   | 必填 | 说明                                    |
| -------- | ------ | ---- | --------------------------------------- |
| token    | string | ✅    | 认证 token                              |
| channels | string | ❌    | 订阅频道，逗号分隔：`sync,agent,system` |

------

### 11.2 推送事件类型

| 事件类型               | 触发时机       | 推送内容                                            |
| ---------------------- | -------------- | --------------------------------------------------- |
| `sync.started`         | 同步任务开始   | `{task_id, type, commit}`                           |
| `sync.progress`        | 同步进行中     | `{task_id, progress_percent, current_step}`         |
| `sync.completed`       | 同步完成       | `{task_id, changes_summary}`                        |
| `sync.failed`          | 同步失败       | `{task_id, error_message}`                          |
| `rollback.detected`    | 检测到回滚     | `{rollback_commit, source_commit, affected_chunks}` |
| `rollback.completed`   | 回滚恢复完成   | `{rollback_id, stats}`                              |
| `agent.task_started`   | Agent 任务开始 | `{task_id, agent_type, trigger}`                    |
| `agent.task_completed` | Agent 任务完成 | `{task_id, agent_type, output_summary}`             |
| `doc.stale_detected`   | 文档过期检测   | `{doc_id, path, reason, related_commit}`            |
| `system.gpu_alert`     | 显存告警       | `{used_mb, total_mb, message}`                      |

**推送格式：**

```json
{
  "event": "sync.completed",
  "timestamp": "2026-07-24T14:55:34Z",
  "data": {
    "task_id": "task_090",
    "type": "INCREMENTAL",
    "changes": {"added":3,"modified":5,"deleted":1},
    "duration_ms": 34000
  }
}
```

------

## 十二、接口汇总表

| #    | 方法 | 路径                                     | 说明                 |
| ---- | ---- | ---------------------------------------- | -------------------- |
| 1    | POST | `/api/v1/chat/completions`               | 提交问答（SSE 流式） |
| 2    | GET  | `/api/v1/chat/conversations`             | 会话列表             |
| 3    | GET  | `/api/v1/chat/conversations/{id}`        | 会话详情             |
| 4    | GET  | `/api/v1/chat/messages/{id}/retrieval`   | 检索详情             |
| 5    | POST | `/api/v1/chat/suggestions`               | 追问建议             |
| 6    | POST | `/api/v1/chat/messages/{id}/feedback`    | 消息反馈             |
| 7    | GET  | `/api/v1/code/tree`                      | 代码包树             |
| 8    | GET  | `/api/v1/code/classes/{id}`              | 类详情               |
| 9    | GET  | `/api/v1/code/methods/{id}`              | 方法详情             |
| 10   | GET  | `/api/v1/code/methods/{id}/source`       | 方法源码             |
| 11   | GET  | `/api/v1/code/search`                    | 代码搜索             |
| 12   | GET  | `/api/v1/graph/call-graph`               | 调用图               |
| 13   | GET  | `/api/v1/graph/code-doc-relations`       | 代码-文档关联图      |
| 14   | GET  | `/api/v1/graph/module-dependency`        | 模块依赖图           |
| 15   | GET  | `/api/v1/graph/search`                   | 图谱节点搜索         |
| 16   | GET  | ~~`/api/v1/communities`~~                | ~~社区列表~~ ❌ 已弃用(2026-07-29) |
| 17   | GET  | ~~`/api/v1/communities/{id}`~~           | ~~社区详情~~ ❌ 已弃用(2026-07-29) |
| 18   | GET  | ~~`/api/v1/communities/{id}/graph`~~     | ~~社区子图~~ ❌ 已弃用(2026-07-29) |
| 19   | POST | ~~`/api/v1/communities/{id}/ask`~~       | ~~社区提问~~ ❌ 已弃用(2026-07-29) |
| 20   | GET  | `/api/v1/sync/status`                    | 索引状态             |
| 21   | GET  | `/api/v1/sync/tasks`                     | 同步任务列表         |
| 22   | GET  | `/api/v1/sync/rollbacks`                 | 回滚历史             |
| 23   | POST | `/api/v1/sync/trigger`                   | 手动触发同步         |
| 24   | GET  | `/api/v1/sync/tasks/{id}`                | 任务详情             |
| 25   | GET  | `/api/v1/sync/change-history/{chunk_id}` | Chunk 变更历史       |
| 26   | GET  | `/api/v1/agents`                         | Agent 列表           |
| 27   | GET  | `/api/v1/agents/{type}/tasks`            | Agent 任务历史       |
| 28   | GET  | `/api/v1/agents/tasks/{id}`              | Agent 任务详情       |
| 29   | POST | `/api/v1/agents/{type}/invoke`           | 手动触发 Agent       |
| 30   | POST | `/api/v1/agents/tasks/{id}/feedback`     | Agent 反馈           |
| 31   | GET  | `/api/v1/monitor/retrieval-perf`         | 检索性能             |
| 32   | GET  | `/api/v1/monitor/resources`              | 资源使用             |
| 33   | GET  | `/api/v1/monitor/api-usage`              | API 用量             |
| 34   | GET  | `/api/v1/monitor/index-stats`            | 索引统计             |
| 35   | GET  | `/api/v1/search/global`                  | 全局搜索             |
| 36   | GET  | `/api/v1/search/docs`                    | 文档搜索             |
| 37   | GET  | `/api/v1/settings`                       | 获取设置             |
| 38   | PUT  | `/api/v1/settings`                       | 更新设置             |
| 39   | POST | `/api/v1/settings/test-connection`       | 测试连通性           |
| 40   | WS   | `/ws/v1/events`                          | 实时事件推送         |

------

## 十三、通用约定

| 项目     | 约定                                                      |
| -------- | --------------------------------------------------------- |
| 基础路径 | `/api/v1`                                                 |
| 认证     | Header `Authorization: Bearer {token}`（单用户可省略）    |
| 分页     | `page` 从 1 开始，`page_size` 默认 20，最大 100           |
| 时间格式 | ISO 8601：`2026-07-24T14:55:00Z`                          |
| 错误响应 | `{"error_code":"NOT_FOUND","message":"...","status":404}` |
| 流式响应 | `Content-Type: text/event-stream`，SSE 格式               |
| 排序     | 默认按时间倒序，支持 `sort_by` + `sort_order` 参数        |