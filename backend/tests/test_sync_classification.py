"""sync_incremental._classify_code 单测：按稳定锚点 code_anchor_key 对齐分类。

验证 code per-chunk 的 ADDED/MODIFIED/DELETED（doc 为文件级，不在此测）。
"""
from __future__ import annotations

from app.pipeline.sync_incremental import _classify_code


def test_added_modified_deleted_by_anchor():
    # (chunk_id, content_hash, code_anchor_key)
    old = [("id_m", "h1", "A.m"), ("id_n", "h2", "A.n"), ("id_p", "h5", "A.p")]
    new = [("id_m", "h1", "A.m"),   # 同锚点同 hash → 未变（不出现在结果）
           ("id_n", "h3", "A.n"),   # 同锚点 hash 变 → MODIFIED
           ("id_o", "h4", "A.o")]   # 新锚点 → ADDED
    changes = {c.chunk_id: c for c in _classify_code("Foo.java", old, new)}

    assert set(changes) == {"id_n", "id_o", "id_p"}
    assert changes["id_n"].change_type == "MODIFIED"
    assert changes["id_n"].old_content_hash == "h2"
    assert changes["id_n"].new_content_hash == "h3"
    assert changes["id_o"].change_type == "ADDED"
    assert changes["id_o"].new_content_hash == "h4"
    assert changes["id_p"].change_type == "DELETED"
    assert changes["id_p"].old_content_hash == "h5"


def test_all_added_when_old_empty():
    new = [("id_a", "h1", "A.a"), ("id_b", "h2", "A.b")]
    changes = _classify_code("New.java", [], new)
    assert {c.change_type for c in changes} == {"ADDED"}
    assert len(changes) == 2


def test_all_deleted_when_new_empty():
    old = [("id_a", "h1", "A.a")]
    changes = _classify_code("Gone.java", old, [])
    assert len(changes) == 1
    assert changes[0].change_type == "DELETED"


def test_anchorless_chunks_classify_by_id():
    # 无锚点的 chunk（文件级/类级）：按 chunk_id 匹配，内容变→id 变→视为新增
    old = [("id_x", "h1", None)]
    new = [("id_x", "h1", None), ("id_y", "h2", None)]  # id_x 不变，id_y 新
    changes = {c.chunk_id: c.change_type for c in _classify_code("F.java", old, new)}
    assert changes == {"id_y": "ADDED"}
