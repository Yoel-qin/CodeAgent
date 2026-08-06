"""sync_service 编排单测：FULL 计数组装、INCREMENTAL 无游标回退 FULL、INCREMENTAL 逐文件处理。

monkeypatch ingest / sync_git / 各 pipeline 函数，用 _FakeSession 跑 _run_full / _run_incremental，
验证 SyncTask 字段与 change_details JSONB 形状（run_sync 的提交/生命周期需 PG，不在无基础设施单测内）。
"""
from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime
from pathlib import Path

from app.db.models import RollbackHistory
from app.pipeline import ingest
from app.pipeline.sync_git import FileChange
from app.pipeline.sync_incremental import Change
from app.services import sync_service


class _Result:
    def __init__(self, rows=None):
        self._rows = rows or []

    def all(self):
        return self._rows

    def scalars(self):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None

    def scalar_one(self):
        return self._rows[0]


class _FakeSession:
    def execute(self, stmt):
        return _Result()

    def get(self, *a, **k):
        return None

    def add(self, obj):
        pass

    def begin_nested(self):
        return nullcontext()


class _Task:
    def __init__(self):
        self.files_changed = 0
        self.chunks_added = 0
        self.chunks_modified = 0
        self.chunks_deleted = 0
        self.relations_updated = 0
        self.vector_sync_status = "PENDING"
        self.graph_update_status = "PENDING"
        self.change_details: dict | None = {"type": "FULL"}


# ---- 纯助手 ----

def test_relations_total():
    assert sync_service._relations_total(None) == 0
    # 兼容历史假设（anchors 为 int）
    assert sync_service._relations_total({"anchors": 3, "call_graph": {"call_edges": 5}}) == 8
    assert sync_service._relations_total({"anchors": 2}) == 2
    # 回归：relations.build_all 实际返回 anchors 为「嵌套 dict」（不是 int）——
    # 旧代码 `total = rel.get("anchors", 0)` 取到 dict 再 `+= call_edges` → dict+int TypeError，
    # 导致 FULL 同步在 _run_full 内崩溃（被真实 git 仓库端到端实测发现）。
    assert sync_service._relations_total({
        "anchors": {"relations": 2, "anchor_mappings": 3, "unmatched_anchors": 1},
        "call_graph": {"call_edges": 5},
    }) == 10


def test_parse_time_handles_good_bad_empty():
    assert sync_service._parse_time("") is None
    assert sync_service._parse_time("not-a-date") is None
    assert sync_service._parse_time("2026-07-28T12:00:00+08:00") == datetime.fromisoformat(
        "2026-07-28T12:00:00+08:00")


# ---- _run_full ----

def test_run_full_assembles_counts(monkeypatch):
    # 用 relations.build_all 的真实返回形状（anchors 为嵌套 dict）——旧实现在此形状下崩溃
    monkeypatch.setattr(ingest, "ingest_repo", lambda *a, **k: {
        "code": {"files": 2, "chunks": 5}, "doc": {"files": 1, "chunks": 3},
        "errors": [],
        "relations": {"anchors": {"relations": 2, "anchor_mappings": 2, "unmatched_anchors": 0},
                      "call_graph": {"call_edges": 7}}})
    task = _Task()
    sync_service._run_full(object(), Path("."), "commitX", task, None)

    assert task.files_changed == 3
    assert task.chunks_added == 8
    assert task.relations_updated == 11  # 2 relations + 2 anchor_mappings + 7 call_edges
    assert task.vector_sync_status == "COMPLETED"
    assert task.graph_update_status == "COMPLETED"
    assert task.change_details["type"] == "FULL"


def test_run_full_respects_no_relations(monkeypatch):
    captured = {}

    def fake_ingest(session, repo, *, module, commit_hash, build_relations):
        captured["build_relations"] = build_relations
        return {"code": {"files": 0, "chunks": 0}, "doc": {"files": 0, "chunks": 0},
                "errors": [], "relations": None}

    monkeypatch.setattr(ingest, "ingest_repo", fake_ingest)
    task = _Task()
    sync_service._run_full(object(), Path("."), "c", task, build_relations=False)
    assert captured["build_relations"] is False
    assert task.graph_update_status == "SKIPPED"


# ---- _run_incremental ----

def test_run_incremental_falls_back_to_full_when_no_cursor(monkeypatch):
    monkeypatch.setattr(sync_service, "_last_completed_commit", lambda s: None)
    monkeypatch.setattr(ingest, "ingest_repo", lambda *a, **k: {
        "code": {"files": 1, "chunks": 4}, "doc": {"files": 0, "chunks": 0},
        "errors": [], "relations": None})
    task = _Task()
    sync_service._run_incremental(_FakeSession(), Path("."), "cX", task)

    assert task.chunks_added == 4
    assert task.change_details.get("fallback") == "FULL"
    assert task.change_details.get("reason") == "no_cursor"


def test_run_incremental_processes_changes(monkeypatch):
    monkeypatch.setattr(sync_service, "_last_completed_commit", lambda s: "cursor0000")
    monkeypatch.setattr(sync_service, "resolve_commit", lambda repo, ref: ref)
    monkeypatch.setattr(sync_service, "changed_files", lambda repo, old, new: [
        FileChange("Foo.java", "code", "MODIFIED"),
        FileChange("gone/docs.md", "doc", "DELETED"),
    ])
    monkeypatch.setattr(sync_service, "commit_meta",
                        lambda repo, c: {"hash": c, "time": "", "author": "A", "message": "m"})

    def fake_apply(session, repo, fc, *, new_commit):
        return ({"file_path": fc.file_path, "kind": "code", "added": 1, "modified": 0, "deleted": 0},
                [Change("cnew", "code", "ADDED", fc.file_path, new_content_hash="h1")])

    monkeypatch.setattr(sync_service, "apply_added_or_modified", fake_apply)
    monkeypatch.setattr(sync_service, "soft_delete_file",
                        lambda session, **k: {"chunks": 2, "relations": 1, "anchors": 0,
                                              "call_edges": 0, "chunk_ids": ["d1", "d2"]})
    monkeypatch.setattr(sync_service, "classify_rollbacks", lambda session, changes, **k: changes)
    monkeypatch.setattr(sync_service, "apply_rollback_restore", lambda *a, **k: [])

    task = _Task()
    sync_service._run_incremental(_FakeSession(), Path("."), "cX", task)

    assert task.files_changed == 2
    assert task.chunks_added == 1
    assert task.chunks_deleted == 2
    assert task.change_details["type"] == "INCREMENTAL"
    assert task.change_details["cursor"] == "cursor0000"
    assert len(task.change_details["changes"]) == 1
    assert task.change_details["changes"][0]["change_type"] == "ADDED"


# ---- run_sync 错误处理（回归：error handler 自身不崩） ----

class _RunSyncSession:
    """支持 run_sync 生命周期（add/flush/commit/rollback/get）的最小假 session。
    run_sync 内部 new 一个 SyncTask 并 add；这里把它捕获，供 get() 取回。"""

    def __init__(self):
        self.task = None

    def add(self, obj):
        self.task = obj

    def flush(self):
        pass

    def commit(self):
        pass

    def rollback(self):
        pass

    def get(self, model, pk):
        return self.task


def test_run_sync_marks_failed_without_crashing_handler(monkeypatch):
    """回归：run_sync 的 except 块曾用 ``type(e).__name__``，而 ``type`` 是 run_sync 的
    形参（``*, type: str``）——被字符串遮蔽后 ``type(e)`` = ``"FULL"(e)`` 抛 'str' object
    is not callable，导致 FAILED 状态/ error_message 永不落库、任务卡死 RUNNING。
    修复用 ``e.__class__.__name__``。此测试用真实异常路径覆盖该分支。"""
    monkeypatch.setattr(sync_service, "git_head", lambda repo: "commitABC")

    def boom(*a, **k):
        raise RuntimeError("ingest exploded")

    monkeypatch.setattr(sync_service, "_run_full", boom)

    session = _RunSyncSession()
    task = sync_service.run_sync(session, Path("."), type="FULL")

    assert task is not None
    assert task.status == "FAILED"
    assert task.error_message and "RuntimeError" in task.error_message
    assert "ingest exploded" in task.error_message


# ---- §18 回滚恢复：检测到回滚时重建关联 ----

def test_run_incremental_rebuilds_relations_on_rollback(monkeypatch):
    """回归 §18：回滚时整文件重入库经 clear_code_chunk_refs 硬删了被打标记的 关系/锚点/调用图行，
    致 apply_rollback_restore 无行可翻、回滚后知识图谱空至下次 FULL。修复：rollback_rows 非空时
    调 relations.build_all 重建（仅链活跃 chunk）。此测试验证 build_all 被调用且 task 计入。"""
    monkeypatch.setattr(sync_service, "_last_completed_commit", lambda s: "cursor0000")
    monkeypatch.setattr(sync_service, "resolve_commit", lambda repo, ref: ref)
    monkeypatch.setattr(sync_service, "changed_files", lambda repo, old, new: [
        FileChange("Foo.java", "code", "MODIFIED")])
    monkeypatch.setattr(sync_service, "commit_meta",
                        lambda repo, c: {"hash": c, "time": "", "author": "A", "message": "Revert ..."})
    monkeypatch.setattr(sync_service, "apply_added_or_modified",
                        lambda session, repo, fc, *, new_commit:
                        ({"file_path": fc.file_path, "kind": "code", "added": 1, "modified": 0, "deleted": 0},
                         [Change("c1", "code", "ADDED", fc.file_path)]))
    monkeypatch.setattr(sync_service, "classify_rollbacks", lambda session, changes, **k: changes)
    # 模拟检测到回滚（rollback_rows 非空）
    monkeypatch.setattr(sync_service, "apply_rollback_restore",
                        lambda *a, **k: [RollbackHistory(rollback_commit="cX", source_commit="cPrev",
                                                          chunks_restored=1, triggered_by="MANUAL",
                                                          status="COMPLETED")])
    monkeypatch.setattr(sync_service, "_write_change_history", lambda *a, **k: None)

    called = {}

    def fake_build_all(session, *, repo_path=None):
        called["yes"] = True
        return {"anchors": {"relations": 3, "anchor_mappings": 2, "unmatched_anchors": 0},
                "call_graph": {"call_edges": 1}}

    monkeypatch.setattr(sync_service.relations, "build_all", fake_build_all)

    task = _Task()
    sync_service._run_incremental(_FakeSession(), Path("."), "cX", task)

    assert called.get("yes") is True                       # 回滚 → 重建关联
    assert task.graph_update_status == "COMPLETED"
    assert task.relations_updated == 6                      # 3 relations + 2 anchor_mappings + 1 call_edge


def test_run_incremental_skips_rebuild_without_rollback(monkeypatch):
    """无回滚时不重建关联（graph_update_status=SKIPPED），保留既有增量语义。"""
    monkeypatch.setattr(sync_service, "_last_completed_commit", lambda s: "cursor0000")
    monkeypatch.setattr(sync_service, "resolve_commit", lambda repo, ref: ref)
    monkeypatch.setattr(sync_service, "changed_files", lambda repo, old, new: [
        FileChange("Foo.java", "code", "MODIFIED")])
    monkeypatch.setattr(sync_service, "commit_meta",
                        lambda repo, c: {"hash": c, "time": "", "author": "A", "message": "normal"})
    monkeypatch.setattr(sync_service, "apply_added_or_modified",
                        lambda session, repo, fc, *, new_commit:
                        ({"file_path": fc.file_path, "kind": "code", "added": 1, "modified": 0, "deleted": 0},
                         [Change("c1", "code", "ADDED", fc.file_path)]))
    monkeypatch.setattr(sync_service, "classify_rollbacks", lambda session, changes, **k: changes)
    monkeypatch.setattr(sync_service, "apply_rollback_restore", lambda *a, **k: [])  # 无回滚
    monkeypatch.setattr(sync_service, "_write_change_history", lambda *a, **k: None)

    called = {}
    monkeypatch.setattr(sync_service.relations, "build_all",
                        lambda *a, **k: called.setdefault("yes", True) or {"anchors": {}, "call_graph": {}})

    task = _Task()
    sync_service._run_incremental(_FakeSession(), Path("."), "cX", task)

    assert "yes" not in called                              # 无回滚 → 不重建
    assert task.graph_update_status == "SKIPPED"
