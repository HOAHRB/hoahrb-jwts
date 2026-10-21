"""Review recent economics second-degree plans without applying publication rules."""

import json
import os
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from hoahrb_jwts.client import TeachingSystemClient
from hoahrb_jwts.config import Settings
from hoahrb_jwts.errors import HoahrbJwtsError
from hoahrb_jwts.models import DiscoveredPlan
from hoahrb_jwts.normalize import normalize_plan


def main():
    root = Path(__file__).resolve().parents[1]
    client = TeachingSystemClient(Settings.from_sources(os.environ, root / ".env"))
    client.refresh_cookie()
    catalog = client.get_catalog()
    years = sorted(catalog.years, reverse=True)[:5]
    department = next(item for item in catalog.departments if item.code == "35")
    report = []
    details = []
    for year in years:
        selected = [
            major
            for major in client.get_majors(year, department)
            if major.code in {"35E232", "35E233"}
        ]
        plans = {}
        entry = {"year": year, "majors": []}
        for major in selected:
            courses = tuple(dict.fromkeys(client.get_plan(major)))
            plans[major.code] = courses
            entry["majors"].append(
                {
                    **asdict(major),
                    "rows": len(courses),
                    "unique_codes": len({course.code for course in courses}),
                    "credits_by_nature": {
                        nature: sum(
                            float(course.credit)
                            for course in courses
                            if (course.nature or "未注明") == nature
                        )
                        for nature in sorted({course.nature or "未注明" for course in courses})
                    },
                    "schedule_rows": dict(
                        Counter(f"{course.academic_year}/{course.term}" for course in courses)
                    ),
                }
            )
            details.append(
                {"major": asdict(major), "courses": [asdict(course) for course in courses]}
            )
        if len(plans) == 2:
            left, right = plans["35E232"], plans["35E233"]
            left_set, right_set = set(left), set(right)
            entry["source_rows_equal"] = left_set == right_set
            if left and right:
                entry["normalized_rows_equal"] = set(
                    normalize_plan(DiscoveredPlan(selected[0], left)).courses
                ) == set(normalize_plan(DiscoveredPlan(selected[1], right)).courses)
            entry["source_subset"] = {
                "232_in_233": left_set <= right_set,
                "233_in_232": right_set <= left_set,
            }
            entry["different_rows"] = {
                "35E232": [asdict(course) for course in left if course not in right_set],
                "35E233": [asdict(course) for course in right if course not in left_set],
            }
        report.append(entry)
        print(json.dumps(entry, ensure_ascii=False), flush=True)
    output = root / ".cache" / "economics-review.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps({"review": report, "details": details}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Saved report: {output}")


if __name__ == "__main__":
    try:
        main()
    except HoahrbJwtsError as error:
        print(f"error: {error}")
        raise SystemExit(1) from None
