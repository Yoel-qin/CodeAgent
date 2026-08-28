from app.core.reader import list_directory, read_file

FIX = "tests/fixtures"


def test_read_file_default_window_500(tmp_path):
    f = tmp_path / "repos" / "mini" / "big.txt"
    f.parent.mkdir(parents=True)
    f.write_text("\n".join(f"line{i}" for i in range(1, 601)), encoding="utf-8")
    res = read_file(tmp_path / "repos", "mini", "big.txt")
    assert res["total_lines"] == 600
    assert res["start_line"] == 1 and res["end_line"] == 500
    assert res["truncated"] is True
    assert res["content"].splitlines()[0] == "line1"
    assert res["content"].splitlines()[-1] == "line500"


def test_read_file_explicit_range(tmp_path):
    res = read_file(FIX, "mini_repo", "com/example/broker/CommitLog.java", 10, 12)
    assert res["start_line"] == 10 and res["end_line"] == 12
    assert len(res["content"].splitlines()) == 3
    assert res["truncated"] is False


def test_read_file_range_clamped_to_500(tmp_path):
    f = tmp_path / "repos" / "mini" / "big.txt"
    f.parent.mkdir(parents=True)
    f.write_text("\n".join(str(i) for i in range(1, 1001)), encoding="utf-8")
    res = read_file(tmp_path / "repos", "mini", "big.txt", 5, 900)
    assert res["end_line"] == 5 + 499
    assert res["truncated"] is True


def test_read_file_missing():
    res = read_file(FIX, "mini_repo", "no/such/File.java")
    assert "error" in res


def test_read_directory_error():
    res = read_file(FIX, "mini_repo", "com")
    assert "error" in res


def test_list_directory_default_depth():
    res = list_directory(FIX, "mini_repo")
    names = [(e["depth"], e["name"]) for e in res["entries"]]
    assert (0, "com") in names and (0, "README.md") in names
    dirs_first = [e["type"] for e in res["entries"]]
    assert dirs_first == sorted(dirs_first, key=lambda t: 0 if t == "dir" else 1)  # dir 在前


def test_list_directory_depth_clamp():
    res = list_directory(FIX, "mini_repo", "com", depth=99)
    assert res["entries"], "应至少列出 com 下内容"
    assert max(e["depth"] for e in res["entries"]) <= 3


def test_list_directory_escape():
    res = list_directory(FIX, "mini_repo", "../..")
    assert "error" in res


def test_read_file_negative_end_line_error():
    res = read_file(FIX, "mini_repo", "com/example/broker/CommitLog.java", 1, -3)
    assert "error" in res and "end_line" in res["error"]


def test_read_file_zero_start_line_error():
    res = read_file(FIX, "mini_repo", "com/example/broker/CommitLog.java", 0, 5)
    assert "error" in res and "start_line" in res["error"]


def test_read_file_inverted_range_error():
    res = read_file(FIX, "mini_repo", "com/example/broker/CommitLog.java", 10, 5)
    assert "error" in res and "exceeds" in res["error"]


def test_list_directory_depth_zero_floors_to_one():
    """depth=0 视为 1（文档化语义）：只列当前层，不递归。"""
    res = list_directory(FIX, "mini_repo", "com", depth=0)
    assert res["entries"]
    assert all(e["depth"] == 1 for e in res["entries"])
