from dataclasses import replace

import pytest

from hoahrb_jwts.config import Settings


@pytest.fixture
def settings() -> Settings:
    return replace(Settings.from_env(
        {
            "HIT_JW_COOKIE": "JSESSIONID=sanitized",
            "HIT_JW_BASE_URL": "http://jwts.hit.edu.cn",
            "HIT_JW_TIMEOUT_SECONDS": "1",
            "HIT_JW_DELAY_SECONDS": "0",
            "HIT_JW_MAX_RETRIES": "0",
        }
    ), cookie="JSESSIONID=sanitized", cookie_file=None)
