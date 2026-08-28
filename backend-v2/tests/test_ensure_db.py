from scripts.ensure_db import assert_compatible


def test_compatible_v2_db_no_version():
    assert assert_compatible("coderag_v2", None) is None


def test_compatible_v2_db_v2_version():
    assert assert_compatible("coderag_v2", "v2_001") is None


def test_rejects_wrong_db_name():
    err = assert_compatible("coderag", None)
    assert err is not None
    assert "coderag_v2" in err


def test_rejects_v1_alembic_version():
    err = assert_compatible("coderag_v2", "b7e2d09af3c1")
    assert err is not None
    assert "v2 迁移链" in err
