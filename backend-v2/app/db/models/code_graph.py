"""调用图三表 ORM：CodeEntity / CallEdge / CodeMetric。

Spec 偏差记录（计划内决策）：
- CodeEntity UK 简化为 (repo, class_name, method_name, file_path, start_line)。
  PG 中 NULL 不参与唯一约束，因此含 NULL method_name 的类实体
  不会与方法实体冲突。start_line 在类/方法实体中均非空，
  使 UK 完全由非 NULL 列组成，避免 NULL 排除语义的歧义。
- CallEdge UK = (caller_id, callee_id, call_site_line)。
- CodeMetric entity_id FK UNIQUE（每实体最多一条度量）。
"""

from sqlalchemy import ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CodeEntity(Base):
    """代码实体（类/接口/枚举/记录/注解类型/方法）。"""
    __tablename__ = "code_entities"

    id: Mapped[int] = mapped_column(primary_key=True)
    repo: Mapped[str] = mapped_column(String(256), index=True)
    entity_type: Mapped[str] = mapped_column(String(32))
    class_name: Mapped[str] = mapped_column(String(512))
    method_name: Mapped[str | None] = mapped_column(String(256))
    module: Mapped[str] = mapped_column(String(256))
    file_path: Mapped[str] = mapped_column(String(1024))
    start_line: Mapped[int | None] = mapped_column(Integer)
    end_line: Mapped[int | None] = mapped_column(Integer)
    signature: Mapped[str | None] = mapped_column(String(2048))

    __table_args__ = (
        Index("ix_code_entities_repo_class_name", "repo", "class_name"),
        UniqueConstraint(
            "repo", "class_name", "method_name", "file_path", "start_line",
            name="uk_code_entities_identity",
        ),
    )


class CallEdge(Base):
    """调用边（caller → callee）。"""
    __tablename__ = "call_edges"

    id: Mapped[int] = mapped_column(primary_key=True)
    caller_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("code_entities.id", ondelete="CASCADE"), index=True
    )
    callee_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("code_entities.id", ondelete="CASCADE"), index=True
    )
    call_type: Mapped[str] = mapped_column(String(32))
    call_site_file: Mapped[str] = mapped_column(String(1024))
    call_site_line: Mapped[int] = mapped_column(Integer)

    __table_args__ = (
        UniqueConstraint("caller_id", "callee_id", "call_site_line"),
    )


class CodeMetric(Base):
    """实体级代码度量（每实体最多一行）。"""
    __tablename__ = "code_metrics"

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("code_entities.id", ondelete="CASCADE"), unique=True
    )
    complexity: Mapped[int] = mapped_column(Integer)
    fan_in: Mapped[int] = mapped_column(Integer)
    fan_out: Mapped[int] = mapped_column(Integer)
    loc: Mapped[int] = mapped_column(Integer)
