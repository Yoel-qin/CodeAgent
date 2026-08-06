"""sync_rollback 单测：回滚分类（ROLLBACK/RESTORED）与恢复（apply_rollback_restore）。

分类的 change_history 查询抽成 _match_rollback_source/_match_restore_source，测试直接
monkeypatch 它们（无需 PG）。恢复的 UPDATE 语句用 _FakeSession 记录、rowcount 取 0，
验证 RollbackHistory 行计数与 doc_pr_closer 调用。
"""
from __future__ import annotations

from app.pipeline import sync_rollback
from app.pipeline.sync_incremental import Change

# ---- classify_rollbacks ----

def test_modified_matching_history_old_hash_becomes_rollback(monkeypatch):
    monkeypatch.setattr(sync_rollback, "_match_rollback_source",
                        lambda s, cid, h: "srcBBB" if (cid == "c1" and h == "h1") else None)
    monkeypatch.setattr(sync_rollback, "_match_restore_source", lambda s, cid: None)

    changes = [Change("c1", "code", "MODIFIED", "F.java", old_content_hash="h2", new_content_hash="h1")]
    out = sync_rollback.classify_rollbacks(None, changes, commit_message="normal")

    assert out[0].change_type == "ROLLBACK"
    assert out[0].rollback_source_commit == "srcBBB"
    assert out[0].is_rollback_related is True


def test_added_with_prior_deleted_becomes_restored(monkeypatch):
    monkeypatch.setattr(sync_rollback, "_match_rollback_source", lambda *a, **k: None)
    monkeypatch.setattr(sync_rollback, "_match_restore_source",
                        lambda s, cid: "srcDEL" if cid == "c2" else None)

    changes = [Change("c2", "code", "ADDED", "F.java", new_content_hash="h1")]
    out = sync_rollback.classify_rollbacks(None, changes)

    assert out[0].change_type == "RESTORED"
    assert out[0].rollback_source_commit == "srcDEL"


def test_revert_message_hint_alone_does_not_relabel(monkeypatch):
    # 无历史命中 → 即便提交信息含 Revert 也保持原分类
    monkeypatch.setattr(sync_rollback, "_match_rollback_source", lambda *a, **k: None)
    monkeypatch.setattr(sync_rollback, "_match_restore_source", lambda *a, **k: None)

    changes = [Change("c3", "code", "ADDED", "F.java")]
    out = sync_rollback.classify_rollbacks(None, changes, commit_message="Revert: something")

    assert out[0].change_type == "ADDED"
    assert out[0].is_rollback_related is False


def test_deleted_changes_are_not_relabelled(monkeypatch):
    monkeypatch.setattr(sync_rollback, "_match_rollback_source", lambda *a, **k: "x")  # 不应被调用影响 DELETED
    monkeypatch.setattr(sync_rollback, "_match_restore_source", lambda *a, **k: "y")
    changes = [Change("c4", "code", "DELETED", "F.java", old_content_hash="h9")]
    out = sync_rollback.classify_rollbacks(None, changes)
    assert out[0].change_type == "DELETED"


# ---- apply_rollback_restore ----

class _Result:
    def __init__(self, rowcount: int = 0, rows=None):
        self.rowcount = rowcount
        self._rows = rows or []

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return self._rows


class _FakeSession:
    def __init__(self):
        self.executed = []
        self.added = []

    def execute(self, stmt):
        self.executed.append(stmt)
        return _Result()

    def get(self, model, pk):
        return None

    def add(self, obj):
        self.added.append(obj)


def test_apply_restore_noop_when_no_rollback_changes():
    changes = [Change("c1", "code", "MODIFIED", "F.java")]  # 无 rollback_source_commit
    assert sync_rollback.apply_rollback_restore(_FakeSession(), changes, new_commit="nc") == []


def test_apply_restore_groups_by_source_and_inserts_history():
    changes = [
        Change("c1", "code", "ROLLBACK", "F.java", rollback_source_commit="src"),
        Change("c2", "code", "RESTORED", "F.java", rollback_source_commit="src"),
        Change("c3", "code", "ADDED", "F.java"),  # 非回滚，忽略
    ]
    closer_calls = []
    rows = sync_rollback.apply_rollback_restore(
        _FakeSession(), changes, new_commit="newc",
        doc_pr_closer=lambda s: closer_calls.append(s) or None,
    )
    assert len(rows) == 1                      # 同 source_commit 合并为一行
    rb = rows[0]
    assert rb.rollback_commit == "newc"
    assert rb.source_commit == "src"
    assert rb.chunks_rolled_back == 1
    assert rb.chunks_restored == 1
    assert closer_calls == ["src"]
