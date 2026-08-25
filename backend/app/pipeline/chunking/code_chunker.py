"""代码切片（设计 §4.1）：
- 文件 < SMALL_FILE_LINES 行 → 整个文件一个 chunk（避免碎片化）
- 否则方法级切片，每个方法（含 Javadoc + 上下文前缀）一个 chunk
- 超长方法（token > MAX_TOKENS）按逻辑块二次切分（签名前缀 + 重叠）
"""
from __future__ import annotations

import re

from app.core.config import settings
from app.pipeline.metadata import (
    approx_token_count,
    content_hash,
    enhance_code_chunk,
    extract_keywords,
    make_anchor_key,
    short_hash,
)
from app.pipeline.parsing.doc_element import CodeChunkSpec, CodeMethod, ParsedCodeFile

SMALL_FILE_LINES = 200
MAX_TOKENS = 1024
MIN_TOKENS = 50
OVERLAP_LINES = 16
_ACCESS_MODS = {"public", "private", "protected"}

_SAFE_RE = re.compile(r"[^A-Za-z0-9]+")


def _safe(s: str) -> str:
    return _SAFE_RE.sub("", s) or "x"


def _access(modifiers: list[str]) -> str | None:
    for m in modifiers:
        if m in _ACCESS_MODS:
            return m
    return None


def _class_header(pf: ParsedCodeFile, cls_name: str, implements: list[str], extends: str | None) -> str:
    bits = [f"// file: {pf.file_path}", f"// class: {cls_name}"]
    if extends:
        bits.append(f"// extends: {extends}")
    if implements:
        bits.append(f"// implements: {', '.join(implements)}")
    return "\n".join(bits) + "\n"


def _method_chunk(pf: ParsedCodeFile, cls_name: str, cls_implements: list[str],
                  cls_extends: str | None, m: CodeMethod, commit_hash: str,
                  *, force_block: bool = False) -> list[CodeChunkSpec]:
    header = _class_header(pf, cls_name, cls_implements, cls_extends)
    full = header + m.source
    base = _build_method_spec(pf, cls_name, cls_implements, cls_extends, m, commit_hash, full)

    # 超长方法二次切分（按行窗口 + 重叠，每块带签名前缀）
    if force_block or approx_token_count(full) > MAX_TOKENS:
        blocks = _split_into_blocks(m, header)
        if len(blocks) > 1:
            specs: list[CodeChunkSpec] = []
            for idx, blk in enumerate(blocks):
                cid = f"code_{_safe(cls_name)}_{_safe(m.name)}_{idx}_{short_hash(blk)}"
                specs.append(_build_method_spec(
                    pf, cls_name, cls_implements, cls_extends, m, commit_hash, blk,
                    chunk_type="block", chunk_id=cid,
                ))
            return specs
    return [base]


def _build_method_spec(pf: ParsedCodeFile, cls_name: str, cls_implements: list[str],
                       cls_extends: str | None, m: CodeMethod, commit_hash: str,
                       content: str, *, chunk_type: str = "method",
                       chunk_id: str | None = None) -> CodeChunkSpec:
    if chunk_id is None:
        chunk_id = f"code_{_safe(cls_name)}_{_safe(m.name)}_{short_hash(content)}"
    # M46 防御：列宽 String(512)——超长签名/锚键截断（MQClientAPIImpl.sendMessage 524 字符实锤）
    sig512 = (m.signature or "")[:512]
    anchor512 = (make_anchor_key(cls_name, m.name) or "")[:512]
    # M46：m.calls 是 (receiver, 方法名) 对——receiver 变量名无语义价值，只取方法名进 keywords
    identifiers = [name for _, name in m.calls] + list(m.parameters) + list(m.annotations)
    return CodeChunkSpec(
        chunk_id=chunk_id,
        file_path=pf.file_path,
        module_name=pf.module_name,
        package_name=pf.package,
        chunk_type=chunk_type,
        class_name=cls_name,
        method_name=m.name,
        method_signature=sig512,
        access_modifier=_access(m.modifiers),
        return_type=m.return_type,
        start_line=m.start_line,
        end_line=m.end_line,
        content=content,
        content_hash=content_hash(content),
        javadoc=m.javadoc,
        inline_comments=[],
        annotations=m.annotations,
        implements_interface=",".join(cls_implements) or None,
        extends_class=cls_extends,
        type_parameters=[],
        code_anchor_key=anchor512,
        keywords=extract_keywords(
            class_name=cls_name, method_name=m.name,
            identifiers=identifiers, annotations=m.annotations,
        ),
        token_count=approx_token_count(content),
        git_commit_hash=commit_hash,
        calls=m.calls,
    )


def _split_into_blocks(m: CodeMethod, header: str) -> list[str]:
    """按行窗口把超长方法切成块（含签名前缀 + 重叠）。"""
    lines = m.source.splitlines()
    sig_line = lines[0] if lines else m.signature
    # 找到方法体起始 '{' 之后
    body_start = 0
    for i, ln in enumerate(lines):
        if "{" in ln:
            body_start = i + 1
            break
    body = lines[body_start:]
    if not body:
        return [header + m.source]
    blocks: list[str] = []
    step = max(40, MAX_TOKENS // 4)  # 粗略：每块 ~40 行
    i = 0
    n = len(body)
    while i < n:
        window = body[i:i + step]
        blk = header + sig_line + "\n    // ... (block {}/{})\n    ".format("", "") + "\n    ".join(window) + "\n"
        blocks.append(blk)
        if i + step >= n:
            break
        i += step - OVERLAP_LINES
    return blocks


def chunk_code_file(pf: ParsedCodeFile, *, commit_hash: str | None = None,
                    small_file_lines: int = SMALL_FILE_LINES) -> list[CodeChunkSpec]:
    """把 ParsedCodeFile 切成 CodeChunkSpec 列表。"""
    ch = commit_hash or pf.commit_hash or "UNKNOWN"

    # 小文件 → 整个文件一个 chunk
    if pf.total_lines < small_file_lines:
        content = pf.source
        # 取第一个类型名作为 class_name（若有）
        cls_name = pf.classes[0].name if pf.classes else None
        return [CodeChunkSpec(
            chunk_id=f"code_{_safe(_filename_stem(pf.file_path))}_{short_hash(content)}",
            file_path=pf.file_path,
            module_name=pf.module_name,
            package_name=pf.package,
            chunk_type="file",
            class_name=cls_name,
            method_name=None,
            method_signature=None,
            access_modifier=None,
            return_type=None,
            start_line=1,
            end_line=pf.total_lines,
            content=content,
            content_hash=content_hash(content),
            javadoc=None,
            inline_comments=[],
            annotations=[],
            implements_interface=None,
            extends_class=None,
            type_parameters=[],
            code_anchor_key=None,
            keywords=extract_keywords(class_name=cls_name, identifiers=[pf.package or ""]),
            token_count=approx_token_count(content),
            git_commit_hash=ch,
            calls=[],
        )]

    # 方法级切片
    specs: list[CodeChunkSpec] = []
    for cls in pf.classes:
        if not cls.methods:
            # 无方法的类（常量/工具）→ 类级 chunk
            content = _class_header(pf, cls.name, cls.interfaces, cls.superclass) + \
                "// (no methods; class-level chunk)\n"
            specs.append(CodeChunkSpec(
                chunk_id=f"code_{_safe(cls.name)}_{short_hash(content)}",
                file_path=pf.file_path, module_name=pf.module_name, package_name=pf.package,
                chunk_type="class", class_name=cls.name, method_name=None,
                method_signature=None, access_modifier=_access(cls.modifiers), return_type=None,
                start_line=cls.start_line, end_line=cls.end_line, content=content,
                content_hash=content_hash(content), javadoc=cls.javadoc, inline_comments=[],
                annotations=cls.annotations, implements_interface=",".join(cls.interfaces) or None,
                extends_class=cls.superclass, type_parameters=[], code_anchor_key=None,
                keywords=extract_keywords(class_name=cls.name, identifiers=cls.interfaces),
                token_count=approx_token_count(content), git_commit_hash=ch, calls=[],
            ))
            continue
        for m in cls.methods:
            specs.extend(_method_chunk(pf, cls.name, cls.interfaces, cls.superclass, m, ch))
    # M32 ①a：规则注释增强（默认 off；content 变更会改 chunk_id，必须在 _dedup_ids 之前）
    if settings.comment_enhance_enabled:
        specs = [enhance_code_chunk(s) for s in specs]
    return _dedup_ids(specs)


def _dedup_ids(specs: list[CodeChunkSpec]) -> list[CodeChunkSpec]:
    """M46 兜底：同文件内极端同 content（如两个真空方法体 + 相同 javadoc）仍会同 chunk_id
    → 追加 _r{n} 后缀消歧，避免同批 INSERT 撞 pk_code_chunks。"""
    used: dict[str, int] = {}
    for s in specs:
        cid = s.chunk_id
        n = used.get(cid, 0)
        if n:
            k, new = n, f"{cid}_r{n}"
            while new in used:
                k += 1
                new = f"{cid}_r{k}"
            s.chunk_id = new
        used[s.chunk_id] = used.get(s.chunk_id, 0) + 1
    return specs


def _filename_stem(path: str) -> str:
    name = path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    return name.rsplit(".", 1)[0]
