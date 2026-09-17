"""Metadata updates use real Git state; GitHub and RPM validation are isolated."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import os
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
    release_sha = "e" * 40

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
        return {"subprojects/hyprland-protocols": "c" * 40, "subprojects/udis86": "d" * 40}[path]

    def release_commit(self, version):
        return self.release_sha


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.previous_cwd = Path.cwd()
        self.temp = tempfile.TemporaryDirectory(prefix="hyprland-updater-tests-")
        self.checkout = Path(self.temp.name)
        os.chdir(self.checkout)
        self.git("init", "-q", "--initial-branch=master")
        self.git("config", "user.name", "Updater test")
        self.git("config", "user.email", "updater@example.invalid")
        Path("git.spec").write_text(self.snapshot_text(current=False))
        Path("release.spec").write_text(self.release_text())
        Path("unrelated.txt").write_text("original\n")
        self.git("add", ".")
        self.git("commit", "-qm", "baseline")
        self.config = updater.Config(Path("git.spec"), Path("release.spec"), False)
        self.stack = contextlib.ExitStack()
        self.stack.enter_context(patch.object(updater, "GitHubClient", GitHubFixture))
        self.stack.enter_context(patch.object(updater, "require_commands"))
        self.stack.enter_context(patch.object(updater, "compare_rpm_versions", side_effect=lambda a, b: 0 if a == b else 12))
        self.validation = self.stack.enter_context(patch.object(updater, "validate_specs"))
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.stack.enter_context(contextlib.redirect_stderr(io.StringIO()))

    def tearDown(self):
        self.stack.close()
        os.chdir(self.previous_cwd)
        self.temp.cleanup()

    @staticmethod
    def git(*args):
        return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout.strip()

    @staticmethod
    def release_text(version="0.56.2", sha="e" * 40):
        return f"%global upstream_version {version}\n%global hyprland_commit {sha}\n"

    @staticmethod
    def snapshot_text(*, current):
        return (
            "%global upstream_version 0.56.2\n%global snapshot 1\n"
            "%global hyprland_commit " + ("b" if current else "a") * 40 + "\n"
            "%global hyprland_commits 20\n"
            "%global hyprland_commit_date " + updater.format_commit_date("2026-09-16T12:00:00Z") + "\n"
            "%global hyprland_commit_message_b64 " + updater.encode_text_base64('fix "focus" handling') + "\n"
            "%global protocols_commit " + "c" * 40 + "\n"
            "%global udis86_commit " + "d" * 40 + "\n"
        )

    def record_fixture(self):
        self.git("add", "git.spec", "release.spec")
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
        self.assertEqual(spec.get_global("protocols_commit"), "c" * 40)
        self.assertEqual(spec.get_global("udis86_commit"), "d" * 40)
        self.validation.assert_called_once()

    def test_new_release_updates_both_specs_and_resets_snapshot_counter(self):
        with patch.object(GitHubFixture, "version", "0.57.0"):
            updater.update(self.config)
        snapshot = updater.SpecDocument.load(Path("git.spec"))
        stable = updater.SpecDocument.load(Path("release.spec"))
        self.assertEqual(snapshot.get_global("upstream_version"), "0.57.0")
        self.assertEqual(snapshot.get_global("snapshot"), "1")
        self.assertEqual(stable.get_global("upstream_version"), "0.57.0")
        self.assertEqual(stable.get_global("hyprland_commit"), GitHubFixture.release_sha)

    def test_stable_drift_is_corrected_without_incrementing_snapshot(self):
        Path("git.spec").write_text(self.snapshot_text(current=True))
        Path("release.spec").write_text(self.release_text("0.55.0", "a" * 40))
        self.record_fixture()
        updater.update(self.config)
        self.assertEqual(updater.SpecDocument.load(Path("git.spec")).get_global("snapshot"), "1")
        self.assertEqual(Path("release.spec").read_text(), self.release_text())
        self.assertEqual(self.git("diff", "--name-only"), "release.spec")

    def test_current_versions_still_resolve_stable_tag_identity(self):
        Path("git.spec").write_text(self.snapshot_text(current=True))
        Path("release.spec").write_text(self.release_text(sha="a" * 40))
        self.record_fixture()
        with patch.object(GitHubFixture, "release_commit", return_value="f" * 40) as resolve:
            updater.update(self.config)
        resolve.assert_called_once_with("0.56.2")
        self.assertEqual(updater.SpecDocument.load(Path("release.spec")).get_global("hyprland_commit"), "f" * 40)
        self.assertEqual(self.git("diff", "--name-only"), "release.spec")

    def test_current_metadata_leaves_worktree_unchanged(self):
        Path("git.spec").write_text(self.snapshot_text(current=True))
        self.record_fixture()
        self.assertEqual(updater.update(self.config), 0)
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.validation.assert_not_called()

    def test_managed_dirty_and_staged_specs_are_rejected(self):
        original_snapshot = Path("git.spec").read_bytes()
        for staged in (False, True):
            with self.subTest(staged=staged):
                Path("release.spec").write_text("manual edits\n")
                if staged:
                    self.git("add", "release.spec")
                with self.assertRaisesRegex(updater.UpdateError, "managed spec files have"):
                    updater.update(self.config)
                self.assertEqual(Path("git.spec").read_bytes(), original_snapshot)
                self.assertEqual(Path("release.spec").read_text(), "manual edits\n")
                self.git("restore", "--staged", "--worktree", "release.spec")

    def test_missing_prerequisite_leaves_specs_untouched(self):
        with patch.object(updater, "require_commands", side_effect=updater.UpdateError("rpmspec unavailable")):
            with self.assertRaisesRegex(updater.UpdateError, "rpmspec unavailable"):
                updater.update(self.config)
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_validation_failure_restores_both_specs_and_index(self):
        Path("unrelated.txt").write_text("staged work\n")
        self.git("add", "unrelated.txt")
        index = self.git("write-tree")
        with patch.object(GitHubFixture, "version", "0.57.0"):
            with patch.object(updater, "validate_specs", side_effect=updater.UpdateError("invalid spec")):
                with self.assertRaisesRegex(updater.UpdateError, "invalid spec"):
                    updater.update(self.config)
        self.assertEqual(self.git("diff", "--name-only"), "")
        self.assertEqual(self.git("write-tree"), index)

    def test_second_write_failure_restores_first_spec(self):
        real_write = updater.SpecDocument.write

        def fail_stable(spec):
            if spec.path.name == "release.spec":
                raise updater.UpdateError("disk write failed")
            real_write(spec)

        with patch.object(GitHubFixture, "version", "0.57.0"):
            with patch.object(updater.SpecDocument, "write", fail_stable):
                with self.assertRaisesRegex(updater.UpdateError, "disk write failed"):
                    updater.update(self.config)
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_fetch_failure_leaves_both_specs_untouched(self):
        with patch.object(GitHubFixture, "release_commit", side_effect=updater.UpdateError("tag lookup failed")):
            with self.assertRaisesRegex(updater.UpdateError, "tag lookup failed"):
                updater.update(self.config)
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_dry_run_validates_and_restores_both_specs_and_permissions(self):
        Path("git.spec").chmod(0o640)
        config = updater.Config(Path("git.spec"), Path("release.spec"), True)
        with patch.object(GitHubFixture, "version", "0.57.0"):
            with patch.object(updater, "show_git_diff") as show:
                updater.update(config)
        self.validation.assert_called_once()
        show.assert_called_once()
        self.assertEqual(Path("git.spec").stat().st_mode & 0o777, 0o640)
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_dry_run_diff_failure_restores_specs(self):
        config = updater.Config(Path("git.spec"), Path("release.spec"), True)
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
            with patch.object(updater, "_sleep_before_retry") as sleep:
                with self.assertRaises(updater.UpdateError) as caught:
                    updater.HttpClient().request("https://example.invalid/metadata", label="metadata")
        self.assertEqual(request.call_count, updater.HTTP_ATTEMPTS)
        self.assertEqual(sleep.call_count, updater.HTTP_ATTEMPTS - 1)
        self.assertNotIn("secret-token", str(caught.exception))

    def test_nonretryable_http_error_omits_response_body(self):
        error = HTTPError("https://example.invalid", 401, "secret-token", {}, io.BytesIO(b"secret-token"))
        with patch.object(updater, "urlopen", side_effect=error) as request:
            with self.assertRaisesRegex(updater.UpdateError, "HTTP status 401") as caught:
                updater.HttpClient().request("https://example.invalid", label="metadata")
        self.assertEqual(request.call_count, 1)
        self.assertNotIn("secret-token", str(caught.exception))

    def test_retry_after_is_bounded(self):
        with patch.object(updater.time, "sleep") as sleep:
            updater._sleep_before_retry(7, "86400")
        sleep.assert_called_once_with(updater.MAX_RETRY_DELAY_SECONDS)

    def test_oversized_response_is_rejected(self):
        with self.assertRaisesRegex(updater.UpdateError, "response exceeds"):
            updater._bounded_read(io.BytesIO(b"x" * (updater.MAX_RESPONSE_BYTES + 1)), "metadata")


if __name__ == "__main__":
    unittest.main()
