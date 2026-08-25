"""元数据工具：内容哈希、锚点 key、关键词提取、token 估算（设计 §5 / §4.1.3）。"""
from __future__ import annotations

import hashlib
import re

import jieba  # 中文分词（BM25/关键词匹配用）

from app.pipeline.parsing.doc_element import CodeChunkSpec

# 标识符切分：驼峰 + 下划线 + 数字边界
_CAMEL_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")
# 去除常见停用标识符
_STOP = {
    "get", "set", "is", "get", "to", "string", "integer", "int", "long",
    "boolean", "void", "public", "private", "protected", "static", "final",
    "this", "super", "return", "value", "name", "list", "map", "obj",
}
_MIN_TOKEN_LEN = 2


def content_hash(text: str) -> str:
    """内容 SHA256（用于变更检测）。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def short_hash(text: str, n: int = 8) -> str:
    return content_hash(text)[:n]


def make_anchor_key(class_name: str | None, method_name: str | None) -> str | None:
    """代码锚点 key：ClassName.methodName（文档 CODE_ANCHOR 匹配用）。"""
    if not class_name or not method_name:
        return None
    return f"{class_name}.{method_name}"


def split_identifier(ident: str) -> list[str]:
    """把驼峰/下划线标识符拆成词。"""
    if not ident:
        return []
    parts = _CAMEL_RE.findall(ident.replace("_", " "))
    return [p.lower() for p in parts if len(p) >= _MIN_TOKEN_LEN]


def extract_keywords(*, class_name: str | None = None, method_name: str | None = None,
                     identifiers: list[str] | None = None, annotations: list[str] | None = None,
                     max_n: int = 32) -> list[str]:
    """从代码标识符提取关键词（camelCase 拆分 + 去停用词 + 去重）。

    BM25 混合检索 + 语义增强用；对代码场景用标识符而非通用分词。
    """
    seen: dict[str, None] = {}
    sources: list[str] = []
    if class_name:
        sources.append(class_name)
    if method_name:
        sources.append(method_name)
    if annotations:
        sources.extend(annotations)
    for ident in identifiers or []:
        sources.append(ident)

    for ident in sources:
        for tok in split_identifier(ident):
            if tok in _STOP:
                continue
            seen.setdefault(tok, None)
            if len(seen) >= max_n:
                return list(seen.keys())
    return list(seen.keys())


_DOC_STOP = {
    "the", "a", "an", "of", "to", "in", "on", "and", "or", "for", "is", "are",
    "with", "as", "by", "at", "be", "this", "that", "from", "it", "we", "you",
    "的", "了", "是", "在", "和", "与", "为", "以", "及", "或", "等", "可", "并",
    "一", "个", "这", "那", "也", "都", "不", "无", "有",
}
_DOC_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]+|[一-鿿]+")


def extract_doc_keywords(*texts: str, max_n: int = 32) -> list[str]:
    """文档关键词：英文标识符按词、中文用 jieba 分词（BM25/混合检索粗信号）。"""
    seen: dict[str, None] = {}
    for text in texts:
        if not text:
            continue
        for tok in _DOC_TOKEN_RE.findall(text):
            if tok[0].isascii():
                t = tok.lower()
                if len(t) >= 2 and t not in _DOC_STOP:
                    seen.setdefault(t, None)
            else:
                # CJK 连续段 → jieba 分词
                for w in jieba.cut(tok):
                    w = w.strip()
                    if len(w) >= 2 and w not in _DOC_STOP:
                        seen.setdefault(w, None)
            if len(seen) >= max_n:
                return list(seen.keys())
    return list(seen.keys())


def approx_token_count(text: str) -> int:
    """粗略 token 估算（无 tiktoken 依赖）：英文 ~4 字符/token，中文按字算。

    仅用于切片大小控制（设计 §4.1.3），精度够用即可。
    """
    # 简单策略：按非空白 token 数 ×1.3 近似，再与字符数/4 取大
    words = len(text.split())
    by_char = len(text) // 4
    return max(int(words * 1.3), by_char, 1)


# ---------------------------------------------------------------------------
# M31：代码源文本中的中文注释抽取（ES chinese_comment 字段用，见 spec §3.5）
# ---------------------------------------------------------------------------
_BLOCK_COMMENT_RE = re.compile(r"/\*+.*?\*+/", re.DOTALL)
_LINE_COMMENT_RE = re.compile(r"//[^\n]*")
_CJK_RE = re.compile(r"[一-鿿]")  # CJK 统一表意文字（一-鿿）


def extract_chinese_comment(source: str, max_chars: int = 2000) -> str:
    """抽取源文本注释段（行注释 + 块注释/javadoc）中**含 CJK** 的行，\n 连接、截断。

    供 ES ``chinese_comment`` 字段用——中文注释独立成字段后 IK 分词 + 检索期 boost 2.0，
    修复「javadoc 根本不进 ES」缺口（M31 spec §1.3/§3.5）。doc chunk 不调用
    （其 content 本身即中文，IK 直接受益）。纯函数，代码侧与 rebuild 侧同源。
    """
    if not source:
        return ""
    segments = _BLOCK_COMMENT_RE.findall(source) + _LINE_COMMENT_RE.findall(source)
    lines: list[str] = []
    for seg in segments:
        for ln in seg.splitlines():
            if _CJK_RE.search(ln):
                lines.append(ln.strip())
    return "\n".join(lines)[:max_chars]


# ---------------------------------------------------------------------------
# M32 ①a：规则注释增强（COMMENT_ENHANCE_ENABLED=on；纯函数，无 I/O）
# ---------------------------------------------------------------------------

def enhance_code_chunk(spec) -> CodeChunkSpec:
    """注释增强：① block/class chunk 补 javadoc 前缀（这两类 content 丢了 javadoc——
    块级切分只留签名前缀、无方法类级 chunk 是占位注释）；② keywords ∪ 注释词
    （extract_doc_keywords，jieba 中文）——修「中文查询词在 PG 词法路 ``keywords ?|``
    永远匹配不到 code chunk」缺口；③ content 变更时 content_hash/token_count 重算 +
    chunk_id 尾短哈希替换（四种 chunk_id 模板的短哈希均在末尾；code_anchor_key 不动
    → 锚点稳定）。method/file chunk 的 content 不改（M46 后 method source 已含 javadoc）。
    """
    doc_bits = [spec.javadoc] + list(spec.inline_comments or [])
    doc_text = "\n".join(t for t in doc_bits if t)

    new_content = spec.content
    if spec.javadoc and spec.chunk_type in ("block", "class"):
        new_content = spec.javadoc + "\n" + spec.content

    new_keywords = list(spec.keywords or [])
    if doc_text:
        seen = {k.lower() for k in new_keywords}
        for tok in extract_doc_keywords(doc_text, max_n=32):
            if tok.lower() not in seen:
                new_keywords.append(tok)
                seen.add(tok.lower())
    new_keywords = new_keywords[:32]

    if new_content != spec.content:
        old_h = short_hash(spec.content)
        parts = spec.chunk_id.rsplit(old_h, 1)
        spec.chunk_id = parts[0] + short_hash(new_content) if len(parts) == 2 else spec.chunk_id
        spec.content = new_content
        spec.content_hash = content_hash(new_content)
        spec.token_count = approx_token_count(new_content)
    spec.keywords = new_keywords
    return spec
