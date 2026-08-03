import json
from pathlib import Path
from urllib.parse import parse_qs

import responses
import toml

from hoa_cli.app import main
from tests.helpers import load_fixture


@responses.activate
def test_crawl_writes_all_paginated_courses(tmp_path: Path) -> None:
    responses.get(
        "http://jwts.hit.edu.cn/zxjh/queryZxkc",
        body=load_fixture("catalog_authenticated.html"),
        status=200,
    )

    def major_callback(request):
        params = parse_qs(request.body)
        if params.get("yxdm") == ["35"] and params.get("nj") == ["2025"]:
            body = '[{"pageZydm": "35158", "pageZymc": "俄语【本】"}]'
        else:
            body = "[]"
        return (200, {"Content-Type": "application/json"}, body)

    responses.add_callback(
        responses.POST,
        "http://jwts.hit.edu.cn/pub/queryYxzyList_x",
        callback=major_callback,
    )

    def plan_callback(request):
        params = parse_qs(request.body)
        body = (
            load_fixture("plan_page_2.html")
            if params.get("pageNo") == ["2"]
            else load_fixture("plan_page_1.html")
        )
        return (200, {"Content-Type": "text/html; charset=utf-8"}, body)

    responses.add_callback(
        responses.POST,
        "http://jwts.hit.edu.cn/zxjh/queryZxkc",
        callback=plan_callback,
    )
    data_dir = tmp_path / "major-data"
    exit_code = main(
        ["crawl", "--years", "2025", "--data-dir", str(data_dir)],
        environ={
            "HIT_JW_COOKIE": "JSESSIONID=sanitized",
            "HIT_JW_DELAY_SECONDS": "0",
            "HIT_JW_MAX_RETRIES": "0",
        },
    )
    assert exit_code == 0
    mapping = json.loads((data_dir / "major_mapping.json").read_text(encoding="utf-8"))
    assert mapping["2025"]["35158"]["plan_ID"] == "HIT-2025-35158"
    generated = toml.load(data_dir / "plans" / "本_2025_人文社科学部_俄语（火箭系统与航天）.toml")
    assert [course["course_code"] for course in generated["courses"]] == [
        "22AD11001",
        "22AD16004",
        "22FL22230",
    ]
