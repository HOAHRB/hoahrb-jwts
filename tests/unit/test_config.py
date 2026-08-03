import pytest

from hoa_cli.config import Settings
from hoa_cli.errors import ConfigError


def test_settings_require_cookie() -> None:
    with pytest.raises(ConfigError, match="HIT_JW_COOKIE"):
        Settings.from_env({})


def test_settings_parse_proxy_and_numeric_values() -> None:
    settings = Settings.from_env(
        {
            "HIT_JW_COOKIE": "JSESSIONID=sanitized",
            "HIT_JW_BASE_URL": "http://jwts.hit.edu.cn/",
            "HTTP_PROXY": "http://127.0.0.1:7897",
            "HTTPS_PROXY": "http://127.0.0.1:7897",
            "HIT_JW_TIMEOUT_SECONDS": "12",
            "HIT_JW_DELAY_SECONDS": "0.25",
        }
    )
    assert settings.cookie == "JSESSIONID=sanitized"
    assert settings.proxies == {
        "http": "http://127.0.0.1:7897",
        "https": "http://127.0.0.1:7897",
    }
    assert settings.timeout_seconds == 12
    assert settings.delay_seconds == 0.25
    assert "JSESSIONID=sanitized" not in repr(settings)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("HIT_JW_TIMEOUT_SECONDS", "0"),
        ("HIT_JW_DELAY_SECONDS", "-1"),
        ("HIT_JW_MAX_RETRIES", "-1"),
    ],
)
def test_settings_reject_invalid_numeric_values(key: str, value: str) -> None:
    with pytest.raises(ConfigError):
        Settings.from_env({"HIT_JW_COOKIE": "sanitized", key: value})
