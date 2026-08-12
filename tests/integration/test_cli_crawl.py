import json
from pathlib import Path
from urllib.parse import parse_qs

import responses
import toml

from hoahrb_jwts.app import main
from tests.helpers import load_fixture


@responses.activate
def test_crawl_writes_all_paginated_courses(tmp_path: Path) -> None:
    responses.get(
        "http://jwts.hit.edu.cn/loginCAS",
        body="refreshed session",
        status=200,
        headers={"Set-Cookie": "JSESSIONID=refreshed; Path=/"},
    )
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

    def introduction_callback(request):
        code = parse_qs(request.url.split("?", 1)[1])["kcdm"][0]
        body = f"""
        <table>
          <tr><th>课程代码：</th><td>{code}</td></tr>
          <tr><th>课程简介：</th><td>{code} 中文简介</td></tr>
          <tr><th>课程英文简介：</th><td>{code} English introduction</td></tr>
        </table>
        """
        return (200, {"Content-Type": "text/html; charset=utf-8"}, body)

    responses.add_callback(
        responses.GET,
        "http://jwts.hit.edu.cn/pub/queryKcxxView",
        callback=introduction_callback,
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
    assert responses.calls[0].request.url == "http://jwts.hit.edu.cn/loginCAS"
    assert responses.calls[1].request.url == "http://jwts.hit.edu.cn/zxjh/queryZxkc"
    assert all(
        call.request.headers["Cookie"] == "JSESSIONID=refreshed" for call in responses.calls[1:]
    )
    mapping = json.loads((data_dir / "major_mapping.json").read_text(encoding="utf-8"))
    assert mapping["2025"]["35158"]["plan_ID"] == "HIT-2025-35158"
    generated = toml.load(data_dir / "plans" / "本_2025_人文社科学部_俄语（火箭系统与航天）.toml")
    assert [course["course_code"] for course in generated["courses"]] == [
        "22AD11001",
        "22AD16004",
        "22FL22230",
    ]
    assert [course["assessment_method"] for course in generated["courses"]] == [
        "考查",
        "考查",
        "考试",
    ]
    introductions = json.loads((data_dir / "course_introductions.json").read_text(encoding="utf-8"))
    assert list(introductions) == ["22AD11001", "22AD16004", "22FL22230"]
    assert introductions["22AD11001"] == {
        "default": {
            "zh": "22AD11001 中文简介",
            "en": "22AD11001 English introduction",
        }
    }
