#!/usr/bin/env python3
"""Check upstream releases and optionally bump eligible RPM package versions."""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from difflib import unified_diff
import logging
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

from github_versions import GitHubError, RateLimitError, latest_version, read_local_token
from package_graph import (
    DEFAULT_TARGETS, GraphError, Requirement, compare_versions, file_capabilities,
    query, requirement, satisfies, spec_paths, split_evr,
)
from upstream_cache import VersionCache
from spec_versions import VersionEditError, apply_version_edits, prepare_version_edit


LOGGER = logging.getLogger(__name__)
REPO_ROOT = Path(__file__).resolve().parents[1]
MANAGED_PACKAGES = {"hyprland", "hyprland-git"}
STATUSES = ("UPDATE", "HELD", "CURRENT", "AHEAD", "ERROR", "BLOCKED", "SKIP")
CACHE_PATH = Path(".cache/upstream-versions.json")


class CheckError(RuntimeError):
    pass


@dataclass(frozen=True)
class Spec:
    name: str
    version: str
    url: str
    path: Path


@dataclass(frozen=True)
class ResultRow:
    package: str
    spec: str
    upstream: str
    status: str
    kind: str = ""
    details: str = ""
    url: str = ""


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", nargs="?", type=Path, default=REPO_ROOT, metavar="DIR",
                        help="package repository root (default: this script's repository)")
    parser.add_argument("--target", default=DEFAULT_TARGETS[0],
                        help=f"Fedora target for RPM conditionals (default: {DEFAULT_TARGETS[0]})")
    parser.add_argument("--updates-only", action="store_true",
                        help="show updates, held releases, and failures; keep all counts in the summary")
    parser.add_argument("--allow-tags", action="store_true",
                        help="if no stable release exists, compare stable numeric tags instead")
    parser.add_argument("--refresh", action="store_true",
                        help="refresh GitHub results instead of reusing the one-hour local cache")
    parser.add_argument("--bump", nargs="*", metavar="PACKAGE",
                        help="update eligible specs; omit package names to update all eligible packages")
    parser.add_argument("--dry-run", action="store_true",
                        help="with --bump, preview spec changes without applying them")
    parser.add_argument("-d", "--debug", action="store_true", help="log progress and diagnostics to stderr")
    args = parser.parse_args(argv)
    if args.dry_run and args.bump is None:
        parser.error("--dry-run requires --bump")
    return args


def parse_spec(spec_path: Path, target: str = DEFAULT_TARGETS[0]) -> Spec:
    """Ask RPM for the active metadata, including macros and conditionals."""
    try:
        value = query(spec_path.parent, Path(spec_path.name), target, "-q", "--srpm", "--qf",
                      "%{NAME}\\t%{VERSION}\\t%{URL}\\n")
    except GraphError as exc:
        raise CheckError(str(exc)) from exc
    fields = value.strip().split("\t")
    if len(fields) != 3 or any(not field or field == "(none)" or "%" in field for field in fields):
        raise CheckError("RPM could not determine Name, Version, and URL")
    return Spec(*fields, path=spec_path)


def extract_repo_info(url: str) -> tuple[str, str]:
    try:
        parsed = urlsplit(url)
        if (parsed.scheme not in {"http", "https"} or parsed.hostname != "github.com"
                or parsed.username is not None or parsed.password is not None
                or parsed.port not in (None, 80 if parsed.scheme == "http" else 443)):
            raise ValueError
        parts = parsed.path.strip("/").split("/")
        owner, repo = parts[0], parts[1].removesuffix(".git")
        if not all(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value) for value in (owner, repo)):
            raise ValueError
    except (ValueError, IndexError) as exc:
        raise CheckError("unsupported upstream URL; expected github.com/OWNER/REPO") from exc
    return owner, repo


def update_blockers(spec: Spec, version: str, target: str,
                    constraints: dict[str, list[tuple[str, Requirement]]]) -> list[str]:
    """Check a candidate against consumers' declared version requirements."""
    provides = query(spec.path.parent, Path(spec.path.name), target,
                     "-q", "--builtrpms", "--provides")
    candidates: dict[str, str] = {}
    declared = set()
    for line in provides.splitlines():
        capability = requirement(line)
        declared.add(capability.name)
        epoch, current_version, _ = split_evr(capability.version)
        # Project the source version onto matching package/alias versions only.
        # Bundled libraries and fixed ABI provides have independent versions.
        if capability.operator == "=" and current_version == spec.version:
            candidates[capability.name] = f"{epoch}:{version}"
    expanded = query(spec.path.parent, Path(spec.path.name), target, "-P")
    for capability in file_capabilities(expanded) - declared:
        # Match the build graph's assumption for this project's .pc/CMake files.
        candidates.setdefault(capability, version)
    blockers = set()
    for capability, candidate in candidates.items():
        for consumer, needed in constraints.get(capability, []):
            if consumer != spec.name and not satisfies(candidate, needed):
                blockers.add(f"{consumer} requires {needed.name} {needed.operator} {needed.version}")
    return sorted(blockers)


def render_report(rows: list[ResultRow], *, updates_only: bool = False) -> None:
    """Keep version strings and links intact, including when the table is wide."""
    visible = [row for row in rows if not updates_only or row.status in {"UPDATE", "HELD", "ERROR", "BLOCKED"}]
    headings = ("Package", "Spec", "Upstream", "Status", "From", "Link")
    cells = [(row.package, row.spec, row.upstream, row.status, row.kind, row.url) for row in visible]
    widths = [max([len(heading), *(len(values[index]) for values in cells)])
              for index, heading in enumerate(headings)]
    if visible:
        print("  ".join(heading.ljust(width) for heading, width in zip(headings, widths)))
        print("  ".join("-" * width for width in widths))
        for row, values in zip(visible, cells):
            print("  ".join(value.ljust(width) for value, width in zip(values, widths)).rstrip())
            if row.details:
                print(f"  {row.details}")
    elif updates_only:
        print("No updates or failed checks.")
    counts = Counter(row.status for row in rows)
    print("Summary: " + ", ".join(f"{counts[status]} {status}" for status in STATUSES) + ".")


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.debug:
        logging.basicConfig(level=logging.DEBUG, format="%(levelname)s: %(message)s")
    root = args.directory.expanduser().resolve()
    try:
        if not root.is_dir():
            raise CheckError(f"not a directory: {root}")
        paths = spec_paths(root)
        if not paths:
            raise CheckError(f"no package specs found under {root} (expected PACKAGE/PACKAGE.spec)")
        compare_versions("1", "1")  # Fail before HTTP calls if python3-rpm is missing.
        local_token = read_local_token(root / ".secrets")
    except (CheckError, GraphError, GitHubError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"Checking {len(paths)} package specs...", file=sys.stderr, flush=True)
    entries: list[Spec | ResultRow] = []
    constraints: dict[str, list[tuple[str, Requirement]]] = {}
    originals: dict[Path, bytes] = {}
    # Read every consumer first, including packages with separate upstream updaters.
    for path in paths:
        try:
            if args.bump is not None:
                originals[path] = path.read_bytes()
            spec = parse_spec(path, args.target)
            needed = [requirement(line) for line in query(
                path.parent, Path(path.name), args.target, "-q", "--buildrequires").splitlines()]
            for dependency in needed:
                if dependency.operator:
                    constraints.setdefault(dependency.name, []).append((spec.name, dependency))
            entries.append(spec)
        except (CheckError, GraphError, OSError) as exc:
            entries.append(ResultRow(path.stem, "?", "?", "ERROR",
                                     details=f"{path.relative_to(root)}: {exc}"))

    selected = set(args.bump) if args.bump else None
    if selected:
        known = {entry.name if isinstance(entry, Spec) else entry.package for entry in entries}
        unknown = selected - known
        if unknown:
            print("error: unknown packages: " + ", ".join(sorted(unknown)), file=sys.stderr)
            return 1

    cache = VersionCache(root / CACHE_PATH)
    cache_hits = 0
    rows = []
    updates: list[tuple[Spec, str]] = []
    rate_limited = False
    for index, entry in enumerate(entries, 1):
        if isinstance(entry, ResultRow):
            rows.append(entry)
            continue
        spec = entry
        if selected and spec.name not in selected:
            continue
        relative = spec.path.relative_to(root)
        LOGGER.debug("Checking %s/%s: %s", index, len(paths), relative)
        name, version = spec.name, spec.version
        try:
            if name in MANAGED_PACKAGES:
                rows.append(ResultRow(name, version, "-", "SKIP",
                                      details="Revision metadata is managed by hyprland-git/update.py."))
                continue
            owner, repo = extract_repo_info(spec.url)
            upstream = None if args.refresh else cache.get(owner, repo, allow_tags=args.allow_tags)
            if upstream is not None:
                cache_hits += 1
            else:
                if rate_limited:
                    rows.append(ResultRow(name, version, "?", "BLOCKED",
                                          details="Not checked because GitHub's rate limit was reached."))
                    continue
                credentials = {"token": local_token} if local_token is not None else {}
                upstream = latest_version(owner, repo, allow_tags=args.allow_tags, **credentials)
                cache.put(owner, repo, upstream, allow_tags=args.allow_tags)
            comparison = compare_versions(version, upstream.version)
            status = "UPDATE" if comparison < 0 else "AHEAD" if comparison > 0 else "CURRENT"
            blockers = update_blockers(spec, upstream.version, args.target, constraints) if status == "UPDATE" else []
            if blockers:
                status = "HELD"
            elif status == "UPDATE" and args.bump is not None:
                updates.append((spec, upstream.version))
            rows.append(ResultRow(name, version, upstream.tag, status, upstream.kind,
                                  details="; ".join(blockers), url=upstream.url))
        except RateLimitError as exc:
            rows.append(ResultRow(name, version, "?", "ERROR", details=f"{relative}: {exc}"))
            rate_limited = True
        except (CheckError, GraphError, GitHubError, OSError) as exc:
            rows.append(ResultRow(name, version, "?", "ERROR", details=f"{relative}: {exc}"))

    cache.save()
    if cache_hits:
        print(f"Reused {cache_hits} cached upstream result(s); --refresh to update.", file=sys.stderr)
    render_report(rows, updates_only=args.updates_only)
    if not any(row.status != "SKIP" for row in rows):
        print("No packages eligible for upstream checks.", file=sys.stderr)
        return 1
    failed = any(row.status in {"ERROR", "BLOCKED"} for row in rows)
    if args.bump is None:
        return int(failed)
    if failed:
        print("No specs changed: resolve the failed checks before bumping versions.", file=sys.stderr)
        return 1
    try:
        if any(path.read_bytes() != original for path, original in originals.items()):
            raise VersionEditError("specs changed during the scan; rerun before bumping versions")
        edits = [prepare_version_edit(spec.path, spec.version, version, args.target)
                 for spec, version in updates]
        if not edits:
            print("No eligible versions to bump.")
        elif args.dry_run:
            for edit in edits:
                relative = edit.path.relative_to(root).as_posix()
                print("".join(unified_diff(
                    edit.original.decode().splitlines(keepends=True),
                    edit.updated.decode().splitlines(keepends=True),
                    fromfile=f"a/{relative}", tofile=f"b/{relative}",
                )), end="")
            print(f"Would update {len(edits)} spec(s). Run without --dry-run to apply.")
        else:
            if any(path.read_bytes() != original for path, original in originals.items()):
                raise VersionEditError("specs changed during preparation; rerun before bumping versions")
            apply_version_edits(edits)
            print(f"Updated {len(edits)} spec(s). Next: python3 scripts/rebuild.py plan, "
                  "then python3 scripts/rebuild.py apply.")
    except (VersionEditError, GraphError, OSError, UnicodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
