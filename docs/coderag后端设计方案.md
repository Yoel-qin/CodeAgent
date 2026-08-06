> ⚠️ **架构变更（2026-07-27）** — 本设计文档部分内容已被后续决策取代，以 [`开发清单.md`](./开发清单.md) / [`项目状态.md`](./项目状态.md) / [`待确认问题清单.md`](./待确认问题清单.md) 为准：
> 1. **嵌入改为可切换双框架**：`EMBEDDING_STRATEGY=unified`（代码+文档统一 BGE-M3 1024d，单 collection）或 `=dual`（[方案一](./嵌入向量方案.md)：代码 CodeBERT 768d 本地 `model_server` + 文档 BGE-M3 1024d，双 collection `code_vectors`/`doc_vectors`，统一 reranker 重排）。→ §7 模型表、§7.4 collection schema（`embedding` 维度按 kind、删 `graph_embedding` 字段）以此为准。
> 2. **图向量整体弃用**：§8（图向量与 GraphRAG 设计）、§11 路径 C（图向量检索）、RRF 权重 `graph_vec:0.9`、GNN/R-GCN、`graph_embeddings.graph_embedding` 向量列 **均已移除**；保留**图遍历（路径 D，PG `call_graph` BFS）**。**GraphRAG 社区摘要（Phase 6）亦于 2026-07-29 整体弃用并删除**：`graph_communities` / `node_community_mapping` 两表 + `graph_embeddings.community_id_l0/l1/l2` 列 + 前端「社区总览」页 + Phase 7 社区工具与「全局问答」Agent 一并移除（见 `开发清单.md` §Phase 6）；社区功能从未写入数据。`graph_embeddings` 表（pagerank/degree/betweenness 通用结构特征）保留。
>    - **残留声明**：本文档 ASCII 架构图/数据流图（§2.1）、§5 元数据表、§10 ER 图、§12.2 部署图、目录、§15 Agent 场景步骤、§8/§10.10-12/§11 中仍出现的「图向量 / GNN / graph_embedding / R-GCN / 社区 / GraphRAG / graph_communities / node_community_mapping / community_id」字样，均为历史设计示意，**一律按本横幅视为已弃用**（为避免破坏 ASCII 对齐未逐字删除）；以本横幅与 `docs/项目状态.md` 为准。
> 3. **Stage0 增加 LLM 查询改写**（§11.2）：规则分词 + LLM 改写，失败降级。
> 4. 部署：后端/前端/model_server 本地 host 跑，Docker 只跑 infra（非本文「全部 Docker」描述）。

# 项目代码智能知识库 RAG 系统 — 完整技术方案（基础设施放在Docker）

------

## 目录

1. [项目概述与目标](https://www.qianwen.com/chat/e2af7ae52e384e11a9d11c0207fa70bb?ch=tongyi_redirect#1-项目概述与目标)
2. [整体架构设计](https://www.qianwen.com/chat/e2af7ae52e384e11a9d11c0207fa70bb?ch=tongyi_redirect#2-整体架构设计)
3. [数据解析](https://www.qianwen.com/chat/e2af7ae52e384e11a9d11c0207fa70bb?ch=tongyi_redirect#3-数据解析)
4. [切片策略](https://www.qianwen.com/chat/e2af7ae52e384e11a9d11c0207fa70bb?ch=tongyi_redirect#4-切片策略)
5. [元数据设计](https://www.qianwen.com/chat/e2af7ae52e384e11a9d11c0207fa70bb?ch=tongyi_redirect#5-元数据设计)
6. [元数据关联机制](https://www.qianwen.com/chat/e2af7ae52e384e11a9d11c0207fa70bb?ch=tongyi_redirect#6-元数据关联机制)
7. [数据向量化](https://www.qianwen.com/chat/e2af7ae52e384e11a9d11c0207fa70bb?ch=tongyi_redirect#7-数据向量化)
8. [图向量与 GraphRAG 设计](https://www.qianwen.com/chat/e2af7ae52e384e11a9d11c0207fa70bb?ch=tongyi_redirect#8-图向量与-graphrag-设计)
9. [存储架构设计](https://www.qianwen.com/chat/e2af7ae52e384e11a9d11c0207fa70bb?ch=tongyi_redirect#9-存储架构设计)
10. [关系数据表结构设计](https://www.qianwen.com/chat/e2af7ae52e384e11a9d11c0207fa70bb?ch=tongyi_redirect#10-关系数据表结构设计)
11. [三阶段检索管道](https://www.qianwen.com/chat/e2af7ae52e384e11a9d11c0207fa70bb?ch=tongyi_redirect#11-三阶段检索管道)
12. [召回精排模型设计](https://www.qianwen.com/chat/e2af7ae52e384e11a9d11c0207fa70bb?ch=tongyi_redirect#12-召回精排模型设计)
13. [增量更新流程](https://www.qianwen.com/chat/e2af7ae52e384e11a9d11c0207fa70bb?ch=tongyi_redirect#13-增量更新流程)
14. [多 Agent 协作体系](https://www.qianwen.com/chat/e2af7ae52e384e11a9d11c0207fa70bb?ch=tongyi_redirect#14-多-agent-协作体系)
15. [各 Agent 场景详细说明](https://www.qianwen.com/chat/e2af7ae52e384e11a9d11c0207fa70bb?ch=tongyi_redirect#15-各-agent-场景详细说明)
16. [整体流程总结](https://www.qianwen.com/chat/e2af7ae52e384e11a9d11c0207fa70bb?ch=tongyi_redirect#16-整体流程总结)
17. [实施路线图](https://www.qianwen.com/chat/e2af7ae52e384e11a9d11c0207fa70bb?ch=tongyi_redirect#17-实施路线图)

------

## 1. 项目概述与目标

### 1.1 项目背景

* ***是一个大型项目，拥有数十万行代码和大量技术文档。开发者在日常工作中面临以下痛点：

- 代码逻辑复杂，新人上手困难
- 文档与代码脱节，文档腐化严重
- 修改代码后不清楚影响范围
- 排查线上问题需要跨多个模块追踪调用链
- 代码审查缺乏项目上下文
- 无法回答架构级全局性问题

### 1.2 系统目标

构建一套基于 RAG（检索增强生成）的智能知识库系统，融合双框架语义向量（unified BGE-M3 / dual 方案一 CodeBERT+BGE-M3）与三阶段精排，实现：

| 目标          | 说明                                       |
| ------------- | ------------------------------------------ |
| 代码语义理解  | 用自然语言查询代码实现                     |
| 文档智能问答  | 基于项目文档回答技术问题                   |
| 代码-文档关联 | 建立代码实现与设计文档的双向映射           |
| 结构感知检索  | 通过图遍历（call_graph BFS）理解代码在调用链中的结构角色 |
| 精准排序      | 三阶段检索管道确保最终结果精准             |
| 全局问答      | 通过 GraphRAG 社区摘要回答架构级问题       |
| 变更感知      | 代码变更后自动识别受影响的文档和下游调用   |
| 多 Agent 协作 | 支撑代码审查、测试生成、缺陷诊断等高级场景 |

### 1.3 技术选型总览

| 组件         | 选型                                | 理由                                 |
| ------------ | ----------------------------------- | ------------------------------------ |
| 代码解析     | Tree-sitter (Java grammar)          | 增量解析、AST 级别精确               |
| 文档解析     | Markdown AST (remark/unified)       | 保留标题层级结构                     |
| 向量数据库   | Milvus                              | 支持标量过滤 + ANN 检索 + 多向量字段 |
| 关系数据库   | PostgreSQL                          | 支持 JSONB、递归 CTE、GIN 索引       |
| 全文检索     | Elasticsearch                       | BM25 关键词精确匹配                  |
| 代码嵌入模型 | CodeBERT (dual) / BGE-M3 (unified)  | 代码语义专用（双框架可切换）         |
| 文档嵌入模型 | BGE-M3                              | 中英文混合文档（unified/dual 文档侧）|
| 粗排模型     | bge-reranker-v2-m3（粗排留空）      | 轻量快速（硅基流动无 base，单阶段）  |
| 精排模型     | bge-reranker-v2-m3 / Qwen3-Reranker | 高精度 Cross-Encoder                 |
| LLM          | GPT-4 / Claude / DeepSeek           | 生成回答                             |
| Agent 框架   | LangGraph                           | 多 Agent 编排                        |

------

## 2. 整体架构设计

### 2.1 系统分层架构

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              应用层 (Agent Layer)                                │
│                                                                                 │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────┐  │
│  │代码理解   │ │文档问答   │ │变更影响    │ │缺陷诊断    │ │代码审查   │ │测试生成 │  │
│  │ Agent    │ │ Agent    │ │ Agent    │ │ Agent    │ │ Agent    │ │ Agent  │  │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────┘ └────────┘  │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐                                       │
│  │文档维护   │ │新人引导    │ │全局问答   │                                       │
│  │ Agent    │ │ Agent    │ │ Agent    │                                       │
│  └──────────┘ └──────────┘ └──────────┘                                       │
└────────────────────────────────────┬────────────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              编排层 (Orchestrator)                               │
│                                                                                 │
│  路由 Agent: 意图识别 → 任务分解 → 分发 → 汇总                                   │
└────────────────────────────────────┬────────────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                         检索层 (三阶段检索管道)                                   │
│                                                                                 │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │ Stage 1: 多路召回                                                        │   │
│  │ ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌────────────────────┐    │   │
│  │ │向量语义检索 │  │BM25关键词   │ │ 图向量检索   │ │图遍历召回            │    │   │
│  │ │(Milvus)   │  │(ES)        │ │ (GNN Embed)│ │(Call Graph BFS)    │    │   │
│  │ └────────────┘ └────────────┘ └────────────┘ └────────────────────┘    │   │
│  │                    → RRF 融合去重 → 候选集 60~80 条                      │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │ Stage 2: 粗排 (ColBERT / bge-reranker-base)                              │   │
│  │                    → Top-20~30                                           │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │ Stage 3: 精排 (bge-reranker-v2-m3 + 图特征融合 + LTR)                    │   │
│  │                    → 最终 Top-5~10                                       │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
│                                                                                 │
│  ┌────────────────┐  ┌────────────────┐  ┌────────────────┐  ┌──────────────┐  │
│  │ 关联关系查询     │  │ 调用图遍历       │  │ 变更历史查询     │  │ 社区摘要查询  │  │
│  │ (PG Relations) │  │ (PG Call Graph)│  │ (PG History)   │  │ (PG Community)│ │
│  └────────────────┘  └────────────────┘  └────────────────┘  └──────────────┘  │
└────────────────────────────────────┬────────────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              存储层 (Storage Layer)                              │
│                                                                                 │
│  ┌─────────────────────────────┐  ┌─────────────────────────────────────────┐  │
│  │ Milvus (向量库)              │  │ PostgreSQL (关系库)                      │  │
│  │ • 语义嵌入向量                │  │ • 完整元数据                            │  │
│  │ • 图嵌入向量                  │  │ • 关联关系                              │  │
│  │ • content (随路数据)          │  │ • 调用图                                │  │
│  │ • 检索过滤字段                │  │ • 变更历史                              │  │
│  │                             │  │ • 图嵌入 + 图结构特征                    │  │
│  │                             │  │ • 社区摘要                              │  │
│  │                             │  │ • 同步任务管理                           │  │
│  └─────────────────────────────┘  └─────────────────────────────────────────┘  │
│                                                                                 │
│  ┌─────────────────────────────┐  ┌─────────────────────────────────────────┐  │
│  │ Elasticsearch (全文索引)     │  │ 图计算引擎 (DGL/PyG)                    │  │
│  │ • 代码全文索引               │  │ • GNN 模型训练与推理                     │  │
│  │ • 文档全文索引               │  │ • 社区检测 (Leiden)                      │  │
│  │ • BM25 检索                 │  │ • 图遍历 (BFS/DFS)                      │  │
│  └─────────────────────────────┘  └─────────────────────────────────────────┘  │
└────────────────────────────────────┬────────────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              数据管道层 (Pipeline Layer)                          │
│                                                                                 │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────────┐  │
│  │ 代码解析  │  │ 文档解析  │  │ 切片引擎  │  │ 向量化    │  │ 增量更新 (Git)   │  │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘  └──────────────────┘  │
│  ┌──────────┐  ┌──────────┐  ┌──────────────────────────────────────────────┐  │
│  │ 图构建    │  │ GNN 推理  │  │ 社区检测 + 摘要生成                          │  │
│  └──────────┘  └──────────┘  └──────────────────────────────────────────────┘  │
└────────────────────────────────────┬────────────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              数据源层 (Data Source)                              │
│                                                                                 │
│  ┌─────────────────────────────┐  ┌─────────────────────────────────────────┐  │
│  │ 源代码 				       │  │ 	文档 (Markdown/HTML)           │  │
│  │ • broker / client / namesrv │  │ • 设计文档 / 用户指南 / FAQ             │  │
│  │ • remoting / store / acl    │  │ • 最佳实践 / 运维手册                   │  │
│  └─────────────────────────────┘  └─────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### 2.2 数据流全景

```
源代码 (.java)                    文档 (.md)
     │                                │
     ▼                                ▼
┌──────────┐                    ┌──────────┐
│ Tree-sitter│                   │ Markdown │
│ AST 解析  │                    │ AST 解析 │
└─────┬────┘                    └─────┬────┘
      │                               │
      ▼                               ▼
┌──────────┐                    ┌──────────┐
│ 代码切片  │                    │ 文档切片  │
│ (方法级)  │                    │ (章节级)  │
└─────┬────┘                    └─────┬────┘
      │                               │
      ▼                               ▼
┌──────────┐                    ┌──────────┐
│ 元数据提取│                    │ 元数据提取│
│ + 锚点   │                    │ + 锚点   │
└─────┬────┘                    └─────┬────┘
      │                               │
      └───────────────┬───────────────┘
                      │
                      ▼
              ┌──────────────┐
              │  关联构建     │
              │ (锚点匹配)   │
              └──────┬───────┘
                     │
          ┌──────────┼──────────┐
          ▼          ▼          ▼
   ┌────────────┐ ┌────────┐ ┌──────────────┐
   │ 语义向量化  │ │图构建   │ │ 全文索引构建  │
   │ → Milvus   │ │→ GNN  │ │ → ES         │
   └────────────┘ └───┬────┘ └──────────────┘
                      │
                      ▼
              ┌──────────────┐
              │ GNN 推理      │
              │ → 图嵌入      │
              │ → Milvus     │
              └──────┬───────┘
                     │
                     ▼
              ┌──────────────┐
              │ 社区检测      │
              │ + 摘要生成    │
              │ → PostgreSQL │
              └──────────────┘
```

------

## 3. 数据解析

### 3.1 代码解析

#### 3.1.1 解析工具选择

| 方案        | 优点                     | 缺点                | 选择   |
| ----------- | ------------------------ | ------------------- | ------ |
| JavaParser  | Java 原生，功能完整      | 仅支持 Java，速度慢 | 备选   |
| Tree-sitter | 增量解析、多语言、速度快 | 需要额外 grammar    | ✅ 推荐 |
| Eclipse JDT | 类型推断完整             | 重量级、启动慢      | 不选   |
| 正则表达式  | 简单                     | 无法处理嵌套结构    | 不选   |

#### 3.1.2 解析提取的信息

从每个 Java 文件中提取以下结构化信息：

| 层级   | 提取内容                                                     |
| ------ | ------------------------------------------------------------ |
| 文件级 | 包名、导入列表、文件路径、模块归属                           |
| 类级   | 类名、访问修饰符、继承关系、实现接口、类注解、类 Javadoc     |
| 方法级 | 方法名、完整签名、参数列表、返回类型、方法注解、方法 Javadoc、起止行号 |
| 语句级 | 方法调用表达式、被调用方法名、调用行号                       |

#### 3.1.3 解析输出结构

每个方法解析后产出一个结构化对象，包含：

- **定位信息**：文件路径、包名、类名、方法名、起止行号
- **语义信息**：Javadoc 注释、方法注解、行内注释
- **结构信息**：访问修饰符、返回类型、参数列表、泛型参数
- **关系信息**：实现的接口、继承的父类、调用的其他方法列表
- **锚点标识**：`ClassName.methodName` 格式的唯一标识

### 3.2 文档解析

#### 3.2.1 解析策略

RocketMQ 文档主要为 Markdown 格式，解析时保留以下结构：

| 层级   | 提取内容                                                     |
| ------ | ------------------------------------------------------------ |
| 文档级 | 文件路径、文档标题、文档类型                                 |
| 章节级 | 标题路径（如 "3. 事务消息 > 3.2 回查机制"）、标题层级、章节顺序 |
| 内容级 | 段落文本、代码块、列表、表格                                 |
| 锚点级 | 文档中引用的代码标识（如 `CODE_ANCHOR: checkLocalTransaction`） |

#### 3.2.2 标题路径保留

标题路径是文档切片的核心定位信息，格式为数组：

```
["3. 事务消息", "3.2 事务消息回查机制", "3.2.1 回查流程"]
```

作用：

- 检索时告诉用户"答案出自哪个章节"
- 构建文档的层级树结构
- 支持按章节范围过滤检索

#### 3.2.3 代码锚点识别

文档中通过特殊标记引用代码：

```markdown
<!-- CODE_ANCHOR: TransactionalMessageServiceImpl.checkLocalTransaction -->
事务消息的回查逻辑由 `checkLocalTransaction` 方法实现...
```

解析时提取所有 `CODE_ANCHOR` 标记，建立文档段落与代码方法的映射关系。

------

## 4. 切片策略

### 4.1 代码切片策略

#### 4.1.1 切片粒度

| 切片类型 | 粒度           | 适用场景                  | 示例             |
| -------- | -------------- | ------------------------- | ---------------- |
| 文件级   | 整个文件       | 小文件（< 200行）、配置类 | 枚举类、常量类   |
| 类级     | 整个类         | 中型类（< 500行）         | 工具类、DTO      |
| 方法级   | 单个方法       | 大型类的各个方法          | Service 类的方法 |
| 块级     | 方法内的逻辑块 | 超长方法（> 100行）       | 复杂算法方法     |

#### 4.1.2 切片规则

**规则一：方法级为默认粒度**

大多数 Java 类按方法切分，每个方法（含 Javadoc）为一个独立 chunk。

**规则二：小文件合并**

文件总行数 < 200 行时，整个文件作为一个 chunk，避免过度碎片化。

**规则三：超长方法二次切分**

方法体超过 100 行时，按逻辑块（if/for/try 等）进行二次切分，每个块附带方法签名作为上下文前缀。

**规则四：保留上下文前缀**

每个方法级 chunk 的头部附加类级上下文：

```
// 文件: broker/src/main/java/.../TransactionalMessageServiceImpl.java
// 类: TransactionalMessageServiceImpl implements TransactionalMessageService
// 方法:
public OperationResult checkLocalTransaction(...) {
    // 方法体...
}
```

#### 4.1.3 切片大小控制

| 参数          | 值   | 说明                 |
| ------------- | ---- | -------------------- |
| 最大 token 数 | 1024 | 超过则触发二次切分   |
| 最小 token 数 | 50   | 低于则与相邻方法合并 |
| 重叠 token 数 | 128  | 二次切分时相邻块重叠 |

### 4.2 文档切片策略

#### 4.2.1 切片粒度

以**标题章节**为基本切片单位：

| 切片类型   | 规则                                         | 示例               |
| ---------- | -------------------------------------------- | ------------------ |
| H2 章节    | 一个 H2 标题下的所有内容为一个 chunk         | "## 3. 事务消息"   |
| H3 子章节  | 如果 H2 内容过长（> 1500 token），按 H3 拆分 | "### 3.2 回查机制" |
| 代码块独立 | 文档中的大段代码块（> 20行）独立为 chunk     | 示例代码           |

#### 4.2.2 切片规则

**规则一：标题层级切分**

优先按 H2 切分，H2 内容超过 1500 token 时按 H3 进一步切分。

**规则二：保留标题路径**

每个 chunk 携带完整的标题路径数组，用于定位和展示。

**规则三：代码块处理**

- 行内代码（< 5行）：保留在段落 chunk 中
- 独立代码块（≥ 5行）：独立为 chunk，附带前后段落作为上下文

**规则四：表格保留完整性**

Markdown 表格不拆分，整个表格作为一个 chunk 或附属于所在章节。

#### 4.2.3 切片大小控制

| 参数          | 值           | 说明                          |
| ------------- | ------------ | ----------------------------- |
| 最大 token 数 | 1500         | 超过则按子标题拆分            |
| 最小 token 数 | 100          | 低于则与相邻章节合并          |
| 重叠策略      | 标题路径继承 | 子 chunk 继承父标题作为上下文 |

### 4.3 切片 ID 命名规范

| 类型       | 格式                                    | 示例                                |
| ---------- | --------------------------------------- | ----------------------------------- |
| 代码 chunk | `code_{ClassName}_{methodName}_{hash8}` | `code_TxMsgSvc_checkLocal_a1b2c3d4` |
| 文档 chunk | `doc_{fileHash8}_{sectionSeq}`          | `doc_e5f6g7h8_3_2_1`                |

------

## 5. 元数据设计

### 5.1 代码 Chunk 元数据

| 字段                 | 类型       | 说明                     | 用途           |
| -------------------- | ---------- | ------------------------ | -------------- |
| chunk_id             | String     | 全局唯一标识             | 主键，关联两库 |
| file_path            | String     | 文件相对路径             | 定位源文件     |
| module_name          | String     | 模块名 (broker/client等) | 按模块过滤     |
| package_name         | String     | Java 包名                | 按包过滤       |
| chunk_type           | Enum       | file/class/method/block  | 区分切片粒度   |
| class_name           | String     | 类名                     | 检索过滤       |
| method_name          | String     | 方法名                   | 检索过滤       |
| method_signature     | String     | 完整方法签名             | 精确匹配       |
| access_modifier      | String     | public/private/protected | 过滤           |
| return_type          | String     | 返回类型                 | 语义理解       |
| start_line           | Integer    | 起始行号                 | 定位           |
| end_line             | Integer    | 结束行号                 | 定位           |
| content              | Text       | 切片原始代码文本         | 返回给 LLM     |
| content_hash         | String(64) | 内容 SHA256              | 变更检测       |
| javadoc              | Text       | Javadoc 注释             | 语义增强       |
| inline_comments      | JSON       | 行内注释列表             | 语义增强       |
| annotations          | JSON       | Java 注解列表            | 识别特殊行为   |
| implements_interface | String     | 实现的接口               | 调用图构建     |
| extends_class        | String     | 继承的父类               | 调用图构建     |
| type_parameters      | JSON       | 泛型参数                 | 完整签名       |
| linked_doc_ids       | JSON       | 关联的文档 chunk_id 列表 | 快速关联查询   |
| code_anchor_key      | String     | "ClassName.methodName"   | 锚点匹配       |
| git_commit_hash      | String(40) | 最后修改的 commit        | 版本追溯       |
| git_commit_time      | Timestamp  | 提交时间                 | 时间排序       |
| is_deleted           | Boolean    | 是否已删除               | 软删除标记     |
| keywords             | JSON       | 提取的关键词             | BM25 混合检索  |
| token_count          | Integer    | token 数量               | 切片质量控制   |
| embedding_synced     | Boolean    | 是否已同步到向量库       | 一致性补偿     |

### 5.2 文档 Chunk 元数据

| 字段               | 类型       | 说明                     | 用途          |
| ------------------ | ---------- | ------------------------ | ------------- |
| chunk_id           | String     | 全局唯一标识             | 主键          |
| file_path          | String     | 文档文件路径             | 定位源文件    |
| title              | String     | 文档标题                 | 展示          |
| heading_path       | JSON       | 标题路径数组             | 章节定位      |
| heading_level      | Integer    | 标题层级 (2/3/4)         | 过滤          |
| section_order      | Integer    | 在文档中的顺序           | 排序          |
| content            | Text       | 段落原文                 | 返回给 LLM    |
| content_hash       | String(64) | 内容 SHA256              | 变更检测      |
| token_count        | Integer    | token 数量               | 质量控制      |
| code_anchors       | JSON       | 引用的代码锚点列表       | 关联构建      |
| linked_code_ids    | JSON       | 关联的代码 chunk_id 列表 | 快速关联查询  |
| inline_code_blocks | JSON       | 文档中的内联代码块       | 代码佐证      |
| stale_anchors      | JSON       | 已失效的锚点             | 文档腐化检测  |
| git_commit_hash    | String(40) | 最后修改的 commit        | 版本追溯      |
| is_deleted         | Boolean    | 是否已删除               | 软删除        |
| keywords           | JSON       | 关键词                   | BM25 混合检索 |
| embedding_synced   | Boolean    | 是否已同步到向量库       | 一致性补偿    |

### 5.3 元数据存储位置

**核心原则：PostgreSQL 是"真相源"，Milvus 是"检索加速层"。**

| 数据           | Milvus (向量库)      | PostgreSQL (关系库) | Elasticsearch    |
| -------------- | -------------------- | ------------------- | ---------------- |
| 语义嵌入向量   | ✅ 唯一存储           | ❌ 不存              | ❌                |
| 图嵌入向量     | ✅ 唯一存储           | ✅ 可选备份          | ❌                |
| content (原文) | ✅ 存（检索随路数据） | ✅ 存（权威副本）    | ✅ 存（全文索引） |
| 检索过滤字段   | ✅ 存（过滤用）       | ✅ 存                | ✅ 存             |
| 完整元数据     | ❌ 不存               | ✅ 唯一存储          | ❌                |
| 关联关系       | ❌ 不存               | ✅ 唯一存储          | ❌                |
| 调用图         | ❌ 不存               | ✅ 唯一存储          | ❌                |
| 变更历史       | ❌ 不存               | ✅ 唯一存储          | ❌                |
| 社区摘要       | ❌                    | ✅ 唯一存储          | ❌                |
| 图结构特征     | 部分（过滤用）       | ✅ 完整存储          | ❌                |

**冗余存 content 的原因：**

- 向量库存 content：检索命中后直接返回，避免跨库查询延迟
- PostgreSQL 存 content：增量更新对比、变更历史回溯、事务一致性保障
- Elasticsearch 存 content：BM25 全文检索

------

## 6. 元数据关联机制

### 6.1 关联类型

| 关系类型           | 方向        | 说明                     | 建立方式                  |
| ------------------ | ----------- | ------------------------ | ------------------------- |
| DOC_TO_CODE        | 文档 → 代码 | 文档段落引用了某代码方法 | 通过 CODE_ANCHOR 精确匹配 |
| CODE_TO_DOC        | 代码 → 文档 | 代码方法被哪些文档引用   | DOC_TO_CODE 的反向        |
| CODE_CALLS_CODE    | 代码 → 代码 | 方法 A 调用了方法 B      | AST 解析调用表达式        |
| CODE_IMPLEMENTS    | 代码 → 代码 | 类实现了某接口           | AST 解析 implements       |
| CODE_EXTENDS       | 代码 → 代码 | 类继承了某父类           | AST 解析 extends          |
| DOC_REFERENCES_DOC | 文档 → 文档 | 文档间的交叉引用         | 解析文档内链接            |
| CO_FILE            | 代码 → 代码 | 同文件内的方法           | 文件解析时自动建立        |
| CO_MODULE          | 代码 → 代码 | 同模块内的类             | 模块解析时自动建立        |

### 6.2 锚点匹配机制

#### 6.2.1 锚点格式

```
CODE_ANCHOR: ClassName.methodName
CODE_ANCHOR: ClassName.methodName(ParamType1, ParamType2)
```

#### 6.2.2 匹配流程

```
文档解析阶段:
  1. 扫描文档中的 CODE_ANCHOR 标记
  2. 提取锚点标识: "TransactionalMessageServiceImpl.checkLocalTransaction"
  3. 记录: doc_chunk_id + anchor_key

代码解析阶段:
  1. 为每个方法生成 anchor_key: "ClassName.methodName"
  2. 写入 code_chunks.code_anchor_key

关联构建阶段:
  1. 用 anchor_key 做精确匹配 (JOIN)
  2. 匹配成功 → 创建 chunk_relations 记录
  3. 匹配失败 → 标记为 stale_anchor (代码可能已重命名/删除)
```

#### 6.2.3 模糊匹配补充

精确匹配之外，还通过以下方式补充关联：

| 方式                | 说明                                     | 置信度 |
| ------------------- | ---------------------------------------- | ------ |
| 精确锚点匹配        | CODE_ANCHOR 标记                         | 1.0    |
| 类名+方法名文本匹配 | 文档中出现的 `ClassName.methodName` 文本 | 0.9    |
| 方法名语义匹配      | 文档描述与代码 Javadoc 的语义相似度      | 0.7    |
| 同文件/同模块关联   | 文档章节与同模块代码的弱关联             | 0.5    |

### 6.3 调用图构建

#### 6.3.1 构建方式

通过 AST 解析每个方法体中的方法调用表达式：

```
方法 A 的代码中发现: this.sendCheckMessage(msg)
  → 解析出被调用方法: sendCheckMessage
  → 在同包/导入中定位: EndTransactionProcessor.sendCheckMessage
  → 创建调用边: A → sendCheckMessage
```

#### 6.3.2 调用图用途

| 用途         | 说明                                         |
| ------------ | -------------------------------------------- |
| 影响分析     | 修改方法 A 后，递归查找所有调用 A 的上游方法 |
| 调用链展示   | 展示从入口到目标方法的完整调用路径           |
| 阅读路径规划 | 为新人规划"从入口到核心"的代码阅读顺序       |
| 测试依赖识别 | 生成测试时识别需要 Mock 的下游依赖           |
| 图遍历召回   | 检索时从种子节点 BFS 扩展                    |
| GNN 图嵌入   | 作为 GNN 的输入图结构                        |
| 社区检测     | 识别功能模块边界                             |

### 6.4 关联失效处理

当代码被删除或重命名时：

```
代码删除事件
     │
     ▼
1. 标记 code_chunks.is_deleted = TRUE
     │
     ▼
2. 标记 chunk_relations.is_stale = TRUE
     │
     ▼
3. 标记 anchor_mappings.is_active = FALSE
     │
     ▼
4. 更新关联文档的 doc_chunks.stale_anchors 字段
     │
     ▼
5. 触发文档维护 Agent 生成更新建议
     │
     ▼
6. 触发图嵌入增量更新 (局部重算受影响节点)
```

------

## 7. 数据向量化

### 7.1 双嵌入模型策略（语义向量）

| 数据类型   | 嵌入模型                        | 维度        | 理由                           |
| ---------- | ------------------------------- | ----------- | ------------------------------ |
| 代码 chunk | Voyage Code / CodeBERT          | 1024 / 768  | 代码语义专用，理解标识符和结构 |
| 文档 chunk | BGE-M3 / text-embedding-3-large | 1024 / 3072 | 中英文混合文档，语义理解强     |

### 7.2 为什么需要双模型

| 查询类型        | 示例                        | 应走哪路     |
| --------------- | --------------------------- | ------------ |
| 自然语言 → 代码 | "事务消息回查是怎么实现的"  | 代码嵌入模型 |
| 自然语言 → 文档 | "怎么配置事务消息超时时间"  | 文档嵌入模型 |
| 代码 → 代码     | "和 sendMessage 类似的方法" | 代码嵌入模型 |
| 文档 → 文档     | "和这篇类似的文档"          | 文档嵌入模型 |

单模型无法同时在代码和自然语言上达到最优效果，因此采用双路检索。

### 7.3 语义向量化流程

```
代码 chunk:
  content + javadoc + method_signature → 拼接 → 代码嵌入模型 → 1024d 向量

文档 chunk:
  heading_path + content → 拼接 → 文档嵌入模型 → 1024d 向量
```

**拼接策略：**

- 代码：将 Javadoc 和方法签名放在代码前面，增强语义表达
- 文档：将标题路径放在内容前面，提供章节上下文

### 7.4 向量库 Collection 设计

> **collection 布局按 `EMBEDDING_STRATEGY` 切换**（见 §7.1 与 `docs/嵌入向量方案.md`）：
> - `unified` → 单一 `coderag_vectors`（1024d，带 `kind` 字段做 code/doc 过滤）；
> - `dual`（方案一）→ `code_vectors`(768d, CodeBERT) + `doc_vectors`(1024d, BGE-M3) 两个 collection，按 collection 名区分 kind。
> - `graph_embedding` 字段已于 2026-07-27 移除（图向量弃用）。content/class_name/heading_path 等不入 Milvus（PG 为真相源，命中后回填）。

**code_vectors / coderag_vectors(code) collection：**

| 字段      | 类型                  | 说明                                                       |
| --------- | --------------------- | ---------------------------------------------------------- |
| chunk_id  | VARCHAR(128)          | 主键                                                       |
| embedding | FLOAT_VECTOR(768/1024) | 代码语义嵌入（dual=CodeBERT 768 / unified=BGE-M3 1024）    |
| kind      | VARCHAR(16)           | 仅 unified 单 collection 时存在（code/doc 过滤）           |

**doc_vectors / coderag_vectors(doc) collection：**

| 字段      | 类型               | 说明                                                   |
| --------- | ------------------ | ------------------------------------------------------ |
| chunk_id  | VARCHAR(128)       | 主键                                                   |
| embedding | FLOAT_VECTOR(1024) | 文档语义嵌入（BGE-M3 1024）                            |
| kind      | VARCHAR(16)        | 仅 unified 单 collection 时存在                        |

**索引配置：**

| 参数           | 值     | 说明                 |
| -------------- | ------ | -------------------- |
| 索引类型       | HNSW   | 高召回率 + 低延迟    |
| M              | 32     | 连接数，越大召回越高 |
| efConstruction | 256    | 构建时搜索宽度       |
| ef (查询时)    | 128    | 查询时搜索宽度       |
| 距离度量       | COSINE | 余弦相似度           |

------

## 8. 图向量与 GraphRAG 设计

> ⛔ **图向量部分已于 2026-07-27 整体移除**：GNN / R-GCN / HGT / Node2Vec / 路径 C 图向量检索 /
> Milvus `graph_embedding` 向量列 / 精排图特征融合（原 §8.1–8.5、§11.3 路径 C、§11.7）均已删除。
> 理由：主链路仅保留「向量(unified 或 dual) + BM25 + 图遍历」三路召回 + RRF + 精排。
> **图遍历（路径 D，PG `call_graph` 递归 BFS）保留**（见 §6.3、§11.3）。
> GraphRAG 社区摘要（§8.6）留作 Phase 6 未来能力，但其原先依赖的 GNN 图向量部分不再适用，
> 若将来落地需改用社区结构特征（pagerank/degree/community，`graph_embeddings` 表已保留这些列）。

### 8.6 GraphRAG 社区摘要

#### 8.6.1 社区检测

对代码调用图执行社区检测算法（Leiden / Louvain），识别功能模块边界：

```
代码调用图 (异构图)
     │
     ▼
Leiden 社区检测算法 (多层级)
     │
     ▼
┌──────────────────────────────────────────────────────────────────────────┐
│ Level 0 (粗粒度，~10个社区):                                             │
│                                                                          │
│ 社区 0: "事务消息核心流程"                                                │
│   包含: sendMessageInTransaction, commitMessage, rollbackMessage,         │
│        checkLocalTransaction, resolveHalfMsg                             │
│   摘要: "该社区负责事务消息的完整生命周期管理，包括半消息发送、             │
│         本地事务执行、消息提交/回滚、以及定时回查机制..."                   │
│                                                                          │
│ 社区 1: "消息存储引擎"                                                    │
│   包含: putMessage, getMessage, CommitLog, ConsumeQueue...               │
│   摘要: "该社区负责消息的持久化存储，采用 CommitLog + ConsumeQueue         │
│         的双层结构..."                                                    │
│                                                                          │
│ 社区 2: "网络通信层"                                                      │
│   包含: NettyRemotingServer, processRequest, invokeSync...               │
│   摘要: "该社区负责 Broker 与 Client 之间的 RPC 通信..."                  │
│                                                                          │
│ Level 1 (细粒度，~50个社区):                                             │
│   社区 0 的子社区: "事务回查子流程", "事务提交子流程"...                    │
└──────────────────────────────────────────────────────────────────────────┘
```

#### 8.6.2 社区摘要生成

对每个社区，使用 LLM 生成自然语言摘要：

```
输入: 社区内所有节点的 class_name + method_name + javadoc + 关联文档标题
     │
     ▼
LLM Prompt: "请为以下代码模块生成一段 200 字的功能摘要..."
     │
     ▼
输出: 社区摘要文本 → 存入 graph_communities 表
     │
     ▼
社区摘要也做向量化 → 存入 Milvus (用于社区级检索)
```

#### 8.6.3 社区摘要的用途

| 用途       | 说明                                               |
| ---------- | -------------------------------------------------- |
| 全局性问题 | "RocketMQ 有哪些核心模块？" → 直接返回社区摘要列表 |
| 模块定位   | 用户问题先匹配社区 → 缩小检索范围到社区内节点      |
| 新人引导   | 按社区组织代码阅读路径                             |
| 变更影响   | 变更跨社区时，影响评估更准确                       |
| 精排特征   | 候选节点与 query 匹配社区的吻合度作为排序特征      |

------

## 9. 存储架构设计

### 9.1 混合存储原则

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          存储分层原则                                         │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  Milvus (向量库)          PostgreSQL (关系库)         Elasticsearch          │
│  ┌─────────────────┐     ┌─────────────────────┐    ┌─────────────────┐    │
│  │ • 语义嵌入向量   │     │ • 完整元数据         │    │ • 代码全文索引   │    │
│  │ • 图嵌入向量     │     │ • 多对多关联关系     │    │ • 文档全文索引   │    │
│  │ • content       │     │ • 调用图             │    │ • BM25 检索     │    │
│  │ • 过滤字段      │     │ • 版本变更历史       │    │                 │    │
│  │ • 社区ID       │     │ • 文档锚点映射       │    │                 │    │
│  │                 │     │ • 图结构特征         │    │                 │    │
│  │ 角色:           │     │ • 社区摘要           │    │ 角色:           │    │
│  │ 向量检索加速    │     │ • 增量更新任务日志   │    │ 关键词精确匹配  │    │
│  │                 │     │ • 检索日志           │    │                 │    │
│  │                 │     │                     │    │                 │    │
│  │                 │     │ 角色:               │    │                 │    │
│  │                 │     │ 数据管理权威源      │    │                 │    │
│  └─────────────────┘     └─────────────────────┘    └─────────────────┘    │
│                                                                             │
│  四者通过 chunk_id 作为全局唯一键关联                                         │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 9.2 各存储的职责

| 需求               | Milvus | PostgreSQL | Elasticsearch | 图计算引擎 |
| ------------------ | ------ | ---------- | ------------- | ---------- |
| 语义向量 ANN 检索  | ✅      | ❌          | ❌             | ❌          |
| BM25 关键词检索    | ❌      | 可选       | ✅             | ❌          |
| 多对多关系 JOIN    | ❌      | ✅          | ❌             | ❌          |
| 递归查询（调用链） | ❌      | ✅          | ❌             | ✅          |
| 事务保障           | ❌      | ✅          | ❌             | ❌          |
| 复杂聚合统计       | ❌      | ✅          | ✅             | ❌          |
| 变更历史追溯       | ❌      | ✅          | ❌             | ❌          |
| 社区检测           | ❌      | ❌          | ❌             | ✅          |
| 图遍历 (BFS/DFS)   | ❌      | ✅          | ❌             | ✅          |

### 9.3 数据一致性保障

**写入顺序：PostgreSQL 先写（事务）→ Milvus 后写 → ES 后写**

原因：

- PG 支持事务，保证元数据 + 关系的原子性
- Milvus 不支持跨 collection 事务
- 如果 Milvus/ES 写入失败，PG 中有 `embedding_synced = FALSE` 标记，可通过补偿任务重新同步

**补偿机制：**

- 定时任务扫描 `embedding_synced = FALSE` 的记录
- 重新计算嵌入 → 写入 Milvus → 同步 ES → 标记 `synced = TRUE`
- 允许短暂不一致（秒级），通过补偿达到最终一致

------

## 10. 关系数据表结构设计

### 10.1 ER 关系图

```
┌──────────────────┐       ┌──────────────────────┐       ┌──────────────────┐
│  code_files      │       │  chunk_relations     │       │  doc_files       │
│                  │       │                      │       │                  │
│  file_id (PK)    │       │  source_chunk_id(FK) │       │  file_id (PK)    │
│  file_path       │       │  target_chunk_id(FK) │       │  file_path       │
│  package_name    │       │  relation_type       │       │  title           │
│  module_name     │       │  anchor_key          │       │  doc_type        │
│  file_hash       │       │  confidence          │       │  file_hash       │
│  ...             │       │  is_stale            │       │  ...             │
└────────┬─────────┘       └──────────────────────┘       └────────┬─────────┘
         │                                                         │
         │ 1:N                                                     │ 1:N
         ▼                                                         ▼
┌──────────────────┐                                      ┌──────────────────┐
│  code_chunks     │                                      │  doc_chunks      │
│                  │                                      │                  │
│  chunk_id (PK)   │                                      │  chunk_id (PK)   │
│  file_id (FK)    │                                      │  file_id (FK)    │
│  class_name      │                                      │  heading_path    │
│  method_name     │                                      │  code_anchors    │
│  content         │                                      │  content         │
│  content_hash    │                                      │  content_hash    │
│  javadoc         │                                      │  linked_code_ids │
│  annotations     │                                      │  stale_anchors   │
│  ...             │                                      │  ...             │
└────────┬─────────┘                                      └──────────────────┘
         │
         │ 1:N
         ▼
┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐
│  call_graph      │  │  change_history  │  │  sync_tasks      │
│                  │  │                  │  │                  │
│  caller_chunk_id │  │  chunk_id        │  │  task_id (PK)    │
│  callee_chunk_id │  │  change_type     │  │  commit_hash     │
│  call_expression │  │  old_content_hash│  │  status          │
│  call_line       │  │  new_content_hash│  │  chunks_added    │
│                  │  │  git_commit_hash │  │  chunks_modified │
└──────────────────┘  └──────────────────┘  │  chunks_deleted  │
                                            └──────────────────┘
┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐
│  anchor_mappings │  │  graph_embeddings│  │  graph_communities│
│                  │  │                  │  │                  │
│  anchor_key      │  │  chunk_id (PK)   │  │  community_id(PK)│
│  code_chunk_id   │  │  graph_embedding │  │  level           │
│  doc_chunk_id    │  │  pagerank        │  │  title           │
│  is_active       │  │  node_degree     │  │  summary         │
│                  │  │  community_id_l0 │  │  member_chunk_ids│
└──────────────────┘  └──────────────────┘  └──────────────────┘

┌──────────────────────┐  ┌──────────────────┐  ┌──────────────────┐
│ node_community_mapping│ │  retrieval_logs  │  │ranking_model_config│
│                      │ │                  │  │                  │
│  chunk_id            │ │  query_text      │  │  model_name      │
│  community_id        │ │  recall_results  │  │  semantic_weight │
│  level               │ │  fine_rank_results│ │  graph_weight    │
│  is_centroid         │ │  user_feedback   │  │  is_active       │
└──────────────────────┘  └──────────────────┘  └──────────────────┘
```

### 10.2 完整 DDL

```sql
-- ============================================================
-- 1. 代码文件表
-- ============================================================
CREATE TABLE code_files (
    file_id         BIGSERIAL PRIMARY KEY,
    file_path       VARCHAR(512) NOT NULL UNIQUE,
    package_name    VARCHAR(256),
    module_name     VARCHAR(128),
    file_hash       VARCHAR(64) NOT NULL,
    total_lines     INTEGER,
    last_commit     VARCHAR(40),
    last_modified   TIMESTAMP WITH TIME ZONE,
    is_deleted      BOOLEAN DEFAULT FALSE,
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_code_files_path ON code_files(file_path);
CREATE INDEX idx_code_files_module ON code_files(module_name);
CREATE INDEX idx_code_files_package ON code_files(package_name);


-- ============================================================
-- 2. 代码切片表 (核心元数据)
-- ============================================================
CREATE TABLE code_chunks (
    chunk_id            VARCHAR(128) PRIMARY KEY,
    file_id             BIGINT NOT NULL REFERENCES code_files(file_id),
    
    -- 代码定位
    chunk_type          VARCHAR(32) NOT NULL,
    class_name          VARCHAR(256),
    method_name         VARCHAR(256),
    method_signature    VARCHAR(512),
    access_modifier     VARCHAR(32),
    return_type         VARCHAR(256),
    start_line          INTEGER NOT NULL,
    end_line            INTEGER NOT NULL,
    
    -- 语义内容
    content             TEXT NOT NULL,
    content_hash        VARCHAR(64) NOT NULL,
    javadoc             TEXT,
    inline_comments     JSONB DEFAULT '[]',
    annotations         JSONB DEFAULT '[]',
    
    -- 代码结构
    implements_interface VARCHAR(256),
    extends_class       VARCHAR(256),
    type_parameters     JSONB DEFAULT '[]',
    
    -- 关联信息
    linked_doc_ids      JSONB DEFAULT '[]',
    code_anchor_key     VARCHAR(512),
    
    -- 版本控制
    git_commit_hash     VARCHAR(40) NOT NULL,
    git_commit_time     TIMESTAMP WITH TIME ZONE,
    is_deleted          BOOLEAN DEFAULT FALSE,
    deleted_at_commit   VARCHAR(40),
    deleted_at          TIMESTAMP WITH TIME ZONE,
    
    -- 检索辅助
    keywords            JSONB DEFAULT '[]',
    token_count         INTEGER,
    embedding_synced    BOOLEAN DEFAULT FALSE,
    
    created_at          TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at          TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_code_chunks_file ON code_chunks(file_id);
CREATE INDEX idx_code_chunks_class ON code_chunks(class_name);
CREATE INDEX idx_code_chunks_method ON code_chunks(method_name);
CREATE INDEX idx_code_chunks_type ON code_chunks(chunk_type);
CREATE INDEX idx_code_chunks_deleted ON code_chunks(is_deleted) WHERE is_deleted = FALSE;
CREATE INDEX idx_code_chunks_anchor ON code_chunks(code_anchor_key);
CREATE INDEX idx_code_chunks_hash ON code_chunks(content_hash);
CREATE INDEX idx_code_chunks_commit ON code_chunks(git_commit_hash);
CREATE INDEX idx_code_chunks_keywords ON code_chunks USING GIN(keywords);


-- ============================================================
-- 3. 文档文件表
-- ============================================================
CREATE TABLE doc_files (
    file_id         BIGSERIAL PRIMARY KEY,
    file_path       VARCHAR(512) NOT NULL UNIQUE,
    title           VARCHAR(512),
    doc_type        VARCHAR(64),
    file_hash       VARCHAR(64) NOT NULL,
    total_chunks    INTEGER DEFAULT 0,
    last_commit     VARCHAR(40),
    last_modified   TIMESTAMP WITH TIME ZONE,
    is_deleted      BOOLEAN DEFAULT FALSE,
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);


-- ============================================================
-- 4. 文档切片表
-- ============================================================
CREATE TABLE doc_chunks (
    chunk_id            VARCHAR(128) PRIMARY KEY,
    file_id             BIGINT NOT NULL REFERENCES doc_files(file_id),
    
    -- 文档定位
    heading_path        JSONB NOT NULL DEFAULT '[]',
    heading_level       SMALLINT,
    section_order       INTEGER,
    
    -- 内容
    content             TEXT NOT NULL,
    content_hash        VARCHAR(64) NOT NULL,
    token_count         INTEGER,
    
    -- 关联信息
    code_anchors        JSONB DEFAULT '[]',
    linked_code_ids     JSONB DEFAULT '[]',
    inline_code_blocks  JSONB DEFAULT '[]',
    stale_anchors       JSONB DEFAULT '[]',
    
    -- 版本控制
    git_commit_hash     VARCHAR(40) NOT NULL,
    git_commit_time     TIMESTAMP WITH TIME ZONE,
    is_deleted          BOOLEAN DEFAULT FALSE,
    deleted_at_commit   VARCHAR(40),
    
    -- 检索辅助
    keywords            JSONB DEFAULT '[]',
    embedding_synced    BOOLEAN DEFAULT FALSE,
    
    created_at          TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at          TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_doc_chunks_file ON doc_chunks(file_id);
CREATE INDEX idx_doc_chunks_deleted ON doc_chunks(is_deleted) WHERE is_deleted = FALSE;
CREATE INDEX idx_doc_chunks_heading ON doc_chunks USING GIN(heading_path);
CREATE INDEX idx_doc_chunks_anchors ON doc_chunks USING GIN(code_anchors);
CREATE INDEX idx_doc_chunks_hash ON doc_chunks(content_hash);


-- ============================================================
-- 5. 切片关联关系表 (核心桥接表)
-- ============================================================
CREATE TABLE chunk_relations (
    relation_id     BIGSERIAL PRIMARY KEY,
    source_chunk_id VARCHAR(128) NOT NULL,
    target_chunk_id VARCHAR(128) NOT NULL,
    relation_type   VARCHAR(32) NOT NULL,
    anchor_key      VARCHAR(512),
    confidence      REAL DEFAULT 1.0,
    is_stale        BOOLEAN DEFAULT FALSE,
    stale_reason    VARCHAR(256),
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    
    CONSTRAINT uk_relation UNIQUE (source_chunk_id, target_chunk_id, relation_type)
);

-- relation_type 枚举值:
-- 'DOC_TO_CODE'        文档 → 代码
-- 'CODE_TO_DOC'        代码 → 文档
-- 'CODE_CALLS_CODE'    代码 → 代码 (方法调用)
-- 'CODE_IMPLEMENTS'    代码 → 代码 (接口实现)
-- 'CODE_EXTENDS'       代码 → 代码 (类继承)
-- 'DOC_REFERENCES_DOC' 文档 → 文档
-- 'CO_FILE'            代码 → 代码 (同文件)
-- 'CO_MODULE'          代码 → 代码 (同模块)

CREATE INDEX idx_relations_source ON chunk_relations(source_chunk_id);
CREATE INDEX idx_relations_target ON chunk_relations(target_chunk_id);
CREATE INDEX idx_relations_type ON chunk_relations(relation_type);
CREATE INDEX idx_relations_anchor ON chunk_relations(anchor_key);
CREATE INDEX idx_relations_stale ON chunk_relations(is_stale) WHERE is_stale = FALSE;


-- ============================================================
-- 6. 代码调用图表
-- ============================================================
CREATE TABLE call_graph (
    edge_id         BIGSERIAL PRIMARY KEY,
    caller_chunk_id VARCHAR(128) NOT NULL REFERENCES code_chunks(chunk_id),
    callee_chunk_id VARCHAR(128) NOT NULL REFERENCES code_chunks(chunk_id),
    
    call_expression VARCHAR(512),
    call_line       INTEGER,
    is_recursive    BOOLEAN DEFAULT FALSE,
    
    git_commit_hash VARCHAR(40),
    is_deleted      BOOLEAN DEFAULT FALSE,
    
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    
    CONSTRAINT uk_call_edge UNIQUE (caller_chunk_id, callee_chunk_id, call_line)
);

CREATE INDEX idx_call_graph_caller ON call_graph(caller_chunk_id);
CREATE INDEX idx_call_graph_callee ON call_graph(callee_chunk_id);


-- ============================================================
-- 7. 变更历史表
-- ============================================================
CREATE TABLE change_history (
    history_id      BIGSERIAL PRIMARY KEY,
    chunk_id        VARCHAR(128) NOT NULL,
    chunk_type      VARCHAR(32) NOT NULL,
    
    change_type     VARCHAR(16) NOT NULL,
    old_content_hash VARCHAR(64),
    new_content_hash VARCHAR(64),
    old_content     TEXT,
    new_content     TEXT,
    
    git_commit_hash VARCHAR(40) NOT NULL,
    git_commit_time TIMESTAMP WITH TIME ZONE,
    git_author      VARCHAR(128),
    commit_message  VARCHAR(512),
    
    affected_relations INTEGER DEFAULT 0,
    
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_history_chunk ON change_history(chunk_id);
CREATE INDEX idx_history_commit ON change_history(git_commit_hash);
CREATE INDEX idx_history_type ON change_history(change_type);
CREATE INDEX idx_history_time ON change_history(git_commit_time DESC);


-- ============================================================
-- 8. 增量同步任务表
-- ============================================================
CREATE TABLE sync_tasks (
    task_id         BIGSERIAL PRIMARY KEY,
    commit_hash     VARCHAR(40) NOT NULL,
    
    status          VARCHAR(32) DEFAULT 'PENDING',
    started_at      TIMESTAMP WITH TIME ZONE,
    completed_at    TIMESTAMP WITH TIME ZONE,
    error_message   TEXT,
    
    files_changed   INTEGER DEFAULT 0,
    chunks_added    INTEGER DEFAULT 0,
    chunks_modified INTEGER DEFAULT 0,
    chunks_deleted  INTEGER DEFAULT 0,
    relations_updated INTEGER DEFAULT 0,
    
    vector_sync_status VARCHAR(32) DEFAULT 'PENDING',
    graph_update_status VARCHAR(32) DEFAULT 'PENDING',
    
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_sync_tasks_status ON sync_tasks(status);
CREATE INDEX idx_sync_tasks_commit ON sync_tasks(commit_hash);


-- ============================================================
-- 9. 锚点映射表
-- ============================================================
CREATE TABLE anchor_mappings (
    mapping_id      BIGSERIAL PRIMARY KEY,
    anchor_key      VARCHAR(512) NOT NULL,
    
    code_chunk_id   VARCHAR(128) REFERENCES code_chunks(chunk_id),
    doc_chunk_id    VARCHAR(128) REFERENCES doc_chunks(chunk_id),
    
    is_active       BOOLEAN DEFAULT TRUE,
    deactivated_at  TIMESTAMP WITH TIME ZONE,
    deactivated_by_commit VARCHAR(40),
    
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    
    CONSTRAINT uk_anchor_pair UNIQUE (anchor_key, code_chunk_id, doc_chunk_id)
);

CREATE INDEX idx_anchor_key ON anchor_mappings(anchor_key);
CREATE INDEX idx_anchor_code ON anchor_mappings(code_chunk_id);
CREATE INDEX idx_anchor_doc ON anchor_mappings(doc_chunk_id);
CREATE INDEX idx_anchor_active ON anchor_mappings(is_active) WHERE is_active = TRUE;


-- ============================================================
-- 10. 图结构特征表（图向量列 graph_embedding/embedding_dim 已于 2026-07-27 移除；
--     保留 pagerank/degree/community 供 Phase 6 GraphRAG / Phase 8 LTR）
-- ============================================================
CREATE TABLE graph_embeddings (
    chunk_id        VARCHAR(128) PRIMARY KEY,

    -- 图结构特征
    node_degree     INTEGER DEFAULT 0,
    in_degree       INTEGER DEFAULT 0,
    out_degree      INTEGER DEFAULT 0,
    pagerank        FLOAT DEFAULT 0,
    betweenness     FLOAT DEFAULT 0,
    
    -- 社区归属 (多层级)
    community_id_l0 BIGINT,
    community_id_l1 BIGINT,
    community_id_l2 BIGINT,
    
    -- 版本
    model_version   VARCHAR(64),
    computed_at     TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    
    FOREIGN KEY (chunk_id) REFERENCES code_chunks(chunk_id)
);

CREATE INDEX idx_graph_embed_pagerank ON graph_embeddings(pagerank DESC);
CREATE INDEX idx_graph_embed_community_l0 ON graph_embeddings(community_id_l0);
CREATE INDEX idx_graph_embed_community_l1 ON graph_embeddings(community_id_l1);


-- ============================================================
-- 11. 图社区表
-- ============================================================
CREATE TABLE graph_communities (
    community_id    BIGSERIAL PRIMARY KEY,
    
    -- 社区信息
    level           INTEGER NOT NULL,
    title           VARCHAR(256),
    summary         TEXT NOT NULL,
    
    -- 统计
    node_count      INTEGER DEFAULT 0,
    edge_count      INTEGER DEFAULT 0,
    member_chunk_ids JSONB DEFAULT '[]',
    
    -- 向量同步状态
    summary_embedding_synced BOOLEAN DEFAULT FALSE,
    
    -- 版本
    computed_at_commit VARCHAR(40),
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_community_level ON graph_communities(level);


-- ============================================================
-- 12. 节点-社区归属表
-- ============================================================
CREATE TABLE node_community_mapping (
    chunk_id        VARCHAR(128) NOT NULL,
    community_id    BIGINT NOT NULL REFERENCES graph_communities(community_id),
    level           INTEGER NOT NULL,
    
    -- 节点在社区中的角色
    is_centroid     BOOLEAN DEFAULT FALSE,
    pagerank_in_community FLOAT,
    
    PRIMARY KEY (chunk_id, level)
);

CREATE INDEX idx_node_community ON node_community_mapping(chunk_id);
CREATE INDEX idx_community_nodes ON node_community_mapping(community_id);


-- ============================================================
-- 13. 检索日志表 (用于 LTR 训练和效果评估)
-- ============================================================
CREATE TABLE retrieval_logs (
    log_id          BIGSERIAL PRIMARY KEY,
    
    -- 查询信息
    query_text      TEXT NOT NULL,
    query_embedding BYTEA,
    
    -- 召回结果
    recall_results  JSONB NOT NULL,
    recall_count    INTEGER,
    
    -- 粗排结果
    coarse_rank_results JSONB,
    coarse_rank_count   INTEGER,
    
    -- 精排结果
    fine_rank_results JSONB,
    fine_rank_count   INTEGER,
    
    -- 最终使用
    final_chunk_ids JSONB,
    
    -- 用户反馈 (用于 LTR)
    user_feedback   VARCHAR(32),
    feedback_time   TIMESTAMP WITH TIME ZONE,
    
    -- 性能指标
    recall_latency_ms   INTEGER,
    coarse_rank_ms      INTEGER,
    fine_rank_ms        INTEGER,
    total_latency_ms    INTEGER,
    
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_retrieval_logs_time ON retrieval_logs(created_at DESC);
CREATE INDEX idx_retrieval_logs_feedback ON retrieval_logs(user_feedback) 
    WHERE user_feedback IS NOT NULL;


-- ============================================================
-- 14. 精排模型配置表
-- ============================================================
CREATE TABLE ranking_model_config (
    config_id       SERIAL PRIMARY KEY,
    
    model_name      VARCHAR(128) NOT NULL,
    model_version   VARCHAR(64) NOT NULL,
    model_type      VARCHAR(32) NOT NULL,
    
    -- 权重配置
    semantic_weight FLOAT DEFAULT 0.5,
    graph_weight    FLOAT DEFAULT 0.2,
    structural_weight FLOAT DEFAULT 0.3,
    
    -- 阈值
    min_score_threshold FLOAT DEFAULT 0.3,
    top_k           INTEGER DEFAULT 10,
    
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
```

### 10.3 典型查询 SQL

**查找某方法的所有关联文档：**

```sql
SELECT 
    dc.chunk_id,
    dc.content,
    dc.heading_path,
    cr.relation_type,
    cr.anchor_key,
    cr.confidence
FROM chunk_relations cr
JOIN doc_chunks dc ON cr.target_chunk_id = dc.chunk_id
WHERE cr.source_chunk_id = 'code_TransactionalMsgSvc_checkLocalTx_a1b2c3'
  AND cr.relation_type = 'CODE_TO_DOC'
  AND cr.is_stale = FALSE
  AND dc.is_deleted = FALSE;
```

**查找删除某方法后影响的所有文档：**

```sql
SELECT 
    dc.chunk_id,
    dc.heading_path,
    dc.content,
    am.anchor_key
FROM anchor_mappings am
JOIN doc_chunks dc ON am.doc_chunk_id = dc.chunk_id
WHERE am.code_chunk_id = 'code_xxx_deleted'
  AND dc.is_deleted = FALSE;
```

**递归查询某方法的完整调用链（下游 3 层）：**

```sql
WITH RECURSIVE call_chain AS (
    SELECT 
        cg.callee_chunk_id,
        cc.method_name,
        cc.class_name,
        1 AS depth,
        ARRAY[cc.class_name || '.' || cc.method_name] AS path
    FROM call_graph cg
    JOIN code_chunks cc ON cg.callee_chunk_id = cc.chunk_id
    WHERE cg.caller_chunk_id = 'code_TransactionalMsgSvc_checkLocalTx_a1b2c3'
      AND cg.is_deleted = FALSE
    
    UNION ALL
    
    SELECT 
        cg.callee_chunk_id,
        cc.method_name,
        cc.class_name,
        chain.depth + 1,
        chain.path || (cc.class_name || '.' || cc.method_name)
    FROM call_graph cg
    JOIN code_chunks cc ON cg.callee_chunk_id = cc.chunk_id
    JOIN call_chain chain ON cg.caller_chunk_id = chain.callee_chunk_id
    WHERE chain.depth < 3
      AND cg.is_deleted = FALSE
)
SELECT * FROM call_chain ORDER BY depth;
```

**查看某次提交的完整变更影响：**

```sql
SELECT 
    ch.chunk_id,
    ch.change_type,
    ch.chunk_type,
    cc.class_name,
    cc.method_name,
    ch.affected_relations,
    cf.file_path
FROM change_history ch
LEFT JOIN code_chunks cc ON ch.chunk_id = cc.chunk_id
LEFT JOIN code_files cf ON cc.file_id = cf.file_id
WHERE ch.git_commit_hash = 'abc123def456'
ORDER BY ch.change_type, cf.file_path;
```

**查询某节点所属社区及社区摘要：**

```sql
SELECT 
    gc.community_id,
    gc.level,
    gc.title,
    gc.summary,
    ncm.is_centroid,
    ncm.pagerank_in_community
FROM node_community_mapping ncm
JOIN graph_communities gc ON ncm.community_id = gc.community_id
WHERE ncm.chunk_id = 'code_TransactionalMsgSvc_checkLocalTx_a1b2c3'
ORDER BY ncm.level;
```

**查询同社区内的所有节点（用于社区级检索）：**

```sql
SELECT 
    ncm.chunk_id,
    cc.class_name,
    cc.method_name,
    ge.pagerank,
    ncm.is_centroid
FROM node_community_mapping ncm
JOIN code_chunks cc ON ncm.chunk_id = cc.chunk_id
JOIN graph_embeddings ge ON ncm.chunk_id = ge.chunk_id
WHERE ncm.community_id = 5
  AND ncm.level = 0
  AND cc.is_deleted = FALSE
ORDER BY ge.pagerank DESC;
```

------

## 11. 三阶段检索管道

### 11.1 管道总览

```
用户提问
    │
    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  Stage 0: 查询理解与改写                                                     │
│  • 意图识别 (代码理解/文档问答/全局问答/混合)                                 │
│  • 实体提取 (类名、方法名、配置项)                                           │
│  • 查询改写 (生成多个子查询)                                                 │
└──────────────────────────────────┬──────────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  Stage 1: 多路召回 (高召回率，候选集 60~80 条)                                │
│                                                                             │
│  路径A: 向量语义检索 (Milvus ANN；dual=CodeBERT+BGE-M3 双路)                │
│  路径B: BM25 关键词检索 (Elasticsearch)                                     │
│  路径D: 图遍历召回 (PostgreSQL call_graph BFS)                              │
│                                                                             │
│  → RRF 融合去重 → 候选集                                                    │
└──────────────────────────────────┬──────────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  Stage 2: 粗排 (轻量模型快速筛选，60~80 → 20~30)                             │
│                                                                             │
│  模型: ColBERTv2 / bge-reranker-base                                        │
│  输入: (query, candidate_content) 对                                        │
│  输出: 相关性分数 → 取 Top-20~30                                           │
└──────────────────────────────────┬──────────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  Stage 3: 精排 (深度语义重排，20~30 → 5~10)                                 │
│                                                                             │
│  模型: bge-reranker-v2-m3                                                   │
│  打分: Cross-Encoder 语义分（dual 下统一重排 code+doc 候选，屏蔽分差）       │
│  输出: 最终 Top-5~10 → 组装 Prompt → LLM 生成                               │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 11.2 Stage 0: 查询理解与改写

| 步骤     | 操作                 | 说明                                  |
| -------- | -------------------- | ------------------------------------- |
| 意图识别 | 分类用户问题类型     | 代码理解 / 文档问答 / 全局问答 / 混合 |
| 实体提取 | 识别问题中的代码实体 | 类名、方法名、配置项、模块名          |
| 查询改写 | 生成多个子查询       | 语义查询 + 精确查询 + 扩展查询        |
| 全局判断 | 是否为架构级问题     | 是 → 走社区摘要路径                   |

**查询改写示例：**

```
原始问题: "事务消息回查超时后消息怎么处理？"

改写为:
  q1 (语义): "事务消息回查超时处理逻辑"
  q2 (精确): "checkLocalTransaction TIMEOUT"
  q3 (扩展): "事务消息 死信队列 超过回查次数"
```

### 11.3 Stage 1: 三路并行召回

#### 路径 A：向量语义检索

| 参数   | 值                               |
| ------ | -------------------------------- |
| 检索库 | Milvus code_chunks + doc_chunks  |
| 模型   | 代码: Voyage Code / 文档: BGE-M3 |
| Top-K  | 各 30                            |
| 过滤   | is_deleted = FALSE               |
| 距离   | COSINE                           |

#### 路径 B：BM25 关键词检索

| 参数     | 值                                                      |
| -------- | ------------------------------------------------------- |
| 检索库   | Elasticsearch                                           |
| 索引内容 | content + javadoc + keywords + class_name + method_name |
| Top-K    | 各 20                                                   |
| 优势     | 精确匹配类名、方法名、配置项                            |

**为什么需要 BM25？**

向量检索对精确标识符匹配较弱。例如用户搜索 `DefaultMQProducer`，向量检索可能返回语义相近但不包含该类的结果，BM25 能精确命中。

#### 路径 C：图向量检索　❌ 已移除（2026-07-27）

> GNN `graph_embedding` / 路径 C 图向量检索已弃用。需要"结构相关"召回时改用**路径 D 图遍历**
> （PG `call_graph` 递归 BFS），确定性、无需训练。

#### 路径 D：图遍历召回

| 参数     | 值                                                        |
| -------- | --------------------------------------------------------- |
| 触发条件 | 用户问题中提到了具体方法名/类名 (由 Stage 0 实体提取识别) |
| 方式     | 从种子节点出发，BFS 扩展 2~3 层                           |
| 来源     | PostgreSQL call_graph + chunk_relations                   |
| 优势     | 确保调用链上的相关方法不被遗漏                            |

```
用户问: "checkLocalTransaction 的上下游是什么？"
     │
     ▼
识别种子节点: checkLocalTransaction
     │
     ▼
BFS 扩展:
  上游 (谁调用它): EndTransactionProcessor.checkTransactionState
  下游 (它调用谁): resolveHalfMsg, sendCheckMessage, getTransactionState
  关联文档: "3.2 回查机制"
     │
     ▼
全部加入候选集
```

### 11.4 RRF 融合

三路召回结果通过 **Reciprocal Rank Fusion (RRF)** 融合：

```
RRF_score(d) = Σ weight_i / (k + rank_i(d))

其中:
  k = 60 (常数)
  rank_i(d) = 文档 d 在第 i 路召回中的排名
  weight_i = 各路径权重
```

| 路径     | 权重 | 说明                 |
| -------- | ---- | -------------------- |
| 向量语义 | 1.0  | 基准                 |
| BM25     | 0.8  | 精确匹配补充         |
| 图遍历   | 1.2  | 确定性关联，权重最高 |

融合后去重，得到 60~80 条候选。

### 11.5 Stage 2: 粗排

| 维度       | 说明                                            |
| ---------- | ----------------------------------------------- |
| **模型**   | ColBERTv2 / bge-reranker-base                   |
| **架构**   | Late Interaction (ColBERT) / 轻量 Cross-Encoder |
| **输入**   | (query, candidate_content) 对                   |
| **输出**   | 相关性分数 (0~1)                                |
| **候选数** | 60~80 → 20~30                                   |
| **延迟**   | ~30-50ms (GPU batch)                            |
| **特点**   | 轻量、快速、精度适中                            |

**ColBERT 的优势：** 延迟交互（Late Interaction），query 和 document 分别编码后做 token 级交互，比 Cross-Encoder 快 10 倍以上，适合粗排。

### 11.6 Stage 3: 精排

| 维度       | 说明                                          |
| ---------- | --------------------------------------------- |
| **模型**   | bge-reranker-v2-m3 / Qwen3-Reranker           |
| **架构**   | Cross-Encoder (全交叉注意力)                  |
| **输入**   | (query, candidate_content) 对，拼接后联合编码 |
| **输出**   | 精确相关性分数                                |
| **候选数** | 20~30 → 5~10                                  |
| **延迟**   | ~100-200ms (GPU batch)                        |

### 11.7 精排中的图特征融合　❌ 已移除（2026-07-27）

> 图向量（`graph_embedding`）及基于它的 `graph_score` 融合已随图向量整体弃用。
> 当前 Stage 3 为**纯语义精排**（仅 Cross-Encoder 语义分）；dual 嵌入框架下，该精排同时充当
> 「统一重排桥」对 code+doc 候选统一打分（见 `docs/嵌入向量方案.md` 方案一）。
> 结构特征（pagerank/degree/community 等）已保留在 `graph_embeddings` 表，留待 Phase 8 LTR 复用。

### 11.8 Learning-to-Rank (进阶优化)

如果有用户反馈数据（点击、采纳、忽略），可以训练 LTR 模型：

```
特征向量 (每个候选):
  [semantic_score, graph_score, bm25_score, graph_distance, 
   pagerank, community_match, relation_count, recency, 
   chunk_type_code, heading_level, token_count, ...]

标签:
  用户采纳 = 1, 用户忽略 = 0

模型:
  LightGBM / XGBoost (LambdaMART)

输出:
  学习到的最优排序分数
```

### 11.9 各阶段延迟预算

| 阶段     | 延迟        | 说明                   |
| -------- | ----------- | ---------------------- |
| 查询理解 | ~20ms       | 轻量 NLP 处理          |
| 四路召回 | ~30ms       | 并行执行，取最慢一路   |
| RRF 融合 | ~5ms        | 纯计算                 |
| 粗排     | ~40ms       | GPU batch              |
| 精排     | ~150ms      | GPU batch + 图特征查询 |
| LLM 生成 | ~2000ms     | 主要延迟               |
| **总计** | **~2250ms** | LLM 生成占主要部分     |

> 检索管道总延迟约 250ms，相比 LLM 生成时间可忽略。

### 11.10 完整检索流程示例

```
用户提问: "事务消息回查超时后，消息最终会怎么处理？"
              │
              ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ Step 0: 查询理解与改写                                                       │
│                                                                             │
│ • 意图识别: 代码理解 + 文档问答 (混合意图)                                   │
│ • 实体提取: "事务消息", "回查", "超时", "消息处理"                           │
│ • 查询改写:                                                                 │
│   q1: "事务消息回查超时处理逻辑" (语义检索用)                                │
│   q2: "checkLocalTransaction TIMEOUT" (BM25 精确匹配用)                     │
│   q3: "事务消息 死信队列" (扩展查询)                                        │
└──────────────────────────────────┬──────────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ Step 1: 四路并行召回                                                         │
│                                                                             │
│ 路径A - 向量语义:                                                           │
│   q1 → 代码嵌入 → Milvus code_chunks → Top30                               │
│   q1 → 文档嵌入 → Milvus doc_chunks → Top30                                │
│                                                                             │
│ 路径B - BM25:                                                               │
│   q2 → Elasticsearch → Top20                                               │
│   命中: checkLocalTransaction 方法 (精确匹配方法名)                           │
│                                                                             │
│ 路径D - 图遍历:                                                             │
│   种子: checkLocalTransaction (从 BM25 结果中识别)                           │
│   BFS 2层: → resolveHalfMsg → getTransactionState                          │
│            → sendCheckMessage → putToDeadLetter ← 关键命中!                 │
│   关联文档: "3.2.3 回查次数限制"                                            │
│                                                                             │
│ RRF 融合 → 去重 → 候选集 72 条                                             │
└──────────────────────────────────┬──────────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ Step 2: 粗排 (ColBERTv2)                                                    │
│                                                                             │
│ 输入: 72 条候选 × (query, content) 对                                      │
│ 输出: 每条候选的相关性分数                                                  │
│ 筛选: 取 Top-25                                                            │
│ 耗时: ~40ms                                                                │
└──────────────────────────────────┬──────────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ Step 3: 精排 (bge-reranker-v2-m3，纯语义重排)                              │
│                                                                             │
│ 对 Top-25 候选:                                                            │
│                                                                             │
│ ① Cross-Encoder 语义分数:                                                   │
│    (query, content) → bge-reranker-v2-m3 → semantic_score                  │
│                                                                             │
│ ② 结构特征（Phase 8 LTR 阶段使用，当前 Stage 3 未启用）:                    │
│    • graph_distance to seed (checkLocalTransaction): 0~3                   │
│    • pagerank / community_match / relation_count / recency                 │
│                                                                             │
│ ③ 排序:                                                                    │
│    final_score = semantic_score（纯语义；图向量相似度已移除）               │
│                                                                             │
│ 输出: Top-8 最终结果                                                        │
│ 耗时: ~150ms                                                               │
└──────────────────────────────────┬──────────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ Step 4: 上下文组装 + LLM 生成                                               │
│                                                                             │
│ 最终 Top-8:                                                                │
│   1. [代码] checkLocalTransaction 方法 (语义+图距离双高)                     │
│   2. [代码] putToDeadLetter 方法 (图遍历命中，关键答案)                      │
│   3. [文档] "3.2.3 回查次数限制" (精确回答)                                 │
│   4. [代码] resolveHalfMsg 方法 (调用链上下文)                              │
│   5. [文档] "5.1 事务消息最佳实践" (补充说明)                               │
│   6. [代码] getTransactionState 方法 (状态判断逻辑)                         │
│   7. [代码] TransactionalMessageCheckService.check (调度入口)               │
│   8. [文档] "FAQ: 事务消息常见问题" (补充)                                  │
│                                                                             │
│ 组装 Prompt → LLM 生成最终回答                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

------

## 12. 召回精排模型设计

### 12.1 模型选型总结

#### 召回模型

| 模型                   | 用途           | 维度     | 推荐度 |
| ---------------------- | -------------- | -------- | ------ |
| Voyage Code / CodeBERT | 代码语义召回   | 1024/768 | ⭐⭐⭐⭐⭐  |
| BGE-M3                 | 文档语义召回   | 1024     | ⭐⭐⭐⭐⭐  |
| Elasticsearch BM25     | 关键词精确召回 | —        | ⭐⭐⭐⭐⭐  |

#### 粗排模型

| 模型              | 延迟  | 精度 | 推荐度 |
| ----------------- | ----- | ---- | ------ |
| ColBERTv2         | ~30ms | 中   | ⭐⭐⭐⭐⭐  |
| bge-reranker-base | ~40ms | 中   | ⭐⭐⭐⭐   |

#### 精排模型

| 模型                  | 延迟   | 精度      | 推荐度 |
| --------------------- | ------ | --------- | ------ |
| bge-reranker-v2-m3    | ~150ms | 高        | ⭐⭐⭐⭐⭐  |
| Qwen3-Reranker        | ~200ms | 最高      | ⭐⭐⭐⭐⭐  |
| BGE-Reasoner-Reranker | ~300ms | 最高+推理 | ⭐⭐⭐⭐   |

> 图嵌入模型（R-GCN / HGT / GraphSAGE / GAT / Node2Vec）已于 2026-07-27 随图向量整体移除。

### 12.2 模型服务部署

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ 模型服务架构                                                                 │
│                                                                             │
│ ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────────────┐   │
│ │ Embedding Service │  │ Reranker Service │  │ GNN Inference Service    │   │
│ │                  │  │                  │  │                          │   │
│ │ • Voyage Code    │  │ • ColBERTv2      │  │ • R-GCN / HGT           │   │
│ │ • BGE-M3        │  │ • bge-reranker   │  │ • 批量推理              │   │
│ │                  │  │   -v2-m3         │  │ • 增量更新              │   │
│ │ GPU: 1×A10      │  │ GPU: 1×A10      │  │ GPU: 1×A10             │   │
│ │ 延迟: <10ms     │  │ 延迟: <200ms    │  │ 延迟: <1s (批量)       │   │
│ └──────────────────┘  └──────────────────┘  └──────────────────────────┘   │
│                                                                             │
│ 部署方式: Triton Inference Server / vLLM / TorchServe                       │
└─────────────────────────────────────────────────────────────────────────────┘
```

------

## 13. 增量更新流程

### 13.1 触发方式

| 触发方式    | 说明                        |
| ----------- | --------------------------- |
| Git Webhook | 代码推送到仓库时自动触发    |
| 定时轮询    | 每隔 N 分钟检查新 commit    |
| 手动触发    | 管理员手动触发全量/增量重建 |

### 13.2 增量更新流程

```
Git Push 事件
     │
     ▼
┌──────────────────────────────────────────────────────────────┐
│ Step 1: 获取变更文件列表                                       │
│                                                              │
│ git diff --name-only {last_commit} {new_commit}              │
│ → 得到: 新增/修改/删除的文件列表                               │
└──────────────────────────────────┬───────────────────────────┘
                                   │
                                   ▼
┌──────────────────────────────────────────────────────────────┐
│ Step 2: 分类处理                                              │
│                                                              │
│ • .java 文件变更 → 代码更新管道                               │
│ • .md 文件变更   → 文档更新管道                               │
│ • 其他文件       → 忽略                                      │
└──────────────────────────────────┬───────────────────────────┘
                                   │
                    ┌──────────────┼──────────────┐
                    ▼                             ▼
         ┌──────────────────┐          ┌──────────────────┐
         │ 代码更新管道      │          │ 文档更新管道      │
         └────────┬─────────┘          └────────┬─────────┘
                  │                              │
                  ▼                              ▼
┌──────────────────────────────────────────────────────────────┐
│ Step 3: 变更检测 (content_hash 对比)                           │
│                                                              │
│ 对每个变更文件:                                               │
│   重新解析 → 重新切片 → 计算每个 chunk 的 content_hash         │
│   与 PostgreSQL 中存储的 content_hash 对比:                    │
│     • hash 相同 → 跳过 (未实际变更)                           │
│     • hash 不同 → 标记为 MODIFIED                            │
│     • 新增 chunk → 标记为 ADDED                              │
│     • 消失 chunk → 标记为 DELETED                            │
└──────────────────────────────────┬───────────────────────────┘
                                   │
                                   ▼
┌──────────────────────────────────────────────────────────────┐
│ Step 4: 执行更新                                              │
│                                                              │
│ ADDED:                                                       │
│   → 计算语义嵌入 → 写入 Milvus + PostgreSQL                  │
│   → 同步 ES 全文索引                                         │
│   → 构建关联关系                                              │
│   → 触发图嵌入增量更新 (局部 GNN 推理)                        │
│                                                              │
│ MODIFIED:                                                    │
│   → 重新计算语义嵌入 → 更新 Milvus + PostgreSQL              │
│   → 更新 ES 全文索引                                         │
│   → 更新关联关系                                              │
│   → 记录变更历史                                              │
│   → 触发图嵌入增量更新                                        │
│                                                              │
│ DELETED:                                                     │
│   → 标记 Milvus 中 is_deleted = TRUE                         │
│   → 标记 PostgreSQL 中 is_deleted = TRUE                     │
│   → 删除 ES 文档                                             │
│   → 标记关联关系 is_stale = TRUE                             │
│   → 更新 anchor_mappings.is_active = FALSE                   │
│   → 更新关联文档的 stale_anchors                              │
│   → 记录变更历史                                              │
└──────────────────────────────────┬───────────────────────────┘
                                   │
                                   ▼
┌──────────────────────────────────────────────────────────────┐
│ Step 5: （图嵌入增量更新已于 2026-07-27 移除）                │
│   图向量 / GNN 弃用后本步骤不再执行；                         │
│   社区结构变更检测留待 Phase 6 GraphRAG。                     │
└──────────────────────────────────┬───────────────────────────┘
                                   │
                                   ▼
┌──────────────────────────────────────────────────────────────┐
│ Step 6: 记录同步任务                                          │
│                                                              │
│ 写入 sync_tasks 表:                                          │
│   commit_hash, 变更统计, 状态, 图更新状态                     │
└──────────────────────────────────────────────────────────────┘
```

### 13.3 定时全量任务

| 任务           | 频率     | 说明                                     |
| -------------- | -------- | ---------------------------------------- |
| 社区检测重算   | 每周     | 重新执行 Leiden 算法 + LLM 生成摘要（Phase 6） |
| 补偿同步       | 每小时   | 扫描 embedding_synced = FALSE，重新同步  |
| 文档腐化检测   | 每日     | 扫描 stale_anchors，生成更新建议         |
| 索引优化       | 每周     | Milvus compact / PG vacuum / ES optimize |

### 13.4 一致性保障

| 策略       | 说明                                                       |
| ---------- | ---------------------------------------------------------- |
| 写入顺序   | PostgreSQL 先写（事务）→ Milvus 后写 → ES 后写             |
| 失败补偿   | Milvus/ES 写入失败时，PG 中标记 `embedding_synced = FALSE` |
| 定时补偿   | 定时任务扫描未同步记录，重新写入                           |
| 图嵌入补偿 | 每日全量 GNN 推理校准                                      |
| 最终一致   | 允许秒级不一致，通过补偿达到最终一致                       |

------

## 14. 多 Agent 协作体系

### 14.1 Agent 全景

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              用户交互层                                          │
│   开发者提问 / IDE 插件 / CI/CD 触发 / 告警触发 / 定时任务                        │
└──────────────────────────────────┬──────────────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                          路由 Agent (Orchestrator)                               │
│                                                                                 │
│   职责: 意图识别 → 任务分解 → 分发给专业 Agent → 汇总结果                         │
└────┬────────┬────────┬────────┬────────┬────────┬────────┬────────┬────────┬────┘
     │        │        │        │        │        │        │        │        │
     ▼        ▼        ▼        ▼        ▼        ▼        ▼        ▼        ▼
┌────────┐┌────────┐┌────────┐┌────────┐┌────────┐┌────────┐┌────────┐┌────────┐┌────────┐
│代码理解 ││文档问答  ││变更影响 ││缺陷诊断 │ │代码审查 ││测试生成 ││文档维护  ││新人引导 ││全局问答 │
│ Agent  ││ Agent  ││ Agent  ││ Agent  ││ Agent  ││ Agent  ││ Agent  ││ Agent  ││ Agent  │
└────────┘└────────┘└────────┘└────────┘└────────┘└────────┘└────────┘└────────┘└────────┘
```

### 14.2 路由 Agent 职责

| 职责       | 说明                                     |
| ---------- | ---------------------------------------- |
| 意图识别   | 判断用户问题属于哪个 Agent 的处理范围    |
| 任务分解   | 复杂问题拆分为多个子任务分发给不同 Agent |
| 结果汇总   | 收集各 Agent 返回结果，融合为最终回答    |
| 上下文管理 | 维护多轮对话上下文                       |
| 全局判断   | 识别架构级问题，路由到全局问答 Agent     |

### 14.3 各 Agent 对系统能力的依赖

| Agent    | 代码向量检索 | 文档向量检索 | 图遍历 | 调用图 | 关联关系 | 变更历史 | 社区摘要 | 精排 |
| -------- | ------------ | ------------ | ------ | ------ | -------- | -------- | -------- | ---- |
| 代码理解 | ✅            | ✅            | ✅      | ✅      | ✅        | ○        | ○        | ✅    |
| 文档问答 | ○            | ✅            | ○      | ○      | ✅        | ○        | ○        | ✅    |
| 变更影响 | ○            | ○            | ✅      | ✅      | ✅        | ✅        | ✅        | ○    |
| 缺陷诊断 | ✅            | ✅            | ✅      | ✅      | ○        | ✅        | ○        | ✅    |
| 代码审查 | ✅            | ✅            | ○      | ✅      | ○        | ✅        | ○        | ✅    |
| 测试生成 | ✅            | ✅            | ✅      | ✅      | ○        | ○        | ○        | ✅    |
| 文档维护 | ✅            | ✅            | ○      | ○      | ✅        | ○        | ✅        | ○    |
| 新人引导 | ✅            | ✅            | ✅      | ✅      | ✅        | ○        | ✅        | ✅    |
| 全局问答 | ○            | ○            | ○      | ○      | ○        | ○        | ✅        | ○    |

> ✅ = 核心依赖   ○ = 可选/辅助　（图向量检索列已于 2026-07-27 移除）

### 14.4 各 Agent 的工具集

**代码理解 Agent：**

- search_code(query, filters) → Milvus 向量语义检索（dual 下 CodeBERT 代码路 + BGE-M3 文档路）
- search_docs(query, filters) → Milvus 文档检索
- bm25_search(query) → Elasticsearch 精确匹配
- graph_traverse(seed_id, depth) → 图遍历召回
- get_call_chain(chunk_id, depth) → PG call_graph 递归查询
- get_related_docs(chunk_id) → PG chunk_relations
- get_javadoc(chunk_id) → PG code_chunks
- rerank(query, candidates) → 精排模型

**变更影响 Agent：**

- get_downstream_callers(chunk_id) → PG call_graph
- get_upstream_callers(chunk_id) → PG call_graph
- get_affected_docs(chunk_id) → PG anchor_mappings
- get_recent_changes(file_path) → PG change_history
- get_community_impact(chunk_id) → PG node_community_mapping
- mark_stale_anchors(chunk_id) → 更新 PG anchor_mappings

**文档维护 Agent：**

- detect_stale_docs() → 查询 PG stale_anchors
- get_community_summary(community_id) → PG graph_communities
- generate_doc_update(old, new) → 调用 LLM 生成新文档
- create_doc_pr(content) → 调用 Git API 提交

**全局问答 Agent：**

- get_all_communities(level) → PG graph_communities
- search_community_summaries(query) → Milvus 社区摘要向量检索
- get_community_members(community_id) → PG node_community_mapping
- get_inter_community_edges() → PG chunk_relations 跨社区边

------

## 15. 各 Agent 场景详细说明

### 15.1 代码理解 Agent

| 维度               | 说明                                                         |
| ------------------ | ------------------------------------------------------------ |
| **解决什么问题**   | "这个方法是干什么的？""这段逻辑为什么这么写？""这个类的职责是什么？" |
| **触发方式**       | 开发者在 IDE 中选中代码提问 / 对话式提问                     |
| **依赖的系统能力** | 代码向量检索、图向量检索、Javadoc、调用图、关联文档、精排    |

**详细流程：**

```
用户: "checkLocalTransaction 这个方法是干什么的？"
     │
     ▼
┌──────────────────────────────────────────────────────────┐
│ Step 1: 多路检索定位目标方法                               │
│                                                          │
│ • BM25 精确匹配 "checkLocalTransaction" → 直接命中       │
│ • 语义检索辅助确认                                       │
│ 获取: 完整方法代码 + Javadoc + 注解                       │
└──────────────────────────────────┬───────────────────────┘
                                   │
                                   ▼
┌──────────────────────────────────────────────────────────┐
│ Step 2: 关联文档检索                                      │
│                                                          │
│ 通过 chunk_relations 查找 CODE_TO_DOC 关系                │
│ 命中: "3.2 事务消息回查机制" 文档段落                       │
│ 获取: 设计文档中对该方法的描述和设计意图                     │
└──────────────────────────────────┬───────────────────────┘
                                   │
                                   ▼
┌──────────────────────────────────────────────────────────┐
│ Step 3: 调用图展开 + 图遍历                               │
│                                                          │
│ 查询 call_graph:                                         │
│   上游: 谁调用了 checkLocalTransaction                    │
│   下游: checkLocalTransaction 调用了哪些方法               │
│ 图向量: 找到结构角色相似的方法作为对比参考                  │
└──────────────────────────────────┬───────────────────────┘
                                   │
                                   ▼
┌──────────────────────────────────────────────────────────┐
│ Step 4: 精排 + LLM 生成回答                              │
│                                                          │
│ 精排: 对所有检索结果按相关性排序                           │
│ Prompt 包含:                                             │
│   • 方法源代码 + Javadoc                                 │
│   • 设计文档中的描述                                      │
│   • 调用链上下文                                         │
│   • 结构相似方法对比                                      │
│ 输出: 结构化的方法功能解释                                 │
└──────────────────────────────────────────────────────────┘
```

**独特价值：** 不仅看代码本身，还能拉出设计文档中的"为什么这么设计"，图向量提供结构角色对比。

------

### 15.2 文档问答 Agent

| 维度               | 说明                                             |
| ------------------ | ------------------------------------------------ |
| **解决什么问题**   | "事务消息最多回查几次？""怎么配置延迟消息等级？" |
| **触发方式**       | 对话式提问                                       |
| **依赖的系统能力** | 文档向量检索、关联代码片段、精排                 |

**详细流程：**

```
用户: "事务消息最多回查几次？超过次数会怎样？"
     │
     ▼
┌──────────────────────────────────────────────────────────┐
│ Step 1: 文档检索                                          │
│                                                          │
│ 用文档嵌入模型编码问题 → Milvus 检索 doc_chunks            │
│ 命中: "3.2.3 回查次数限制" 段落                            │
│ 内容: "默认最多回查 15 次，超过后消息进入死信队列..."        │
└──────────────────────────────────┬───────────────────────┘
                                   │
                                   ▼
┌──────────────────────────────────────────────────────────┐
│ Step 2: 关联代码佐证                                      │
│                                                          │
│ 通过 doc_chunks.linked_code_ids 找到关联代码               │
│ 获取: 定义 MAX_CHECK_TIMES = 15 的常量代码                 │
│ 获取: 超过次数后 putToDeadLetter 的代码实现                │
└──────────────────────────────────┬───────────────────────┘
                                   │
                                   ▼
┌──────────────────────────────────────────────────────────┐
│ Step 3: 精排 + LLM 生成回答                              │
│                                                          │
│ 精排确保最相关的文档段落和代码排在前面                      │
│ 回答包含:                                                │
│   • 文档中的配置说明 (出处: 3.2.3 章节)                   │
│   • 代码中的常量定义作为佐证                               │
│   • 超限后的处理逻辑说明                                  │
└──────────────────────────────────────────────────────────┘
```

**独特价值：** 回答配置/概念类问题时，附带代码实现作为"证据"，增强可信度，并标注文档出处章节。

------

### 15.3 变更影响分析 Agent

| 维度               | 说明                                                   |
| ------------------ | ------------------------------------------------------ |
| **解决什么问题**   | "我改了这个方法，会影响哪些下游？哪些文档需要更新？"   |
| **触发方式**       | PR 提交时自动触发 / 开发者手动查询                     |
| **依赖的系统能力** | 调用图递归查询、关联关系表、变更历史、图向量、社区摘要 |

**详细流程：**

```
触发: 开发者修改了 checkLocalTransaction 方法，提交 PR
     │
     ▼
┌──────────────────────────────────────────────────────────┐
│ Step 1: 识别变更 chunk                                    │
│                                                          │
│ 增量管道检测到 content_hash 变化                           │
│ 确认: code_TransactionalMsgSvc_checkLocalTx 被修改         │
└──────────────────────────────────┬───────────────────────┘
                                   │
                                   ▼
┌──────────────────────────────────────────────────────────┐
│ Step 2: 调用图向下游遍历                                   │
│                                                          │
│ 递归查询 call_graph (深度 3 层):                          │
│   checkLocalTransaction                                  │
│     → resolveHalfMsg                                     │
│       → getTransactionState                              │
│     → sendCheckMessage                                   │
│       → buildCheckMessage                                │
│ 输出: 5 个下游方法可能受影响                               │
└──────────────────────────────────┬───────────────────────┘
                                   │
                                   ▼
┌──────────────────────────────────────────────────────────┐
│ Step 3: 调用图向上游遍历                                   │
│                                                          │
│ 查找谁调用了 checkLocalTransaction:                       │
│   ← EndTransactionProcessor.checkTransactionState         │
│     ← TransactionalMessageCheckService.check              │
│ 输出: 2 个上游调用方                                      │
└──────────────────────────────────┬───────────────────────┘
                                   │
                                   ▼
┌──────────────────────────────────────────────────────────┐
│ Step 4: （图向量找"结构相似"已于 2026-07-27 移除）         │
│   图向量弃用；潜在影响改由调用图 BFS（路径 D）+ 锚点关联覆盖 │
└──────────────────────────────────┬───────────────────────┘
                                   │
                                   ▼
┌──────────────────────────────────────────────────────────┐
│ Step 5: 查找受影响的文档                                   │
│                                                          │
│ 查询 anchor_mappings + chunk_relations:                   │
│   锚点 "checkLocalTransaction" 关联的文档段落:             │
│     • "3.2 事务消息回查机制" (doc_3_2_001)                │
│     • "5.1 事务消息最佳实践" (doc_5_1_003)                │
│ 输出: 2 个文档段落可能需要更新                             │
└──────────────────────────────────┬───────────────────────┘
                                   │
                                   ▼
┌──────────────────────────────────────────────────────────┐
│ Step 6: 社区级影响评估                                     │
│                                                          │
│ 查询 node_community_mapping:                             │
│   变更方法属于社区 0 "事务消息核心流程"                     │
│   判断: 变更是否影响社区边界？是否跨社区？                  │
│   如果跨社区 → 影响评估升级                               │
└──────────────────────────────────┬───────────────────────┘
                                   │
                                   ▼
┌──────────────────────────────────────────────────────────┐
│ Step 7: 生成影响报告                                      │
│                                                          │
│ 报告内容:                                                │
│   • 变更方法: checkLocalTransaction                       │
│   • 下游影响: 5 个方法 (列表 + 调用路径)                   │
│   • 上游影响: 2 个调用方                                  │
│   • 结构相似: 2 个其他模块的类似方法需关注                 │
│   • 文档影响: 2 个段落需要审查是否过时                     │
│   • 社区影响: 限于"事务消息核心流程"社区内                 │
│   • 风险评估: 中 (涉及核心事务流程)                       │
└──────────────────────────────────────────────────────────┘
```

**独特价值：** 代码改动 → 自动识别受影响的文档段落、下游方法、结构相似方法，结合社区信息评估影响范围。

------

### 15.4 缺陷诊断 Agent

| 维度               | 说明                                                        |
| ------------------ | ----------------------------------------------------------- |
| **解决什么问题**   | "线上报了 NPE，堆栈指向这个方法，可能是什么原因？"          |
| **触发方式**       | 告警触发 / 开发者粘贴堆栈提问                               |
| **依赖的系统能力** | 代码检索 + 图遍历 + 文档已知问题 + 变更历史 + 图向量 + 精排 |

**详细流程：**

```
输入: 异常堆栈
  java.lang.NullPointerException
    at ...TransactionalMessageServiceImpl.checkLocalTransaction(TransactionalMessageServiceImpl.java:156)
    at ...EndTransactionProcessor.checkTransactionState(EndTransactionProcessor.java:89)
     │
     ▼
┌──────────────────────────────────────────────────────────┐
│ Step 1: 定位异常代码                                      │
│                                                          │
│ 从堆栈提取: TransactionalMessageServiceImpl 第 156 行      │
│ BM25 精确检索 → 获取 checkLocalTransaction 完整代码        │
│ 定位第 156 行的具体语句                                   │
└──────────────────────────────────┬───────────────────────┘
                                   │
                                   ▼
┌──────────────────────────────────────────────────────────┐
│ Step 2: 图遍历向上追溯调用来源                             │
│                                                          │
│ BFS 向上扩展:                                            │
│   EndTransactionProcessor.checkTransactionState           │
│   → 传入了什么参数？参数来源是什么？                       │
│ 获取上游方法代码，分析参数传递链                           │
└──────────────────────────────────┬───────────────────────┘
                                   │
                                   ▼
┌──────────────────────────────────────────────────────────┐
│ Step 3: 检索已知问题                                      │
│                                                          │
│ 文档检索: "checkLocalTransaction NullPointerException"    │
│ 命中: FAQ 章节 "已知问题: 事务消息回查时空指针"            │
│ 内容: "当 Producer 端未正确设置 TransactionListener 时..." │
└──────────────────────────────────┬───────────────────────┘
                                   │
                                   ▼
┌──────────────────────────────────────────────────────────┐
│ Step 4: 图向量找"同模式"的历史问题                         │
│                                                          │
│ 用异常方法的 graph_embedding 检索结构相似节点:             │
│   找到: 其他 Processor 中类似的空指针风险点                │
│   参考: 这些位置是否有已有的防御性代码                     │
└──────────────────────────────────┬───────────────────────┘
                                   │
                                   ▼
┌──────────────────────────────────────────────────────────┐
│ Step 5: 检查最近变更                                      │
│                                                          │
│ 查询 change_history:                                     │
│   最近 7 天内 checkLocalTransaction 是否有修改？           │
│   如果有 → 对比新旧代码，分析是否引入了空指针风险          │
└──────────────────────────────────┬───────────────────────┘
                                   │
                                   ▼
┌──────────────────────────────────────────────────────────┐
│ Step 6: 精排 + 综合诊断                                   │
│                                                          │
│ 精排: 对所有诊断证据按相关性排序                           │
│ 输出:                                                    │
│   • 异常位置: 第 156 行 msg.getTransactionId() 调用       │
│   • 可能原因: msg 为 null (上游未正确传递)                 │
│   • 已知问题匹配: FAQ 中有类似案例                        │
│   • 结构相似: 其他 Processor 中有防御性 null check        │
│   • 最近变更: 3 天前有修改，新增了参数校验逻辑             │
│   • 建议修复: 添加 null check / 参考 FAQ 中的解决方案     │
└──────────────────────────────────────────────────────────┘
```

**独特价值：** 结合代码实现 + 文档已知问题 + 图向量结构相似 + 最近变更，给出多维度诊断。

------

### 15.5 代码审查 Agent

| 维度               | 说明                                                         |
| ------------------ | ------------------------------------------------------------ |
| **解决什么问题**   | PR 提交后自动审查：逻辑是否正确、是否符合项目规范、是否影响已有功能 |
| **触发方式**       | PR Webhook 自动触发                                          |
| **依赖的系统能力** | 代码检索、图向量（找相似实现）、调用图、文档（设计规范）、变更历史、精排 |

**详细流程：**

```
触发: 开发者提交 PR，修改了 SendMessageProcessor 类
     │
     ▼
┌──────────────────────────────────────────────────────────┐
│ Step 1: 解析 PR Diff                                     │
│                                                          │
│ 提取变更的方法和代码行                                    │
│ 识别: 修改了 processRequest 方法 + 新增了 validateMsg 方法 │
└──────────────────────────────────┬───────────────────────┘
                                   │
                                   ▼
┌──────────────────────────────────────────────────────────┐
│ Step 2: 图向量检索相似实现                                 │
│                                                          │
│ 用变更方法的 graph_embedding 检索结构相似节点:             │
│   • PullMessageProcessor.processRequest (结构角色相同)    │
│   • QueryMessageProcessor.processRequest                 │
│ 对比: 新代码是否遵循了相同的模式和异常处理方式             │
└──────────────────────────────────┬───────────────────────┘
                                   │
                                   ▼
┌──────────────────────────────────────────────────────────┐
│ Step 3: 影响范围评估                                      │
│                                                          │
│ 查询 call_graph:                                         │
│   processRequest 被哪些上游调用？                         │
│   修改是否影响了调用方的预期行为？                         │
└──────────────────────────────────┬───────────────────────┘
                                   │
                                   ▼
┌──────────────────────────────────────────────────────────┐
│ Step 4: 规范检查                                          │
│                                                          │
│ 文档检索: 项目编码规范 / 设计约束                          │
│ 检查:                                                    │
│   • 异常处理是否符合规范                                  │
│   • 日志打印是否完整                                      │
│   • 是否缺少必要的参数校验                                │
└──────────────────────────────────┬───────────────────────┘
                                   │
                                   ▼
┌──────────────────────────────────────────────────────────┐
│ Step 5: 历史模式参考                                      │
│                                                          │
│ 查询 change_history:                                     │
│   该文件过去的修改中，是否有回滚记录？                     │
│   是否有相关的 Bug 修复历史？                             │
│ 参考: 避免重复引入已知问题                                │
└──────────────────────────────────┬───────────────────────┘
                                   │
                                   ▼
┌──────────────────────────────────────────────────────────┐
│ Step 6: 精排 + 生成审查意见                               │
│                                                          │
│ 精排: 对所有参考信息按相关性排序                           │
│ 输出:                                                    │
│   • [建议] validateMsg 方法缺少对 topic 长度的校验        │
│     (参考: PullMessageProcessor 中有类似校验)             │
│   • [警告] processRequest 修改影响了 3 个上游调用方       │
│   • [规范] 异常处理应使用 RemotingCommand 标准错误码      │
│     (参考: 项目编码规范 4.2 节)                           │
│   • [通过] 整体逻辑正确，与项目风格一致                   │
└──────────────────────────────────────────────────────────┘
```

**独特价值：** 图向量找到"结构角色相同"的代码进行对比，不是通用 Lint，而是结合项目上下文的"有业务理解的审查"。

------

### 15.6 测试生成 Agent

| 维度               | 说明                                                       |
| ------------------ | ---------------------------------------------------------- |
| **解决什么问题**   | "给这个方法生成单元测试"                                   |
| **触发方式**       | 开发者指定方法 / PR 中新增方法自动触发                     |
| **依赖的系统能力** | 代码检索、调用图（依赖链）、文档（边界条件）、图遍历、精排 |

**详细流程：**

```
用户: "给 checkLocalTransaction 方法生成单元测试"
     │
     ▼
┌──────────────────────────────────────────────────────────┐
│ Step 1: 获取方法完整信息                                   │
│                                                          │
│ 检索 code_chunks:                                        │
│   • 方法签名: public OperationResult checkLocalTransaction│
│     (MessageExt msg, String transactionId)               │
│   • 返回类型: OperationResult                            │
│   • 方法体: 完整代码                                     │
│   • 注解: @Override                                      │
└──────────────────────────────────┬───────────────────────┘
                                   │
                                   ▼
┌──────────────────────────────────────────────────────────┐
│ Step 2: 图遍历识别依赖 (需要 Mock 的对象)                  │
│                                                          │
│ BFS 下游 2 层:                                           │
│   • this.resolveHalfMsg() → 需要 Mock                    │
│   • this.sendCheckMessage() → 需要 Mock                  │
│   • transactionStore.get() → 需要 Mock                   │
│ 输出: 3 个需要 Mock 的依赖                               │
└──────────────────────────────────┬───────────────────────┘
                                   │
                                   ▼
┌──────────────────────────────────────────────────────────┐
│ Step 3: 提取边界条件 (从文档)                              │
│                                                          │
│ 检索关联文档:                                            │
│   • "回查最多 15 次" → 测试边界: checkTimes = 15, 16     │
│   • "transactionId 为空时直接返回" → 测试: null 输入      │
│   • "消息不存在时返回 ROLLBACK" → 测试: msg not found    │
│ 精排: 确保最相关的边界条件排在前面                         │
│ 输出: 5 个测试场景                                       │
└──────────────────────────────────┬───────────────────────┘
                                   │
                                   ▼
┌──────────────────────────────────────────────────────────┐
│ Step 4: 生成测试用例                                      │
│                                                          │
│ LLM 生成:                                               │
│   • testCheckLocalTransaction_Success                    │
│   • testCheckLocalTransaction_NullTransactionId          │
│   • testCheckLocalTransaction_MaxCheckTimes              │
│   • testCheckLocalTransaction_MessageNotFound            │
│   • testCheckLocalTransaction_SendCheckFailed            │
│ 每个测试包含: Mock 设置 + 调用 + 断言                     │
└──────────────────────────────────────────────────────────┘
```

**独特价值：** 文档中描述的边界条件直接转化为测试断言，图遍历确保 Mock 依赖完整。

------

### 15.7 文档维护 Agent

| 维度               | 说明                                                   |
| ------------------ | ------------------------------------------------------ |
| **解决什么问题**   | 代码改了但文档没更新 → 自动检测并生成更新建议          |
| **触发方式**       | 增量更新管道检测到代码变更时自动触发 / 定时扫描        |
| **依赖的系统能力** | 增量更新管道、anchor_mappings、stale_anchors、社区摘要 |

**详细流程：**

```
触发: 增量管道检测到 checkLocalTransaction 方法被修改
     │
     ▼
┌──────────────────────────────────────────────────────────┐
│ Step 1: 检测受影响的文档                                   │
│                                                          │
│ 查询 anchor_mappings:                                    │
│   anchor_key = "checkLocalTransaction"                   │
│   → 关联文档: doc_3_2_001 ("3.2 回查机制")               │
│   → 关联文档: doc_5_1_003 ("5.1 最佳实践")               │
└──────────────────────────────────┬───────────────────────┘
                                   │
                                   ▼
┌──────────────────────────────────────────────────────────┐
│ Step 2: 对比文档描述与新代码                               │
│                                                          │
│ 文档描述: "回查时调用 checkLocalTransaction，              │
│           返回 COMMIT/ROLLBACK/UNKNOW 三种状态"           │
│ 新代码: 返回值从 3 种变为 4 种 (新增 TIMEOUT 状态)         │
│ 判断: 文档描述已过时                                      │
└──────────────────────────────────┬───────────────────────┘
                                   │
                                   ▼
┌──────────────────────────────────────────────────────────┐
│ Step 3: 社区级文档腐化检测                                 │
│                                                          │
│ 查询社区 0 "事务消息核心流程" 的摘要:                      │
│   摘要中提到 "三种状态" → 也需要更新                      │
│   判断: 社区摘要是否过时                                  │
└──────────────────────────────────┬───────────────────────┘
                                   │
                                   ▼
┌──────────────────────────────────────────────────────────┐
│ Step 4: 生成文档更新建议                                   │
│                                                          │
│ LLM 生成:                                               │
│   原文: "返回 COMMIT/ROLLBACK/UNKNOW 三种状态"            │
│   建议: "返回 COMMIT/ROLLBACK/UNKNOW/TIMEOUT 四种状态，   │
│         其中 TIMEOUT 表示回查超时，消息将..."              │
└──────────────────────────────────┬───────────────────────┘
                                   │
                                   ▼
┌──────────────────────────────────────────────────────────┐
│ Step 5: 提交文档更新 PR                                   │
│                                                          │
│ 自动创建 Git PR:                                         │
│   • 修改 docs/transaction-message.md                     │
│   • PR 描述: "代码变更同步: checkLocalTransaction         │
│     新增 TIMEOUT 状态 (commit: abc123)"                  │
│   • 标记为: 需要人工审核                                  │
└──────────────────────────────────────────────────────────┘
```

**独特价值：** 解决"文档腐化"问题，结合社区摘要检测模块级文档过时。

------

### 15.8 新人引导 Agent

| 维度               | 说明                                               |
| ------------------ | -------------------------------------------------- |
| **解决什么问题**   | "我刚接手这个项目，事务消息模块从哪里开始看？"     |
| **触发方式**       | 新人入职 / 开发者切换到新模块                      |
| **依赖的系统能力** | 文档检索、代码检索、调用图、图向量、社区摘要、精排 |

**详细流程：**

```
用户: "我想了解事务消息模块，应该从哪里开始看？"
     │
     ▼
┌──────────────────────────────────────────────────────────┐
│ Step 1: 社区摘要提供模块全景                               │
│                                                          │
│ 查询社区 0 "事务消息核心流程" 的摘要:                      │
│   "该社区负责事务消息的完整生命周期管理，包括半消息发送、   │
│    本地事务执行、消息提交/回滚、以及定时回查机制..."        │
│ 获取: 模块概述、核心组件列表                              │
└──────────────────────────────────┬───────────────────────┘
                                   │
                                   ▼
┌──────────────────────────────────────────────────────────┐
│ Step 2: 定位入口类                                        │
│                                                          │
│ 代码检索: "事务消息 入口 Producer"                         │
│ 命中: TransactionMQProducer (客户端入口)                  │
│ 命中: EndTransactionProcessor (Broker 端入口)             │
└──────────────────────────────────┬───────────────────────┘
                                   │
                                   ▼
┌──────────────────────────────────────────────────────────┐
│ Step 3: 生成阅读路径 (调用图 + 图向量)                     │
│                                                          │
│ 从入口沿 call_graph 展开:                                 │
│ 图向量辅助: 按结构重要性 (pagerank) 排序阅读顺序           │
│                                                          │
│ 推荐阅读顺序:                                            │
│   1. TransactionMQProducer.sendMessageInTransaction()    │
│   2. EndTransactionProcessor.processRequest()            │
│   3. TransactionalMessageServiceImpl.commitMessage()     │
│   4. TransactionalMessageServiceImpl.checkLocalTransaction()│
│   5. TransactionalMessageCheckService.check()            │
└──────────────────────────────────┬───────────────────────┘
                                   │
                                   ▼
┌──────────────────────────────────────────────────────────┐
│ Step 4: 每步附带文档解释 + 精排优化                        │
│                                                          │
│ 对阅读路径中的每个方法:                                   │
│   查询 chunk_relations → 获取关联文档段落                  │
│   精排: 确保最相关的文档解释排在前面                       │
│                                                          │
│ 输出格式:                                                │
│   📖 Step 1: TransactionMQProducer                       │
│   代码: [方法签名 + 关键逻辑]                             │
│   文档: "事务消息的发送入口，用户需要实现                  │
│         TransactionListener 接口..." (出处: 2.1 节)       │
│   下一步: → EndTransactionProcessor                      │
└──────────────────────────────────────────────────────────┘
```

**独特价值：** 社区摘要提供全景，调用图规划路径，图向量优化顺序，文档提供解说。

------

### 15.9 全局问答 Agent（新增）

| 维度               | 说明                                                  |
| ------------------ | ----------------------------------------------------- |
| **解决什么问题**   | "RocketMQ 的整体架构是怎样的？""各模块之间如何协作？" |
| **触发方式**       | 架构级/全局性问题                                     |
| **依赖的系统能力** | 社区摘要、社区间关系、图向量                          |

**详细流程：**

```
用户: "RocketMQ 的整体架构是怎样的？各模块之间如何协作？"
     │
     ▼
┌──────────────────────────────────────────────────────────┐
│ Step 1: 识别为全局性问题                                   │
│                                                          │
│ 路由 Agent 判断: 这是架构级问题，不是具体代码/文档问题      │
│ 路由到: 全局问答 Agent                                    │
└──────────────────────────────────┬───────────────────────┘
                                   │
                                   ▼
┌──────────────────────────────────────────────────────────┐
│ Step 2: 获取所有社区摘要                                   │
│                                                          │
│ 查询 graph_communities (level=0):                        │
│   社区 0: "事务消息核心流程" — 摘要...                    │
│   社区 1: "消息存储引擎" — 摘要...                        │
│   社区 2: "网络通信层" — 摘要...                          │
│   社区 3: "消费者管理" — 摘要...                          │
│   ...                                                    │
└──────────────────────────────────┬───────────────────────┘
                                   │
                                   ▼
┌──────────────────────────────────────────────────────────┐
│ Step 3: 获取社区间关系                                     │
│                                                          │
│ 查询跨社区的 chunk_relations 和 call_graph 边:            │
│   社区 0 (事务消息) → 社区 1 (存储): putMessage 调用      │
│   社区 2 (网络) → 社区 0 (事务消息): processRequest 分发  │
│   社区 3 (消费者) → 社区 1 (存储): getMessage 拉取        │
└──────────────────────────────────┬───────────────────────┘
                                   │
                                   ▼
┌──────────────────────────────────────────────────────────┐
│ Step 4: LLM 生成全局架构描述                               │
│                                                          │
│ Prompt 包含:                                             │
│   • 所有社区摘要                                         │
│   • 社区间调用关系                                       │
│   • 各社区的核心节点列表                                  │
│                                                          │
│ 输出: 结构化的架构描述 + 模块协作关系图                    │
└──────────────────────────────────────────────────────────┘
```

**独特价值：** 普通 RAG 无法回答全局性问题，GraphRAG 社区摘要使系统具备"鸟瞰视角"。

------

### 15.10 Agent 间协作场景

#### 场景一：完整的 PR 处理流程

```
开发者提交 PR
     │
     ▼
┌──────────────────┐
│  代码审查 Agent   │ ← 自动触发
│  审查代码质量     │
└────────┬─────────┘
         │ 发现改动了 checkLocalTransaction 方法
         ▼
┌──────────────────┐
│  变更影响 Agent   │ ← 被审查 Agent 调用
│  分析影响范围     │
│  输出: 3个下游方法 + 2个文档段落 + 1个结构相似方法受影响
└────────┬─────────┘
         │
         ├──────────────────────────┐
         ▼                          ▼
┌──────────────────┐      ┌──────────────────┐
│  测试生成 Agent   │      │  文档维护 Agent   │
│  为改动生成测试   │      │  生成文档更新建议  │
└──────────────────┘      └──────────────────┘
         │                          │
         └────────────┬─────────────┘
                      ▼
              汇总到 PR 评论中
```

#### 场景二：线上故障排查

```
告警: TransactionCheckTimeoutException
     │
     ▼
┌──────────────────┐
│  缺陷诊断 Agent   │
│  定位异常代码     │
│  图遍历追溯调用链 │
│  图向量找同模式   │
│  匹配已知问题     │
└────────┬─────────┘
         │ 发现是最近一次提交引入的
         ▼
┌──────────────────┐
│  变更影响 Agent   │
│  确认影响范围     │
│  社区级评估       │
│  建议回滚方案     │
└──────────────────┘
```

#### 场景三：新人入职第一天

```
新人: "我想了解 RocketMQ 的整体架构"
     │
     ▼
┌──────────────────┐
│  全局问答 Agent   │
│  社区摘要全景     │
│  模块协作关系     │
└────────┬─────────┘
         │ 新人继续问: "事务消息模块怎么看？"
         ▼
┌──────────────────┐
│  新人引导 Agent   │
│  生成阅读路径     │
│  附带文档解说     │
└────────┬─────────┘
         │ 新人阅读后提问: "这个方法什么意思？"
         ▼
┌──────────────────┐
│  代码理解 Agent   │
│  解释具体方法     │
│  展开调用链       │
└──────────────────┘
```

------

## 16. 整体流程总结

### 16.1 系统建设流程（一次性）

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ Phase 1: 数据准备                                                            │
│                                                                             │
│ 1. 克隆 RocketMQ 源代码仓库                                                 │
│ 2. 收集所有文档 (docs/ 目录 + Wiki)                                         │
│ 3. 确定模块划分和文件范围                                                    │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ Phase 2: 全量解析与切片                                                      │
│                                                                             │
│ 1. Tree-sitter 解析所有 Java 文件 → 提取 AST 结构                           │
│ 2. Markdown 解析所有文档 → 提取章节结构                                      │
│ 3. 按策略切片 → 生成 code_chunks + doc_chunks                               │
│ 4. 提取元数据 → 写入 PostgreSQL                                             │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ Phase 3: 关联构建                                                            │
│                                                                             │
│ 1. 解析 CODE_ANCHOR → 建立文档-代码关联                                     │
│ 2. 解析方法调用 → 构建 call_graph                                           │
│ 3. 解析继承/实现关系 → 补充关联                                             │
│ 4. 写入 chunk_relations + anchor_mappings                                   │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ Phase 4: 语义向量化                                                          │
│                                                                             │
│ 1. 代码 chunks → 代码嵌入模型 → 写入 Milvus code_chunks collection          │
│ 2. 文档 chunks → 文档嵌入模型 → 写入 Milvus doc_chunks collection           │
│ 3. 标记 embedding_synced = TRUE                                             │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ Phase 5: 全文索引构建                                                        │
│                                                                             │
│ 1. 代码 content + javadoc + keywords → Elasticsearch 索引                   │
│ 2. 文档 content + heading_path → Elasticsearch 索引                         │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ Phase 6: 图构建与图向量化                                                    │
│                                                                             │
│ 1. 从 PG 导出节点和边 → 构建异构图                                          │
│ 2. 训练 R-GCN / HGT 模型 (自监督)                                          │
│ 3. GNN 推理 → 生成所有节点的 graph_embedding (256d)                         │
│ 4. 写入 Milvus (graph_embedding 字段) + PG (graph_embeddings 表)            │
│ 5. 计算 PageRank、度数等图结构特征                                           │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ Phase 7: 社区检测与摘要                                                      │
│                                                                             │
│ 1. Leiden 算法执行多层级社区检测                                             │
│ 2. LLM 为每个社区生成自然语言摘要                                           │
│ 3. 社区摘要向量化 → 写入 Milvus                                             │
│ 4. 写入 PG (graph_communities + node_community_mapping)                     │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ Phase 8: 精排模型部署                                                        │
│                                                                             │
│ 1. 部署 ColBERTv2 (粗排)                                                    │
│ 2. 部署 bge-reranker-v2-m3 (精排)                                          │
│ 3. 配置融合权重 (α, β, γ)                                                  │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ Phase 9: 验证与上线                                                          │
│                                                                             │
│ 1. 准备测试问题集                                                           │
│ 2. 验证检索准确率 (Recall@K, MRR, NDCG)                                    │
│ 3. 验证关联正确性                                                           │
│ 4. 验证图向量检索效果                                                       │
│ 5. 验证精排提升效果 (A/B 对比)                                              │
│ 6. 部署 Agent 服务                                                          │
│ 7. 配置增量更新管道 (Git Webhook)                                           │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 16.2 日常运行流程（持续）

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ 持续运行                                                                     │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │ 增量更新管道 (每次 Git Push)                                         │   │
│  │                                                                     │   │
│  │ Git Push → 变更检测 → 重新切片 → 更新 PG → 更新 Milvus →            │   │
│  │ 更新 ES → 图嵌入局部更新 → 更新关联 → 记录历史                       │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │ 三阶段检索服务 (每次用户提问)                                        │   │
│  │                                                                     │   │
│  │ 查询理解 → 四路召回 → RRF融合 → 粗排 → 精排(含图特征) →             │   │
│  │ Prompt 组装 → LLM 生成                                              │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │ Agent 服务 (按触发条件)                                              │   │
│  │                                                                     │   │
│  │ PR 提交 → 代码审查 + 变更影响 + 测试生成 Agent                      │   │
│  │ 告警触发 → 缺陷诊断 Agent                                           │   │
│  │ 代码变更 → 文档维护 Agent                                           │   │
│  │ 用户提问 → 路由 Agent → 分发到对应 Agent                            │   │
│  │ 全局问题 → 全局问答 Agent (社区摘要)                                 │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │ 定时任务                                                             │   │
│  │                                                                     │   │
│  │ • 每小时: 补偿同步 (embedding_synced = FALSE)                       │   │
│  │ • 每日: 图嵌入全量重算 (GNN 推理)                                   │   │
│  │ • 每日: 文档腐化检测 (stale_anchors)                                │   │
│  │ • 每周: 社区检测重算 + 摘要更新                                     │   │
│  │ • 每周: 索引优化 (Milvus compact / PG vacuum / ES optimize)         │   │
│  │ • 持续: 收集用户反馈 → LTR 模型迭代                                 │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 16.3 系统核心价值总结

| 能力维度           | 普通 RAG      | 本系统                      |
| ------------------ | ------------- | --------------------------- |
| 回答代码问题       | ✅             | ✅                           |
| 回答文档问题       | ✅             | ✅                           |
| 代码-文档双向关联  | ❌             | ✅ 通过锚点 + 关联表         |
| 追溯影响范围       | ❌             | ✅ 通过调用图递归            |
| 感知代码变更       | ❌             | ✅ 通过增量管道              |
| 保持文档一致性     | ❌             | ✅ 通过锚点失效检测          |
| 生成有上下文的测试 | ❌             | ✅ 通过关联文档提取边界条件  |
| 规划代码阅读路径   | ❌             | ✅ 通过调用图 + 文档交织     |
| 结合历史变更诊断   | ❌             | ✅ 通过变更历史              |
| 结构相似性检索     | ❌             | ✅ 通过 GNN 图向量           |
| 多跳推理           | ❌             | ✅ 图向量编码多跳 + 图遍历   |
| 全局架构问答       | ❌             | ✅ GraphRAG 社区摘要         |
| 精确标识符匹配     | 弱            | ✅ BM25 补充                 |
| 排序精度           | 中 (向量距离) | ✅ 三阶段精排 + 图特征 + LTR |

**一句话总结：语义向量让系统"理解文本含义"，图向量让系统"理解代码的结构角色"，精排模型让系统"从候选中精准挑选"，GraphRAG 社区摘要让系统"具备全局视野"。四者互补，形成"语义理解 + 结构感知 + 精准排序 + 全局视野"的完整检索能力，支撑上层 Agent 实现从"被动问答"到"主动感知、分析、行动"的跃迁。**

------

## 17. 实施路线图

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ Phase 1: 基础 RAG 建设 (3~4 周)                                             │
│                                                                             │
│ • 代码/文档解析 + 切片 + 元数据提取                                         │
│ • 关联构建 (锚点匹配 + 调用图)                                             │
│ • 语义向量化 → Milvus                                                      │
│ • 基础双路检索 + LLM 生成                                                   │
│ • 效果: 基础问答能力可用                                                    │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ Phase 2: 精排模型引入 (1~2 周)                                               │
│                                                                             │
│ • 部署 bge-reranker-v2-m3 服务                                             │
│ • 在双路检索后增加精排阶段                                                  │
│ • 效果: 检索精度提升 15~25%                                                 │
│ • 风险: 低 (纯增量)                                                        │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ Phase 3: BM25 + 多路召回 (1~2 周)                                           │
│                                                                             │
│ • 部署 Elasticsearch，建立全文索引                                          │
│ • 实现四路召回 + RRF 融合                                                   │
│ • 效果: 召回率提升 10~15%                                                   │
│ • 风险: 低                                                                  │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ Phase 4: 图遍历召回 (1 周)                                                   │
│                                                                             │
│ • 实现基于 call_graph 的 BFS 召回路径                                       │
│ • 与向量召回结果融合                                                        │
│ • 效果: 调用链相关问题召回率提升 20%+                                       │
│ • 风险: 低 (利用已有 PG 数据)                                               │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ Phase 5: GNN 图向量 (3~4 周)                                                │
│                                                                             │
│ • 构建异构图 (代码+文档节点，多种边)                                        │
│ • 训练 R-GCN / HGT 模型                                                    │
│ • 生成图嵌入 → 写入 Milvus                                                 │
│ • 实现图向量检索路径                                                        │
│ • 精排融合图特征                                                            │
│ • 效果: 结构相似性检索 + 多跳推理                                           │
│ • 风险: 中 (需要 GPU 训练)                                                  │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ Phase 6: GraphRAG 社区摘要 (2~3 周)                                         │
│                                                                             │
│ • 社区检测 (Leiden 算法)                                                    │
│ • LLM 生成社区摘要                                                         │
│ • 实现全局问答 Agent                                                        │
│ • 效果: 支持架构级全局问答                                                  │
│ • 风险: 中 (摘要质量依赖 LLM)                                              │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ Phase 7: 多 Agent 体系 (3~4 周)                                             │
│                                                                             │
│ • 实现路由 Agent + 各专业 Agent                                             │
│ • Agent 间协作流程                                                          │
│ • 对接 CI/CD (PR 审查) + 告警系统                                          │
│ • 效果: 从被动问答到主动分析                                                │
│ • 风险: 中 (流程复杂)                                                       │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ Phase 8: LTR 持续优化 (持续)                                                 │
│                                                                             │
│ • 收集用户反馈数据                                                          │
│ • 训练 LambdaMART 排序模型                                                  │
│ • A/B 测试优化权重                                                          │
│ • 效果: 排序质量持续提升                                                    │
│ • 风险: 低 (渐进式优化)                                                     │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 总工期估算

| 阶段                   | 工期          | 累计  |
| ---------------------- | ------------- | ----- |
| Phase 1: 基础 RAG      | 3~4 周        | 4 周  |
| Phase 2: 精排模型      | 1~2 周        | 6 周  |
| Phase 3: BM25 多路召回 | 1~2 周        | 8 周  |
| Phase 4: 图遍历召回    | 1 周          | 9 周  |
| Phase 5: GNN 图向量    | 3~4 周        | 13 周 |
| Phase 6: GraphRAG      | 2~3 周        | 16 周 |
| Phase 7: 多 Agent      | 3~4 周        | 20 周 |
| Phase 8: LTR 优化      | 持续          | —     |
| **总计**               | **约 5 个月** | —     |

------

*文档版本: v2.0 (融合图向量与精排模型)*
*适用项目: Apache RocketMQ 智能知识库*

## 18、支持代码回滚

------

### 一、现有表新增字段（2 个）

#### 1.1 `change_history` 表新增

```sql
ALTER TABLE change_history ADD COLUMN rollback_source_commit VARCHAR(40);
ALTER TABLE change_history ADD COLUMN is_rollback_related BOOLEAN DEFAULT FALSE;

CREATE INDEX idx_history_rollback ON change_history(rollback_source_commit) 
    WHERE rollback_source_commit IS NOT NULL;
```

| 字段                     | 类型        | 说明                                      |
| ------------------------ | ----------- | ----------------------------------------- |
| `rollback_source_commit` | VARCHAR(40) | 被回滚的原始 commit hash，非回滚时为 NULL |
| `is_rollback_related`    | BOOLEAN     | 该条变更是否由回滚触发                    |

#### 1.2 `sync_tasks` 表新增

```sql
ALTER TABLE sync_tasks ADD COLUMN doc_pr_url VARCHAR(512);
ALTER TABLE sync_tasks ADD COLUMN doc_pr_status VARCHAR(32);
```

| 字段            | 类型         | 说明                                              |
| --------------- | ------------ | ------------------------------------------------- |
| `doc_pr_url`    | VARCHAR(512) | 该次同步触发的文档更新 PR 地址                    |
| `doc_pr_status` | VARCHAR(32)  | PR 状态：`OPEN` / `MERGED` / `CLOSED_BY_ROLLBACK` |

------

### 二、新增表（1 张）

#### 2.1 `rollback_history` 回滚记录表

```sql
CREATE TABLE rollback_history (
    rollback_id         BIGSERIAL PRIMARY KEY,
    
    -- 回滚标识
    rollback_commit     VARCHAR(40) NOT NULL,   -- 回滚操作本身的 commit
    source_commit       VARCHAR(40) NOT NULL,   -- 被回滚的原始 commit
    
    -- 影响统计
    chunks_rolled_back  INTEGER DEFAULT 0,      -- 内容改回旧版本的 chunk 数
    chunks_restored     INTEGER DEFAULT 0,      -- 重新出现的 chunk 数
    chunks_deleted      INTEGER DEFAULT 0,      -- 被撤销删除的 chunk 数
    relations_restored  INTEGER DEFAULT 0,      -- 恢复的关联关系数
    anchors_restored    INTEGER DEFAULT 0,      -- 恢复的锚点数
    stale_anchors_cleared INTEGER DEFAULT 0,    -- 清除的文档 stale 标记数
    
    -- 文档影响
    doc_pr_closed       VARCHAR(512),           -- 被关闭的文档 PR 地址
    
    -- 触发来源
    triggered_by        VARCHAR(32) DEFAULT 'MANUAL',
    -- 'MANUAL'           人工执行 git revert
    -- 'AGENT_SUGGESTED'  Agent 建议后人工确认
    -- 'CI_AUTO'          CI/CD 自动回滚
    
    status              VARCHAR(32) DEFAULT 'COMPLETED',
    created_at          TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_rollback_source ON rollback_history(source_commit);
CREATE INDEX idx_rollback_commit ON rollback_history(rollback_commit);
```

------

### 三、`change_type` 枚举值扩展

现有值：`ADDED`、`MODIFIED`、`DELETED`

新增值：

| 新值       | 含义                      | 判断条件                                                     |
| ---------- | ------------------------- | ------------------------------------------------------------ |
| `ROLLBACK` | 内容改回旧版本            | MODIFIED 且 new_content_hash == 该 chunk 历史某条记录的 old_content_hash |
| `RESTORED` | 之前删除的 chunk 重新出现 | ADDED 且该 chunk_id 在 change_history 中存在 DELETED 记录    |

------

### 四、增量管道新增逻辑：回滚识别 + 恢复分支

在现有增量管道的 **Step 2（变更检测）** 之后，插入以下分支：

#### 4.1 回滚识别逻辑

```
对每个变更的 chunk:

IF change_type == MODIFIED:
    查询: SELECT old_content_hash FROM change_history 
          WHERE chunk_id = 当前chunk_id 
          ORDER BY changed_at DESC;
    
    IF 当前 new_content_hash 命中历史 old_content_hash:
        → 标记为 ROLLBACK
        → 记录 rollback_source_commit = 那条历史记录的 git_commit_hash

IF change_type == ADDED:
    查询: SELECT * FROM change_history 
          WHERE chunk_id = 当前chunk_id AND change_type = 'DELETED';
    
    IF 存在 DELETED 记录:
        → 标记为 RESTORED
        → 记录 rollback_source_commit = 那条 DELETED 记录的 git_commit_hash

辅助判断: commit message 包含 "Revert" / "revert" / "回滚"
```

#### 4.2 恢复操作（全部是 UPDATE，不改表结构）

识别为回滚后，执行以下恢复操作：

```
① 关联关系恢复:
   UPDATE chunk_relations 
   SET is_stale = FALSE, stale_reason = NULL, updated_at = NOW()
   WHERE is_stale = TRUE 
     AND stale_reason LIKE '%' || source_commit || '%';

② 锚点映射恢复:
   UPDATE anchor_mappings 
   SET is_active = TRUE, deactivated_at = NULL, deactivated_by_commit = NULL
   WHERE is_active = FALSE 
     AND deactivated_by_commit = source_commit;

③ 文档 stale 标记清除:
   UPDATE doc_chunks 
   SET stale_anchors = stale_anchors - '对应锚点名', updated_at = NOW()
   WHERE stale_anchors ? '对应锚点名';

④ 调用图边恢复:
   UPDATE call_graph 
   SET is_deleted = FALSE
   WHERE is_deleted = TRUE 
     AND git_commit_hash = source_commit;

⑤ 语义嵌入 (Milvus):
   ROLLBACK  → 重新计算嵌入 → 更新向量
   RESTORED  → 重新计算嵌入 → is_deleted 改为 FALSE
   撤销新增  → is_deleted 改为 TRUE

⑥ 图嵌入 (GNN):
   受影响节点 + 2跳邻居 → 局部 GNN 前向传播 → 更新 Milvus + PG

⑦ ES 索引:
   ROLLBACK → update
   RESTORED → index
   撤销新增 → delete

⑧ 文档维护 Agent:
   查询 sync_tasks WHERE doc_pr_status = 'OPEN' AND 关联 source_commit
   → 关闭 PR，doc_pr_status 改为 'CLOSED_BY_ROLLBACK'

⑨ 写入 rollback_history 表
```

## 19、模型部署方案

#### RTX 4050 (8GB) 本地部署方案 — LLM 走 API

------

#### 一、部署分工总览

| 类别                     | 部署方式       | 说明                 |
| ------------------------ | -------------- | -------------------- |
| 代码嵌入                 | ✅ 本地 GPU     | CodeBERT / UniXcoder |
| 文档嵌入                 | ✅ 本地 GPU     | BGE-M3               |
| 图嵌入 GNN               | ✅ 本地 GPU     | R-GCN                |
| 粗排                     | ✅ 本地 GPU     | bge-reranker-base    |
| 精排                     | ✅ 本地 GPU     | bge-reranker-v2-m3   |
| 全文检索                 | ✅ 本地 CPU     | Elasticsearch BM25   |
| **LLM 生成/问答/Agent**  | 🔗 **API 调用** | DeepSeek / Qwen API  |
| **社区摘要生成**         | 🔗 **API 调用** | DeepSeek / Qwen API  |
| **回滚识别中的语义判断** | 🔗 **API 调用** | DeepSeek / Qwen API  |

------

#### 二、本地部署模型（全部跑在 GPU 上）

| 用途     | 模型                   | 参数量 | 显存占用   | 备注                  |
| -------- | ---------------------- | ------ | ---------- | --------------------- |
| 代码嵌入 | **CodeBERT**           | 125M   | ~500MB     | 微软开源，Java 支持好 |
| 文档嵌入 | **BGE-M3**             | 568M   | ~1.2GB     | 智源开源，中英文混合  |
| 图嵌入   | **R-GCN (2层, 128维)** | ~3M    | ~200MB     | DGL/PyG 训练推理      |
| 粗排     | **bge-reranker-base**  | 278M   | ~600MB     | 智源开源              |
| 精排     | **bge-reranker-v2-m3** | 568M   | ~1.2GB     | 智源开源              |
| **合计** |                        |        | **~3.7GB** | **剩余 4.3GB 空闲**   |

> 所有本地模型可以同时加载，总显存仅 3.7GB，8GB 显卡非常宽裕。

------

#### 三、API 调用的 LLM 模型

###### 3.1 推荐 API 服务

| 服务             | 模型                        | 价格（参考）                         | 适用场景                  |
| ---------------- | --------------------------- | ------------------------------------ | ------------------------- |
| **DeepSeek API** | DeepSeek-V3 / DeepSeek-R1   | 输入 ¥1/百万token，输出 ¥2/百万token | 日常问答、Agent、代码理解 |
| **阿里云百炼**   | Qwen3-235B-A22B / Qwen3-32B | 按量计费                             | 复杂推理、社区摘要        |
| **硅基流动**     | Qwen3-32B / DeepSeek-V3     | 按量计费                             | 备选                      |
| **OpenAI**       | GPT-4o                      | $2.5/百万token                       | 备选                      |

###### 3.2 各场景 API 调用分配

| 场景                   | 调用频率               | 推荐模型    | 说明                   |
| ---------------------- | ---------------------- | ----------- | ---------------------- |
| 用户问答生成           | 每次查询 1 次          | DeepSeek-V3 | 延迟低、成本低         |
| Agent 推理 (路由/规划) | 每次查询 1~3 次        | DeepSeek-V3 | 需要较强推理能力       |
| 社区摘要生成           | 离线批量，图变更时触发 | Qwen3-32B   | 长上下文、批量处理     |
| 回滚语义判断           | 低频，回滚时触发       | DeepSeek-V3 | 判断代码变更是否为回滚 |
| 文档腐化检测           | 低频，定时任务         | Qwen3-32B   | 需要长上下文对比       |
| 测试用例生成           | 按需                   | DeepSeek-R1 | 需要深度推理           |

###### 3.3 API 调用封装建议

```python
# 统一 LLM 调用接口，方便切换供应商
class LLMClient:
    def __init__(self, provider="deepseek", model="deepseek-chat"):
        self.provider = provider
        self.model = model
    
    def chat(self, messages, temperature=0.7, max_tokens=4096):
        """通用对话接口"""
        ...
    
    def batch_summarize(self, texts, system_prompt):
        """批量摘要（社区摘要生成用）"""
        ...
    
    def judge(self, prompt):
        """判断类任务（回滚识别、腐化检测用）"""
        ...
```

------

#### 四、本地显存预算（最终版）

#### 在线检索（实时请求）

```
┌─────────────────────────────────────────────────┐
│  GPU 显存 8GB                                    │
│                                                  │
│  ┌──────────────────────────────────────────┐   │
│  │ CodeBERT          500MB                  │   │
│  │ BGE-M3           1200MB                  │   │
│  │ R-GCN             200MB                  │   │
│  │ bge-reranker-base 600MB                  │   │
│  │ bge-reranker-v2-m3 1200MB               │   │
│  │                                          │   │
│  │ 已用: ~3.7GB    空闲: ~4.3GB            │   │
│  └──────────────────────────────────────────┘   │
│                                                  │
│  LLM 生成 → API 调用 (不占本地显存)             │
└─────────────────────────────────────────────────┘
```

#### 离线索引构建（批量）

```
┌─────────────────────────────────────────────────┐
│  GPU 显存 8GB                                    │
│                                                  │
│  ┌──────────────────────────────────────────┐   │
│  │ CodeBERT (批量编码)   500MB              │   │
│  │ BGE-M3 (批量编码)    1200MB              │   │
│  │ R-GCN (训练/推理)     200MB              │   │
│  │                                          │   │
│  │ 已用: ~1.9GB    空闲: ~6.1GB            │   │
│  └──────────────────────────────────────────┘   │
│                                                  │
│  社区摘要 → API 批量调用 (不占本地显存)          │
└─────────────────────────────────────────────────┘
```

------

#### 五、最终推荐清单

| 用途             | 模型                 | 部署方式 | 显存  |
| ---------------- | -------------------- | -------- | ----- |
| 代码嵌入         | CodeBERT / UniXcoder | 本地 GPU | 500MB |
| 文档嵌入         | BGE-M3               | 本地 GPU | 1.2GB |
| 图嵌入           | R-GCN (2层, 128维)   | 本地 GPU | 200MB |
| 粗排             | bge-reranker-base    | 本地 GPU | 600MB |
| 精排             | bge-reranker-v2-m3   | 本地 GPU | 1.2GB |
| 全文检索         | Elasticsearch        | 本地 CPU | 0     |
| **问答生成**     | **DeepSeek-V3**      | **API**  | **0** |
| **Agent 推理**   | **DeepSeek-V3**      | **API**  | **0** |
| **社区摘要**     | **Qwen3-32B**        | **API**  | **0** |
| **回滚语义判断** | **DeepSeek-V3**      | **API**  | **0** |
| **文档腐化检测** | **Qwen3-32B**        | **API**  | **0** |
| **测试生成**     | **DeepSeek-R1**      | **API**  | **0** |

------

#### 六、一句话结论

**LLM 全部走 API 后，本地 GPU 只需跑嵌入 + 排序 + GNN，总共 3.7GB 显存，RTX 4050 的 8GB 完全够用，还有 4.3GB 余量。** 不需要做任何量化或模型裁剪，全部 FP16 原精度运行即可。