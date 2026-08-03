"""Strict source-to-``hoa-major-data`` normalization."""

from __future__ import annotations

import math
import re

from .errors import ValidationError
from .models import (
    DiscoveredPlan,
    NormalizedCourse,
    NormalizedPlan,
    SourceCourse,
)

_YEAR_LABELS = {
    "1": "第一学年",
    "2": "第二学年",
    "3": "第三学年",
    "4": "第四学年",
    "5": "第五学年",
}
_TERMS = {"秋季", "春季", "夏季", "寒假", "暑假"}


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(value.split())
    return cleaned or None


def _required(value: str | None, field_name: str) -> str:
    cleaned = _clean(value)
    if cleaned is None:
        raise ValidationError(f"{field_name} must not be empty")
    return cleaned


def normalize_course(course: SourceCourse) -> NormalizedCourse:
    course_code = _required(course.code, "course code")
    course_name = _required(course.name, "course name")
    academic_year = _required(course.academic_year, "academic year")
    term = _required(course.term, "course term")
    if academic_year not in _YEAR_LABELS:
        raise ValidationError(f"unknown academic year number: {academic_year}")
    if term not in _TERMS:
        raise ValidationError(f"unknown course term: {term}")

    credit_text = _required(course.credit, "course credit")
    try:
        credit = float(credit_text)
    except ValueError as exc:
        raise ValidationError(f"course credit is not numeric: {credit_text}") from exc
    if not math.isfinite(credit) or credit < 0:
        raise ValidationError(f"course credit is invalid: {credit_text}")

    indicator = _clean(course.examination_indicator)
    assessment_method = {
        "是": "考试",
        "否": "考查",
        "考试": "考试",
        "考查": "考查",
    }.get(indicator)

    total_hours = None
    total_hours_text = _clean(course.total_hours)
    if total_hours_text is not None and re.fullmatch(r"\d+", total_hours_text):
        total_hours = int(total_hours_text)

    return NormalizedCourse(
        course_code=course_code,
        course_name=course_name,
        credit=credit,
        assessment_method=assessment_method,
        recommended_year_semester=f"{_YEAR_LABELS[academic_year]}{term}",
        course_nature=_clean(course.nature),
        course_category=_clean(course.category),
        track=_clean(course.track),
        offering_college=_clean(course.offering_department),
        total_hours=total_hours,
    )


def normalize_plan(plan: DiscoveredPlan) -> NormalizedPlan:
    major = plan.major
    year = _required(major.year, "plan year")
    department_code = _required(major.department_code, "department code")
    major_code = _required(major.code, "major code")
    major_name = _required(major.name, "major name")
    school_name = _required(major.department_name, "school name")
    normalized_courses = tuple(normalize_course(course) for course in plan.courses)
    if not normalized_courses:
        raise ValidationError(f"plan {year}/{major_code} has no courses")
    return NormalizedPlan(
        year=year,
        department_code=department_code,
        major_code=major_code,
        major_name=major_name,
        school_name=school_name,
        plan_id=f"HIT-{year}-{major_code}",
        courses=normalized_courses,
    )


def plan_to_dict(plan: NormalizedPlan) -> dict[str, object]:
    info = {
        "year": plan.year,
        "major_code": plan.major_code,
        "major_name": plan.major_name,
        "school_name": plan.school_name,
        "plan_ID": plan.plan_id,
    }
    courses: list[dict[str, object]] = []
    for course in plan.courses:
        output: dict[str, object] = {
            "course_code": course.course_code,
            "course_name": course.course_name,
            "credit": course.credit,
            "recommended_year_semester": course.recommended_year_semester,
        }
        optional_fields = (
            ("assessment_method", course.assessment_method),
            ("course_nature", course.course_nature),
            ("course_category", course.course_category),
            ("track", course.track),
            ("offering_college", course.offering_college),
            ("total_hours", course.total_hours),
        )
        output.update({key: value for key, value in optional_fields if value is not None})
        courses.append(output)
    return {"info": info, "courses": courses}
