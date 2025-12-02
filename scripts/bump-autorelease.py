#!/usr/bin/env python3
import re
import sys
from pathlib import Path
from typing import Match

"""
Match: "Release: %autorelease -bN"
Captures two groups:
    - 'prefix'       : everything up to and including "-b"
    - 'build_number' : numeric build number N
"""
AUTORELEASE_WITH_BUILD_PATTERN = re.compile(
    r"(?P<prefix>^Release:\s*%autorelease\s+-b\s*)(?P<build_number>\d+)",
    re.MULTILINE,
)

"""
Match: "Release: %autorelease"
Captures one group:
    - 'release_line' : full matching line
"""
AUTORELEASE_PLAIN_PATTERN = re.compile(
    r"(?P<release_line>^Release:\s*%autorelease\s*)$",
    re.MULTILINE,
)


def _increment_existing_build_number(match: Match[str], spec_path: Path) -> str:
    """
    Regex callback used when an existing build number "-bN" is present
    Increments build number
    Example:
        "Release: %autorelease -b2" -> "Release: %autorelease -b3"
    """
    prefix = match.group("prefix")
    current_build = int(match.group("build_number"))
    new_build = current_build + 1

    print(f"{spec_path}: -b{current_build} -> -b{new_build}")
    return f"{prefix}{new_build}"


def _add_initial_build_number(match: Match[str], spec_path: Path) -> str:
    """
    Regex callback used when there is a plain "%autorelease" line
        with no "-bN"
    Appends " -b1"
    Example:
        "Release: %autorelease" -> "Release: %autorelease -b1"
    """
    release_line = match.group("release_line")
    print(f"{spec_path}: adding -b1 to %autorelease")
    return f"{release_line} -b1"


def bump_autorelease_in_spec_file(spec_file_path: Path) -> None:
    spec_file_content = spec_file_path.read_text()

    # Try to bump an existing build number "-bN"
    def replace_existing_build(match: Match[str]) -> str:
        return _increment_existing_build_number(match, spec_file_path)

    updated_content, substitutions = AUTORELEASE_WITH_BUILD_PATTERN.subn(
        replace_existing_build,
        spec_file_content,
        # Only touch the first plain release line
        count=1,
    )

    # If there's no "-bN", add "-b1"
    if substitutions == 0:

        def replace_plain_autorelease(match: Match[str]) -> str:
            return _add_initial_build_number(match, spec_file_path)

        updated_content, substitutions = AUTORELEASE_PLAIN_PATTERN.subn(
            replace_plain_autorelease,
            spec_file_content,
            # Only touch the first plain release line
            count=1,
        )

    # No "%autorelease" line was found
    if substitutions == 0:
        print(
            f'{spec_file_path}: no "%autorelease" line found, skipping',
            file=sys.stderr,
        )
        return

    # Write back modified content
    spec_file_path.write_text(updated_content)


def main(argv: list[str]) -> int:
    if not argv:
        program_name = Path(sys.argv[0]).name
        print(
            f"Usage: {program_name} path/to/*.spec ...",
            file=sys.stderr,
        )
        return 1

    for spec_file in argv:
        bump_autorelease_in_spec_file(Path(spec_file))

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
