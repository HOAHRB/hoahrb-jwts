"""Locate and fast-forward the adjacent major-data checkout."""

import subprocess
from pathlib import Path

from .config import DEFAULT_WORK_DIR, SOURCE_CHECKOUT
from .errors import PublicationError

DEFAULT_MAJOR_DATA_DIR = (
    DEFAULT_WORK_DIR.parent if SOURCE_CHECKOUT else DEFAULT_WORK_DIR
) / "hoa-major-data"
MAJOR_DATA_URL = "https://github.com/HOAHRB/hoa-major-data.git"


def _git(path: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(path), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PublicationError("major-data Git operation failed or timed out") from exc
    if result.returncode:
        raise PublicationError(
            f"major-data git {args[0]} failed; inspect the checkout before retrying"
        )
    return result.stdout.strip()


def sync_major_checkout(path: Path = DEFAULT_MAJOR_DATA_DIR) -> Path:
    path = Path(path)
    if not path.exists():
        _git(path.parent, "clone", MAJOR_DATA_URL, str(path))
    if not (path / ".git").exists():
        raise PublicationError(f"major-data directory is not a Git checkout: {path}")
    if Path(_git(path, "rev-parse", "--show-toplevel")).resolve() != path.resolve():
        raise PublicationError("major-data path must be the repository root")
    if not _git(path, "branch", "--show-current"):
        raise PublicationError("major-data is detached; switch to a named branch first")
    remote = _git(path, "remote", "get-url", "origin")
    if remote.removesuffix(".git").rstrip("/") not in {
        "https://github.com/HOAHRB/hoa-major-data",
        "git@github.com:HOAHRB/hoa-major-data",
    }:
        raise PublicationError("major-data origin is unexpected; sync it manually or use --no-sync")
    _git(path, "fetch", "origin", "main")
    ahead, behind = map(
        int,
        _git(
            path, "rev-list", "--left-right", "--count", "HEAD...refs/remotes/origin/main"
        ).split(),
    )
    if behind:
        if ahead or _git(path, "status", "--porcelain"):
            raise PublicationError(
                "major-data has incoming updates and local changes; integrate them before retrying (nothing discarded)"
            )
        _git(path, "merge", "--ff-only", "refs/remotes/origin/main")
    return path
