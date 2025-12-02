#!/usr/bin/env bash
set -euo pipefail

# Mark all spec files that depend on a given pkgconfig() name for rebuild
#   after an ABI-breaking update.

if (($# != 1)); then
  printf 'Usage: %s <pkgconfig-name>\n' "${0##*/}" >&2
  exit 1
fi

readonly pkg="$1"

printf '"%s" update changed ABI.\n\n' "${pkg}"

# Find all spec files affected by the ABI change
#   i.e. those referencing pkgconfig(<pkg>)
mapfile -t specs < <(
  grep -rlE "pkgconfig\(${pkg}\)" \
       --include='*.spec' .
)

if ((${#specs[@]} == 0)); then
  echo "No matching spec files found" >&2
  exit 0
fi

echo "Marking ABI-dependent specs for rebuild:"
printf '  %s\n' "${specs[@]}"

./scripts/bump-autorelease.py "${specs[@]}"
