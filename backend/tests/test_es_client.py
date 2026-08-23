"""es_client mapping 选择与查询体构造（M31）。零 infra：fake ES 对象，不连真实集群。"""
from __future__ import annotations

import app.clients.es_client as ec
from app.core.config import settings


class _FakeIndices:
    def __init__(self):
        self.created: list[tuple[str, dict | None, dict | None]] = []

    def exists(self, index):
        return False

    def create(self, index, mappings=None, settings=None):
        self.created.append((index, mappings, settings))


class _FakeES:
    def __init__(self):
        self.indices = _FakeIndices()
        self.searches: list[dict] = []

    def search(self, index, **body):
        self.searches.append(body)
        return {"hits": {"hits": []}}


def _with_fake_es(monkeypatch):
    fake = _FakeES()
    monkeypatch.setattr(ec, "_es", fake)
    return fake


# ---- ensure_index：按开关选 mapping ----


def test_ensure_index_off_uses_standard_mapping(monkeypatch):
    monkeypatch.setattr(settings, "es_ik_enabled", False)
    fake = _with_fake_es(monkeypatch)
    ec.ensure_index()
    index, mappings, msettings = fake.indices.created[0]
    assert index == ec.INDEX
    assert msettings is None                       # off 无自定义 analysis
    assert mappings == ec._MAPPING                 # 现行为逐字节不变
    assert "chinese_comment" not in mappings["properties"]
    assert "fields" not in mappings["properties"]["content"]


def test_ensure_index_on_uses_ik_mapping(monkeypatch):
    monkeypatch.setattr(settings, "es_ik_enabled", True)
    fake = _with_fake_es(monkeypatch)
    ec.ensure_index()
    index, mappings, msettings = fake.indices.created[0]
    assert index == ec.INDEX
    assert msettings is not None                   # on 带 analysis settings
    analysis = msettings["analysis"]
    # code_analyzer：word_delimiter_graph 拆 camelCase + 保原词
    wdg = analysis["filter"]["code_split"]
    assert wdg["type"] == "word_delimiter_graph"
    assert wdg["split_on_case_change"] is True
    assert wdg["preserve_original"] is True
    assert analysis["analyzer"]["code_analyzer"]["tokenizer"] == "standard"
    props = mappings["properties"]
    assert props["content"]["analyzer"] == "ik_max_word"
    assert props["content"]["search_analyzer"] == "ik_smart"
    assert props["content"]["fields"]["code"]["analyzer"] == "code_analyzer"
    assert props["chinese_comment"]["analyzer"] == "ik_max_word"
    assert props["chinese_comment"]["search_analyzer"] == "ik_smart"


def test_ensure_index_skips_when_exists(monkeypatch):
    monkeypatch.setattr(settings, "es_ik_enabled", True)
    fake = _with_fake_es(monkeypatch)
    fake.indices.exists = lambda index: True      # 已存在 → 不重建（升级走 rebuild 脚本）
    ec.ensure_index()
    assert fake.indices.created == []


# ---- search：off 2 子句 / on 4 子句（boost 2.0/1.0/1.0/2.0），kinds 过滤共存 ----


def test_search_off_two_clauses(monkeypatch):
    monkeypatch.setattr(settings, "es_ik_enabled", False)
    fake = _with_fake_es(monkeypatch)
    ec.search(["发送", "消息"], "发送消息", 10, None)
    body = fake.searches[0]
    should = body["query"]["bool"]["should"]
    assert len(should) == 2
    assert {"terms": {"keywords": ["发送", "消息"], "boost": 2.0}} in should
    assert {"match": {"content": {"query": "发送消息", "boost": 1.0}}} in should
    assert body["size"] == 10


def test_search_on_four_clauses(monkeypatch):
    monkeypatch.setattr(settings, "es_ik_enabled", True)
    fake = _with_fake_es(monkeypatch)
    ec.search(["发送"], "默认生产者发送消息", 20, ["code"])
    body = fake.searches[0]
    bool_q = body["query"]["bool"]
    should = bool_q["should"]
    assert len(should) == 4
    assert {"terms": {"keywords": ["发送"], "boost": 2.0}} in should
    assert {"match": {"content": {"query": "默认生产者发送消息", "boost": 1.0}}} in should
    assert {"match": {"content.code": {"query": "默认生产者发送消息", "boost": 1.0}}} in should
    assert {"match": {"chinese_comment": {"query": "默认生产者发送消息", "boost": 2.0}}} in should
    # M45 RBAC kinds 过滤与 M31 开关正交共存
    assert bool_q["filter"] == [{"terms": {"kind": ["code"]}}]
