# Package maintenance

Run these commands from the repository root.

## Update everything: short procedure

1. Start with a clean checkout of `master`, then run `git pull --ff-only`.
2. Refresh Hyprland first, while its snapshot spec is still clean:

   ```sh
   python3 hyprland-git/update.py --dry-run
   python3 hyprland-git/update.py
   ```

3. Preview and apply all eligible upstream releases:

   ```sh
   python3 scripts/check-upstream-versions.py --allow-tags --refresh --bump --dry-run
   python3 scripts/check-upstream-versions.py --allow-tags --bump
   ```

   Leave `HELD` packages alone until their consumers support the new version.
   Review the release changes and `git diff`: remove backports already included
   upstream, verify remaining patches, and update build requirements as needed.
   The checker follows releases and tags; review Aquamarine's post-release Git
   commits separately and update its pinned upstream patches when needed.
   If package dependencies changed, run `python3 scripts/rebuild.py graph --refresh`.

4. Prepare dependent release bumps and validate the whole update:

   ```sh
   python3 scripts/rebuild.py plan
   python3 scripts/rebuild.py apply
   bash scripts/check.sh
   git diff --check
   git diff
   ```

5. Stage the reviewed package files, `build-plan.json`, and any changed
   `package-graph.json`, then commit and push:

   ```sh
   git add -u
   git commit -m "Update upstream packages and dependent rebuilds"
   git push origin master
   ```

   Add any new patches explicitly before committing. The **Build RPMs** workflow
   starts automatically and runs builds in dependency order; you can leave it
   running without keeping a local build command open.

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
results. The Hyprland snapshot spec appears as skipped because its metadata has
a separate updater.

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
Use `--target fedora-45-x86_64` to check Fedora 45's dependency constraints.
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
recorded plan. The default targets are Fedora 44 and Fedora 45 x86_64; repeat
`--target <chroot>` to explicitly choose a different set of Fedora targets.
Explicit targets replace the defaults, so include every target you intend to build.

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

The planner uses your local specs and does not check upstream. Dependency
rebuilds keep the Hyprland commit already pinned in its spec.

To refresh Hyprland snapshot metadata, start with its spec clean:

```sh
python3 hyprland-git/update.py --dry-run
python3 hyprland-git/update.py
```

The first command previews and restores the changes. The second edits the spec;
then run the planner and commit as above. The scheduled workflow does this every
six hours. It validates the generated metadata and rebuild plan, commits the
changes, and passes that exact commit to **Build RPMs** for validation and builds.
New snapshots start at RPM release `1`.
If `master` changes while the updater prepares a commit, it starts over from
the new commit and regenerates the plan, with up to three attempts.

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
resume after a failure. **Build RPMs** runs the full validation suite once on
Fedora 44 and once on Fedora 45, then submits COPR builds only if both pass.
Scheduled updates call this same workflow. COPR build jobs run one at a time.

A failed build blocks its dependents. Other packages continue, and the workflow
fails when they finish. Reruns reuse successful builds and retry failed ones.

For coordinated library updates, enable manual repository publication in COPR
and publish after all affected packages succeed.

### Fedora 45 rollout

Keep Fedora 44 enabled while Fedora 45 is being tested. The default planner and
scheduled updater prepare both `fedora-44-x86_64` and `fedora-45-x86_64`.
Before enabling a new target in COPR, run `python3 scripts/rebuild.py apply`,
validate, and commit and push the resulting `build-plan.json`. Enabling a target
that is absent from the committed plan causes builds to stop before submission.

After the plan is published, enable `fedora-45-x86_64` in the COPR project and
start the **Build RPMs** workflow. It builds every missing package for the new
target in dependency order and reuses successful builds for existing targets.
Adding a target alone does not require package release bumps.

Test installation and an upgrade from Fedora 44 with `hyprland-git`, `hyprpm`
plugin builds, screen sharing, XWayland, and lock/suspend/resume before retiring
Fedora 44. The Hyprland snapshot spec keeps its Lua 5.4 compatibility on Fedora
44 and uses Lua 5.5 on Fedora 45.

## Checks

The tooling needs Fedora RPM tools and macros, Git, `python3-rpm`, and
`rpmdevtools`. The metadata updater needs Python 3.14.6 or newer. The
[build workflow](../.github/workflows/build.yml) lists the macro and test
packages to install.

Run the same checks as CI:

```sh
bash scripts/check.sh
```

Pass a Git commit as an argument to also check changes since that commit.
To run only the tests, use `python3 -B -m unittest discover -s tests -v`.

Tests use temporary Git repositories and simulated COPR responses. They do not
submit builds. The full suite needs Fedora RPM tools and macros, Git, CMake 3.30
or newer, a C++ compiler, `patch`, and `pkg-config`.

**Build RPMs** validates pull requests and pushes to `master`. Pull requests run
checks only; `master` builds after both Fedora validation jobs pass. Manual runs
on other branches also run checks only. Markdown-only changes skip automatic CI.

The checks cover tests, the dependency graph, release bumps, RPM specs, and shell
syntax. The shared check script expands specs for both targets even when run
locally on only one Fedora release. Planning runs on Fedora 44; package
compilation runs in COPR using the project's enabled targets.

The `hyprland-git` RPM also checks Bash completion against its unpacked source
and newly built `hyprctl`, since upstream help contains stale and missing entries.
To run that check locally, point it at the matching source tree and binary:

```sh
python3 hyprland-git/check-hyprctl-completion.py \
  --source-tree /path/to/Hyprland \
  --completion hyprland-git/hyprctl.bash \
  --hyprctl /path/to/hyprctl
```

The check covers built-in commands, flags, and literal arguments. It does not
start a compositor or execute compositor commands. Hyprsunset is a separate
server, so its subcommands are checked against Hyprctl's help text.
