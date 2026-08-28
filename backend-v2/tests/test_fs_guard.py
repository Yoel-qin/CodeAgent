import pytest

from app.core.fs_guard import PathEscapeError, resolve_repo_path


def test_valid_repo_only(mini_repo_env):
    p = resolve_repo_path(mini_repo_env, "mini")
    assert p == mini_repo_env.resolve() / "mini"


def test_valid_rel_path(mini_repo_env):
    p = resolve_repo_path(mini_repo_env, "mini", "com/example/broker/CommitLog.java")
    assert p.name == "CommitLog.java"


@pytest.mark.parametrize(
    "repo,rel",
    [
        ("..", ""),
        ("../..", ""),
        ("mini", "../escape.java"),
        ("mini", "../../escape.java"),
        ("mini/inner", ""),          # repo 必须单段
        ("", ""),                    # repo 不能为空
    ],
)
def test_escape_rejected(mini_repo_env, repo, rel):
    with pytest.raises(PathEscapeError):
        resolve_repo_path(mini_repo_env, repo, rel)


@pytest.mark.parametrize("rel", ["/etc/passwd", "C:/Windows/system32", "C:\\Windows\\system32"])
def test_absolute_rejected(mini_repo_env, rel):
    with pytest.raises(PathEscapeError):
        resolve_repo_path(mini_repo_env, "mini", rel)
