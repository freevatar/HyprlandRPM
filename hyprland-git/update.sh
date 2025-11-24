#!/usr/bin/bash
set -euxo pipefail

ec=0
newRelease=0
curl_opts=(--connect-timeout 10 --retry 7 --retry-connrefused -Ss -X POST)

GIT_SPEC="hyprland-git.spec"
REL_SPEC="../hyprland/hyprland.spec"

oldTag="$(rpmspec -q --qf "%{version}\n" hyprland-git.spec | head -1 | sed 's/\^.*//')"
newTag="$(curl "https://api.github.com/repos/hyprwm/Hyprland/tags" | jq -r '.[0].name' | sed 's/^v//')"

oldHyprlandCommit="$(sed -n 's/.*hyprland_commit \(.*\)/\1/p' hyprland-git.spec)"
newHyprlandCommit="$(curl -s -H "Accept: application/vnd.github.sha" "https://api.github.com/repos/hyprwm/Hyprland/commits/main")"

oldCommitsCount="$(sed -n 's/.*commits_count \(.*\)/\1/p' hyprland-git.spec)"
newCommitsCount="$(curl -I \
                "https://api.github.com/repos/hyprwm/Hyprland/commits?per_page=1&sha=${newHyprlandCommit}" | \
                sed -n '/^[Ll]ink:/ s/.*"next".*page=\([0-9]*\).*"last".*/\1/p')"

oldCommitDate="$(sed -n 's/.*commit_date \(.*\)/\1/p' hyprland-git.spec)"
newCommitDate="$(TZ=America/New_York date -d "$(curl -s "https://api.github.com/repos/hyprwm/Hyprland/commits?per_page=1&ref=${newHyprlandCommit}" | \
                jq -r '.[].commit.committer.date')" +"%a %b %d %T %Y")"

oldProtocolsCommit="$(sed -n 's/.*protocols_commit \(.*\)/\1/p' hyprland-git.spec)"
newProtocolsCommit="$(curl -L \
            -H "Accept: application/vnd.github+json" \
            -H "X-GitHub-Api-Version: 2022-11-28" \
            "https://api.github.com/repos/hyprwm/Hyprland/contents/subprojects/hyprland-protocols?ref=${newHyprlandCommit}" | jq -r '.sha')"

oldUdis86Commit="$(sed -n 's/.*udis86_commit \(.*\)/\1/p' hyprland-git.spec)"
newUdis86Commit="$(curl -L \
            -H "Accept: application/vnd.github+json" \
            -H "X-GitHub-Api-Version: 2022-11-28" \
            "https://api.github.com/repos/hyprwm/Hyprland/contents/subprojects/udis86?ref=${newHyprlandCommit}" | jq -r '.sha')"

# Update GIT_SPEC
sed -e "s/${oldHyprlandCommit}/${newHyprlandCommit}/" \
    -e "/^%global commits_count/s/${oldCommitsCount}/${newCommitsCount}/" \
    -e "s/${oldCommitDate}/${newCommitDate}/" \
    -e "s/${oldProtocolsCommit}/${newProtocolsCommit}/" \
    -e "s/${oldUdis86Commit}/${newUdis86Commit}/" \
    -i "${GIT_SPEC}"

# Detect new upstream release tag
rpmdev-vercmp "${oldTag}" "${newTag}" || ec=$?
case $ec in
    0) ;;
    12)
        # New upstream release: reset bumpver and bump Version in git spec
        perl -pe 's/(?<=bumpver\s)(\d+)/0/' -i "${GIT_SPEC}"
        sed -i "/^Version:/s/${oldTag}/${newTag}/" "${GIT_SPEC}"
        newRelease=1 ;;
    *) exit 1 ;;
esac

# If a new upstream release happened, also bump the release spec Version
if [[ "${newRelease}" == "1" && -f "${REL_SPEC}" ]]; then
    oldRelTag="$(rpmspec -q --qf "%{version}\n" "${REL_SPEC}" | head -1)"
    if [[ "${oldRelTag}" != "${newTag}" ]]; then
        sed -i "/^Version:/s/${oldRelTag}/${newTag}/" "${REL_SPEC}"
    fi
    git add "${REL_SPEC}"
fi

# Commit only if version changed
if ! git diff --quiet; then
    # Increment bumpver for git/nightly builds
    perl -pe 's/(?<=bumpver\s)(\d+)/$1 + 1/ge' -i "${GIT_SPEC}"

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
