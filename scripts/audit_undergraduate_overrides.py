"""Inspect raw labels for existing undergraduate overrides without applying them."""

import json
import os
from pathlib import Path

from hoahrb_jwts.client import TeachingSystemClient
from hoahrb_jwts.config import Settings
from hoahrb_jwts.errors import HoahrbJwtsError
from hoahrb_jwts.parsers import parse_major_list

CODES = {
    "01044",
    "260126Q01",
    "260226Q02",
    "260326Q03",
    "260426Q04",
    "260726Q05",
    "260826Q07",
    "260926Q08",
    "261026Q10",
    "261326Q13",
    "52RGZNZGC",
    "52JDYQ01",
    "37289",
}


def main():
    root = Path(__file__).resolve().parents[1]
    client = TeachingSystemClient(Settings.from_sources(os.environ, root / ".env"))
    client.refresh_cookie()
    catalog = client.get_catalog()
    report = {"years": sorted(catalog.years), "records": []}
    output = root / ".cache/undergraduate-overrides-audit.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    for year in sorted(catalog.years):
        for department in catalog.departments:
            if department.code not in {"01", "52", "37"} and not department.code.startswith("26"):
                continue
            response = client._request(
                "POST",
                "/pub/queryYxzyList_x",
                data={"yxdm": department.code, "nj": year},
                headers={"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"},
            )
            payload = client._json(response, "undergraduate audit")
            for item in payload:
                if item.get("pageZydm") not in CODES:
                    continue
                major = parse_major_list([item], year, department)[0]
                courses = client.get_plan(major)
                record = {
                    "year": year,
                    "department": department.name,
                    "code": item["pageZydm"],
                    "raw_name": item["pageZymc"],
                    "course_rows": len(courses),
                    "academic_years": sorted({course.academic_year for course in courses}),
                    "sample_courses": [course.name for course in courses[:8]],
                }
                report["records"].append(record)
                print(json.dumps(record, ensure_ascii=False), flush=True)
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved: {output}")


if __name__ == "__main__":
    try:
        main()
    except HoahrbJwtsError as error:
        print(f"error: {error}")
        raise SystemExit(1) from None
