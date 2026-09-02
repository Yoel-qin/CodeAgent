"""节点 ``config`` 形参签名守卫（Task 10 评审遗留 ③）。

langgraph 按运行时**注解对象**识别节点可注入的 ``config`` 形参；模块一旦加
``from __future__ import annotations``（PEP 563），注解全部字符串化 → ``config``
被静默丢弃（``configurable`` 里的 session/cost/top_k 全落空）+ UserWarning，且无
任何报错。用 ``inspect.signature`` 拿真注解对象，断言其非 str 且属于白名单形态
（``RunnableConfig`` / ``Optional[RunnableConfig]``≡``RunnableConfig | None``）。
"""
import ast
import inspect

import pytest
from langchain_core.runnables import RunnableConfig

from app.agent import codenav, docqa, nodes, query_analysis

NODES = [
    query_analysis.query_analysis_node,
    codenav.codenav_node,
    docqa.docqa_node,
    nodes.retrieve_node,
    nodes.clarify_node,
]

MODULES = [query_analysis, codenav, docqa, nodes]

# 允许的注解形态（真注解对象；RunnableConfig | None 与 Optional[RunnableConfig] 在
# typing 下是相等且同 hash 的同一对象，集合两形态通吃）
_ALLOWED = {RunnableConfig, RunnableConfig | None}


@pytest.mark.parametrize("node", NODES, ids=lambda f: f.__name__)
def test_config_param_annotation_is_runtime_object(node):
    """``config`` 注解必须是真注解对象——字符串化（PEP 563）会被 langgraph 静默丢参。"""
    ann = inspect.signature(node).parameters["config"].annotation
    assert not isinstance(ann, str), (
        f"{node.__module__}.{node.__name__}: config 注解被字符串化 {ann!r}"
        "（from __future__ import annotations？）→ langgraph 静默丢 config")
    assert ann in _ALLOWED, f"{node.__name__}: 非白名单注解形态 {ann!r}"


@pytest.mark.parametrize("mod", MODULES, ids=lambda m: m.__name__)
def test_node_module_does_not_enable_pep563(mod):
    """节点模块不得启用 PEP 563（双保险：注解对象断言的成因侧守卫）。

    AST 层判 ``from __future__ import annotations`` 的模块级 ImportFrom——不用子串匹配
    （四个模块头部都有提及该句的警示注释，子串会误报）。
    """
    tree = ast.parse(inspect.getsource(mod))
    futures = [n for n in tree.body
               if isinstance(n, ast.ImportFrom) and n.module == "__future__"
               and any(a.name == "annotations" for a in n.names)]
    assert not futures
