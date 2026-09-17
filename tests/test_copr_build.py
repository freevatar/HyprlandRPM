"""COPR graph scheduling and recovery; no live requests or builds."""

from __future__ import annotations

import copy
from dataclasses import dataclass
import importlib.util
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.error import URLError


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "copr_build.py"
sys.path.insert(0, str(MODULE_PATH.parent))
MODULE_SPEC = importlib.util.spec_from_file_location("copr_build_tests_module", MODULE_PATH)
assert MODULE_SPEC and MODULE_SPEC.loader
runner = importlib.util.module_from_spec(MODULE_SPEC)
sys.modules[MODULE_SPEC.name] = runner
MODULE_SPEC.loader.exec_module(runner)

TARGET = "fedora-44-x86_64"
SECOND_TARGET = "fedora-45-aarch64"
REPOSITORY = "https://github.com/example/packages.git"


@dataclass(frozen=True)
class Package:
    name: str
    path: Path
    version: str = "1.0"
    release: str = "2"
    epoch: str = "0"

    @property
    def version_release(self):
        return f"{self.version}-{self.release}"


@dataclass
class Graph:
    target: str
    packages: dict[str, Package]
    dependencies: dict[str, set[str]]


class Service:
    """Remote data outlives the runner, including uncertain submissions."""

    def __init__(self):
        self.records = []
        self.targets = [TARGET]
        self.versions = {}
        self.outcomes = {}
        self.events = []
        self.requests = []
        self.submissions = []
        self.ambiguous = False
        self.lock = threading.RLock()

    def add(self, name, *, target=TARGET, state="succeeded", version=None,
            unknown=False, per_target=None):
        source = {"name": name, "version": version or self.versions.get(name, "1.0-2")}
        record = {
            "id": len(self.records) + 1, "state": state,
            "ownername": "giperborey", "projectname": "Hyprland", "project_dirname": "Hyprland",
            "chroots": [target] if isinstance(target, str) else target,
            "source_package": None if unknown else source,
            "_source": source,
            "_config": {"source_type": "scm", "source_dict": {
                "clone_url": REPOSITORY, "subdirectory": name, "spec": f"{name}.spec",
            }},
            "_per_target": per_target or {},
        }
        self.records.append(record)
        return record

    @staticmethod
    def public(record):
        return copy.deepcopy({key: value for key, value in record.items() if not key.startswith("_")})

    def request(self, client, endpoint, *, data=None, auth=False, **query):
        with self.lock:
            self.requests.append((endpoint, copy.deepcopy(data), query))
            if endpoint == "project":
                return {"chroot_repos": {target: "https://example.invalid/repo" for target in self.targets}}
            if endpoint == "build/check-before-build":
                return {"message": "It should be safe to submit a build like this"}
            if endpoint == "build/list":
                ordered = sorted(self.records, key=lambda item: item["id"], reverse=True)
                start, count = query["offset"], query["limit"]
                return {"items": [self.public(item) for item in ordered[start:start + count]]}
            if endpoint.startswith("build/source-build-config/"):
                return copy.deepcopy(self.records[int(endpoint.rsplit("/", 1)[1]) - 1]["_config"])
            if endpoint == "build/create/scm":
                self.submissions.append(copy.deepcopy(data))
                name = Path(data["spec"]).stem
                self.events.append(("submit", name, data["chroots"][0]))
                record = self.add(name, target=data["chroots"], state="pending", unknown=True)
                record["_config"]["source_dict"] = copy.deepcopy(data)
                if self.ambiguous:
                    self.ambiguous = False
                    raise runner.BuildError("Response was lost")
                return self.public(record)
            if endpoint.startswith("build/"):
                record = self.records[int(endpoint.rsplit("/", 1)[1]) - 1]
                name = record["_source"]["name"]
                target = record["chroots"][0]
                record["state"] = self.outcomes.get((name, target), "succeeded")
                record["source_package"] = record["_source"]
                self.events.append(("finish", name, target))
                return self.public(record)
            if endpoint == "build-chroot":
                record = self.records[query["build_id"] - 1]
                return {"state": record["_per_target"].get(query["chrootname"], record["state"])}
            raise AssertionError(endpoint)


class BuildTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hyprland-copr-tests-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.git("init", "-q")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.invalid")
        self.git("remote", "add", "origin", REPOSITORY)
        self.service = Service()
        self.patch = patch.object(runner.Copr, "request", autospec=True,
                                  side_effect=self.service.request)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.env = patch.dict("os.environ", {"COPR_LOGIN": "login", "COPR_TOKEN": "secret"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.sleep = patch.object(runner.time, "sleep")
        self.sleep.start()
        self.addCleanup(self.sleep.stop)
        self.output = patch.object(runner.sys, "stderr", io.StringIO())
        self.output.start()
        self.addCleanup(self.output.stop)
        # Preparation fingerprints and release bumps have integration coverage
        # in test_rebuild; these tests isolate remote reconciliation.
        self.preparation = patch.object(runner, "validate_preparation")
        self.validate_preparation = self.preparation.start()
        self.addCleanup(self.preparation.stop)

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.root), *args], check=True,
                              text=True, capture_output=True).stdout.strip()

    def graph(self, dependencies, target=TARGET, *, release="2"):
        packages = {}
        for name in dependencies:
            path = Path(name) / f"{name}.spec"
            (self.root / path).parent.mkdir(exist_ok=True)
            if not (self.root / path).exists():
                (self.root / path).write_text(f"Name: {name}\nVersion: 1.0\nRelease: {release}\n")
            packages[name] = Package(name, path, release=release)
            self.service.versions[name] = f"1.0-{release}"
        self.git("add", ".")
        self.git("commit", "-qm", "Packages", "--allow-empty")
        return Graph(target, packages, dependencies)

    def test_builds_prerequisites_before_consumers_and_pins_sources(self):
        graph = self.graph({"library": set(), "middle": {"library"}, "app": {"middle"}, "other": set()})
        runner.build(self.root, [graph])
        events = self.service.events
        self.assertLess(events.index(("finish", "library", TARGET)), events.index(("submit", "middle", TARGET)))
        self.assertLess(events.index(("finish", "middle", TARGET)), events.index(("submit", "app", TARGET)))
        self.assertEqual(len(self.service.submissions), 4)
        for data in self.service.submissions:
            self.assertEqual(data["committish"], self.git("rev-parse", "HEAD"))
            self.assertEqual(data["clone_url"], REPOSITORY)
            self.assertEqual(data["source_build_method"], "rpkg")
            self.assertEqual(data["spec"], f"{data['subdirectory']}.spec")

    def test_new_release_does_not_reuse_success_from_old_consumer(self):
        graph = self.graph({"app": set()}, release="3")
        self.service.add("app", version="1.0-2")
        runner.build(self.root, [graph])
        self.assertEqual(len(self.service.submissions), 1)
        self.assertEqual(self.service.records[-1]["source_package"]["version"], "1.0-3")

    def test_rerun_skips_matching_success_without_local_state(self):
        graph = self.graph({"library": set(), "app": {"library"}})
        runner.build(self.root, [graph])
        runner.build(self.root, [graph])
        self.assertEqual(len(self.service.submissions), 2)

    def test_newer_failure_is_not_hidden_by_older_success(self):
        graph = self.graph({"app": set()})
        self.service.add("app")
        self.service.add("app", state="failed")
        runner.build(self.root, [graph])
        self.assertEqual(len(self.service.submissions), 1)

    def test_failed_prerequisite_blocks_only_its_descendants(self):
        graph = self.graph({"library": set(), "app": {"library"}, "grandchild": {"app"}, "other": set()})
        self.service.outcomes[("library", TARGET)] = "failed"
        with self.assertRaisesRegex(runner.BuildError, "blocked by"):
            runner.build(self.root, [graph], jobs=2)
        names = {data["subdirectory"] for data in self.service.submissions}
        self.assertEqual(names, {"library", "other"})

    def test_source_build_without_name_is_resumed_from_scm_identity(self):
        graph = self.graph({"app": set()})
        self.service.add("app", state="pending", unknown=True)
        runner.build(self.root, [graph])
        self.assertFalse(self.service.submissions)
        self.assertIn(("finish", "app", TARGET), self.service.events)

    def test_older_active_build_is_drained_before_reusing_newer_success(self):
        graph = self.graph({"library": set(), "app": {"library"}})
        old = self.service.add("library", state="pending", version="0.9-1")
        self.service.add("library")
        runner.build(self.root, [graph])
        self.assertEqual(old["state"], "succeeded")
        self.assertEqual([data["subdirectory"] for data in self.service.submissions], ["app"])
        self.assertLess(self.service.events.index(("finish", "library", TARGET)),
                        self.service.events.index(("submit", "app", TARGET)))

    def test_obsolete_unknown_failures_do_not_require_source_config(self):
        graph = self.graph({"app": set()})
        self.service.add("app", state="failed", unknown=True)
        self.service.add("app")
        runner.build(self.root, [graph])
        self.assertFalse(self.service.submissions)
        self.assertFalse(any(endpoint.startswith("build/source-build-config/")
                             for endpoint, _, _ in self.service.requests))

    def test_lost_submission_response_is_recovered_on_next_run(self):
        graph = self.graph({"app": set()})
        self.service.ambiguous = True
        with self.assertRaisesRegex(runner.BuildError, "was not confirmed"):
            runner.build(self.root, [graph])
        self.assertEqual(len(self.service.submissions), 1)
        runner.build(self.root, [graph])
        self.assertEqual(len(self.service.submissions), 1)

    def test_unknown_source_in_other_subproject_is_not_reused(self):
        graph = self.graph({"app": set()})
        record = self.service.add("app", state="pending", unknown=True)
        record["project_dirname"] = "Hyprland:custom:other"
        runner.build(self.root, [graph])
        self.assertEqual(len(self.service.submissions), 1)
        self.assertEqual(record["state"], "pending")

    def test_graph_dependencies_and_failures_are_target_specific(self):
        first = self.graph({"library": set(), "app": {"library"}})
        second = self.graph({"library": set(), "app": set()}, SECOND_TARGET)
        self.service.targets.append(SECOND_TARGET)
        self.service.outcomes[("library", TARGET)] = "failed"
        with self.assertRaises(runner.BuildError):
            runner.build(self.root, [first, second])
        submitted = {(data["subdirectory"], data["chroots"][0]) for data in self.service.submissions}
        self.assertNotIn(("app", TARGET), submitted)
        self.assertIn(("app", SECOND_TARGET), submitted)

    def test_partial_multichroot_success_only_rebuilds_failed_target(self):
        first = self.graph({"app": set()})
        second = self.graph({"app": set()}, SECOND_TARGET)
        self.service.targets.append(SECOND_TARGET)
        self.service.add("app", target=[TARGET, SECOND_TARGET], state="failed",
                         per_target={TARGET: "succeeded", SECOND_TARGET: "failed"})
        runner.build(self.root, [first, second])
        self.assertEqual([data["chroots"] for data in self.service.submissions], [[SECOND_TARGET]])

    def test_incomplete_build_record_with_no_chroots_cannot_satisfy_target(self):
        graph = self.graph({"app": set()})
        self.service.add("app", target=[])
        runner.build(self.root, [graph])
        self.assertEqual(len(self.service.submissions), 1)

    def test_dirty_tree_and_wrong_commit_fail_before_remote_access(self):
        graph = self.graph({"app": set()})
        original = self.git("rev-parse", "HEAD")
        (self.root / "uncommitted").write_text("change")
        with self.assertRaisesRegex(runner.BuildError, "Commit all"):
            runner.build(self.root, [graph])
        self.assertFalse(self.service.requests)
        self.git("add", ".")
        self.git("commit", "-qm", "Second")
        with self.assertRaisesRegex(runner.BuildError, "checked-out"):
            runner.build(self.root, [graph], commit=original)
        self.assertFalse(self.service.requests)

    def test_missing_credentials_and_missing_target_fail_before_submission(self):
        graph = self.graph({"app": set()})
        with patch.dict("os.environ", {"COPR_TOKEN": ""}):
            with self.assertRaisesRegex(runner.BuildError, "COPR_LOGIN"):
                runner.build(self.root, [graph])
        self.assertFalse(self.service.requests)
        self.service.targets.append(SECOND_TARGET)
        with self.assertRaisesRegex(runner.BuildError, "every enabled"):
            runner.build(self.root, [graph])
        self.assertFalse(self.service.submissions)

    def test_invalid_preparation_is_rejected_before_remote_access(self):
        graph = self.graph({"app": set()})
        self.validate_preparation.side_effect = runner.BuildError("package inputs changed since apply")
        with self.assertRaisesRegex(runner.BuildError, "inputs changed"):
            runner.build(self.root, [graph])
        self.assertFalse(self.service.requests)

    def test_preparation_validation_converts_errors_and_requires_tracked_manifest(self):
        graph = self.graph({"app": set()})
        self.preparation.stop()

        class PlanError(RuntimeError):
            pass

        def reject(root, graphs):
            raise PlanError("missing release bumps")

        module = SimpleNamespace(MANIFEST=Path("build-plan.json"), PlanError=PlanError,
                                 validate_preparation=reject)
        with patch.dict(sys.modules, {"rebuild": module}):
            with self.assertRaisesRegex(runner.BuildError, "missing release bumps"):
                runner.validate_preparation(self.root, [graph])
            module.validate_preparation = lambda root, graphs: None
            with self.assertRaisesRegex(runner.BuildError, "ls-files"):
                runner.validate_preparation(self.root, [graph])
            (self.root / "build-plan.json").write_text("{}")
            self.git("add", "build-plan.json")
            with self.assertRaises(runner.BuildError):
                runner.validate_preparation(self.root, [graph])
            (self.root / "package-graph.json").write_text("{}")
            self.git("add", "package-graph.json")
            runner.validate_preparation(self.root, [graph])

    def test_paginates_history_before_treating_old_success_as_missing(self):
        graph = self.graph({"app": set()})
        self.service.add("app")
        for _ in range(3):
            self.service.add("unrelated")
        with patch.object(runner, "PAGE_SIZE", 2):
            runner.build(self.root, [graph])
        offsets = [query["offset"] for endpoint, _, query in self.service.requests if endpoint == "build/list"]
        self.assertEqual(offsets, [0, 2, 4])
        self.assertFalse(self.service.submissions)

    def test_target_discovery_needs_no_credentials(self):
        with patch.dict("os.environ", {"COPR_LOGIN": "", "COPR_TOKEN": ""}):
            self.assertEqual(runner.get_project_targets(), [TARGET])


class RequestTests(unittest.TestCase):
    def test_ambiguous_post_is_not_retried(self):
        with patch.dict("os.environ", {"COPR_LOGIN": "login", "COPR_TOKEN": "secret"}):
            client = runner.Copr("owner", "project")
        with patch.object(runner, "urlopen", side_effect=URLError("secret")) as request:
            with self.assertRaises(runner.BuildError) as error:
                client.request("build/create/scm", auth=True, data={"committish": "a" * 40})
        self.assertEqual(request.call_count, 1)
        self.assertNotIn("secret", str(error.exception))


if __name__ == "__main__":
    unittest.main()
