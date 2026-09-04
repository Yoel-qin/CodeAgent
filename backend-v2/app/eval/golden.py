"""Golden set 加载与锚点解析（M8，纯函数；IO 仅 load_golden_set 读 YAML 文件）。

锚点语法（冻结）：
- code：``"Class.method"`` 或 ``"Class"``（类实体）——运行时经 ``code_entities`` 解析为
  ``(file_path, start_line, end_line)`` 目标集；同名多实体（重载/内部类）= 多个可接受
  目标，命中任一即算命中。
- doc：``"doc_name#anchor"``——anchor 是 ingest 期生成的 slug（heading_path 各段 slug 化
  后 "/" join，见 pipeline/chunking/doc_sections.py）。

spec §8.2 偏差（计划内决策）：spec 写 ``file:symbol`` / ``doc_id§section``——golden 用
``Class.method`` 类限定符**运行时解析**为行区间，比手写全路径+行号更耐文件移动/行漂移
（旧库 eval_set.yaml 的 Class.method 运行时解析同模式）；file_path 参与精确匹配，但来自
code_entities 解析结果而非手写。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import yaml


@dataclass(frozen=True)
class CodeTarget:
    """一个可接受的代码目标（code_entities 一行投影）。"""

    file_path: str
    start_line: int
    end_line: int


@dataclass(frozen=True)
class DocTarget:
    """一个可接受的文档目标（documents ⋈ doc_sections 一行投影）。"""

    doc_name: str
    anchor: str


@dataclass
class GoldenCase:
    """一条评测用例：query + 期望锚点（两类各自可空但不能全空）。"""

    id: str
    query: str
    repo: str
    expect_code: list[str] = field(default_factory=list)
    expect_doc: list[str] = field(default_factory=list)


def load_golden_set(path: str) -> tuple[str, list[GoldenCase]]:
    """读 golden YAML → (文件级默认 repo, cases)。

    结构（冻结）：顶层 ``repo`` + ``cases: [{id, query, repo?, expect: {code?: [...],
    doc?: [...]}}]``；case 级 ``repo`` 覆盖文件级。id 重复 / cases 为空 / expect 两列表
    全空 → ``ValueError``（坏集合在加载期暴露，不流到跑批中途）。
    """
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    default_repo = str(data.get("repo") or "")
    raw_cases = data.get("cases") or []
    if not raw_cases:
        raise ValueError(f"golden set {path!r} 无 cases")
    cases: list[GoldenCase] = []
    seen: set[str] = set()
    for item in raw_cases:
        cid = str(item.get("id") or "").strip()
        if cid in seen:
            raise ValueError(f"golden set {path!r} case id 重复: {cid!r}")
        seen.add(cid)
        expect = item.get("expect") or {}
        expect_code = [str(s) for s in expect.get("code") or []]
        expect_doc = [str(s) for s in expect.get("doc") or []]
        if not expect_code and not expect_doc:
            raise ValueError(f"case {cid!r} expect.code/doc 全空——无锚点无法评分")
        cases.append(GoldenCase(
            id=cid, query=str(item.get("query") or ""),
            repo=str(item.get("repo") or default_repo),
            expect_code=expect_code, expect_doc=expect_doc,
        ))
    return default_repo, cases


def parse_code_spec(spec: str) -> tuple[str, str | None]:
    """``"Class.method"`` → (Class, method)；``"Class"`` → (Class, None)。

    超过一段的方法名含点场景不支持（Java 方法名无点，内部类按 ``Outer`` 类目标匹配即可）。
    """
    parts = spec.strip().split(".", 1)
    if len(parts) == 2 and parts[1]:
        return parts[0], parts[1]
    return parts[0], None


def resolve_code_targets(rows: list[dict], spec: str) -> list[CodeTarget]:
    """rows（code_entities 投影）内找 spec 的全部同名实体 → 目标集。

    method 为 None 的 spec 只匹配 ``method_name IS NULL`` 的类实体；行内 ``end_line``
    缺 → 回落 ``start_line``（read_file 引用单行区间同理可命中）。
    """
    cls, method = parse_code_spec(spec)
    out: list[CodeTarget] = []
    for r in rows:
        if r.get("class_name") != cls:
            continue
        row_method = r.get("method_name")
        if method is None:
            if row_method is not None:
                continue
        elif row_method != method:
            continue
        start = r.get("start_line")
        if not isinstance(start, int):
            continue
        end = r.get("end_line")
        out.append(CodeTarget(file_path=str(r.get("file_path")), start_line=start,
                              end_line=end if isinstance(end, int) else start))
    return out


def resolve_doc_targets(rows: list[dict], spec: str) -> list[DocTarget]:
    """rows（{doc_name, anchor} 投影）内精确匹配 ``doc_name#anchor``。"""
    doc_name, _, anchor = spec.strip().partition("#")
    if not doc_name or not anchor:
        return []
    return [DocTarget(doc_name=r["doc_name"], anchor=r["anchor"])
            for r in rows
            if r.get("doc_name") == doc_name and r.get("anchor") == anchor]
