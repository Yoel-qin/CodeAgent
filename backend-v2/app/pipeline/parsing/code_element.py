"""代码解析中间结构：ParsedCodeFile / CodeClass / CodeMethod（tree-sitter Java 解析产出）。

从旧库 doc_element.py 的代码段原样拷贝（Task 9）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CodeMethod:
    name: str
    class_name: str
    signature: str                       # 完整签名（modifiers + 返回类型 + 名 + 参数）
    modifiers: list[str]
    return_type: str | None
    parameters: list[str]                # ["int x", "String y"]
    annotations: list[str]
    javadoc: str | None
    start_line: int                      # 从 1 开始（含 Javadoc）
    end_line: int
    source: str                          # 方法源码（含 Javadoc）
    # M46：调用对（receiver 简单标识符 or None, 方法名）。receiver 仅当 object 域为
    # identifier 时记录——链式调用（a.getB().c() 的 c）记 None（跨类定型够不着的边放弃）。
    calls: list[tuple[str | None, str]] = field(default_factory=list)
    local_types: dict[str, str] = field(default_factory=dict)  # 局部变量名 → 类型简单名


@dataclass
class CodeClass:
    name: str
    kind: str                            # class / interface / enum / record / annotation_type
    modifiers: list[str]
    annotations: list[str]
    javadoc: str | None
    superclass: str | None
    interfaces: list[str]
    start_line: int
    end_line: int
    methods: list[CodeMethod] = field(default_factory=list)
    fields: dict[str, str] = field(default_factory=dict)  # M46：字段名 → 类型简单名


@dataclass
class ParsedCodeFile:
    file_path: str
    package: str | None
    imports: list[str]
    module_name: str | None
    total_lines: int
    classes: list[CodeClass]
    source: str
    commit_hash: str | None = None
    commit_time: Any = None
