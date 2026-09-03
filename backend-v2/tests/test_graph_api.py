"""Task 2：graph 读 API。服务层 seed 真 SQL 断言图形状；API 层空库契约 + 参数校验。"""
import pytest
from sqlalchemy import text

_ENTITIES = [  # (id, type, cls, method, module, file)
    (1, "class", "CommitLog", None, "broker", "broker/src/main/CommitLog.java"),
    (2, "method", "CommitLog", "putMessage", "broker", "broker/src/main/CommitLog.java"),
    (3, "method", "FlushService", "flush", "store", "store/src/main/FlushService.java"),
    (4, "method", "MappedFile", "append", "store", "store/src/main/MappedFile.java"),
]
_EDGES = [(2, 3, "cross_class"), (3, 4, "cross_class")]


@pytest.fixture
async def seeded_graph():
    """会话姿势逐字沿 test_chat_service.async_session（NullPool + 连接级事务 + rollback）；
    显式 id INSERT 便于 CTE 断言可读（回滚不留痕，序列不受影响）。"""
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.core.config import settings

    engine = create_async_engine(settings.postgres_dsn, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            tx = await conn.begin()
            session = AsyncSession(bind=conn, expire_on_commit=False)
            await session.execute(text(
                "INSERT INTO code_entities (id, repo, entity_type, class_name, method_name,"
                " module, file_path) VALUES (:id, 'r', :t, :c, :m, :mo, :f)"), [
                    dict(zip(("id", "t", "c", "m", "mo", "f"), e)) for e in _ENTITIES])
            await session.execute(text(
                "INSERT INTO call_edges (caller_id, callee_id, call_type, call_site_file,"
                " call_site_line) VALUES (:a, :b, :t, 'x.java', 1)"), [
                    {"a": a, "b": b, "t": t} for a, b, t in _EDGES])
            yield session
            await session.close()
            await tx.rollback()
    finally:
        await engine.dispose()


async def test_call_graph_class_center_two_hops(seeded_graph):
    from app.services.graph_service import call_graph
    g = await call_graph(seeded_graph, repo="r", class_name="CommitLog", method=None,
                         direction="CALLEES", depth=2, max_nodes=50)
    assert g["center"] == "2"  # putMessage（类中心落到唯一方法种子）
    ids = {n["id"] for n in g["nodes"]}
    assert {"2", "3", "4"} <= ids
    assert ("2", "3") in {(e["source"], e["target"]) for e in g["edges"]}
    assert g["truncated"] is False


async def test_call_graph_method_center_callers(seeded_graph):
    from app.services.graph_service import call_graph
    g = await call_graph(seeded_graph, repo="r", class_name="MappedFile", method="append",
                         direction="CALLERS", depth=2, max_nodes=50)
    assert {n["id"] for n in g["nodes"]} >= {"4", "3", "2"}
    assert ("3", "4") in {(e["source"], e["target"]) for e in g["edges"]}


async def test_call_graph_max_nodes_truncates(seeded_graph):
    from app.services.graph_service import call_graph
    g = await call_graph(seeded_graph, repo="r", class_name="CommitLog", method="putMessage",
                         direction="CALLEES", depth=2, max_nodes=2)
    assert len(g["nodes"]) <= 2 and g["truncated"] is True


# ---- 终审 promised fixture：环 / diamond / BOTH 三条 CTE 无测路径（repo 'g2' 与主夹具隔离）----

_SHAPES_ENTITIES = [  # (id, type, cls, method, module, file) —— 全方法实体（种子 SQL 要求 method 非空）
    (11, "method", "CycA", "run", "ma", "a/CycA.java"),
    (12, "method", "CycB", "call", "ma", "a/CycB.java"),
    (21, "method", "DiaA", "start", "md", "a/DiaA.java"),
    (22, "method", "DiaB", "step1", "md", "a/DiaB.java"),
    (23, "method", "DiaC", "step2", "md", "a/DiaC.java"),
    (24, "method", "DiaD", "end", "md", "a/DiaD.java"),
]
# (caller, callee, type, call_site_line)：环两条有向边；diamond 上半 A→B 平行两调用点
# （line 1/2，合法：uk=caller+callee+line）→ BOTH/权重断言用。
_SHAPES_EDGES = [
    (11, 12, "cross_class", 1), (12, 11, "cross_class", 2),               # 2-cycle CycA↔CycB
    (21, 22, "cross_class", 1), (21, 22, "cross_class", 2),               # DiaA→DiaB ×2
    (21, 23, "cross_class", 3),                                            # DiaA→DiaC
    (22, 24, "cross_class", 4), (23, 24, "cross_class", 5),               # DiaB→DiaD / DiaC→DiaD
]


@pytest.fixture
async def seeded_shapes():
    """环/diamond/BOTH 夹具：姿势逐字沿 seeded_graph（显式 id、回滚不留痕），repo='g2' 隔离。"""
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.core.config import settings

    engine = create_async_engine(settings.postgres_dsn, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            tx = await conn.begin()
            session = AsyncSession(bind=conn, expire_on_commit=False)
            await session.execute(text(
                "INSERT INTO code_entities (id, repo, entity_type, class_name, method_name,"
                " module, file_path) VALUES (:id, 'g2', :t, :c, :m, :mo, :f)"), [
                    dict(zip(("id", "t", "c", "m", "mo", "f"), e)) for e in _SHAPES_ENTITIES])
            await session.execute(text(
                "INSERT INTO call_edges (caller_id, callee_id, call_type, call_site_file,"
                " call_site_line) VALUES (:a, :b, :t, 'x.java', :ln)"), [
                    {"a": a, "b": b, "t": t, "ln": ln} for a, b, t, ln in _SHAPES_EDGES])
            yield session
            await session.close()
            await tx.rollback()
    finally:
        await engine.dispose()


async def test_call_graph_two_cycle_terminates(seeded_shapes):
    """2-cycle A↔B：visited 数组防环必须终止（depth 放到 5 足够裸递归转死循环）。"""
    from app.services.graph_service import call_graph
    g = await call_graph(seeded_shapes, repo="g2", class_name="CycA", method="run",
                         direction="CALLEES", depth=5, max_nodes=50)
    assert g["center"] == "11"
    assert {n["id"] for n in g["nodes"]} == {"11", "12"}   # 两节点都返回，未死循环
    assert {(e["source"], e["target"]) for e in g["edges"]} == {("11", "12")}
    # 回边 12→11 被 visited 挡住（callee 已在 visited 数组）——CALLEES 侧不可达


async def test_call_graph_two_cycle_both_directions(seeded_shapes):
    """2-cycle + BOTH：两方向各跑一次合并后，两条有向边都在、节点集按 id 去重。"""
    from app.services.graph_service import call_graph
    g = await call_graph(seeded_shapes, repo="g2", class_name="CycA", method="run",
                         direction="BOTH", depth=5, max_nodes=50)
    pairs = {(e["source"], e["target"]): e["weight"] for e in g["edges"]}
    assert set(pairs) == {("11", "12"), ("12", "11")}
    assert all(w >= 1 for w in pairs.values())
    assert {n["id"] for n in g["nodes"]} == {"11", "12"}   # 节点集去重（不因两方向翻倍）


async def test_call_graph_diamond_depth2(seeded_shapes):
    """diamond A→B→D + A→C→D：depth 2 下四节点、四条边全活（两条中继路径都可达 D）。"""
    from app.services.graph_service import call_graph
    g = await call_graph(seeded_shapes, repo="g2", class_name="DiaA", method="start",
                         direction="CALLEES", depth=2, max_nodes=50)
    assert g["center"] == "21"
    assert {n["id"] for n in g["nodes"]} == {"21", "22", "23", "24"}
    assert {(e["source"], e["target"]) for e in g["edges"]} == {
        ("21", "22"), ("21", "23"), ("22", "24"), ("23", "24")}
    assert g["truncated"] is False


async def test_call_graph_both_merges_callers_and_callees(seeded_shapes):
    """BOTH = CALLERS ∪ CALLEES：以 B 为中心，上游 A→B（平行×2）与下游 B→D 同图，
    节点集按 id 去重合并、边集按 (source,target) 合并且 weight=调用点数。"""
    from app.services.graph_service import call_graph
    g = await call_graph(seeded_shapes, repo="g2", class_name="DiaB", method="step1",
                         direction="BOTH", depth=2, max_nodes=50)
    assert g["center"] == "22"
    assert {n["id"] for n in g["nodes"]} == {"21", "22", "24"}
    w = {(e["source"], e["target"]): e["weight"] for e in g["edges"]}
    assert set(w) == {("21", "22"), ("22", "24")}
    assert w[("21", "22")] == 2      # 两条平行调用点边（call_site_line 1/2）合并计数
    assert w[("22", "24")] == 1


async def test_search_entities(seeded_graph):
    from app.services.graph_service import search_entities
    r = await search_entities(seeded_graph, q="Flush", repo="r", limit=10)
    assert [i["name"] for i in r["items"]] == ["FlushService#flush"]
    # 终审修复：搜索项必须带 method_name（前端 pickCenter 依它发方法中心图；
    # 缺席则选方法也退化成类中心）。方法行携带真值、类行为 None。
    assert r["items"][0]["method_name"] == "flush" and r["items"][0]["type"] == "method"
    r2 = await search_entities(seeded_graph, q="CommitLog", repo="r", limit=10)
    assert {i["name"]: i["method_name"] for i in r2["items"]} == {
        "CommitLog#putMessage": "putMessage",   # 方法实体排前
        "CommitLog": None,                       # 类行 method_name=None
    }


async def test_module_deps(seeded_graph):
    from app.services.graph_service import module_deps_graph
    g = await module_deps_graph(seeded_graph, repo="r", max_nodes=60)
    assert ("broker", "store") in {(e["source"], e["target"]) for e in g["edges"]}
    assert all(n["type"] == "module" for n in g["nodes"])


def test_api_empty_and_validation(monkeypatch):
    from fastapi.testclient import TestClient

    from app.agent import tools_loader
    from app.main import app

    async def _noop_load(transports=None):
        return None

    monkeypatch.setattr(tools_loader, "load_tools", _noop_load)
    with TestClient(app) as client:
        assert client.get("/v1/graph/search", params={"q": "X", "repo": "r"}).json()["items"] == []
        g = client.get("/v1/graph/call-graph",
                       params={"repo": "r", "class_name": "Nope"}).json()
        assert g["nodes"] == [] and g["edges"] == []
        assert client.get("/v1/graph/search", params={"repo": "r"}).status_code == 422  # q 必填
        assert client.get("/v1/graph/call-graph",
                          params={"repo": "r", "class_name": "A", "depth": 9}).status_code == 422
