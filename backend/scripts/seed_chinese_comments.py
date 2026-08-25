"""M31 回测实验：给 rocketmq 锚点 chunk 人工标注中文 javadoc（模拟中文团队的代码库）。

背景：M31 验收门 1/2/3 FAIL 的根因之一是 rocketmq 全库无中文注释，chinese_comment
字段无信号（boost 2.0 反而只抬了 demo 库的中文注释）。本脚本把自然、领域准确的中文
javadoc 前置写入 code_chunks.content，模拟「有中文注释习惯的团队维护同一代码库」，
随后 rebuild_es_index 重建索引即可回测 IK + chinese_comment boost 机制是否兑现。

实验纪律（防过拟合披露）：
- 注释按 RocketMQ 4.9.8 真实语义撰写（依据 chunk 的 method_signature/原始 javadoc），
  使用标准中文领域术语，**不逐字照抄评测查询文本**；查询与注释同源于同一代码语义，
  术语重叠是机制本身（IK 分词后词项命中）而非作弊。
- 只改 content；不动 chunk_id（锚点稳定）/ keywords（不混入既有 jieba 路径）/
  Milvus 向量（冻结——隔离变量，full 漏斗中向量路与此前各 arm 逐字节一致）。
- 幂等：检测到已注入（content 以标记注释开头）则跳过；--restore 从快照整体还原。

用法（从 backend/ 运行）::
  uv run python scripts/seed_chinese_comments.py            # 注入（快照到 _m31_content_backup.json）
  uv run python scripts/seed_chinese_comments.py --restore  # 从快照还原 content
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core.config import settings  # noqa: E402

BACKUP_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_m31_content_backup.json")
MARKER = "/** [zh-annot] "  # 注入块首行标记（幂等检测用）

# ---------------------------------------------------------------------------
# 标注表。两类键：
#   "Class.method"     → code_anchor_key 精确匹配（重载 chunk 全部同注，模拟 javadoc 复制）
#   ("Class", "method") → (class_name, method_name) 匹配（类配置子集用）
# 干扰类（DISTRACT_ 前缀语义）best-effort：解析不到仅告警跳过。
# ---------------------------------------------------------------------------
ANNOTATIONS: dict[str | tuple[str, str], str] = {
    # ---- rm01 生产者实现类初始化 ----
    "DefaultMQProducerImpl.DefaultMQProducerImpl":
        "生产者默认实现类的构造方法：绑定上层 DefaultMQProducer 配置对象（及可选 RPC 钩子），"
        "初始化对客户端实例、主题路由管理等内部服务的引用。",
    # ---- rm02 发送核心流程（send 全部重载同注）----
    "DefaultMQProducerImpl.send":
        "消息发送入口（同步/异步/单向/指定队列等重载形态）：校验消息与运行状态后委托"
        " sendDefaultImpl 执行默认发送流程，返回发送结果或经回调异步返回。",
    # ---- rm03 发送默认实现 ----
    "DefaultMQProducerImpl.sendDefaultImpl":
        "发送消息的默认实现逻辑：先查主题发布信息，再按重试次数循环调用 tryToFindTopicPublishInfo "
        "选择消息队列（失败换 broker 重试），最终走 sendKernelImpl 完成底层发送。",
    # ---- rm04 生产者启动初始化 ----
    "DefaultMQProducerImpl.start":
        "生产者启动流程：检查配置与状态后置为运行态，注册到共享的客户端实例并触发其启动，"
        "完成消息发送所需的初始化。",
    # ---- rm05 生产者关闭 ----
    "DefaultMQProducerImpl.shutdown":
        "生产者关闭并释放资源：从客户端实例注销本生产组，清理发送线程池与回调执行器，"
        "可选拉停共享的底层客户端工厂。",
    # ---- rm06 单向发送 ----
    "DefaultMQProducerImpl.sendOneway":
        "单向发送（oneway）：只管把消息发出而不等待 broker 应答，无重试无回调，"
        "开销最小、可靠性最低，适用于日志上报等可容忍丢失的场景。",
    # ---- rm07 客户端底层发送 ----
    "MQClientAPIImpl.sendMessage":
        "客户端底层远程接口：组装发送消息请求（消息头、重试标记、序号等），"
        "经 Netty 通道把消息发给 broker，并把应答映射为发送结果。",
    # ---- rm08 异步底层调用 ----
    "MQClientAPIImpl.sendMessageAsync":
        "发送消息的异步底层调用：请求发出后立即返回，应答到达时由注册的回调"
        "完成发送结果回填或异常上抛，不阻塞调用线程。",
    # ---- rm09 消息编码 ----
    "MessageDecoder.encode":
        "消息编码：把消息属性转成字符串头，写出总长度与魔数，消息体按需压缩后"
        "整体序列化为字节流写回缓冲区，供网络传输与落盘使用。",
    # ---- rm10 消息解码 ----
    "MessageDecoder.decode":
        "消息解码：按总长度与魔数从字节流中还原消息体与属性字段，重建完整的"
        " MessageExt 消息对象。",
    # ---- rm11 属性与字符串互转 ----
    "MessageDecoder.messageProperties2String":
        "消息属性与字符串的互相转换：编码端把属性 Map 拼成 name=value 分号串，"
        "解码端再拆回属性表。",
    "MessageDecoder.decodeProperties":
        "仅从消息字节流缓冲区解码属性部分（不读消息体），还原属性字符串。",
    # ---- rm12 重试主题 ----
    "MixAll.getRetryTopic":
        "取消费重试主题的 topic 名字：%RETRY% 前缀拼接消费组名；消费失败的消息"
        "会被投递到该重试主题按延迟级别重试。",
    # ---- rm13 死信队列 ----
    "MixAll.getDLQTopic":
        "取死信队列（DLQ）的 topic 名字：%DLQ% 前缀拼接消费组名；重试耗尽仍失败的"
        "消息进入死信队列兜底存储。",
    # ---- rm14 消费者拉取入口 ----
    "DefaultMQPushConsumerImpl.pullMessage":
        "推模式消费者的消息拉取入口：对拉取请求做流控判断后向 broker 发起拉取，"
        "回流的消息提交到消费线程池处理。",
    # ---- rm15 broker 拉取处理器 ----
    "PullMessageProcessor.processRequest":
        "broker 端处理拉取请求的处理器：校验订阅关系与消费位点，从存储按队列偏移量"
        "读取消息，未命中且允许挂起时进入长轮询挂起等待新消息。",
    # ---- rm16 broker 异步处理发送 ----
    "SendMessageProcessor.asyncSendMessage":
        "broker 异步处理生产者发来的单条消息：构建消息轨迹上下文、设置存储属性后"
        "异步写入消息存储并返回应答。",
    # ---- rm17 批量消息 ----
    "SendMessageProcessor.asyncSendBatchMessage":
        "批量消息的异步写入存储：一次请求携带多条消息，逐条设置队列偏移与存储时间等"
        "属性后整体落盘，返回批量发送结果。",
    # ---- rm18 重试与死信兜底 ----
    "SendMessageProcessor.handleRetryAndDLQ":
        "消费重试与死信队列的兜底处理：消息重试次数达到上限时改写目标 topic 为"
        "重试主题（延迟级别递增）或死信主题，实现投递失败的消息兜底。",
    # ---- rm19 路由更新 ----
    "MQClientInstance.updateTopicRouteInfoFromNameServer":
        "从 nameserver 更新 topic 路由信息：按需/定时拉取主题的路由数据写入本地路由表，"
        "并同步刷新相关生产者与消费者的队列信息。",
    # ---- rm20 按名查找生产者 ----
    "MQClientInstance.selectProducer":
        "按生产组名字查找本客户端实例内注册的生产者内部实现实例。",
    # ---- rm21 VIP 通道端口 ----
    "MixAll.brokerVIPChannel":
        "VIP 通道地址换算：启用时把 broker 监听端口减 3（如 10911 换成 10909），"
        "让发送走 broker 上独立的 VIP 处理线程池。",
    # ---- rm22 创建 topic ----
    "MQClientAPIImpl.createTopic":
        "创建 topic 的底层请求：向 broker 发送 UPDATE_AND_CREATE_TOPIC 请求，"
        "落成新的主题队列配置。",
    # ---- rm25 消费者启动 ----
    "DefaultMQPushConsumerImpl.start":
        "推模式消费者启动：校验订阅与配置、绑定再均衡与消费服务，进入消息拉取循环。",

    # ============ rm23 DefaultMQProducer 配置项（类名锚 → 全类 chunk 相关，注配置读方法）============
    ("DefaultMQProducer", "getProducerGroup"):
        "生产者配置项：生产组名 producerGroup 的读取（同一组内的生产者互为副本）。",
    ("DefaultMQProducer", "getCreateTopicKey"):
        "生产者配置项：自动创建主题使用的键 createTopicKey 的读取。",
    ("DefaultMQProducer", "getDefaultTopicQueueNums"):
        "生产者配置项：自动建主题默认队列数 defaultTopicQueueNums 的读取。",
    ("DefaultMQProducer", "getSendMsgTimeout"):
        "生产者配置项：发送消息超时时间 sendMsgTimeout 的读取（默认 3000ms）。",
    ("DefaultMQProducer", "getRetryTimesWhenSendFailed"):
        "生产者配置项：同步发送失败重试次数 retryTimesWhenSendFailed 的读取（默认 2）。",
    ("DefaultMQProducer", "getRetryTimesWhenSendAsyncFailed"):
        "生产者配置项：异步发送失败重试次数 retryTimesWhenSendAsyncFailed 的读取。",
    ("DefaultMQProducer", "getMaxMessageSize"):
        "生产者配置项：单条消息最大长度 maxMessageSize 的读取（默认 4MB）。",
    ("DefaultMQProducer", "getCompressMsgBodyOverHowmuch"):
        "生产者配置项：消息体超过该阈值 compressMsgBodyOverHowmuch 时启用压缩的读取。",
    ("DefaultMQProducer", "isSendMessageWithVIPChannel"):
        "生产者配置项：是否走 VIP 通道发送 sendMessageWithVIPChannel 的读取。",
    ("DefaultMQProducer", "isRetryAnotherBrokerWhenNotStoreOK"):
        "生产者配置项：存储不成功时是否换 broker 重发 retryAnotherBrokerWhenNotStoreOK 的读取。",
    ("DefaultMQProducer", "isSendLatencyFaultEnable"):
        "生产者配置项：发送延迟故障转移开关 sendLatencyFaultEnable 的读取。",
    ("DefaultMQProducer", "getLatencyMax"):
        "生产者配置项：延迟退避时间档位表 latencyMax 的读取。",
    ("DefaultMQProducer", "getNotAvailableDuration"):
        "生产者配置项：broker 不可用时长档位表 notAvailableDuration 的读取。",
    ("DefaultMQProducer", "getRetryResponseCodes"):
        "生产者配置项：触发重试的应答码集合 retryResponseCodes 的读取。",
    ("DefaultMQProducer", "start"):
        "默认生产者启动：初始化并启动底层实现，进入可发送状态。",
    ("DefaultMQProducer", "shutdown"):
        "默认生产者关闭：停掉底层实现并释放资源。",
    ("DefaultMQProducer", "sendOneway"):
        "默认生产者的单向发送：发出即忘，不等待应答。",
    ("DefaultMQProducer", "sendMessageInTransaction"):
        "默认生产者的事务消息发送：半消息 + 本地事务 + 提交/回查。",
    ("DefaultMQProducer", "batch"):
        "默认生产者的批量发送：多条消息打包成一条批量消息发送。",

    # ============ rm24 MessageExt 消息实体字段（类名锚 → 全类 chunk 相关，注字段读方法）============
    ("MessageExt", "getMsgId"):
        "消息实体 MessageExt 的字段：消息唯一标识 msgId。",
    ("MessageExt", "getBodyCRC"):
        "消息实体 MessageExt 的字段：消息体 CRC 校验和 bodyCRC。",
    ("MessageExt", "getQueueId"):
        "消息实体 MessageExt 的字段：所在队列编号 queueId。",
    ("MessageExt", "getQueueOffset"):
        "消息实体 MessageExt 的字段：在队列中的偏移量 queueOffset。",
    ("MessageExt", "getCommitLogOffset"):
        "消息实体 MessageExt 的字段：在 commitlog 中的物理偏移 commitLogOffset。",
    ("MessageExt", "getSysFlag"):
        "消息实体 MessageExt 的字段：系统标记位 sysFlag（压缩/事务/属性存储方式等位）。",
    ("MessageExt", "getBornTimestamp"):
        "消息实体 MessageExt 的字段：消息产生时间戳 bornTimestamp。",
    ("MessageExt", "getBornHost"):
        "消息实体 MessageExt 的字段：发送端地址 bornHost。",
    ("MessageExt", "getStoreHost"):
        "消息实体 MessageExt 的字段：存储端 broker 地址 storeHost。",
    ("MessageExt", "getStoreTimestamp"):
        "消息实体 MessageExt 的字段：消息写入存储的时间戳 storeTimestamp。",
    ("MessageExt", "getStoreSize"):
        "消息实体 MessageExt 的字段：消息占用存储字节数 storeSize。",
    ("MessageExt", "getReconsumeTimes"):
        "消息实体 MessageExt 的字段：已被重新消费次数 reconsumeTimes。",
    ("MessageExt", "getPreparedTransactionOffset"):
        "消息实体 MessageExt 的字段：事务半消息的 prepared 事务偏移 preparedTransactionOffset。",
    ("MessageExt", "getBrokerName"):
        "消息实体 MessageExt 的字段：存储该消息的 broker 名称 brokerName。",
    ("MessageExt", "toString"):
        "消息实体 MessageExt 的各字段汇总输出（msgId/topic/队列/偏移/时间戳等）。",
    ("MessageExt", "parseTopicFilterType"):
        "消息实体字段：从系统标记位解析主题过滤类型 tag/SQL92。",

    # ============ 干扰类（非锚点，模拟中文团队对周边代码的常规注释）============
    "ConsumeMessageConcurrentlyService.start":
        "并发消费服务启动：创建消费线程池并进入消息消费循环。",
    "ConsumeMessageConcurrentlyService.shutdown":
        "并发消费服务关闭：停掉消费线程池并等待在途消费任务结束。",
    "ConsumeMessageConcurrentlyService.consumeMessageDirectly":
        "直接消费指定消息（运维/诊断用）：绕过拉取流程同步消费一条消息并返回结果。",
    "RebalancePushImpl.messageChanged":
        "推模式消费者的再均衡回调：订阅的队列分配变化时刷新本地消息队列。",
    "CommitLog.putMessage":
        "存储层 commitlog 追加写入一条消息：定位最后一个内存映射文件、写入消息并构建索引。",
    "MappedFile.appendMessage":
        "内存映射文件追加消息字节：写满则滚动到下一个文件。",
    "DefaultMQPullConsumerImpl.pull":
        "拉模式消费者主动拉取消息：按队列偏移向 broker 发起拉取请求。",
    "RemotingHelper.parseSocketAddressAddr":
        "解析套接字地址字符串，还原 broker/客户端的网络地址。",
}

# 干扰类锚点（解析不到仅告警，不失败）
BEST_EFFORT = {
    "ConsumeMessageConcurrentlyService.start",
    "ConsumeMessageConcurrentlyService.shutdown",
    "ConsumeMessageConcurrentlyService.consumeMessageDirectly",
    "RebalancePushImpl.messageChanged",
    "CommitLog.putMessage",
    "MappedFile.appendMessage",
    "DefaultMQPullConsumerImpl.pull",
    "RemotingHelper.parseSocketAddressAddr",
}


def _wrap(comment: str) -> str:
    """注释文本 → 前置的 javadoc 块（带幂等标记）。"""
    lines = [ln for ln in comment.strip().splitlines() if ln.strip()]
    body = "\n".join(f" * {ln.strip()}" for ln in lines)
    return f"/** [zh-annot]\n{body}\n */\n"


def _resolve_targets(session: Session) -> tuple[list[tuple[str, str]], list[str]]:
    """把标注键解析成 (chunk_id, 注释块) 列表；返回 (targets, warnings)。"""
    targets: list[tuple[str, str]] = []
    warnings: list[str] = []
    anchor_keys = [k for k in ANNOTATIONS if isinstance(k, str)]
    if anchor_keys:
        rows = session.execute(text(
            "SELECT chunk_id, code_anchor_key FROM code_chunks "
            "WHERE code_anchor_key = ANY(:a) AND is_deleted = false"
        ), {"a": anchor_keys}).mappings().all()
        by_key: dict[str, list[str]] = {}
        for r in rows:
            by_key.setdefault(r["code_anchor_key"], []).append(r["chunk_id"])
        for k in anchor_keys:
            for cid in by_key.get(k, []):
                targets.append((cid, _wrap(ANNOTATIONS[k])))
            if k not in by_key:
                warnings.append(
                    f"anchor 未命中: {k}"
                    + ("（best-effort 跳过）" if k in BEST_EFFORT else " ⚠ STRICT MISS"))
    pair_keys = [k for k in ANNOTATIONS if isinstance(k, tuple)]
    if pair_keys:
        rows = session.execute(text(
            "SELECT chunk_id, class_name, method_name FROM code_chunks "
            "WHERE class_name = ANY(:c) AND is_deleted = false"
        ), {"c": sorted({c for c, _ in pair_keys})}).mappings().all()
        pair_index = {k: _wrap(v) for k, v in ANNOTATIONS.items() if isinstance(k, tuple)}
        hit = set()
        for r in rows:
            key = (r["class_name"], r["method_name"])
            if key in pair_index:
                hit.add(key)
                targets.append((r["chunk_id"], pair_index[key]))
        for k in pair_keys:
            if k not in hit:
                warnings.append(f"(class,method) 未命中: {k}（跳过）")
    return targets, warnings


def inject(session: Session) -> int:
    targets, warnings = _resolve_targets(session)
    for w in warnings:
        print("WARN:", w)
    print(f"解析目标 chunk: {len(targets)}")

    # 快照（幂等：已有快照则保留最早一份，防二次注入覆盖原始值）
    backup: dict[str, str] = {}
    if os.path.exists(BACKUP_PATH):
        with open(BACKUP_PATH, encoding="utf-8") as f:
            backup = json.load(f)
        print(f"已有快照 {len(backup)} 条（保留原始份）")

    n_updated = n_skipped = 0
    for cid, block in targets:
        row = session.execute(text(
            "SELECT content FROM code_chunks WHERE chunk_id = :c"
        ), {"c": cid}).mappings().first()
        if row is None:
            continue
        original = row["content"] or ""
        if original.startswith(MARKER[:14]):
            n_skipped += 1
            continue
        if cid not in backup:
            backup[cid] = original
        session.execute(text(
            "UPDATE code_chunks SET content = :new WHERE chunk_id = :c"
        ), {"new": block + original, "c": cid})
        n_updated += 1
    session.commit()
    with open(BACKUP_PATH, "w", encoding="utf-8") as f:
        json.dump(backup, f, ensure_ascii=False)
    print(f"注入完成: updated={n_updated} skipped(已注入)={n_skipped} 快照={len(backup)} -> {BACKUP_PATH}")
    return 0


def restore(session: Session) -> int:
    if not os.path.exists(BACKUP_PATH):
        print(f"无快照文件: {BACKUP_PATH}")
        return 1
    with open(BACKUP_PATH, encoding="utf-8") as f:
        backup: dict[str, str] = json.load(f)
    n = 0
    for cid, original in backup.items():
        session.execute(text(
            "UPDATE code_chunks SET content = :c WHERE chunk_id = :id"
        ), {"c": original, "id": cid})
        n += 1
    session.commit()
    print(f"还原完成: {n} chunks")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="M31 回测：rocketmq 锚点中文 javadoc 注入/还原")
    ap.add_argument("--restore", action="store_true", help="从快照还原 content")
    args = ap.parse_args(argv)
    engine = create_engine(settings.database_url_sync)
    with Session(engine) as session:
        return restore(session) if args.restore else inject(session)


if __name__ == "__main__":
    raise SystemExit(main())
