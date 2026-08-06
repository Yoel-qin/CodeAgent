"""milvus_client collection 布局单测（纯函数 collection_for，无需真实 Milvus）。"""
from __future__ import annotations

from app.clients import milvus_client
from app.core.config import settings


def test_unified_layout_single_collection_with_kind_field():
    for kind in ("code", "doc", None):
        name, dim, has_kind = milvus_client.collection_for("unified", kind)
        assert (name, dim, has_kind) == ("coderag_vectors", 1024, True), kind


def test_dual_layout_two_collections_without_kind_field():
    assert milvus_client.collection_for("dual", "code") == ("code_vectors", 768, False)
    assert milvus_client.collection_for("dual", "doc") == ("doc_vectors", 1024, False)
    # 缺省 kind → doc collection
    assert milvus_client.collection_for("dual", None) == ("doc_vectors", 1024, False)


def test_layout_defaults_to_current_strategy():
    """strategy=None 时回落 settings.embedding_strategy（显式钉住，避免依赖 .env）。"""
    saved = settings.embedding_strategy
    try:
        settings.embedding_strategy = "unified"
        name, dim, has_kind = milvus_client.collection_for(None, "code")
        assert (name, has_kind) == ("coderag_vectors", True)

        settings.embedding_strategy = "dual"
        name, dim, has_kind = milvus_client.collection_for(None, "code")
        assert (name, has_kind) == ("code_vectors", False)
    finally:
        settings.embedding_strategy = saved
