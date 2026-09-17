"""Prepare and apply small, validated RPM version updates."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import tempfile

from package_graph import GraphError, compare_versions, query
from rebuild import atomic_write


class VersionEditError(RuntimeError):
    pass


@dataclass(frozen=True)
class VersionEdit:
    path: Path
    original: bytes
    updated: bytes


NUMBER = re.compile(rb"[0-9]+(?:\.[0-9]+)*")
DIRECTIVE = re.compile(rb"^([ \t]*(Version|Release):[ \t]*)(.*?)(\r?\n)?$", re.IGNORECASE)
CONDITIONAL = re.compile(rb"^[ \t]*%(if(?:arch|narch|os|nos)?|endif)(?:\s|$)")
RELEASE = re.compile(rb"(?:%autorelease(?:[ \t]+-b[ \t]*[0-9]+)?|([0-9]+)(%\{\?dist\})?)")


def _manual(path: Path, reason: str) -> VersionEditError:
    return VersionEditError(f"{path}: {reason}; edit this spec manually")


def _identity(path: Path, target: str) -> tuple[str, str]:
    fields = query(path.parent, Path(path.name), target, "-q", "--srpm", "--qf",
                   "%{NAME}\\t%{VERSION}\\n").strip().split("\t")
    if len(fields) != 2 or any(not field or "%" in field or field == "(none)" for field in fields):
        raise _manual(path, "RPM could not determine Name and Version")
    return fields[0], fields[1]


def prepare_version_edit(path: Path, old_version: str, new_version: str, target: str) -> VersionEdit:
    """Validate an upgrade without changing the original spec."""
    path = path.absolute()
    try:
        if path.is_symlink():
            raise _manual(path, "symlink specs cannot be updated automatically")
        original = path.read_bytes()
        old, new = old_version.encode("ascii"), new_version.encode("ascii")
        if not NUMBER.fullmatch(old) or not NUMBER.fullmatch(new):
            raise _manual(path, "automatic updates require numeric dotted versions")
        if compare_versions(new_version, old_version) <= 0:
            raise VersionEditError(f"{path}: {new_version} is not newer than {old_version}")

        lines = original.splitlines(keepends=True)
        found: dict[bytes, list[tuple[int, re.Match[bytes]]]] = {b"version": [], b"release": []}
        depth = 0
        for index, line in enumerate(lines):
            conditional = CONDITIONAL.match(line)
            if conditional:
                depth += -1 if conditional[1] == b"endif" else 1
            directive = DIRECTIVE.fullmatch(line)
            if directive:
                if depth:
                    raise _manual(path, "Version and Release must be unconditional")
                found[directive[2].lower()].append((index, directive))
        if any(len(matches) != 1 for matches in found.values()):
            raise _manual(path, "automatic updates require exactly one Version and one Release directive")

        for name, matches in found.items():
            index, match = matches[0]
            # Keep indentation, trailing whitespace, comments, and original newlines.
            value = re.fullmatch(rb"(.*?)([ \t]+#[^\r\n]*)?([ \t]*)", match[3])
            assert value is not None
            token, comment, whitespace = value[1], value[2] or b"", value[3]
            if name == b"version":
                if not NUMBER.fullmatch(token):
                    raise _manual(path, "Version must be a literal numeric value")
                if token != old:
                    raise VersionEditError(f"{path}: Version changed after checking; run the checker again")
                replacement = new
            else:
                release = RELEASE.fullmatch(token)
                if release is None:
                    raise _manual(path, "Release must be %autorelease with an optional -bN, or a number with optional %{?dist}")
                replacement = b"1" + (release[2] or b"") if release[1] else b"%autorelease"
            lines[index] = match[1] + replacement + comment + whitespace + (match[4] or b"")

        updated = b"".join(lines)
        before = _identity(path, target)
        if before[1] != old_version:
            raise VersionEditError(f"{path}: Version changed after checking; run the checker again")
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.stem}-", suffix=".spec") as stream:
            stream.write(updated)
            stream.flush()
            after = _identity(Path(stream.name), target)
        if after != (before[0], new_version):
            raise _manual(path, "the candidate does not preserve the RPM name and expected version")
        return VersionEdit(path, original, updated)
    except (OSError, UnicodeError, GraphError) as exc:
        raise VersionEditError(f"{path}: cannot prepare version update: {exc}") from exc


def apply_version_edits(edits: list[VersionEdit]) -> None:
    """Check the whole batch before writing, and restore earlier writes on failure."""
    if len({edit.path.absolute() for edit in edits}) != len(edits):
        raise VersionEditError("duplicate spec in version update batch")
    try:
        for edit in edits:
            if edit.path.is_symlink() or edit.path.read_bytes() != edit.original:
                raise VersionEditError(f"{edit.path}: changed after checking; run the checker again")
    except OSError as exc:
        raise VersionEditError(f"cannot verify version update batch: {exc}") from exc

    written = []
    try:
        for edit in edits:
            atomic_write(edit.path, edit.updated)
            written.append(edit)
    except BaseException as exc:
        failures = []
        for edit in reversed(written):
            try:
                atomic_write(edit.path, edit.original)
            except OSError:
                failures.append(str(edit.path))
        if failures:
            raise VersionEditError("version update failed; could not restore " + ", ".join(failures)) from exc
        if not isinstance(exc, Exception):
            raise
        raise VersionEditError(f"version update failed; earlier edits restored: {exc}") from exc
