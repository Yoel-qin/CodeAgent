import pytest


@pytest.fixture
def mini_repo_env(monkeypatch, tmp_path):
    """把 settings.repos_root 指到 tmp 下的迷你仓库（Task 2 起 core 工具测试用）。"""
    repo_root = tmp_path / "repos"
    (repo_root / "mini").mkdir(parents=True)
    monkeypatch.setattr("app.core.config.settings.repos_root", str(repo_root))
    return repo_root
