"""fixture 仓库自检：锚点文件/符号存在，后续工具测试依赖它们。"""
from pathlib import Path

FIXTURE = Path(__file__).parent / "fixtures" / "mini_repo"


def test_fixture_files_exist():
    for rel in (
        "com/example/broker/CommitLog.java",
        "com/example/broker/FlushService.java",
        "com/example/client/MessageConsumer.java",
        "com/example/client/RetryPolicy.java",
        "com/example/common/utils/PathUtil.java",
        "README.md",
    ):
        assert (FIXTURE / rel).is_file(), rel


def test_fixture_anchor_symbols():
    text = (FIXTURE / "com/example/broker/CommitLog.java").read_text(encoding="utf-8")
    assert "MAX_RETRY_TIMES" in text
    assert "class CommitLog" in text
