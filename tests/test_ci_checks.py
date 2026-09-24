"""The shared CI gate stops on failures and checks expanded spec scriptlets."""

import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class CheckScriptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hyprland-check-tests-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for directory in ("scripts", "hyprland-git", "sample", "bin"):
            (self.root / directory).mkdir()
        shutil.copy(ROOT / "scripts/check.sh", self.root / "scripts/check.sh")
        shutil.copy(ROOT / "hyprland-git/update.py", self.root / "hyprland-git/update.py")
        (self.root / "sample/sample.spec").write_text("fixture\n")
        self.log = self.root / "commands.log"
        self.env = dict(os.environ, PATH=f"{self.root / 'bin'}:{os.environ['PATH']}",
                        CHECK_LOG=str(self.log), TEST_EXIT="0", PLAN_EXIT="0",
                        RPMSPEC_EXIT="0", EXPANDED_SPEC="%prep\ntrue\n")
        self.executable("python3", f"""#!/bin/sh
printf '%s\\n' "$*" >> "$CHECK_LOG"
case "$*" in
    *'unittest discover'*) exit "$TEST_EXIT" ;;
    *'scripts/rebuild.py check'*) exit "$PLAN_EXIT" ;;
esac
exec {shlex.quote(sys.executable)} "$@"
""")
        self.executable("rpmspec", """#!/bin/sh
printf 'rpmspec %s\\n' "$*" >> "$CHECK_LOG"
printf '%s' "$EXPANDED_SPEC"
exit "$RPMSPEC_EXIT"
""")

    def executable(self, name, content):
        path = self.root / "bin" / name
        path.write_text(content)
        path.chmod(0o755)

    def run_check(self):
        # Start outside the fixture checkout to exercise the script's root lookup.
        return subprocess.run(["bash", str(self.root / "scripts/check.sh")],
                              cwd=self.root / "sample", env=self.env,
                              capture_output=True, text=True)

    def test_regression_failure_stops_the_gate(self):
        self.env["TEST_EXIT"] = "19"
        result = self.run_check()
        self.assertEqual(result.returncode, 19)
        self.assertNotIn("rebuild.py", self.log.read_text())
        self.assertNotIn("rpmspec", self.log.read_text())

    def test_plan_failure_stops_the_gate(self):
        self.env["PLAN_EXIT"] = "23"
        result = self.run_check()
        self.assertEqual(result.returncode, 23)
        self.assertNotIn("rpmspec", self.log.read_text())

    def test_spec_expansion_failure_fails_the_gate(self):
        self.env["RPMSPEC_EXIT"] = "42"
        result = self.run_check()
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Validated", result.stdout)

    def test_invalid_expanded_shell_fails_the_gate(self):
        self.env["EXPANDED_SPEC"] = "%prep\nif true; then\n"
        result = self.run_check()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid %prep shell syntax", result.stderr)

    def test_valid_expanded_spec_passes(self):
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Validated sample/sample.spec", result.stdout)


if __name__ == "__main__":
    unittest.main()
