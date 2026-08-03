import pytest

from hoa_cli.config import Settings


@pytest.fixture
def settings() -> Settings:
    return Settings.from_env(
        {
            "HIT_JW_COOKIE": "JSESSIONID=sanitized",
            "HIT_JW_BASE_URL": "http://jwts.hit.edu.cn",
            "HIT_JW_TIMEOUT_SECONDS": "1",
            "HIT_JW_DELAY_SECONDS": "0",
            "HIT_JW_MAX_RETRIES": "0",
        }
    )
