#!/usr/bin/env python3
"""Prepare dependent RPM release bumps locally, then build committed specs in COPR."""

from __future__ import annotations

import argparse
from collections import deque
from dataclasses import dataclass
import io
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import textwrap

from package_graph import (
    DEFAULT_TARGETS, GRAPH_FILE, Graph, GraphError, Package, build_graph,
    check_graph, compare_versions, load_graph, write_graph,
)


class PlanError(RuntimeError):
    pass


MANIFEST = Path("build-plan.json")


def git(root: Path, *arguments: str) -> str:
    try:
        return subprocess.check_output(["git", "-C", str(root), *arguments], text=True, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as exc:
        raise PlanError(exc.stderr.strip()) from exc


def repository_root() -> Path:
    return Path(git(Path.cwd(), "rev-parse", "--show-toplevel").strip())


def resolve_base(root: Path, base: str) -> str:
    return git(root, "rev-parse", "--verify", "--end-of-options", f"{base}^{{commit}}").strip()


def files_under(root: Path, directory: Path, allowed: set[str] | None = None) -> dict[str, bytes]:
    result = {}
    for path in sorted((root / directory).rglob("*")):
        relative = path.relative_to(root)
        if allowed is not None and relative.as_posix() not in allowed:
            continue
        if path.is_symlink():
            raise PlanError(f"symlink in package inputs is not supported: {relative}")
        if path.is_file():
            result[path.relative_to(root / directory).as_posix()] = path.read_bytes()
    return result


def package_inputs(root: Path, package: Package) -> dict[str, bytes]:
    return {path.as_posix(): (root / path).read_bytes() for path in package.inputs or (package.path,)}


def historical_graph(root: Path, target: str) -> Graph:
    # Revisions from before the saved graph was introduced still need their
    # original dependencies when computing the rebuild closure.
    return (load_graph(root, target) if (root / GRAPH_FILE).exists()
            else build_graph(root, target, validate=False))


def fingerprint(root: Path, graphs: list[Graph], updates: dict[Path, bytes] | None = None) -> str:
    updates = updates or {}
    paths = {path for graph in graphs for package in graph.packages.values()
             for path in package.inputs or (package.path,)}
    if (root / GRAPH_FILE).exists():
        paths.add(GRAPH_FILE)
    tracked = (set(git(root, "ls-files", "--cached", "--others", "--exclude-standard", "-z").split("\0"))
               if (root / ".git").exists() else None)
    paths.update(Path(".copr") / name for name in files_under(root, Path(".copr"), tracked))
    digest = hashlib.sha256()
    digest.update(json.dumps(sorted(graph.target for graph in graphs)).encode())
    for path in sorted(paths):
        content = updates.get(path) if path in updates else (root / path).read_bytes()
        digest.update(path.as_posix().encode() + b"\0" + hashlib.sha256(content).digest())
    return digest.hexdigest()


def atomic_write(path: Path, content: bytes) -> None:
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(mode)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


RELEASE = re.compile(rb"(?m)^(Release:[ \t]*%autorelease)(?:[ \t]+-b[ \t]*(\d+))?([ \t]*)(?=\r?$)")


def bumped_release(original: bytes, current: bytes, path: Path) -> bytes:
    if any(len(re.findall(rb"(?m)^Release:", content)) != 1 for content in (original, current)):
        raise PlanError(f"{path}: automatic bumps require one unconditional Release directive")
    baseline = RELEASE.search(original)
    present = RELEASE.search(current)
    if baseline is None or present is None:
        raise PlanError(f"{path}: automatic bumps require Release: %autorelease with an optional -bN")
    release = int(baseline[2] or b"1") + 1
    replacement = present[1] + f" -b{release}".encode() + present[3]
    return current[:present.start()] + replacement + current[present.end():]


@dataclass
class Plan:
    root: Path
    base: str
    graphs: list[Graph]
    changed: set[str]
    affected: set[str]
    reasons: dict[str, set[str]]
    updates: dict[Path, bytes]
    originals: dict[Path, bytes]
    input_fingerprint: str
    base_prepared: bool
    previous: dict[str, Package]
    base_record_rewritten: bool

    def show(self) -> None:
        width = shutil.get_terminal_size(fallback=(120, 24)).columns
        color = sys.stdout.isatty() and "NO_COLOR" not in os.environ and os.environ.get("TERM") != "dumb"

        def style(value: str, code: str) -> str:
            return f"\033[{code}m{value}\033[0m" if color else value

        def wrapped(value: str, prefix: str = "", continuation: str | None = None) -> str:
            return textwrap.fill(value, width=width, initial_indent=prefix,
                                 subsequent_indent=prefix if continuation is None else continuation,
                                 break_long_words=False, break_on_hyphens=False)

        print(style(f"Base: {self.base}", "2"))
        if not self.affected:
            print("No packaging changes.")
            return
        count = len(self.affected)
        changed = len(self.changed & self.affected)
        print(style(f"Build plan: {count} packages", "1"))
        print(wrapped(f"{changed} edited, {count - changed} dependent rebuilds, "
                      f"{len(self.updates)} release bumps"))
        print()
        packages = {name: package for graph in self.graphs for name, package in graph.packages.items()}
        rows = []
        for name in sorted(self.affected):
            package = packages[name]
            old = self.previous.get(name)
            release = package.release
            if package.path in self.updates:
                release = RELEASE.search(self.updates[package.path])[2].decode()
            if old is None:
                change, change_color = "new package", "32"
            elif old.epoch != package.epoch:
                change = f"{old.evr} -> {package.epoch}:{package.version}-{release}"
                change_color = "32"
            elif old.version != package.version:
                change, change_color = f"{old.version} -> {package.version}", "32"
            elif old.release != release:
                change, change_color = f"release {old.release} -> {release}", "33"
            else:
                change, change_color = "-", "2"
            reason = "packaging changed" if name in self.changed else "needs " + ", ".join(sorted(self.reasons[name]))
            rows.append((name, package.version, change, reason, change_color))

        headings = ("Package", "Version", "Change", "Reason")
        widths = [max(len(headings[index]), *(len(row[index]) for row in rows)) for index in range(3)]
        reason_column = sum(widths) + 6
        if width - reason_column >= 24:
            print(style("  ".join(heading.ljust(size) for heading, size in zip(headings, widths)) + "  Reason", "1"))
            print(style("  ".join("-" * size for size in widths) + "  " + "-" * (width - reason_column), "2"))
            for name, version, change, reason, change_color in rows:
                columns = [name.ljust(widths[0]), version.ljust(widths[1]), change.ljust(widths[2])]
                if name in self.changed:
                    columns = [style(value, "32") for value in columns]
                else:
                    columns[2] = style(columns[2], change_color)
                reasons = textwrap.wrap(reason, width=width - reason_column,
                                        break_long_words=False, break_on_hyphens=False)
                print("  ".join(columns) + "  " + reasons[0])
                for continuation in reasons[1:]:
                    print(" " * reason_column + continuation)
        else:
            for name, version, change, reason, change_color in rows:
                edited = name in self.changed
                print(style(name, "1;32" if edited else "1"))
                version_line = wrapped(f"Version: {version}", "  ")
                print(style(version_line, "32") if edited else version_line)
                print(style(wrapped(f"Change: {change}", "  "), "32" if edited else change_color))
                print(style(wrapped(reason, "  "), "2"))
                print()

        for graph in self.graphs:
            print(style(f"\nBuild order: {graph.target}", "1"))
            print(style(wrapped("Packages within a stage can build in parallel."), "2"))
            for index, stage in enumerate(graph.stages(self.affected & graph.packages.keys()), 1):
                prefix = f"  {index}. "
                print(wrapped(", ".join(stage), prefix, " " * len(prefix)))

    def apply(self) -> None:
        # Certify the saved edges before recording a prepared batch. Builds can
        # then trust this graph while its fingerprint and package inputs match.
        check_graph(self.root, (graph.target for graph in self.graphs))
        # Check every file before writing, so a stale plan cannot overwrite edits.
        if fingerprint(self.root, self.graphs) != self.input_fingerprint:
            raise PlanError("package inputs changed after planning; run the planner again")
        for path, original in self.originals.items():
            if (self.root / path).read_bytes() != original:
                raise PlanError(f"{path} changed after planning; run the planner again")
        updates = dict(self.updates)
        originals: dict[Path, bytes | None] = dict(self.originals)
        manifest_path = self.root / MANIFEST
        previous = manifest_path.read_bytes() if manifest_path.exists() else None
        final_fingerprint = fingerprint(self.root, self.graphs, updates)
        try:
            old_manifest = json.loads(previous) if previous else None
            if old_manifest is not None and not isinstance(old_manifest, dict):
                raise ValueError("not an object")
        except ValueError as exc:
            raise PlanError(f"invalid {MANIFEST}; restore it from Git before applying") from exc
        preserve_record = False
        if old_manifest and old_manifest.get("inputs_sha256") == final_fingerprint and not self.updates:
            try:
                validate_preparation(self.root, self.graphs)
                preserve_record = True
            except PlanError:
                # A squash/rebase can remove the recorded base. Applying from
                # a valid earlier baseline regenerates the preparation record.
                pass
        if not preserve_record:
            if not self.base_prepared:
                raise PlanError("the chosen base contains unprepared changes or a stale plan; "
                                "use --base from before your package edits")
            if self.base_record_rewritten and not self.affected:
                raise PlanError("the chosen base has a stale plan referencing rewritten history; "
                                "use --base from before the amended or squashed changes")
            updates[MANIFEST] = (json.dumps({
                "schema": 1, "base": self.base,
                "targets": [graph.target for graph in self.graphs],
                "inputs_sha256": final_fingerprint,
            }, indent=2) + "\n").encode()
            originals[MANIFEST] = previous
        written = []
        try:
            for path, content in updates.items():
                atomic_write(self.root / path, content)
                written.append(path)
        except BaseException:
            for path in written:
                if originals[path] is None:
                    (self.root / path).unlink(missing_ok=True)
                else:
                    atomic_write(self.root / path, originals[path])
            raise
        print(f"Updated {len(self.updates)} release(s). Commit the package changes and {MANIFEST} together.")


def plan(root: Path, base: str = "HEAD", targets: tuple[str, ...] = DEFAULT_TARGETS) -> Plan:
    root = root.resolve()
    revision = resolve_base(root, base)
    tracked = set(git(root, "ls-files", "--cached", "--others", "--exclude-standard", "-z").split("\0"))
    conflicts = git(root, "diff", "--name-only", "--diff-filter=U")
    if conflicts:
        raise PlanError("resolve Git conflicts before preparing packages")
    graphs = [load_graph(root, target) for target in targets]
    packages = {name: package for graph in graphs for name, package in graph.packages.items()}
    if not packages:
        raise PlanError("no package specs found")

    with tempfile.TemporaryDirectory(prefix="hyprland-plan-") as directory:
        baseline_root = Path(directory)
        archive = subprocess.check_output(["git", "-C", str(root), "archive", revision])
        with tarfile.open(fileobj=io.BytesIO(archive)) as archive_file:
            archive_file.extractall(baseline_root, filter="data")
        previous_graphs = [historical_graph(baseline_root, target) for target in targets]
        # Validate the record at the comparison base, not the current record:
        # the latter may describe a later batch or history before a squash.
        base_prepared = True
        base_record_rewritten = False
        if (baseline_root / MANIFEST).exists():
            try:
                record = read_record(baseline_root)
                recorded_graphs = (previous_graphs if tuple(record["targets"]) == targets else
                                   [historical_graph(baseline_root, target) for target in record["targets"]])
                base_prepared = fingerprint(baseline_root, recorded_graphs) == record["inputs_sha256"]
                # Amending history does not invalidate unchanged package inputs
                # as the baseline of a new batch. An empty batch must still use
                # an earlier base to preserve the previous preparation.
                try:
                    git(root, "merge-base", "--is-ancestor", record["base"], revision)
                except PlanError:
                    base_record_rewritten = True
            except PlanError:
                base_prepared = False
        previous = {name: package for graph in previous_graphs for name, package in graph.packages.items()}
        removed = previous.keys() - packages.keys()
        if removed:
            raise PlanError("package removal/rename needs a manual migration: " + ", ".join(sorted(removed)))
        changed = set()
        common_changed = files_under(root, Path(".copr"), tracked) != files_under(baseline_root, Path(".copr"))
        for name, package in packages.items():
            old = previous.get(name)
            if old is None or common_changed or any(
                name not in old_graph.packages or package_inputs(root, graph.packages[name]) != package_inputs(baseline_root, old_graph.packages[name])
                or graph.dependencies[name] != old_graph.dependencies[name]
                for graph, old_graph in zip(graphs, previous_graphs) if name in graph.packages
            ):
                changed.add(name)

        # Both graphs matter: a consumer can drop a build dependency in this update.
        reverse = {name: set() for name in packages}
        for graph in graphs + previous_graphs:
            for consumer, dependencies in graph.dependencies.items():
                for dependency in dependencies:
                    if dependency in reverse and consumer in packages:
                        reverse[dependency].add(consumer)
        affected = set(changed)
        reasons: dict[str, set[str]] = {name: set() for name in packages}
        pending = deque(sorted(changed))
        while pending:
            dependency = pending.popleft()
            for consumer in sorted(reverse[dependency]):
                reasons[consumer].add(dependency)
                if consumer not in affected:
                    affected.add(consumer)
                    pending.append(consumer)

        updates: dict[Path, bytes] = {}
        originals: dict[Path, bytes] = {}
        for name in sorted(affected):
            package = packages[name]
            old = previous.get(name)
            if old is None:
                continue
            # Validate every target, not just the host's conditional branch.
            comparisons = []
            for graph, old_graph in zip(graphs, previous_graphs):
                if name in graph.packages and name in old_graph.packages:
                    comparisons.append(compare_versions(graph.packages[name].evr, old_graph.packages[name].evr))
            if any(comparison < 0 for comparison in comparisons):
                raise PlanError(f"{name}: package version/release went backwards since {revision[:12]}")
            if all(comparison > 0 for comparison in comparisons):
                continue
            if any(comparison > 0 for comparison in comparisons):
                raise PlanError(f"{name}: inconsistent release bump across targets; edit the spec explicitly")
            current = (root / package.path).read_bytes()
            originals[package.path] = current
            updates[package.path] = bumped_release((baseline_root / old.path).read_bytes(), current, package.path)
        return Plan(root, revision, graphs, changed, affected, reasons, updates, originals,
                    fingerprint(root, graphs), base_prepared, previous, base_record_rewritten)


def read_record(root: Path) -> dict:
    try:
        record = json.loads((root / MANIFEST).read_text())
        if (record.get("schema") != 1 or not re.fullmatch(r"[0-9a-f]{40}", record["base"])
                or not isinstance(record["targets"], list) or not record["targets"]
                or not all(isinstance(target, str) for target in record["targets"])
                or len(set(record["targets"])) != len(record["targets"])
                or not re.fullmatch(r"[0-9a-f]{64}", record["inputs_sha256"])):
            raise ValueError("invalid preparation record")
    except (FileNotFoundError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise PlanError(f"missing or invalid {MANIFEST}; run apply before committing") from exc
    return record


def validate_preparation(root: Path, graphs: list[Graph] | None = None) -> Plan:
    """A committed preparation record makes retries independent of push event ranges."""
    record = read_record(root)
    try:
        git(root, "merge-base", "--is-ancestor", record["base"], "HEAD")
    except PlanError as exc:
        raise PlanError(f"{MANIFEST} references a missing or rewritten base commit; "
                        "rerun apply --base <commit-before-your-package-edits>") from exc
    result = plan(root, record["base"], tuple(record["targets"]))
    if graphs is not None and not {graph.target for graph in graphs}.issubset(record["targets"]):
        raise PlanError("enabled build targets were not prepared; rerun apply with all required --target values")
    if result.input_fingerprint != record.get("inputs_sha256"):
        raise PlanError(f"package inputs changed since apply; rerun apply --base {record['base']}")
    if result.updates:
        raise PlanError("preparation is missing release bumps; rerun apply against its recorded base")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("plan", "apply", "check"):
        child = commands.add_parser(command)
        child.add_argument("--base", default=None if command == "check" else "HEAD",
                           help="commit before your package edits (plan/apply default: HEAD; check uses the preparation record)")
        child.add_argument("--target", action="append", help="Fedora chroot; repeat for multiple targets")
    graph_parser = commands.add_parser("graph", help="show, refresh, or check the saved dependency graph")
    graph_parser.add_argument("--target", action="append")
    graph_action = graph_parser.add_mutually_exclusive_group()
    graph_action.add_argument("--refresh", action="store_true", help="regenerate package-graph.json from the specs")
    graph_action.add_argument("--check", action="store_true", help="check saved dependencies against the specs")
    builder = commands.add_parser("build", help="resume/build committed package versions in COPR")
    builder.add_argument("--owner", default=os.environ.get("COPR_OWNER", "giperborey"))
    builder.add_argument("--project", default=os.environ.get("COPR_PROJECT", "Hyprland"))
    builder.add_argument("--repo", help="public clone URL (default: origin)")
    builder.add_argument("--commit", help="commit to build (default: HEAD)")
    builder.add_argument("--jobs", type=int, default=4)
    args = parser.parse_args(argv)
    try:
        root = repository_root()
        if args.command == "build":
            from copr_build import BuildError, build, get_project_targets
            try:
                graphs = [load_graph(root, target) for target in get_project_targets(args.owner, args.project)]
                build(root, graphs, owner=args.owner, project=args.project,
                      repo=args.repo, commit=args.commit, jobs=args.jobs)
            except BuildError as exc:
                raise PlanError(str(exc)) from exc
        elif args.command == "graph":
            targets = tuple(args.target or DEFAULT_TARGETS)
            if args.refresh:
                graphs = write_graph(root, targets)
                print(f"Updated {GRAPH_FILE} from the specs.")
            elif args.check:
                graphs = check_graph(root, targets)
                print(f"{GRAPH_FILE} matches the specs.")
            else:
                graphs = [load_graph(root, target) for target in targets]
            print(f"Shared build order ({', '.join(graph.target for graph in graphs)}):")
            for number, stage in enumerate(graphs[0].stages(), 1):
                print(f"  {number}: {', '.join(stage)}")
        else:
            if args.command == "check":
                targets = args.target or (read_record(root)["targets"] if args.base is None else DEFAULT_TARGETS)
                check_graph(root, tuple(targets))
            result = (validate_preparation(root) if args.command == "check" and args.base is None
                      else plan(root, args.base, tuple(args.target or DEFAULT_TARGETS)))
            result.show()
            if args.command == "apply":
                result.apply()
            elif args.command == "check" and result.updates:
                raise PlanError("missing release bumps; run apply against the same base, then commit the changes")
    except (GraphError, PlanError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
