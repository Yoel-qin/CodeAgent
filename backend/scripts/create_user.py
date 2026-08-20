"""M45 RBAC 用户管理 CLI：创建用户（bcrypt 哈希入库）。

用法（backend/ 下执行）：
    uv run python scripts/create_user.py --username alice --role external
    # --password 缺省交互输入（getpass，不回显）
角色须为内置/库中已存在角色（内置 4 角色：admin/developer/ops/external）。
幂等性：username 唯一约束，重名 → 报错退出（不覆盖）。
"""
from __future__ import annotations

import argparse
import sys

sys.path.insert(0, ".")

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models.auth import Role, User
from app.services.auth_service import hash_password


def create_user(session, username: str, password: str, role_name: str) -> User:
    """纯逻辑（可单测）：校验角色存在 + 用户名未占 → 建 User 并 commit。"""
    role = session.execute(
        select(Role).where(Role.name == role_name)).scalars().first()
    if role is None:
        raise SystemExit(f"角色不存在：{role_name}（内置：admin/developer/ops/external）")
    dup = session.execute(
        select(User).where(User.username == username)).scalars().first()
    if dup is not None:
        raise SystemExit(f"用户名已存在：{username}")
    u = User(username=username, password_hash=hash_password(password),
             role_id=role.id, is_active=True)
    session.add(u)
    session.commit()
    return u


def main() -> None:
    ap = argparse.ArgumentParser(description="创建 CodeRAG 用户（M45 RBAC）")
    ap.add_argument("--username", required=True)
    ap.add_argument("--password", default=None, help="缺省交互输入（不回显）")
    ap.add_argument("--role", required=True, choices=["admin", "developer", "ops", "external"])
    args = ap.parse_args()

    import getpass
    password = args.password or getpass.getpass("密码：")
    if len(password) < 8:
        raise SystemExit("密码至少 8 位")

    engine = create_engine(settings.database_url_sync)
    with Session(engine) as session:
        u = create_user(session, args.username, password, args.role)
        print(f"已创建用户 {u.username}（角色 {args.role}，id={u.id}）")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
