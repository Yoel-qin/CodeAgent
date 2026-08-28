"""find_symbol：符号定义/引用查找（M1 启发式 grep 版；M3 起 def 走 code_entity SQL）。

def 路线两条正则：类型（class/interface/enum）与方法（修饰符序列 + 返回类型 + 名称 + "("）。
正则刻意宽松——误报交给调用方（LLM/后续 SQL 版）收敛，漏报才是硬伤。
"""
import re

from app.core.grep import grep_code

_TYPE_RX = r"^\s*(?:@\w+(?:\([^)]*\))?\s+)*(?:(?:public|private|protected|static|final|abstract)\s+)*(?:class|interface|enum)\s+{name}\b"
_METHOD_RX = r"^\s*(?:@\w+(?:\([^)]*\))?\s+)*(?:(?:public|private|protected|static|final|abstract|synchronized|native)\s+)*[\w<>\[\],.\s]+?\s{name}\s*\("


def find_symbol(repos_root, repo: str, symbol_name: str, ref_type: str = "def") -> dict:
    name = (symbol_name or "").strip()
    if not name:
        return {"error": "symbol_name is required"}
    esc = re.escape(name)
    if ref_type == "ref":
        res = grep_code(repos_root, repo, rf"\b{esc}\b", file_glob="*.java")
        if "error" in res:
            return res
        locations = [
            {"file": m["file"], "line": m["line"], "content": m["content"], "kind": "ref"}
            for m in res["matches"]
        ]
    else:
        locations = []
        total = 0
        for kind, rx in (("type", _TYPE_RX), ("method", _METHOD_RX)):
            res = grep_code(repos_root, repo, rx.format(name=esc), file_glob="*.java")
            if "error" in res:
                continue
            for m in res["matches"]:
                total += 1
                if len(locations) < 50:
                    locations.append(
                        {"file": m["file"], "line": m["line"], "content": m["content"], "kind": kind}
                    )
        return {"locations": locations, "total_count": total, "truncated": total > len(locations)}
    return {"locations": locations, "total_count": res["total_count"], "truncated": res["truncated"]}
