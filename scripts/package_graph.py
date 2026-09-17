"""Read the project's build graph from RPM specs, without building packages."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from collections.abc import Iterable


DEFAULT_TARGETS = ("fedora-44-x86_64",)
GRAPH_FILE = Path("package-graph.json")
REFRESH_COMMAND = "python3 scripts/rebuild.py graph --refresh"


class GraphError(RuntimeError):
    pass


@dataclass(frozen=True)
class Package:
    name: str
    path: Path
    version: str
    release: str
    epoch: str = "0"
    inputs: tuple[Path, ...] = ()

    @property
    def version_release(self) -> str:
        return f"{self.version}-{self.release}"

    @property
    def evr(self) -> str:
        return f"{self.epoch}:{self.version_release}"


@dataclass
class Graph:
    target: str
    packages: dict[str, Package]
    dependencies: dict[str, set[str]]
    external: dict[str, set[str]] = field(default_factory=dict)

    def stages(self, selected: set[str] | None = None) -> list[list[str]]:
        pending = set(self.packages) if selected is None else set(selected)
        stages = []
        while pending:
            ready = sorted(name for name in pending if not self.dependencies[name] & pending)
            if not ready:
                edges = ", ".join(
                    f"{name} needs {'/'.join(sorted(self.dependencies[name] & pending))}"
                    for name in sorted(pending)
                )
                raise GraphError(f"{self.target}: dependency cycle: {edges}")
            stages.append(ready)
            pending.difference_update(ready)
        return stages


@dataclass(frozen=True)
class Requirement:
    name: str
    operator: str = ""
    version: str = ""


def requirement(text: str) -> Requirement:
    match = re.fullmatch(r"([^\s]+?)(?:\s+(>=|<=|=|>|<)\s+(\S+))?", text.strip())
    if not match or text.startswith("(") or "%" in text:
        raise GraphError(f"unsupported dependency expression: {text!r}")
    return Requirement(match[1], match[2] or "", match[3] or "")


def split_evr(value: str) -> tuple[str, str, str]:
    epoch, sep, rest = value.partition(":")
    if not sep:
        epoch, rest = "0", epoch
    version, sep, release = rest.rpartition("-")
    return epoch, version if sep else rest, release if sep else ""


def compare_versions(left: str, right: str) -> int:
    try:
        import rpm
    except ImportError as exc:
        raise GraphError("install python3-rpm to compare RPM versions") from exc
    return rpm.labelCompare(split_evr(left), split_evr(right))


def satisfies(provided: str, required: Requirement) -> bool:
    if not required.operator:
        return True
    if not provided:
        return False
    # A requirement without a release compares only epoch and version.
    epoch, version, release = split_evr(required.version)
    if not release:
        provided_epoch, provided_version, _ = split_evr(provided)
        provided = f"{provided_epoch}:{provided_version}"
    result = compare_versions(provided, required.version)
    return {"=": result == 0, ">": result > 0, "<": result < 0,
            ">=": result >= 0, "<=": result <= 0}[required.operator]


def rpm_arguments(target: str) -> list[str]:
    match = re.fullmatch(r"fedora-([0-9]+)-([A-Za-z0-9_]+)", target)
    if not match:
        raise GraphError(f"unsupported target {target!r}; use fedora-VERSION-ARCH")
    return ["rpmspec", "--target", match[2], "--define", f"fedora {match[1]}",
            "--define", "dist %{nil}"]


def query(root: Path, path: Path, target: str, *arguments: str) -> str:
    command = rpm_arguments(target) + list(arguments) + [str(path)]
    try:
        result = subprocess.run(command, cwd=root, text=True, capture_output=True, check=True)
    except FileNotFoundError as exc:
        raise GraphError("rpmspec is missing; install rpm-build and Fedora RPM macros") from exc
    except subprocess.CalledProcessError as exc:
        raise GraphError(f"{target}: cannot parse {path}: {exc.stderr.strip()}") from exc
    return result.stdout


def spec_paths(root: Path) -> list[Path]:
    """Use the repository's package layout, excluding ignored build outputs."""
    root = root.resolve()
    paths = sorted(root.glob("*/*.spec"))
    if (root / ".git").exists():
        try:
            result = subprocess.run(
                ["git", "-C", str(root), "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
                text=True, capture_output=True, check=True,
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            raise GraphError(f"cannot read the Git package inventory in {root}") from exc
        included = set(result.stdout.split("\0"))
        paths = [path for path in paths if path.relative_to(root).as_posix() in included]
    return paths


def file_capabilities(expanded: str) -> set[str]:
    """Infer module names from literal %files paths, not project name guesses."""
    capabilities: set[str] = set()
    in_files = False
    for line in expanded.splitlines():
        if re.match(r"^%files\b", line):
            in_files = True
            continue
        if re.match(r"^%(?:package|description|prep|build|install|check|changelog|pre|post|preun|postun|trigger\w*)\b", line):
            in_files = False
        if not in_files or line.lstrip().startswith(("#", "%exclude")):
            continue
        pc = re.search(r"/pkgconfig/([^/\s*?\[\]{}%]+)\.pc(?:\s|$)", line)
        cmake = re.search(r"/cmake/([^/\s*?\[\]{}%]+)(?:/|\s|$)", line)
        if pc:
            capabilities.add(f"pkgconfig({pc[1]})")
        if cmake:
            capabilities.add(f"cmake({cmake[1]})")
    return capabilities


def package_metadata(root: Path, target: str) -> tuple[dict[str, Package], dict[str, str], set[str]]:
    """Read identities and local inputs without resolving any dependencies."""
    root = root.resolve()
    rpm_arguments(target)
    packages: dict[str, Package] = {}
    expanded_specs: dict[str, str] = {}
    unsupported: set[str] = set()
    architecture = target.rsplit("-", 1)[-1]
    for absolute in spec_paths(root):
        path = absolute.relative_to(root)
        expanded = query(root, path, target, "-P")
        if re.search(r"^%generate_buildrequires\b", expanded, re.MULTILINE):
            raise GraphError(f"{path}: dynamic BuildRequires need a build; not supported by the planner")
        excluded = re.findall(r"^ExcludeArch:\s*(.*)$", expanded, re.MULTILINE)
        exclusive = re.findall(r"^ExclusiveArch:\s*(.*)$", expanded, re.MULTILINE)
        identity = query(root, path, target, "-q", "--srpm", "--qf",
                         "%{NAME}\\t%{EPOCHNUM}\\t%{VERSION}\\t%{RELEASE}\\n").strip().split("\t")
        if len(identity) != 4 or any(not value or "%" in value for value in identity):
            raise GraphError(f"{path}: cannot determine source package identity")
        name, epoch, version, release = identity
        if (any(architecture in line.split() for line in excluded)
                or any(architecture not in line.split() for line in exclusive)):
            unsupported.add(name)
            continue
        if name in packages:
            raise GraphError(f"duplicate source package {name}")
        inputs = {path}
        for source in re.findall(r"^(?:Source|Patch)\d*:\s*(\S+)", expanded, re.MULTILINE):
            if "://" in source:
                continue
            source_path = (root / path.parent / source).resolve()
            if not source_path.is_relative_to(root):
                raise GraphError(f"{path}: local source is outside this repository: {source}")
            if not source_path.is_file():
                raise GraphError(f"{path}: local source is missing: {source}")
            inputs.add(source_path.relative_to(root))
        package = Package(name, path, version, release, epoch, tuple(sorted(inputs)))
        packages[name] = package
        expanded_specs[name] = expanded
    return packages, expanded_specs, unsupported


def build_graph(root: Path, target: str, *, validate: bool = True) -> Graph:
    """Resolve current RPM requirements and providers for refresh/check operations."""
    root = root.resolve()
    packages, expanded_specs, unsupported = package_metadata(root, target)
    requirements: dict[str, list[Requirement]] = {}
    providers: dict[str, dict[str, str]] = {}
    for name, package in packages.items():
        path = package.path
        expanded = expanded_specs[name]
        requirements[name] = [requirement(line) for line in query(
            root, path, target, "-q", "--buildrequires").splitlines() if line.strip()]
        for line in query(root, path, target, "-q", "--builtrpms", "--provides").splitlines():
            capability = requirement(line)
            providers.setdefault(capability.name, {})[name] = capability.version
        for capability in file_capabilities(expanded):
            # These project modules use their source version for .pc/CMake versions.
            providers.setdefault(capability, {}).setdefault(name, package.version)

    graph = Graph(target, packages, {name: set() for name in packages},
                  {name: set() for name in packages})
    for name, needed in requirements.items():
        for dependency in needed:
            choices = providers.get(dependency.name, {})
            if not choices:
                module = re.fullmatch(r"(?:pkgconfig|cmake)\(([^)]+)\)", dependency.name)
                possible = module[1] if module else dependency.name
                local = [candidate for candidate in set(packages) | unsupported
                         if possible == candidate or possible.startswith(candidate + "-")]
                if local and validate:
                    raise GraphError(f"{target}: {name} needs unresolved local provider {dependency.name}")
                if local:
                    graph.dependencies[name].update(candidate for candidate in local if candidate in packages and candidate != name)
                graph.external[name].add(dependency.name)
                continue
            matches = [provider for provider, version in choices.items()
                       if not validate or satisfies(version, dependency)]
            if not matches:
                available = ", ".join(f"{provider} {version}" for provider, version in choices.items())
                raise GraphError(f"{target}: {name} needs {dependency.name} {dependency.operator} "
                                 f"{dependency.version}; planned providers: {available}")
            if len(matches) != 1 and validate:
                raise GraphError(f"{target}: {name} has ambiguous provider for {dependency.name}: "
                                 + ", ".join(sorted(matches)))
            graph.dependencies[name].update(provider for provider in matches if provider != name)
    if validate:
        graph.stages()  # Reject cycles even when none of their packages changed.
    return graph


def _saved_edges(root: Path) -> dict[str, set[str]]:
    try:
        document = json.loads((root / GRAPH_FILE).read_text())
    except (OSError, ValueError) as exc:
        raise GraphError(f"cannot read {GRAPH_FILE}; run {REFRESH_COMMAND}") from exc
    if (not isinstance(document, dict) or type(document.get("schema")) is not int
            or document["schema"] != 1 or not isinstance(document.get("dependencies"), dict)):
        raise GraphError(f"invalid {GRAPH_FILE}; run {REFRESH_COMMAND}")
    saved = document["dependencies"]
    if any(not isinstance(name, str) or not name for name in saved):
        raise GraphError(f"invalid package list in {GRAPH_FILE}; run {REFRESH_COMMAND}")
    dependencies = {}
    for name, values in saved.items():
        if (not isinstance(values, list) or not all(isinstance(value, str) for value in values)
                or len(values) != len(set(values))):
            raise GraphError(f"invalid dependencies for {name}; run {REFRESH_COMMAND}")
        unknown = set(values) - saved.keys()
        if unknown:
            raise GraphError(f"{name} references unknown packages {', '.join(sorted(unknown))}; "
                             f"run {REFRESH_COMMAND}")
        dependencies[name] = set(values)
    return dependencies


def load_graph(root: Path, target: str) -> Graph:
    """Combine saved edges with fresh package versions and local source inputs."""
    dependencies = _saved_edges(root)
    packages, _, _ = package_metadata(root, target)
    missing, removed = packages.keys() - dependencies.keys(), dependencies.keys() - packages.keys()
    if missing or removed:
        details = []
        if missing:
            details.append("missing packages: " + ", ".join(sorted(missing)))
        if removed:
            details.append("removed packages: " + ", ".join(sorted(removed)))
        raise GraphError(f"{target}: {GRAPH_FILE} does not match the specs ({'; '.join(details)}); "
                         f"run {REFRESH_COMMAND}")
    graph = Graph(target, packages, dependencies)
    try:
        graph.stages()
    except GraphError as exc:
        raise GraphError(f"{exc}; run {REFRESH_COMMAND}") from exc
    return graph


def graph_document(graphs: Iterable[Graph]) -> bytes:
    """Store one graph after verifying that the requested targets agree."""
    targets = set()
    shared = None
    first_target = None
    for graph in graphs:
        if graph.target in targets:
            raise GraphError(f"duplicate graph target: {graph.target}")
        if graph.dependencies.keys() != graph.packages.keys():
            raise GraphError(f"{graph.target}: graph package and dependency lists differ")
        for name, dependencies in graph.dependencies.items():
            if not dependencies.issubset(graph.packages):
                raise GraphError(f"{graph.target}: {name} has unresolved graph dependencies")
        graph.stages()
        targets.add(graph.target)
        if shared is None:
            shared = graph.dependencies
            first_target = graph.target
        elif shared != graph.dependencies:
            changed = sorted(name for name in shared.keys() | graph.dependencies.keys()
                             if shared.get(name) != graph.dependencies.get(name))
            raise GraphError(f"a shared graph cannot represent {first_target} and {graph.target}: "
                             f"dependencies differ for {', '.join(changed)}")
    if not targets:
        raise GraphError("specify at least one graph target")
    dependencies = {name: sorted(values) for name, values in shared.items()}
    return (json.dumps({"schema": 1, "dependencies": dependencies}, indent=2, sort_keys=True) + "\n").encode()


def write_graph(root: Path, targets: Iterable[str]) -> list[Graph]:
    """Refresh the shared graph after resolving and comparing requested targets."""
    graphs = [build_graph(root, target) for target in targets]
    content = graph_document(graphs)
    destination = root / GRAPH_FILE
    if destination.is_file() and destination.read_bytes() == content:
        return graphs
    descriptor, temporary = tempfile.mkstemp(prefix=f".{GRAPH_FILE.name}.", dir=root)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(content)
        os.chmod(temporary, destination.stat().st_mode & 0o777 if destination.exists() else 0o644)
        os.replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return graphs


def check_graph(root: Path, targets: Iterable[str]) -> list[Graph]:
    """Check that the shared graph matches every requested target."""
    saved = _saved_edges(root)
    graphs = []
    for target in targets:
        graph = build_graph(root, target)
        if graph.dependencies != saved:
            changed = sorted(name for name in graph.packages.keys() | saved.keys()
                             if graph.dependencies.get(name) != saved.get(name))
            raise GraphError(f"{target}: {GRAPH_FILE} is stale for {', '.join(changed)}; run {REFRESH_COMMAND}")
        graphs.append(graph)
    if not graphs:
        raise GraphError("specify at least one graph target")
    return graphs
