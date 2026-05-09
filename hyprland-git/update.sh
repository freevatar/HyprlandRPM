#!/usr/bin/bash
set -euxo pipefail

ec=0
newRelease=0
curl_opts=(--connect-timeout 10 --retry 7 --retry-connrefused -Ss -X POST)

GIT_SPEC="hyprland-git.spec"
REL_SPEC="../hyprland/hyprland.spec"

oldTag="$(rpmspec -q --qf "%{version}\n" "${GIT_SPEC}" | head -1 | sed 's/\^.*//')"
newTag="$(curl "https://api.github.com/repos/hyprwm/Hyprland/tags" | jq -r '.[0].name' | sed 's/^v//')"

newHyprlandCommit="$(curl -s -H "Accept: application/vnd.github.sha" "https://api.github.com/repos/hyprwm/Hyprland/commits/main")"
newHyprlandCommits="$(curl -I \
    "https://api.github.com/repos/hyprwm/Hyprland/commits?per_page=1&sha=${newHyprlandCommit}" | \
    sed -n '/^[Ll]ink:/ s/.*"next".*page=\([0-9]*\).*"last".*/\1/p')"
newHyprlandCommitDate="$(TZ=America/New_York date -d "$(curl -s "https://api.github.com/repos/hyprwm/Hyprland/commits?per_page=1&ref=${newHyprlandCommit}" | \
    jq -r '.[].commit.committer.date')" +"%a %b %d %T %Y")"
newProtocolsCommit="$(curl -L \
    -H "Accept: application/vnd.github+json" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    "https://api.github.com/repos/hyprwm/Hyprland/contents/subprojects/hyprland-protocols?ref=${newHyprlandCommit}" | jq -re '.sha')"
newUdis86Commit="$(curl -L \
    -H "Accept: application/vnd.github+json" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    "https://api.github.com/repos/hyprwm/Hyprland/contents/subprojects/udis86?ref=${newHyprlandCommit}" | jq -re '.sha')"

sed -i \
  -e "s/^\(%global[[:space:]]\+hyprland_commit\)[[:space:]]\+.*/\1 ${newHyprlandCommit}/" \
  -e "s/^\(%global[[:space:]]\+hyprland_commits\)[[:space:]]\+.*/\1 ${newHyprlandCommits}/" \
  -e "s/^\(%global[[:space:]]\+hyprland_commit_date\)[[:space:]]\+.*/\1 ${newHyprlandCommitDate}/" \
  -e "s/^\(%global[[:space:]]\+protocols_commit\)[[:space:]]\+.*/\1 ${newProtocolsCommit}/" \
  -e "s/^\(%global[[:space:]]\+udis86_commit\)[[:space:]]\+.*/\1 ${newUdis86Commit}/" \
  "${GIT_SPEC}"

# Detect new upstream release tag
rpmdev-vercmp "${oldTag}" "${newTag}" || ec=$?
case $ec in
    0)
        ;;
    12) # New upstream release
        sed -i \
          -e "s/^\(%global[[:space:]]\+upstream_version\)[[:space:]]\+.*/\1 ${newTag}/" \
          -e "s/^\(%global[[:space:]]\+snapshot\)[[:space:]]\+.*/\1 0/" \
          "${GIT_SPEC}"

        if [[ -f "${REL_SPEC}" ]]; then
            sed -i \
              -e "s/^\(%global[[:space:]]\+upstream_version\)[[:space:]]\+.*/\1 ${newTag}/" \
              "${REL_SPEC}"
            git add "${REL_SPEC}"
        fi

        newRelease=1
        ;;
    *)
        exit 1
        ;;
esac

if ! git diff --quiet; then
    # Increment snapshot for git builds
    perl -0pi -e 's/^(%global\s+snapshot\s+)(\d+)$/$1.($2+1)/mge' "${GIT_SPEC}"

    git add "${GIT_SPEC}"
    git commit -m "up rev hyprland-git-${newTag}+${newHyprlandCommit:0:7}"
    git push

    hyprlandGitBuildId=$(curl "${curl_opts[@]}" "https://copr.fedorainfracloud.org/webhooks/custom/${COPR_WEBHOOK_ID}/${COPR_WEBHOOK_TOKEN}/hyprland-git")
    copr watch-build "${hyprlandGitBuildId}"

    if [[ "${newRelease}" == "1" ]]; then
        hyprlandBuildId=$(curl "${curl_opts[@]}" "https://copr.fedorainfracloud.org/webhooks/custom/${COPR_WEBHOOK_ID}/${COPR_WEBHOOK_TOKEN}/hyprland")
        copr watch-build "${hyprlandBuildId}"
    fi
fi
