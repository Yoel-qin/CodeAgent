> ⚠️ **架构变更（2026-07-27）**：工具清单中**图向量相关工具（路径 C `graph_vector_search`、GNN/社区图向量检索）已弃用**；图遍历类（`graph_traverse` / `get_call_chain` 等，基于 PG `call_graph`）保留。嵌入检索工具按双框架（unified/dual）理解。
>
> ⚠️ **架构变更（2026-07-29）**：随 Phase 6 GraphRAG 社区摘要整体弃用，**社区相关工具已删除**（`search_community_summaries`、`get_inter_community_edges`、`get_community_members`、`get_community_summary`、`get_all_communities`、`get_community_impact`，共 6 个，下表标 ❌）；「全局问答」Agent 一并移除。工具总数 26 → 20，Agent 9 → 8。

# CodeRAG 方案所需的 MCP 工具清单

根据方案文档中各 Agent 的工具集定义和系统能力需求，整理出以下完整的工具清单：

------

## 一、检索类工具（Retrieval Tools）

| #    | 工具名                       | 功能说明                         | 数据来源                             | 使用 Agent                                                 |
| ---- | ---------------------------- | -------------------------------- | ------------------------------------ | ---------------------------------------------------------- |
| 1    | `vector_search_code`         | 代码语义向量检索                 | Chroma/Milvus (代码 collection)      | 代码理解、缺陷诊断、代码审查、测试生成、新人引导           |
| 2    | `vector_search_doc`          | 文档语义向量检索                 | Chroma/Milvus (文档 collection)      | 代码理解、文档问答、缺陷诊断、代码审查、测试生成、新人引导 |
| 3    | ~~`vector_search_graph`~~    | ~~图向量检索~~ **❌ 已移除(2026-07-27)** | GNN graph_embedding 弃用；结构相关改用 `graph_traverse` | — |
| 4    | `bm25_search`                | BM25 关键词精确匹配              | PostgreSQL (tsvector + zhparser)     | 代码理解、文档问答、缺陷诊断                               |
| 5    | `image_search`               | 图片语义检索（通过描述文本嵌入） | Chroma (image chunk)                 | 文档问答                                                   |
| 6    | `table_search`               | 表格语义检索（通过描述文本嵌入） | Chroma (table chunk)                 | 文档问答                                                   |
| 7    | ~~`search_community_summaries`~~ | ~~社区摘要向量检索~~ **❌ 已移除(2026-07-29)** | GraphRAG 社区摘要整体弃用（Phase 6） | — |

------

## 二、图遍历类工具（Graph Traversal Tools）

| #    | 工具名                      | 功能说明                        | 数据来源                        | 使用 Agent                                       |
| ---- | --------------------------- | ------------------------------- | ------------------------------- | ------------------------------------------------ |
| 8    | `graph_traverse`            | 从种子节点 BFS 扩展 N 层邻居    | Neo4j / PG call_graph           | 代码理解、缺陷诊断、测试生成、新人引导           |
| 9    | `get_call_chain`            | 递归查询完整调用链（上游/下游） | PG call_graph (递归 CTE)        | 代码理解、变更影响、缺陷诊断、代码审查、测试生成 |
| 10   | `get_downstream_callers`    | 获取下游被调用方法              | PG call_graph                   | 变更影响                                         |
| 11   | `get_upstream_callers`      | 获取上游调用方                  | PG call_graph                   | 变更影响                                         |
| 12   | ~~`get_inter_community_edges`~~ | ~~获取跨社区的调用边~~ **❌ 已移除(2026-07-29)** | GraphRAG 社区摘要整体弃用（Phase 6） | — |

------

## 三、关联查询类工具（Relation Query Tools）

| #    | 工具名                  | 功能说明                   | 数据来源                         | 使用 Agent                   |
| ---- | ----------------------- | -------------------------- | -------------------------------- | ---------------------------- |
| 13   | `get_related_docs`      | 获取代码关联的文档段落     | PG chunk_relations (CODE_TO_DOC) | 代码理解、测试生成           |
| 14   | `get_affected_docs`     | 获取变更影响的文档         | PG anchor_mappings               | 变更影响                     |
| 15   | `get_javadoc`           | 获取方法的 Javadoc 注释    | PG code_chunks                   | 代码理解                     |
| 16   | ~~`get_community_members`~~ | ~~获取社区内所有节点~~ **❌ 已移除(2026-07-29)** | GraphRAG 社区摘要整体弃用（Phase 6） | — |
| 17   | ~~`get_community_summary`~~ | ~~获取社区摘要~~ **❌ 已移除(2026-07-29)** | GraphRAG 社区摘要整体弃用（Phase 6） | — |
| 18   | ~~`get_all_communities`~~   | ~~获取所有社区列表（按层级）~~ **❌ 已移除(2026-07-29)** | GraphRAG 社区摘要整体弃用（Phase 6） | — |
| 19   | ~~`get_community_impact`~~  | ~~评估变更的社区级影响~~ **❌ 已移除(2026-07-29)** | GraphRAG 社区摘要整体弃用（Phase 6） | — |

------

## 四、变更历史类工具（Change History Tools）

| #    | 工具名               | 功能说明                    | 数据来源                    | 使用 Agent         |
| ---- | -------------------- | --------------------------- | --------------------------- | ------------------ |
| 20   | `get_recent_changes` | 获取文件/方法的最近变更记录 | PG change_history           | 缺陷诊断、代码审查 |
| 21   | `detect_stale_docs`  | 检测过期文档（锚点失效）    | PG stale_anchors            | 文档维护           |
| 22   | `mark_stale_anchors` | 标记锚点为失效状态          | PG anchor_mappings (UPDATE) | 变更影响           |

------

## 五、精排类工具（Reranking Tools）

| #    | 工具名   | 功能说明                                         | 数据来源                      | 使用 Agent                                                 |
| ---- | -------- | ------------------------------------------------ | ----------------------------- | ---------------------------------------------------------- |
| 23   | `rerank` | 对候选结果进行精排（Cross-Encoder + 图特征融合） | bge-reranker-v2-m3 (本地 GPU) | 代码理解、文档问答、缺陷诊断、代码审查、测试生成、新人引导 |

------

## 六、生成类工具（Generation Tools）

| #    | 工具名                | 功能说明                     | 数据来源          | 使用 Agent |
| ---- | --------------------- | ---------------------------- | ----------------- | ---------- |
| 24   | `generate_doc_update` | 调用 LLM 生成文档更新内容    | DeepSeek/Qwen API | 文档维护   |
| 25   | `create_doc_pr`       | 调用 Git API 创建文档更新 PR | Git API           | 文档维护   |

------

## 七、Neo4j 图查询工具（Graph Database Tools）

| #    | 工具名                 | 功能说明                             | 数据来源 | 使用 Agent       |
| ---- | ---------------------- | ------------------------------------ | -------- | ---------------- |
| 26   | `neo4j_query` (Cypher) | 执行 Cypher 查询（调用图、社区检测） | Neo4j 5  | graph_agent 子图 |

------

## 八、工具与 Agent 的映射关系

```
┌─────────────────────────────────────────────────────────────────┐
│                        路由 Agent (Orchestrator)                  │
│   工具: intent_classify (意图识别，使用 with_structured_output)   │
└────┬────────┬────────┬────────┬────────┬────────┬────────┬──────┘
     │        │        │        │        │        │        │
     ▼        ▼        ▼        ▼        ▼        ▼        ▼
┌────────┐┌────────┐┌────────┐┌────────┐┌────────┐┌────────┐┌────────┐
│代码理解 ││文档问答 ││变更影响 ││缺陷诊断 ││代码审查 ││测试生成 ││文档维护 │
│        ││        ││        ││        ││        ││        ││        │
│工具:   ││工具:   ││工具:   ││工具:   ││工具:   ││工具:   ││工具:   │
│#1,2,3  ││#2,5,6  ││#3,9-12 ││#1-4,8  ││#1-3,9  ││#1,2,8  ││#21,17  │
│#4,8,9  ││#4,13   ││#14,19  ││#9,20   ││#20,23  ││#9,13   ││#24,25  │
│#13,15  ││#23     ││#22     ││#23     ││        ││#23     ││        │
│#23     ││        ││        ││        ││        ││        ││        │
└────────┘└────────┘└────────┘└────────┘└────────┘└────────┘└────────┘
                                                              ┌────────┐
                                                              │全局问答 │
                                                              │工具:   │
                                                              │#7,16-18│
                                                              │#12     │
                                                              └────────┘
```

------

## 九、工具实现技术栈

| 工具类别      | 实现方式                                | 对应文件                                          |
| ------------- | --------------------------------------- | ------------------------------------------------- |
| 向量检索      | `BaseRetriever` 子类 + Chroma           | `retrievers/chroma_retriever.py`                  |
| BM25 检索     | 自定义 Retriever + PG tsvector          | `retrievers/pg_bm25_retriever.py`                 |
| 混合检索      | HybridRetriever (RRF 融合)              | `retrievers/hybrid_retriever.py`                  |
| 图遍历        | Neo4j Driver / PG 递归 CTE              | `tools/neo4j_query.py`                            |
| 精排          | sentence-transformers CrossEncoder      | `tools/reranker.py`                               |
| LLM 生成      | `@tool` + ChatOpenAI (langchain-openai) | 各 Agent 内部                                     |
| 图片/表格检索 | 自定义 Retriever + content_type 过滤    | `tools/image_search.py` / `tools/table_search.py` |

------

## 十、总结

整个方案共需要 **26 个核心 MCP 工具**，按功能分为 7 大类：

| 类别       | 数量 | 核心能力                                     |
| ---------- | ---- | -------------------------------------------- |
| 检索类     | 6    | 多路召回（向量 + BM25 + 图遍历 + 图片/表格） |
| 图遍历类   | 5    | 调用链追溯、BFS 扩展、跨社区边               |
| 关联查询类 | 7    | 代码-文档关联、社区信息、锚点映射            |
| 变更历史类 | 3    | 变更追溯、文档腐化检测                       |
| 精排类     | 1    | Cross-Encoder + 图特征融合排序               |
| 生成类     | 2    | 文档更新生成、PR 创建                        |
| 图数据库类 | 1    | Cypher 查询                                  |

这些工具通过 LangChain 的 `@tool` 装饰器定义，在 LangGraph 子图中被各 Agent 节点调用，最终通过 FastAPI + SSE 对外暴露服务。