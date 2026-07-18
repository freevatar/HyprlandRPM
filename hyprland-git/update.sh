#!/usr/bin/bash
set -Eeuo pipefail

GIT_SPEC="${GIT_SPEC:-hyprland-git.spec}"
REL_SPEC="${REL_SPEC:-../hyprland/hyprland.spec}"

GITHUB_API="https://api.github.com"
GITHUB_REPOSITORY="hyprwm/Hyprland"
COPR_BASE_URL="https://copr.fedorainfracloud.org/webhooks/custom"

new_release=0

log() {
    printf '==> %s\n' "$*" >&2
}

die() {
    printf 'error: %s\n' "$*" >&2
    exit 1
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"
}

get_spec_global() {
    local spec_file=$1
    local macro_name=$2

    awk -v macro_name="${macro_name}" '
        $1 == "%global" && $2 == macro_name {
            $1 = ""
            $2 = ""
            sub(/^[[:space:]]+/, "")
            print
            exit
        }
    ' "${spec_file}"
}

update_spec_globals() {
    local spec_file=$1
    shift

    python3 - "${spec_file}" "$@" <<'PY'
from __future__ import annotations

import pathlib
import re
import sys

spec_path = pathlib.Path(sys.argv[1])
updates: dict[str, str] = {}

for argument in sys.argv[2:]:
    name, separator, value = argument.partition("=")
    if not separator or not name:
        raise SystemExit(f"invalid global assignment: {argument!r}")
    updates[name] = value

text = spec_path.read_text(encoding="utf-8")

for name, value in updates.items():
    pattern = re.compile(
        rf"^(%global[ \t]+{re.escape(name)})[ \t]+.*$",
        flags=re.MULTILINE,
    )
    text, replacements = pattern.subn(rf"\g<1> {value}", text, count=1)
    if replacements != 1:
        raise SystemExit(
            f"expected exactly one %global {name} definition in {spec_path}, "
            f"found {replacements}"
        )

spec_path.write_text(text, encoding="utf-8")
PY
}

# Escape text that will be expanded by RPM inside a shell single-quoted value.
escape_commit_message() {
    python3 - "$1" <<'PY'
import sys

value = sys.argv[1]
value = value.replace("%", "%%")
value = value.replace("'", "'\\''")
print(value, end="")
PY
}

github_api() {
    local -a options=(
        --fail-with-body
        --location
        --silent
        --show-error
        --connect-timeout 10
        --retry 7
        --retry-connrefused
        --header "Accept: application/vnd.github+json"
        --header "X-GitHub-Api-Version: 2022-11-28"
    )

    if [[ -n "${GITHUB_TOKEN:-}" ]]; then
        options+=(--header "Authorization: Bearer ${GITHUB_TOKEN}")
    fi

    curl "${options[@]}" "$@"
}

get_commit_count() {
    local commit=$1
    local headers body link last_page

    headers=$(mktemp)
    body=$(mktemp)

    if ! github_api \
        --dump-header "${headers}" \
        --output "${body}" \
        "${GITHUB_API}/repos/${GITHUB_REPOSITORY}/commits?sha=${commit}&per_page=1"; then
        rm -f "${headers}" "${body}"
        return 1
    fi

    link=$(tr -d '\r' < "${headers}" | sed -n '/^[Ll]ink:/p' | tail -n 1)
    last_page=$(sed -nE 's/.*[?&]page=([0-9]+)>; rel="last".*/\1/p' <<< "${link}")

    if [[ -n "${last_page}" ]]; then
        printf '%s\n' "${last_page}"
    else
        jq -er 'length' "${body}"
    fi

    rm -f "${headers}" "${body}"
}

trigger_copr_build() {
    local package=$1
    local build_id

    build_id=$(curl \
        --fail-with-body \
        --silent \
        --show-error \
        --connect-timeout 10 \
        --retry 7 \
        --retry-connrefused \
        --request POST \
        "${COPR_BASE_URL}/${COPR_WEBHOOK_ID}/${COPR_WEBHOOK_TOKEN}/${package}")

    [[ "${build_id}" =~ ^[0-9]+$ ]] || die \
        "unexpected COPR webhook response for ${package}: ${build_id}"

    copr watch-build "${build_id}"
}

for command in awk copr curl date git jq python3 rpmdev-vercmp rpmspec sed; do
    require_command "${command}"
done

[[ -f "${GIT_SPEC}" ]] || die "spec file not found: ${GIT_SPEC}"

# Refuse to mix automated changes with existing edits to either managed spec.
managed_specs=("${GIT_SPEC}")
if [[ -f "${REL_SPEC}" ]]; then
    managed_specs+=("${REL_SPEC}")
fi

git diff --quiet -- "${managed_specs[@]}" || die \
    "managed spec files have uncommitted changes"
git diff --cached --quiet -- "${managed_specs[@]}" || die \
    "managed spec files have staged changes"

old_tag=$(get_spec_global "${GIT_SPEC}" upstream_version)
[[ -n "${old_tag}" ]] || die "unable to read upstream_version from ${GIT_SPEC}"

log "Fetching upstream release and main-branch metadata"

release_json=$(github_api \
    "${GITHUB_API}/repos/${GITHUB_REPOSITORY}/releases/latest")
new_tag=$(jq -er '.tag_name | sub("^v"; "")' <<< "${release_json}")

commit_json=$(github_api \
    "${GITHUB_API}/repos/${GITHUB_REPOSITORY}/commits/main")
new_hyprland_commit=$(jq -er '.sha' <<< "${commit_json}")
new_hyprland_commit_iso_date=$(jq -er '.commit.committer.date' <<< "${commit_json}")
new_hyprland_commit_message=$(jq -er \
    '.commit.message | split("\n")[0] | gsub("[[:cntrl:]]"; " ")' \
    <<< "${commit_json}")
new_hyprland_commit_message=$(escape_commit_message \
    "${new_hyprland_commit_message}")

[[ "${new_hyprland_commit}" =~ ^[0-9a-f]{40}$ ]] || die \
    "invalid Hyprland commit returned by GitHub: ${new_hyprland_commit}"

new_hyprland_commits=$(get_commit_count "${new_hyprland_commit}")
[[ "${new_hyprland_commits}" =~ ^[0-9]+$ ]] || die \
    "invalid Hyprland commit count: ${new_hyprland_commits}"

new_hyprland_commit_date=$(LC_ALL=C TZ=America/New_York \
    date -d "${new_hyprland_commit_iso_date}" '+%a %b %d %T %Y')

new_protocols_commit=$(github_api \
    "${GITHUB_API}/repos/${GITHUB_REPOSITORY}/contents/subprojects/hyprland-protocols?ref=${new_hyprland_commit}" \
    | jq -er '.sha')
new_udis86_commit=$(github_api \
    "${GITHUB_API}/repos/${GITHUB_REPOSITORY}/contents/subprojects/udis86?ref=${new_hyprland_commit}" \
    | jq -er '.sha')

for commit in "${new_protocols_commit}" "${new_udis86_commit}"; do
    [[ "${commit}" =~ ^[0-9a-f]{40}$ ]] || die \
        "invalid submodule commit returned by GitHub: ${commit}"
done

update_spec_globals "${GIT_SPEC}" \
    "hyprland_commit=${new_hyprland_commit}" \
    "hyprland_commits=${new_hyprland_commits}" \
    "hyprland_commit_date=${new_hyprland_commit_date}" \
    "hyprland_commit_message=${new_hyprland_commit_message}" \
    "protocols_commit=${new_protocols_commit}" \
    "udis86_commit=${new_udis86_commit}"

comparison=0
rpmdev-vercmp "${old_tag}" "${new_tag}" || comparison=$?

case "${comparison}" in
    0)
        ;;
    12)
        log "Detected new upstream release: ${old_tag} -> ${new_tag}"
        update_spec_globals "${GIT_SPEC}" \
            "upstream_version=${new_tag}" \
            "snapshot=0"

        if [[ -f "${REL_SPEC}" ]]; then
            update_spec_globals "${REL_SPEC}" \
                "upstream_version=${new_tag}"
        fi

        new_release=1
        ;;
    11)
        die "configured version ${old_tag} is newer than latest release ${new_tag}"
        ;;
    *)
        die "rpmdev-vercmp failed with status ${comparison}"
        ;;
esac

if git diff --quiet -- "${managed_specs[@]}"; then
    log "Already up to date"
    exit 0
fi

# Every changed main-branch snapshot gets a monotonically increasing snapshot
# number. A new release resets the value to zero before this increment.
snapshot=$(get_spec_global "${GIT_SPEC}" snapshot)
[[ "${snapshot}" =~ ^[0-9]+$ ]] || die "invalid snapshot value: ${snapshot}"
update_spec_globals "${GIT_SPEC}" "snapshot=$((snapshot + 1))"

log "Validating updated spec files"
git diff --check -- "${managed_specs[@]}"
rpmspec -P "${GIT_SPEC}" >/dev/null
if [[ -f "${REL_SPEC}" ]]; then
    rpmspec -P "${REL_SPEC}" >/dev/null
fi

if [[ "${DRY_RUN:-0}" == 1 ]]; then
    log "DRY_RUN=1; leaving changes uncommitted"
    git diff -- "${managed_specs[@]}"
    exit 0
fi

: "${COPR_WEBHOOK_ID:?COPR_WEBHOOK_ID must be set}"
: "${COPR_WEBHOOK_TOKEN:?COPR_WEBHOOK_TOKEN must be set}"

log "Committing and pushing the update"
git add -- "${managed_specs[@]}"
git commit -m \
    "up rev hyprland-git-${new_tag}+${new_hyprland_commit:0:7}"
git push

log "Starting the hyprland-git COPR build"
trigger_copr_build hyprland-git

if [[ "${new_release}" == 1 ]]; then
    log "Starting the release Hyprland COPR build"
    trigger_copr_build hyprland
fi
