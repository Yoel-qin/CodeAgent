"""M32 ①b：translate_javadoc 纯函数 + mock LLM 编排——零 infra、零真实调用。"""
from __future__ import annotations

import json

import pytest

import scripts.translate_javadoc as tj


def test_anchors_from_tags_only_method_anchors(tmp_path):
    p = tmp_path / "es.yaml"
    p.write_text(
        "version: 1\ntarget_repos: []\nqueries:\n"
        '  - { id: r1, text: "发送消息", relevant: ["DefaultMQProducerImpl.send", "DefaultMQProducer"], tags: ["rocketmq"] }\n'
        '  - { id: r2, text: "别的", relevant: ["code_abc12345"], tags: ["rocketmq"] }\n'
        '  - { id: o1, text: "其他", relevant: ["Account.deposit"] }\n',
        encoding="utf-8",
    )
    anchors, skipped = tj._anchors_from_tags(str(p), ["rocketmq"])
    assert anchors == ["DefaultMQProducerImpl.send"]
    assert skipped == 2   # 整类名 + literal chunk_id 各 1


def test_wrap_marker_block():
    out = tj._wrap("第一行\n第二行")
    assert out.startswith(tj.MARKER)
    assert out.endswith("*/\n")
    assert " * 第一行" in out


@pytest.mark.asyncio
async def test_translate_batch_json_roundtrip_and_retry(monkeypatch):
    class _LLM:
        def __init__(self):
            self.n = 0

        async def chat(self, messages, **kw):
            self.n += 1
            if self.n == 1:
                return "不是 JSON"          # 首次坏输出 → 重试
            return json.dumps(["译文一", "译文二"], ensure_ascii=False)

    out = await tj._translate_batch(_LLM(), ["doc one", "doc two"])
    assert out == ["译文一", "译文二"]


@pytest.mark.asyncio
async def test_translate_batch_length_mismatch_gives_none(monkeypatch):
    class _LLM:
        async def chat(self, messages, **kw):
            return json.dumps(["只有一条"])

    out = await tj._translate_batch(_LLM(), ["a", "b"])
    assert out == [None, None]              # 长度不符 → 整批放弃（逐条 None=跳过）


def test_apply_update_keeps_identity_and_merges_keywords():
    class _S:
        def __init__(self):
            self.sqls = []

        def execute(self, sql, params=None):
            self.sqls.append((str(sql), params))

    s = _S()
    tj._apply_update(s, "cid1", tj._wrap("生产者发送消息"), old_kw=["send"], old_content="public void send() {}")
    sql, params = s.sqls[0]
    assert "UPDATE code_chunks" in sql
    assert params["c"] == "cid1"
    assert params["id"] == "cid1"
    assert params["new"].startswith(tj.MARKER)
    assert "生产者" in params["kw"] and "send" in params["kw"]
    assert "content_hash" not in sql and "chunk_id" not in sql.replace("WHERE chunk_id", "")  # 身份不动
