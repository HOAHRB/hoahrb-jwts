"""Anchor variant course patterns against source-labelled 2+X field plans."""

import json
import os
from dataclasses import asdict
from pathlib import Path

from hoahrb_jwts.client import TeachingSystemClient
from hoahrb_jwts.config import Settings
from hoahrb_jwts.errors import HoahrbJwtsError


def main():
    root = Path(__file__).resolve().parents[1]
    path = root / ".cache/future-variants-review.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    client = TeachingSystemClient(Settings.from_sources(os.environ, root / ".env"))
    client.refresh_cookie()
    department = next(item for item in client.get_catalog().departments if item.code == "52")
    report["named_field_plans"] = []
    for year in ("2022", "2023", "2024"):
        majors = client.get_majors(year, department)
        for major in majors:
            if major.code not in {"52WL02", "52WL03", "52WL04"}:
                continue
            courses = tuple(dict.fromkeys(client.get_plan(major)))
            entry = {"major": asdict(major), "courses": [asdict(course) for course in courses]}
            report["named_field_plans"].append(entry)
            print(json.dumps(entry, ensure_ascii=False), flush=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    try:
        main()
    except HoahrbJwtsError as error:
        print(f"error: {error}")
        raise SystemExit(1) from None
