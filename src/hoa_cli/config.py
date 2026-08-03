"""Runtime configuration for the maintainer-run HIT crawler."""

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from .errors import ConfigError

# Keep the original HITSZ CLI storage location for callers that omit --data-dir.
DEFAULT_DATA_DIR = Path(__file__).parent / "data"


@dataclass(frozen=True)
class Settings:
    """Immutable settings loaded from an explicitly supplied environment."""

    base_url: str
    cookie: str = field(repr=False)
    proxies: dict[str, str]
    timeout_seconds: float
    delay_seconds: float
    max_retries: int

    @classmethod
    def from_env(cls, environ: Mapping[str, str] = os.environ) -> "Settings":
        cookie = environ.get("HIT_JW_COOKIE", "").strip()
        if not cookie:
            raise ConfigError("HIT_JW_COOKIE is required")

        base_url = environ.get("HIT_JW_BASE_URL", "http://jwts.hit.edu.cn").strip().rstrip("/")
        if not base_url:
            raise ConfigError("HIT_JW_BASE_URL must not be empty")

        try:
            timeout_seconds = float(environ.get("HIT_JW_TIMEOUT_SECONDS", "20"))
            delay_seconds = float(environ.get("HIT_JW_DELAY_SECONDS", "0.2"))
            max_retries = int(environ.get("HIT_JW_MAX_RETRIES", "3"))
        except ValueError as exc:
            raise ConfigError(
                "HIT_JW_TIMEOUT_SECONDS, HIT_JW_DELAY_SECONDS, and HIT_JW_MAX_RETRIES must be numeric"
            ) from exc

        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ConfigError("HIT_JW_TIMEOUT_SECONDS must be positive")
        if not math.isfinite(delay_seconds) or delay_seconds < 0:
            raise ConfigError("HIT_JW_DELAY_SECONDS must not be negative")
        if max_retries < 0:
            raise ConfigError("HIT_JW_MAX_RETRIES must not be negative")

        proxies = {
            scheme: value
            for scheme, key in (("http", "HTTP_PROXY"), ("https", "HTTPS_PROXY"))
            if (value := environ.get(key, "").strip())
        }
        return cls(
            base_url=base_url,
            cookie=cookie,
            proxies=proxies,
            timeout_seconds=timeout_seconds,
            delay_seconds=delay_seconds,
            max_retries=max_retries,
        )
