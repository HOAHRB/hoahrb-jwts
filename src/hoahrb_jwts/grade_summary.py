"""Parse public HIT grade-component responses into ``grades_summary.json`` data.

The public grade page has had more than one response shape over time.  The
normalizer therefore accepts both the JSON shape returned by the current
front-end and the older table/JSON wrapper shapes, while keeping the output
contract deliberately small:

``{course_code: {variant_key: [{"name": ..., "percent": ...}]}}``

The ``percent`` key is omitted when the source does not provide a percentage,
which matches the existing ``grades_summary.json`` files.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from bs4 import BeautifulSoup

from .errors import AuthenticationError, ParseError

GradeItem = dict[str, str]
GradeSummary = dict[str, dict[str, list[GradeItem]]]


@dataclass(frozen=True)
class GradeDetailReference:
    """Arguments used by a ``queryCjView`` button in the grade list."""

    id: str
    rwh: str
    jhh: str


@dataclass(frozen=True)
class GradeSummaryPage:
    """One response page and its pagination metadata."""

    summary: GradeSummary
    page_no: int = 1
    page_count: int = 1
    page_size: int | None = None
    detail_references: tuple[GradeDetailReference, ...] = ()


_COURSE_CODE_KEYS = (
    "course_code",
    "courseCode",
    "courseNo",
    "courseNumber",
    "kcdm",
    "kcdh",
    "kcbh",
    "kch",
    "kcCode",
    "课程代码",
    "课程编号",
    "课程号",
)
_VARIANT_KEYS = (
    "entry_key",
    "entryKey",
    "variant",
    "variantKey",
    "course_variant",
    "gradeKey",
    "grade_key",
    "年级专业",
    "年度专业",
)
_YEAR_KEYS = (
    "year",
    "entryYear",
    "enrollmentYear",
    "gradeYear",
    "cohort",
    "nj",
    "入学年份",
    "年级",
)
_MAJOR_KEYS = (
    "major",
    "majorName",
    "major_code",
    "majorCode",
    "zy",
    "zymc",
    "zydm",
    "专业",
    "专业名称",
)
_GRADE_VALUE_KEYS = (
    "grade",
    "grading",
    "gradeComposition",
    "grade_composition",
    "scoreComposition",
    "score_composition",
    "composition",
    "components",
    "gradeDetails",
    "grade_details",
    "details",
    "items",
    "cjgc",
    "cjgcb",
    "cjgcmc",
    "cjgcs",
    "cjgzb",
    "cjgzz",
    "cjgz",
    "cjfb",
    "cjfx",
    "cjf",
    "cjgcz",
    "成绩构成",
    "成绩组成",
    "成绩比例",
    "考核方式",
    "考核比例",
)
_ITEM_NAME_KEYS = (
    "name",
    "itemName",
    "component",
    "componentName",
    "label",
    "title",
    "cjgcmc",
    "cjfbmc",
    "cjgzmc",
    "cjfxmc",
    "mc",
    "名称",
    "项目",
    "考核项目",
)
_PERCENT_KEYS = (
    "percent",
    "percentage",
    "ratio",
    "weight",
    "proportion",
    "percentValue",
    "cjgcz",
    "cjgcbz",
    "cjgcbfb",
    "cjgz",
    "cjfbz",
    "cjfbbl",
    "cjfxz",
    "zb",
    "zbl",
    "weightValue",
    "比例",
    "比例值",
    "百分比",
    "占比",
)
_WRAPPER_KEYS = (
    "data",
    "rows",
    "records",
    "list",
    "items",
    "result",
    "courses",
    "courseList",
    "cjList",
    "scoreList",
)
_PAGE_NO_KEYS = ("pageNo", "page_no", "currentPage", "current_page", "页码")
_PAGE_COUNT_KEYS = (
    "pageCount",
    "page_count",
    "totalPage",
    "total_page",
    "totalPages",
    "pages",
    "总页数",
)
_PAGE_SIZE_KEYS = ("pageSize", "page_size", "limit", "size", "每页条数")
_TOTAL_KEYS = ("total", "totalCount", "total_count", "记录总数")


def _text(value: object) -> str:
    if value is None:
        return ""
    return " ".join(str(value).replace("\xa0", " ").split())


def _key_token(value: object) -> str:
    return re.sub(r"[\s_\-]", "", str(value)).lower()


def _value_for(mapping: Mapping[object, object], keys: Sequence[str]) -> object | None:
    wanted = {_key_token(key) for key in keys}
    for key, value in mapping.items():
        if _key_token(key) in wanted:
            return value
    return None


def _has_key(mapping: Mapping[object, object], keys: Sequence[str]) -> bool:
    wanted = {_key_token(key) for key in keys}
    return any(_key_token(key) in wanted for key in mapping)


def _int_value(value: object | None) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    match = re.fullmatch(r"[1-9]\d*", _text(value))
    return int(match.group()) if match else None


def _pagination(source: Mapping[object, object]) -> tuple[int, int, int | None]:
    page_no = _int_value(_value_for(source, _PAGE_NO_KEYS)) or 1
    page_size = _int_value(_value_for(source, _PAGE_SIZE_KEYS))
    page_count = _int_value(_value_for(source, _PAGE_COUNT_KEYS))
    total = _int_value(_value_for(source, _TOTAL_KEYS))
    if page_count is None and total is not None and page_size:
        page_count = max(1, (total + page_size - 1) // page_size)
    page_count = page_count or 1
    if page_no > page_count:
        raise ParseError("grade-summary page number exceeds page count")
    return page_no, page_count, page_size


def _normalize_percent(value: object | None) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        if 0 <= number <= 1:
            number *= 100
        return f"{number:g}%"

    raw = _text(value).replace("％", "%")
    match = re.search(r"(\d+(?:\.\d+)?)\s*%", raw)
    if match:
        return f"{float(match.group(1)):g}%"
    if re.fullmatch(r"\d+(?:\.\d+)?", raw):
        number = float(raw)
        if 0 <= number <= 1:
            number *= 100
        return f"{number:g}%"
    return None


def _split_grade_text(raw: str) -> list[str]:
    normalized = raw.replace("＋", "+").replace("％", "%").strip()
    if not normalized:
        return []

    # Most source strings use '+'.  The other separators occur in the public
    # page's table export.  A whitespace separator is accepted only when the
    # next component also ends in a percentage, avoiding splits in names such
    # as "作业+实验".
    separator = r"(?<=%)\s*(?:\+|；|;|,|，|、|\|)\s*|\s+\+\s+"
    parts = [part.strip() for part in re.split(separator, normalized) if part.strip()]
    if len(parts) == 1:
        parts = [
            part.strip()
            for part in re.split(r"(?<=%)\s+(?=[^0-9%]+?\s*\d+(?:\.\d+)?\s*%)", normalized)
            if part.strip()
        ]
    return parts or [normalized]


def parse_grade_text(raw: str) -> list[GradeItem]:
    """Parse a human-readable composition string into output items."""

    items: list[GradeItem] = []
    for part in _split_grade_text(raw):
        match = re.search(r"(\d+(?:\.\d+)?)\s*%", part.replace("％", "%"))
        if match:
            name = part[: match.start()].strip(" :：\t-—") or part.strip()
            items.append({"name": name, "percent": f"{match.group(1)}%"})
        else:
            items.append({"name": part.strip()})
    return items


def _item_from_mapping(value: Mapping[object, object]) -> GradeItem | None:
    name_value = _value_for(value, _ITEM_NAME_KEYS)
    percent_value = _value_for(value, _PERCENT_KEYS)
    if name_value is None:
        return None
    name = _text(name_value)
    if not name:
        return None
    item: GradeItem = {"name": name}
    percent = _normalize_percent(percent_value)
    if percent is not None:
        item["percent"] = percent
    return item


def _component_items(value: object) -> list[GradeItem]:
    if value is None or isinstance(value, bool):
        return []
    if isinstance(value, str):
        return parse_grade_text(value)
    if isinstance(value, (int, float)):
        return []
    if isinstance(value, Mapping):
        direct_item = _item_from_mapping(value)
        if direct_item is not None:
            if "percent" not in direct_item and re.search(
                r"\d+(?:\.\d+)?\s*%", direct_item["name"]
            ):
                return parse_grade_text(direct_item["name"])
            return [direct_item]

        # Some versions wrap a single component as {name: ..., ratio: ...}
        # while others nest it under ``grade``/``details``.
        result: list[GradeItem] = []
        for nested in value.values():
            result.extend(_component_items(nested))
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        result = []
        for nested in value:
            result.extend(_component_items(nested))
        return result
    return []


def _pair_items(record: Mapping[object, object]) -> list[GradeItem]:
    name_value = _value_for(record, _ITEM_NAME_KEYS)
    percent_value = _value_for(record, _PERCENT_KEYS)
    if name_value is None or percent_value is None:
        return []

    if isinstance(name_value, Sequence) and not isinstance(name_value, (str, bytes, bytearray)):
        names = list(name_value)
    else:
        names = re.split(r"\s*[,，;；|、]\s*", _text(name_value))
    if isinstance(percent_value, Sequence) and not isinstance(
        percent_value, (str, bytes, bytearray)
    ):
        percentages = list(percent_value)
    else:
        percentages = re.split(r"\s*[,，;；|、]\s*", _text(percent_value))
    if len(names) != len(percentages):
        return []

    result: list[GradeItem] = []
    for name, percent in zip(names, percentages, strict=True):
        name_text = _text(name)
        if not name_text:
            continue
        item: GradeItem = {"name": name_text}
        normalized = _normalize_percent(percent)
        if normalized is not None:
            item["percent"] = normalized
        result.append(item)
    return result


def _grade_value(record: Mapping[object, object]) -> object | None:
    paired = _pair_items(record)
    if paired:
        return paired

    value = _value_for(record, _GRADE_VALUE_KEYS)
    if value is not None:
        return value

    # Keep this heuristic narrow: public responses have historically renamed
    # the composition field, but the field name still contained one of these
    # stable Chinese/English fragments.
    for key, candidate in record.items():
        token = _key_token(key)
        if any(fragment in token for fragment in ("cjgc", "cjfb", "gradecomposition", "grading")):
            return candidate
        if any(fragment in str(key) for fragment in ("成绩构成", "成绩比例", "考核比例")):
            return candidate
    return None


def _normalize_year(value: object | None) -> str | None:
    if value is None:
        return None
    raw = _text(value)
    match = re.search(r"(20\d{2}|\d{2})", raw)
    if not match:
        return None
    year = match.group(1)
    return f"20{year}" if len(year) == 2 else year


def _variant_key(record: Mapping[object, object]) -> str:
    explicit = _value_for(record, _VARIANT_KEYS)
    if explicit is not None and _text(explicit):
        raw = _text(explicit).replace("＋", "+")
        if raw.lower() == "default":
            return "default"
        match = re.fullmatch(r"(20\d{2})_?(.*)", raw)
        if match:
            suffix = match.group(2).strip(" _-")
            return f"{match.group(1)}_{suffix or 'default'}"
        match = re.fullmatch(r"(\d{2})级\s*(.*)", raw)
        if match:
            suffix = match.group(2).strip()
            return f"20{match.group(1)}_{suffix or 'default'}"

    year = _normalize_year(_value_for(record, _YEAR_KEYS))
    major = _text(_value_for(record, _MAJOR_KEYS))
    if year:
        return f"{year}_{major or 'default'}"
    return "default"


def _course_code(record: Mapping[object, object]) -> str | None:
    value = _value_for(record, _COURSE_CODE_KEYS)
    if value is None:
        nested = _value_for(record, ("course", "courseInfo", "课程"))
        if isinstance(nested, Mapping):
            value = _value_for(nested, _COURSE_CODE_KEYS)
    if value is None or isinstance(value, (Mapping, Sequence)) and not isinstance(value, str):
        return None
    result = _text(value)
    return result or None


def _merge_item_lists(left: list[GradeItem], right: Sequence[GradeItem]) -> list[GradeItem]:
    seen = {(item.get("name"), item.get("percent")) for item in left}
    for item in right:
        identity = (item.get("name"), item.get("percent"))
        if identity not in seen:
            left.append(dict(item))
            seen.add(identity)
    return left


def _add_record(summary: GradeSummary, record: Mapping[object, object]) -> bool:
    code = _course_code(record)
    if code is None:
        return False
    items = _component_items(_grade_value(record))
    if not items:
        return False
    variant = _variant_key(record)
    course = summary.setdefault(code, {})
    _merge_item_lists(course.setdefault(variant, []), items)
    return True


def _looks_like_summary(value: object) -> bool:
    if not isinstance(value, Mapping) or not value:
        return False
    return all(
        isinstance(variants, Mapping)
        and all(isinstance(items, (list, tuple, Mapping, str)) for items in variants.values())
        for variants in value.values()
    )


def _from_summary_shape(value: Mapping[object, object]) -> GradeSummary:
    summary: GradeSummary = {}
    for raw_code, raw_variants in value.items():
        code = _text(raw_code)
        if not code or not isinstance(raw_variants, Mapping):
            continue
        course: dict[str, list[GradeItem]] = {}
        for raw_variant, raw_items in raw_variants.items():
            variant = _text(raw_variant) or "default"
            items = _component_items(raw_items)
            if items:
                _merge_item_lists(course.setdefault(variant, []), items)
        if course:
            summary[code] = course
    return summary


def _records(value: object, depth: int = 0) -> list[Mapping[object, object]]:
    if depth > 6:
        return []
    if isinstance(value, Mapping):
        if _course_code(value) is not None:
            return [value]
        for key in _WRAPPER_KEYS:
            nested = _value_for(value, (key,))
            if nested is not None:
                found = _records(nested, depth + 1)
                if found:
                    return found

        # A few responses use {course_code: {variant: ...}} without a data
        # wrapper.  Preserve those keys as synthetic records.
        candidates: list[Mapping[object, object]] = []
        for key, nested in value.items():
            code = _text(key)
            if not code or not isinstance(nested, (Mapping, list, tuple, str)):
                continue
            if isinstance(nested, Mapping) and any(
                _key_token(item_key) in {_key_token(k) for k in _VARIANT_KEYS}
                for item_key in nested
            ):
                candidates.append({"course_code": code, "grades": nested})
            elif re.search(r"[A-Za-z].*\d|\d.*[A-Za-z]", code):
                candidates.append({"course_code": code, "grade": nested})
        if candidates:
            return candidates

        for nested in value.values():
            found = _records(nested, depth + 1)
            if found:
                return found
        return []
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        records: list[Mapping[object, object]] = []
        for nested in value:
            if isinstance(nested, Mapping):
                records.append(nested)
            else:
                records.extend(_records(nested, depth + 1))
        return records
    return []


def _parse_json_page(payload: object) -> GradeSummaryPage:
    if isinstance(payload, Mapping):
        page_no, page_count, page_size = _pagination(payload)
        for key in _WRAPPER_KEYS:
            nested = _value_for(payload, (key,))
            if isinstance(nested, Mapping) and _looks_like_summary(nested):
                return GradeSummaryPage(_from_summary_shape(nested), page_no, page_count, page_size)
        if _looks_like_summary(payload):
            return GradeSummaryPage(_from_summary_shape(payload), page_no, page_count, page_size)
    else:
        page_no, page_count, page_size = 1, 1, None

    summary: GradeSummary = {}
    for record in _records(payload):
        if _has_key(record, ("grades", "gradeSummary", "gradeDetails", "成绩构成")):
            nested = _value_for(record, ("grades", "gradeSummary", "gradeDetails", "成绩构成"))
            if isinstance(nested, Mapping) and not _grade_value(record):
                for raw_variant, raw_items in nested.items():
                    synthetic = dict(record)
                    synthetic["entry_key"] = raw_variant
                    synthetic["grade"] = raw_items
                    _add_record(summary, synthetic)
                continue
        _add_record(summary, record)
    return GradeSummaryPage(summary, page_no, page_count, page_size)


def _header_index(
    headers: Sequence[str], names: Sequence[str], fragments: Sequence[str]
) -> int | None:
    wanted = {_key_token(name) for name in names}
    for index, header in enumerate(headers):
        token = _key_token(header)
        if token in wanted or any(fragment in token for fragment in fragments):
            return index
    return None


_QUERY_CJ_VIEW_RE = re.compile(
    r"queryCjView\s*\(\s*(['\"])(?P<id>[^'\"]+)\1\s*,\s*"
    r"(['\"])(?P<rwh>[^'\"]+)\3\s*,\s*(['\"])(?P<jhh>[^'\"]+)\5\s*\)",
    re.DOTALL,
)
_DETAIL_PERCENT_RE = re.compile(
    r"权重.*?(?:\[|【|（|\()\s*(\d+(?:\.\d+)?)\s*[%％]",
    re.DOTALL,
)


def _qmcj_detail_references(soup: BeautifulSoup) -> tuple[GradeDetailReference, ...]:
    """Read the arguments from the grade-list page's magnifying-glass buttons."""

    references: list[GradeDetailReference] = []
    seen: set[tuple[str, str, str]] = set()
    for node in soup.select(".cz_see"):
        call = _QUERY_CJ_VIEW_RE.search(str(node.get("onclick", "")))
        if call is None:
            continue
        values = (call.group("id"), call.group("rwh"), call.group("jhh"))
        if values in seen:
            continue
        seen.add(values)
        references.append(GradeDetailReference(*values))
    return tuple(references)


def _parse_html_page(html: str) -> GradeSummaryPage:
    if any(marker in html for marker in ("页面过期", "统一身份认证登录", "用户登录")):
        raise AuthenticationError("grade-summary page is not authenticated")
    soup = BeautifulSoup(html, "html.parser")
    page_text = _text(soup.get_text(" ", strip=True))
    if any(marker in page_text for marker in ("页面过期", "统一身份认证登录", "用户登录")):
        raise AuthenticationError("grade-summary page is not authenticated")

    page_values: dict[str, object] = {}
    for name in (*_PAGE_NO_KEYS, *_PAGE_COUNT_KEYS, *_PAGE_SIZE_KEYS):
        element = soup.find("input", attrs={"name": name}) or soup.find("input", id=name)
        if element is not None and element.get("value"):
            page_values[name] = element.get("value")
    page_no, page_count, page_size = _pagination(page_values)

    detail_references = _qmcj_detail_references(soup)
    if detail_references:
        return GradeSummaryPage(
            {}, page_no, page_count, page_size, detail_references=detail_references
        )

    summary: GradeSummary = {}
    code_headers = (*_COURSE_CODE_KEYS, "课程代码", "课程编号", "课程号")
    grade_headers = (*_GRADE_VALUE_KEYS, "成绩构成", "成绩组成", "考核方式")
    year_headers = (*_YEAR_KEYS, "年级", "入学年份")
    major_headers = (*_MAJOR_KEYS, "专业", "专业名称")
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        for header_row_index, row in enumerate(rows):
            headers = [
                _text(cell.get_text(" ", strip=True))
                for cell in row.find_all(["th", "td"], recursive=False)
            ]
            code_index = _header_index(headers, code_headers, ("课程代码", "coursecode", "kcdm"))
            grade_index = _header_index(
                headers, grade_headers, ("成绩构成", "成绩比例", "gradecomposition", "cjgc")
            )
            if code_index is None or grade_index is None:
                continue
            year_index = _header_index(headers, year_headers, ("年级", "入学年份", "year", "nj"))
            major_index = _header_index(headers, major_headers, ("专业", "major", "zymc"))
            for data_row in rows[header_row_index + 1 :]:
                cells = data_row.find_all(["th", "td"], recursive=False)
                values = [_text(cell.get_text(" ", strip=True)) for cell in cells]
                if len(values) <= max(code_index, grade_index) or not values[code_index]:
                    continue
                record: dict[str, object] = {
                    "course_code": values[code_index],
                    "grade": values[grade_index],
                }
                if year_index is not None and year_index < len(values):
                    record["year"] = values[year_index]
                if major_index is not None and major_index < len(values):
                    record["major"] = values[major_index]
                _add_record(summary, record)
            break
    if not summary:
        raise ParseError("grade-summary response has no recognizable course records")
    return GradeSummaryPage(summary, page_no, page_count, page_size)


def _detail_pairs(soup: BeautifulSoup) -> list[tuple[str, str]]:
    """Extract label/value pairs from the grade-detail table."""

    pairs: list[tuple[str, str]] = []
    for row in soup.find_all("tr"):
        cells = row.find_all(["th", "td"], recursive=False)
        for index in range(0, len(cells) - 1, 2):
            label = _text(cells[index].get_text(" ", strip=True))
            value = _text(cells[index + 1].get_text(" ", strip=True))
            if label:
                pairs.append((label, value))
    return pairs


def parse_grade_summary_detail(payload: object) -> GradeSummary:
    """Parse one ``queryCjxxView`` response into the public JSON contract.

    ``queryQmcj`` contains only final scores.  The score-component names and
    their weights live in this detail page, where the component value includes
    a literal ``权重（占总成绩百分比）[...]`` annotation.
    """

    if isinstance(payload, bytes):
        payload = payload.decode("utf-8", errors="replace")
    if not isinstance(payload, str):
        raise ParseError("grade-detail response is not HTML")
    if any(marker in payload for marker in ("页面过期", "统一身份认证登录", "用户登录")):
        raise AuthenticationError("grade-detail page is not authenticated")

    soup = BeautifulSoup(payload, "html.parser")
    page_text = _text(soup.get_text(" ", strip=True))
    if any(marker in page_text for marker in ("页面过期", "统一身份认证登录", "用户登录")):
        raise AuthenticationError("grade-detail page is not authenticated")

    course_code: str | None = None
    items: list[GradeItem] = []
    for label, value in _detail_pairs(soup):
        if _key_token(label.rstrip(":：")) in {_key_token(key) for key in _COURSE_CODE_KEYS}:
            course_code = value or course_code
            continue
        match = _DETAIL_PERCENT_RE.search(value)
        if match is None:
            continue
        percent = _normalize_percent(match.group(1))
        if percent is not None:
            items.append({"name": label.rstrip(":：").strip(), "percent": percent})

    if not course_code:
        raise ParseError("grade-detail response has no course code")
    if not items:
        return {}
    return {course_code: {"default": items}}


def _json_or_jsonp(text: str) -> object:
    raw = text.lstrip("\ufeff ")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = re.fullmatch(r"[\w$.]+\s*\(\s*(\{.*\}|\[.*\])\s*\)\s*;?", raw, re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(1))


def parse_grade_summary_page(payload: object) -> GradeSummaryPage:
    """Parse a decoded payload, JSON/JSONP text, or an HTML response page."""

    if isinstance(payload, bytes):
        payload = payload.decode("utf-8", errors="replace")
    if isinstance(payload, str):
        text = payload.lstrip("\ufeff ")
        try:
            payload = _json_or_jsonp(text)
        except json.JSONDecodeError:
            return _parse_html_page(payload)

    if isinstance(payload, (Mapping, Sequence)) and not isinstance(
        payload, (str, bytes, bytearray)
    ):
        page = _parse_json_page(payload)
        if not page.summary and not page.detail_references:
            raise ParseError("grade-summary response has no recognizable course records")
        return page
    raise ParseError("grade-summary response is neither JSON nor HTML")


def parse_grade_summary(payload: object) -> GradeSummary:
    """Parse one complete response into the public JSON contract."""

    return parse_grade_summary_page(payload).summary


def merge_grade_summaries(target: GradeSummary, source: GradeSummary) -> None:
    """Merge one response page into an existing summary in place."""

    for code, variants in source.items():
        destination = target.setdefault(code, {})
        for variant, items in variants.items():
            _merge_item_lists(destination.setdefault(variant, []), items)
