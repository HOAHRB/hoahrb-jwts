"""Load and validate the maintainer-owned major-rules TOML file."""

from dataclasses import dataclass
from functools import cache
from pathlib import Path

import toml


@dataclass(frozen=True)
class MajorRule:
    years: tuple[str, ...]
    code: str
    publish: bool = True
    name: str | None = None
    reason: str = ""
    category: str | None = None
    filename_name: str | None = None


@cache
def _rules_by_id() -> dict[tuple[str, str], MajorRule]:
    payload = toml.loads(Path(__file__).with_name("major_rules.toml").read_text(encoding="utf-8"))
    rules: dict[tuple[str, str], MajorRule] = {}
    for item in payload.get("rules", []):
        raw_years = item["year"]
        years = (raw_years,) if isinstance(raw_years, (int, str)) else tuple(raw_years)
        rule = MajorRule(
            years=tuple(str(year) for year in years),
            code=item["code"],
            publish=item.get("publish", True),
            name=item.get("name"),
            reason=item.get("reason", ""),
            category=item.get("category"),
            filename_name=item.get("filename_name"),
        )
        rules.update({(year, rule.code): rule for year in rule.years})
    return rules


def rule_for(year: str, code: str) -> MajorRule | None:
    rules = _rules_by_id()
    return rules.get((year, code)) or rules.get(("*", code))
