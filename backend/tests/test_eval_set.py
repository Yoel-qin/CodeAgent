"""评测集结构校验（eval/eval_set.yaml）：条数、id 唯一、relevant 标注形状、text/relevant 非空。

不依赖 DB（不解析 chunk_id），只保证标注语法合规；实际解析命中率由 ``--validate`` 门控。
"""
from __future__ import annotations

import re
from pathlib import Path

from app.eval.eval_service import load_eval_queries

_EVAL_SET = Path(__file__).resolve().parents[1] / "eval" / "eval_set.yaml"
# relevant 标注形状：Class.method | 裸类名 | code_/doc_ 字面 chunk_id（均 \w+ 可含下划线）
_RELEVANT_RE = re.compile(r"^\w+(\.\w+)?$")


def test_eval_set_well_formed():
    queries = load_eval_queries(str(_EVAL_SET))
    assert 80 <= len(queries) <= 100, f"expected ~85 queries, got {len(queries)}"

    ids = [q.id for q in queries]
    assert len(ids) == len(set(ids)), "duplicate query ids"

    for q in queries:
        assert q.text.strip(), f"empty text in {q.id}"
        assert q.relevant, f"empty relevant in {q.id}"
        for rel in q.relevant:
            assert _RELEVANT_RE.match(rel), f"bad relevant {rel!r} in {q.id}"
