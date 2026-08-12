"""现有 8 个 Agent 的 Registry 登记（M33 渐进迁移）。

import 副作用注册（由 ``registry.get_registry`` 首次访问触发，仅执行一次）。不删/不改任何
现有 agent 模块——只把路由相关元信息（agent_type/intent/node_name/node_fn/route_guard）登记
进 Registry；``tools``/``prompt``/``build_agent``/``degrade_label`` 等「元信息集中」字段留空，
后续渐进回填。DOC_MAINTAIN 是 HITL 链，这里只登记其入口节点 ``propose``。
"""
from __future__ import annotations

from app.agent.agents.bug_diagnosis import bug_diagnosis
from app.agent.agents.change_impact import change_impact
from app.agent.agents.code_review import code_review
from app.agent.agents.code_understand import code_understand
from app.agent.agents.doc_answer import doc_answer
from app.agent.agents.test_generation import test_generation
from app.agent.agents.web import web_search
from app.agent.nodes.doc_maintain import propose
from app.agent.registry import AgentSpec, register
from app.agent.tools.web_tools import get_web_tools

register(AgentSpec(agent_type="CODE_UNDERSTAND", node_name="code_understand",
                  node_fn=code_understand, intent="code"))
register(AgentSpec(agent_type="DOC_ANSWER", node_name="doc_answer",
                  node_fn=doc_answer, intent="doc"))
register(AgentSpec(agent_type="CHANGE_IMPACT", node_name="change_impact",
                  node_fn=change_impact, intent="graph"))
register(AgentSpec(agent_type="BUG_DIAGNOSIS", node_name="bug_diagnosis",
                  node_fn=bug_diagnosis, intent="bug"))
register(AgentSpec(agent_type="CODE_REVIEW", node_name="code_review",
                  node_fn=code_review, intent="review"))
register(AgentSpec(agent_type="TEST_GENERATION", node_name="test_generation",
                  node_fn=test_generation, intent="test"))
register(AgentSpec(agent_type="WEB_SEARCH", node_name="web_search",
                  node_fn=web_search, intent="web", route_guard=get_web_tools))
register(AgentSpec(agent_type="DOC_MAINTAIN", node_name="propose", node_fn=propose))
