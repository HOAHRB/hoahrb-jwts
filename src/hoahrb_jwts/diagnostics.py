"""Bounded authentication diagnostics; never record credentials or response bodies."""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import urlsplit

logger = logging.getLogger("hoahrb_jwts.auth")


def configure_auth_logging(path: Path = Path("data/jwts-auth.log")) -> None:
    if any(isinstance(handler, RotatingFileHandler) for handler in logger.handlers):
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(path, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


def endpoint(url) -> str:
    parsed = urlsplit(str(url))
    # Do not expose tickets, query arguments, or identifiers in unknown paths.
    known = {
        "/loginCAS",
        "/login",
        "/logout",
        "/zxjh/queryZxkc",
        "/cxcyXmsbjcgl/queryCxdgxs",
        "/authserver/login",
        "/authserver",
        "/",
    }
    host = (
        parsed.hostname
        if parsed.hostname in {"jwts.hit.edu.cn", "ids.hit.edu.cn"}
        else "other-host"
    )
    path = parsed.path if parsed.path in known else "/<other>"
    return f"{host}{path}"
