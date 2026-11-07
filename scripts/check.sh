#!/usr/bin/env bash
# Run the checks required before publishing package changes or starting builds.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
base=${1:-}

python3 -B -m unittest discover -s tests -v
python3 -B scripts/rebuild.py check

if [[ -n "$base" && ! "$base" =~ ^0+$ ]]; then
    if ! git cat-file -e "$base^{commit}" 2>/dev/null; then
        git fetch --no-tags origin "$base"
    fi
    python3 -B scripts/rebuild.py check --base "$base"
fi

python3 -B - <<'PY'
import runpy
import subprocess
from pathlib import Path

from scripts.package_graph import DEFAULT_TARGETS, rpm_arguments

updater = runpy.run_path("hyprland-git/update.py")
for path in sorted(Path(".").glob("*/*.spec")):
    for target in DEFAULT_TARGETS:
        expanded = subprocess.run(
            rpm_arguments(target) + ["-P", str(path)],
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        ).stdout
        updater["validate_shell_scriptlets"](path, expanded)
        print(f"Validated {path} ({target})", flush=True)
PY
