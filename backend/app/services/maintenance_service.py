"""运营加固服务（Phase 7 Milestone 14 Part B）—— HITL 中断超时过期。

被放弃的 HITL 审批会永久挂起（``chat_messages.status='interrupted'`` 不翻）。本服务把
超过 ``timeout_hours`` 仍未 resume 的中断态 assistant 消息翻为 ``status='expired'`` 终态，
并写一句超时说明，让会话有干净终态（而非无限「等待人工确认」）。返回过期消息所属的
``conversation_id`` 列表，供调用方（``main`` 维护循环）立即清理对应 thread 的 checkpoint
（见 ``checkpoint_cleanup.delete_thread_checkpoints``），使晚到的 ``/resume`` 干净失败。

时间戳用 ``chat_messages.created_at``（中断创建时刻，带 ``idx_chat_messages_created``）；
``ChatMessage`` 无 ``updated_at``，故仅能按创建时刻判超时。
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_EXPIRE_SELECT_SQL = text("""
    SELECT message_id, conversation_id FROM chat_messages
    WHERE status = 'interrupted'
      AND created_at < now() - make_interval(hours => :hours)
""")
_EXPIRE_UPDATE_SQL = text("""
    UPDATE chat_messages
       SET status = 'expired', content = '（审批超时已自动取消）'
     WHERE message_id = :mid
""")
_EXPIRE_NOTE = "（审批超时已自动取消）"


async def expire_stale_interrupts(session: AsyncSession, timeout_hours: int) -> list[str]:
    """把超过 ``timeout_hours`` 的 ``interrupted`` 消息翻为 ``expired``，返回去重后的 conversation_id 列表。

    ``timeout_hours <= 0`` → 直接返回 ``[]``（禁用超时）。空命中仍会 commit（幂等，维持事务语义）。
    """
    if timeout_hours <= 0:
        return []
    rows = (await session.execute(_EXPIRE_SELECT_SQL, {"hours": timeout_hours})).all()
    cids: list[str] = []
    for msg_id, cid in rows:
        await session.execute(_EXPIRE_UPDATE_SQL, {"mid": msg_id})
        cids.append(cid)
    await session.commit()
    # 同一会话多条中断（罕见）去重，避免后续重复删同一 thread checkpoint
    return list(dict.fromkeys(cids))
