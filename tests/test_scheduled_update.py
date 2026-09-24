"""Exercise publication races using local Git repositories and small fake tools."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import textwrap
import unittest


HELPER = Path(__file__).resolve().parents[1] / ".github/scripts/update.sh"


@unittest.skipUnless(shutil.which("git") and shutil.which("bash"), "requires Git and Bash")
class ScheduledUpdateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="scheduled-update-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repo, self.origin, self.peer = (self.root / name for name in ("repo", "origin", "peer"))
        self.scratch = self.root / "scratch"
        self.scratch.mkdir()
        self.git(self.root, "init", "-q", "--bare", "--initial-branch=master", str(self.origin))
        self.git(self.root, "clone", "-q", str(self.origin), str(self.repo))
        self.identity(self.repo)
        self.write("package/package.spec", "Version: 1\n")
        self.write("build-plan.json", '{"base": "initial"}\n')
        self.write("hyprland-git/update.py", """
            import os
            from pathlib import Path
            spec = Path("package/package.spec")
            if not os.environ.get("SKIP_UPDATE"):
                spec.write_text(spec.read_text().replace("Version: 1", "Version: 2"))
        """)
        self.write("scripts/rebuild.py", """
            import json
            from pathlib import Path
            import subprocess
            import sys
            if sys.argv[1] == "apply":
                changed = subprocess.check_output(["git", "diff", "--name-only"], text=True)
                if changed:
                    Path("build-plan.json").write_text(json.dumps({"base": sys.argv[3]}) + "\\n")
        """)
        self.write("scripts/check.sh", """
            set -eu
            printf 'check\\n' >> "$VALIDATOR_LOG"
            if [ -n "${VALIDATION_FAIL:-}" ]; then exit 41; fi
            if [ -n "${PEER_PUSH_MODE:-}" ] && { [ "$PEER_PUSH_MODE" = always ] || [ ! -e "$PEER_MARKER" ]; }; then
                touch "$PEER_MARKER"
                git -C "$PEER_DIR" pull --ff-only
                printf '# peer update\\n' >> "$PEER_DIR/package/package.spec"
                git -C "$PEER_DIR" commit -am 'Peer update'
                git -C "$PEER_DIR" push origin master
            fi
        """)
        self.git(self.repo, "add", ".")
        self.git(self.repo, "commit", "-qm", "Baseline")
        self.git(self.repo, "push", "-q", "origin", "master")
        self.initial = self.git(self.repo, "rev-parse", "HEAD")
        self.git(self.root, "clone", "-q", str(self.origin), str(self.peer))
        self.identity(self.peer)
        self.output = self.root / "output"
        self.log = self.root / "checks"
        self.env = {
            **os.environ,
            "TMPDIR": str(self.scratch),
            "GITHUB_OUTPUT": str(self.output),
            "VALIDATOR_LOG": str(self.log),
            "PEER_DIR": str(self.peer),
            "PEER_MARKER": str(self.root / "peer-marker"),
        }

    @staticmethod
    def git(directory, *args):
        return subprocess.check_output(
            ["git", "-C", str(directory), *args], text=True, stderr=subprocess.PIPE
        ).strip()

    def identity(self, directory):
        self.git(directory, "config", "user.name", "Scheduled update test")
        self.git(directory, "config", "user.email", "test@example.invalid")

    def write(self, name, content):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(content).lstrip())

    def run_helper(self, **env):
        result = subprocess.run(
            ["bash", str(HELPER)], cwd=self.repo, env={**self.env, **env},
            capture_output=True, text=True, timeout=45,
        )
        self.assertEqual(self.git(self.repo, "rev-parse", "HEAD"), self.initial)
        self.assertEqual(self.git(self.repo, "worktree", "list", "--porcelain").count("worktree "), 1)
        self.assertEqual(list(self.scratch.iterdir()), [])
        return result

    def assert_published(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        published = self.output.read_text().strip().removeprefix("commit=")
        self.assertEqual(published, self.git(self.origin, "rev-parse", "master"))
        return published

    def test_publishes_update_without_touching_original_work(self):
        self.write("local.txt", "staged local work\n")
        self.git(self.repo, "add", "local.txt")
        self.write("package/package.spec", "unstaged local work\n")
        index = self.git(self.repo, "write-tree")
        status = self.git(self.repo, "status", "--porcelain")
        published = self.assert_published(self.run_helper())
        self.assertEqual(self.git(self.origin, "show", f"{published}:package/package.spec"), "Version: 2")
        self.assertEqual(self.git(self.repo, "write-tree"), index)
        self.assertEqual(self.git(self.repo, "status", "--porcelain"), status)

    def test_concurrent_push_regenerates_plan_and_preserves_peer_edits(self):
        published = self.assert_published(self.run_helper(PEER_PUSH_MODE="once"))
        peer = self.git(self.peer, "rev-parse", "HEAD")
        self.assertEqual(self.git(self.origin, "rev-parse", f"{published}^"), peer)
        plan = json.loads(self.git(self.origin, "show", f"{published}:build-plan.json"))
        self.assertEqual(plan["base"], peer)
        self.assertEqual(self.git(self.origin, "show", f"{published}:package/package.spec"),
                         "Version: 2\n# peer update")
        self.assertEqual(len(self.log.read_text().splitlines()), 2)

    def test_validation_failure_does_not_publish(self):
        result = self.run_helper(VALIDATION_FAIL="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.git(self.origin, "rev-parse", "master"), self.initial)
        self.assertFalse(self.output.exists())

    def test_noop_returns_existing_commit(self):
        self.assertEqual(self.assert_published(self.run_helper(SKIP_UPDATE="1")), self.initial)

    def test_noop_rechecks_master_after_validation(self):
        published = self.assert_published(self.run_helper(SKIP_UPDATE="1", PEER_PUSH_MODE="once"))
        self.assertEqual(published, self.git(self.peer, "rev-parse", "HEAD"))
        self.assertEqual(len(self.log.read_text().splitlines()), 2)

    def test_rejected_push_with_unchanged_remote_stops_without_retry(self):
        hook = self.origin / "hooks/pre-receive"
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o755)
        result = self.run_helper()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("master was unchanged", result.stderr)
        self.assertEqual(len(self.log.read_text().splitlines()), 1)
        self.assertEqual(self.git(self.origin, "rev-parse", "master"), self.initial)
        self.assertFalse(self.output.exists())

    def test_accepted_push_with_lost_response_is_recognized(self):
        wrapper = self.root / "bin/git"
        wrapper.parent.mkdir()
        wrapper.write_text(textwrap.dedent("""\
            #!/usr/bin/env bash
            "$REAL_GIT" "$@"
            status=$?
            if [[ $status -eq 0 && " $* " == *" push origin HEAD:refs/heads/master "* ]]; then
                exit 1
            fi
            exit "$status"
        """))
        wrapper.chmod(0o755)
        published = self.assert_published(self.run_helper(
            REAL_GIT=shutil.which("git"), PATH=f"{wrapper.parent}:{os.environ['PATH']}",
        ))
        self.assertNotEqual(published, self.initial)
        self.assertEqual(len(self.log.read_text().splitlines()), 1)

    def test_concurrent_push_retries_are_bounded(self):
        result = self.run_helper(PEER_PUSH_MODE="always")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("all three attempts", result.stderr)
        self.assertEqual(len(self.log.read_text().splitlines()), 3)
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
