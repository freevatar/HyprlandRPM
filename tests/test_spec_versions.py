"""Safe, local spec version edits with real RPM candidate validation."""

from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from package_graph import GraphError
import spec_versions
from spec_versions import VersionEdit, VersionEditError, apply_version_edits, prepare_version_edit


TARGET = "fedora-44-x86_64"


@unittest.skipUnless(shutil.which("rpmspec"), "requires RPM")
class SpecVersionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="spec-version-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def spec(self, filename="package.spec", *, version="1.0", release="5", prefix="", name="package"):
        path = self.root / filename
        path.write_text(
            prefix + f"Name: {name}\nVersion: {version}\nRelease: {release}\n"
            "Summary: Test package\nLicense: MIT\nURL: https://github.com/example/package\n"
            "\n%description\nTest package.\n\n%files\n"
        )
        return path

    def prepare(self, path, old="1.0", new="2.0"):
        return prepare_version_edit(path, old, new, TARGET)

    def test_preserves_formatting_comments_crlf_and_other_contents(self):
        path = self.spec(release="%autorelease -b5")
        original = (path.read_bytes()
                    .replace(b"Version: 1.0", b"Version:\t  1.0 \t")
                    .replace(b"Release: %autorelease -b5", b"Release:    %autorelease -b5  # release comment  ")
                    .replace(b"Name:", b"# Keep this comment\nName:")
                    .replace(b"\n", b"\r\n"))
        path.write_bytes(original)
        path.chmod(0o640)
        edit = self.prepare(path)
        self.assertEqual(path.read_bytes(), original)
        expected = original.replace(b"Version:\t  1.0", b"Version:\t  2.0").replace(b"%autorelease -b5", b"%autorelease")
        self.assertEqual(edit, VersionEdit(path, original, expected))
        self.assertEqual(list(self.root.iterdir()), [path])
        apply_version_edits([edit])
        self.assertEqual(path.read_bytes(), expected)
        self.assertEqual(path.stat().st_mode & 0o777, 0o640)

    def test_resets_supported_release_forms(self):
        for release, expected in (
            ("%autorelease", "%autorelease"), ("%autorelease -b7", "%autorelease"),
            ("%autorelease -b 7", "%autorelease"), ("7", "1"), ("7%{?dist}", "1%{?dist}"),
        ):
            with self.subTest(release=release):
                path = self.spec(release=release)
                self.assertIn(f"Release: {expected}\n".encode(), self.prepare(path).updated)

    def test_rpm_numeric_ordering_and_version_upgrade_required(self):
        path = self.spec(version="9.0")
        self.assertIn(b"Version: 10.0\n", self.prepare(path, "9.0", "10.0").updated)
        for version in ("8.0", "9.0", "09.0"):
            with self.subTest(version=version), self.assertRaisesRegex(VersionEditError, "not newer"):
                self.prepare(path, "9.0", version)

    def test_refuses_macro_versions_and_unsupported_releases(self):
        cases = [
            ("%{upstream}", "1", "%global upstream 1.0\n"),
            ("1.0", "%{revision}", "%global revision 5\n"),
            ("1.0", "%autorelease -p", ""), ("1.0", "5.local%{?dist}", ""),
        ]
        for version, release, prefix in cases:
            with self.subTest(version=version, release=release):
                path = self.spec(version=version, release=release, prefix=prefix)
                original = path.read_bytes()
                with self.assertRaisesRegex(VersionEditError, "edit this spec manually"):
                    self.prepare(path)
                self.assertEqual(path.read_bytes(), original)

    def test_refuses_conditional_directives(self):
        for directive in (b"Version: 1.0\n", b"Release: 5\n"):
            with self.subTest(directive=directive):
                path = self.spec()
                path.write_bytes(path.read_bytes().replace(directive, b"%if 1\n" + directive + b"%endif\n"))
                with self.assertRaisesRegex(VersionEditError, "unconditional"):
                    self.prepare(path)

    def test_other_conditionals_do_not_prevent_update(self):
        path = self.spec(prefix="%if 0%{?fedora} == 44\n%global testing 1\n%else\n%global testing 0\n%endif\n")
        self.assertIn(b"Version: 2.0\n", self.prepare(path).updated)

    def test_refuses_duplicate_or_missing_directives(self):
        for directive in (b"Version: 1.0\n", b"Release: 5\n"):
            for replacement in (b"", directive * 2):
                with self.subTest(directive=directive, replacement=replacement):
                    path = self.spec()
                    path.write_bytes(path.read_bytes().replace(directive, replacement))
                    with self.assertRaisesRegex(VersionEditError, "exactly one"):
                        self.prepare(path)

    def test_rejects_invalid_new_versions_and_changed_literal(self):
        path = self.spec()
        for version in ("2.0-rc1", "%{next}", "2.0\nName: other", "２.０"):
            with self.subTest(version=version), self.assertRaises(VersionEditError):
                self.prepare(path, new=version)
        with self.assertRaisesRegex(VersionEditError, "changed after checking"):
            self.prepare(path, old="0.9")

    def test_rejects_malformed_candidate_without_writing(self):
        path = self.spec()
        path.write_text(path.read_text() + "\n%if \"%{version}\" == \"2.0\"\n%error candidate-invalid\n%endif\n")
        original = path.read_bytes()
        with self.assertRaisesRegex(VersionEditError, "candidate-invalid"):
            self.prepare(path)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(list(self.root.iterdir()), [path])

    def test_candidate_name_must_match_original(self):
        path = self.spec(name="package-%{version}")
        path.write_text(path.read_text().replace("Name: package-%{version}\nVersion: 1.0\n",
                                                 "Version: 1.0\nName: package-%{version}\n"))
        with self.assertRaisesRegex(VersionEditError, "preserve the RPM name"):
            self.prepare(path)

    def test_candidate_version_must_match_requested(self):
        path = self.spec()
        with patch.object(spec_versions, "_identity", side_effect=[("package", "1.0"), ("package", "3.0")]):
            with self.assertRaisesRegex(VersionEditError, "expected version"):
                self.prepare(path)

    def test_rpm_failure_leaves_spec_and_directory_untouched(self):
        path = self.spec()
        original = path.read_bytes()
        with patch.object(spec_versions, "query", side_effect=["package\t1.0\n", GraphError("RPM unavailable")]):
            with self.assertRaisesRegex(VersionEditError, "RPM unavailable"):
                self.prepare(path)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(list(self.root.iterdir()), [path])

    def test_refuses_symlink_during_preparation_and_apply(self):
        path = self.spec()
        link = self.root / "link.spec"
        link.symlink_to(path)
        with self.assertRaisesRegex(VersionEditError, "symlink"):
            self.prepare(link)
        edit = self.prepare(path)
        path.unlink()
        path.symlink_to(link.name)
        with self.assertRaisesRegex(VersionEditError, "changed after checking"):
            apply_version_edits([edit])

    def test_checks_entire_batch_for_stale_files_before_writing(self):
        first = self.prepare(self.spec("first.spec"))
        second = self.prepare(self.spec("second.spec"))
        second.path.write_bytes(second.original + b"# user edit\n")
        with self.assertRaisesRegex(VersionEditError, "changed after checking"):
            apply_version_edits([first, second])
        self.assertEqual(first.path.read_bytes(), first.original)
        self.assertEqual(second.path.read_bytes(), second.original + b"# user edit\n")

    def test_rolls_back_earlier_writes_if_batch_write_fails(self):
        first = self.prepare(self.spec("first.spec"))
        second = self.prepare(self.spec("second.spec"))
        write = spec_versions.atomic_write

        def fail_second(path, content):
            if path == second.path:
                raise OSError("disk full")
            write(path, content)

        with patch.object(spec_versions, "atomic_write", side_effect=fail_second):
            with self.assertRaisesRegex(VersionEditError, "earlier edits restored: disk full"):
                apply_version_edits([first, second])
        self.assertEqual(first.path.read_bytes(), first.original)
        self.assertEqual(second.path.read_bytes(), second.original)

    def test_successful_batch_and_duplicate_rejection(self):
        edits = [self.prepare(self.spec(f"{name}.spec")) for name in ("first", "second")]
        with self.assertRaisesRegex(VersionEditError, "duplicate"):
            apply_version_edits(edits + edits[:1])
        apply_version_edits(edits)
        for edit in edits:
            self.assertEqual(edit.path.read_bytes(), edit.updated)


if __name__ == "__main__":
    unittest.main()
