"""Read-only comparison of Future Technology College plan suffixes."""

import json
import os
import re
from collections import defaultdict
from dataclasses import asdict
from itertools import combinations
from pathlib import Path

from hoahrb_jwts.client import TeachingSystemClient
from hoahrb_jwts.config import Settings
from hoahrb_jwts.errors import HoahrbJwtsError


def main():
    root = Path(__file__).resolve().parents[1]
    output = root / ".cache" / "future-variants-review.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    client = TeachingSystemClient(Settings.from_sources(os.environ, root / ".env"))
    client.refresh_cookie()
    catalog = client.get_catalog()
    department = next(item for item in catalog.departments if item.code == "52")
    report = {"catalog_years": catalog.years, "major_lists": {}, "plans": [], "comparisons": []}

    def save():
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    for year in sorted(year for year in catalog.years if "2022" <= year <= "2026"):
        majors = client.get_majors(year, department)
        report["major_lists"][year] = [asdict(major) for major in majors]
        groups = defaultdict(list)
        for major in majors:
            match = re.fullmatch(r"52WL(\d+)([A-Z]*)", major.code)
            if match:
                groups[match[1]].append(major)
        print(
            f"year={year} majors={len(majors)} paired_groups={sum(len(group) > 1 for group in groups.values())}",
            flush=True,
        )
        for base, group in sorted(groups.items()):
            if len(group) < 2:
                continue
            plans = {}
            for major in group:
                courses = tuple(dict.fromkeys(client.get_plan(major)))
                plans[major.code] = courses
                report["plans"].append(
                    {"major": asdict(major), "courses": [asdict(course) for course in courses]}
                )
                save()
                print(f"  {major.code} {major.name}: rows={len(courses)}", flush=True)
            for left, right in combinations(group, 2):
                left_courses, right_courses = plans[left.code], plans[right.code]
                shared_codes = {course.code for course in left_courses} & {
                    course.code for course in right_courses
                }
                report["comparisons"].append(
                    {
                        "year": year,
                        "base": base,
                        "codes": [left.code, right.code],
                        "rows": [len(left_courses), len(right_courses)],
                        "shared_codes": len(shared_codes),
                        "identical_rows": len(set(left_courses) & set(right_courses)),
                        "unique_codes": {
                            major.code: [
                                asdict(course)
                                for course in plans[major.code]
                                if course.code not in shared_codes
                            ]
                            for major in (left, right)
                        },
                        "changed_shared_rows": {
                            major.code: [
                                asdict(course)
                                for course in plans[major.code]
                                if course.code in shared_codes
                                and course not in set(plans[other.code])
                            ]
                            for major, other in ((left, right), (right, left))
                        },
                    }
                )
                save()
        save()
    print(f"Saved {len(report['comparisons'])} comparisons: {output}")


if __name__ == "__main__":
    try:
        main()
    except HoahrbJwtsError as error:
        print(f"error: {error}")
        raise SystemExit(1) from None
