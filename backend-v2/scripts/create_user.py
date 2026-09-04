"""建用户 CLI（M9）：uv run python scripts/create_user.py <username> <password> <role>。

无注册端点——用户只从这里建（无默认密码后门）。role 必须是 roles 表既有行
（迁移 seed：admin/developer/ops/external）。"""
from __future__ import annotations

import sys
from pathlib import Path

# sys.path 自举（允许 ``uv run python scripts/create_user.py`` 直接跑；同 eval_run.py 模式）
_repo_root = Path(__file__).resolve().parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

sys.stdout.reconfigure(encoding="utf-8")  # 中文 Windows GBK 控制台

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.db.models import Role, User  # noqa: E402


def main() -> int:
    if len(sys.argv) != 4:
        print("用法: uv run python scripts/create_user.py <username> <password> <role>")
        return 2
    username, password, role_name = sys.argv[1], sys.argv[2], sys.argv[3]
    eng = create_engine(settings.postgres_dsn_sync)
    try:
        with Session(bind=eng) as s:
            role = s.execute(select(Role).where(Role.name == role_name)).scalars().first()
            if role is None:
                names = list(s.execute(select(Role.name)).scalars())
                print(f"角色不存在: {role_name}（可选: {names}）")
                return 1
            s.add(User(username=username, password_hash=hash_password(password),
                       role_id=role.id))
            s.commit()
    finally:
        eng.dispose()
    print(f"用户已创建: {username}（角色 {role_name}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
