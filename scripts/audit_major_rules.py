"""Read-only source evidence for manually curated major rules; never apply them."""

import json
import os
from dataclasses import asdict
from pathlib import Path

from hoahrb_jwts.client import TeachingSystemClient
from hoahrb_jwts.config import Settings
from hoahrb_jwts.errors import HoahrbJwtsError


def main():
    root = Path(__file__).resolve().parents[1]
    client = TeachingSystemClient(Settings.from_sources(os.environ, root / ".env"))
    client.refresh_cookie()
    catalog = client.get_catalog()
    departments = {item.code: item for item in catalog.departments}
    report = []
    requests = [("2024", "01", {"01182", "01182L"}), ("2024", "09", {"09331"})]
    requests += [
        (year, department, codes)
        for year in ("2023", "2024", "2025", "2026")
        for department, codes in (
            ("35", {"35152", "35158", "35E232", "35E233"}),
            ("25", {"25142", "25145"}),
        )
    ]
    path = root / ".cache/major-rules-audit.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    for year, department, codes in requests:
        if year not in catalog.years:
            continue
        majors = client.get_majors(year, departments[department])
        selected = [item for item in majors if item.code in codes]
        entry = {
            "year": year,
            "department": department,
            "related_source_names": [
                asdict(item)
                for item in majors
                if any(
                    token in item.name
                    for token in ("土木", "俄语", "经济学", "化学工程与工艺", "飞行器设计")
                )
            ],
            "plans": [],
        }
        for major in selected:
            courses = tuple(dict.fromkeys(client.get_plan(major)))
            entry["plans"].append(
                {"major": asdict(major), "courses": [asdict(course) for course in courses]}
            )
            print(
                f"{year}/{major.code} {major.name} [{major.category}] rows={len(courses)}",
                flush=True,
            )
        if department == "01" and len(entry["plans"]) == 2:
            left, right = (item["courses"] for item in entry["plans"])
            entry["identical_rows"] = left == right
            entry["same_row_set"] = {json.dumps(row, sort_keys=True) for row in left} == {
                json.dumps(row, sort_keys=True) for row in right
            }
            print(f"aerospace same row set: {entry['same_row_set']}", flush=True)
        report.append(entry)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved: {path}")


if __name__ == "__main__":
    try:
        main()
    except HoahrbJwtsError as error:
        print(f"error: {error}")
        raise SystemExit(1) from None
