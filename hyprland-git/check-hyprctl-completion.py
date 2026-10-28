#!/usr/bin/env python3
"""Check Bash suggestions against this build's Hyprland source and hyprctl binary."""

import argparse
import os
from pathlib import Path
import re
import subprocess
import sys


def function_body(source: str, name: str) -> str:
    match = re.search(rf"(?m)^\S[^\n]*\b{re.escape(name)}\([^\n]*\) \{{\n", source)
    if match is None:
        raise ValueError(f"cannot find source function {name}; review the completion check")
    end = re.search(r"(?m)^}\s*$", source[match.end():])
    if end is None:
        raise ValueError(f"cannot find the end of source function {name}")
    return source[match.end():match.end() + end.start()]


def matches(pattern: str, source: str) -> set[str]:
    result = set(re.findall(pattern, source))
    if not result:
        raise ValueError(f"source no longer matches {pattern!r}; review the completion check")
    return result


def help_text(header: str, name: str) -> str:
    match = re.search(rf'\b{name} = R"#\((.*?)\)#";', header, re.S)
    if match is None:
        raise ValueError(f"cannot find help string {name}")
    return match[1]


def source_candidates(root: Path) -> dict[tuple[str, ...], set[str]]:
    ipc = (root / "src/ipc/s1/Commands.cpp").read_text()
    cli = (root / "hyprctl/src/main.cpp").read_text()
    header = (root / "hyprctl/src/Strings.hpp").read_text()
    paper = (root / "hyprctl/src/hyprpaper/Hyprpaper.cpp").read_text()

    registry = function_body(ipc, "registerBuiltinCommands")
    commands = matches(r'(?:legacyCommand\(|\.name\s*=\s*)"([a-z][a-z0-9_-]*)"', registry)
    if len(commands) != registry.count("socket.registerCommand("):
        raise ValueError("unrecognized or duplicate command registration; review the completion check")
    # These requests are handled by the client rather than the compositor's IPC.
    commands |= matches(r'fullRequest\.contains\("/(instances|hyprpaper|hyprsunset)"\)', cli)
    flags = matches(r'ARGS\[i\]\s*==\s*"(-[a-z-]+)"', cli) - {"--"}
    output = function_body(ipc, "dispatchOutput")
    keyboard = function_body(ipc, "switchXKBLayoutRequest")
    backends = matches(r'vars\[2\] == "([a-z]+)"', output)
    definitions = (root / "src/SharedDefs.hpp").read_text()
    icon_enum = re.search(r"enum eIcons[^\{]*\{(.*?)\};", definitions, re.S)
    if icon_enum is None:
        raise ValueError("cannot find notification icon values")
    icons = {"-1"}
    value = -1
    for item in icon_enum[1].split(","):
        if not item.strip():
            continue
        icon = re.fullmatch(r"\s*ICON_[A-Z_]+(?:\s*=\s*(\d+))?\s*", item)
        if icon is None:
            raise ValueError("unrecognized notification icon value")
        value = int(icon[1]) if icon[1] is not None else value + 1
        icons.add(str(value))

    return {
        ("",): commands,
        ("-",): flags - {"-f", "--follow"},
        ("rollinglog", "-"): flags,
        ("--follow", ""): {"rollinglog"},
        ("monitors", ""): matches(r'vars\[1\] == "([a-z]+)"', function_body(ipc, "monitorsRequest")),
        ("reload", ""): matches(r'request\.ends_with\("([a-z-]+)"\)', function_body(ipc, "reloadRequest")),
        ("notify", ""): icons,
        ("output", ""): matches(r'MODE == "([a-z]+)"', output),
        ("output", "create", ""): backends,
        ("output", "add", ""): backends,
        ("plugin", ""): matches(r'OPERATION == "([a-z]+)"', function_body(ipc, "dispatchPlugin")),
        ("switchxkblayout", ""): matches(r'KB == "([a-z]+)"', keyboard),
        ("switchxkblayout", "main", ""): matches(r'CMD == "([a-z]+)"', keyboard),
        ("hyprpaper", ""): matches(r'LHS == "([a-z]+)"', function_body(paper, "makeHyprpaperRequest")),
        # hyprsunset's server is a separate project; validate the client's help.
        ("hyprsunset", ""): matches(r'(?m)^    ([a-z]+)\b', help_text(header, "HYPRSUNSET_HELP")),
    }


def complete(completion: Path, executable: Path, words: tuple[str, ...]) -> set[str]:
    env = {key: value for key, value in os.environ.items()
           if key not in ("BASH_ENV", "ENV", "HYPRLAND_INSTANCE_SIGNATURE")}
    result = subprocess.run(
        ["bash", "--noprofile", "--norc", "-c", """
set -euo pipefail
source "$1"
shift
COMP_WORDS=("$@")
COMP_CWORD=$((${#COMP_WORDS[@]} - 1))
_hyprctl
if ((${#COMPREPLY[@]})); then
    printf '%s\\0' "${COMPREPLY[@]}"
fi
""", "completion-check", str(completion), str(executable), *words],
        env=env, capture_output=True, text=True, check=True, timeout=5,
    )
    if result.stderr:
        raise ValueError(f"completion wrote to stderr: {result.stderr.strip()}")
    items = result.stdout.rstrip("\0").split("\0") if result.stdout else []
    if len(items) != len(set(items)):
        raise ValueError(f"duplicate suggestions for {words!r}")
    return set(items)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-tree", required=True, type=Path)
    parser.add_argument("--completion", required=True, type=Path)
    parser.add_argument("--hyprctl", required=True, type=Path)
    args = parser.parse_args()
    try:
        source = args.source_tree.resolve()
        completion = args.completion.resolve()
        executable = args.hyprctl.resolve()
        # Reject accidentally testing a different installed hyprctl version.
        header = (source / "hyprctl/src/Strings.hpp").read_text()
        result = subprocess.run([str(executable), "--help"], capture_output=True, text=True, timeout=5)
        if result.returncode not in (0, 1) or result.stderr or result.stdout.strip() != help_text(header, "USAGE").strip():
            raise ValueError("hyprctl --help does not match the selected source tree")

        expected = source_candidates(source)
        failures = []
        for words, candidates in expected.items():
            actual = complete(completion, executable, words)
            if actual != candidates:
                failures.append(f"hyprctl {' '.join(words)!r}: missing {sorted(candidates - actual)}, "
                                f"unsupported {sorted(actual - candidates)}")
        if failures:
            raise ValueError("\n".join(failures))
        print(f"Validated {len(expected[('',)])} commands, {len(expected[('rollinglog', '-')])} flags, "
              f"and {len(expected)} completion contexts against Hyprland source.")
        return 0
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"hyprctl completion check failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
