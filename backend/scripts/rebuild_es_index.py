"""重建 ES 全文索引（M31 升级/回退路径，spec §3.4）。

删 coderag_chunks → 按**当前 ES_IK_ENABLED** 选 mapping 重建 → 从 PG（source of truth）
全量重写 code+doc chunks（复用 indexing.build_*_es_doc，与 ingest 同源）。不动 PG/Milvus。
顺带清理 ES 历史残留文档（delete_by_file 漏删的旧 file_path）。预计分钟级（~6000 chunks）。

用法（从 backend/ 运行）::
  ES_IK_ENABLED=1 uv run python scripts/rebuild_es_index.py   # IK mapping
  ES_IK_ENABLED=0 uv run python scripts/rebuild_es_index.py   # 回退旧 mapping
"""
from __future__ import annotations

import argparse
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.clients import es_client  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.db.models import CodeChunk, CodeFile, DocChunk, DocFile  # noqa: E402
from app.pipeline import indexing  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="重建 ES 索引（PG 全量重写；mapping 由 ES_IK_ENABLED 决定）")
    ap.add_argument("--batch", type=int, default=500, help="bulk 批大小（默认 500）")
    args = ap.parse_args(argv)

    es = es_client.get_es()
    if bool(es.indices.exists(index=es_client.INDEX)):
        es.indices.delete(index=es_client.INDEX)
        print(f"已删除旧索引: {es_client.INDEX}")
    es_client.ensure_index()
    print(f"已建索引: {es_client.INDEX}  (ES_IK_ENABLED={settings.es_ik_enabled})")

    docs: list[dict] = []
    n_code = n_doc = 0
    engine = create_engine(settings.database_url_sync)
    with Session(engine) as session:
        code_rows = session.execute(
            select(CodeChunk, CodeFile.file_path)
            .join(CodeFile, CodeChunk.file_id == CodeFile.file_id)
            .where(CodeChunk.is_deleted == False)  # noqa: E712
        ).all()
        docs.extend(indexing.build_code_es_doc(chunk, fp) for chunk, fp in code_rows)
        n_code = len(code_rows)

        doc_rows = session.execute(
            select(DocChunk, DocFile.file_path)
            .join(DocFile, DocChunk.file_id == DocFile.file_id)
            .where(DocChunk.is_deleted == False)  # noqa: E712
        ).all()
        docs.extend(indexing.build_doc_es_doc(chunk, fp) for chunk, fp in doc_rows)
        n_doc = len(doc_rows)

    succ = 0
    for start in range(0, len(docs), args.batch):
        succ += es_client.bulk_index_chunks(docs[start:start + args.batch])
    print(f"完成: code={n_code} doc={n_doc} 写入 ES={succ}")
    if succ < len(docs):
        print("⚠ 部分批次写入失败（详见日志）", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
