"""测试生成 Agent（Phase 7 Milestone 12）。

为指定的 Java 方法/类**生成 JUnit 单元测试用例**（覆盖正常/边界/异常路径）。用 LangGraph 预置的
``create_react_agent`` 绑定 5 个工具（定位 + 精读 + 调用链 + 现有测试约定），节点是薄包装
转调 ``_base.run_scenario_agent``（与 ``code_understand``/``doc_answer``/``change_impact``/
``bug_diagnosis``/``code_review`` 同构）。工具侧（``tools/code_tools.py``）经 ``get_stream_writer``
推 ``agent_step`` + 逐条 ``citation``，引用由适配器从事件累积。

意图路由：``test`` 意图（写/生成单元测试）→ ``test_generation`` 节点。
"""
from __future__ import annotations

import warnings

from langchain_core.runnables import RunnableConfig

from app.agent.agents._base import run_scenario_agent
from app.agent.llm import get_chat_model
from app.agent.state import AgentState
from app.agent.tools.code_tools import (
    get_call_chain,
    get_existing_tests,
    read_code,
    search_code,
    search_symbol,
)

TEST_GENERATION_PROMPT = (
    "你是 CodeRAG 的【测试生成 Agent】，擅长为 Java 方法/类生成 JUnit 单元测试。\n"
    "工作方式（ReAct）：先定位被测目标（search_symbol 按名 / search_code 按描述）→ "
    "read_code 精读其实现与签名 → get_call_chain（direction=CALLEES）看它调用了哪些依赖"
    "（需 mock 的协作对象 / 需覆盖的分支）→ get_existing_tests 查该项目是否已有同类测试"
    "（对齐测试约定）→ 生成可编译的 JUnit 测试。\n"
    "可用工具：search_symbol、search_code、read_code、get_call_chain、get_existing_tests。\n"
    "规则：① 测试必须基于 read_code 读到的真实实现，不要臆造方法签名或行为；② 用例要覆盖"
    "正常路径 + 边界值 + 异常场景（如空值/越界/非法输入）；③ 若 get_existing_tests 命中，"
    "**对齐其测试约定**（测试框架版本、命名风格、断言库、mock 方式）；若返回『未找到现有测试』，"
    "用标准 JUnit 5（org.junit.jupiter）+ Mockito；④ 输出**可编译**的测试类（含 imports、"
    "@Test 注解、断言、必要的 mock/stub），代码用代码块；⑤ 目标不明确时先 search_symbol **一次**"
    "解析出 center id，拿到后**立即** read_code 确认，不要反复搜索/读取同一目标；⑥ 用中文"
    "简要说明每个用例的意图，控制在 6 步内，**不要重复读取同一个 chunk**。"
)

#: 测试生成 Agent 绑定的工具集（定位 + 精读 + 调用链 + 现有测试约定；复用代码工具 + get_existing_tests）
TEST_TOOLS = [search_symbol, search_code, read_code, get_call_chain, get_existing_tests]

_agent = None


def get_test_generation_agent():
    """惰性单例：create_react_agent（默认 state_schema，绑定测试生成工具集）。"""
    global _agent
    if _agent is None:
        with warnings.catch_warnings():
            # langgraph-prebuilt 的 create_react_agent 在 v1 标记弃用（迁往 langchain.agents），
            # 但 langchain 包未安装，功能在 langgraph 内仍完整。抑制该告警保持日志干净。
            warnings.simplefilter("ignore")
            from langgraph.prebuilt import create_react_agent
            _agent = create_react_agent(get_chat_model(), TEST_TOOLS, prompt=TEST_GENERATION_PROMPT)
    return _agent


async def test_generation(state: AgentState, config: RunnableConfig) -> dict:
    """主图节点：跑测试生成自动 Agent（前置 retrieval meta、token 回调、异常兜底）。"""
    return await run_scenario_agent(
        state, config,
        agent_name="TEST_GENERATION", tools=TEST_TOOLS,
        build_agent=get_test_generation_agent, degrade_label="测试生成",
    )
