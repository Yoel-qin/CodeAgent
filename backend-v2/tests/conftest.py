import pytest as _pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.config import settings


@_pytest.fixture(scope="session")
def pg_engine():
    return create_engine(settings.postgres_dsn_sync)


@_pytest.fixture
def session(pg_engine):
    conn = pg_engine.connect()
    tx = conn.begin()
    s = Session(bind=conn, expire_on_commit=False)
    yield s
    s.close()
    tx.rollback()
    conn.close()


@_pytest.fixture
def mini_repo_env(monkeypatch, tmp_path):
    """把 settings.repos_root 指到 tmp 下的迷你仓库（Task 2 起 core 工具测试用）。"""
    repo_root = tmp_path / "repos"
    (repo_root / "mini").mkdir(parents=True)
    monkeypatch.setattr("app.core.config.settings.repos_root", str(repo_root))
    return repo_root
