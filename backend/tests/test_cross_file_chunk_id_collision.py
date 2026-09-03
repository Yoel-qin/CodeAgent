"""Regression test: cross-file identical content must produce distinct chunk_ids.

Reproduces the M47 Task-1 bug: sa-token demo/test/starter modules contain identical
Java files across submodules; chunk_id = code_{ClassName}_{sha256(content)[:8]}
collides on PK, causing session.rollback() in ingest_code.py to lose all
previously ingested files.
"""
from __future__ import annotations

import re

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import CodeChunk, CodeFile
from app.pipeline.ingest_code import ingest_java_source

# Byte-identical Java source for two different file paths (simulates copy-pasted
# utility classes across demo submodules).
_IDENTICAL_SOURCE = """package com.pj.test.model;

/** Role entity */
public class SysRole {
    private Long id;
    private String name;
}
"""

_PATH_A = "demo-a/src/main/java/com/pj/test/model/SysRole.java"
_PATH_B = "demo-b/src/main/java/com/pj/test/model/SysRole.java"


def _pg_available(dsn: str | None = None) -> bool:
    """PG 连接探针（connect_timeout=2 防挂起）。

    本模块是全仓唯一需要真 PG 的集成回归测试；CI 的 ci job 是零 infra 契约
    （无 PG service），探针不可达时整模块跳过，本地/有 PG 环境照常运行。
    """
    try:
        eng = create_engine(dsn or settings.database_url_sync,
                            connect_args={"connect_timeout": 2})
    except Exception:  # noqa: S110 — 坏 DSN 等同不可达
        return False
    try:
        with eng.connect():
            return True
    except Exception:
        return False
    finally:
        eng.dispose()


pytestmark = pytest.mark.skipif(
    not _pg_available(),
    reason="需要可达的 PostgreSQL（M47 集成回归）；CI ci job 零 infra，跳过",
)


def test_pg_probe_false_on_refused_port():
    """探针在端口拒绝连接（= CI 无 PG 场景）时必须返回 False，驱动模块级 skip。"""
    assert _pg_available(
        "postgresql+psycopg://coderag:coderag@127.0.0.1:1/coderag"
    ) is False


@pytest.fixture(scope="module")
def engine():
    return create_engine(settings.database_url_sync)


def test_cross_file_identical_content_distinct_chunk_ids(engine):
    """Two files with byte-identical class content must get distinct chunk_ids and both persist."""
    with Session(engine) as session:
        # --- cleanup ---
        for fp in (_PATH_A, _PATH_B):
            session.execute(
                CodeChunk.__table__.delete().where(CodeChunk.file_id.in_(
                    select(CodeFile.file_id).where(CodeFile.file_path == fp)
                ))
            )
            session.execute(
                CodeFile.__table__.delete().where(CodeFile.file_path == fp)
            )
        session.commit()

        # --- ingest file A ---
        result_a = ingest_java_source(
            session, source=_IDENTICAL_SOURCE, file_path=_PATH_A,
            commit_hash="test", module_name="collision_test",
        )
        assert result_a["chunks"] >= 1
        fid_a = session.execute(
            select(CodeFile.file_id).where(CodeFile.file_path == _PATH_A)
        ).scalar_one()
        session.commit()

        # --- ingest file B (byte-identical class content, different path) ---
        result_b = ingest_java_source(
            session, source=_IDENTICAL_SOURCE, file_path=_PATH_B,
            commit_hash="test", module_name="collision_test",
        )
        assert result_b["chunks"] >= 1
        fid_b = session.execute(
            select(CodeFile.file_id).where(CodeFile.file_path == _PATH_B)
        ).scalar_one()
        session.commit()

        # --- verify: fresh session to avoid identity-map interference ---
        with Session(engine) as verify:
            va = verify.execute(
                select(CodeChunk.chunk_id).where(CodeChunk.file_id == fid_a)
            ).scalars().all()
            vb = verify.execute(
                select(CodeChunk.chunk_id).where(CodeChunk.file_id == fid_b)
            ).scalars().all()

        assert len(va) >= 1, f"file A should have persisted chunks, got {len(va)}"
        assert len(vb) >= 1, f"file B should have persisted chunks, got {len(vb)}"
        assert set(va).isdisjoint(set(vb)), (
            f"cross-file chunk_id collision! A={va} B={vb}"
        )

        # File A was ingested first (no prior collision) so its chunk_id must be
        # the original unsuffixed form: code_{ClassName}_{8-hex-content-hash}.
        # This catches a bug that needlessly suffixes the non-colliding first file.
        _FSUFFIX = re.compile(r"_f[0-9a-f]{4,}$")
        for cid in va:
            assert cid.startswith("code_SysRole_"), (
                f"unexpected prefix for first-file chunk: {cid}"
            )
            assert not _FSUFFIX.search(cid), (
                f"first file's chunk was needlessly suffixed: {cid}"
            )
            # The trailing segment must be the 8-hex content hash (no extra suffix chars)
            suffix = cid.rsplit("_", 1)[-1]
            assert re.fullmatch(r"[0-9a-f]{8}", suffix), (
                f"expected 8-hex content hash at tail, got: {suffix} (from {cid})"
            )

        # --- cleanup ---
        session.execute(
            CodeChunk.__table__.delete().where(CodeChunk.file_id.in_([fid_a, fid_b]))
        )
        session.execute(
            CodeFile.__table__.delete().where(CodeFile.file_id.in_([fid_a, fid_b]))
        )
        session.commit()
