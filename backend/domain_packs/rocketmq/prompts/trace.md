【RocketMQ 链路追踪专属指引】
- 先识别消息类型（普通/事务/延迟/顺序），按对应模板（见下方【领域链路模板】）的 method_sequence 展开。
- producer 端：DefaultMQProducer → DefaultMQProducerImpl → MQClientAPIImpl（RPC）。
- broker 端：SendMessageProcessor → MessageStore → CommitLog（+ 异步 ConsumeQueue/Index）。
- 事务消息注意 half message（RMQ_SYS_TRANS_HALF_TOPIC）+ EndTransactionProcessor + 回查机制。
- 延迟消息注意 SCHEDULE_TOPIC_XXXX（18 级别）+ 到时重写 topic 投递。
- 顺序消息注意 MessageQueueSelector + ConsumeMessageOrderlyService（vs 并发 ConsumeMessageConcurrentlyService）。
- 用 search_symbol 解析类名，get_call_chain/get_downstream_callers 展开链路，read_code 看实现。
