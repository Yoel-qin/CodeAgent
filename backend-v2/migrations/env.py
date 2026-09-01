"""alembic env：同步 psycopg 驱动（URL 运行时从 settings 注入，ini 里的占位不生效）。"""
from alembic import context
from sqlalchemy import engine_from_config, pool

import app.db.models  # noqa: F401 — 让 target_metadata 看到三表
from app.core.config import settings
from app.db.base import Base

# 后续任务的模型都在这里 import，autogenerate 才看得到
config = context.config
config.set_main_option("sqlalchemy.url", settings.postgres_dsn_sync)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(url=settings.postgres_dsn_sync, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
