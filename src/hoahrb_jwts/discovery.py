"""Cohort-scoped traversal and conflict detection."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import Protocol

from .errors import ValidationError
from .major_rules import rule_for
from .models import Catalog, Department, DiscoveredPlan, Major, SourceCourse


class TeachingSystemGateway(Protocol):
    def get_catalog(self) -> Catalog: ...

    def get_majors(self, year: str, department: Department) -> tuple[Major, ...]: ...

    def get_plan(self, major: Major, page_size: int = 500) -> tuple[SourceCourse, ...]: ...


def _deduplicate_courses(courses: Sequence[SourceCourse], major: Major) -> tuple[SourceCourse, ...]:
    seen: set[SourceCourse] = set()
    result: list[SourceCourse] = []
    for course in courses:
        if not course.code.strip():
            raise ValidationError(f"empty course code in {major.year}/{major.code}")
        if course in seen:
            continue
        seen.add(course)
        result.append(course)
    return tuple(result)


def _same_plan(left: DiscoveredPlan, right: DiscoveredPlan) -> bool:
    return left.major == right.major and _deduplicate_courses(
        left.courses, left.major
    ) == _deduplicate_courses(right.courses, right.major)


def _is_redundant_w_variant(
    plan: DiscoveredPlan, discovered: dict[tuple[str, str], DiscoveredPlan]
) -> bool:
    code = plan.major.code
    if not code.endswith("W"):
        return False
    base = discovered.get((plan.major.year, code[:-1]))
    # A W-suffixed plan is published only when it adds courses not already
    # present in its same-cohort, non-W counterpart.
    return base is not None and set(plan.courses).issubset(base.courses)


def _curate_major_name(major: Major) -> Major:
    rule = rule_for(major.year, major.code)
    return replace(major, name=rule.name) if rule is not None and rule.name else major


def discover_plans(
    gateway: TeachingSystemGateway,
    years: tuple[str, ...],
) -> tuple[DiscoveredPlan, ...]:
    """Discover only non-empty plans returned for the requested cohorts."""

    requested_years = tuple(dict.fromkeys(years))
    if not requested_years:
        raise ValidationError("at least one requested year is required")

    catalog = gateway.get_catalog()
    missing_years = [year for year in requested_years if year not in catalog.years]
    if missing_years:
        raise ValidationError(f"requested year {missing_years[0]} is not present in the catalog")

    discovered: dict[tuple[str, str], DiscoveredPlan] = {}
    major_identities: dict[tuple[str, str], Major] = {}
    valid_counts = dict.fromkeys(requested_years, 0)
    queried_majors: set[tuple[str, str, str, str, str, str]] = set()

    for year in requested_years:
        for department in catalog.departments:
            majors = gateway.get_majors(year, department)
            for major in majors:
                major = _curate_major_name(major)
                if major.year != year:
                    raise ValidationError(
                        f"major {major.code} is scoped to {major.year}, not requested year {year}"
                    )
                rule = rule_for(major.year, major.code)
                if rule is not None and not rule.publish:
                    continue
                major_key = (
                    major.year,
                    major.department_code,
                    major.department_name,
                    major.code,
                    major.name,
                    major.category,
                )
                if major_key in queried_majors:
                    continue
                queried_majors.add(major_key)
                identity = (major.year, major.code)
                existing_major = major_identities.get(identity)
                if existing_major is not None and existing_major != major:
                    raise ValidationError(
                        f"conflicting execution plans for {major.year}/{major.code}"
                    )
                major_identities[identity] = major
                courses = gateway.get_plan(major)
                if not courses:
                    continue
                normalized_courses = _deduplicate_courses(courses, major)
                if not normalized_courses:
                    continue
                candidate = DiscoveredPlan(major=major, courses=normalized_courses)
                existing = discovered.get(identity)
                if existing is not None:
                    if not _same_plan(existing, candidate):
                        raise ValidationError(
                            f"conflicting execution plans for {major.year}/{major.code}"
                        )
                    continue
                discovered[identity] = candidate
                valid_counts[year] += 1

    for year, count in valid_counts.items():
        if count == 0:
            raise ValidationError(f"no valid execution plans for requested year {year}")

    return tuple(
        sorted(
            (plan for plan in discovered.values() if not _is_redundant_w_variant(plan, discovered)),
            key=lambda plan: (plan.major.year, plan.major.code, plan.major.department_code),
        )
    )
