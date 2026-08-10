"""Atomic persistence for a refreshed maintainer Cookie in a project .env file."""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

from .errors import ConfigError

_COOKIE_ASSIGNMENT = re.compile(r"^(\s*HIT_JW_COOKIE\s*=).*?(\r?\n|$)", re.MULTILINE)


def _dotenv_quote(value: str) -> str:
    """Encode a Cookie value as a double-quoted dotenv value."""

    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def persist_dotenv_cookie(dotenv_path: Path, cookie: str) -> None:
    """Replace the existing Cookie assignment without changing unrelated .env text."""

    if "\r" in cookie or "\n" in cookie:
        raise ConfigError("refreshed HIT_JW_COOKIE is malformed")

    try:
        original = dotenv_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError("could not read .env for Cookie refresh") from exc

    replacement_count = 0

    def replace_assignment(match: re.Match[str]) -> str:
        nonlocal replacement_count
        replacement_count += 1
        return f"{match.group(1)}{_dotenv_quote(cookie)}{match.group(2)}"

    updated = _COOKIE_ASSIGNMENT.sub(replace_assignment, original, count=1)
    if replacement_count != 1:
        raise ConfigError(".env does not contain a writable HIT_JW_COOKIE assignment")

    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=dotenv_path.parent, delete=False
        ) as temporary_file:
            temporary_file.write(updated)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
            temp_path = Path(temporary_file.name)
        os.replace(temp_path, dotenv_path)
    except OSError as exc:
        raise ConfigError("could not persist refreshed HIT_JW_COOKIE") from exc
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink(missing_ok=True)
