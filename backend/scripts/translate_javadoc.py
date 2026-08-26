"""M32 ①b：LLM 翻译 javadoc —— 覆盖层（overlay），只译指定锚点的 chunk。

设计（spec §3.2）：
- 覆盖层语义：只改 content/keywords/token_count，**chunk_id 与 content_hash 不动**
  （调用图/锚点关系/外键全部无损）；源码变更触发重入库时覆盖层被自然冲掉，重跑本脚本恢复。
- 只译所需（用户约束）：--tags 从 eval_set.yaml 解析锚点（仅 Class.method 项，
  literal/整类名跳过计数）；--anchors 直接给列表。重载 chunk 经 code_anchor_key 全含。
- 写回后 eager 重嵌入（unified 主档；dual 主+code_vectors_bge 镜像）+ ES 单文件重索引。
- 幂等：content 以 marker 开头则跳过；快照保留最早份；--restore 整体还原。

用法（backend/ 下）：
  uv run python scripts/translate_javadoc.py --tags rocketmq
  uv run python scripts/translate_javadoc.py --anchors "DefaultMQProducerImpl.send,MixAll.getRetryTopic"
  uv run python scripts/translate_javadoc.py --restore
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.clients.llm_client import LLMClient  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.pipeline import indexing  # noqa: E402
from app.pipeline.metadata import approx_token_count, extract_doc_keywords  # noqa: E402

BACKUP_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_m32_xlate_backup.json")
DEFAULT_EVAL_SET = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "eval", "eval_set.yaml",
)
MARKER = "/** [zh-xlate]"
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _anchors_from_tags(eval_set_path: str, tags: list[str]) -> tuple[list[str], int]:
    """Read eval_set.yaml, collect unique Class.method anchors from queries matching *tags*.

    Items that are bare class names or literal chunk_ids are counted as *skipped* and not
    included in the returned anchor list.
    """
    with open(eval_set_path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    anchors: dict[str, None] = {}
    skipped = 0
    for q in data.get("queries") or []:
        if not (set(tags) & set(q.get("tags") or [])):
            continue
        for item in q.get("relevant") or []:
            if re.fullmatch(r"[A-Za-z_][\w$]*\.[A-Za-z_][\w$]*", str(item)):
                anchors.setdefault(str(item), None)
            else:
                skipped += 1
    return list(anchors), skipped


def _wrap(translation: str) -> str:
    lines = [ln.strip() for ln in translation.strip().splitlines() if ln.strip()]
    body = "\n".join(f" * {ln}" for ln in lines)
    return f"{MARKER}\n{body}\n */\n"


async def _translate_batch(llm: LLMClient, docs: list[str]) -> list[str | None]:
    """Batch-translate javadoc strings via LLM.  JSON-array round-trip; one retry.

    On unrecoverable failure (bad JSON / length mismatch even after retry) the entire
    batch is returned as ``[None, ...]`` so that each chunk is individually skipped.
    """
    msgs = [
        {
            "role": "system",
            "content": (
                "你是资深 Java 中间件工程师。把输入 JSON 数组中的英文 javadoc 逐条翻译成简体中文"
                "技术注释：保留代码标识符/参数名原文，去掉 @param/@return/@throws 标签行，每条输出"
                "1-3 句中文说明。只输出与输入等长的 JSON 字符串数组，不要任何其他文本。"
            ),
        },
        {"role": "user", "content": json.dumps(docs, ensure_ascii=False)},
    ]
    for _ in range(2):  # first attempt + 1 retry
        try:
            raw = await llm.chat(msgs, max_tokens=8192)
            if not raw or not raw.strip():
                continue
            arr = json.loads(_FENCE_RE.sub("", raw.strip()))
            if isinstance(arr, list) and len(arr) == len(docs) and all(isinstance(x, str) for x in arr):
                return arr
        except Exception:
            continue
    return [None] * len(docs)


def _apply_update(
    session: Session,
    chunk_id: str,
    block: str,
    *,
    old_kw: list[str],
    old_content: str,
) -> None:
    """Write translated block back to a single chunk.

    Content is *prepended* with the translation block; keywords are merged (union);
    token_count is recomputed.  **chunk_id and content_hash are never touched.**
    """
    kws = list(old_kw or [])
    seen = {k.lower() for k in kws}
    for tok in extract_doc_keywords(block, max_n=32):
        if tok.lower() not in seen:
            kws.append(tok)
            seen.add(tok.lower())
    session.execute(
        text(
            "UPDATE code_chunks SET content = :new, keywords = :kw, token_count = :tok "
            "WHERE chunk_id = :id"
        ),
        {"c": chunk_id, "new": block + old_content, "kw": json.dumps(kws[:32], ensure_ascii=False),
         "tok": approx_token_count(block + old_content), "id": chunk_id},
    )


def _reembed_and_reindex(session: Session, chunk_ids: list[str]) -> None:
    """Eager re-embed (primary + dual mirror) + ES re-index by file.  Soft-fail each step."""
    rows = session.execute(
        text(
            "SELECT c.chunk_id, c.content, c.keywords, c.class_name, c.method_name, "
            "       c.chunk_type, c.code_anchor_key, f.file_path FROM code_chunks c "
            "JOIN code_files f ON c.file_id = f.file_id WHERE c.chunk_id = ANY(cast(:ids as text[]))"
        ),
        {"ids": chunk_ids},
    ).mappings().all()
    specs = [type("S", (), dict(r))() for r in rows]  # duck-typed spec for build_code_es_doc
    strat = settings.embedding_strategy
    for kind in ("code", "code_bge"):
        try:
            if indexing._embed_enabled_for(strat, kind):
                indexing.index_chunks_to_milvus(
                    strat,
                    kind,
                    [{"chunk_id": s.chunk_id, "text": indexing.embed_text_for("code", s)} for s in specs],
                )
        except Exception as e:
            print(f"WARN: re-embed {kind} 失败（{e}）——可由 resync/reindex_code_bge 补")
    by_file: dict[str, list[dict]] = {}
    for s in specs:
        by_file.setdefault(s.file_path, []).append(indexing.build_code_es_doc(s, s.file_path))
    for fp, docs in by_file.items():
        try:
            indexing.index_chunks_to_es(fp, docs)
        except Exception as e:
            print(f"WARN: ES 重索引 {fp} 失败（{e}）——可由 rebuild_es_index.py 补")


async def _run_translate(session: Session, anchors: list[str], batch_size: int) -> int:
    rows = session.execute(
        text(
            "SELECT chunk_id, javadoc, content, keywords FROM code_chunks "
            "WHERE code_anchor_key = ANY(cast(:a as text[])) AND is_deleted = false"
        ),
        {"a": anchors},
    ).mappings().all()
    backup: dict[str, dict] = {}
    if os.path.exists(BACKUP_PATH):
        with open(BACKUP_PATH, encoding="utf-8") as f:
            backup = json.load(f)
        print(f"已有快照 {len(backup)} 条（保留原始份）")
    targets = [r for r in rows if r["javadoc"]]
    no_doc = len(rows) - len(targets)
    llm = LLMClient()
    updated: list[str] = []
    done = 0
    for i in range(0, len(targets), batch_size):
        batch = targets[i : i + batch_size]
        trans = await _translate_batch(llm, [r["javadoc"] for r in batch])
        for r, t in zip(batch, trans):
            if t is None:
                print(f"WARN: 翻译失败跳过 {r['chunk_id']}")
                continue
            if (r["content"] or "").startswith(MARKER):
                continue  # already translated (idempotent)
            if r["chunk_id"] not in backup:
                backup[r["chunk_id"]] = {"content": r["content"], "keywords": r["keywords"]}
            _apply_update(
                session,
                r["chunk_id"],
                _wrap(t),
                old_kw=list(r["keywords"] or []),
                old_content=r["content"] or "",
            )
            updated.append(r["chunk_id"])
            done += 1
        session.commit()
        print(f"进度 {min(i + batch_size, len(targets))}/{len(targets)}（本轮新增 {done}）")
    if updated:
        _reembed_and_reindex(session, updated)
    with open(BACKUP_PATH, "w", encoding="utf-8") as f:
        json.dump(backup, f, ensure_ascii=False)
    print(f"完成：translated={done} skipped_no_javadoc={no_doc} -> {BACKUP_PATH}")
    return 0


def _restore(session: Session) -> int:
    if not os.path.exists(BACKUP_PATH):
        print(f"无快照文件: {BACKUP_PATH}")
        return 1
    with open(BACKUP_PATH, encoding="utf-8") as f:
        backup: dict[str, dict] = json.load(f)
    for cid, snap in backup.items():
        session.execute(
            text("UPDATE code_chunks SET content = :c, keywords = :k WHERE chunk_id = :id"),
            {"c": snap["content"], "k": snap["keywords"], "id": cid},
        )
    session.commit()
    print(f"还原完成: {len(backup)} chunks（重嵌入/ES 请跑 resync 或 rebuild_es_index.py）")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="M32 task6: LLM translate javadoc overlay (scoped anchors)")
    ap.add_argument("--tags", nargs="+", default=None, help="从 eval_set.yaml 按 tags 取锚点")
    ap.add_argument("--eval-set", default=DEFAULT_EVAL_SET)
    ap.add_argument("--anchors", default=None, help="逗号分隔 Class.method 锚点列表")
    ap.add_argument("--batch-size", type=int, default=20)
    ap.add_argument("--restore", action="store_true")
    args = ap.parse_args(argv)

    engine = create_engine(settings.database_url_sync)
    with Session(engine) as session:
        if args.restore:
            return _restore(session)
        if args.tags:
            anchors, skipped = _anchors_from_tags(args.eval_set, args.tags)
            print(f"锚点 {len(anchors)} 个（非方法锚点跳过 {skipped} 项）")
        elif args.anchors:
            anchors = [a.strip() for a in args.anchors.split(",") if a.strip()]
        else:
            ap.error("--tags 或 --anchors 必须给一个")
            return 2
        if not anchors:
            print("无锚点可处理")
            return 0
        return asyncio.run(_run_translate(session, anchors, args.batch_size))


if __name__ == "__main__":
    raise SystemExit(main())
