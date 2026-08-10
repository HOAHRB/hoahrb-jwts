"""Deterministic and transactional publication for generated major data."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import toml

from .errors import PublicationError, ValidationError
from .grade_summary import GradeSummary
from .models import NormalizedPlan
from .normalize import plan_to_dict


@dataclass(frozen=True)
class PublicationSummary:
    added: int
    updated: int
    removed: int
    unchanged: int


@dataclass(frozen=True)
class GradePublicationSummary:
    courses: int
    added: int
    updated: int
    unchanged: int


def _plan_filename(plan: NormalizedPlan) -> str:
    safe_school_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", plan.school_name).rstrip(" .")
    safe_major_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", plan.major_name).rstrip(" .")
    if not safe_school_name or not safe_major_name:
        raise ValidationError(f"major {plan.year}/{plan.major_code} has no usable filename")
    # In the code segment after the catalog's full college code, E denotes a
    # second bachelor's degree, while F and Y denote minors. In a code such as
    # 13BY031, B is the final character of the college code and Y is the actual
    # plan marker. College codes are not fixed-width; do not infer their
    # boundary from character count.
    college_suffix = plan.major_code.removeprefix(plan.department_code).upper()
    is_y_minor = college_suffix.startswith("Y")
    plan_category = (
        "第二学士学位"
        if "E" in college_suffix
        else "辅修"
        if "F" in college_suffix or is_y_minor
        else "本"
    )
    name_parts = [plan_category, plan.year, safe_school_name]
    if safe_major_name != safe_school_name:
        name_parts.append(safe_major_name)
    # Y and F plans can have the same year, school, and major name. Keep Y
    # plans under the `辅修_*.toml` namespace while making their filenames
    # unambiguous and stable.
    if is_y_minor:
        name_parts.append(plan.major_code)
    return "_".join(name_parts) + ".toml"


def _validate_plans(
    plans: Sequence[NormalizedPlan], requested_years: set[str] | None = None
) -> tuple[dict[str, object], dict[str, NormalizedPlan]]:
    if not plans:
        raise ValidationError("no normalized execution plans to publish")
    if requested_years is not None:
        missing_years = requested_years - {plan.year for plan in plans}
        if missing_years:
            year = sorted(missing_years)[0]
            raise ValidationError(f"no valid execution plans for requested year {year}")

    seen_ids: set[str] = set()
    seen_identities: set[tuple[str, str]] = set()
    seen_filenames: dict[str, NormalizedPlan] = {}
    plan_files: dict[str, NormalizedPlan] = {}
    mapping: dict[str, dict[str, object]] = {}
    for plan in plans:
        identity = (plan.year, plan.major_code)
        if requested_years is not None and plan.year not in requested_years:
            raise ValidationError(f"plan year {plan.year} is outside requested years")
        if plan.plan_id in seen_ids:
            raise ValidationError(f"duplicate plan ID: {plan.plan_id}")
        if identity in seen_identities:
            raise ValidationError(f"duplicate plan identity: {plan.year}/{plan.major_code}")
        expected_id = f"HIT-{plan.year}-{plan.major_code}"
        if plan.plan_id != expected_id:
            raise ValidationError(f"plan ID is not deterministic for {plan.year}/{plan.major_code}")

        filename = _plan_filename(plan)
        if filename in seen_filenames:
            existing = seen_filenames[filename]
            raise ValidationError(
                f"duplicate generated filename: {filename} "
                f"({existing.year}/{existing.major_code} and {plan.year}/{plan.major_code})"
            )
        if not plan.courses or len(plan.courses) != len(set(plan.courses)):
            raise ValidationError(f"duplicate course entries in plan {plan.year}/{plan.major_code}")

        seen_ids.add(plan.plan_id)
        seen_identities.add(identity)
        seen_filenames[filename] = plan
        plan_files[filename] = plan
        mapping.setdefault(plan.year, {})[plan.major_code] = {
            "name": plan.major_name,
            "plan_ID": plan.plan_id,
            "school_name": plan.school_name,
            "majors": [],
        }

    sorted_mapping = {
        year: {code: mapping[year][code] for code in sorted(mapping[year])}
        for year in sorted(mapping)
    }
    return sorted_mapping, dict(sorted(plan_files.items()))


def build_major_mapping(plans: Sequence[NormalizedPlan]) -> dict[str, object]:
    """Build the generated mapping in stable year/code order."""

    mapping, _ = _validate_plans(plans)
    return mapping


def _load_existing_mapping(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PublicationError("existing major_mapping.json cannot be read") from exc
    if not isinstance(value, dict):
        raise PublicationError("existing major_mapping.json is not an object")
    return value


def _year_from_filename(path: Path) -> str | None:
    match = re.match(r"(?:(\d{4})_|[^_]+_(\d{4})_)", path.name)
    return (match.group(1) or match.group(2)) if match else None


def _write_toml(path: Path, plan: NormalizedPlan) -> None:
    path.write_text(toml.dumps(plan_to_dict(plan)), encoding="utf-8")


def _json_text(mapping: dict[str, object]) -> str:
    return json.dumps(mapping, ensure_ascii=False, indent=2) + "\n"


def _remove_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif path.exists() or path.is_symlink():
        path.unlink()


def _summary(
    existing_plans: Path, staged_plans: Path, requested_years: set[str]
) -> PublicationSummary:
    existing = (
        {
            path.name: path.read_bytes()
            for path in existing_plans.glob("*.toml")
            if _year_from_filename(path) in requested_years
        }
        if existing_plans.exists()
        else {}
    )
    staged = {
        path.name: path.read_bytes()
        for path in staged_plans.glob("*.toml")
        if _year_from_filename(path) in requested_years
    }
    added = len(set(staged) - set(existing))
    removed = len(set(existing) - set(staged))
    common = set(existing) & set(staged)
    updated = sum(existing[name] != staged[name] for name in common)
    unchanged = len(common) - updated
    return PublicationSummary(added=added, updated=updated, removed=removed, unchanged=unchanged)


def publish_plans(
    data_dir: Path, plans: Sequence[NormalizedPlan], requested_years: set[str]
) -> PublicationSummary:
    """Validate and atomically replace generated files for requested years."""

    if not requested_years:
        raise ValidationError("at least one requested year is required")
    generated_mapping, generated_files = _validate_plans(plans, requested_years)
    data_dir = Path(data_dir)
    mapping_path = data_dir / "major_mapping.json"
    plans_path = data_dir / "plans"
    existing_mapping = _load_existing_mapping(mapping_path)
    candidate_mapping = {
        year: existing_mapping[year]
        for year in sorted(existing_mapping)
        if year not in requested_years
    }
    candidate_mapping.update(generated_mapping)
    candidate_mapping = {year: candidate_mapping[year] for year in sorted(candidate_mapping)}

    try:
        data_dir.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix=".hoahrb-jwts-stage-", dir=str(data_dir.parent)
        ) as stage_name:
            stage = Path(stage_name)
            staged_plans = stage / "plans"
            staged_plans.mkdir()
            if plans_path.exists():
                for path in plans_path.iterdir():
                    if path.is_file() and _year_from_filename(path) not in requested_years:
                        shutil.copy2(path, staged_plans / path.name)
            for filename, plan in generated_files.items():
                _write_toml(staged_plans / filename, plan)
            staged_mapping = stage / "major_mapping.json"
            staged_mapping.write_text(_json_text(candidate_mapping), encoding="utf-8")
            summary = _summary(plans_path, staged_plans, requested_years)

            data_dir.mkdir(parents=True, exist_ok=True)
            backup_root = Path(
                tempfile.mkdtemp(prefix=".hoahrb-jwts-backup-", dir=str(data_dir.parent))
            )
            backup_plans = backup_root / "plans"
            backup_mapping = backup_root / "major_mapping.json"
            had_plans = plans_path.exists()
            had_mapping = mapping_path.exists()
            plans_backed_up = False
            mapping_backed_up = False
            rollback_incomplete = False
            try:
                if had_plans:
                    os.replace(plans_path, backup_plans)
                    plans_backed_up = True
                if had_mapping:
                    os.replace(mapping_path, backup_mapping)
                    mapping_backed_up = True
                os.replace(staged_plans, plans_path)
                os.replace(staged_mapping, mapping_path)
            except OSError as exc:
                try:
                    if plans_backed_up and plans_path.exists():
                        _remove_path(plans_path)
                    elif not had_plans and plans_path.exists():
                        _remove_path(plans_path)
                    if mapping_backed_up and mapping_path.exists():
                        _remove_path(mapping_path)
                    elif not had_mapping and mapping_path.exists():
                        _remove_path(mapping_path)
                    if plans_backed_up and backup_plans.exists():
                        os.replace(backup_plans, plans_path)
                    if mapping_backed_up and backup_mapping.exists():
                        os.replace(backup_mapping, mapping_path)
                except OSError as restore_error:
                    rollback_incomplete = True
                    raise PublicationError(
                        "publication failed and rollback was incomplete; backup was preserved"
                    ) from restore_error
                raise PublicationError("publication failed; existing data was restored") from exc
            finally:
                if backup_root.exists() and not rollback_incomplete:
                    shutil.rmtree(backup_root, ignore_errors=True)
            return summary
    except PublicationError:
        raise
    except OSError as exc:
        raise PublicationError("could not prepare or publish generated data") from exc


def _validate_grade_summary(summary: GradeSummary) -> None:
    if not isinstance(summary, dict) or not summary:
        raise ValidationError("grade summary is empty")
    for course_code, variants in summary.items():
        if not isinstance(course_code, str) or not course_code.strip():
            raise ValidationError("grade summary contains an invalid course code")
        if not isinstance(variants, dict) or not variants:
            raise ValidationError(f"grade summary {course_code} has no variants")
        for variant, items in variants.items():
            if not isinstance(variant, str) or not variant.strip():
                raise ValidationError(f"grade summary {course_code} has an invalid variant")
            if not isinstance(items, list) or not items:
                raise ValidationError(f"grade summary {course_code}/{variant} has no items")
            for item in items:
                if not isinstance(item, dict):
                    raise ValidationError(
                        f"grade summary {course_code}/{variant} has an invalid item"
                    )
                name = item.get("name")
                if not isinstance(name, str) or not name.strip():
                    raise ValidationError(
                        f"grade summary {course_code}/{variant} has an invalid item name"
                    )
                percent = item.get("percent")
                if percent is not None and (
                    not isinstance(percent, str) or not re.fullmatch(r"\d+(?:\.\d+)?%", percent)
                ):
                    raise ValidationError(
                        f"grade summary {course_code}/{variant} has an invalid percentage"
                    )


def _grade_json_text(summary: GradeSummary) -> str:
    return json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _load_grade_summary_for_diff(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def publish_grade_summary(data_dir: Path, summary: GradeSummary) -> GradePublicationSummary:
    """Atomically replace ``grades_summary.json`` with a validated summary."""

    _validate_grade_summary(summary)
    data_dir = Path(data_dir)
    output_path = data_dir / "grades_summary.json"
    candidate = _grade_json_text(summary)
    existing = _load_grade_summary_for_diff(output_path)
    existing_codes = set(existing)
    new_codes = set(summary)
    added = len(new_codes - existing_codes)
    removed_or_missing = len(existing_codes - new_codes)
    updated = sum(existing.get(code) != summary.get(code) for code in new_codes & existing_codes)
    unchanged = len(new_codes & existing_codes) - updated
    # A removed course is represented as an update from the file consumer's
    # perspective; retain the compact four-field publication summary used by
    # the plan writer without exposing a second removal field.
    updated += removed_or_missing

    temporary_path: Path | None = None
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        fd, raw_temporary_path = tempfile.mkstemp(
            prefix=".hoahrb-jwts-grades-", suffix=".json", dir=str(data_dir)
        )
        temporary_path = Path(raw_temporary_path)
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(candidate)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, output_path)
        temporary_path = None
    except OSError as exc:
        raise PublicationError("could not publish grades_summary.json") from exc
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass

    return GradePublicationSummary(
        courses=len(summary),
        added=added,
        updated=updated,
        unchanged=unchanged,
    )
