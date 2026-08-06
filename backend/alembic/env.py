"""Alembic 迁移环境（同步，psycopg；目标元数据 = 所有 ORM 模型）。"""
from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool

from alembic import context

# 确保 backend/ 在 sys.path（prepend_sys_path=. 通常已处理）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402
from app.db.models import Base  # noqa: E402,F401  导入即注册所有模型到 metadata

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 注入同步连接串（psycopg）
config.set_main_option("sqlalchemy.url", settings.database_url_sync)

target_metadata = Base.metadata


# langgraph AsyncPostgresSaver.setup() 自建的检查点四表（checkpoints / checkpoint_writes /
# checkpoint_blobs + 迁移版本表 checkpoint_migrations）非应用 ORM、由 langgraph 自管；
# 排除以免 autogenerate 把它们标为 drop。
def _include_object(object, name, type_, reflected, compare_to_object):
    if type_ == "table" and name in {
        "checkpoints", "checkpoint_writes", "checkpoint_blobs", "checkpoint_migrations",
    }:
        return False
    return True


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            include_object=_include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
