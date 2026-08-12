"""Pure parsers for the observed HIT teaching-system responses."""

from __future__ import annotations

import re
from collections.abc import Mapping

from bs4 import BeautifulSoup

from .errors import AuthenticationError, ParseError
from .models import Catalog, Department, Major, PlanPage, SourceCourse


def _text(value: object) -> str:
    return " ".join(str(value).split())


def _cell_text(cell: object) -> str:
    return _text(" ".join(getattr(cell, "stripped_strings", ())))


def _optional(value: str) -> str | None:
    return value or None


def parse_catalog_page(html: str) -> Catalog:
    """Parse the authenticated execution-plan query catalog."""

    soup = BeautifulSoup(html, "html.parser")
    page_text = _text(soup.get_text(" ", strip=True))
    if "执行教学计划查询" not in page_text:
        raise AuthenticationError("execution-plan catalog is not authenticated")

    years_select = soup.find("select", attrs={"name": "pageNj"})
    departments_select = soup.find("select", attrs={"name": "pageYxdm"})
    if years_select is None or departments_select is None:
        raise ParseError("execution-plan catalog is missing pageNj or pageYxdm")

    years: list[str] = []
    for option in years_select.find_all("option"):
        year = _text(option.get("value", ""))
        if year and year not in years:
            years.append(year)

    departments: list[Department] = []
    seen_codes: set[str] = set()
    for option in departments_select.find_all("option"):
        code = _text(option.get("value", ""))
        if not code:
            continue
        display_name = _text(option.get_text(" ", strip=True))
        display_name = re.sub(r"^\s*\[[^]]+\]\s*", "", display_name)
        if not display_name:
            raise ParseError("department option has an empty name")
        if code in seen_codes:
            continue
        seen_codes.add(code)
        departments.append(Department(code=code, name=display_name))

    if not years:
        raise ParseError("execution-plan catalog has no academic years")
    if not departments:
        raise ParseError("execution-plan catalog has no departments")
    return Catalog(years=tuple(years), departments=tuple(departments))


def parse_major_list(payload: object, year: str, department: Department) -> tuple[Major, ...]:
    """Parse the JSON major list for one cohort and department."""

    if not isinstance(payload, list):
        raise ParseError("major list response is not an array")

    majors: list[Major] = []
    for index, entry in enumerate(payload):
        if not isinstance(entry, Mapping):
            raise ParseError(f"major list entry {index} is not an object")
        code = entry.get("pageZydm")
        raw_name = entry.get("pageZymc")
        if not isinstance(code, str) or not code.strip():
            raise ParseError(f"major list entry {index} has an invalid pageZydm")
        if not isinstance(raw_name, str) or not raw_name.strip():
            raise ParseError(f"major list entry {index} has an invalid pageZymc")
        normalized_code = _text(code)
        normalized_year = _text(year)
        suffix = normalized_code.removeprefix(department.code).upper()
        display_name = _text(raw_name)
        category_match = re.search(r"【([^】]+)】$", display_name)
        if category_match:
            category = category_match.group(1).strip()
            if category not in {"本", "辅修", "第二学士学位"}:
                raise ParseError(f"major list entry {index} has unknown category {category}")
            name = display_name[: category_match.start()].strip()
        else:
            name = display_name
            if suffix.startswith("M"):
                category = "微专业"
            elif re.match(r"^(?:\d{2})?L", suffix):
                category = "本"
            elif "E" in suffix:
                category = "第二学士学位"
            elif "F" in suffix:
                category = "辅修"
            else:
                category = "未分类"
        # Y is an explicit correction marker and overrides a wrong source
        # label. Its academic meaning is not established, so keep it as an
        # independent category. 01044 is the one known exception.
        if suffix.startswith("Y"):
            category = "Y"
        if normalized_code == "01044":
            category = "本"
        if not name:
            raise ParseError(f"major list entry {index} has an empty major name")
        majors.append(
            Major(
                year=normalized_year,
                department_code=department.code,
                department_name=department.name,
                code=normalized_code,
                name=name,
                category=category,
            )
        )
    return tuple(majors)


_REQUIRED_PLAN_HEADERS = ("课程代码", "课程名称", "开课学年", "开课学期", "学分")
_OPTIONAL_PLAN_HEADERS = {
    "课程英文名": "english_name",
    "开课院系": "offering_department",
    "课程性质": "nature",
    "课程类别": "category",
    "专业方向": "track",
    "总学时": "total_hours",
    "是否考试课": "examination_indicator",
}


def _pagination_value(soup: BeautifulSoup, name: str) -> int | None:
    element = soup.find("input", attrs={"name": name}) or soup.find("input", id=name)
    if element is None:
        return None
    value = _text(element.get("value", ""))
    if name == "pageNo" and not value:
        current_page = soup.select_one("ul.list-page a.cur")
        if current_page is not None:
            current_value = _text(current_page.get_text(" ", strip=True))
            if not re.fullmatch(r"[1-9]\d*", current_value):
                raise ParseError("execution-plan page has invalid current page marker")
            return int(current_value)
        return 1
    if not re.fullmatch(r"[1-9]\d*", value):
        raise ParseError(f"execution-plan page has invalid {name}")
    return int(value)


def parse_plan_page(html: str) -> PlanPage:
    """Parse one header-driven, paginated execution-plan table."""

    soup = BeautifulSoup(html, "html.parser")
    page_no = _pagination_value(soup, "pageNo")
    page_count = _pagination_value(soup, "pageCount")
    page_size = _pagination_value(soup, "pageSize")
    if page_no is None and page_count is None:
        page_no = page_count = 1
    elif page_no is None:
        raise ParseError("execution-plan page is missing pageNo")
    elif page_count is None:
        raise ParseError("execution-plan page is missing pageCount")
    if page_no > page_count:
        raise ParseError("execution-plan page number exceeds page count")

    selected_row = None
    header_map: dict[str, int] = {}
    for table in soup.find_all("table"):
        for row in table.find_all("tr"):
            cells = row.find_all(["th", "td"], recursive=False)
            headers = [_cell_text(cell) for cell in cells]
            if all(header in headers for header in _REQUIRED_PLAN_HEADERS):
                selected_row = row
                header_map = {header: index for index, header in enumerate(headers)}
                break
        if selected_row is not None:
            break

    if selected_row is None:
        raise ParseError("execution-plan table is missing 课程代码")

    table = selected_row.find_parent("table")
    if table is None:  # pragma: no cover - BeautifulSoup always supplies the parent here.
        raise ParseError("execution-plan table cannot be located")
    rows = table.find_all("tr")
    header_index = rows.index(selected_row)
    courses: list[SourceCourse] = []
    for row in rows[header_index + 1 :]:
        cells = row.find_all(["th", "td"], recursive=False)
        if not cells or not any(_cell_text(cell) for cell in cells):
            continue
        if len(cells) <= max(header_map.values()):
            raise ParseError("execution-plan row has fewer cells than its headers")
        values = [_cell_text(cell) for cell in cells]
        required_values = {header: values[header_map[header]] for header in _REQUIRED_PLAN_HEADERS}
        if any(not value for value in required_values.values()):
            raise ParseError("execution-plan row is missing a required value")

        optional_values = {
            field: _optional(values[index])
            for header, field in _OPTIONAL_PLAN_HEADERS.items()
            if header != "是否考试课"
            if (index := header_map.get(header)) is not None and index < len(values)
        }
        examination_index = header_map.get("是否考试课")
        examination_indicator = values[examination_index] if examination_index is not None else None
        courses.append(
            SourceCourse(
                code=required_values["课程代码"],
                name=required_values["课程名称"],
                english_name=optional_values.get("english_name"),
                academic_year=required_values["开课学年"],
                term=required_values["开课学期"],
                offering_department=optional_values.get("offering_department"),
                nature=optional_values.get("nature"),
                category=optional_values.get("category"),
                track=optional_values.get("track"),
                credit=required_values["学分"],
                total_hours=optional_values.get("total_hours"),
                examination_indicator=examination_indicator,
            )
        )
    return PlanPage(
        courses=tuple(courses),
        page_no=page_no,
        page_count=page_count,
        page_size=page_size,
    )
