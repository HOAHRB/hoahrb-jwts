import pytest

from hoahrb_jwts.errors import ParseError
from hoahrb_jwts.parsers import parse_plan_page
from tests.helpers import load_fixture


def test_parse_plan_page_reads_verified_columns() -> None:
    page = parse_plan_page(load_fixture("plan_page_1.html"))
    assert (page.page_no, page.page_count) == (1, 2)
    assert page.courses[0].code == "22AD11001"
    assert page.courses[0].credit == "2.0"
    assert page.courses[1].total_hours == "3周"
    assert page.courses[0].examination_indicator == ""
    assert page.courses[1].examination_indicator == ""


def test_parse_plan_page_rejects_changed_table_contract() -> None:
    with pytest.raises(ParseError, match="课程代码"):
        parse_plan_page(load_fixture("plan_malformed.html"))


def test_parse_plan_page_normalizes_whitespace_and_accepts_empty_table() -> None:
    html = """
    <input name="pageNo" value="1"><input name="pageCount" value="1">
    <table><tr><th>课程代码</th><th>课程名称</th><th>开课学年</th>
    <th>开课学期</th><th>学分</th></tr></table>
    """
    page = parse_plan_page(html)
    assert page.courses == ()


def test_parse_plan_page_distinguishes_missing_exam_column_from_blank_cell() -> None:
    html = """
    <table>
      <tr><th>课程代码</th><th>课程名称</th><th>开课学年</th><th>开课学期</th><th>学分</th></tr>
      <tr><td>TEST1001</td><td>测试课程</td><td>1</td><td>秋季</td><td>1.0</td></tr>
    </table>
    """
    page = parse_plan_page(html)
    assert page.courses[0].examination_indicator is None


def test_parse_plan_page_accepts_unpaginated_table() -> None:
    html = """
    <table><tr><th>课程代码</th><th>课程名称</th><th>开课学年</th>
    <th>开课学期</th><th>学分</th></tr></table>
    """
    page = parse_plan_page(html)
    assert (page.page_no, page.page_count) == (1, 1)
    assert page.courses == ()


def test_parse_plan_page_treats_blank_first_page_number_as_one() -> None:
    html = """
    <input name="pageNo" value=""><input name="pageCount" value="2">
    <input name="pageSize" value="20">
    <table><tr><th>课程代码</th><th>课程名称</th><th>开课学年</th>
    <th>开课学期</th><th>学分</th></tr></table>
    """
    page = parse_plan_page(html)
    assert (page.page_no, page.page_count) == (1, 2)
    assert page.page_size == 20


def test_parse_plan_page_reads_current_page_marker_when_page_number_is_blank() -> None:
    html = """
    <input name="pageNo" value=""><input name="pageCount" value="2">
    <ul class="list-page"><li><a class="cur"><b>2</b></a></li></ul>
    <table><tr><th>课程代码</th><th>课程名称</th><th>开课学年</th>
    <th>开课学期</th><th>学分</th></tr></table>
    """
    page = parse_plan_page(html)
    assert (page.page_no, page.page_count) == (2, 2)


def test_parse_plan_page_rejects_invalid_pagination() -> None:
    html = """
    <input name="pageNo" value="0"><input name="pageCount" value="1">
    <table><tr><th>课程代码</th><th>课程名称</th><th>开课学年</th>
    <th>开课学期</th><th>学分</th></tr></table>
    """
    with pytest.raises(ParseError, match="pageNo"):
        parse_plan_page(html)
