"""The shared CI gate rejects failed checks and validates both Fedora targets."""

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
        shutil.copy(ROOT / "scripts/package_graph.py", self.root / "scripts/package_graph.py")
        shutil.copy(ROOT / "hyprland-git/update.py", self.root / "hyprland-git/update.py")
        (self.root / "sample/sample.spec").write_text("fixture\n")
        self.env = dict(os.environ, PATH=f"{self.root / 'bin'}:{os.environ['PATH']}",
                        TEST_EXIT="0", PLAN_EXIT="0", RPMSPEC_EXIT="0", INVALID_SHELL_TARGET="none")
        self.executable("python3", f"""#!/bin/sh
case "$*" in
    *'unittest discover'*) exit "$TEST_EXIT" ;;
    *'scripts/rebuild.py check'*) exit "$PLAN_EXIT" ;;
esac
exec {shlex.quote(sys.executable)} "$@"
""")
        self.executable("rpmspec", """#!/bin/sh
case "$*" in
    *"fedora $INVALID_SHELL_TARGET"*)
        printf '%%prep\\nif true; then\\n'
        exit 0
        ;;
esac
printf '%%prep\\ntrue\\n'
exit "$RPMSPEC_EXIT"
""")

    def executable(self, name, content):
        path = self.root / "bin" / name
        path.write_text(content)
        path.chmod(0o755)

    def run_check(self, **env):
        # Start outside the fixture checkout to exercise the script's root lookup.
        return subprocess.run(["bash", str(self.root / "scripts/check.sh")],
                              cwd=self.root / "sample", env={**self.env, **env},
                              capture_output=True, text=True, timeout=15)

    def test_failed_validation_rejects_the_gate(self):
        for check in ("TEST_EXIT", "PLAN_EXIT", "RPMSPEC_EXIT"):
            with self.subTest(check=check):
                result = self.run_check(**{check: "1"})
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_valid_expanded_spec_passes(self):
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_invalid_shell_on_either_target_rejects_the_gate(self):
        for fedora in ("44", "45"):
            with self.subTest(fedora=fedora):
                result = self.run_check(INVALID_SHELL_TARGET=fedora)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
