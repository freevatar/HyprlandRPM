#!/usr/bin/env bash
set -euo pipefail

# Mark all spec files that depend on a given pkgconfig() name for rebuild
#   after an ABI-breaking update.

if (($# < 1)); then
  printf 'Usage: %s <pkgconfig-name> [up]\n' "${0##*/}" >&2
  exit 1
fi

readonly package_name="$1"
readonly do_update="${2:-skip}"

printf '"%s" update changed ABI.\n\n' "${package_name}"

# Find all spec files affected by the ABI change
#   i.e. those referencing pkgconfig(<pkg>)
mapfile -t specs < <(
  grep -rl "pkgconfig(${package_name})" \
       --include='*.spec' .
)

if ((${#specs[@]} == 0)); then
  echo "No matching spec files found" >&2
  exit 0
fi

echo "Marking ABI-dependent specs for rebuild:"
printf '  %s\n' "${specs[@]}"

if [[ "${do_update}" == "up" ]]; then
    ./scripts/bump-autorelease.py "${specs[@]}"
fi
