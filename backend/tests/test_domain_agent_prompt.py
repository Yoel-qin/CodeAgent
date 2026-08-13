"""M37 领域 Agent prompt 组装 + pack 解析（纯函数，无 infra）。"""
from __future__ import annotations

from app.agent.agents._domain_prompt import _BASE_ROLE, _pack_from_state, build_domain_prompt
from app.domain_packs.models import (
    DiagnosisTree,
    DomainPack,
    Manifest,
    TraceTemplate,
    TuningRule,
)


def _pack_with(**kwargs) -> DomainPack:
    base = dict(
        manifest=Manifest(name="rocketmq", target_repo="apache/rocketmq"),
        trace_templates=[TraceTemplate(name="normal_send", scenario="普通消息发送",
                                       method_sequence=["DefaultMQProducer.send", "Broker.put"])],
        diagnosis_trees=[DiagnosisTree(name="msg_accumulation", symptoms=["消息堆积"],
                                       hypothesis_checks=["查 consume_queue"])],
        tuning_rules=[TuningRule(scenario="high_throughput", parameter="maxReconsumeTimes",
                                 suggestion="调大")],
    )
    base.update(kwargs)
    return DomainPack(**base)


def test_build_domain_prompt_no_pack_returns_base_only():
    out = build_domain_prompt("trace", None)
    assert out == _BASE_ROLE["trace"]


def test_build_domain_prompt_with_pack_appends_knowledge():
    pack = _pack_with()
    out = build_domain_prompt("trace", pack)
    assert _BASE_ROLE["trace"] in out
    assert "normal_send" in out          # trace_template 注入
    assert "DefaultMQProducer.send" in out


def test_build_domain_prompt_with_domain_hint():
    pack = _pack_with(prompts={"trace": "【RocketMQ 专属】按发送链路模板作答。"})
    out = build_domain_prompt("trace", pack)
    assert "【RocketMQ 专属】" in out


def test_serialize_empty_knowledge_no_empty_section():
    pack = _pack_with(trace_templates=[])   # trace 字段空
    out = build_domain_prompt("trace", pack)
    assert out == _BASE_ROLE["trace"]       # 不附加空段
    assert "===" not in out


def test_serialize_diagnose_and_tune():
    pack = _pack_with()
    assert "msg_accumulation" in build_domain_prompt("diagnose", pack)
    assert "maxReconsumeTimes" in build_domain_prompt("tune", pack)


def test_pack_from_state_resolves_via_registry(monkeypatch):
    pack = _pack_with()
    fake_reg = type("R", (), {"get": lambda self, name: pack if name == "rocketmq" else None})()
    monkeypatch.setattr("app.agent.agents._domain_prompt._get_pack_registry", lambda: fake_reg)
    assert _pack_from_state({"active_pack_name": "rocketmq"}) is pack
    assert _pack_from_state({"active_pack_name": None}) is None
    assert _pack_from_state({}) is None                     # 缺键 → None
    assert _pack_from_state({"active_pack_name": "missing"}) is None


def test_build_domain_prompt_real_rocketmq_pack_trace():
    """M38：真实 rocketmq 包——trace prompt 含 base + 领域 hint（prompts/trace.md）+ trace_templates。"""
    from pathlib import Path

    from app.domain_packs.loader import load_pack
    repo_root = Path(__file__).resolve().parents[1]
    pack = load_pack(repo_root / "domain_packs" / "rocketmq")
    out = build_domain_prompt("trace", pack)
    assert _BASE_ROLE["trace"] in out                 # base 角色
    assert "RocketMQ 链路追踪专属指引" in out          # prompts/trace.md 注入（loader 修复后 key="trace" 生效）
    assert "normal_message_send" in out               # trace_templates 序列化
    assert "DefaultMQProducer.send" in out


def test_build_domain_prompt_real_rocketmq_pack_diagnose_and_tune():
    """M38：真实 rocketmq 包——diagnose/tune prompt 含领域 hint + 序列化内容。"""
    from pathlib import Path

    from app.domain_packs.loader import load_pack
    repo_root = Path(__file__).resolve().parents[1]
    pack = load_pack(repo_root / "domain_packs" / "rocketmq")
    d_out = build_domain_prompt("diagnose", pack)
    assert "RocketMQ 故障诊断专属指引" in d_out
    assert "message_accumulation" in d_out
    t_out = build_domain_prompt("tune", pack)
    assert "RocketMQ 性能调优专属指引" in t_out
    assert "high_throughput" in t_out
