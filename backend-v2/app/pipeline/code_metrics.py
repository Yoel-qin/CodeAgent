"""代码度量计算（纯函数，无 IO / 无 DB）。

complexity = 方法 source 中 \b(if|for|while|case|catch)\b 计数 + 1
loc = end_line - start_line + 1
fan_in / fan_out 由调用边聚合传入。
"""
from __future__ import annotations

import re

from app.pipeline.parsing.code_element import ParsedCodeFile

_COMPLEXITY_RE = re.compile(r"\b(if|for|while|case|catch)\b")


def compute_metrics(
    parsed_files: list[ParsedCodeFile],
    fan_in_out: dict[tuple[str, str], tuple[int, int]],
) -> list[dict]:
    """计算每个方法实体的度量，返回可入库的 dict 列表。

    fan_in_out: {(class_name, method_name): (fan_in, fan_out)}
    返回: [{"class_name", "method_name", "complexity", "fan_in", "fan_out", "loc"}]
    """
    rows: list[dict] = []
    for pf in parsed_files:
        for cls in pf.classes:
            for m in cls.methods:
                key = (cls.name, m.name)
                fi, fo = fan_in_out.get(key, (0, 0))
                complexity = len(_COMPLEXITY_RE.findall(m.source)) + 1
                loc = m.end_line - m.start_line + 1
                rows.append({
                    "class_name": cls.name,
                    "method_name": m.name,
                    "complexity": complexity,
                    "fan_in": fi,
                    "fan_out": fo,
                    "loc": loc,
                })
    return rows
