#!/usr/bin/env bash
# Regenerate updates when master advances; never merge a generated build plan.
set -euo pipefail

root=$(git rev-parse --show-toplevel)
temporary=$(mktemp -d "${TMPDIR:-/tmp}/hyprland-update.XXXXXXXX")
worktree="$temporary/checkout"

remove_worktree() {
    if [[ -d "$worktree" ]]; then
        git -C "$root" worktree remove --force "$worktree"
    fi
}

cleanup() {
    remove_worktree || true
    rm -rf -- "$temporary"
}
trap cleanup EXIT

remote_head() {
    git -C "$root" fetch --no-tags origin refs/heads/master
    git -C "$root" rev-parse FETCH_HEAD
}

published() {
    printf 'commit=%s\n' "$1" >> "${GITHUB_OUTPUT:-/dev/stdout}"
}

for attempt in 1 2 3; do
    base=$(remote_head)
    git -C "$root" worktree add --detach "$worktree" "$base"
    (
        cd "$worktree"
        python3 -B scripts/rebuild.py check
        python3 -B hyprland-git/update.py
        python3 -B scripts/rebuild.py apply --base "$base"
        bash scripts/check.sh "$base"
        git diff --check

        mapfile -d '' -t changed_specs < <(git diff --name-only -z -- '*.spec')
        if ((${#changed_specs[@]})); then
            git add -- "${changed_specs[@]}"
        fi
        git add -- build-plan.json
        if ! git diff --cached --quiet; then
            git commit -m "Update package revisions"
        fi
    )
    candidate=$(git -C "$worktree" rev-parse HEAD)

    if [[ "$candidate" != "$base" ]]; then
        if git -C "$worktree" push origin HEAD:refs/heads/master; then
            published "$candidate"
            exit 0
        fi
    fi

    latest=$(remote_head)
    if [[ "$latest" == "$candidate" ]]; then
        # Covers a no-op and a push accepted despite a lost response.
        published "$candidate"
        exit 0
    fi
    if [[ "$latest" == "$base" ]]; then
        echo "Push failed while master was unchanged; check repository access." >&2
        exit 1
    fi

    echo "master advanced; preparing the update again ($attempt/3)." >&2
    remove_worktree
done

echo "master changed during all three attempts; rerun the updater." >&2
exit 1
