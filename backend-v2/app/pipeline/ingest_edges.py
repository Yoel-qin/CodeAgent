"""调用边入库：按 repo 替换全部 call_edges（先删后插，同事务）。"""
from __future__ import annotations

import loguru
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.models.code_graph import CallEdge, CodeEntity

logger = loguru.logger


def replace_edges(session: Session, *, repo: str, edges: list[dict]) -> int:
    """按 repo 先 DELETE 全部 call_edges 再批量 INSERT，返回插入边数。

    边 dict 的 class/method 名 → code_entities.id 查询只匹配方法实体
    （method_name IS NOT NULL），类实体不参与调用边。查不到 id 的边跳过并计数。
    """
    # 1. 删除该 repo 所有边（经 caller/callee 两端 join 定位）
    del_stmt = delete(CallEdge).where(
        CallEdge.id.in_(
            select(CallEdge.id).join(
                CodeEntity, CallEdge.caller_id == CodeEntity.id
            ).where(CodeEntity.repo == repo)
        )
    )
    session.execute(del_stmt)

    # 2. 预加载该 repo 所有方法实体 id → (class_name, method_name) 映射
    ent_stmt = select(
        CodeEntity.id, CodeEntity.class_name, CodeEntity.method_name
    ).where(
        CodeEntity.repo == repo,
        CodeEntity.method_name.is_not(None),
    )
    id_map: dict[tuple[str, str], int] = {
        (row.class_name, row.method_name): row.id
        for row in session.execute(ent_stmt).all()
    }

    # 3. 批量 INSERT，跳过无法解析 id 的边
    skipped = 0
    inserted = 0
    for e in edges:
        caller_key = (e["caller_class"], e["caller_method"])
        callee_key = (e["callee_class"], e["callee_method"])
        caller_id = id_map.get(caller_key)
        callee_id = id_map.get(callee_key)
        if caller_id is None or callee_id is None:
            skipped += 1
            continue
        session.add(CallEdge(
            caller_id=caller_id,
            callee_id=callee_id,
            call_type=e["call_type"],
            call_site_file=e["call_site_file"],
            call_site_line=e["call_site_line"],
        ))
        inserted += 1

    if skipped:
        logger.warning(f"replace_edges: {skipped} edges skipped (entity id not found)")

    session.flush()
    return inserted
