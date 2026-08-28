"""确保 PG 库 coderag_v2 存在（alembic 不会建库）。幂等：存在即跳过。

用法（backend-v2/ 下）：uv run python scripts/ensure_db.py
"""
import sys

import psycopg

sys.stdout.reconfigure(encoding="utf-8")  # GBK 控制台安全

from app.core.config import settings  # noqa: E402


def main() -> None:
    dsn = (
        f"host={settings.postgres_host} port={settings.postgres_port} "
        f"user={settings.postgres_user} password={settings.postgres_password} "
        f"dbname=postgres"
    )
    with psycopg.connect(dsn) as conn:
        conn.autocommit = True  # CREATE DATABASE 不能在事务里
        exists = conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (settings.postgres_db,)
        ).fetchone()
        if exists:
            print(f"[ensure_db] {settings.postgres_db} 已存在，跳过")
            return
        conn.execute(f'CREATE DATABASE "{settings.postgres_db}"')
        print(f"[ensure_db] 已创建 {settings.postgres_db}")


if __name__ == "__main__":
    main()
