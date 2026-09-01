"""四步跨类调用图构建（M46 算法移植，纯函数，无 IO / 无 DB）。

Steps:
① receiver 定型（参数>字段>局部变量，miss → 类名 fallback=static）
② 类型简单名匹配 class_name
③ 继承闭包分发到后代实现类
④ this/super/无 receiver → 同类（same_class）

边 dict 字段：caller_class, caller_method, callee_class, callee_method,
             call_type (direct/static/same_class/interface_dispatch),
             call_site_file, call_site_line.
call_site_line = 调用方法 start_line（v1 粒度）。
"""
from __future__ import annotations

import re
from collections import defaultdict

from app.pipeline.parsing.code_element import (
    CodeClass,
    CodeMethod,
    ParsedCodeFile,
)

_MODIFIER_TOKENS = {"final", "transient", "volatile", "static"}
_GENERIC_RE = re.compile(r"<[^<>]*>")


def _simple_name(type_text: str) -> str:
    """Strip generics, arrays, modifiers → last token's simple name.

    "final Map<String, String>" → "Map", "org.acme.Foo[]" → "Foo".
    """
    t = _GENERIC_RE.sub("", type_text).split("[", 1)[0].strip()
    toks = [x for x in t.split() if x not in _MODIFIER_TOKENS]
    if not toks:
        return ""
    return toks[-1].rsplit(".", 1)[-1]


def _param_types(m: CodeMethod) -> dict[str, str]:
    """参数原文 → {变量名: 类型简单名}。

    过滤 @注解与修饰词后，末 token 为变量名，其余拼回类型文本。
    """
    out: dict[str, str] = {}
    for p in m.parameters:
        toks = [x for x in _GENERIC_RE.sub("", p).split() if not x.startswith("@")]
        if len(toks) < 2:
            continue
        tname = _simple_name(" ".join(toks[:-1]))
        if tname:
            out[toks[-1].rstrip("...")] = tname
    return out


def _infer_type(recv: str, m: CodeMethod, cls: CodeClass) -> str | None:
    """receiver 变量 → 声明类型简单名。

    优先级：方法参数 > 本类字段 > 局部变量；全 miss → None。
    """
    for scope in (_param_types(m), cls.fields, m.local_types):
        t = scope.get(recv)
        if t:
            return t
    return None


def _descendants(name: str, children: dict[str, set[str]], limit: int = 5) -> set[str]:
    """类型名的全部后代类（BFS，防环，限深）。

    起点预置进 seen——环（A↔B）不重复；起点自身不算后代。
    """
    seen: set[str] = {name}
    frontier = [name]
    for _ in range(limit):
        nxt: list[str] = []
        for n in frontier:
            for ch in children.get(n, ()):
                if ch not in seen:
                    seen.add(ch)
                    nxt.append(ch)
        if not nxt:
            break
        frontier = nxt
    return seen - {name}


def _build_children_map(parsed_files: list[ParsedCodeFile]) -> dict[str, set[str]]:
    """Build parent simple-name → set of child class names map."""
    children: dict[str, set[str]] = defaultdict(set)
    for pf in parsed_files:
        for cls in pf.classes:
            if cls.superclass:
                children[_simple_name(cls.superclass)].add(cls.name)
            for iface in cls.interfaces:
                if iface.strip():
                    children[_simple_name(iface.strip())].add(cls.name)
    return children


def build_call_edges(parsed_files: list[ParsedCodeFile]) -> list[dict]:
    """从解析后的文件列表构建跨类调用边（纯函数，四步算法）。

    返回边 dict 列表，每条边包含：
      caller_class, caller_method, callee_class, callee_method,
      call_type, call_site_file, call_site_line。

    call_type 取值：
      "same_class"   — this/super/无 receiver，同类内调用
      "static"       — receiver 定型 miss，以 receiver 名当类名
      "direct"       — 定型成功 + 简单名直接匹配
      "interface_dispatch" — 经继承闭包分发到后代实现类

    call_site_line = 调用方法的 start_line（v1 粒度，行级定位 P2）。
    """
    method_set: set[tuple[str, str]] = set()  # (class_name, method_name)
    for pf in parsed_files:
        for cls in pf.classes:
            for m in cls.methods:
                method_set.add((cls.name, m.name))

    children = _build_children_map(parsed_files)
    edges: list[dict] = []
    seen_edges: set[tuple[str, str, str, str]] = set()

    for pf in parsed_files:
        for cls in pf.classes:
            for m in cls.methods:
                for recv, callee_name in m.calls:
                    # Step ④: this/super/无 receiver → same_class
                    if recv in (None, "this", "super"):
                        if (cls.name, callee_name) in method_set:
                            key = (cls.name, m.name, cls.name, callee_name)
                            if key not in seen_edges:
                                seen_edges.add(key)
                                edges.append({
                                    "caller_class": cls.name,
                                    "caller_method": m.name,
                                    "callee_class": cls.name,
                                    "callee_method": callee_name,
                                    "call_type": "same_class",
                                    "call_site_file": pf.file_path,
                                    "call_site_line": m.start_line,
                                })
                        continue

                    # Step ①: receiver 定型
                    t = _infer_type(recv, m, cls)
                    if t is None:
                        t = recv  # fallback → 类名 = static
                        call_type = "static"
                    else:
                        call_type = "direct"

                    # Step ②+③: 简单名匹配 + 继承闭包
                    cands = {t} | _descendants(t, children)
                    for cn in cands:
                        if (cn, callee_name) in method_set:
                            if cn == t:
                                ct = call_type
                            else:
                                ct = "interface_dispatch"
                            key = (cls.name, m.name, cn, callee_name)
                            if key not in seen_edges:
                                seen_edges.add(key)
                                edges.append({
                                    "caller_class": cls.name,
                                    "caller_method": m.name,
                                    "callee_class": cn,
                                    "callee_method": callee_name,
                                    "call_type": ct,
                                    "call_site_file": pf.file_path,
                                    "call_site_line": m.start_line,
                                })
    return edges
