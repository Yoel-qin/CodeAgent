"""M43 导出候选 eval query（feedback 闭环 → eval 集人工通道）。

用法（from backend/）：
  uv run python scripts/export_candidates.py                 # 列 CANDIDATE 候选
  uv run python scripts/export_candidates.py -o out.yaml     # 导出片段 + 翻 EXPORTED
  uv run python scripts/export_candidates.py --ids 1,2 --mark merged|rejected

片段形状对齐 backend/eval/eval_set.yaml 条目（id/text/relevant）——relevant 留空人工补，
纠错与 repo 进注释。服务端不自动写 committed YAML（多实例/容器化下写仓库文件会坏）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# 中文 Windows GBK 控制台
if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models.eval import CandidateEvalQuery


def build_fragment(rows) -> str:
    """候选行 → eval-set 兼容 YAML 片段（纯函数，可测）。"""
    if not rows:
        return "# 无候选（CANDIDATE 状态为空）\n"
    lines = ["queries:"]
    for r in rows:
        note = f"  # 纠错: {json.dumps(r.correction, ensure_ascii=False)}" if r.correction else ""
        repo_note = f"  # 来源 repo: {r.repo}" if r.repo else ""
        lines.append(
            f'- {{ id: fb_{r.id}, text: {json.dumps(r.query, ensure_ascii=False)}, relevant: [] }}{repo_note}{note}'
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-o", "--out", type=Path, default=None, help="输出 YAML 片段到文件并翻 EXPORTED")
    ap.add_argument("--ids", default="", help="逗号分隔候选 id（配合 --mark）")
    ap.add_argument("--mark", choices=["merged", "rejected", "exported"], default=None)
    args = ap.parse_args()

    engine = create_engine(settings.database_url_sync)
    with Session(engine) as session:
        if args.mark and args.ids:
            ids = [int(x) for x in args.ids.split(",") if x.strip()]
            rows = session.execute(
                select(CandidateEvalQuery).where(CandidateEvalQuery.id.in_(ids))
            ).scalars().all()
            for r in rows:
                r.status = args.mark.upper()
            session.commit()
            print(f"已翻 {len(rows)} 条 → {args.mark.upper()}")
            return 0

        rows = session.execute(
            select(CandidateEvalQuery).where(CandidateEvalQuery.status == "CANDIDATE")
            .order_by(CandidateEvalQuery.id)
        ).scalars().all()
        if not rows:
            print("无候选")
            return 0
        for r in rows:
            print(f"[{r.id}] ({r.repo or '-'}) {r.query}  分类={r.categories} 纠错={r.correction or '-'}")
        if args.out:
            frag = build_fragment(rows)
            args.out.write_text(frag, encoding="utf-8")
            for r in rows:
                r.status = "EXPORTED"
            session.commit()
            print(f"已导出 {len(rows)} 条 → {args.out}（状态翻 EXPORTED）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
