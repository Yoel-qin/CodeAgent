"""回答幻觉校验（M34）：提取回答里的代码标识符，对照检索 citation 元数据，
把未验证的标识符在文末标注「未验证」。纯函数——无 LLM / 无 DB / 无网络。

由 ``stream_graph`` 跑完主图后调用（覆盖 retrieve→generate 与所有 scenario-agent 路径：
适配器为全路径累积了 answer + citations；agent 节点本身不写图 state）。
opt-in（``CITATION_ENFORCE_ENABLED``，默认 off）；关 = 零行为变更。只标注，不重生成（YAGNI）。
"""
from __future__ import annotations

import re

# 代码标识符形状：
# 1) dotted 限定：com.foo.Bar / Bar.method / Account.getBalance（句末句点不匹配：点后须跟标识符）
_DOTTED = re.compile(r"[A-Za-z_][\w]*(?:\.[A-Za-z_][\w]+)+")
# 2) PascalCase 类名：Account / RocketMQ（再过 stopword）
_PASCAL = re.compile(r"\b[A-Z][a-zA-Z0-9_]*\b")
# 3) 方法调用：getBalance( / sendMessage (
_METHOD = re.compile(r"[a-z][a-zA-Z0-9_]*\s*\(")
# 4) camelCase 方法名（无括号，纯文本中提及，须含内部大写以避开散文词）：getBalance / sendMessage
_CAMEL_METHOD = re.compile(r"\b[a-z][a-zA-Z0-9_]*[A-Z][a-zA-Z0-9_]*\b")

# stopword / 常见词：英文功能词 + 常见框架/通用技术词，砍假阳（非穷尽，可按 M34 评测迭代）。
_STOPWORDS = frozenset(w.lower() for w in {
    # 英文功能词 / 关键字
    "the", "a", "an", "this", "that", "these", "those", "with", "without",
    "and", "or", "not", "but", "if", "else", "for", "while", "return", "from",
    "import", "class", "interface", "void", "int", "long", "double", "boolean",
    "public", "private", "protected", "static", "final", "new", "null", "true",
    "false", "throws", "throw", "try", "catch", "package", "extends", "implements",
    # 常见框架 / 通用技术 PascalCase 词（非本项目标识符）
    "Spring", "Config", "Configuration", "Service", "Controller", "Component",
    "Repository", "Manager", "Factory", "Builder", "Exception", "Error",
    "Logger", "Test", "Tests", "Get", "Set", "List", "Map", "String",
    "Integer", "Object", "System", "Math", "Array", "Arrays", "Collection",
})


def extract_identifiers(answer: str) -> list[str]:
    """从回答文本抓 Java 代码形标识符（dotted / PascalCase / 方法调用），去重保序，过 stopword。"""
    raw: list[str] = []
    raw.extend(_DOTTED.findall(answer))
    raw.extend(_PASCAL.findall(answer))
    # 方法调用匹配带末尾「(」与空白，剥成纯名字
    raw.extend(m.rstrip("( ") for m in _METHOD.findall(answer))
    # camelCase 方法名（纯文本提及）
    raw.extend(_CAMEL_METHOD.findall(answer))
    seen: set[str] = set()
    out: list[str] = []
    for tok in raw:
        if not tok or tok.lower() in _STOPWORDS or tok in seen:
            continue
        seen.add(tok)
        out.append(tok)
    return out
