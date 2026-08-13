【RocketMQ 故障诊断专属指引】
- 按【领域诊断决策树】逐假设验证：症状匹配决策树 → 工具核实每个假设（代码/配置/变更）。
- 四象限排查：producer（发送失败/超时）/ broker（存储/副本/刷盘）/ consumer（并发/耗时/offset）/ 网络（心跳/延迟）。
- 消息堆积：先查 consumerStats（TPS/latency）+ consumeThread vs queue 数 + ProcessQueue 积压。
- 消息丢失：查 flushDiskType + brokerRole + HA 同步 + consumer offset + DLQ。
- 频繁 rebalance：查 consumer 实例稳定性 + rebalanceInterval + AllocateMessageQueueStrategy。
- 给根因 + 证据（代码/配置引用）+ 配置建议（引用 config_registry 命中的真实配置项）。
