# Hyprland RPMs

RPM packages for Hyprland and related tools, forked from
[solopasha/hyprlandRPM](https://github.com/solopasha/hyprlandRPM).

## Install

Enable the [COPR repository](https://copr.fedorainfracloud.org/coprs/giperborey/Hyprland/):

```sh
sudo dnf copr enable giperborey/Hyprland
```

Then install the packages you need.

## Update packages

Check for upstream releases before editing versions:

```sh
python3 scripts/check-upstream-versions.py --updates-only
```

The checker reports `UPDATE`, `CURRENT`, `AHEAD`, or `HELD`, with full versions
and release links. `HELD` means a newer version falls outside the local specs'
`BuildRequires` constraints. It checks declared constraints, which cannot
guarantee a successful build. Omit `--updates-only` to see every package. Held
updates and errors stay visible in either view, and the summary includes all
results. Hyprland's stable and snapshot specs appear as skipped because their
metadata has a separate updater.

It uses stable GitHub releases by default. Add `--allow-tags` for repositories
without a stable release; it selects the highest numeric tag, excludes
prereleases, and labels the result `tag`. A rate limit stops further requests
and shows when to retry; other temporary network failures get a few bounded retries.

For authenticated API limits, set `GITHUB_TOKEN` in your environment or put it
in `.secrets` at the repository root:

```dotenv
GITHUB_TOKEN=your_token_here
```

The checker loads this file automatically. An exported `GITHUB_TOKEN` takes
precedence. `.secrets` is ignored by Git; use `chmod 600 .secrets` to restrict
access to your account. Its contents are read as data, without running shell commands.

Successful GitHub lookups are cached for one hour in
`.cache/upstream-versions.json` (ignored by Git). Specs and dependency constraints
are reread on every run. Add `--refresh` to fetch fresh results. Errors are never
cached. Saved results stay on disk until a successful lookup replaces them;
expired results are not used when GitHub is unavailable.

The checker uses RPM to expand macros and compares upstream versions without
RPM release numbers. It reads the same package inventory as the build tools,
excluding ignored build output. It defaults to this repository and Fedora 44;
use `DIR` and `--target <chroot>` to override them, or `--debug` for diagnostics.
Specs are only edited with `--bump`. Exit code `0` means every eligible check
succeeded, even if updates were found or held; `1` means a failed or incomplete
scan or a version bump that could not be applied.

To bump discovered versions, preview the changes, then apply them:

```sh
python3 scripts/check-upstream-versions.py --allow-tags --bump --dry-run
python3 scripts/check-upstream-versions.py --allow-tags --bump
```

Add names after `--bump` to update specific packages, for example
`--bump hyprutils hyprtoolkit`. Only `UPDATE` packages are changed; held packages
and Hyprland's separately managed specs are skipped. Each version bump resets
its release to 1 (`%autorelease` for this repository). The command validates all
edits before applying them and leaves specs untouched if a check or edit fails.
Macro-based or conditional version declarations need a manual edit.

Review the diff and adjust any requirements or patches that need to change.
Then prepare dependent rebuilds and the build plan before committing:

```sh
python3 scripts/rebuild.py plan
python3 scripts/rebuild.py apply
python3 scripts/rebuild.py check
git diff
```

The planner uses the saved dependency graph to find affected packages and order
their builds. `apply` validates the graph, adds any needed release bumps once,
and writes `build-plan.json` to record the base commit and prepared package
inputs. Review and commit it with the spec changes, then push to `master`.
The build workflow checks the plan
and starts COPR builds. The initial plan is included in the repository; keep it
tracked as packages change.

`plan` and `apply` compare your working tree with `HEAD`. Use `--base <commit>` if
you have already committed the version changes. `check` validates the graph and
recorded plan. The default target is Fedora 44 x86_64; repeat
`--target <chroot>` to explicitly choose other Fedora targets.

Dependencies are stored once in `package-graph.json`. View the saved build order with:

```sh
python3 scripts/rebuild.py graph
```

When adding or removing packages or dependencies, refresh the graph before
running `plan` and commit it with the changes:

```sh
python3 scripts/rebuild.py graph --refresh
```

Ordinary version bumps do not change the graph file. `apply` and `check` reject
a stale graph or dependency versions that do not satisfy the specs. CI runs
these checks too. Refresh fails if the targets have different package graphs.
Use `graph --check` to check just the saved graph.

To refresh stable and snapshot Hyprland metadata, start with those specs clean:

```sh
python3 hyprland-git/update.py --dry-run
python3 hyprland-git/update.py
```

The first command previews and restores the changes. The second edits the specs;
then run the planner and commit as above. The scheduled workflow does this every
six hours, commits the changes, and runs the builds directly.

## COPR builds

Set the GitHub repository secrets `COPR_LOGIN` and `COPR_TOKEN` from your
[COPR API credentials](https://copr.fedorainfracloud.org/api/). The workflows use
`giperborey/Hyprland`; repository variables `COPR_OWNER` and `COPR_PROJECT` override
that destination. For local builds, set the same credentials in your environment:

```sh
python3 scripts/rebuild.py build --owner giperborey --project Hyprland
```

Run this from a clean checkout after pushing your prepared changes. Builds check
the committed plan, then use that exact commit and the COPR project's enabled
targets. Rerun the command, or start the **Build RPMs** workflow manually, to
resume after a failure. Scheduled updates and build workflows do not overlap.

For coordinated library updates, enable manual repository publication in COPR
and publish after all affected packages succeed.

## Checks

The tooling needs Fedora RPM tools and macros, Git, `python3-rpm`, and
`rpmdevtools`. The metadata updater needs Python 3.14.6 or newer. The
[validation workflow](.github/workflows/check.yml) lists the macro and test
packages to install.

Run tests from the repository root:

```sh
python3 -B -m unittest discover -s tests -v
```

Tests use temporary Git repositories and simulated COPR responses. They do not
submit builds. The full suite needs Fedora RPM tools and macros, Git, CMake 3.30
or newer, a C++ compiler, `patch`, and `pkg-config`.

CI runs tests and checks the dependency graph, release bumps, RPM specs, and
shell syntax on Fedora 44. It runs when packaging files,
tooling, tests, or workflows change. Documentation-only changes skip CI. Package
compilation runs in COPR.
