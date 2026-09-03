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


async def test_search_entities(seeded_graph):
    from app.services.graph_service import search_entities
    r = await search_entities(seeded_graph, q="Flush", repo="r", limit=10)
    assert [i["name"] for i in r["items"]] == ["FlushService#flush"]


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
