"""检索评测（横切·评测）：纯函数 IR 指标 + 真实检索管线编排 + A/B 消融。

- ``metrics``：Recall@K / Precision@K / MRR / NDCG@K 纯函数（零依赖，仿 ``tests/test_fusion.py``）。
- ``eval_service``：ground-truth 解析（anchor_key/类名/chunk_id → chunk_id）+ ``run_eval`` 编排。
- ``ab_service``：检索 A/B（经 ``retrieval.ablation.AblationConfig`` 关闭某环节）对照 on/off
  的 Recall@K/NDCG delta，兑现 §2/§3/§4「需评测集」验收（Phase 9 M24）。
"""
