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
    assert pack.prompts == {"trace": "你是链路追踪 Agent。"}


def test_load_rocketmq_skeleton_pack():
    """加载仓库内真实的 RocketMQ 骨架包（整链路 fixture）。"""
    repo_root = Path(__file__).resolve().parents[1]   # backend/
    pack_dir = repo_root / "domain_packs" / "rocketmq"
    pack = load_pack(pack_dir)
    assert pack.manifest.name == "rocketmq"
    assert pack.manifest.target_repo == "apache/rocketmq"
    assert "trace" in pack.manifest.active_agents
    # 骨架 yaml 含占位示例条目（非空，验证 schema）
    assert isinstance(pack.trace_templates, list)
    assert isinstance(pack.config_registry, list)


def test_load_rocketmq_full_content():
    """M38：rocketmq 包填充完整内容后加载——4 trace / 3 diagnosis。"""
    repo_root = Path(__file__).resolve().parents[1]   # backend/
    pack = load_pack(repo_root / "domain_packs" / "rocketmq")
    assert pack.manifest.version == "0.2.0"
    # trace 4 链路
    trace_names = [t.name for t in pack.trace_templates]
    assert "normal_message_send" in trace_names
    assert "transaction_message" in trace_names
    assert "delay_message" in trace_names
    assert "orderly_message" in trace_names
    assert len(pack.trace_templates) == 4
    # normal 链路含关键方法
    normal = next(t for t in pack.trace_templates if t.name == "normal_message_send")
    assert "DefaultMQProducer.send" in normal.method_sequence
    assert "CommitLog.putMessage" in normal.method_sequence
    # diagnosis 3 树
    diag_names = [d.name for d in pack.diagnosis_trees]
    assert "message_accumulation" in diag_names
    assert "message_loss" in diag_names
    assert "message_rebalance" in diag_names
    assert len(pack.diagnosis_trees) == 3
