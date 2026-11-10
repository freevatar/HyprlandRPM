"""Exercise the packaged hyprpm command without running hyprpm or using its cache."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "hyprland-git"
TOOLS = ("cmake", "g++", "make", "patch", "pkg-config")
UPSTREAM_CONFIGURE = r'''    if (m_bVerbose)
        progress.printMessageAbove(verboseString("setting PREFIX for cmake to {}", DataState::getHeadersPath()));

    const auto CONFIGURE_CMD = nixDevelopIfNeeded(std::format("make -C '{}' PREFIX='{}' cmake", WORKINGDIR, DataState::getHeadersPath()), HLVER);

    if (!CONFIGURE_CMD) {
        std::println(stderr, "\n{}", failureString("Could not configure hyprland: {}", CONFIGURE_CMD.error()));
'''
CMAKE_PREAMBLE = """cmake_minimum_required(VERSION 3.30)
project(header_configuration NONE)
find_package(PkgConfig REQUIRED)
"""
MAKEFILE_FIXTURE = """PREFIX ?= /usr/local
BUILDDIR ?= ./build
CMAKE_BUILD_TYPE := Release
CMAKE_ARGS = -DCMAKE_BUILD_TYPE:STRING=$(CMAKE_BUILD_TYPE) -DCMAKE_INSTALL_PREFIX:STRING=$(PREFIX)
CMAKE_BUILDTYPE_FILE = $(BUILDDIR)/cmake_last_build_type
CMAKE_GENERATE_CMD = cmake -Wno-unused-cli $(CMAKE_ARGS) -S . -B $(BUILDDIR)

.PHONY: cmake
cmake $(CMAKE_BUILDTYPE_FILE):
\tmkdir -p "$(BUILDDIR)"
\tif [ ! -f "$(CMAKE_BUILDTYPE_FILE)" ]; then $(CMAKE_GENERATE_CMD); fi
\techo "$(CMAKE_BUILD_TYPE)+$(CMAKE_ARGS)" > "$(CMAKE_BUILDTYPE_FILE)"
"""


@unittest.skipUnless(all(shutil.which(tool) for tool in TOOLS), "requires CMake/C++ tools")
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

    def command(self, working_directory):
        fixture = self.directory / "hyprland-git-fixture"
        source = fixture / "hyprpm/src/core/PluginManager.cpp"
        source.parent.mkdir(parents=True)
        source.write_text(UPSTREAM_CONFIGURE)
        subprocess.run(
            ["patch", "--batch", "--fuzz=0", "-p1"],
            input=(PACKAGE / "lua54-hyprpm.patch").read_text(),
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
        working = self.directory / "hyprland-git"
        working.mkdir()
        cmake = working / "CMakeLists.txt"
        cmake.write_text(
            CMAKE_PREAMBLE
            + "pkg_search_module(LUA REQUIRED IMPORTED_TARGET GLOBAL lua>=5.5 lua5.5 lua-5.5)\n"
        )
        (working / "Makefile").write_text(MAKEFILE_FIXTURE)
        original = subprocess.run(
            ["cmake", "-S", str(working), "-B", str(working / "original-build")],
            env=self.env, capture_output=True, text=True,
        )
        self.assertNotEqual(original.returncode, 0)
        command = self.command(working)
        subprocess.run(
            ["/bin/sh", "-eu", "-c", command],
            env=self.env, capture_output=True, text=True, check=True,
        )
        cache = (working / "build/CMakeCache.txt").read_text()
        self.assertIn("LUA_VERSION:INTERNAL=5.4.8\n", cache)
        self.assertIn(f"CMAKE_INSTALL_PREFIX:STRING={self.directory / 'headers'}\n", cache)
        self.assertIn("CMAKE_BUILD_TYPE:STRING=Release\n", cache)

    def test_upstream_drift_stops_before_cmake(self):
        working = self.directory / "drift"
        working.mkdir()
        (working / "CMakeLists.txt").write_text(CMAKE_PREAMBLE)
        (working / "Makefile").write_text(MAKEFILE_FIXTURE)
        result = subprocess.run(
            ["/bin/sh", "-eu", "-c", self.command(working)],
            env=self.env, capture_output=True, text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((working / "build").exists())


if __name__ == "__main__":
    unittest.main()
