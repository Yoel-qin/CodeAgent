"""文档维护写动作服务（M15 generate_doc_update / create_doc_pr）单测。

复用关键字分发假 session（取 doc/code chunk）+ monkeypatch ``llm.chat`` / ``minio_client.put_bytes``，
无需 infra。覆盖：重写成功 + 工件写回 MinIO；无 LLM / chunk 缺失 / LLM 失败 / MinIO 失败 降级；
PR 载荷装配 + 落库（PENDING_PUSH / PENDING_MANUAL / FAILED）。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import app.services.doc_maintenance_service as dms
from app.db.models.history import DocUpdateProposal

_FAKE_SHA = "deadbeefcafebabe000000000000000000000000"


@pytest.fixture(autouse=True)
def _stub_git_head(monkeypatch):
    """M21：create_doc_pr 捕获 source_commit 时调 ``git_head``——桩掉避真实 subprocess/网络，
    保持单测离线确定。需真 git 的场景由 test_doc_pr_service 覆盖。"""
    monkeypatch.setattr(dms, "git_head", lambda repo: _FAKE_SHA)

# ---- 假 LLM（替换模块级 ``llm``，``configured`` 与 ``chat`` 可控）----


class _FakeLLM:
    def __init__(self, *, configured: bool = True, reply: str = "重写后的段落内容",
                 raise_on_chat: bool = False):
        self.configured = configured
        self._reply = reply
        self._raise = raise_on_chat

    async def chat(self, messages, **kw):
        if self._raise:
            raise RuntimeError("llm boom")
        return self._reply


# ---- 假 session：按 SQL 关键字分发 doc/code 行（.first()）----


class _FirstResult:
    def __init__(self, row):
        self._row = row

    def first(self):
        return self._row


class _DocCodeSession:
    """``generate_doc_update`` 用：SQL 含 ``doc_files`` → doc 行；含 ``code_chunks`` → code 行。"""

    def __init__(self, doc_row, code_row):
        self._doc = doc_row
        self._code = code_row

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        if "doc_files" in sql:
            return _FirstResult(self._doc)
        if "code_chunks" in sql:
            return _FirstResult(self._code)
        return _FirstResult(None)


class _PrSession:
    """``create_doc_pr`` 用：记录 add 的对象；refresh 注入 proposal_id；commit 可选抛错。"""

    def __init__(self, *, commit_raises: bool = False):
        self.added: list = []
        self.commit_raises = commit_raises
        self.rolled_back = False

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        if self.commit_raises:
            raise RuntimeError("db down")

    async def refresh(self, obj):
        obj.proposal_id = 777

    async def rollback(self):
        self.rolled_back = True


def _doc_row():
    return SimpleNamespace(content="旧文档", heading_path=["指南", "3.2"],
                           file_id=5, file_path="docs/g.md")


def _code_row():
    return SimpleNamespace(content="void run(){}", class_name="Foo", method_name="run")


# ---- 纯 helper ----


def test_helpers_slug_branch_commit_diff():
    assert dms._slug("API / 认证 流程！") == "API认证流程"
    assert dms._slug("   ") == "section"
    assert dms._branch_name(3, ["API", "认证"]) == "coderag/doc-update-3-API认证"
    msg = dms._commit_message(["API", "认证"], "conv_1")
    assert msg.startswith("docs: 同步过时文档段落（API › 认证）") and "conv_1" in msg
    diff = dms._unified_diff("a\nb\n", "a\nB\n")
    assert "-b" in diff and "+B" in diff
    assert dms._unified_diff(None, "x") == ""


# ---- generate_doc_update ----


async def test_generate_doc_update_rewrites_and_writes_artifact(monkeypatch):
    puts: list = []
    monkeypatch.setattr(dms, "llm", _FakeLLM(reply="新文档段落"))
    monkeypatch.setattr(dms.minio_client, "put_bytes",
                        lambda key, data, *, content_type=None: puts.append((key, data, content_type)) or key)
    out = await dms.generate_doc_update(
        _DocCodeSession(_doc_row(), _code_row()), doc_chunk_id="doc_y", code_chunk_id="code_x")
    assert out["rewritten_ok"] is True and out["reason"] == "ok"
    assert out["rewritten_text"] == "新文档段落" and out["original_text"] == "旧文档"
    assert out["artifact_key"] and out["artifact_key"].startswith("doc-updates/5/")
    assert puts and puts[0][2] == "text/markdown"          # content_type
    assert "新文档段落".encode() in puts[0][1]        # 工件含重写后正文


async def test_generate_doc_update_no_llm_skips_minio(monkeypatch):
    puts: list = []
    monkeypatch.setattr(dms, "llm", _FakeLLM(configured=False))
    monkeypatch.setattr(dms.minio_client, "put_bytes", lambda *a, **k: puts.append(1))
    out = await dms.generate_doc_update(
        _DocCodeSession(_doc_row(), _code_row()), doc_chunk_id="doc_y", code_chunk_id="code_x")
    assert out["rewritten_ok"] is False and out["reason"] == "no_llm"
    assert out["rewritten_text"] is None and puts == []     # 未配置 LLM → 不写 MinIO


async def test_generate_doc_update_chunk_not_found(monkeypatch):
    monkeypatch.setattr(dms, "llm", _FakeLLM())
    out = await dms.generate_doc_update(
        _DocCodeSession(None, _code_row()), doc_chunk_id="doc_y", code_chunk_id="code_x")
    assert out["rewritten_ok"] is False and out["reason"] == "chunk_not_found"


async def test_generate_doc_update_llm_failure_degrades(monkeypatch):
    puts: list = []
    monkeypatch.setattr(dms, "llm", _FakeLLM(raise_on_chat=True))
    monkeypatch.setattr(dms.minio_client, "put_bytes", lambda *a, **k: puts.append(1))
    out = await dms.generate_doc_update(
        _DocCodeSession(_doc_row(), _code_row()), doc_chunk_id="doc_y", code_chunk_id="code_x")
    assert out["rewritten_ok"] is False and out["reason"] == "llm_error"
    assert puts == []                                        # LLM 失败 → 不写 MinIO


async def test_generate_doc_update_minio_failure_keeps_rewritten_text(monkeypatch):
    monkeypatch.setattr(dms, "llm", _FakeLLM(reply="新段落"))
    monkeypatch.setattr(dms.minio_client, "put_bytes",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("minio down")))
    out = await dms.generate_doc_update(
        _DocCodeSession(_doc_row(), _code_row()), doc_chunk_id="doc_y", code_chunk_id="code_x")
    assert out["rewritten_ok"] is True                       # 重写仍算成功
    assert out["rewritten_text"] == "新段落"
    assert out["artifact_key"] is None                       # MinIO 失败 → 工件 key 留空（不抛）


# ---- create_doc_pr ----


async def test_create_doc_pr_pending_push():
    session = _PrSession()
    out = await dms.create_doc_pr(
        session, conversation_id="conv_1", file_id=5, doc_chunk_id="doc_y",
        heading_path=["指南", "3.2"], relation_ids=[7, 8], original_text="旧",
        rewritten_text="新", artifact_key="doc-updates/5/1.md")
    assert out["status"] == "PENDING_PUSH" and out["proposal_id"] == 777
    assert out["branch_name"].startswith("coderag/doc-update-5-")
    row = session.added[0]
    assert isinstance(row, DocUpdateProposal)
    assert row.relation_ids == [7, 8] and row.rewritten_text == "新"
    assert row.status == "PENDING_PUSH" and row.artifact_key == "doc-updates/5/1.md"


async def test_create_doc_pr_pending_manual_when_no_rewrite():
    session = _PrSession()
    out = await dms.create_doc_pr(
        session, conversation_id="conv_1", file_id=5, doc_chunk_id="doc_y",
        heading_path=["H"], relation_ids=[7], original_text="旧",
        rewritten_text=None, artifact_key=None)
    assert out["status"] == "PENDING_MANUAL" and out["rewritten_ok"] is False
    assert session.added[0].rewritten_text is None


async def test_create_doc_pr_commit_failure_returns_failed():
    session = _PrSession(commit_raises=True)
    out = await dms.create_doc_pr(
        session, conversation_id="conv_1", file_id=5, doc_chunk_id="doc_y",
        heading_path=["H"], relation_ids=[7], original_text="旧",
        rewritten_text="新", artifact_key="k")
    assert out["status"] == "FAILED" and out["proposal_id"] is None
    assert session.rolled_back is True


# ---- create_doc_pr: M21 source_commit 捕获（base 提交 → 回滚 closer 匹配键）----


async def test_create_doc_pr_captures_source_commit():
    """create_doc_pr 捕获仓库当前 HEAD 作为 source_commit（回滚关 PR 匹配键）。"""
    session = _PrSession()
    await dms.create_doc_pr(
        session, conversation_id=None, file_id=5, doc_chunk_id="doc_y",
        heading_path=["H"], relation_ids=[7], original_text="旧",
        rewritten_text="新", artifact_key="k")
    assert session.added[0].source_commit == _FAKE_SHA


async def test_create_doc_pr_source_commit_none_when_git_unavailable(monkeypatch):
    """非 git 仓库 / git 不可用 → source_commit=None（best-effort，不阻断提案落库）。"""
    def _boom(repo):
        raise RuntimeError("not a git repo")
    monkeypatch.setattr(dms, "git_head", _boom)  # 覆 autouse 桩
    session = _PrSession()
    out = await dms.create_doc_pr(
        session, conversation_id=None, file_id=5, doc_chunk_id="doc_y",
        heading_path=["H"], relation_ids=[7], original_text="旧",
        rewritten_text="新", artifact_key="k")
    assert out["status"] == "PENDING_PUSH"              # 仍正常落库
    assert session.added[0].source_commit is None
