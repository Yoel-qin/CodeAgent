from app.core.config import settings


def test_v2_env_overrides_root_env():
    """防回归：pydantic-settings v2 env_file last-wins。

    backend-v2/.env 设 POSTGRES_DB=coderag_v2，根 .env 设 coderag。
    若 env_file 顺序写反（根 .env 后读），此断言失败。
    无 .env 的机器上默认值恰好也是 coderag_v2，所以该断言
    只在「根 .env 存在且 env_file 顺序写反」这一 bug 形态下失败。
    """
    assert settings.postgres_db == "coderag_v2"
