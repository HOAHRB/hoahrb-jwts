#!/usr/bin/env python3
"""Update grades_summary.json from repos-management/grades_summary.toml.

Output schema:
- Top-level is a mapping: { <course_code>: { <entry_key>: <grade_items> } }
- entry_key is one of:
  - default
  - 入学年份_default (e.g. 2024_default)
  - 入学年份_专业 (e.g. 2021_自动化)
- grade_items is a flat ordered list: [ {"name": <str>, "percent": <str|null>} ... ]

Notes:
- We intentionally do NOT output course_name / grades wrapper / raw / note.
- For unparseable segments, percent is null and name keeps the original text.
"""

from __future__ import annotations

import argparse
import json
import re
import tomllib
import urllib.request
from collections.abc import Sequence
from pathlib import Path
from typing import Any

SOURCE_URL = (
    "https://raw.githubusercontent.com/HITSZ-OpenAuto/repos-management/main/grades_summary.toml"
)
DEFAULT_DATA_DIR = Path(__file__).resolve().parents[1] / "src" / "hoahrb_jwts" / "data"
PERCENT_RE = re.compile(r"(\d+%)")


def normalize_entry_key(course_variant: str) -> str:
    """Normalize TOML variant keys into one of:

    - default
    - 入学年份_default (e.g. 2024_default)
    - 入学年份_专业 (e.g. 2022_自动化)

    Notes:
    - Keys like "23级" / "21级自动化" are converted to 2023_* / 2021_*.
    - A few variants in upstream data are not enrollment-year based (e.g. teacher names).
      We currently map them into "default" (see PR discussion if this needs refinement).
    """

    if course_variant == "default":
        return "default"

    # Plain year.
    if re.fullmatch(r"\d{4}", course_variant):
        return f"{course_variant}_default"

    # Patterns like "23级" / "21级自动化".
    m = re.fullmatch(r"(?P<yy>\d{2})级(?P<rest>.*)", course_variant)
    if m:
        year = f"20{m.group('yy')}"
        rest = m.group("rest").strip()
        if not rest:
            return f"{year}_default"
        return f"{year}_{rest}"

    # Non-year variants in the upstream file.
    return "__DROP__"


def parse_grade(raw: str) -> list[dict[str, Any]]:
    """Parse a grade string into a flat list of items.

    Output item format:
      {"name": <str>, "percent": <str | None>}

    For unparseable segments, percent is None and name preserves the text.
    """

    raw_norm = raw.replace("＋", "+")

    # Split on '+' only when it likely separates components.
    # - "... 30%+ ..." (plus right after a percent)
    # - "... + ..." (plus surrounded by whitespace)
    # This avoids splitting merged names like "作业+实验 50%".
    split_re = r"(?<=%)\s*\+\s*|\s+\+\s+"
    parts = [p.strip() for p in re.split(split_re, raw_norm) if p.strip()]

    items: list[dict[str, Any]] = []

    for part in parts or [raw_norm.strip()]:
        m = PERCENT_RE.search(part)
        if not m:
            items.append({"name": part})
            continue

        percent = m.group(1)
        name = part[: m.start()].strip() or part
        items.append({"name": name, "percent": percent})

    return items


def extract_grade_strings(obj: Any) -> list[str]:
    """Collect all grade strings found under a variant block.

    Upstream TOML shapes include:
    - variant -> default -> {grade: "..."}
    - variant -> default -> default -> {grade: "..."}
    """

    out: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "grade" and isinstance(v, str):
                out.append(v)
            else:
                out.extend(extract_grade_strings(v))
    elif isinstance(obj, list):
        for v in obj:
            out.extend(extract_grade_strings(v))
    return out


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="将 repos-management 的成绩构成转换为 hoa-major-data 使用的 JSON"
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help="输出目录；默认 src/hoahrb_jwts/data",
    )
    args = parser.parse_args(argv)

    raw_bytes = urllib.request.urlopen(SOURCE_URL).read()
    toml_data = tomllib.loads(raw_bytes.decode("utf-8"))

    grades = toml_data.get("grades", {})
    out_grades: dict[str, Any] = {}

    for course_code, course_obj in grades.items():
        if not isinstance(course_obj, dict):
            continue

        entries: dict[str, Any] = {}
        dropped_grade_strings: list[str] = []

        for variant_key, variant_obj in course_obj.items():
            if variant_key == "course_name":
                continue  # explicitly removed per repo convention

            grade_strings = extract_grade_strings(variant_obj)
            if not grade_strings:
                continue

            entry_key = normalize_entry_key(str(variant_key))
            if entry_key == "__DROP__":
                # Per request: drop non-year variants, but we may still use one of their values
                # as a representative if nothing else exists for this course.
                dropped_grade_strings.extend(grade_strings)
                continue

            # If multiple grade strings appear for the same entry_key, pick the first.
            first = grade_strings[0]
            grade_items = parse_grade(first)

            # If we see the same entry_key again, keep the first.
            entries.setdefault(entry_key, grade_items)

        # If the course had only non-year variants, keep ONE representative under "default".
        if not entries and dropped_grade_strings:
            entries["default"] = parse_grade(dropped_grade_strings[0])

        if entries:
            out_grades[course_code] = entries

    out = out_grades

    output_path = args.data_dir / "grades_summary.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
