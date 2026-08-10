from pathlib import Path

import pytest

from hoahrb_jwts.cookie_store import persist_dotenv_cookie
from hoahrb_jwts.errors import ConfigError


def test_persist_dotenv_cookie_replaces_only_cookie_assignment(tmp_path: Path) -> None:
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text(
        "# Maintainer-only session\n"
        "HIT_JW_COOKIE=JSESSIONID=old\n"
        "HTTP_PROXY=http://127.0.0.1:7890\n",
        encoding="utf-8",
    )

    persist_dotenv_cookie(dotenv_path, "JSESSIONID=new; ROUTE=campus")

    assert dotenv_path.read_text(encoding="utf-8") == (
        "# Maintainer-only session\n"
        'HIT_JW_COOKIE="JSESSIONID=new; ROUTE=campus"\n'
        "HTTP_PROXY=http://127.0.0.1:7890\n"
    )


def test_persist_dotenv_cookie_keeps_original_file_when_replacement_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    dotenv_path = tmp_path / ".env"
    original = "# keep this\nHIT_JW_COOKIE=JSESSIONID=old\n"
    dotenv_path.write_text(original, encoding="utf-8")

    monkeypatch.setattr(
        "hoahrb_jwts.cookie_store.os.replace", lambda *args: (_ for _ in ()).throw(OSError())
    )

    with pytest.raises(ConfigError, match="persist"):
        persist_dotenv_cookie(dotenv_path, "JSESSIONID=new")

    assert dotenv_path.read_text(encoding="utf-8") == original
