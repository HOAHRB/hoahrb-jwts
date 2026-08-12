"""Typed records crossing the crawler's source and output boundaries."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Department:
    code: str
    name: str


@dataclass(frozen=True)
class Major:
    year: str
    department_code: str
    department_name: str
    code: str
    name: str
    category: str = "本"


@dataclass(frozen=True)
class Catalog:
    years: tuple[str, ...]
    departments: tuple[Department, ...]


@dataclass(frozen=True)
class SourceCourse:
    code: str
    name: str
    english_name: str | None
    academic_year: str
    term: str
    offering_department: str | None
    nature: str | None
    category: str | None
    track: str | None
    credit: str
    total_hours: str | None
    examination_indicator: str | None


@dataclass(frozen=True)
class PlanPage:
    courses: tuple[SourceCourse, ...]
    page_no: int
    page_count: int
    page_size: int | None


@dataclass(frozen=True)
class DiscoveredPlan:
    major: Major
    courses: tuple[SourceCourse, ...]


@dataclass(frozen=True)
class NormalizedCourse:
    course_code: str
    course_name: str
    credit: float
    assessment_method: str | None
    recommended_year_semester: str
    course_nature: str | None
    course_category: str | None
    track: str | None
    offering_college: str | None
    total_hours: int | None


@dataclass(frozen=True)
class NormalizedPlan:
    year: str
    department_code: str
    major_code: str
    major_name: str
    school_name: str
    plan_id: str
    courses: tuple[NormalizedCourse, ...]
    category: str = "本"
