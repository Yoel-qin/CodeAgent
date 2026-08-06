"""仓库级统一入库编排：parse → chunk → relate → embed → index。

按文件扩展名分流到代码 / 文档入库，per-file try/except+rollback（单文件失败不阻断整体），
最后可选构建关联（锚点匹配 + 调用图，:func:`relations.build_all`）。

代码入库 ``ingest_code.*`` 与文档入库 ``ingest_doc.*`` 内部已含 PG→ES→Milvus 一致性写入
（收敛在 :mod:`app.pipeline.indexing`），故本函数只负责遍历分流与关联编排，提交时机由调用方
（脚本 / API）控制。

示例::

    with Session(engine) as session:
        stats = ingest_repo(session, "../data/repo/sample", module="demo")
        session.commit()
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from app.pipeline import relations
from app.pipeline.ingest_code import ingest_java_file
from app.pipeline.ingest_doc import ingest_doc_file
from app.pipeline.parsing.router import EXT_KIND

# 扩展名 → kind 默认映射（真相源在 parsing.router.EXT_KIND：.java→code，.md/.pdf/.docx/.txt/...→doc）
DEFAULT_EXTS: dict[str, str] = EXT_KIND


def ingest_repo(
    session: Session, repo_path: str | Path, *, module: str | None = None,
    commit_hash: str = "UNKNOWN", doc_type: str | None = None,
    small_file_lines: int | None = None, build_relations: bool = True,
    exts: dict[str, str] | None = None,
) -> dict:
    """遍历 ``repo_path`` 按扩展名分流入库，可选构建关联。返回统计。

    - ``exts``：覆盖默认 ``{".java": "code", ".md": "doc"}``。
    - ``build_relations=True``：入库后调 ``relations.build_all(session, repo_path)``（锚点 + 调用图）。
    - 单文件失败 ``rollback`` 并记入 ``stats["errors"]``，不阻断其它文件。
    - 不提交——提交由调用方负责。
    """
    repo = Path(repo_path)
    ext_map = exts or DEFAULT_EXTS
    stats: dict = {
        "code": {"files": 0, "chunks": 0},
        "doc": {"files": 0, "chunks": 0},
        "errors": [],
        "details": [],  # 每文件结果（含 file_path/chunks 等），供 CLI 还原逐文件打印
    }

    for ext, kind in ext_map.items():
        for f in sorted(repo.rglob(f"*{ext}")):
            try:
                if kind == "code":
                    s = ingest_java_file(
                        session, f, commit_hash=commit_hash, repo_root=repo,
                        module_name=module, small_file_lines=small_file_lines,
                    )
                    stats["code"]["files"] += 1
                    stats["code"]["chunks"] += s.get("chunks", 0)
                    stats["details"].append({"kind": "code", **s})
                elif kind == "doc":
                    s = ingest_doc_file(
                        session, f, commit_hash=commit_hash, repo_root=repo, doc_type=doc_type,
                    )
                    stats["doc"]["files"] += 1
                    stats["doc"]["chunks"] += s.get("chunks", 0)
                    stats["details"].append({"kind": "doc", **s})
            except Exception as e:  # 单文件失败不阻断整体
                session.rollback()
                stats["errors"].append({"file": str(f), "error": f"{type(e).__name__}: {e}"})

    stats["relations"] = relations.build_all(session, repo_path=repo) if build_relations else None
    return stats
