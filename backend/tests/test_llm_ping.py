"""llm_ping --tier 参数解析纯函数测试（零网络）。"""
from __future__ import annotations

import pytest

from scripts.llm_ping import build_parser


def test_tier_default_reasoning():
    ns = build_parser().parse_args([])
    assert ns.tier == "reasoning"


@pytest.mark.parametrize("t", ["routing", "extraction", "reasoning"])
def test_tier_choices(t):
    assert build_parser().parse_args(["--tier", t]).tier == t


def test_tier_rejects_unknown():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--tier", "embed"])
