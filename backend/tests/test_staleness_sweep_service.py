"""主动腐化巡检服务（M16 run_staleness_sweep / build_staleness_report）单测。

假 session 按 ``str(stmt)`` 关键字分发（仿 test_doc_maintenance_service 的关键字分发），无需 infra：
SELECT chunk_relations → 种子关系（尊重 is_stale=false 过滤 + LIMIT）；SELECT change_history → 按 :ids
+ change_type 过滤并按 chunk 去重取最新；UPDATE chunk_relations → 翻内存行 is_stale/stale_reason；
FILTER/ORDER BY updated_at → 报告聚合/recent。覆盖：MODIFIED/DELETED 晚于关系 → 标；ADDED/早于/RESTORED/
无 change → 不标；方向约定两端；幂等；batch LIMIT；永不抛；报告聚合。
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import app.services.staleness_sweep_service as svc

_FLOOR = datetime(2000, 1, 1, tzinfo=UTC)


def _dt(minute: int = 0) -> datetime:
    return datetime(2026, 1, 1, 12, 0, tzinfo=UTC) + timedelta(minutes=minute)


class _Mappings:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return _Mappings(self._rows)


class _SweepSession:
    """假 session：按 SQL 文本分发 relations / change_history / UPDATE / 报告聚合 / recent。

    ``select_boom`` / ``update_boom`` 触发对应分支抛错（测永不抛路径）。
    """

    def __init__(self, relations, changes, *, select_boom=False, update_boom=False):
        self._relations = relations
        self._changes = changes
        self.select_boom = select_boom
        self.update_boom = update_boom
        self.rolled_back = False

    async def execute(self, stmt, params=None):
        sql = str(stmt).upper()
        params = params or {}
        if sql.lstrip().startswith("UPDATE"):
            if self.update_boom:
                raise RuntimeError("update boom")
            rid = params["rid"]
            for r in self._relations:
                if r["relation_id"] == rid:
                    r["is_stale"] = True
                    r["stale_reason"] = params["reason"]
                    break
            return _Result([])
        if self.select_boom:
            raise RuntimeError("select boom")
        if "FROM CHANGE_HISTORY" in sql:
            ids = set(params.get("ids") or [])
            cands = [c for c in self._changes
                     if c["chunk_id"] in ids and c["change_type"] in ("MODIFIED", "DELETED")]
            latest: dict[str, dict] = {}
            for c in sorted(cands, key=lambda c: c["git_commit_time"] or _FLOOR):
                latest[c["chunk_id"]] = c  # DISTINCT ON chunk_id，取最新
            return _Result(list(latest.values()))
        if "FROM CHUNK_RELATIONS" in sql:
            if "FILTER" in sql:  # 报告聚合
                dc = [r for r in self._relations
                      if r["relation_type"] in ("DOC_TO_CODE", "CODE_TO_DOC")]
                stale = [r for r in dc if r.get("is_stale")]
                sweep = [r for r in stale if (r.get("stale_reason") or "").startswith("SWEEP:")]
                deleted = [r for r in stale if (r.get("stale_reason") or "").startswith("DELETED:")]
                return _Result([{
                    "total": len(dc), "stale": len(stale),
                    "sweep": len(sweep), "deleted": len(deleted),
                    "other": len(stale) - len(sweep) - len(deleted),
                }])
            if "ORDER BY UPDATED_AT DESC" in sql:  # recent SWEEP 发现
                dc = [r for r in self._relations
                      if (r.get("stale_reason") or "").startswith("SWEEP:")]
                dc.sort(key=lambda r: r.get("updated_at") or _FLOOR, reverse=True)
                return _Result([dict(r) for r in dc])
            # 关系枚举（is_stale=false，ORDER BY relation_id，LIMIT :limit）
            rows = sorted((dict(r) for r in self._relations if not r.get("is_stale")),
                          key=lambda r: r["relation_id"])
            limit = params.get("limit")
            if limit is not None:
                rows = rows[:limit]
            return _Result(rows)
        return _Result([])

    async def commit(self):
        pass

    async def rollback(self):
        self.rolled_back = True


def _rel(rid, *, rtype="DOC_TO_CODE", source="doc_a", target="code_x", updated=_dt(0),
         is_stale=False, stale_reason=None, anchor="A.f"):
    return {"relation_id": rid, "source_chunk_id": source, "target_chunk_id": target,
            "relation_type": rtype, "anchor_key": anchor, "updated_at": updated,
            "is_stale": is_stale, "stale_reason": stale_reason}


def _change(chunk_id, ctype, *, at=_dt(10), commit="abcdef1234567890", msg="fix"):
    return {"chunk_id": chunk_id, "change_type": ctype, "git_commit_time": at,
            "git_commit_hash": commit, "commit_message": msg}


# ---- 判定规则 ----


async def test_modified_after_relation_is_marked():
    rels = [_rel(1)]
    s = _SweepSession(rels, [_change("code_x", "MODIFIED", at=_dt(10))])
    out = await svc.run_staleness_sweep(s, batch_size=200)
    assert out["marked"] == 1 and out["by_change_type"]["MODIFIED"] == 1
    assert out["scanned"] == 1
    assert rels[0]["is_stale"] is True
    assert rels[0]["stale_reason"].startswith("SWEEP:MODIFIED@")
    assert "fix" in rels[0]["stale_reason"]


async def test_deleted_after_relation_is_marked():
    rels = [_rel(1)]
    s = _SweepSession(rels, [_change("code_x", "DELETED", at=_dt(10))])
    out = await svc.run_staleness_sweep(s)
    assert out["marked"] == 1 and out["by_change_type"]["DELETED"] == 1
    assert rels[0]["stale_reason"].startswith("SWEEP:DELETED@")


async def test_added_change_does_not_mark():
    rels = [_rel(1)]
    s = _SweepSession(rels, [_change("code_x", "ADDED", at=_dt(10))])
    out = await svc.run_staleness_sweep(s)
    assert out["marked"] == 0 and rels[0]["is_stale"] is False


async def test_modified_before_or_equal_relation_not_marked():
    rels = [_rel(1, updated=_dt(20))]
    s = _SweepSession(rels, [_change("code_x", "MODIFIED", at=_dt(10))])  # 早于 updated
    assert (await svc.run_staleness_sweep(s))["marked"] == 0
    rels2 = [_rel(2, updated=_dt(10))]
    s2 = _SweepSession(rels2, [_change("code_x", "MODIFIED", at=_dt(10))])  # 等于 updated
    assert (await svc.run_staleness_sweep(s2))["marked"] == 0


async def test_restored_change_does_not_mark():
    rels = [_rel(1)]
    s = _SweepSession(rels, [_change("code_x", "RESTORED", at=_dt(10))])
    assert (await svc.run_staleness_sweep(s))["marked"] == 0


async def test_no_change_history_not_marked():
    rels = [_rel(1, target="code_none")]
    s = _SweepSession(rels, [_change("code_x", "MODIFIED", at=_dt(10))])  # chunk 不匹配
    out = await svc.run_staleness_sweep(s)
    assert out["marked"] == 0 and out["scanned"] == 1


async def test_already_stale_relation_skipped():
    rels = [_rel(1, is_stale=True, stale_reason="SWEEP:MODIFIED@x")]
    s = _SweepSession(rels, [_change("code_x", "MODIFIED", at=_dt(10))])
    out = await svc.run_staleness_sweep(s)
    assert out["scanned"] == 0 and out["marked"] == 0  # is_stale=false 过滤


# ---- 方向约定 ----


async def test_direction_convention_both_relation_types():
    # DOC_TO_CODE: code = target_chunk_id
    d2c = [_rel(1, rtype="DOC_TO_CODE", source="doc1", target="codeA")]
    s1 = _SweepSession(d2c, [_change("codeA", "MODIFIED", at=_dt(10))])
    assert (await svc.run_staleness_sweep(s1))["marked"] == 1
    # CODE_TO_DOC: code = source_chunk_id（target 是 doc，给 doc 的 change 不应触发）
    c2d = [_rel(2, rtype="CODE_TO_DOC", source="codeB", target="doc2")]
    s2 = _SweepSession(c2d, [_change("doc2", "MODIFIED", at=_dt(10))])  # change 打在 doc 侧
    assert (await svc.run_staleness_sweep(s2))["marked"] == 0
    s3 = _SweepSession(c2d, [_change("codeB", "MODIFIED", at=_dt(10))])  # change 打在 code 侧
    assert (await svc.run_staleness_sweep(s3))["marked"] == 1


# ---- 幂等 / 批量 / 永不抛 ----


async def test_idempotent_second_run_marks_zero():
    rels = [_rel(1), _rel(2, target="code_y")]
    changes = [_change("code_x", "MODIFIED", at=_dt(10)), _change("code_y", "MODIFIED", at=_dt(10))]
    s = _SweepSession(rels, changes)
    first = await svc.run_staleness_sweep(s)
    assert first["marked"] == 2
    second = await svc.run_staleness_sweep(s)  # 已标的关系出池
    assert second["marked"] == 0 and second["scanned"] == 0


async def test_batch_size_limit_respected():
    rels = [_rel(i, target=f"c{i}") for i in range(1, 4)]
    changes = [_change(f"c{i}", "MODIFIED", at=_dt(10)) for i in range(1, 4)]
    s = _SweepSession(rels, changes)
    out = await svc.run_staleness_sweep(s, batch_size=2)
    assert out["scanned"] == 2 and out["marked"] == 2  # LIMIT 2


async def test_never_throws_on_select_error_returns_error_dict():
    s = _SweepSession([], [], select_boom=True)
    out = await svc.run_staleness_sweep(s)
    assert out["marked"] == 0 and "error" in out and s.rolled_back is True


async def test_never_throws_on_update_error():
    rels = [_rel(1)]
    s = _SweepSession(rels, [_change("code_x", "MODIFIED", at=_dt(10))], update_boom=True)
    out = await svc.run_staleness_sweep(s)
    assert out["marked"] == 0 and "error" in out and s.rolled_back is True


# ---- 报告聚合 ----


async def test_build_staleness_report_aggregation_and_recent():
    rels = [
        _rel(1, is_stale=True, stale_reason="SWEEP:MODIFIED@a", updated=_dt(5)),
        _rel(2, is_stale=True, stale_reason="DELETED:deadbeef", updated=_dt(3)),
        _rel(3, is_stale=True, stale_reason="HITL 人工确认", updated=_dt(1)),
        _rel(4, is_stale=False, updated=_dt(0)),
    ]
    s = _SweepSession(rels, [])
    rep = await svc.build_staleness_report(s, recent=10)
    assert rep["total"] == 4 and rep["stale"] == 3
    assert rep["by_source"] == {"sweep": 1, "deleted": 1, "other": 1}
    assert len(rep["recent"]) == 1 and rep["recent"][0]["relation_id"] == 1


async def test_build_staleness_report_empty():
    rep = await svc.build_staleness_report(_SweepSession([], []))
    assert rep["total"] == 0 and rep["stale"] == 0
    assert rep["by_source"] == {"sweep": 0, "deleted": 0, "other": 0}
    assert rep["recent"] == []
