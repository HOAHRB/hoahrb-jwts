from pathlib import Path

import pytest

from hoahrb_jwts.checkpoint import CrawlCheckpoint
from hoahrb_jwts.errors import ParseError


def test_invalid_responses_are_not_saved_and_corrupt_cache_is_refetched(tmp_path: Path) -> None:
    checkpoint = CrawlCheckpoint(tmp_path / "cache", tmp_path / "data", ("2023",))

    def reject(text):
        raise ParseError("malformed response")

    with pytest.raises(ParseError):
        checkpoint.read_or_fetch("key", lambda: "bad", reject, lambda: None)
    assert not list(checkpoint.directory.glob("*.json"))
    assert checkpoint.read_or_fetch("key", lambda: "good", str, lambda: None) == "good"
    path = next(checkpoint.directory.glob("*.json"))
    path.write_text("broken JSON", encoding="utf-8")
    assert checkpoint.read_or_fetch("key", lambda: "repaired", str, lambda: None) == "repaired"


def test_checkpoints_are_scoped_to_output_and_years(tmp_path: Path) -> None:
    root = tmp_path / "cache"
    first = CrawlCheckpoint(root, tmp_path / "a", ("2023", "2024"))
    reordered = CrawlCheckpoint(root, tmp_path / "a", ("2024", "2023"))
    other_year = CrawlCheckpoint(root, tmp_path / "a", ("2023",))
    other_output = CrawlCheckpoint(root, tmp_path / "b", ("2023", "2024"))
    assert first.directory == reordered.directory
    assert len({first.directory, other_year.directory, other_output.directory}) == 3
