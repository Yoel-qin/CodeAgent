"""代码解析：tree-sitter Java AST → ParsedCodeFile（设计 §3.1）。

提取：文件级（包/导入/路径/模块）、类级（名/修饰/继承/实现/Javadoc）、
方法级（签名/参数/返回/Javadoc/注解/行号/源码/调用表达式）。
"""
from __future__ import annotations

from functools import lru_cache

import tree_sitter_java as tsj
from tree_sitter import Language, Parser, Query, QueryCursor

from app.pipeline.parsing.doc_element import CodeClass, CodeMethod, ParsedCodeFile

_LANGUAGE = Language(tsj.language())

_TYPE_QUERIES = {
    "class": "(class_declaration) @n",
    "interface": "(interface_declaration) @n",
    "enum": "(enum_declaration) @n",
    "record": "(record_declaration) @n",
    "annotation_type": "(annotation_type_declaration) @n",
}
_METHOD_KINDS = {"method_declaration", "constructor_declaration", "annotation_type_element_declaration"}
_COMMENT_KINDS = {"block_comment", "line_comment"}


@lru_cache(maxsize=1)
def _parser() -> Parser:
    return Parser(_LANGUAGE)


def _text(src: bytes, node) -> str:
    return src[node.start_byte:node.end_byte].decode("utf-8", "replace")


def _query(pattern: str) -> Query:
    return Query(_LANGUAGE, pattern)


def _mods_and_annotations(decl) -> tuple[list[str], list[str]]:
    """从声明节点提取修饰符与注解（public/static/final + @Override 等）。

    tree-sitter-java 把修饰符包在 `modifiers` 节点里，关键字本身是独立节点
    （type 为 'public'/'static'/...，而非 'modifier'）；注解为 annotation/marker_annotation。
    """
    modifiers: list[str] = []
    annotations: list[str] = []
    for ch in decl.children:
        if ch.type == "modifiers":
            for m in ch.children:
                if m.type in ("annotation", "marker_annotation"):
                    annotations.append(_anno_name(m))
                else:
                    txt = _node_text(m)
                    if txt:
                        modifiers.append(txt)
        elif ch.type in ("annotation", "marker_annotation"):
            annotations.append(_anno_name(ch))
    return modifiers, annotations


_TYPE_NODE_KINDS = ("type_identifier", "scoped_type_identifier", "generic_type")


def _collect_type_names(clause_node) -> list[str]:
    """从 extends/implements 子句节点收集类型名（descend into type_list）。"""
    names: list[str] = []
    stack = [clause_node]
    while stack:
        cur = stack.pop(0)
        for ch in cur.children:
            if ch.type in _TYPE_NODE_KINDS:
                names.append(_node_text(ch))
            elif ch.type in ("type_list", "super_interfaces", "superclass", "implements", "extends"):
                stack.append(ch)
    return names


def _first_type_name(clause_node) -> str | None:
    names = _collect_type_names(clause_node)
    return names[0] if names else None


def _node_text(node) -> str:
    """读取节点文本（tree-sitter 0.26 Node.text 返回 bytes）。"""
    raw = node.text
    return raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)


def _anno_name(anno_node) -> str:
    name_node = anno_node.child_by_field_name("name")
    if name_node is None:
        return _node_text(anno_node).lstrip("@")
    return _node_text(name_node)


def _javadoc_before(node) -> str | None:
    """紧邻节点之前的 Javadoc（/** ... */），非相邻或非 javadoc 返回 None。"""
    parent = node.parent
    if parent is None:
        return None
    prev_comment = None
    for ch in parent.children:
        if ch.start_byte >= node.start_byte:
            break
        if ch.type in _COMMENT_KINDS:
            prev_comment = ch
    if prev_comment is None:
        return None
    raw = _node_text(prev_comment)
    # 仅当与目标相邻（中间仅空白）
    if not _adjacent(prev_comment, node):
        return None
    return raw if raw.startswith("/**") else None


def _adjacent(prev, node) -> bool:
    # Node 之间只能有空白才算相邻
    try:
        from tree_sitter import Tree  # noqa: F401
    except Exception:  # pragma: no cover
        pass
    src = _source_of(node)
    if src is None:
        return True
    gap = src[prev.end_byte:node.start_byte]
    return gap.strip() == b""


def _source_of(node) -> bytes | None:
    """取节点所属源码 bytes（沿父节点取其 .text 的根）。"""
    cur = node
    while cur.parent is not None:
        cur = cur.parent
    raw = cur.text
    return raw if isinstance(raw, bytes) else None


def _extract_calls(method_body, src: bytes) -> list[tuple[str | None, str]]:
    """方法体内调用 (receiver, 方法名)，去重保序（M46 调用对）。

    receiver 仅当 method_invocation 的 object 域是 identifier（简单变量/类名）或 this/super
    关键字节点时记录其文本；链式调用（a.getB().c() 的 c）、字面量、复杂表达式记 None。
    """
    if method_body is None:
        return []
    seen: dict[tuple[str | None, str], None] = {}
    for inv in QueryCursor(_query("(method_invocation) @inv")).captures(method_body).get("inv", []):
        name_node = inv.child_by_field_name("name")
        if name_node is None:
            continue
        obj_node = inv.child_by_field_name("object")
        recv = (_text(src, obj_node)
                if obj_node is not None and obj_node.type in ("identifier", "this", "super")
                else None)
        seen.setdefault((recv, _text(src, name_node)), None)
    return list(seen.keys())


def _simple_type_name(raw: str) -> str:
    """类型文本 → 简单名：剥泛型 <...>、数组 [...]、修饰前缀、包限定（取末段）。"""
    t = raw.split("<", 1)[0].split("[", 1)[0].strip()
    parts = t.split()
    if len(parts) > 1:  # "final MessageStore" 等带修饰前缀 → 类型是最后一段
        t = parts[-1]
    return t.rsplit(".", 1)[-1] if "." in t else t


def _declarator_names(decl_node, src: bytes) -> list[str]:
    """field/local 声明节点内的变量名（variable_declarator 的 identifier）。"""
    q = _query("(variable_declarator name: (identifier) @vn)")
    return [_text(src, vn) for vn in QueryCursor(q).captures(decl_node).get("vn", [])]


def _parse_fields(body, src: bytes) -> dict[str, str]:
    """类体字段：变量名 → 声明类型简单名。"""
    fields: dict[str, str] = {}
    for f in QueryCursor(_query("(field_declaration) @f")).captures(body).get("f", []):
        type_node = f.child_by_field_name("type")
        if type_node is None:
            continue
        tname = _simple_type_name(_node_text(type_node))
        if not tname:
            continue
        for vn in _declarator_names(f, src):
            fields[vn] = tname
    return fields


def _parse_local_types(body_node, src: bytes) -> dict[str, str]:
    """方法体内局部变量声明：变量名 → 类型简单名。

    try-with-resources / for-var / lambda 参数不覆盖（M46 非目标，诚实限制）。
    """
    if body_node is None:
        return {}
    out: dict[str, str] = {}
    for d in QueryCursor(_query("(local_variable_declaration) @d")).captures(body_node).get("d", []):
        type_node = d.child_by_field_name("type")
        if type_node is None:
            continue
        tname = _simple_type_name(_node_text(type_node))
        for vn in _declarator_names(d, src):
            out[vn] = tname
    return out


def _parse_method(method_node, class_name: str, src: bytes) -> CodeMethod:
    name_node = method_node.child_by_field_name("name")
    name = _text(src, name_node) if name_node else "<anonymous>"

    modifiers, annotations = _mods_and_annotations(method_node)

    return_type_node = method_node.child_by_field_name("type")
    return_type = _text(src, return_type_node) if return_type_node else None

    params_node = method_node.child_by_field_name("parameters")
    params_text = _text(src, params_node) if params_node else "()"
    parameters = [
        _text(src, p).strip()
        for p in (params_node.children if params_node else [])
        if p.type == "formal_parameter"
    ]

    mods = " ".join(modifiers)
    sig = " ".join(p for p in [mods, return_type, f"{name}{params_text}"] if p).strip()

    javadoc = _javadoc_before(method_node)
    start_node = _comment_node_before(method_node) if javadoc else method_node
    start_line = (start_node.start_point[0] + 1) if start_node else (method_node.start_point[0] + 1)
    end_line = method_node.end_point[0] + 1
    source = _text(src, start_node if start_node else method_node) if javadoc else _text(src, method_node)

    body_node = method_node.child_by_field_name("body")
    calls = _extract_calls(body_node, src)
    local_types = _parse_local_types(body_node, src)

    return CodeMethod(
        name=name,
        class_name=class_name,
        signature=sig,
        modifiers=modifiers,
        return_type=return_type,
        parameters=parameters,
        annotations=annotations,
        javadoc=javadoc,
        start_line=start_line,
        end_line=end_line,
        source=source,
        calls=calls,
        local_types=local_types,
    )


def _comment_node_before(node):
    """返回紧邻的 comment 节点（供 source/行号定位）。"""
    parent = node.parent
    if parent is None:
        return None
    prev = None
    for ch in parent.children:
        if ch.start_byte >= node.start_byte:
            break
        if ch.type in _COMMENT_KINDS:
            prev = ch
    if prev is not None and _adjacent(prev, node):
        return prev
    return None


def _parse_type(type_node, kind: str, src: bytes) -> CodeClass:
    name_node = type_node.child_by_field_name("name")
    name = _text(src, name_node) if name_node else "<anonymous>"

    modifiers, annotations = _mods_and_annotations(type_node)
    javadoc = _javadoc_before(type_node)

    superclass_node = type_node.child_by_field_name("superclass")
    superclass = _first_type_name(superclass_node) if superclass_node is not None else None

    interfaces: list[str] = []
    iface_field = (
        type_node.child_by_field_name("interfaces")
        or type_node.child_by_field_name("super_interfaces")
    )
    if iface_field is not None:
        interfaces = _collect_type_names(iface_field)

    body = type_node.child_by_field_name("body")
    methods: list[CodeMethod] = []
    if body is not None:
        for ch in body.children:
            if ch.type in _METHOD_KINDS:
                methods.append(_parse_method(ch, name, src))
    fields = _parse_fields(body, src) if body is not None else {}

    return CodeClass(
        name=name,
        kind=kind,
        modifiers=modifiers,
        annotations=annotations,
        javadoc=javadoc,
        superclass=superclass,
        interfaces=interfaces,
        start_line=type_node.start_point[0] + 1,
        end_line=type_node.end_point[0] + 1,
        methods=methods,
        fields=fields,
    )


def parse_java(source: str | bytes, file_path: str, *, module_name: str | None = None,
               commit_hash: str | None = None) -> ParsedCodeFile:
    """解析单个 Java 文件。"""
    src = source.encode("utf-8") if isinstance(source, str) else source
    tree = _parser().parse(src)
    root = tree.root_node

    # 包名
    package = None
    pkg_q = _query("(package_declaration) @pkg")
    pkgs = QueryCursor(pkg_q).captures(root).get("pkg", [])
    if pkgs:
        name_field = pkgs[0].child_by_field_name("name") or pkgs[0]
        package = _text(src, name_field) if name_field.type != "package_declaration" else _text(src, name_field).removeprefix("package ").rstrip(";")

    # 导入
    imports: list[str] = []
    imp_q = _query("(import_declaration) @imp")
    for imp in QueryCursor(imp_q).captures(root).get("imp", []):
        imports.append(_text(src, imp))

    # 类型声明（含嵌套）
    classes: list[CodeClass] = []
    for kind, pattern in _TYPE_QUERIES.items():
        for node in QueryCursor(_query(pattern)).captures(root).get("n", []):
            classes.append(_parse_type(node, kind, src))

    if module_name is None and package:
        module_name = package.split(".")[0]

    total_lines = src.count(b"\n") + (0 if src.endswith(b"\n") or not src else 1)

    return ParsedCodeFile(
        file_path=file_path,
        package=package,
        imports=imports,
        module_name=module_name,
        total_lines=total_lines or 1,
        classes=classes,
        source=src.decode("utf-8", "replace"),
        commit_hash=commit_hash,
    )
