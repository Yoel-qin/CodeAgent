"""解析中间结构：代码（ParsedCodeFile/CodeClass/CodeMethod）、文档（DocElement）、切片规格（ChunkSpec）。

文档多格式（markdown/PDF/Word）统一为 DocElement（Phase 1.5）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ---------------- 代码 ----------------

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


# ---------------- 文档（Phase 1.5 用） ----------------

@dataclass
class DocElement:
    """多格式文档统一中间结构。"""
    type: str                            # HEADING / PARAGRAPH / TABLE / IMAGE / CODE_BLOCK / LIST
    content: str
    heading_path: list[str] = field(default_factory=list)
    heading_level: int | None = None
    page_number: int | None = None
    bbox: dict | None = None
    metadata: dict = field(default_factory=dict)


@dataclass
class ParseMeta:
    """解析器附加元信息（Phase 1.5a）：写入 DocFile 的 file_format/parse_engine/parse_status 等列。"""
    file_format: str                     # markdown / pdf / docx / doc / html / txt
    parse_engine: str                    # markdown / pymupdf / python-docx / text / none
    total_pages: int | None = None
    total_images: int | None = None
    total_tables: int | None = None
    ocr_required: bool = False
    parse_status: str = "COMPLETED"      # COMPLETED / PARTIAL / FAILED
    parse_error: str | None = None


# ---------------- 切片规格（写库前中间结构） ----------------

@dataclass
class CodeChunkSpec:
    """代码 chunk 规格字段（对齐 code_chunks 表）。"""
    chunk_id: str
    file_path: str
    module_name: str | None
    package_name: str | None
    chunk_type: str                      # file / class / method / block
    class_name: str | None
    method_name: str | None
    method_signature: str | None
    access_modifier: str | None
    return_type: str | None
    start_line: int
    end_line: int
    content: str
    content_hash: str
    javadoc: str | None
    inline_comments: list[str]
    annotations: list[str]
    implements_interface: str | None
    extends_class: str | None
    type_parameters: list[str]
    code_anchor_key: str | None
    keywords: list[str]
    token_count: int
    git_commit_hash: str
    calls: list[tuple[str | None, str]]  # 供后续 call_graph 解析（非入库字段，M46 调用对）


@dataclass
class DocChunkSpec:
    """文档 chunk 规格字段（对齐 doc_chunks 表，Phase 1 markdown）。"""
    chunk_id: str
    file_path: str
    heading_path: list[str]
    heading_level: int | None
    section_order: int | None
    content: str
    content_hash: str
    token_count: int
    code_anchors: list[str]
    keywords: list[str]
    git_commit_hash: str
    # Phase 1.5a：PDF/Word 透传（markdown/txt 留默认）
    page_number: int | None = None
    chunk_content_type: str = "text"     # text / code_block / table / table_fragment / image / ...
    # Phase 1.5c：表格结构化（仅 table/table_fragment chunk 填）
    table_data: dict | None = None        # {headers, rows, col_spans, n_rows, n_cols}
    table_html: str | None = None
    table_description: str | None = None
    table_total_rows: int | None = None
    table_total_cols: int | None = None
    is_table_fragment: bool | None = None
    table_fragment_index: int | None = None
    parent_table_chunk_id: str | None = None
    # Phase 1.5b：图片（仅 image chunk 填）
    image_url: str | None = None
    image_thumbnail_url: str | None = None
    image_description: str | None = None
    image_width: int | None = None
    image_height: int | None = None
    image_caption: str | None = None
    context_before: str | None = None
    context_after: str | None = None
