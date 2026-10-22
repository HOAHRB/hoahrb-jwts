import pytest

from hoahrb_jwts.config import Settings
from hoahrb_jwts.errors import ConfigError


def test_missing_credentials_are_valid_for_first_browser_login(tmp_path):
    settings = Settings.from_sources({}, tmp_path / ".env")
    assert settings.cookie == ""
    assert settings.cas_cookie == ""
    assert settings.cookie_file == tmp_path / ".env.cookies.json"


def test_dotenv_and_environment_cookie_headers_are_ignored(tmp_path):
    path = tmp_path / ".env"
    path.write_text("HIT_JW_COOKIE=legacy\nHIT_JW_CAS_COOKIE=legacy\nHIT_JW_TIMEOUT_SECONDS=12\n")
    settings = Settings.from_sources(
        {"HIT_JW_COOKIE": "process", "HIT_JW_CAS_COOKIE": "process"}, path
    )
    assert settings.cookie == settings.cas_cookie == ""
    assert settings.timeout_seconds == 12
    assert settings.cookie_file == tmp_path / ".env.cookies.json"


def test_session_path_and_proxy_configuration(tmp_path):
    settings = Settings.from_env(
        {
            "HIT_JW_SESSION_FILE": str(tmp_path / "session.json"),
            "HTTP_PROXY": "http://localhost:7890",
        }
    )
    assert settings.cookie_file == tmp_path / "session.json"
    assert settings.proxies == {"http": "http://localhost:7890"}


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
