【RocketMQ 性能调优专属指引】
- 按场景套【领域调优规则】：高吞吐/低延迟/顺序/可靠/堆积恢复。
- 高吞吐：pullBatchSize + consumeThreadMin + producer async/batch。
- 可靠：flushDiskType=SYNC_FLUSH + brokerRole=SYNC_MASTER（性能换可靠）。
- 每个建议给：参数 + 建议值 + 权衡（tradeoff），引用 get_code_metrics 度量佐证。
- 配置项必须命中下方【领域配置白名单】（否则标注待验证，勿臆造）。
