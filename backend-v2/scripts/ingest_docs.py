#!/usr/bin/env python3
"""文档 ingest CLI：解析→分段→PG/Milvus/ES/MinIO 幂等写入。

用法: cd backend-v2 && python scripts/ingest_docs.py [--repo NAME] [--docs-dir PATH] [--reindex]
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# 自举 + Windows GBK 兜底
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")

# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="文档 ingest CLI")
    parser.add_argument("--repo", default=None, help="仓库名（默认 settings.default_repo）")
    parser.add_argument(
        "--docs-dir", default=None,
        help="文档目录（默认 <repo>/docs，不存在则扫 <repo> 顶层 *.md）",
    )
    parser.add_argument("--reindex", action="store_true", help="强制重嵌入（忽略 hash 幂等跳过）")
    args = parser.parse_args()

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.core.config import settings
    from app.db.models.doc import Document
    from app.pipeline.ingest_doc import (
        _ingest_doc_pg,
        _run_external_io,
        ingest_doc_repo,
    )

    repo = args.repo or settings.default_repo
    repos_root = Path(settings.repos_root).resolve()

    # 确定 docs_dir
    if args.docs_dir:
        docs_dir = Path(args.docs_dir).resolve()
        if not docs_dir.is_dir():
            logger.error("--docs-dir %s 不是目录", docs_dir)
            sys.exit(1)
        fallback = False
    else:
        docs_dir = repos_root / repo / "docs"
        fallback = not docs_dir.is_dir()

    if not fallback:
        # 正常路径：递归扫描所有支持格式（ingest_doc_repo 自建 engine，每文件事务）
        stats = ingest_doc_repo(repo=repo, docs_dir=docs_dir, reindex=args.reindex)
    else:
        # 回退路径：扫 repo 根目录顶层 *.md（同样每文件事务）
        repo_root = repos_root / repo
        if not repo_root.is_dir():
            logger.error("仓库目录不存在: %s", repo_root)
            sys.exit(1)
        md_files = sorted(
            f for f in repo_root.iterdir()
            if f.suffix.lower() == ".md" and f.is_file()
        )
        if not md_files:
            logger.error("未找到文档文件: %s", repo_root)
            sys.exit(1)
        logger.info(
            "回退模式: 扫描 %s 顶层 %d 个 *.md 文件", repo_root, len(md_files),
        )
        engine = create_engine(settings.postgres_dsn_sync)
        stats = {
            "total": 0, "skipped": 0, "sections": 0,
            "embedded": 0, "media": 0, "failed": 0,
        }
        for i, f in enumerate(md_files):
            if (i + 1) % 20 == 0:
                logger.info("进度: %d/%d", i + 1, len(md_files))
            data = f.read_bytes()
            try:
                # Phase 1: PG（per-file transaction）
                with engine.begin() as conn:
                    s = Session(bind=conn, expire_on_commit=False)
                    pg = _ingest_doc_pg(
                        s, repo=repo, file_path=f.name,
                        data=data, reindex=args.reindex,
                    )
                stats["total"] += 1
                if pg.get("skipped"):
                    stats["skipped"] += 1
                    continue
                if pg["status"] == "FAILED":
                    stats["failed"] += 1
                    continue
                # Phase 2: External IO + 短 PG 更新
                with engine.begin() as conn:
                    s = Session(bind=conn, expire_on_commit=False)
                    doc = s.query(Document).filter_by(
                        repo=repo, doc_name=pg["doc_name"],
                    ).first()
                    embedded = _run_external_io(
                        s, doc=doc, doc_name=pg["doc_name"],
                        repo=repo, section_rows=pg["section_rows"],
                        data=data,
                    )
                stats["sections"] += len(pg["section_rows"])
                stats["embedded"] += embedded
                stats["media"] += pg["media_count"]
            except Exception:
                logger.exception("处理失败: %s", f)
                stats["total"] += 1
                stats["failed"] += 1

    # 摘要
    logger.info(
        "完成 repo=%s: 总计 %d, 跳过 %d, sections %d, "
        "embedded %d, media %d, 失败 %d",
        repo,
        stats["total"], stats["skipped"],
        stats["sections"], stats["embedded"],
        stats["media"], stats["failed"],
    )


if __name__ == "__main__":
    main()
