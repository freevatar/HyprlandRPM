"""Exercise the packaged hyprpm command without running hyprpm or using its cache."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ("cmake", "g++", "patch", "pkg-config", "rpmspec")
UPSTREAM_CONFIGURE = r'''        progress.printMessageAbove(verboseString("setting PREFIX for cmake to {}", DataState::getHeadersPath()));

    const auto CONFIGURE_CMD =
        nixDevelopIfNeeded(std::format("cd {} && cmake --no-warn-unused-cli -DCMAKE_BUILD_TYPE:STRING=Release -DCMAKE_INSTALL_PREFIX:STRING=\"{}\" -S . -B ./build", WORKINGDIR,
                                       DataState::getHeadersPath()),
                           HLVER);

    if (!CONFIGURE_CMD) {
'''
CMAKE_PREAMBLE = """cmake_minimum_required(VERSION 3.30)
project(header_configuration NONE)
find_package(PkgConfig REQUIRED)
"""


@unittest.skipUnless(all(shutil.which(tool) for tool in TOOLS), "requires RPM/CMake/C++ tools")
class HyprpmCompatibilityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        pkgconfig = self.directory / "pkgconfig"
        pkgconfig.mkdir()
        (pkgconfig / "lua.pc").write_text(
            "Name: lua\nDescription: isolated Lua 5.4 test fixture\nVersion: 5.4.8\nLibs:\nCflags:\n"
        )
        self.env = {
            **os.environ,
            "PKG_CONFIG_LIBDIR": str(pkgconfig),
            "PKG_CONFIG_PATH": str(pkgconfig),
            "CCACHE_DISABLE": "1",
        }

    def command(self, package, working_directory):
        fixture = self.directory / f"{package}-fixture"
        source = fixture / "hyprpm/src/core/PluginManager.cpp"
        source.parent.mkdir(parents=True)
        source.write_text(UPSTREAM_CONFIGURE)
        subprocess.run(
            ["patch", "--batch", "--fuzz=0", "-p1"],
            input=(ROOT / package / "lua54-hyprpm.patch").read_text(),
            cwd=fixture, text=True, capture_output=True, check=True,
        )
        patched = source.read_text()
        start = patched.index("std::format(")
        end_marker = "DataState::getHeadersPath())"
        end = patched.index(end_marker, start) + len(end_marker)
        expression = patched[start:end].replace("WORKINGDIR", "argv[1]").replace(
            "DataState::getHeadersPath()", "argv[2]"
        )
        program = fixture / "command.cpp"
        program.write_text(
            "#include <format>\n#include <iostream>\n"
            f"int main(int argc, char** argv) {{ std::cout << {expression}; }}\n"
        )
        executable = fixture / "command"
        subprocess.run(
            ["g++", "-std=c++20", str(program), "-o", str(executable)],
            env=self.env, capture_output=True, text=True, check=True,
        )
        command = subprocess.run(
            [str(executable), str(working_directory), str(self.directory / "headers")],
            capture_output=True, text=True, check=True,
        ).stdout
        subprocess.run(["/bin/sh", "-n"], input=command, text=True, check=True)
        return command

    def test_downloaded_headers_configure_with_lua54(self):
        for package in ("hyprland", "hyprland-git"):
            with self.subTest(package=package):
                working = self.directory / package
                working.mkdir()
                cmake = working / "CMakeLists.txt"
                cmake.write_text(
                    CMAKE_PREAMBLE
                    + "pkg_search_module(LUA REQUIRED IMPORTED_TARGET GLOBAL lua>=5.5 lua5.5 lua-5.5)\n"
                )
                original = subprocess.run(
                    ["cmake", "-S", str(working), "-B", str(working / "original-build")],
                    env=self.env, capture_output=True, text=True,
                )
                self.assertNotEqual(original.returncode, 0)
                self.assertIn("None of the required", original.stderr)
                command = self.command(package, working)
                subprocess.run(
                    ["/bin/sh", "-eu", "-c", command],
                    env=self.env, capture_output=True, text=True, check=True,
                )
                self.assertIn(
                    "pkg_search_module(LUA REQUIRED IMPORTED_TARGET GLOBAL lua)\n", cmake.read_text()
                )
                self.assertTrue((working / "build/CMakeCache.txt").is_file())

    def test_upstream_drift_stops_before_cmake(self):
        working = self.directory / "drift"
        working.mkdir()
        (working / "CMakeLists.txt").write_text(CMAKE_PREAMBLE)
        result = subprocess.run(
            ["/bin/sh", "-eu", "-c", self.command("hyprland", working)],
            env=self.env, capture_output=True, text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CMake Error at CMakeLists.txt:", result.stdout)
        self.assertIn("Lua 5.4 compatibility patch did not match", result.stdout)
        self.assertFalse((working / "build").exists())

    def test_patch_is_only_applied_on_lua54_fedora_releases(self):
        for package in ("hyprland", "hyprland-git"):
            for fedora in (44, 45):
                with self.subTest(package=package, fedora=fedora):
                    expanded = subprocess.run(
                        ["rpmspec", "--define", f"fedora {fedora}", "-P", str(ROOT / package / f"{package}.spec")],
                        text=True, capture_output=True, check=True,
                    ).stdout
                    preamble, sections = expanded.split("%prep\n", 1)
                    self.assertIn("lua54-hyprpm.patch", preamble)
                    prep = sections.split("%build\n", 1)[0]
                    self.assertEqual("lua54-hyprpm.patch" in prep, fedora == 44)


if __name__ == "__main__":
    unittest.main()
