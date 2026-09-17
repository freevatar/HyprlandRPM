"""Compile generated version metadata, including characters unsafe in C++ strings."""

import base64
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ("cmake", "g++", "patch", "rpmspec")

# Preserve the upstream configure_file boundary so the shipped patch is exercised.
CMAKE_FIXTURE = """cmake_minimum_required(VERSION 3.30)
project(metadata NONE)
set(GIT_COMMIT_HASH "$ENV{GIT_COMMIT_HASH}")
set(GIT_COMMIT_MESSAGE "$ENV{GIT_COMMIT_MESSAGE}")
if(NOT GIT_COMMIT_HASH)
  set(GIT_COMMIT_HASH "unknown")
endif()

configure_file(
    ${CMAKE_SOURCE_DIR}/src/version.h.in
    ${CMAKE_SOURCE_DIR}/src/version.h
    @ONLY
)
"""


@unittest.skipUnless(all(shutil.which(tool) for tool in TOOLS), "requires RPM/CMake/C++ tools")
class VersionMetadataTests(unittest.TestCase):
    def generated_metadata(self, package, title):
        spec_path = ROOT / package / f"{package}.spec"
        text = spec_path.read_text()
        if package == "hyprland-git":
            encoded = base64.b64encode(title.encode()).decode()
            text = re.sub(
                r"(?m)^%global hyprland_commit_message_b64 .*$",
                f"%global hyprland_commit_message_b64 {encoded}",
                text,
            )

        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            (directory / "src").mkdir()
            (directory / "CMakeLists.txt").write_text(CMAKE_FIXTURE)
            (directory / "src/version.h.in").write_text(
                '#define GIT_COMMIT_HASH "@GIT_COMMIT_HASH@"\n'
                '#define GIT_COMMIT_MESSAGE "@GIT_COMMIT_MESSAGE@"\n'
            )
            (directory / "package.spec").write_text(text)
            subprocess.run(
                ["patch", "--batch", "--fuzz=0", "-p1"],
                input=(ROOT / package / "escape-version-metadata.patch").read_text(),
                cwd=directory, text=True, capture_output=True, check=True,
            )
            expanded = subprocess.run(
                ["rpmspec", "-P", str(directory / "package.spec")],
                text=True, capture_output=True, check=True,
            ).stdout
            build = expanded.split("%build\n", 1)[1]
            marker = (
                "export GIT_COMMIT_MESSAGE\n" if package == "hyprland-git"
                else "export GIT_DIRTY='clean'\n"
            )
            # Run the real RPM-expanded metadata exports without running its build.
            self.assertIn(marker, build)
            exports = build.split(marker, 1)[0] + marker
            subprocess.run(
                ["/bin/sh", "-eu", "-c", exports + 'exec cmake -S . -B build'],
                cwd=directory, text=True, capture_output=True, check=True,
                env={key: value for key, value in os.environ.items() if not key.startswith("GIT_")},
            )
            (directory / "main.cpp").write_text(
                '#include <iostream>\n#include "src/version.h"\n'
                'int main() { std::cout << GIT_COMMIT_HASH << "\\n" << GIT_COMMIT_MESSAGE; }\n'
            )
            subprocess.run(
                ["g++", "-std=c++17", "main.cpp", "-o", "metadata"],
                cwd=directory, text=True, capture_output=True, check=True,
                env={**os.environ, "CCACHE_DISABLE": "1"},
            )
            output = subprocess.run(
                [str(directory / "metadata")], text=True, capture_output=True, check=True
            ).stdout
            return output.split("\n", 1)

    def test_snapshot_title_survives_rpm_shell_cmake_and_cpp(self):
        for title in (
            'fix "focus" handling',
            r'preserve \n and C:\tmp\path',
            'quotes "and\\"; ${not_expanded} $(not_executed) %macro',
            "UTF-8 café — 窗口",
            "trailing slash\\",
        ):
            with self.subTest(title=title):
                commit, actual_title = self.generated_metadata("hyprland-git", title)
                self.assertRegex(commit, r"^[0-9a-f]{40}$")
                self.assertEqual(actual_title, title)

    def test_stable_build_exports_the_pinned_commit(self):
        commit, _ = self.generated_metadata("hyprland", "")
        expected = re.search(
            r"(?m)^%global hyprland_commit ([0-9a-f]{40})$",
            (ROOT / "hyprland/hyprland.spec").read_text(),
        )
        self.assertIsNotNone(expected)
        self.assertEqual(commit, expected.group(1))


if __name__ == "__main__":
    unittest.main()
