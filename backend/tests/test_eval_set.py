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


def test_eval_set_qa_structure():
    """eval_set_qa.yaml：每条有 id/text/scoring_hints，无 dup id。"""
    from app.eval.qa_service import load_qa_queries
    from app.services.eval_run_service import DEFAULT_QA_EVAL_SET
    qs = load_qa_queries(str(DEFAULT_QA_EVAL_SET))
    assert len(qs) >= 8
    ids = [q.id for q in qs]
    assert len(ids) == len(set(ids))  # 无重复
    assert all(q.text and isinstance(q.scoring_hints, dict) for q in qs)
