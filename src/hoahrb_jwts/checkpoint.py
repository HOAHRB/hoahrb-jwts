"""Atomic, per-request checkpoints for an unfinished curriculum crawl."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from .errors import ParseError, PublicationError

T = TypeVar("T")


class CrawlCheckpoint:
    def __init__(self, root: Path, data_dir: Path, years: tuple[str, ...]) -> None:
        identity = json.dumps([str(data_dir.resolve()), sorted(set(years))])
        self.directory = root / hashlib.sha256(identity.encode()).hexdigest()

    def read_or_fetch(
        self,
        key: str,
        fetch: Callable[[], str],
        parse: Callable[[str], T],
        on_hit: Callable[[], None],
    ) -> T:
        path = self.directory / (hashlib.sha256(key.encode()).hexdigest() + ".json")
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
            if cached["version"] != 1 or not isinstance(cached["text"], str):
                raise ValueError("invalid checkpoint")
            result = parse(cached["text"])
        except (OSError, ValueError, KeyError, TypeError, ParseError):
            pass
        else:
            on_hit()
            return result

        body = fetch()
        result = parse(body)
        temporary_path: Path | None = None
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(dir=self.directory, suffix=".tmp")
            temporary_path = Path(name)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump({"version": 1, "text": body}, handle, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, path)
        except OSError as exc:
            raise PublicationError("could not save crawl checkpoint") from exc
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
        return result

    def clear(self) -> None:
        try:
            for path in self.directory.glob("*.json"):
                path.unlink()
        except OSError as exc:
            raise PublicationError("could not clear crawl checkpoint") from exc
