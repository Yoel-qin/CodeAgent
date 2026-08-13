"""M36 DomainPack loader 单测——纯函数 + tmp YAML，无 infra。"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.domain_packs import models
from app.domain_packs.loader import PackLoadError, load_pack


def _write(pack_dir: Path, files: dict[str, str]) -> None:
    for name, body in files.items():
        p = pack_dir / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")


def test_load_pack_full(tmp_path):
    _write(tmp_path, {
        "manifest.yaml": "name: rocketmq\ntarget_repo: apache/rocketmq\nversion: v1\nactive_agents: [trace, diagnose, tune]\n",
        "config_registry.yaml": "- key: max_reconsume_times\n  description: 最大重试\n  allowed_values: [\"0\", \"16\"]\n",
        "trace_templates.yaml": "- name: normal_send\n  scenario: normal\n  method_sequence: [producer.send, consumer.pull]\n",
        "diagnosis_trees.yaml": "- name: message_accumulation\n  symptoms: [堆积]\n  hypothesis_checks: [查消费并发]\n",
        "tuning_rules.yaml": "- scenario: accumulation\n  parameter: consume_thread_min\n  suggestion: 扩容\n",
    })
    pack = load_pack(tmp_path)
    assert isinstance(pack, models.DomainPack)
    assert pack.manifest.name == "rocketmq"
    assert pack.manifest.target_repo == "apache/rocketmq"
    assert pack.config_registry[0].key == "max_reconsume_times"
    assert pack.trace_templates[0].method_sequence == ["producer.send", "consumer.pull"]
    assert pack.diagnosis_trees[0].name == "message_accumulation"
    assert pack.tuning_rules[0].parameter == "consume_thread_min"


def test_load_pack_missing_manifest_raises(tmp_path):
    _write(tmp_path, {"config_registry.yaml": "- key: x\n"})
    with pytest.raises(PackLoadError):
        load_pack(tmp_path)


def test_load_pack_invalid_schema_raises(tmp_path):
    _write(tmp_path, {"manifest.yaml": "name: x\n"})  # 缺 target_repo（必填）
    with pytest.raises(PackLoadError):
        load_pack(tmp_path)


def test_load_pack_missing_domain_yaml_tolerates_empty(tmp_path):
    # 只有 manifest，缺 4 个领域 yaml → 各字段空列表（容错，骨架包不必全有）
    _write(tmp_path, {"manifest.yaml": "name: stub\ntarget_repo: stub/repo\n"})
    pack = load_pack(tmp_path)
    assert pack.manifest.name == "stub"
    assert pack.trace_templates == []
    assert pack.config_registry == []
    assert pack.prompts == {}


def test_load_pack_prompts_loaded(tmp_path):
    _write(tmp_path, {
        "manifest.yaml": "name: x\ntarget_repo: x/y\n",
        "prompts/trace.md": "你是链路追踪 Agent。",
    })
    pack = load_pack(tmp_path)
    assert pack.prompts == {"trace.md": "你是链路追踪 Agent。"}
