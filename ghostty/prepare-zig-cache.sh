#!/usr/bin/env bash
# Prepare the source RPM using the release and compiler pinned in the spec.
set -euo pipefail
umask 022

spec=$(realpath "$1")
outdir=$(realpath "$2")
work=$(mktemp -d "$outdir/.ghostty-XXXXXXXX")
trap 'rm -rf -- "$work"' EXIT
cd "$work"

rpmspec -P "$spec" | awk '/^Source[01]:/ {print $2}' | while IFS= read -r url; do
    curl --fail --silent --show-error --location --retry 3 --remote-name "$url"
done
sha256sum --check --strict "$(dirname "$spec")/sources.sha256"

tar -xf ghostty-*.tar.gz
tar -xf zig-*.tar.xz
cd ghostty-*/
ZIG_GLOBAL_CACHE_DIR="$work/cache" "$work"/zig-*/zig build --fetch=all

tar --sort=name --mtime=@0 --owner=0 --group=0 --numeric-owner \
    -caf "$work/$(basename "$PWD")-deps.tar.zst" zig-pkg
mv -- "$work"/*.tar.* "$outdir/"
