"""M46 Task2：跨类调用四步解析（定型/fallback/继承分发/this-super 同类）——纯函数 + FakeSession 编排。"""
from __future__ import annotations

from pathlib import Path

from app.db.models import CallGraph, CodeChunk
from app.pipeline.parsing.doc_element import CodeClass, CodeMethod
from app.pipeline.relations import (
    _descendants,
    _infer_type,
    _param_types,
    build_call_graph,
    build_type_edges,
)

# ---------- 纯函数：定型 ----------

def _m(params=(), local=None):
    return CodeMethod(
        name="send", class_name="Producer", signature="void send()", modifiers=["public"],
        return_type=None, parameters=list(params), annotations=[], javadoc=None,
        start_line=1, end_line=2, source="", local_types=local or {},
    )


def _cls(fields=None):
    return CodeClass(
        name="Producer", kind="class", modifiers=["public"], annotations=[],
        javadoc=None, superclass=None, interfaces=[], start_line=1, end_line=2,
        fields=fields or {},
    )


def test_param_types_strips_final_annotation_and_generics():
    m = _m(params=["final MessageStore store", "@NotNull Map<String, String> cache", "Msg msg"])
    assert _param_types(m) == {"store": "MessageStore", "cache": "Map", "msg": "Msg"}


def test_infer_type_priority_param_over_field_over_local():
    m = _m(params=["RemotingClient client"], local={"store": "LocalStore"})
    cls = _cls(fields={"store": "FieldStore", "client": "FieldClient"})
    assert _infer_type("client", m, cls) == "RemotingClient"   # 参数最高
    assert _infer_type("store", m, cls) == "FieldStore"        # 字段次之（局部无 store）
    m2 = _m(params=[], local={"only_local": "LocalType"})
    assert _infer_type("only_local", m2, _cls()) == "LocalType"
    assert _infer_type("unknown", m2, _cls()) is None          # 全 miss → None（交 fallback）


def test_descendants_bfs_and_cycle_guard():
    children = {"MessageStore": {"DefaultMessageStore"}, "DefaultMessageStore": {"MappedStore"},
                "A": {"B"}, "B": {"A"}}  # A↔B 病态环
    assert _descendants("MessageStore", children) == {"DefaultMessageStore", "MappedStore"}
    assert _descendants("A", children) == {"B"}               # 防环不死循环
    assert _descendants("NoChildren", children) == set()


# ---------- 编排：FakeSession 端到端（内存，无基础设施） ----------

class _Result:
    def __init__(self, rows=None):
        self._rows = rows or []

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _FakeSession:
    """build_call_graph 用到的最小面：execute(select)→chunks；execute(delete)→None；add/flush。"""

    def __init__(self, chunks):
        self._chunks = chunks
        self.added: list = []

    def execute(self, stmt):
        if "code_chunks" in str(stmt):
            return _Result(self._chunks)
        return _Result()

    def add(self, obj):
        self.added.append(obj)

    def flush(self):
        pass


def _chunk(chunk_id, cls, method, implements=None, extends=None):
    return CodeChunk(
        chunk_id=chunk_id, file_id=1, chunk_type="method", class_name=cls,
        method_name=method, method_signature=None, access_modifier=None, return_type=None,
        start_line=1, end_line=2, content="x", content_hash="h",
        implements_interface=implements, extends_class=extends,
        git_commit_hash="c", code_anchor_key=f"{cls}.{method}",
    )


STORE_SRC = """package demo;
public interface MessageStore { void put(Msg m); }
"""

IMPL_SRC = """package demo;
public class DefaultMessageStore implements MessageStore { public void put(Msg m) {} }
"""

PRODUCER_SRC = """package demo;
public class Producer {
    private final MessageStore store;
    private Msg msg;
    public void send(final Msg message) {
        Helper h = new Helper();
        store.put(msg);
        h.assist();
        Validator.check(msg);
        this.send(msg);
        retry();
    }
    void retry() {}
}
"""


def test_build_call_graph_cross_class_edges(tmp_path: Path):
    (tmp_path / "MessageStore.java").write_text(STORE_SRC, encoding="utf-8")
    (tmp_path / "DefaultMessageStore.java").write_text(IMPL_SRC, encoding="utf-8")
    (tmp_path / "Producer.java").write_text(PRODUCER_SRC, encoding="utf-8")

    chunks = [
        _chunk("c_ms_put", "MessageStore", "put"),
        _chunk("c_impl_put", "DefaultMessageStore", "put", implements="MessageStore"),
        _chunk("c_prod_send", "Producer", "send"),
        _chunk("c_prod_retry", "Producer", "retry"),
        _chunk("c_help_assist", "Helper", "assist"),
        _chunk("c_val_check", "Validator", "check"),
    ]
    session = _FakeSession(chunks)
    stats = build_call_graph(session, tmp_path)
    edges = {(e.caller_chunk_id, e.callee_chunk_id) for e in session.added if isinstance(e, CallGraph)}

    assert stats["call_edges"] >= 5
    # 字段定型 + 接口闭包分发：Producer.send → MessageStore.put 与 DefaultMessageStore.put 都连
    assert ("c_prod_send", "c_ms_put") in edges
    assert ("c_prod_send", "c_impl_put") in edges
    # 局部变量定型
    assert ("c_prod_send", "c_help_assist") in edges
    # fallback：receiver 名直接当类名（静态调用）
    assert ("c_prod_send", "c_val_check") in edges
    # this/无 receiver → 同类（现行为回归）
    assert ("c_prod_send", "c_prod_send") in edges
    assert ("c_prod_send", "c_prod_retry") in edges


# ---------- CLI：ingest_code.py 默认 build_relations=True + --no-relations 逃生口 ----------

def _run_cli(monkeypatch, tmp_path, argv_extra=()):
    import scripts.ingest_code as cli

    captured: dict = {}

    def fake_ingest_repo(session, repo, **kw):
        captured.update(kw)
        return {"details": [], "errors": []}

    monkeypatch.setattr(cli, "ingest_repo", fake_ingest_repo)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "A.java").write_text("class A {}", encoding="utf-8")
    rc = cli.main(["--repo", str(repo), "--module", "demo", *argv_extra])
    return rc, captured


def test_cli_defaults_build_relations_true(monkeypatch, tmp_path):
    rc, captured = _run_cli(monkeypatch, tmp_path)
    assert rc == 0
    assert captured["build_relations"] is True


def test_cli_no_relations_flag(monkeypatch, tmp_path):
    rc, captured = _run_cli(monkeypatch, tmp_path, ["--no-relations"])
    assert rc == 0
    assert captured["build_relations"] is False


# ---------- M32 ②：物化 implements/extends 边 ----------

class _RecordingSession(_FakeSession):
    """在 _FakeSession 上记录 delete 语句，验证幂等先删。"""

    def __init__(self, chunks):
        super().__init__(chunks)
        self.deletes: list[str] = []

    def execute(self, stmt):
        s = str(stmt)
        if s.startswith("DELETE"):
            self.deletes.append(s)
            return _Result()
        return super().execute(stmt)


def _tchunk(chunk_id, cls, chunk_type="method", start_line=10, extends=None, implements=None):
    return CodeChunk(
        chunk_id=chunk_id, file_id=1, chunk_type=chunk_type, class_name=cls,
        method_name="m" if chunk_type == "method" else None, method_signature=None,
        access_modifier=None, return_type=None, start_line=start_line, end_line=start_line + 5,
        content="c", content_hash="h", javadoc=None, inline_comments=[], annotations=[],
        implements_interface=implements, extends_class=extends, type_parameters=[],
        code_anchor_key=None, git_commit_hash="H", keywords=[], token_count=1,
        embedding_synced=False,
    )


def test_build_type_edges_representative_and_edges():
    chunks = [
        _tchunk("iface_cls", "IFoo", chunk_type="class"),                     # 接口类代表
        _tchunk("impl_m1", "Impl", start_line=10, implements="IFoo"),          # Impl 代表（首个 method）
        _tchunk("impl_m2", "Impl", start_line=99, implements="IFoo"),
        _tchunk("sub_m1", "Sub", start_line=5, extends="Impl", implements="IFoo, Bar"),
        _tchunk("bar_cls", "Bar", chunk_type="class"),
    ]
    s = _RecordingSession(chunks)
    stats = build_type_edges(s)
    got = {(r.source_chunk_id, r.target_chunk_id, r.relation_type) for r in s.added}
    assert ("impl_m1", "iface_cls", "CODE_IMPLEMENTS") in got     # 代表=class 优先/首 method
    assert ("impl_m1", "impl_m1", "CODE_IMPLEMENTS") not in got   # Impl 自身 implements 边只经代表
    assert ("sub_m1", "impl_m1", "CODE_EXTENDS") in got
    assert ("sub_m1", "bar_cls", "CODE_IMPLEMENTS") in got        # implements CSV 多边
    assert stats["type_edges"] == len(got) >= 3
    # 幂等先删：检查执行了 DELETE（SQLAlchemy postcompile 使字符串中不直接包含值）
    assert len(s.deletes) > 0 and "DELETE FROM chunk_relations" in s.deletes[0]


def test_build_type_edges_skips_unknown_parent_and_class_representative_preferred():
    chunks = [
        _tchunk("a_class", "A", chunk_type="class", extends="Nowhere"),   # 父类不在库 → 无边
        _tchunk("b_m5", "B", start_line=50),
        _tchunk("b_m1", "B", start_line=1),
        _tchunk("b_class", "B", chunk_type="class"),
        _tchunk("c_m", "C", start_line=1, extends="B"),
    ]
    s = _RecordingSession(chunks)
    build_type_edges(s)
    got = {(r.source_chunk_id, r.target_chunk_id) for r in s.added}
    assert ("c_m", "b_class") in got      # class chunk 优先于更小的 start_line
    assert not any(t == "a_class" for _, t in got)
