"""Metadata updates use real Git state; GitHub and RPM validation are isolated."""

from __future__ import annotations

import base64
import contextlib
import importlib.util
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError


MODULE_PATH = Path(__file__).resolve().parents[1] / "hyprland-git" / "update.py"
MODULE_SPEC = importlib.util.spec_from_file_location("hyprland_updater", MODULE_PATH)
assert MODULE_SPEC is not None and MODULE_SPEC.loader is not None
updater = importlib.util.module_from_spec(MODULE_SPEC)
sys.modules[MODULE_SPEC.name] = updater
MODULE_SPEC.loader.exec_module(updater)


class GitHubFixture:
    version = "0.56.2"
    commit = "b" * 40

    def __init__(self, token):
        pass

    def latest_release(self):
        return self.version

    def main_commit(self):
        return self.commit, "2026-09-16T12:00:00Z", 'fix "focus" handling'

    def commit_count(self, commit):
        return 20

    def submodule_commit(self, path, commit):
        assert commit == self.commit
        if path == "subprojects/hyprland-protocols":
            raise updater.UpdateError(f"fetching {path} commit failed with HTTP status 404")
        assert path == "subprojects/udis86"
        return "d" * 40


class UpdateTests(unittest.TestCase):
    def setUp(self):
        checkout = self.enterContext(tempfile.TemporaryDirectory(prefix="hyprland-updater-tests-"))
        self.enterContext(contextlib.chdir(checkout))
        self.git("init", "-q", "--initial-branch=master")
        self.git("config", "user.name", "Updater test")
        self.git("config", "user.email", "updater@example.invalid")
        Path("git.spec").write_text(self.snapshot_text(current=False))
        Path("unrelated.txt").write_text("original\n")
        self.git("add", ".")
        self.git("commit", "-qm", "baseline")
        self.config = updater.Config(Path("git.spec"), False)
        self.enterContext(patch.object(updater, "GitHubClient", GitHubFixture))
        self.enterContext(patch.object(updater, "require_commands"))
        self.enterContext(patch.object(updater, "compare_rpm_versions", side_effect=lambda a, b: 0 if a == b else 12))
        self.validation = self.enterContext(patch.object(updater, "validate_specs"))
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        self.enterContext(contextlib.redirect_stderr(io.StringIO()))

    @staticmethod
    def git(*args):
        return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout.strip()

    @staticmethod
    def snapshot_text(*, current, release="%autorelease -b4"):
        date = "Wed Sep 16 08:00:00 2026" if current else "Tue Sep 15 08:00:00 2026"
        title = 'fix "focus" handling' if current else "previous commit"
        return (
            "%global upstream_version 0.56.2\n%global snapshot 1\n"
            "%global hyprland_commit " + ("b" if current else "a") * 40 + "\n"
            f"%global hyprland_commits {20 if current else 19}\n"
            f"%global hyprland_commit_date {date}\n"
            "%global hyprland_commit_message_b64 " + base64.b64encode(title.encode()).decode() + "\n"
            "%global udis86_commit " + ("d" if current else "c") * 40 + "\n"
            "Name: hyprland-git\n"
            "Version: %{upstream_version}^%{snapshot}.git%{hyprland_commit}\n"
            f"Release: {release}\n"
        )

    def record_fixture(self):
        self.git("add", "git.spec")
        self.git("commit", "-qm", "fixture metadata")

    def test_updates_metadata_without_committing_staging_or_building(self):
        head = self.git("rev-parse", "HEAD")
        Path("unrelated.txt").write_text("private staged work\n")
        self.git("add", "unrelated.txt")
        index = self.git("write-tree")
        with patch.object(updater, "run_command", wraps=updater.run_command) as commands:
            self.assertEqual(updater.update(self.config), 0)
        self.assertEqual(self.git("rev-parse", "HEAD"), head)
        self.assertEqual(self.git("write-tree"), index)
        self.assertEqual(self.git("diff", "--name-only"), "git.spec")
        self.assertEqual(self.git("diff", "--cached", "--name-only"), "unrelated.txt")
        for call in commands.call_args_list:
            args = call.args[0]
            self.assertNotIn("copr", args)
            self.assertFalse(any(word in args for word in ("commit", "push", "add")))
        spec = updater.SpecDocument.load(Path("git.spec"))
        self.assertEqual(spec.get_global("snapshot"), "2")
        self.assertEqual(spec.get_global("hyprland_commit"), "b" * 40)
        self.assertEqual(spec.get_global("hyprland_commits"), "20")
        self.assertEqual(spec.get_global("hyprland_commit_date"), "Wed Sep 16 08:00:00 2026")
        self.assertEqual(base64.b64decode(spec.get_global("hyprland_commit_message_b64")).decode(),
                         'fix "focus" handling')
        self.assertEqual(spec.get_global("udis86_commit"), "d" * 40)
        self.assertIn("Release: %autorelease\n", spec.text)
        self.validation.assert_called_once()

    def test_new_release_updates_version_and_resets_snapshot_counter(self):
        with patch.object(GitHubFixture, "version", "0.57.0"):
            updater.update(self.config)
        snapshot = updater.SpecDocument.load(Path("git.spec"))
        self.assertEqual(snapshot.get_global("upstream_version"), "0.57.0")
        self.assertEqual(snapshot.get_global("snapshot"), "1")
        self.assertIn("Release: %autorelease\n", snapshot.text)

    def test_current_metadata_leaves_worktree_unchanged(self):
        Path("git.spec").write_text(self.snapshot_text(current=True))
        self.record_fixture()
        self.assertEqual(updater.update(self.config), 0)
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.validation.assert_not_called()

    def test_new_snapshot_resets_supported_release_forms(self):
        for old, new in (
            ("%autorelease", "%autorelease"),
            ("%autorelease -b15", "%autorelease"),
            ("%autorelease -b 15", "%autorelease"),
            ("15", "1"),
            ("15%{?dist}", "1%{?dist}"),
        ):
            with self.subTest(release=old):
                Path("git.spec").write_text(self.snapshot_text(current=False, release=old))
                self.git("add", "git.spec")
                self.git("commit", "--allow-empty", "-qm", "release fixture")
                updater.update(self.config)
                self.assertIn(f"Release: {new}\n", Path("git.spec").read_text())

    def test_ambiguous_or_unsupported_release_leaves_spec_untouched(self):
        for release_line, error in (
            ("Release: %{custom_release}\n", "unsupported Release"),
            ("Release: %autorelease -p\n", "unsupported Release"),
            ("Release: 4\nRelease: 5\n", "exactly one Release"),
            ("", "exactly one Release"),
            ("%if 0%{?fedora}\nRelease: 4\n%endif\n", "conditional Release"),
        ):
            with self.subTest(release_line=release_line):
                text = self.snapshot_text(current=False).replace("Release: %autorelease -b4\n", release_line)
                Path("git.spec").write_text(text)
                self.record_fixture()
                with patch.object(GitHubFixture, "version", "0.57.0"):
                    with self.assertRaisesRegex(updater.UpdateError, error):
                        updater.update(self.config)
                self.assertEqual(self.git("status", "--porcelain"), "")
        self.validation.assert_not_called()

    def test_managed_dirty_and_staged_spec_is_rejected(self):
        for staged in (False, True):
            with self.subTest(staged=staged):
                Path("git.spec").write_text("manual edits\n")
                if staged:
                    self.git("add", "git.spec")
                with self.assertRaisesRegex(updater.UpdateError, "managed spec files have"):
                    updater.update(self.config)
                self.assertEqual(Path("git.spec").read_text(), "manual edits\n")
                self.git("restore", "--staged", "--worktree", "git.spec")

    def test_validation_failure_restores_spec_and_index(self):
        Path("unrelated.txt").write_text("staged work\n")
        self.git("add", "unrelated.txt")
        index = self.git("write-tree")

        def invalid_spec(specs):
            for spec in specs:
                self.assertIn("Release: %autorelease\n", spec.path.read_text())
            raise updater.UpdateError("invalid spec")

        with patch.object(GitHubFixture, "version", "0.57.0"):
            with patch.object(updater, "validate_specs", side_effect=invalid_spec):
                with self.assertRaisesRegex(updater.UpdateError, "invalid spec"):
                    updater.update(self.config)
        self.assertEqual(self.git("diff", "--name-only"), "")
        self.assertEqual(self.git("write-tree"), index)

    def test_write_failure_restores_spec(self):
        real_write = updater.SpecDocument.write

        def fail_write(spec):
            real_write(spec)
            raise updater.UpdateError("disk write failed")

        with patch.object(GitHubFixture, "version", "0.57.0"):
            with patch.object(updater.SpecDocument, "write", fail_write):
                with self.assertRaisesRegex(updater.UpdateError, "disk write failed"):
                    updater.update(self.config)
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_fetch_failure_leaves_spec_untouched(self):
        with patch.object(GitHubFixture, "submodule_commit", side_effect=updater.UpdateError("submodule lookup failed")):
            with self.assertRaisesRegex(updater.UpdateError, "submodule lookup failed"):
                updater.update(self.config)
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_dry_run_validates_and_restores_spec_and_permissions(self):
        Path("git.spec").chmod(0o640)
        config = updater.Config(Path("git.spec"), True)
        with patch.object(GitHubFixture, "version", "0.57.0"):
            with patch.object(updater, "show_git_diff") as show:
                updater.update(config)
        self.validation.assert_called_once()
        show.assert_called_once()
        self.assertEqual(Path("git.spec").stat().st_mode & 0o777, 0o640)
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_dry_run_diff_failure_restores_spec(self):
        config = updater.Config(Path("git.spec"), True)
        with patch.object(updater, "show_git_diff", side_effect=updater.UpdateError("cannot display diff")):
            with self.assertRaisesRegex(updater.UpdateError, "cannot display diff"):
                updater.update(config)
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_invalid_snapshot_or_downgrade_changes_nothing(self):
        Path("git.spec").write_text(self.snapshot_text(current=False).replace("%global snapshot 1", "%global snapshot invalid"))
        self.record_fixture()
        with self.assertRaisesRegex(updater.UpdateError, "invalid snapshot"):
            updater.update(self.config)
        self.assertEqual(self.git("status", "--porcelain"), "")
        with patch.object(updater, "compare_rpm_versions", return_value=11):
            with self.assertRaisesRegex(updater.UpdateError, "newer than latest"):
                updater.update(self.config)
        self.assertEqual(self.git("status", "--porcelain"), "")


class HttpTests(unittest.TestCase):
    def test_get_timeout_retries_with_bounded_attempts_and_safe_diagnostics(self):
        with patch.object(updater, "urlopen", side_effect=URLError("secret-token")) as request:
            with patch.object(updater.time, "sleep") as sleep:
                with self.assertRaises(updater.UpdateError) as caught:
                    updater.HttpClient().request("https://example.invalid/metadata", label="metadata")
        self.assertEqual(request.call_count, updater.HTTP_ATTEMPTS)
        self.assertEqual(sleep.call_count, updater.HTTP_ATTEMPTS - 1)
        self.assertTrue(all(0 <= call.args[0] <= 30 for call in sleep.call_args_list))
        self.assertNotIn("secret-token", str(caught.exception))

    def test_nonretryable_http_error_omits_response_body(self):
        error = HTTPError("https://example.invalid", 401, "secret-token", {}, io.BytesIO(b"secret-token"))
        with patch.object(updater, "urlopen", side_effect=error) as request:
            with self.assertRaisesRegex(updater.UpdateError, "HTTP status 401") as caught:
                updater.HttpClient().request("https://example.invalid", label="metadata")
        self.assertEqual(request.call_count, 1)
        self.assertNotIn("secret-token", str(caught.exception))

    def test_retry_after_is_bounded(self):
        error = HTTPError("https://example.invalid", 503, "unavailable", {"Retry-After": "86400"}, None)
        response = io.BytesIO(b"metadata")
        response.headers = {}
        with patch.object(updater, "urlopen", side_effect=[error, response]):
            with patch.object(updater.time, "sleep") as sleep:
                payload, _ = updater.HttpClient().request("https://example.invalid", label="metadata")
        self.assertEqual(payload, b"metadata")
        sleep.assert_called_once_with(30)

    def test_oversized_response_is_rejected(self):
        response = io.BytesIO(b"x" * (updater.MAX_RESPONSE_BYTES + 1))
        response.headers = {}
        with patch.object(updater, "urlopen", return_value=response):
            with self.assertRaisesRegex(updater.UpdateError, "response exceeds"):
                updater.HttpClient().request("https://example.invalid", label="metadata")


if __name__ == "__main__":
    unittest.main()
