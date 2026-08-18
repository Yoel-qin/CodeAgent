"""M43 导出 CLI 单测：build_fragment 纯函数（eval-set 兼容形状）。"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

# scripts 不在包路径，需要手动插入
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.export_candidates import build_fragment


def _row(id=1, query="RocketMQ 消息堆积怎么排查", categories=None, correction="应该是刷盘",
         repo="apache/rocketmq"):
    return SimpleNamespace(id=id, query=query, categories=categories or ["答案错误"],
                           correction=correction, repo=repo)


def test_fragment_shape_matches_eval_set():
    """测试片段形状与 eval_set 条目一致。"""
    frag = build_fragment([_row()])
    assert "queries:" in frag
    assert '- { id: fb_1, text: "RocketMQ 消息堆积怎么排查", relevant: [] }' in frag
    assert "# 纠错: 应该是刷盘" in frag            # 纠错进注释，供标注者参考
    assert "# 来源 repo: apache/rocketmq" in frag


def test_fragment_empty():
    """测试空候选列表返回提示。"""
    assert build_fragment([]) == "# 无候选（CANDIDATE 状态为空）\n"


def test_fragment_multiple_rows():
    """测试多行候选。"""
    rows = [
        _row(id=1, query="查询1", correction="纠错1", repo="repo1"),
        _row(id=2, query="查询2", correction=None, repo=None),
        _row(id=3, query="查询3", correction="纠错3", repo="repo3"),
    ]
    frag = build_fragment(rows)
    assert "queries:" in frag
    assert 'id: fb_1, text: "查询1"' in frag
    assert "# 纠错: 纠错1" in frag
    assert "# 来源 repo: repo1" in frag
    # row 2: 无任何注释
    assert 'id: fb_2, text: "查询2", relevant: [] }\n' in frag  # 行尾无注释
    # row 3: 有纠错和 repo
    assert 'id: fb_3, text: "查询3"' in frag
    assert "# 纠错: 纠错3" in frag
    assert "# 来源 repo: repo3" in frag
