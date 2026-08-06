"""Phase 7 多 Agent 地基（Milestone 1）。

把现有 chat_service 的检索流水线重表达为一个 LangGraph StateGraph：
    START → query_analysis → retrieve → generate → post_process → END

- 节点复用既有检索/LLM 代码（query_understanding / pipeline.recall / llm.stream_tokens），
  行为与 legacy stream_chat 同构。
- 节点内通过 get_stream_writer() 推 SSE 事件（retrieval/citation/token），
  streaming.stream_graph 适配器把它们转成与 legacy 一致的 (event, data) 序列并负责落库。
- 默认不启用：settings.rag_engine == "langgraph" 时才走本图，否则 stream_chat 走 legacy。

后续 Milestone（Orchestrator 路由 / 8 场景 Agent / 20 @tool / 检索子图 fan-out / 面板 API）在此之上扩展。
"""
