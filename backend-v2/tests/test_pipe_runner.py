"""M5 Task 13：Workers B/C/D + runner（保序消费/重试/死信/幂等跳过）。

brief 3 个逐字（增量更新+删除 / 重放零副作用 / 重试后死信）+ 补充：
非 M/A/D status 跳过、非法 repo 跳过、文档 file 事件 ingest→delete 全链路。

统一走 InMemoryQueue + mini_repo 拷贝到 tmp + 真 PG（repo 前缀 ``pipe_tmp_``，
module fixture 前后清理——不上 git，直接用 files 载荷）。
"""

import shutil
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy import text as _t
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models.doc import Document
from app.pipeline.runner import run_worker_once
from tests.fakes import InMemoryQueue

FIX = Path(__file__).parent / "fixtures" / "mini_repo"
REPO = "pipe_tmp_run"


def _entity_fingerprint(s) -> set:
    rows = s.execute(_t(f"SELECT class_name, method_name, start_line FROM code_entities "
                        f"WHERE repo = '{REPO}'")).all()
    return set(rows)


def _cleanup(engine) -> None:
    """清 pipe_tmp_% 的账本 + 实体/边/度量/文档（call_edges/code_metrics 虽有
    ondelete=CASCADE，仍显式删一遍双保险；先于 fixture 建仓 + 尾部各跑一次）。"""
    with engine.begin() as conn:
        conn.execute(_t(
            "DELETE FROM call_edges WHERE caller_id IN "
            "(SELECT id FROM code_entities WHERE repo LIKE 'pipe_tmp_%') "
            "OR callee_id IN (SELECT id FROM code_entities WHERE repo LIKE 'pipe_tmp_%')"))
        conn.execute(_t(
            "DELETE FROM code_metrics WHERE entity_id IN "
            "(SELECT id FROM code_entities WHERE repo LIKE 'pipe_tmp_%')"))
        conn.execute(_t("DELETE FROM code_entities WHERE repo LIKE 'pipe_tmp_%'"))
        conn.execute(_t("DELETE FROM doc_sections WHERE repo LIKE 'pipe_tmp_%'"))
        conn.execute(_t("DELETE FROM media_chunks WHERE repo LIKE 'pipe_tmp_%'"))
        conn.execute(_t("DELETE FROM documents WHERE repo LIKE 'pipe_tmp_%'"))
        conn.execute(_t("DELETE FROM pipeline_events WHERE repo LIKE 'pipe_tmp_%'"))


@pytest.fixture(scope="module")
def repo_dir_env(tmp_path_factory, pg_engine):
    root = tmp_path_factory.mktemp("repos")
    shutil.copytree(FIX, root / REPO)
    _cleanup(pg_engine)  # 上次中断运行可能残留
    yield root
    _cleanup(pg_engine)


def _push(q, files):
    q.enqueue("push", {"repo": REPO, "commit_hash": "c1", "files": files})


def test_incremental_update_and_delete(pg_engine, repo_dir_env, monkeypatch):
    monkeypatch.setattr(settings, "repos_root", str(repo_dir_env))
    q = InMemoryQueue()
    # 初始全量（复用 ingest CLI 抽出的函数）
    from app.pipeline.ingest_code import run_full_code_ingest
    with Session(pg_engine) as s:
        run_full_code_ingest(s, repo=REPO, repo_dir=repo_dir_env / REPO)
        s.commit()
    # 模拟变更：改 CommitLog.java + 删 MessageConsumer.java + 加新文件
    target = repo_dir_env / REPO / "com/example/broker/CommitLog.java"
    src = target.read_text(encoding="utf-8")
    target.write_text(src.replace("putMessage", "putMessageV2"), encoding="utf-8")
    (repo_dir_env / REPO / "com/example/client/MessageConsumer.java").unlink()
    new = repo_dir_env / REPO / "com/example/broker/BrandNew.java"
    new.write_text("class BrandNew { void go() {} }", encoding="utf-8")
    _push(q, [{"path": "com/example/broker/CommitLog.java", "status": "M"},
              {"path": "com/example/client/MessageConsumer.java", "status": "D"},
              {"path": "com/example/broker/BrandNew.java", "status": "A"}])
    stats = run_worker_once(q, max_events=20)
    assert stats["processed"] >= 3
    with Session(pg_engine) as s:
        rows = s.execute(_t(
            f"SELECT class_name, method_name FROM code_entities WHERE repo = '{REPO}'")).all()
    classes = {r[0] for r in rows}
    assert "BrandNew" in classes and "MessageConsumer" not in classes
    assert any(r[1] == "putMessageV2" for r in rows), "修改文件实体已更新"


def test_replay_no_side_effects(pg_engine, repo_dir_env, monkeypatch):
    monkeypatch.setattr(settings, "repos_root", str(repo_dir_env))
    q = InMemoryQueue()
    _push(q, [{"path": "com/example/broker/CommitLog.java", "status": "M"}])
    run_worker_once(q, max_events=20)
    with Session(pg_engine) as s:
        before = _entity_fingerprint(s)
    q2 = InMemoryQueue()
    _push(q2, [{"path": "com/example/broker/CommitLog.java", "status": "M"}])
    stats = run_worker_once(q2, max_events=20)
    with Session(pg_engine) as s:
        after = _entity_fingerprint(s)
    assert stats["processed"] == 0 and stats["skipped"] >= 1
    assert before == after, "重复消费零副作用（实体指纹逐项一致）"


def test_retry_then_dead_letter(pg_engine, repo_dir_env, monkeypatch):
    monkeypatch.setattr(settings, "repos_root", str(repo_dir_env))
    q = InMemoryQueue()
    q.enqueue("file", {"repo": REPO, "commit_hash": "cx",
                       "path": "no/such/File.java", "status": "M"})
    for _ in range(3):
        run_worker_once(q, max_events=10)  # brief 逐字（去掉未用绑定过 ruff F841）
    assert q.dead and "no/such/File.java" in str(q.dead[0].payload)


def test_runner_skips_unsupported_status(pg_engine, repo_dir_env, monkeypatch):
    """Task 12 评审 ⚠️-2：非 M/A/D status → skip（不 raise、不进死信），账本落 DONE。"""
    monkeypatch.setattr(settings, "repos_root", str(repo_dir_env))
    q = InMemoryQueue()
    q.enqueue("file", {"repo": REPO, "commit_hash": "s1",
                       "path": "com/example/broker/CommitLog.java", "status": "R"})
    stats = run_worker_once(q, max_events=5)
    assert stats["processed"] == 0 and stats["skipped"] == 1 and not q.dead
    with Session(pg_engine) as s:
        row = s.execute(_t("SELECT status FROM pipeline_events "
                           "WHERE repo = :r AND path = :p"),
                        {"r": REPO, "p": "com/example/broker/CommitLog.java"}).scalar()
    assert row == "DONE", "跳过也要闭环账本，不留悬 PENDING"


def test_runner_skips_bad_repo(pg_engine, repo_dir_env, monkeypatch):
    """Task 12 评审 ⚠️-3：repo 非单段 / 越出 repos_root → skip + ack，不重试。"""
    monkeypatch.setattr(settings, "repos_root", str(repo_dir_env))
    q = InMemoryQueue()
    q.enqueue("file", {"repo": "../escape", "commit_hash": "x1",
                       "path": "A.java", "status": "M"})
    q.enqueue("graph_rebuild", {"repo": "a/b", "commit_hash": "x2"})
    stats = run_worker_once(q, max_events=5)
    assert stats["processed"] == 0 and stats["skipped"] == 2 and not q.dead


def test_doc_file_ingest_then_delete(pg_engine, repo_dir_env, monkeypatch):
    """Worker D：.md 的 M → ingest_doc_file（PG 落 sections），D → delete_doc 清三表。"""
    monkeypatch.setattr(settings, "repos_root", str(repo_dir_env))
    monkeypatch.setattr("app.pipeline.ingest_doc.upload_original", lambda *a, **k: None)
    monkeypatch.setattr("app.pipeline.ingest_doc.embed_texts",
                        lambda texts, **k: [[0.1] * 1024 for _ in texts])
    monkeypatch.setattr("app.pipeline.ingest_doc.upsert_sections", lambda rows: len(rows))
    monkeypatch.setattr("app.pipeline.ingest_doc.bulk_index_sections", lambda docs: len(docs))
    monkeypatch.setattr("app.pipeline.ingest_doc.get_client", lambda: MagicMock())
    monkeypatch.setattr("app.pipeline.ingest_doc.get_es", lambda: MagicMock())

    docs_dir = repo_dir_env / REPO / "docs"
    docs_dir.mkdir(exist_ok=True)
    (docs_dir / "guide.md").write_text("# 指南\n\n内容甲。\n", encoding="utf-8")

    q = InMemoryQueue()
    q.enqueue("file", {"repo": REPO, "commit_hash": "d1",
                       "path": "docs/guide.md", "status": "M"})
    assert run_worker_once(q, max_events=5)["processed"] == 1
    with Session(pg_engine) as s:
        assert s.query(Document).filter_by(repo=REPO, doc_name="docs/guide.md").first() is not None

    q2 = InMemoryQueue()
    q2.enqueue("file", {"repo": REPO, "commit_hash": "d2",
                        "path": "docs/guide.md", "status": "D"})
    assert run_worker_once(q2, max_events=5)["processed"] == 1
    with Session(pg_engine) as s:
        assert s.query(Document).filter_by(repo=REPO, doc_name="docs/guide.md").first() is None
