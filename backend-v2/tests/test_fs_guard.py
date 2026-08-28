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


def test_symlink_escape_rejected(mini_repo_env):
    """symlink 指向 repos_root 外部必须被 resolve() 后的 is_relative_to 拒绝。"""
    outside = mini_repo_env.parent / "outside"
    outside.mkdir()
    link = mini_repo_env / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as e:
        pytest.skip(f"symlink unavailable on this platform: {e}")
    with pytest.raises(PathEscapeError):
        resolve_repo_path(mini_repo_env, "linked")
