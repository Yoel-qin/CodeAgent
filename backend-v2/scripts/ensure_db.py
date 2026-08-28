"""确保 PG 库 coderag_v2 存在（alembic 不会建库）。幂等：存在即跳过。

用法（backend-v2/ 下）：uv run python scripts/ensure_db.py
"""
import os
import sys

import psycopg

sys.stdout.reconfigure(encoding="utf-8")  # GBK 控制台安全

from app.core.config import settings  # noqa: E402

TARGET_DB = "coderag_v2"


def assert_compatible(db_name: str, alembic_version: str | None) -> str | None:
    """返回错误消息（需中止），None 表示兼容。"""
    if db_name != TARGET_DB:
        return (
            f"错误：目标库为 '{db_name}'，期望 '{TARGET_DB}'。\n"
            f"请创建 backend-v2/.env 并设置 POSTGRES_DB={TARGET_DB}。\n"
            f"（设 ENSURE_DB_ALLOW_ANY=1 可跳过此检查）"
        )
    if alembic_version is not None and not alembic_version.startswith("v2_"):
        return (
            f"错误：{db_name} 的 alembic_version='{alembic_version}' 不是 v2 迁移链。\n"
            f"此库属于其他项目，请使用 POSTGRES_DB={TARGET_DB} 指向正确库。"
        )
    return None


def main() -> None:
    allow_any = os.getenv("ENSURE_DB_ALLOW_ANY", "").strip() in ("1", "true", "yes")
    if not allow_any:
        err = assert_compatible(settings.postgres_db, None)
        if err:
            print(f"[ensure_db] {err}", file=sys.stderr)
            sys.exit(1)

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
            # 检查已有库是否属于 v2 迁移链
            if not allow_any:
                try:
                    ver_row = psycopg.connect(
                        dsn.replace("dbname=postgres", f"dbname={settings.postgres_db}")
                    ).execute(
                        "SELECT version_num FROM alembic_version LIMIT 1"
                    ).fetchone()
                    alembic_version = ver_row[0] if ver_row else None
                except Exception:
                    alembic_version = None  # 表不存在等情况，让 alembic 自己报
                err = assert_compatible(settings.postgres_db, alembic_version)
                if err:
                    print(f"[ensure_db] {err}", file=sys.stderr)
                    sys.exit(1)
            print(f"[ensure_db] {settings.postgres_db} 已存在，跳过")
            return
        conn.execute(f'CREATE DATABASE "{settings.postgres_db}"')
        print(f"[ensure_db] 已创建 {settings.postgres_db}")


if __name__ == "__main__":
    main()
