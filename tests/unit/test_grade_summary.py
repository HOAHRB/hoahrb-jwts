from hoahrb_jwts.grade_summary import (
    GradeDetailReference,
    parse_grade_summary,
    parse_grade_summary_detail,
    parse_grade_summary_page,
)


def test_parse_public_json_grade_components_and_variant() -> None:
    payload = {
        "data": [
            {
                "kcdm": "AUTO1001",
                "nj": "2024",
                "zymc": "自动化",
                "cjfb": [
                    {"cjfbmc": "平时成绩", "cjfbz": 40},
                    {"cjfbmc": "期末考试", "cjfbz": 60},
                ],
            }
        ],
        "pageNo": 1,
        "pageCount": 1,
    }

    assert parse_grade_summary(payload) == {
        "AUTO1001": {
            "2024_自动化": [
                {"name": "平时成绩", "percent": "40%"},
                {"name": "期末考试", "percent": "60%"},
            ]
        }
    }


def test_parse_grade_summary_accepts_existing_json_shape_and_unparseable_text() -> None:
    payload = '{"C001": {"default": [{"name": "作业 40% + 考试 60%"}]}}'

    assert parse_grade_summary(payload) == {
        "C001": {
            "default": [
                {"name": "作业", "percent": "40%"},
                {"name": "考试", "percent": "60%"},
            ]
        }
    }


def test_parse_grade_summary_accepts_html_table() -> None:
    html = """
    <table>
      <tr><th>课程代码</th><th>年级</th><th>成绩构成</th></tr>
      <tr><td>PHYS1001</td><td>2023</td><td>平时 30% + 考试 70%</td></tr>
    </table>
    """

    page = parse_grade_summary_page(html)

    assert page.page_no == 1
    assert page.summary == {
        "PHYS1001": {
            "2023_default": [
                {"name": "平时", "percent": "30%"},
                {"name": "考试", "percent": "70%"},
            ]
        }
    }


def test_parse_grade_summary_reads_query_qmcj_detail_references() -> None:
    html = """
    <form name="page" method="post" action="/cjcx/queryQmcj">
      <input name="pageNo" />
      <input name="pageSize" value="20" />
      <input name="pageCount" value="2" />
    </form>
    <table class="bot_line">
      <tr><th>序号</th><th>学年学期</th><th>课程代码</th><th>最终成绩</th></tr>
      <tr><td>1</td><td>2025秋季</td><td>22AD16001</td><td>92</td>
      <td><div class="cz_button cz_see" onclick="queryCjView('id-1', 'task-1', 'plan-1')"></div></td></tr>
      <tr><td>2</td><td>2026春季</td><td>22AD16002</td><td>优秀</td>
      <td><div class="cz_button cz_see" onclick="queryCjView('id-2', 'task-2', 'plan-2')"></div></td></tr>
    </table>
    """

    page = parse_grade_summary_page(html)

    assert page.page_no == 1
    assert page.page_count == 2
    assert page.page_size == 20
    assert page.summary == {}
    assert page.detail_references == (
        GradeDetailReference("id-1", "task-1", "plan-1"),
        GradeDetailReference("id-2", "task-2", "plan-2"),
    )


def test_parse_grade_summary_detail_reads_weighted_components_only() -> None:
    html = """
    <table>
      <tr><th>课程代码：</th><td>22AD16001</td><th>总成绩：</th><td>92</td></tr>
      <tr><th>期末考试成绩：</th><td>权重（占总成绩百分比）[50.0%]，满分[100.0]</td>
          <th>本项得分</th><td>90</td></tr>
      <tr><th>MOOC成绩：</th><td>权重（占总成绩百分比）[30.0%]，满分[100.0]</td>
          <th>本项得分</th><td>95</td></tr>
      <tr><th>累计加分</th><td>5</td><th>本项得分</th><td>5</td></tr>
    </table>
    """

    assert parse_grade_summary_detail(html) == {
        "22AD16001": {
            "default": [
                {"name": "期末考试成绩", "percent": "50%"},
                {"name": "MOOC成绩", "percent": "30%"},
            ]
        }
    }


def test_parse_grade_summary_accepts_jsonp() -> None:
    assert parse_grade_summary(
        'callback({"data":[{"courseCode":"C001","grade":"考试 100%"}]})'
    ) == {"C001": {"default": [{"name": "考试", "percent": "100%"}]}}
