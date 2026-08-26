"""Regression test: cross-file identical content must produce distinct chunk_ids.

Reproduces the M47 Task-1 bug: sa-token demo/test/starter modules contain identical
Java files across submodules; chunk_id = code_{ClassName}_{sha256(content)[:8]}
collides on PK, causing session.rollback() in ingest_code.py to lose all
previously ingested files.
"""
from __future__ import annotations

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

        # --- cleanup ---
        session.execute(
            CodeChunk.__table__.delete().where(CodeChunk.file_id.in_([fid_a, fid_b]))
        )
        session.execute(
            CodeFile.__table__.delete().where(CodeFile.file_id.in_([fid_a, fid_b]))
        )
        session.commit()
