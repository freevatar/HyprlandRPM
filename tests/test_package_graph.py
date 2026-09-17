"""Stored dependency graphs keep edges stable and package metadata current."""

import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import package_graph as graph_module
from package_graph import (GRAPH_FILE, GraphError, build_graph, check_graph,
                           graph_document, load_graph, write_graph)


TARGET = "fedora-44-x86_64"
SECOND_TARGET = "fedora-45-x86_64"


@unittest.skipUnless(shutil.which("rpmspec"), "requires RPM tools")
class StoredGraphTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="rpm-stored-graph-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def spec(self, name, *, version="1.0", release="1", requires=(), extra=""):
        directory = self.root / name
        directory.mkdir(exist_ok=True)
        path = directory / f"{name}.spec"
        path.write_text(
            f"Name: {name}\nVersion: {version}\nRelease: {release}\n"
            "Summary: Test package\nLicense: MIT\n"
            + "".join(f"BuildRequires: {value}\n" for value in requires)
            + extra
            + f"\n%description\nTest package.\n\n%files\n/usr/lib64/pkgconfig/{name}.pc\n"
        )
        return path

    def save(self, targets=(TARGET,)):
        return write_graph(self.root, targets)

    def test_load_reads_fresh_versions_and_inputs_without_resolving_dependencies(self):
        self.spec("library")
        self.spec("app", requires=("pkgconfig(library)",))
        self.save()
        saved = (self.root / GRAPH_FILE).read_bytes()
        self.spec("library", version="2.0", release="3", extra="Patch0: new.patch\n")
        (self.root / "library/new.patch").write_text("new local source input")
        with patch.object(graph_module, "query", wraps=graph_module.query) as query:
            graph = load_graph(self.root, TARGET)
        calls = [call.args[3:] for call in query.call_args_list]
        self.assertFalse(any("--buildrequires" in call or "--provides" in call for call in calls))
        self.assertEqual(len(calls), 4)
        self.assertEqual(graph.packages["library"].version_release, "2.0-3")
        self.assertIn(Path("library/new.patch"), graph.packages["library"].inputs)
        self.assertEqual(graph.dependencies["app"], {"library"})
        self.assertEqual((self.root / GRAPH_FILE).read_bytes(), saved)

    def test_version_change_does_not_change_stored_document(self):
        self.spec("library")
        self.spec("app", requires=("pkgconfig(library) >= 1.0",))
        self.save()
        saved = (self.root / GRAPH_FILE).read_bytes()
        self.spec("library", version="2.0")
        self.save()
        self.assertEqual((self.root / GRAPH_FILE).read_bytes(), saved)
        check_graph(self.root, [TARGET])

    def test_explicit_check_finds_dependency_changes(self):
        self.spec("library")
        self.spec("app")
        self.save()
        self.spec("app", requires=("pkgconfig(library)",))
        self.assertEqual(load_graph(self.root, TARGET).dependencies["app"], set())
        with self.assertRaisesRegex(GraphError, "stale for app.*graph --refresh"):
            check_graph(self.root, [TARGET])
        self.save()
        self.assertEqual(check_graph(self.root, [TARGET])[0].dependencies["app"], {"library"})

    def test_explicit_check_validates_dependency_versions(self):
        self.spec("library")
        self.spec("app", requires=("pkgconfig(library) >= 1.0",))
        self.save()
        self.spec("app", requires=("pkgconfig(library) >= 2.0",))
        self.assertEqual(load_graph(self.root, TARGET).dependencies["app"], {"library"})
        with self.assertRaisesRegex(GraphError, "planned providers"):
            check_graph(self.root, [TARGET])

    def test_targets_share_edges_and_load_fresh_conditional_metadata(self):
        self.spec("library", version="%{fedora}.0")
        self.spec("app", requires=("pkgconfig(library)",))
        self.save((TARGET, SECOND_TARGET))
        saved = (self.root / GRAPH_FILE).read_bytes()
        self.spec("library", version="%{fedora}.1")
        first = load_graph(self.root, TARGET)
        second = load_graph(self.root, SECOND_TARGET)
        self.assertEqual(first.dependencies, second.dependencies)
        self.assertEqual(first.dependencies["app"], {"library"})
        self.assertEqual(first.packages["library"].version, "44.1")
        self.assertEqual(second.packages["library"].version, "45.1")
        self.assertEqual((self.root / GRAPH_FILE).read_bytes(), saved)
        check_graph(self.root, [TARGET, SECOND_TARGET])

    def test_refresh_rejects_different_target_edges_and_preserves_existing_graph(self):
        self.spec("oldlib")
        self.spec("newlib")
        self.spec("app", requires=("pkgconfig(oldlib)",))
        self.save((TARGET, SECOND_TARGET))
        saved = (self.root / GRAPH_FILE).read_bytes()
        self.spec("app", extra=("%if 0%{?fedora} < 45\nBuildRequires: pkgconfig(oldlib)\n"
                                "%else\nBuildRequires: pkgconfig(newlib)\n%endif\n"))
        with self.assertRaises(GraphError):
            self.save((TARGET, SECOND_TARGET))
        self.assertEqual((self.root / GRAPH_FILE).read_bytes(), saved)
        with self.assertRaisesRegex(GraphError, "stale for app"):
            check_graph(self.root, [TARGET, SECOND_TARGET])

    def test_document_rejects_different_target_nodes(self):
        self.spec("library")
        self.spec("app")
        first, second = build_graph(self.root, TARGET), build_graph(self.root, SECOND_TARGET)
        second.packages.pop("library")
        second.dependencies.pop("library")
        with self.assertRaises(GraphError):
            graph_document([first, second])

    def test_missing_file_and_changed_nodes_require_refresh(self):
        library = self.spec("library")
        with self.assertRaisesRegex(GraphError, "graph --refresh"):
            load_graph(self.root, TARGET)
        self.save()
        self.assertEqual(load_graph(self.root, SECOND_TARGET).dependencies, {"library": set()})
        self.spec("new")
        with self.assertRaisesRegex(GraphError, "missing packages: new"):
            load_graph(self.root, TARGET)
        self.save()
        library.unlink()
        with self.assertRaisesRegex(GraphError, "removed packages: library"):
            load_graph(self.root, TARGET)

    def test_malformed_references_and_cycles_are_rejected(self):
        self.spec("first")
        self.spec("second")
        cases = [
            {"schema": True, "dependencies": {}},
            {"schema": 1, "dependencies": []},
            {"schema": 1, "dependencies": {"first": "second", "second": []}},
            {"schema": 1, "dependencies": {"first": ["missing"], "second": []}},
            {"schema": 1, "dependencies": {"first": ["second", "second"], "second": []}},
            {"schema": 1, "dependencies": {"first": ["second"], "second": ["first"]}},
        ]
        for document in cases:
            with self.subTest(document=document):
                (self.root / GRAPH_FILE).write_text(json.dumps(document))
                with self.assertRaisesRegex(GraphError, "graph --refresh"):
                    load_graph(self.root, TARGET)

    def test_document_is_deterministic_and_contains_no_package_versions(self):
        self.spec("library", version="123.456")
        self.spec("app", requires=("pkgconfig(library)",))
        first, second = build_graph(self.root, TARGET), build_graph(self.root, SECOND_TARGET)
        content = graph_document([first, second])
        self.assertEqual(content, graph_document([second, first]))
        self.assertNotIn(b"123.456", content)
        self.assertEqual(json.loads(content), {
            "schema": 1, "dependencies": {"app": ["library"], "library": []},
        })

    def test_failed_refresh_preserves_existing_graph(self):
        self.spec("library")
        self.spec("app", requires=("pkgconfig(library)",))
        self.save()
        original = (self.root / GRAPH_FILE).read_bytes()
        self.spec("app", requires=("pkgconfig(library) >= 2.0",))
        with self.assertRaises(GraphError):
            self.save()
        self.assertEqual((self.root / GRAPH_FILE).read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
