"""工具结果格式化：把候选 chunk / 图谱响应压成给 LLM 看的紧凑中文文本。

文本里显式带 chunk_id / center id，便于 LLM 把结果喂给下一步工具（如 get_call_chain）。
"""
from __future__ import annotations

from app.schemas.graph import GraphNode, GraphResponse, GraphSearchItem

_MAX_SNIPPET = 600
#: format_impact_callers 每层最多列出几个调用方（防广泛被调方法 120 节点爆 token；其余仍作引用）
_MAX_PER_DEPTH = 8


def code_label(c: dict) -> str:
    cls = c.get("class_name")
    meth = c.get("method_name")
    if cls and meth:
        return f"{cls}.{meth}"
    if cls:
        return str(cls)
    return str(c.get("chunk_id", "?"))


def format_code_candidates(chunks: list[dict]) -> str:
    if not chunks:
        return "（未检索到相关代码片段）"
    lines = [f"命中 {len(chunks)} 个代码片段："]
    for i, c in enumerate(chunks, 1):
        lines.append(f"{i}. {code_label(c)}  [chunk_id={c.get('chunk_id')}]")
        snip = (c.get("content") or "").strip()
        if snip:
            lines.append(f"   {snip[:_MAX_SNIPPET]}")
    return "\n".join(lines)


def format_code_detail(row: dict) -> str:
    """read_code 的详细输出：签名 + 位置 + javadoc + 源码。"""
    loc = f"{row.get('file_path') or '?'}:{row.get('start_line')}-{row.get('end_line')}"
    head = f"{row.get('class_name') or ''}.{row.get('method_name') or row.get('chunk_id')}"
    sig = row.get("method_signature")
    parts = [f"{head}  @ {loc}", f"[chunk_id={row.get('chunk_id')}]"]
    if sig:
        parts.append(f"签名：{sig}")
    jd = (row.get("javadoc") or "").strip()
    if jd:
        parts.append(f"Javadoc：{jd[:_MAX_SNIPPET]}")
    parts.append("源码：")
    parts.append((row.get("content") or "").strip())
    return "\n".join(parts)


def format_symbol_search(items: list[GraphSearchItem]) -> str:
    if not items:
        return "（未找到匹配的类/方法）"
    lines = ["匹配符号："]
    for it in items:
        lines.append(f"- {it.name} ({it.type})  id={it.id}  module={it.module or '?'}")
    return "\n".join(lines)


def format_call_graph(resp: GraphResponse, direction: str) -> str:
    if not resp.nodes:
        return f"（{resp.center} 周围无调用关系）"
    name_of = {n.id: n.name for n in resp.nodes}
    role = {"CALLERS": "上游调用者", "CALLEES": "下游被调用", "BOTH": "调用关系"}.get(direction, "调用关系")
    lines = [f"中心 {resp.center} 的{role}（共 {len(resp.nodes)} 个方法）："]
    for e in resp.edges:
        lines.append(f"  {name_of.get(e.source, e.source)} ──调用──▶ {name_of.get(e.target, e.target)}")
    if not resp.edges:
        for n in resp.nodes:
            lines.append(f"  {n.name}  [chunk_id={n.id}]")
    return "\n".join(lines)


def format_impact_callers(resp: GraphResponse) -> str:
    """get_callers 的输出：上游影响面按 BFS 层归类，紧凑（控制 token，防广泛被调方法爆量）。

    depth=0 为修改对象（目标本身），depth≥1 为受影响的上游调用方（按层 = 直接/间接）。
    """
    nodes = [n for n in resp.nodes if not str(n.id).startswith("class:")]
    if not nodes:
        return f"（{resp.center} 周围无上游调用关系，修改它不波及其他代码）"
    by_depth: dict[int, list[GraphNode]] = {}
    classes: set[str] = set()
    for n in nodes:
        by_depth.setdefault(n.depth or 0, []).append(n)
        if n.class_name:
            classes.add(n.class_name)
    callers = [n for n in nodes if (n.depth or 0) >= 1]
    lines = [f"中心 {resp.center} 的调用关系（共 {len(nodes)} 个，按距目标的层数归类）："]
    for d in sorted(by_depth):
        tag = "修改对象（目标本身）" if d == 0 else ("直接调用方（受直接影响）" if d == 1 else f"第 {d} 层间接调用方")
        grp = by_depth[d]
        lines.append(f"【{tag}】")
        for n in grp[:_MAX_PER_DEPTH]:
            lines.append(f"  - {n.name}  [chunk_id={n.id}]")
        if len(grp) > _MAX_PER_DEPTH:
            lines.append(f"  …（本层还有 {len(grp) - _MAX_PER_DEPTH} 个，已并入引用）")
    lines.append(f"受影响上游调用方 {len(callers)} 个；涉及 {len(classes)} 个类"
                 + (f"：{', '.join(sorted(classes))}" if classes else ""))
    return "\n".join(lines)


def format_related_docs(resp: GraphResponse) -> str:
    doc_nodes = [n for n in resp.nodes if n.type == "doc"]
    if not doc_nodes:
        return "（未找到关联文档）"
    lines = ["关联文档："]
    for n in doc_nodes:
        hp = " > ".join(n.heading_path) if n.heading_path else n.name
        lines.append(f"- {hp}  [chunk_id={n.id}]")
    return "\n".join(lines)


def format_change_history(rows: list[dict], chunk_id: str) -> str:
    """get_recent_changes 的输出：某 chunk 最近的 git 变更记录（类型/提交/作者/信息）。

    rows 来自 change_history 表（已按 git_commit_time DESC 取最多 10 条）。空 → 提示无历史
    （全量入库、未经增量同步的代码无记录）。截断 commit_message 控 token。
    """
    if not rows:
        return f"（chunk {chunk_id} 无已记录的变更历史；可能尚未经过增量同步）"
    lines = [f"近期变更（最近 {len(rows)} 次）："]
    for r in rows:
        h = str(r.get("git_commit_hash") or "")[:8]
        t = r.get("git_commit_time")
        ts = t.strftime("%Y-%m-%d") if hasattr(t, "strftime") else str(t or "")[:10]
        author = r.get("git_author") or "?"
        msg = (r.get("commit_message") or "").strip().replace("\n", " ")[:120]
        lines.append(f"- [{r.get('change_type')}] {ts} {h} by {author}\n  {msg}")
    return "\n".join(lines)


def format_code_metrics(m: dict) -> str:
    """get_code_metrics 的输出：代码度量（LOC/token/fan-in/fan-out）+ 温和阈值提示。

    供代码审查 Agent 引用客观数字佐证复杂度/影响面判断。缺失 chunk → 提示未找到。
    """
    if not m.get("found"):
        return f"（未找到 chunk_id={m.get('chunk_id')} 的代码，无法度量）"
    loc = m.get("loc", 0)
    fan_in = m.get("fan_in", 0)
    fan_out = m.get("fan_out", 0)
    head = f"{m.get('class_name') or ''}.{m.get('method_name') or m.get('chunk_id')}"
    lines = [f"{head} 度量：LOC={loc}，fan-in={fan_in}（被调用），fan-out={fan_out}（调用他人）"]
    tc = m.get("token_count")
    if tc is not None:
        lines.append(f"token≈{tc}")
    flags = []
    if loc > 120:
        flags.append("方法偏长，建议拆分")
    if fan_in > 15:
        flags.append("被广泛调用，改动需谨慎")
    if fan_out > 10:
        flags.append("依赖较多，耦合偏高")
    if flags:
        lines.append("提示：" + "；".join(flags))
    sig = m.get("method_signature")
    if sig:
        lines.append(f"签名：{sig}")
    return "\n".join(lines)


def format_existing_tests(rows: list[dict], class_name: str) -> str:
    """get_existing_tests 的输出：某类的现有测试类/方法（供测试生成 Agent 对齐项目测试约定）。

    rows 来自 code_chunks（class_name ILIKE '{Class}%Test'，最多 8 条）。空 → 提示无现有测试，
    生成时按 JUnit 5 + Mockito 通用约定（全量入库的样本库常无测试类，优雅降级）。
    """
    if not rows:
        return (f"（未找到 {class_name} 的现有测试；将按 JUnit 5 + Mockito 通用约定生成单元测试）")
    lines = [f"找到 {len(rows)} 个 {class_name} 的现有测试（供参考测试约定：框架/命名/断言/mock 风格）："]
    for r in rows:
        label = code_label(r)
        lines.append(f"- {label}  [chunk_id={r.get('chunk_id')}]")
        snip = (r.get("content") or "").strip()
        if snip:
            lines.append(f"   {snip[:_MAX_SNIPPET]}")
    return "\n".join(lines)


def format_stale_candidates(rows: list[dict], center: str) -> str:
    """detect_stale_docs 的输出：center 的文档↔代码锚点候选 + staleness 证据（供文档维护 Agent 判断是否过时）。

    每行含 relation_id（提交提案时用）、code↔doc 两侧标签、code 侧最近变更（无变更记录则提示——
    全量入库、未经增量同步的代码无 change_history）。空 → 提示无锚点。
    """
    if not rows:
        return f"（未找到 {center} 的文档-代码锚点关系；该代码可能未被任何文档锚定）"
    lines = [f"找到 {len(rows)} 个 {center} 的文档-代码锚点（附 staleness 证据，供判断文档是否过时）："]
    for r in rows:
        lines.append(f"- {r.get('anchor_key') or r.get('code_label')}  [relation_id={r.get('relation_id')}]")
        lines.append(f"   代码：{r.get('code_label')}  ↔  文档：{r.get('doc_heading')}")
        lc = r.get("last_change")
        if lc:
            t = lc.get("git_commit_time")
            ts = t.strftime("%Y-%m-%d") if hasattr(t, "strftime") else str(t or "")[:10]
            msg = (lc.get("commit_message") or "").strip().replace("\n", " ")[:80]
            lines.append(f"   代码最近变更：[{lc.get('change_type')}] {ts} {msg}")
        else:
            lines.append("   代码无变更记录（未经过增量同步，无法判断是否近期改动）")
    return "\n".join(lines)


# ---- 文档问答 Agent 用 ----


def _heading(hp) -> str:
    """heading_path（JSONB list）压成 `a › b › c` 面包屑；空则占位。"""
    if hp:
        return " › ".join(str(h) for h in hp)
    return "（无章节）"


def format_doc_candidates(docs: list[dict]) -> str:
    """search_docs 的候选列表：章节面包屑 + chunk_id + 片段。"""
    if not docs:
        return "（未检索到相关文档段落）"
    lines = [f"命中 {len(docs)} 个文档段落："]
    for i, d in enumerate(docs, 1):
        lines.append(f"{i}. {_heading(d.get('heading_path'))}  [chunk_id={d.get('chunk_id')}]")
        snip = (d.get("content") or "").strip()
        if snip:
            lines.append(f"   {snip[:_MAX_SNIPPET]}")
    return "\n".join(lines)


def format_doc_detail(row: dict) -> str:
    """read_doc 的详细输出：章节面包屑 + 出处 + 类型 + 全文（表格/图片附结构化说明）。"""
    parts = [_heading(row.get("heading_path")), f"[chunk_id={row.get('chunk_id')}]"]
    src = row.get("title") or row.get("file_path")
    if src:
        parts.append(f"出处：{src}")
    ctype = row.get("chunk_content_type")
    if ctype and ctype != "text":
        parts.append(f"类型：{ctype}")
    if ctype == "table":
        dim = f"{row.get('table_total_rows') or '?'}×{row.get('table_total_cols') or '?'}"
        td = (row.get("table_description") or "").strip()
        parts.append(f"表格 {dim}" + (f"：{td[:_MAX_SNIPPET]}" if td else ""))
    elif ctype == "image":
        desc = (row.get("image_description") or "").strip()
        if desc:
            parts.append(f"图片描述：{desc[:_MAX_SNIPPET]}")
    parts.append("内容：")
    parts.append((row.get("content") or "").strip())
    return "\n".join(parts)


def format_related_code(resp: GraphResponse) -> str:
    """get_related_code 的输出：从文档段拉出的关联代码节点（作佐证）。"""
    code_nodes = [n for n in resp.nodes if n.type != "doc"]
    if not code_nodes:
        return "（未找到关联代码）"
    lines = [f"关联代码（共 {len(code_nodes)} 个）："]
    for n in code_nodes:
        lines.append(f"- {n.name}  [chunk_id={n.id}]")
    return "\n".join(lines)


# ---- M26 新增工具用 ----


def format_impact_callees(resp: GraphResponse) -> str:
    """get_downstream_callers 的输出：下游被调用方（center 依赖谁）按 BFS 层归类。

    与 ``format_impact_callers`` 同构（上游影响面），但语义为「它调用了什么」（下游依赖），
    供变更影响 Agent 补全下游视角。
    """
    nodes = [n for n in resp.nodes if not str(n.id).startswith("class:")]
    if not nodes:
        return f"（{resp.center} 无下游被调用关系，它不依赖其他方法）"
    by_depth: dict[int, list[GraphNode]] = {}
    classes: set[str] = set()
    for n in nodes:
        by_depth.setdefault(n.depth or 0, []).append(n)
        if n.class_name:
            classes.add(n.class_name)
    callees = [n for n in nodes if (n.depth or 0) >= 1]
    lines = [f"中心 {resp.center} 的下游被调用（共 {len(nodes)} 个，按距目标的层数归类）："]
    for d in sorted(by_depth):
        tag = "修改对象（目标本身）" if d == 0 else ("直接依赖（它调用的）" if d == 1 else f"第 {d} 层间接依赖")
        grp = by_depth[d]
        lines.append(f"【{tag}】")
        for n in grp[:_MAX_PER_DEPTH]:
            lines.append(f"  - {n.name}  [chunk_id={n.id}]")
        if len(grp) > _MAX_PER_DEPTH:
            lines.append(f"  …（本层还有 {len(grp) - _MAX_PER_DEPTH} 个，已并入引用）")
    lines.append(f"下游依赖 {len(callees)} 个；涉及 {len(classes)} 个类"
                 + (f"：{', '.join(sorted(classes))}" if classes else ""))
    return "\n".join(lines)


def format_media_search(rows: list[dict], media_type: str) -> str:
    """image_search/table_search 的输出：按描述命中的图片/表格文档段。"""
    label = "图片" if media_type == "image" else "表格"
    if not rows:
        return f"（未检索到匹配的{label}）"
    lines = [f"命中 {len(rows)} 个{label}文档段："]
    for r in rows:
        lines.append(f"- {_heading(r.get('heading_path'))}  [chunk_id={r.get('chunk_id')}]")
        desc = (r.get("description") or "").strip()
        if desc:
            lines.append(f"   {label}描述：{desc[:_MAX_SNIPPET]}")
    return "\n".join(lines)


def format_affected_docs(rows: list[dict], center: str) -> str:
    """get_affected_docs 的输出：锚定到 center 代码的文档段（改该代码可能需更新这些文档）。

    每条附代码最近变更作腐化信号（对接文档维护弧线）：有变更 → 提示可能过时；无记录 → 提示
    未增量同步。空 → 该代码未被任何文档锚定。
    """
    if not rows:
        return f"（未找到锚定 {center} 的文档；该代码可能未被任何文档引用）"
    lines = [f"找到 {len(rows)} 个锚定该代码的文档段（改代码时需同步检查/更新）："]
    for r in rows:
        lines.append(f"- {_heading(r.get('heading_path'))}  [chunk_id={r.get('chunk_id')}]")
        lc = r.get("last_change")
        if lc:
            t = lc.get("git_commit_time")
            ts = t.strftime("%Y-%m-%d") if hasattr(t, "strftime") else str(t or "")[:10]
            lines.append(f"   代码最近变更：[{lc.get('change_type')}] {ts}（文档可能已过时）")
        else:
            lines.append("   代码无变更记录（未经过增量同步，无法判断是否近期改动）")
    return "\n".join(lines)


def format_rerank(rows: list[dict]) -> str:
    """rerank 的输出：按相关性重排后的候选（chunk_id + 分数），供 Agent 聚焦最相关的。"""
    if not rows:
        return "（无候选可重排）"
    lines = [f"重排 {len(rows)} 个候选（按相关性降序）："]
    for i, r in enumerate(rows, 1):
        cls = r.get("class_name")
        meth = r.get("method_name")
        name = f"{cls}.{meth}" if cls and meth else r.get("chunk_id", "?")
        lines.append(f"{i}. {name}  [chunk_id={r.get('chunk_id')}]  score={float(r.get('score') or 0):.3f}")
    return "\n".join(lines)
