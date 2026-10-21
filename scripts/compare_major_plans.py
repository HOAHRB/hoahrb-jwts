"""Read-only diagnosis of same-name plans using the normal credential loader."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict
from pathlib import Path

from hoahrb_jwts.client import TeachingSystemClient
from hoahrb_jwts.config import Settings
from hoahrb_jwts.errors import HoahrbJwtsError, ValidationError
from hoahrb_jwts.models import DiscoveredPlan
from hoahrb_jwts.normalize import normalize_plan


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", default="2023")
    parser.add_argument("--department", default="25")
    parser.add_argument("--codes", nargs=2, default=["25142", "25145"])
    parser.add_argument("--dotenv", type=Path, default=Path(__file__).resolve().parents[1] / ".env")
    args = parser.parse_args()
    try:
        settings = Settings.from_sources(os.environ, args.dotenv)
        client = TeachingSystemClient(settings)
        client.refresh_cookie()
        catalog = client.get_catalog()
        department = next(
            (item for item in catalog.departments if item.code == args.department), None
        )
        if department is None:
            raise ValidationError("requested department is absent from the source catalog")
        majors = client.get_majors(args.year, department)
        selected = [major for major in majors if major.code in args.codes]
        if {major.code for major in selected} != set(args.codes):
            raise ValidationError("requested major codes are absent from the source major list")
        source = {}
        normalized = {}
        for major in selected:
            courses = tuple(dict.fromkeys(client.get_plan(major)))
            source[major.code] = courses
            normalized[major.code] = normalize_plan(DiscoveredPlan(major, courses)).courses
        left, right = args.codes
        left_rows, right_rows = set(source[left]), set(source[right])
        left_codes = {course.code for course in left_rows}
        right_codes = {course.code for course in right_rows}

        def rows(courses):
            return sorted(
                (asdict(course) for course in courses),
                key=lambda row: json.dumps(row, sort_keys=True),
            )

        result = {
            "year": args.year,
            "department": asdict(department),
            "majors_from_source": [asdict(major) for major in selected],
            "source_course_counts": {code: len(courses) for code, courses in source.items()},
            "source_rows_equal": left_rows == right_rows,
            "normalized_rows_equal": set(normalized[left]) == set(normalized[right]),
            "course_codes_only": {
                left: sorted(left_codes - right_codes),
                right: sorted(right_codes - left_codes),
            },
            "different_rows": {
                left: rows(left_rows - right_rows),
                right: rows(right_rows - left_rows),
            },
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except HoahrbJwtsError as error:
        # Only domain error messages; never dump requests, headers or Settings.
        print(f"error: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
