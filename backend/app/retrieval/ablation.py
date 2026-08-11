"""A/B 消融开关配置（横切·评测 / 后端设计 Phase 9 M24）。

``AblationConfig`` 是给 :func:`app.retrieval.pipeline.RetrievalPipeline.recall` 的**可选消融钩子**：
控制四条可独立开关的检索环节（向量召回 / 词法召回 / 图遍历 / 精排），供检索 A/B 评测
（``app.eval.ab_service``）逐项测 on/off 的 Recall@K / Precision@K / NDCG delta，兑现
开发清单 §2/§3/§4「需评测集」验收。

默认实例（``AblationConfig()`` / :func:`full`）四项全 True，**与生产链路完全一致**；
``recall(ablation=None)`` 等价于 ``recall(ablation=full())`` —— 守卫代码在生产是死代码，
零行为变更。A/B 只在评测侧构造非默认实例注入（经 ``run_eval`` 的 ``recall_fn`` DI 接缝）。

本模块不导入 ``pipeline``（避免环依赖）。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AblationConfig:
    """检索环节消融开关（默认全开 = 生产链路）。

    各字段为 ``True`` 时该环节正常执行；为 ``False`` 时在 ``recall`` 内被跳过
    （对应召回路置空候选、精排块整体跳过），从而隔离该环节对检索质量的贡献。

    图遍历依赖向量+词法的前 N 条 code chunk 作种子（``pipeline._SEED_TOP``），
    故关闭向量/词法会间接饿死图遍历——这是链路内禀依赖，非本配置额外处理。
    """

    vector: bool = True
    lexical: bool = True
    graph: bool = True
    rerank: bool = True


def full() -> AblationConfig:
    """全开配置（= 生产链路）。等价于 ``AblationConfig()``，语义显式化。"""
    return AblationConfig()
