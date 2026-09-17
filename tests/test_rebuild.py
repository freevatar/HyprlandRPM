"""Exercise planning against real Git history and RPM spec queries."""

from pathlib import Path
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from package_graph import DEFAULT_TARGETS, GRAPH_FILE, GraphError, build_graph, write_graph
from rebuild import PlanError, plan, validate_preparation


TARGET = "fedora-44-x86_64"


@unittest.skipUnless(shutil.which("rpmspec") and shutil.which("git"), "requires RPM and Git")
class PlannerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="rpm-plan-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.git("init", "-q")
        self.git("config", "user.name", "Planner tests")
        self.git("config", "user.email", "tests@example.invalid")

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.root), *args], text=True, stderr=subprocess.PIPE)

    def spec(self, name, *, version="1.0", requires=(), provides=(), files=None, extra=""):
        directory = self.root / name
        directory.mkdir(exist_ok=True)
        path = directory / f"{name}.spec"
        text = (
            f"Name: {name}\nVersion: {version}\nRelease: %autorelease\n"
            "Summary: Test package\nLicense: MIT\n"
            + "".join(f"BuildRequires: {value}\n" for value in requires)
            + "".join(f"Provides: {value}\n" for value in provides)
            + extra
            + "\n%description\nTest package.\n\n%files\n"
            + "\n".join(files if files is not None else [f"/usr/lib64/pkgconfig/{name}.pc"])
            + "\n"
        )
        path.write_text(text)
        return path

    def commit(self):
        self.refresh_graph()
        self.git("add", ".")
        self.git("commit", "-qm", "baseline")

    def refresh_graph(self):
        write_graph(self.root, DEFAULT_TARGETS)

    def prepare(self):
        return plan(self.root, targets=(TARGET,))

    def bump_version(self, path, version="2.0"):
        path.write_text(path.read_text().replace("Version: 1.0", f"Version: {version}"))

    def test_combines_roots_and_bumps_transitive_consumers_once(self):
        first = self.spec("first")
        second = self.spec("second")
        self.spec("middle", requires=("pkgconfig(first)", "pkgconfig(second)"))
        self.spec("application", requires=("pkgconfig(middle)",))
        unrelated = self.spec("unrelated")
        untouched = unrelated.read_bytes()
        self.commit()
        self.bump_version(first)
        self.bump_version(second)
        result = self.prepare()
        self.assertEqual(result.affected, {"first", "second", "middle", "application"})
        self.assertEqual(set(result.updates), {Path("middle/middle.spec"), Path("application/application.spec")})
        self.assertEqual(result.reasons["middle"], {"first", "second"})
        result.apply()
        self.assertIn("Release: %autorelease -b2", (self.root / "middle/middle.spec").read_text())
        again = self.prepare()
        self.assertFalse(again.updates)
        again.apply()
        self.assertEqual(unrelated.read_bytes(), untouched)
        self.assertEqual(self.git("diff", "--cached", "--name-only"), "")

    def test_version_updated_consumer_needs_no_extra_release_bump(self):
        library = self.spec("library")
        app = self.spec("app", requires=("pkgconfig(library)",))
        self.commit()
        self.bump_version(library)
        self.bump_version(app)
        self.assertFalse(self.prepare().updates)

    def test_static_and_scanner_capabilities_resolve_to_source_packages(self):
        self.spec("headers", provides=("headers-static = 1.0",))
        self.spec("wire", files=["/usr/lib64/cmake/wire-scanner/", "/usr/lib64/pkgconfig/wire-scanner.pc"])
        self.spec("app", requires=("headers-static", "cmake(wire-scanner) >= 1.0"))
        graph = build_graph(self.root, TARGET)
        self.assertEqual(graph.dependencies["app"], {"headers", "wire"})

    def test_graph_uses_fedora_conditional_branches(self):
        self.spec("oldlib")
        self.spec("newlib")
        self.spec("app", extra="%if 0%{?fedora} < 45\nBuildRequires: pkgconfig(oldlib)\n%else\nBuildRequires: pkgconfig(newlib)\n%endif\n")
        self.assertEqual(build_graph(self.root, TARGET).dependencies["app"], {"oldlib"})
        self.assertEqual(build_graph(self.root, "fedora-45-x86_64").dependencies["app"], {"newlib"})

    def test_insufficient_version_stops_before_any_writes(self):
        self.spec("library")
        app = self.spec("app", requires=("pkgconfig(library) >= 2.0",))
        original = app.read_bytes()
        with self.assertRaisesRegex(GraphError, "planned providers"):
            build_graph(self.root, TARGET)
        self.assertEqual(app.read_bytes(), original)

    def test_ambiguous_provider_and_cycle_are_rejected(self):
        self.spec("first", provides=("choice",))
        self.spec("second", provides=("choice",))
        self.spec("app", requires=("choice",))
        with self.assertRaisesRegex(GraphError, "ambiguous"):
            build_graph(self.root, TARGET)
        self.spec("first", requires=("pkgconfig(second)",))
        self.spec("second", requires=("pkgconfig(first)",))
        self.spec("app")
        with self.assertRaisesRegex(GraphError, "cycle"):
            build_graph(self.root, TARGET)

    def test_removed_dependency_still_propagates_rebuild(self):
        library = self.spec("library")
        self.spec("middle", requires=("pkgconfig(library)",))
        self.spec("app", requires=("pkgconfig(middle)",))
        self.commit()
        self.bump_version(library)
        self.spec("middle")
        self.refresh_graph()
        self.assertEqual(self.prepare().affected, {"library", "middle", "app"})

    def test_local_patch_changes_bump_source_and_consumer_but_tool_edits_do_not(self):
        self.spec("library", extra="Patch0: fix.patch\n")
        patch = self.root / "library/fix.patch"
        patch.write_text("original patch\n")
        tool = self.root / "library/update.py"
        tool.write_text("# updater\n")
        self.spec("app", requires=("pkgconfig(library)",))
        self.commit()
        tool.write_text("# changed updater\n")
        self.assertFalse(self.prepare().affected)
        patch.write_text("new patch\n")
        result = self.prepare()
        self.assertEqual(result.affected, {"library", "app"})
        self.assertEqual(len(result.updates), 2)

    def test_release_bump_preserves_crlf_and_blank_lines(self):
        library = self.spec("library")
        library.write_bytes(library.read_bytes().replace(b"\n", b"\r\n"))
        self.commit()
        library.write_bytes(library.read_bytes().replace(b"Summary: Test", b"Summary: Updated"))
        result = self.prepare()
        result.apply()
        content = library.read_bytes()
        self.assertIn(b"Release: %autorelease -b2\r\n", content)
        self.assertNotIn(b"\n", content.replace(b"\r\n", b""))

    def test_unrelated_staged_changes_are_preserved(self):
        self.spec("library")
        self.commit()
        readme = self.root / "README.md"
        readme.write_text("private draft\n")
        self.git("add", "README.md")
        index = self.git("diff", "--cached")
        result = self.prepare()
        result.apply()
        self.assertEqual(self.git("diff", "--cached"), index)
        self.assertFalse(result.affected)

    def test_new_package_is_not_given_rebuild_release(self):
        self.spec("old")
        self.commit()
        self.spec("new", requires=("pkgconfig(old)",))
        self.refresh_graph()
        result = self.prepare()
        self.assertEqual(result.affected, {"new"})
        self.assertFalse(result.updates)

    def test_downgrade_and_unsupported_release_refuse_to_write(self):
        library = self.spec("library")
        self.commit()
        self.bump_version(library, "0.5")
        with self.assertRaisesRegex(PlanError, "backwards"):
            self.prepare()
        library = self.spec("library")
        library.write_text(library.read_text().replace("Release: %autorelease", "Release: 1"))
        with self.assertRaisesRegex(PlanError, "automatic bumps require"):
            self.prepare()

    def test_rejects_stale_plan_without_overwriting_new_changes(self):
        library = self.spec("library")
        self.commit()
        library.write_text(library.read_text().replace("Summary: Test", "Summary: Updated"))
        result = self.prepare()
        library.write_text(library.read_text() + "# subsequent edit\n")
        with self.assertRaisesRegex(PlanError, "changed after planning"):
            result.apply()
        self.assertIn("# subsequent edit", library.read_text())

    def test_explicit_provide_version_overrides_file_inference(self):
        self.spec("library", version="2.0", provides=("pkgconfig(library) = 1.0",))
        self.spec("app", requires=("pkgconfig(library) >= 2.0",))
        with self.assertRaisesRegex(GraphError, "planned providers"):
            build_graph(self.root, TARGET)

    def test_generated_module_version_does_not_inherit_rpm_epoch(self):
        self.spec("library", extra="Epoch: 2\n")
        self.spec("app", requires=("pkgconfig(library) >= 2.0",))
        with self.assertRaisesRegex(GraphError, "planned providers"):
            build_graph(self.root, TARGET)

    def test_conditional_release_is_rejected_without_partial_bump(self):
        library = self.spec("library")
        library.write_text(library.read_text().replace(
            "Release: %autorelease", "%if 0%{?fedora} < 45\nRelease: %autorelease\n%else\nRelease: %autorelease -b3\n%endif"))
        self.commit()
        library.write_text(library.read_text().replace("Summary: Test", "Summary: Updated"))
        original = library.read_bytes()
        with self.assertRaisesRegex(PlanError, "one unconditional Release"):
            plan(self.root)
        self.assertEqual(library.read_bytes(), original)

    def test_preparation_detects_changes_after_apply_and_survives_fresh_checkout(self):
        library = self.spec("library")
        self.spec("app", requires=("pkgconfig(library)",))
        self.commit()
        self.bump_version(library)
        self.prepare().apply()
        validate_preparation(self.root)
        self.commit()
        with tempfile.TemporaryDirectory(prefix="rpm-plan-clone-") as directory:
            subprocess.run(["git", "clone", "-q", str(self.root), directory], check=True)
            self.assertFalse(validate_preparation(Path(directory)).updates)
        library.write_text(library.read_text().replace("Version: 2.0", "Version: 3.0"))
        with self.assertRaisesRegex(PlanError, "changed since apply"):
            validate_preparation(self.root)

    def test_new_batch_bumps_consumers_again_after_previous_batch_is_committed(self):
        library = self.spec("library")
        self.spec("app", requires=("pkgconfig(library)",))
        self.commit()
        self.bump_version(library)
        self.prepare().apply()
        self.commit()
        library.write_text(library.read_text().replace("Version: 2.0", "Version: 3.0"))
        self.prepare().apply()
        self.assertIn("Release: %autorelease -b3", (self.root / "app/app.spec").read_text())
        validate_preparation(self.root)

    def test_wrong_base_after_unprepared_commit_is_rejected(self):
        library = self.spec("library")
        self.spec("app", requires=("pkgconfig(library)",))
        self.commit()
        self.prepare().apply()
        self.commit()
        prepared_base = self.git("rev-parse", "HEAD").strip()
        self.bump_version(library)
        self.commit()
        with self.assertRaisesRegex(PlanError, "unprepared changes"):
            self.prepare().apply()
        # Adding another edit must not let a new batch hide the unprepared one.
        self.spec("newpackage")
        self.refresh_graph()
        with self.assertRaisesRegex(PlanError, "unprepared changes"):
            self.prepare().apply()
        plan(self.root, prepared_base, (TARGET,)).apply()
        validate_preparation(self.root)

    def test_planning_uses_saved_edges_without_resolving_dependencies(self):
        library = self.spec("library")
        self.spec("app", requires=("pkgconfig(library)",))
        self.commit()
        self.bump_version(library)
        with patch("rebuild.build_graph", side_effect=AssertionError("unexpected dependency resolution")), \
                patch("package_graph.build_graph", side_effect=AssertionError("unexpected dependency resolution")):
            result = self.prepare()
        self.assertEqual(result.affected, {"library", "app"})

    def test_apply_does_not_certify_stale_dependencies_or_unsatisfied_versions(self):
        self.spec("first")
        self.spec("second")
        app = self.spec("app", requires=("pkgconfig(first)",))
        self.commit()
        for requirement, error in (("pkgconfig(second)", "stale"),
                                   ("pkgconfig(first) >= 2.0", "planned providers")):
            with self.subTest(requirement=requirement):
                self.spec("app", requires=(requirement,))
                original = app.read_bytes()
                with self.assertRaisesRegex(GraphError, error):
                    self.prepare().apply()
                self.assertEqual(app.read_bytes(), original)
                self.assertFalse((self.root / "build-plan.json").exists())

    def test_graph_change_after_preparation_requires_new_preparation(self):
        self.spec("library")
        self.spec("app")
        self.commit()
        self.prepare().apply()
        graph_path = self.root / GRAPH_FILE
        record = json.loads(graph_path.read_text())
        record["dependencies"]["app"] = ["library"]
        graph_path.write_text(json.dumps(record))
        with self.assertRaisesRegex(PlanError, "changed since apply"):
            validate_preparation(self.root)

    def test_first_saved_graph_uses_historical_specs_for_old_dependencies(self):
        library = self.spec("library")
        self.spec("app", requires=("pkgconfig(library)",))
        # This baseline predates the stored graph.
        self.git("add", ".")
        self.git("commit", "-qm", "before stored graphs")
        self.bump_version(library)
        self.spec("app")
        self.refresh_graph()
        result = self.prepare()
        self.assertEqual(result.affected, {"library", "app"})
        result.apply()
        validate_preparation(self.root)

    def test_apply_repairs_preparation_after_squashed_history(self):
        library = self.spec("library")
        self.spec("app", requires=("pkgconfig(library)",))
        self.commit()
        published = self.git("rev-parse", "HEAD").strip()
        (self.root / "README.md").write_text("tooling changes\n")
        self.commit()
        old_base = self.git("rev-parse", "HEAD").strip()
        self.bump_version(library)
        self.prepare().apply()
        self.commit()
        self.git("reset", "--soft", published)
        self.git("commit", "-qm", "squashed update")
        with self.assertRaisesRegex(PlanError, "missing or rewritten base"):
            validate_preparation(self.root)
        with self.assertRaisesRegex(PlanError, "stale plan"):
            self.prepare().apply()
        result = plan(self.root, published, (TARGET,))
        self.assertNotEqual(result.base, old_base)
        result.apply()
        validate_preparation(self.root)
        self.assertIn("Release: %autorelease -b2", (self.root / "app/app.spec").read_text())

    def test_atomic_write_failure_restores_every_changed_spec(self):
        first = self.spec("first")
        second = self.spec("second")
        self.commit()
        for path in (first, second):
            path.write_text(path.read_text().replace("Summary: Test", "Summary: Updated"))
        originals = {path: path.read_bytes() for path in (first, second)}
        result = self.prepare()
        import os
        real_replace = os.replace

        def fail_second(source, destination):
            if destination == second:
                raise OSError("simulated I/O failure")
            return real_replace(source, destination)

        with patch("rebuild.os.replace", side_effect=fail_second):
            with self.assertRaisesRegex(OSError, "I/O failure"):
                result.apply()
        self.assertEqual({path: path.read_bytes() for path in originals}, originals)
        self.assertFalse((self.root / "build-plan.json").exists())


if __name__ == "__main__":
    unittest.main()
