"""Merge local crawl output into a checkout without losing contributed grading."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

import toml
from bs4 import BeautifulSoup

from .errors import ParseError, PublicationError, ValidationError
from .major_rules import category_for


def parse_student_profile(html: str) -> dict[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    labels = {"年级": "year", "专业": "major_name", "学院": "school_name", "专业代码": "major_code"}
    profile = {}
    for header in soup.select("th"):
        key = labels.get(header.get_text(strip=True).rstrip(":："))
        cell = header.find_next_sibling("td")
        if key and cell is not None:
            value = " ".join(cell.get_text(" ", strip=True).split())
            if key in profile and profile[key] != value:
                raise ParseError("student profile contains conflicting identity fields")
            profile[key] = value
    if not all(profile.get(key) for key in ("year", "major_name", "school_name")):
        raise ParseError("student profile lacks year, major or school; refusing to guess")
    if len(profile["year"]) != 4 or not profile["year"].isdigit():
        raise ParseError("student profile has an invalid enrollment year")
    return profile


def parse_default_plan_identity(html: str, profile: dict[str, str]) -> dict[str, str]:
    """Read server-selected account defaults from an unfiltered live plan page."""
    soup = BeautifulSoup(html, "html.parser")
    selected = {}
    for field in ("pageNj", "pageYxdm", "pageZydm"):
        options = soup.select(f'select[name="{field}"] option[selected]')
        if len(options) != 1 or not options[0].get("value", "").strip():
            raise ParseError(f"account plan page lacks a unique selected {field}")
        selected[field] = options[0]
    year = selected["pageNj"]["value"].strip()
    department = selected["pageYxdm"]["value"].strip()
    code = selected["pageZydm"]["value"].strip()
    school = re.sub(r"^\[[^\]]+\]\s*", "", selected["pageYxdm"].get_text(strip=True))
    if year != profile["year"] or school != profile["school_name"]:
        raise ParseError("account default plan disagrees with student profile year or school")
    if not re.fullmatch(r"[A-Za-z0-9]+", code) or not code.startswith(department):
        raise ParseError("account default plan has an invalid major code")
    if profile.get("major_code") and profile["major_code"] != code:
        raise ParseError("account default plan disagrees with student profile major code")
    return {**profile, "major_code": code}


def _json(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def write_files(root: Path, candidates: dict[str, bytes], removed: set[str] | None = None) -> None:
    """Stage changes, retain rollback copies, and leave unchanged files untouched."""
    root.mkdir(parents=True, exist_ok=True)
    journal = root / ".jwts-merge-recovery.json"
    if journal.exists():
        raise PublicationError("previous merge recovery journal exists; restore it before writing")
    changes = {
        name: body
        for name, body in candidates.items()
        if not (root / name).exists() or (root / name).read_bytes() != body
    }
    names = sorted(set(changes) | (removed or set()))
    if not names:
        return
    with tempfile.TemporaryDirectory(prefix=".jwts-merge-", dir=root.parent) as temp:
        stage = Path(temp)
        originals = {
            name: (root / name).read_bytes() if (root / name).exists() else None for name in names
        }
        for n, name in enumerate(names):
            if name in changes:
                with (stage / str(n)).open("wb") as handle:
                    handle.write(changes[name])
                    handle.flush()
                    os.fsync(handle.fileno())
        with (stage / "recovery.json").open("wb") as handle:
            handle.write(
                _json(
                    {
                        name: body.decode("utf-8") if body is not None else None
                        for name, body in originals.items()
                    }
                )
            )
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(stage / "recovery.json", journal)
        applied = []
        try:
            for n, name in enumerate(names):
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                if name in changes:
                    os.replace(stage / str(n), target)
                else:
                    target.unlink()
                applied.append(name)
            journal.unlink()
        except OSError as exc:
            for name in reversed(applied):
                if originals[name] is None:
                    (root / name).unlink(missing_ok=True)
                else:
                    (root / name).write_bytes(originals[name])
            journal.unlink()
            raise PublicationError("merge failed; existing data restored") from exc


def _plans(root: Path) -> dict:
    result = {}
    for path in sorted((root / "plans").glob("*.toml")):
        data = toml.loads(path.read_text(encoding="utf-8"))
        info = data["info"]
        key = (str(info["year"]), info["major_code"])
        if key in result:
            raise ValidationError(f"duplicate plan identity: {key}")
        if info["plan_ID"] != f"HIT-{key[0]}-{key[1]}":
            raise ValidationError(f"invalid plan ID: {key}")
        if not path.name.startswith(info["plan_category"] + "_"):
            raise ValidationError(f"invalid plan filename category: {path.name}")
        result[key] = (path, data)
    return result


@dataclass
class MergeSummary:
    added: int = 0
    updated: int = 0
    unchanged: int = 0
    conflicts: tuple[str, ...] = ()


@contextmanager
def _merge_lock(target: Path):
    target.mkdir(parents=True, exist_ok=True)
    lock = target / ".jwts-merge.lock"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except OSError as exc:
        raise PublicationError("another merge is running or a stale merge lock exists") from exc
    try:
        os.close(descriptor)
        yield
    finally:
        lock.unlink()


def merge_data(source: Path, target: Path) -> MergeSummary:
    with _merge_lock(target):
        return _merge_data(source, target)


def _merge_data(source: Path, target: Path) -> MergeSummary:
    if source.resolve() == target.resolve():
        raise ValidationError("source and target must be different directories")
    incoming, existing = _plans(source), _plans(target)
    if not incoming and not (source / "grade_contributions").exists():
        raise ValidationError("source has no plans or grade contributions")
    result = MergeSummary()
    conflicts = []
    files = {}
    removed = set()
    mapping_path = target / "major_mapping.json"
    mapping = json.loads(mapping_path.read_text(encoding="utf-8")) if mapping_path.exists() else {}
    final = {key: (path.name, deepcopy(data)) for key, (path, data) in existing.items()}
    for key, (path, data) in incoming.items():
        old = existing.get(key)
        if old:
            retained = {c["course_code"]: c for c in data["courses"]}
            for course in old[1]["courses"]:
                if "grade_details" not in course:
                    continue
                code = course["course_code"]
                if code not in retained:
                    raise ValidationError(
                        f"merge would remove grade_details: {key[0]}/{key[1]}/{code}"
                    )
                new = retained[code]
                if "grade_details" in new and new["grade_details"] != course["grade_details"]:
                    conflicts.append(f"{key[0]}/{key[1]}/{code}")
                new["grade_details"] = course["grade_details"]
            if old[0].name != path.name:
                removed.add("plans/" + old[0].name)
        final[key] = (path.name, data)
    for bundle_path in sorted((source / "grade_contributions").glob("*.json")):
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        profile = bundle["profile"]
        if not profile.get("major_code"):
            raise ValidationError(
                f"unresolved account plan in {bundle_path.name}; no files changed"
            )
        key = (profile["year"], profile["major_code"])
        if key not in final:
            raise ValidationError(f"contribution plan is missing: {key}")
        from .writer import _validate_grade_summary

        _validate_grade_summary(bundle["grades"])
        unmatched = set(bundle["grades"]) - {c["course_code"] for c in final[key][1]["courses"]}
        conflicts.extend(f"unmatched:{key[0]}/{key[1]}/{code}" for code in sorted(unmatched))
        for course in final[key][1]["courses"]:
            variants = bundle["grades"].get(course["course_code"], {})
            if not variants:
                continue
            if set(variants) != {"default"}:
                raise ValidationError(
                    "account contribution must contain one composition per course"
                )
            items = variants["default"]
            if "grade_details" in course and course["grade_details"] != items:
                conflicts.append(f"{key[0]}/{key[1]}/{course['course_code']}")
            else:
                course["grade_details"] = items
    filenames = set()
    for key, (name, data) in final.items():
        info = data["info"]
        category = category_for(*key, info["plan_category"])
        if category != info["plan_category"]:
            if key in existing and existing[key][0].name == name:
                removed.add("plans/" + name)
            name = category + name[len(info["plan_category"]):]
            info["plan_category"] = category
        if name.casefold() in filenames:
            raise ValidationError(f"filename collision: {name}")
        filenames.add(name.casefold())
        data["info"].setdefault("study_level", "undergrad")
        body = toml.dumps(data).encode()
        # Keep untouched files byte-for-byte, including comments and formatting.
        old = existing.get(key)
        if old and old[1] == data:
            body = old[0].read_bytes()
        elif key in incoming and incoming[key][1] == data:
            original = toml.loads(incoming[key][0].read_text(encoding="utf-8"))
            if original == data:
                body = incoming[key][0].read_bytes()
        files["plans/" + name] = body
        if not old:
            result.added += 1
        elif old[0].name != name or old[0].read_bytes() != body:
            result.updated += 1
        else:
            result.unchanged += 1
        info = data["info"]
        entry = dict(mapping.get(key[0], {}).get(key[1], {}))
        entry.update(
            name=info["major_name"],
            school_name=info["school_name"],
            plan_ID=info["plan_ID"],
            study_level=info["study_level"],
        )
        entry.setdefault("majors", [])
        mapping.setdefault(key[0], {})[key[1]] = entry
    files["major_mapping.json"] = _json(mapping)
    intro_path = source / "course_introductions.json"
    if intro_path.exists():
        current = target / "course_introductions.json"
        intros = json.loads(current.read_text(encoding="utf-8")) if current.exists() else {}
        intros.update(json.loads(intro_path.read_text(encoding="utf-8")))
        files["course_introductions.json"] = _json(intros)
    # Central grades_summary uses repository IDs, not account course codes.
    # Never replace it with a personal crawl or a local directory snapshot.
    for bundle_path in sorted((source / "grade_contributions").glob("*.json")):
        files["grade_contributions/" + bundle_path.name] = bundle_path.read_bytes()
    if conflicts:
        report = {
            "conflicts": sorted(set(conflicts)),
            "incoming_grade_details": {
                f"{key[0]}/{key[1]}": {
                    c["course_code"]: c["grade_details"]
                    for c in toml.loads(path.read_text(encoding="utf-8"))["courses"]
                    if "grade_details" in c
                }
                for key, (path, _) in incoming.items()
            },
        }
        report_body = _json(report)
        digest = hashlib.sha256(report_body).hexdigest()
        files[f"grade_conflicts/{digest}.json"] = report_body
    write_files(target, files, removed - set(files))
    result.conflicts = tuple(sorted(set(conflicts)))
    return result
