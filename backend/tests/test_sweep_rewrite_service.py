"""SWEEP 批量重写服务（M17 run_sweep_rewrite / list_proposals / set_proposal_status）单测。

策略：``run_sweep_rewrite`` 的编排（池→去重→幂等守卫→逐段落循环→计数）通过 monkeypatch
``sweep_rewrite_service.generate_doc_update`` / ``create_doc_pr``（原语自身在
``test_doc_maintenance_service`` 已测）来隔离，假 session 只分发池/守卫查询。``list_proposals`` /
``set_proposal_status`` 用同一假 session 的 ORM-select / UPDATE 分支。

假 session ``execute`` 按 ``str(stmt).upper()`` 分发（顺序敏感）：
  - ``UPDATE`` → 状态翻转（按 proposal_id 找内存行）
  - ``SOURCE_CHUNK_ID`` → SWEEP 过时池（过滤 is_stale+SWEEP + LIMIT）
  - ``DISTINCT DOC_CHUNK_ID`` → 幂等守卫（active set ∩ :ids）
  - ``FROM DOC_UPDATE_PROPOSALS`` + ``COUNT`` → list 计数；否则 → list 行（SimpleNamespace，可分页/过滤）
"""
from __future__ import annotations

import re
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

import app.services.sweep_rewrite_service as srs
from app.pipeline.metadata import approx_token_count, content_hash

_FLOOR = datetime(2000, 1, 1, tzinfo=UTC)


def _dt() -> datetime:
    return datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _no_real_embed(monkeypatch):
    """隔离全部测试不触真实嵌入/Milvus/git——默认嵌入器禁用（approve→``lazy``）、index 桩返 True、
    fulfill 桩返 git_status=None（approve→维持 APPROVED，不触真 git）。

    走 eager 路径的单测在自身覆 ``srs.embedding_client.enabled``→True + ``srs.index_chunks_to_milvus`` 桩；
    走 git 路径的单测在自身覆 ``srs.fulfill_doc_update`` 桩。
    """
    monkeypatch.setattr(srs.embedding_client, "enabled", lambda: False)
    monkeypatch.setattr(srs, "index_chunks_to_milvus", lambda *a, **k: True)

    async def _no_git(session, proposal_id):
        return {"git_status": None, "commit_sha": None, "pr_url": None, "error": None}

    monkeypatch.setattr(srs, "fulfill_doc_update", _no_git)


# ---- 假结果对象（支持 mappings/scalars/first/scalar_one/rowcount 多种访问）----


class _Mappings:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


class _Result:
    def __init__(self, rows=None, scalar=None, rowcount=0):
        self._rows = rows or []
        self._scalar = scalar
        self.rowcount = rowcount

    def mappings(self):
        return _Mappings(self._rows)

    def scalars(self):
        return _Mappings(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None

    def scalar_one(self):
        return self._scalar


class _Session:
    """假 session：池 / 守卫 / list / UPDATE / M18 写回 SELECT 分发 + best-effort 分页过滤。"""

    def __init__(self, *, pool=None, active=None, proposals=None,
                 pool_boom=False, update_boom=False, doc_write_boom=False,
                 doc_chunk_exists=True):
        self._pool = pool or []
        self._active = set(active or [])
        self._proposals = list(proposals or [])
        self.pool_boom = pool_boom
        self.update_boom = update_boom
        self.doc_write_boom = doc_write_boom
        self.doc_chunk_exists = doc_chunk_exists
        self.rolled_back = False
        # M18 写回可观测槽
        self.written = None           # doc_chunks UPDATE 的 params（content/hash/tokens/cid）
        self.cleared_ids = None       # 清过时关系 UPDATE 的 relation_ids
        self.last_stats = None        # 幂等守卫传入的 status 集
        self.synced_cid = None        # M20 mark-synced UPDATE 的 cid（eager 重嵌入成功翻 flag 时）

    async def execute(self, stmt, params=None):
        sql = str(stmt).upper()
        params = params or {}
        if "EMBEDDING_SYNCED = TRUE" in sql:  # M20 mark-synced（区别于写回的 EMBEDDING_SYNCED = FALSE）
            self.synced_cid = params.get("cid")
            return _Result(rowcount=1)
        if sql.lstrip().startswith("UPDATE DOC_CHUNKS"):  # M18 写回 doc_chunks.content
            if self.doc_write_boom:
                raise RuntimeError("doc write boom")
            self.written = params
            return _Result(rowcount=1 if self.doc_chunk_exists else 0)
        if sql.lstrip().startswith("UPDATE CHUNK_RELATIONS"):  # M18 清关系过时
            self.cleared_ids = list(params.get("ids") or [])
            return _Result(rowcount=len(self.cleared_ids))
        if sql.lstrip().startswith("UPDATE"):  # set_proposal_status 状态翻转（doc_update_proposals）
            if self.update_boom:
                raise RuntimeError("update boom")
            pid = params["pid"]
            for p in self._proposals:
                if p.proposal_id == pid:
                    p.status = params["status"]
                    return _Result(rowcount=1)
            return _Result(rowcount=0)
        if self.pool_boom and "SOURCE_CHUNK_ID" in sql:
            raise RuntimeError("pool boom")
        if "SOURCE_CHUNK_ID" in sql:  # SWEEP 过时池
            rows = [r for r in self._pool
                    if r.get("is_stale") and (r.get("stale_reason") or "").startswith("SWEEP:")]
            n = params.get("n")
            if n is not None:
                rows = rows[:n]
            return _Result(rows=[dict(r) for r in rows])
        if "DISTINCT DOC_CHUNK_ID" in sql:  # 幂等守卫
            self.last_stats = params.get("stats")
            ids = set(params.get("ids") or [])
            return _Result(rows=[{"doc_chunk_id": d} for d in (self._active & ids)])
        if "WHERE PROPOSAL_ID" in sql:  # M18 写回前的提案 SELECT（text，区别于 list ORM select）
            pid = params.get("pid")
            for p in self._proposals:
                if p.proposal_id == pid:
                    return _Result(rows=[{
                        "doc_chunk_id": p.doc_chunk_id,
                        "rewritten_text": p.rewritten_text,
                        "relation_ids": list(p.relation_ids or []),
                    }])
            return _Result()  # .mappings().first() → None
        if "FROM DOC_UPDATE_PROPOSALS" in sql:  # list_proposals（select / count）
            # ORM select 不把绑定参数作 ``params`` 传入（嵌在 stmt 内），用 literal_binds 还原值。
            lit = str(stmt.compile(compile_kwargs={"literal_binds": True})).upper()
            mstat = re.search(r"STATUS\s*=\s*'([A-Z_]+)'", lit)
            status_val = mstat.group(1) if mstat else None
            matching = [p for p in self._proposals if status_val is None or p.status == status_val]
            matching = sorted(matching, key=lambda p: p.created_at, reverse=True)
            if "COUNT" in sql:
                return _Result(scalar=len(matching))
            mlimit = re.search(r"LIMIT (\d+)", lit)
            moff = re.search(r"OFFSET (\d+)", lit)
            limit = int(mlimit.group(1)) if mlimit else len(matching)
            offset = int(moff.group(1)) if moff else 0
            return _Result(rows=matching[offset:offset + limit])
        return _Result()

    async def commit(self):
        pass

    async def rollback(self):
        self.rolled_back = True


# ---- 种子工厂 ----


def _rel(rid, *, rtype="DOC_TO_CODE", source="doc_a", target="code_a",
         is_stale=True, reason="SWEEP:MODIFIED@abcd1234 fix", anchor="A.f"):
    return {"relation_id": rid, "source_chunk_id": source, "target_chunk_id": target,
            "relation_type": rtype, "anchor_key": anchor, "stale_reason": reason,
            "updated_at": _dt(), "is_stale": is_stale}


def _gen_factory(spec):
    """spec: dict[doc_chunk_id -> {"ok": bool, "reason": str}]；缺省 ok=True。"""
    async def gen(session, *, doc_chunk_id, code_chunk_id):
        cfg = spec.get(doc_chunk_id, {"ok": True, "reason": "ok"})
        ok = cfg["ok"]
        return {"rewritten_ok": ok, "rewritten_text": "新段落" if ok else None,
                "original_text": "旧段落",
                "artifact_key": f"doc-updates/5/{doc_chunk_id}.md" if ok else None,
                "file_id": 5, "file_path": "docs/g.md", "heading_path": ["H", doc_chunk_id],
                "reason": cfg["reason"]}
    return gen


def _pr_factory(fail_for=None):
    """fail_for: set[doc_chunk_id] → 该 doc 落 FAILED。proposal_id 自增。"""
    counter = [0]

    async def pr(session, *, conversation_id, file_id, doc_chunk_id, heading_path,
                 relation_ids, original_text, rewritten_text, artifact_key):
        if fail_for and doc_chunk_id in fail_for:
            return {"proposal_id": None, "branch_name": "b", "commit_message": "m",
                    "status": "FAILED", "artifact_key": artifact_key,
                    "rewritten_ok": bool(rewritten_text), "error": "boom"}
        counter[0] += 1
        status = "PENDING_PUSH" if rewritten_text else "PENDING_MANUAL"
        return {"proposal_id": counter[0], "branch_name": "b", "commit_message": "m",
                "status": status, "artifact_key": artifact_key,
                "rewritten_ok": bool(rewritten_text)}
    return pr


def _proposal(pid, *, doc="doc_a", status="PENDING_PUSH", rewritten="新段落", ts=None):
    return SimpleNamespace(
        proposal_id=pid, conversation_id=None, file_id=5, doc_chunk_id=doc,
        heading_path=["H"], relation_ids=[1], original_text="旧", rewritten_text=rewritten,
        artifact_key=f"k/{pid}", branch_name="b", status=status, commit_sha=None, pr_url=None,
        created_at=ts or _FLOOR.replace(year=2000 + pid), updated_at=ts or _FLOOR,
    )


# ---- run_sweep_rewrite ----


async def test_run_sweep_rewrite_happy_two_docs(monkeypatch):
    monkeypatch.setattr(srs, "generate_doc_update", _gen_factory({}))
    monkeypatch.setattr(srs, "create_doc_pr", _pr_factory())
    s = _Session(pool=[_rel(1, source="doc1", target="code1"),
                       _rel(2, source="doc2", target="code2")])
    out = await srs.run_sweep_rewrite(s, top_n=10)
    assert out["scanned"] == 2 and out["slots"] == 2
    assert out["rewritten"] == 2 and out["pending_push"] == 2
    assert out["failed"] == 0 and len(out["proposals"]) == 2
    assert {p["doc_chunk_id"] for p in out["proposals"]} == {"doc1", "doc2"}


async def test_run_sweep_rewrite_dedup_same_doc_aggregates_relation_ids(monkeypatch):
    monkeypatch.setattr(srs, "generate_doc_update", _gen_factory({}))
    monkeypatch.setattr(srs, "create_doc_pr", _pr_factory())
    s = _Session(pool=[_rel(1, source="doc_same", target="code_a"),
                       _rel(2, source="doc_same", target="code_b")])
    out = await srs.run_sweep_rewrite(s)
    assert out["slots"] == 1 and out["rewritten"] == 1
    assert out["proposals"][0]["relation_ids"] == [1, 2]


async def test_run_sweep_rewrite_top_n_limit(monkeypatch):
    monkeypatch.setattr(srs, "generate_doc_update", _gen_factory({}))
    monkeypatch.setattr(srs, "create_doc_pr", _pr_factory())
    s = _Session(pool=[_rel(1, source="d1", target="c1"),
                       _rel(2, source="d2", target="c2"),
                       _rel(3, source="d3", target="c3")])
    out = await srs.run_sweep_rewrite(s, top_n=2)
    assert out["scanned"] == 2  # LIMIT 2


async def test_run_sweep_rewrite_no_llm_yields_pending_manual(monkeypatch):
    monkeypatch.setattr(srs, "generate_doc_update",
                        _gen_factory({"doc1": {"ok": False, "reason": "no_llm"}}))
    monkeypatch.setattr(srs, "create_doc_pr", _pr_factory())
    s = _Session(pool=[_rel(1, source="doc1", target="code1")])
    out = await srs.run_sweep_rewrite(s)
    assert out["rewritten"] == 0 and out["pending_manual"] == 1 and out["pending_push"] == 0


async def test_run_sweep_rewrite_chunk_not_found_yields_pending_manual(monkeypatch):
    monkeypatch.setattr(srs, "generate_doc_update",
                        _gen_factory({"doc1": {"ok": False, "reason": "chunk_not_found"}}))
    monkeypatch.setattr(srs, "create_doc_pr", _pr_factory())
    s = _Session(pool=[_rel(1, source="doc1", target="code1")])
    out = await srs.run_sweep_rewrite(s)
    assert out["pending_manual"] == 1 and out["rewritten"] == 0


async def test_run_sweep_rewrite_one_failed_continues_batch(monkeypatch):
    monkeypatch.setattr(srs, "generate_doc_update", _gen_factory({}))
    monkeypatch.setattr(srs, "create_doc_pr", _pr_factory(fail_for={"doc1"}))
    s = _Session(pool=[_rel(1, source="doc1", target="c1"),
                       _rel(2, source="doc2", target="c2")])
    out = await srs.run_sweep_rewrite(s)
    assert out["failed"] == 1 and out["pending_push"] == 1  # 另一 doc 仍成功（never-throw 组合）


async def test_run_sweep_rewrite_pool_error_returns_error_dict(monkeypatch):
    monkeypatch.setattr(srs, "generate_doc_update", _gen_factory({}))
    monkeypatch.setattr(srs, "create_doc_pr", _pr_factory())
    s = _Session(pool=[_rel(1)], pool_boom=True)
    out = await srs.run_sweep_rewrite(s)
    assert "error" in out and s.rolled_back is True
    assert out["scanned"] == 0


async def test_run_sweep_rewrite_excludes_non_sweep_and_nonstale(monkeypatch):
    monkeypatch.setattr(srs, "generate_doc_update", _gen_factory({}))
    monkeypatch.setattr(srs, "create_doc_pr", _pr_factory())
    s = _Session(pool=[
        _rel(1, source="doc_keep", target="c"),                       # SWEEP + stale → 入池
        _rel(2, source="doc_nostale", target="c", is_stale=False),    # 非过时 → 排除
        _rel(3, source="doc_deleted", target="c", reason="DELETED:beef"),  # 非 SWEEP → 排除
    ])
    out = await srs.run_sweep_rewrite(s)
    assert out["scanned"] == 1 and out["proposals"][0]["doc_chunk_id"] == "doc_keep"


async def test_run_sweep_rewrite_idempotency_guard_skips_existing(monkeypatch):
    calls: list = []
    gen = _gen_factory({})
    async def tracking_gen(session, *, doc_chunk_id, code_chunk_id):
        calls.append(doc_chunk_id)
        return await gen(session, doc_chunk_id=doc_chunk_id, code_chunk_id=code_chunk_id)
    monkeypatch.setattr(srs, "generate_doc_update", tracking_gen)
    monkeypatch.setattr(srs, "create_doc_pr", _pr_factory())
    s = _Session(pool=[_rel(1, source="doc_existing", target="c"),
                       _rel(2, source="doc_new", target="c")],
                 active={"doc_existing"})  # 已有 active 提案 → 守卫跳过
    out = await srs.run_sweep_rewrite(s)
    assert out["skipped_existing"] == 1 and out["rewritten"] == 1
    assert calls == ["doc_new"]  # doc_existing 未被重写


# ---- list_proposals ----


async def test_list_proposals_pagination(monkeypatch):
    proposals = [_proposal(i) for i in range(1, 6)]  # 5 条
    s = _Session(proposals=proposals)
    out = await srs.list_proposals(s, offset=0, limit=2)
    assert out["total"] == 5 and len(out["items"]) == 2  # page1 limit=2


async def test_list_proposals_status_filter():
    proposals = [_proposal(1, status="PENDING_PUSH"),
                 _proposal(2, status="REJECTED"),
                 _proposal(3, status="PENDING_PUSH")]
    s = _Session(proposals=proposals)
    out = await srs.list_proposals(s, status="PENDING_PUSH", offset=0, limit=20)
    assert out["total"] == 2 and all(it["status"] == "PENDING_PUSH" for it in out["items"])


async def test_list_proposals_empty():
    out = await srs.list_proposals(_Session(), offset=0, limit=20)
    assert out["total"] == 0 and out["items"] == []


async def test_list_proposals_exposes_rewrite_texts():
    # M19：审批 UI 预览——_proposal_to_dict 须发出 rewritten_text/original_text（既有列，零迁移）。
    s = _Session(proposals=[_proposal(1, rewritten="重写后段落")])
    out = await srs.list_proposals(s, offset=0, limit=20)
    assert out["items"][0]["rewritten_text"] == "重写后段落"
    assert out["items"][0]["original_text"] == "旧"


# ---- set_proposal_status ----


async def test_set_proposal_status_approved_flips_and_applies():
    s = _Session(proposals=[_proposal(7, status="PENDING_PUSH")])
    out = await srs.set_proposal_status(s, proposal_id=7, status="APPROVED")
    assert out["status"] == "APPROVED" and out["applied"] is True
    assert s._proposals[0].status == "APPROVED"


async def test_set_proposal_status_rejected_flips():
    s = _Session(proposals=[_proposal(7, status="PENDING_PUSH")])
    out = await srs.set_proposal_status(s, proposal_id=7, status="REJECTED")
    assert out["status"] == "REJECTED" and out["applied"] is False
    assert s.written is None  # reject 不写回 doc_chunks
    assert s._proposals[0].status == "REJECTED"


async def test_set_proposal_status_invalid_status_returns_error():
    s = _Session(proposals=[_proposal(7)])
    out = await srs.set_proposal_status(s, proposal_id=7, status="PENDING_PUSH")
    assert out.get("error") == "invalid status"
    assert s._proposals[0].status == "PENDING_PUSH"  # 未改


async def test_set_proposal_status_not_found():
    s = _Session(proposals=[_proposal(7)])
    out = await srs.set_proposal_status(s, proposal_id=999, status="APPROVED")
    assert out.get("error") == "not found"


async def test_set_proposal_status_never_throws_on_db_error():
    s = _Session(proposals=[_proposal(7)], update_boom=True)
    out = await srs.set_proposal_status(s, proposal_id=7, status="APPROVED")
    assert "error" in out and s.rolled_back is True


# ---- set_proposal_status: M18 写回（approve→doc_chunks + 清关系）----


async def test_approve_writes_back_content_and_clears_relations():
    s = _Session(proposals=[_proposal(7, status="PENDING_PUSH", rewritten="重写后内容")])
    out = await srs.set_proposal_status(s, proposal_id=7, status="APPROVED")
    assert out["status"] == "APPROVED" and out["applied"] is True
    assert out["doc_chunk_id"] == "doc_a" and out["relations_cleared"] == 1
    assert s.written is not None and s.written["content"] == "重写后内容"
    assert s.written["cid"] == "doc_a"
    assert s.cleared_ids == [1]  # 提案 relation_ids=[1]
    assert s._proposals[0].status == "APPROVED"


async def test_approve_recomputes_content_hash_and_token_count():
    s = _Session(proposals=[_proposal(7, rewritten="新段落 ABC")])
    await srs.set_proposal_status(s, proposal_id=7, status="APPROVED")
    assert s.written["hash"] == content_hash("新段落 ABC")
    assert s.written["tokens"] == approx_token_count("新段落 ABC")


async def test_approve_pending_manual_returns_error_and_does_not_write():
    s = _Session(proposals=[_proposal(7, status="PENDING_MANUAL", rewritten=None)])
    out = await srs.set_proposal_status(s, proposal_id=7, status="APPROVED")
    assert out.get("error") == "no rewrite to apply"
    assert s.written is None  # 未写 doc_chunks
    assert s._proposals[0].status == "PENDING_MANUAL"  # status 不变


async def test_approve_missing_doc_chunk_returns_error_and_rolls_back():
    s = _Session(proposals=[_proposal(7, status="PENDING_PUSH")], doc_chunk_exists=False)
    out = await srs.set_proposal_status(s, proposal_id=7, status="APPROVED")
    assert out.get("error") == "doc chunk not found"
    assert s.rolled_back is True
    assert s._proposals[0].status == "PENDING_PUSH"  # status 未翻


async def test_approve_no_relations_still_applies_content():
    s = _Session(proposals=[_proposal(7, status="PENDING_PUSH")])
    s._proposals[0].relation_ids = []  # 无锚点关系
    out = await srs.set_proposal_status(s, proposal_id=7, status="APPROVED")
    assert out["applied"] is True and out["relations_cleared"] == 0
    assert s.written is not None  # 仍写回 content
    assert s.cleared_ids is None  # 无 relation_ids 不发清关系 UPDATE


async def test_approve_clears_multiple_relations():
    s = _Session(proposals=[_proposal(7, status="PENDING_PUSH")])
    s._proposals[0].relation_ids = [5, 6, 7]
    out = await srs.set_proposal_status(s, proposal_id=7, status="APPROVED")
    assert out["relations_cleared"] == 3 and s.cleared_ids == [5, 6, 7]


async def test_approve_doc_write_boom_never_throws():
    s = _Session(proposals=[_proposal(7, status="PENDING_PUSH")], doc_write_boom=True)
    out = await srs.set_proposal_status(s, proposal_id=7, status="APPROVED")
    assert "error" in out and s.rolled_back is True


async def test_rejected_missing_proposal_returns_not_found():
    s = _Session(proposals=[_proposal(7)])
    out = await srs.set_proposal_status(s, proposal_id=999, status="REJECTED")
    assert out.get("error") == "not found"  # REJECTED 经 status UPDATE rowcount 0


# ---- _ACTIVE_STATUSES 精简（M18：APPROVED/REJECTED 不再占位，闭环可循环）----


async def test_active_statuses_excludes_decided(monkeypatch):
    monkeypatch.setattr(srs, "generate_doc_update", _gen_factory({}))
    monkeypatch.setattr(srs, "create_doc_pr", _pr_factory())
    assert srs._ACTIVE_STATUSES == ("PENDING_PUSH", "PENDING_MANUAL")
    s = _Session(pool=[_rel(1, source="doc1", target="c1")])
    await srs.run_sweep_rewrite(s)
    assert "APPROVED" not in (s.last_stats or [])


# ---- set_proposal_status: M20 post-commit eager 重嵌入 ----


def _track_index(calls, retval=True):
    """记录 index_chunks_to_milvus 调用并返回 retval 的桩。"""
    def _stub(*a, **k):
        calls.append(1)
        return retval
    return _stub


async def test_approve_eager_reembed_synced_flips_flag(monkeypatch):
    monkeypatch.setattr(srs.embedding_client, "enabled", lambda: True)
    monkeypatch.setattr(srs, "index_chunks_to_milvus", lambda *a, **k: True)
    s = _Session(proposals=[_proposal(7, status="PENDING_PUSH", rewritten="重写后内容")])
    out = await srs.set_proposal_status(s, proposal_id=7, status="APPROVED")
    assert out["reembed_status"] == "synced"
    assert s.synced_cid == "doc_a"           # mark-synced UPDATE 命中该 chunk
    assert out["applied"] is True and "error" not in out


async def test_approve_eager_reembed_embedder_disabled_returns_lazy(monkeypatch):
    # autouse 已置 enabled→False；覆 index 为追踪桩确认未被调
    calls: list = []
    monkeypatch.setattr(srs, "index_chunks_to_milvus", _track_index(calls))
    s = _Session(proposals=[_proposal(7, status="PENDING_PUSH")])
    out = await srs.set_proposal_status(s, proposal_id=7, status="APPROVED")
    assert out["reembed_status"] == "lazy"
    assert calls == []                        # 闸门未过 → 不嵌
    assert s.synced_cid is None              # 不翻 flag


async def test_approve_eager_reembed_upsert_failure_returns_failed(monkeypatch):
    monkeypatch.setattr(srs.embedding_client, "enabled", lambda: True)
    monkeypatch.setattr(srs, "index_chunks_to_milvus", lambda *a, **k: False)  # upsert 失败
    s = _Session(proposals=[_proposal(7, status="PENDING_PUSH")])
    out = await srs.set_proposal_status(s, proposal_id=7, status="APPROVED")
    assert out["reembed_status"] == "failed"
    assert s.synced_cid is None              # 不翻 flag（留懒，resync 兜底）
    assert out["applied"] is True            # 写回仍成功


async def test_approve_eager_reembed_exception_never_taints_approval(monkeypatch):
    monkeypatch.setattr(srs.embedding_client, "enabled", lambda: True)

    def _boom(*a, **k):
        raise RuntimeError("embed boom")
    monkeypatch.setattr(srs, "index_chunks_to_milvus", _boom)
    s = _Session(proposals=[_proposal(7, status="PENDING_PUSH")])
    out = await srs.set_proposal_status(s, proposal_id=7, status="APPROVED")
    assert out["reembed_status"] == "failed"
    assert out["applied"] is True and out["status"] == "APPROVED"
    assert "error" not in out                # eager 失败绝不染 error（否则端点误判 400）


async def test_approve_eager_reembed_killswitch_returns_lazy(monkeypatch):
    monkeypatch.setattr(srs.embedding_client, "enabled", lambda: True)
    monkeypatch.setattr(srs.settings, "eager_reembed_enabled", False)  # opt-out kill-switch
    calls: list = []
    monkeypatch.setattr(srs, "index_chunks_to_milvus", _track_index(calls))
    s = _Session(proposals=[_proposal(7, status="PENDING_PUSH")])
    out = await srs.set_proposal_status(s, proposal_id=7, status="APPROVED")
    assert out["reembed_status"] == "lazy"
    assert calls == []                        # kill-switch → 不嵌


async def test_rejected_no_reembed(monkeypatch):
    calls: list = []
    monkeypatch.setattr(srs, "index_chunks_to_milvus", _track_index(calls))
    s = _Session(proposals=[_proposal(7, status="PENDING_PUSH")])
    out = await srs.set_proposal_status(s, proposal_id=7, status="REJECTED")
    assert out["reembed_status"] is None     # REJECTED 不写内容 → 不重嵌
    assert calls == []


# ---- set_proposal_status: M21 post-commit 真 git ----


async def test_approve_calls_fulfill_and_records_git_status(monkeypatch):
    """approve 后 post-commit 调 fulfill，git_status 反映为终态 status，commit_sha/pr_url 透传。"""
    seen: dict = {}

    async def _fulfill(session, proposal_id):
        seen["pid"] = proposal_id
        return {"git_status": "COMMITTED", "commit_sha": "abc123", "pr_url": None, "error": None}

    monkeypatch.setattr(srs, "fulfill_doc_update", _fulfill)  # 覆 autouse 的 _no_git
    s = _Session(proposals=[_proposal(7, status="PENDING_PUSH", rewritten="重写后内容")])
    out = await srs.set_proposal_status(s, proposal_id=7, status="APPROVED")
    assert seen["pid"] == 7
    assert out["git_status"] == "COMMITTED"
    assert out["status"] == "COMMITTED"          # 被 git 终态覆盖（非 APPROVED）
    assert out["commit_sha"] == "abc123" and out["pr_url"] is None
    assert out["applied"] is True and "error" not in out


async def test_approve_git_none_keeps_approved(monkeypatch):
    """fulfill 返 git_status=None（kill-switch / 无文件路径）→ status 维持 APPROVED。"""
    async def _fulfill(session, proposal_id):
        return {"git_status": None, "commit_sha": None, "pr_url": None, "error": None}

    monkeypatch.setattr(srs, "fulfill_doc_update", _fulfill)
    s = _Session(proposals=[_proposal(7, status="PENDING_PUSH", rewritten="x")])
    out = await srs.set_proposal_status(s, proposal_id=7, status="APPROVED")
    assert out["status"] == "APPROVED" and out["git_status"] is None


async def test_rejected_no_git(monkeypatch):
    """REJECTED 不 apply → 不调 fulfill（git_status=None）。"""
    called = {"n": 0}

    async def _fulfill(session, proposal_id):
        called["n"] += 1
        return {"git_status": None, "commit_sha": None, "pr_url": None, "error": None}

    monkeypatch.setattr(srs, "fulfill_doc_update", _fulfill)
    s = _Session(proposals=[_proposal(7, status="PENDING_PUSH")])
    out = await srs.set_proposal_status(s, proposal_id=7, status="REJECTED")
    assert out["git_status"] is None and called["n"] == 0

