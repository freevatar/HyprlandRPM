"""Build the committed package graph in COPR, resuming remote work on reruns."""

from __future__ import annotations

import base64
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
from typing import TYPE_CHECKING, Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from package_graph import split_evr

if TYPE_CHECKING:
    from package_graph import Graph, Package

API = "https://copr.fedorainfracloud.org/api_3/"
TERMINAL = frozenset({"succeeded", "failed", "canceled", "skipped"})
POLL_SECONDS = 15
WAIT_SECONDS = 6 * 60 * 60
PAGE_SIZE = 100


class BuildError(RuntimeError):
    """A build could not be submitted, recovered, or completed."""


def log(message: str) -> None:
    print(f"==> {message}", file=sys.stderr, flush=True)


def _object(value: Any, description: str) -> dict:
    if not isinstance(value, dict):
        raise BuildError(f"COPR returned invalid {description}")
    return value


class Copr:
    """Small API client. Submission requests are deliberately never retried."""

    def __init__(self, owner: str, project: str, *, authenticated: bool = True):
        self.owner, self.project = owner, project
        self.authorization: str | None = None
        if authenticated:
            login, token = os.environ.get("COPR_LOGIN"), os.environ.get("COPR_TOKEN")
            if not login or not token:
                raise BuildError("Set COPR_LOGIN and COPR_TOKEN to your COPR API credentials")
            self.authorization = "Basic " + base64.b64encode(
                f"{login}:{token}".encode()
            ).decode()
        self.records: list[dict] = []
        self.origins: dict[int, dict] = {}
        self.lock = threading.Lock()
        self.stopping = threading.Event()

    def request(self, endpoint: str, *, data: dict | None = None,
                auth: bool = False, **query: str | int) -> dict:
        url = API + endpoint
        if query:
            url += "?" + urlencode(query)
        headers = {"Accept": "application/json", "User-Agent": "HyprlandRPM-build/1"}
        if auth:
            if not self.authorization:
                raise BuildError("COPR API credentials are missing")
            headers["Authorization"] = self.authorization
        payload = None
        if data is not None:
            payload = json.dumps(data).encode()
            headers["Content-Type"] = "application/json"
        request = Request(url, data=payload, headers=headers)
        attempts = 1 if data is not None else 3
        for attempt in range(attempts):
            try:
                with urlopen(request, timeout=30) as response:
                    return _object(json.load(response), endpoint)
            except HTTPError as exc:
                if exc.code not in {408, 429, 500, 502, 503, 504} or attempt + 1 == attempts:
                    raise BuildError(f"COPR {endpoint} returned HTTP {exc.code}") from None
            except (URLError, TimeoutError, OSError, ValueError):
                if attempt + 1 == attempts:
                    raise BuildError(f"Unable to read the COPR response for {endpoint}") from None
            time.sleep(attempt + 1)
        raise AssertionError("unreachable")

    def targets(self) -> list[str]:
        project = self.request("project", ownername=self.owner, projectname=self.project)
        targets = project.get("chroot_repos")
        if not isinstance(targets, dict) or not targets:
            raise BuildError("The COPR project has no enabled targets")
        return sorted(targets)

    @staticmethod
    def build_id(record: dict) -> int:
        value = record.get("id")
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise BuildError("COPR returned an invalid build ID")
        return value

    def preflight(self, targets: set[str]) -> None:
        enabled = set(self.targets())
        if targets != enabled:
            raise BuildError(
                "Resolve every enabled COPR target before building: " + ", ".join(sorted(enabled))
            )
        # This endpoint validates access and target names; it creates no build.
        self.request("build/check-before-build", auth=True, data={
            "ownername": self.owner, "projectname": self.project,
            "chroots": sorted(targets),
        })
        offset = 0
        while True:
            page = self.request("build/list", ownername=self.owner, projectname=self.project,
                                limit=PAGE_SIZE, offset=offset, order="id", order_type="DESC")
            items = page.get("items")
            if not isinstance(items, list):
                raise BuildError("COPR returned an invalid build list")
            for item in items:
                record = _object(item, "build")
                self._validate_record(record)
                self.records.append(record)
                source = record.get("source_package") or {}
                if (record["state"] not in TERMINAL and not source.get("name")
                        and record.get("project_dirname", self.project) == self.project
                        and (not record["chroots"] or targets.intersection(record["chroots"]))):
                    # Direct SCM submissions need not have a package name yet.
                    # Looking only at packagename-filtered history would miss
                    # precisely the build left by a lost submission response.
                    config = self.request(f"build/source-build-config/{self.build_id(record)}")
                    if config.get("source_type") == "scm":
                        self.origins[self.build_id(record)] = config.get("source_dict") or {}
            if len(items) < PAGE_SIZE:
                break
            offset += len(items)
        self.records.sort(key=self.build_id, reverse=True)

    def _validate_record(self, record: dict) -> None:
        self.build_id(record)
        if record.get("ownername") != self.owner or record.get("projectname") != self.project:
            raise BuildError("COPR returned a build from another project")
        if not isinstance(record.get("chroots"), list) or not record.get("state"):
            raise BuildError("COPR returned incomplete build state")

    def _same_origin(self, record: dict, package: Package, repo: str) -> bool:
        source = self.origins.get(self.build_id(record), {})
        return (
            str(source.get("clone_url", "")).removesuffix(".git").rstrip("/")
            == repo.removesuffix(".git").rstrip("/")
            and source.get("subdirectory", "").strip("/") == str(package.path.parent).strip("/")
            and source.get("spec") in {"", package.path.name}
        )

    @staticmethod
    def matches(record: dict, package: Package) -> bool:
        source = record.get("source_package") or {}
        version = source.get("version")
        # COPR includes a nonzero epoch in its source version; an omitted
        # epoch and an explicit zero both identify the default RPM epoch.
        return (source.get("name") == package.name and isinstance(version, str)
                and split_evr(version) == (package.epoch, package.version, package.release))

    def wait(self, record: dict) -> dict:
        build_id = self.build_id(record)
        deadline = time.monotonic() + WAIT_SECONDS
        previous = None
        while record["state"] not in TERMINAL:
            if self.stopping.is_set():
                raise BuildError(f"Stopped watching COPR build {build_id}; rerun to resume it")
            if record["state"] != previous:
                log(f"COPR build {build_id}: {record['state']}")
                previous = record["state"]
            if time.monotonic() >= deadline:
                raise BuildError(f"COPR build {build_id} is still active; rerun to resume it")
            time.sleep(POLL_SECONDS)
            record = self.request(f"build/{build_id}")
            self._validate_record(record)
        return record

    def succeeded(self, record: dict, package: Package, target: str) -> bool:
        if not self.matches(record, package) or target not in record["chroots"]:
            return False
        # A build can succeed on one architecture and fail on another.
        chroot = self.request("build-chroot", build_id=self.build_id(record), chrootname=target)
        return chroot.get("state") == "succeeded"

    def ensure(self, package: Package, target: str, repo: str, commit: str) -> None:
        label = f"{package.name}-{package.version_release} ({target})"
        with self.lock:
            records = sorted(self.records, key=self.build_id, reverse=True)
        relevant = []
        for record in records:
            # Subprojects have separate repositories and cannot satisfy this build.
            if record.get("project_dirname", self.project) != self.project:
                continue
            if record["chroots"] and target not in record["chroots"]:
                continue
            source = record.get("source_package") or {}
            unknown = not source.get("version") or not source.get("name")
            if source.get("name") != package.name and not (unknown and self._same_origin(record, package, repo)):
                continue
            relevant.append(record)

        # Drain every relevant active build before reusing any success. An older
        # submission can still finish after a newer successful build.
        for index, record in enumerate(relevant):
            if record["state"] not in TERMINAL:
                log(f"Resuming COPR build {self.build_id(record)} for {label}")
                relevant[index] = self.wait(record)
        for record in relevant:
            source = record.get("source_package") or {}
            unknown = not source.get("version") or not source.get("name")
            if self.succeeded(record, package, target):
                log(f"Already built: {label}")
                return
            if self.matches(record, package) or unknown:
                # Do not let an older success hide the newest failed attempt.
                break

        log(f"Building {label}")
        if self.stopping.is_set():
            raise BuildError(f"Stopped before submitting {label}")
        try:
            record = self.request("build/create/scm", auth=True, data={
                "ownername": self.owner, "projectname": self.project,
                "clone_url": repo, "committish": commit,
                "subdirectory": str(package.path.parent), "spec": package.path.name,
                "scm_type": "git", "source_build_method": "rpkg", "chroots": [target],
            })
            self._validate_record(record)
        except BuildError as exc:
            raise BuildError(
                f"Submission for {label} was not confirmed ({exc}); rerun to inspect COPR before retrying"
            ) from None
        with self.lock:
            self.records.append(record)
        record = self.wait(record)
        if not self.succeeded(record, package, target):
            raise BuildError(f"COPR build {self.build_id(record)} failed to produce {label}")
        log(f"Built: {label}")


def get_project_targets(owner: str = "giperborey", project: str = "Hyprland") -> list[str]:
    return Copr(owner, project, authenticated=False).targets()


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(root), *args], text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if result.returncode:
        raise BuildError(f"Git {' '.join(args[:2])} failed; check the repository and revision")
    return result.stdout.strip()


def source_revision(root: Path, repo: str | None, commit: str | None) -> tuple[str, str]:
    if _git(root, "status", "--porcelain", "--untracked-files=normal"):
        raise BuildError("Commit all packaging changes before building")
    head = _git(root, "rev-parse", "HEAD")
    revision = _git(root, "rev-parse", "--verify", f"{commit or head}^{{commit}}")
    if revision != head:
        raise BuildError("The build revision must match the checked-out package graph")
    repository = repo or _git(root, "remote", "get-url", "origin")
    match = re.fullmatch(r"git@([^:]+):(.+)", repository)
    if match:
        repository = f"https://{match[1]}/{match[2]}"
    parsed = urlsplit(repository)
    if (parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password
            or parsed.query or parsed.fragment):
        raise BuildError("Use a public HTTPS Git clone URL without credentials")
    return repository, revision


def validate_preparation(root: Path, graphs: list[Graph]) -> None:
    # Import at execution time because rebuild.py also imports this runner.
    from rebuild import MANIFEST, PlanError, validate_preparation as validate
    from package_graph import GRAPH_FILE

    try:
        validate(root, graphs)
    except PlanError as exc:
        raise BuildError(str(exc)) from exc
    _git(root, "ls-files", "--error-unmatch", "--", str(MANIFEST))
    _git(root, "ls-files", "--error-unmatch", "--", str(GRAPH_FILE))


def build(root: Path, graphs: list[Graph], *, owner: str = "giperborey",
          project: str = "Hyprland", repo: str | None = None,
          commit: str | None = None, jobs: int = 4) -> None:
    """Reconcile each package/target after its own prerequisites succeed."""
    if jobs < 1:
        raise BuildError("jobs must be at least 1")
    if not graphs or len({graph.target for graph in graphs}) != len(graphs):
        raise BuildError("Provide one package graph for each COPR target")
    repository, revision = source_revision(root, repo, commit)
    validate_preparation(root, graphs)
    client = Copr(owner, project)
    nodes = {(name, graph.target): package for graph in graphs for name, package in graph.packages.items()}
    dependencies = {
        (name, graph.target): {(dependency, graph.target) for dependency in graph.dependencies.get(name, set())}
        for graph in graphs for name in graph.packages
    }
    if any(not requirement.issubset(nodes) for requirement in dependencies.values()):
        raise BuildError("The package graph has unresolved dependencies")
    for package in nodes.values():
        for path in set(getattr(package, "inputs", ())) | {package.path}:
            if path.is_absolute() or ".." in path.parts:
                raise BuildError("Package input paths must be relative to the repository")
            _git(root, "ls-files", "--error-unmatch", "--", str(path))
    client.preflight({graph.target for graph in graphs})
    pending = set(nodes)
    finished: set[tuple[str, str]] = set()
    failed: dict[tuple[str, str], str] = {}
    executor = ThreadPoolExecutor(max_workers=jobs)
    try:
        running = {}
        while pending or running:
            for node in sorted(pending):
                blocked = dependencies[node].intersection(failed)
                if blocked:
                    failed[node] = "blocked by " + ", ".join(name for name, _ in sorted(blocked))
                    log(f"{node[0]} ({node[1]}): {failed[node]}")
                    pending.remove(node)
            ready = [node for node in sorted(pending) if dependencies[node].issubset(finished)]
            for node in ready[:max(0, jobs - len(running))]:
                pending.remove(node)
                future = executor.submit(client.ensure, nodes[node], node[1], repository, revision)
                running[future] = node
            if not running:
                if pending:
                    # Failures can propagate through several levels between polls.
                    if any(dependencies[node].intersection(failed) for node in pending):
                        continue
                    raise BuildError("The package graph has a dependency cycle")
                break
            complete, _ = wait(running, return_when=FIRST_COMPLETED)
            for future in complete:
                node = running.pop(future)
                try:
                    future.result()
                    finished.add(node)
                except BuildError as exc:
                    failed[node] = str(exc)
                    log(str(exc))
    finally:
        client.stopping.set()
        executor.shutdown(wait=True, cancel_futures=True)
    if failed:
        summary = "; ".join(f"{name} ({target}): {reason}" for (name, target), reason in sorted(failed.items()))
        raise BuildError(summary)
