"""Exercise Bash completion with help output and no running compositor."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
COMPLETION = ROOT / "hyprland-git/hyprctl.bash"
HELP = """usage: hyprctl [flags] <command> [args...|--help]

commands:
    activewindow        → Gets the active window
    dispatch <dispatcher> [args] → Issue a dispatch
    eval <code>         → Execute Lua
    keyword <name> <value> → Outdated command in upstream help
    layouts             → Outdated command in upstream help
    monitors            → Lists active outputs,
                          wrapped description is not a command
    plugin ...          → Issue a plugin request
    repl [code]         → Enter Lua REPL
    setprop ...         → Outdated command in upstream help

flags:
    -j                  → Output in JSON
    -r                  → Refresh state
    --batch             → Execute a batch
    --instance (-i)     → Use a specific instance
    --quiet (-q)        → Disable output

--help:
    Can be used to print command arguments
"""
REQUESTS = {
    "plugin": "load <path>\nunload <path>\nlist",
    "hyprpaper": "wallpaper",
    "hyprsunset": "temperature <temp>\nidentity\ngamma <gamma>",
}


@unittest.skipUnless(shutil.which("bash") and shutil.which("awk"), "requires Bash and awk")
class HyprctlCompletionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="hyprctl completion tests-")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        binary = self.directory / "bin"
        binary.mkdir()
        self.help = self.directory / "help"
        self.help.write_text(HELP)
        for command, requests in REQUESTS.items():
            (self.directory / command).write_text(
                f"usage: hyprctl {command} <request>\n\nrequests:\n"
                + "".join(f"    {request}    → Description\n" for request in requests.splitlines())
                + "\nflags:\n    See 'hyprctl --help'\n"
            )
        self.log = self.directory / "calls"
        self.executable = binary / "hyprctl"
        self.executable.write_text("""#!/bin/bash
printf '%s\\n' "$*" >> "$HYPRCTL_FIXTURE/calls"
case "$*" in
    --help) cat "$HYPRCTL_FIXTURE/help" ;;
    'plugin --help'|'hyprpaper --help'|'hyprsunset --help')
        cat "$HYPRCTL_FIXTURE/$1" ;;
    *) echo 'completion tried to execute a command' >&2; exit 71 ;;
esac
# Match hyprctl, which exits with status 1 after printing help.
exit 1
""")
        self.executable.chmod(0o755)
        self.env = {
            **os.environ,
            "PATH": f"{binary}:{os.environ['PATH']}",
            "HYPRCTL_FIXTURE": str(self.directory),
        }
        self.env.pop("HYPRLAND_INSTANCE_SIGNATURE", None)

    def complete(self, *words):
        result = subprocess.run(
            ["bash", "--noprofile", "--norc", "-c", """
set -euo pipefail
source "$1"
shift
COMP_WORDS=("$@")
COMP_CWORD=$((${#COMP_WORDS[@]} - 1))
COMPREPLY=(stale-result)
_hyprctl
if ((${#COMPREPLY[@]})); then
    printf '%s\\0' "${COMPREPLY[@]}"
fi
""", "completion-test", str(COMPLETION), *words],
            cwd=self.directory, env=self.env, capture_output=True, text=True, timeout=5,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        if self.log.exists():
            self.assertTrue(all(call in ("--help", "plugin --help", "hyprpaper --help", "hyprsunset --help")
                                for call in self.log.read_text().splitlines()))
        return result.stdout.rstrip("\0").split("\0") if result.stdout else []

    def test_commands_follow_help_and_ignore_descriptions(self):
        self.assertEqual(self.complete("hyprctl", ""),
                         ["activewindow", "dispatch", "eval", "monitors", "plugin", "repl",
                          "deprecated-config", "descriptions", "locked", "reloadshaders", "submap"])
        self.help.write_text(HELP.replace("    activewindow", "    future-command"))
        self.assertEqual(self.complete("hyprctl", "future"), ["future-command"])
        self.assertEqual(self.complete("hyprctl", "active"), [])

    def test_source_corrections_do_not_duplicate_updated_help(self):
        self.help.write_text(HELP.replace("    activewindow", "    descriptions"))
        self.assertEqual(self.complete("hyprctl", "desc"), ["descriptions"])

    def test_flags_include_short_aliases_and_help(self):
        self.assertEqual(set(self.complete("hyprctl", "-")),
                         {"-j", "-r", "-a", "-c", "--config", "--batch", "--instance", "-i",
                          "--quiet", "-q", "--help"})
        self.assertEqual(self.complete("hyprctl", "--in"), ["--instance"])
        self.assertEqual(self.complete("hyprctl", "rollinglog", "--f"), ["--follow"])
        self.assertEqual(self.complete("hyprctl", "--follow", ""), ["rollinglog"])
        self.assertEqual(self.complete("hyprctl", "-f", "mon"), [])

    def test_flags_and_instance_values_do_not_consume_the_command(self):
        for words in (("-j", "-i", "0", "mon"), ("--instance", "session_signature", "mon"),
                      ("--", "mon")):
            with self.subTest(words=words):
                self.assertEqual(self.complete("hyprctl", *words), ["monitors"])
        self.assertEqual(self.complete("hyprctl", "plugin", "-j", "--instance", "0", "lo"), ["load"])
        self.assertEqual(self.complete("hyprctl", "-i", ""), [])
        self.assertEqual(self.complete("hyprctl", "--instance", "--q"), [])
        self.assertEqual(self.complete("hyprctl", "--", "-"), [])

    def test_subcommands_follow_their_own_help(self):
        for command, prefix, expected in (("plugin", "l", ["load", "list"]),
                                          ("hyprpaper", "w", ["wallpaper"]),
                                          ("hyprpaper", "l", ["listactive"]),
                                          ("hyprsunset", "g", ["gamma"])):
            with self.subTest(command=command):
                self.assertEqual(self.complete("hyprctl", command, prefix), expected)

    def test_common_arguments(self):
        for words, expected in ((("monitors", "a"), ["all"]),
                                (("reload", "f"), ["full-reset"]),
                                (("reload", "c"), []),
                                (("output", "c"), ["create"]),
                                (("output", "d"), ["destroy"]),
                                (("output", "a"), ["add"]),
                                (("output", "create", "h"), ["headless"]),
                                (("output", "create", "x"), []),
                                (("output", "add", "w"), ["wayland"]),
                                (("switchxkblayout", "a"), ["active", "all"]),
                                (("switchxkblayout", "keyboard", "n"), ["next"]),
                                (("notify", "0"), ["0"]),
                                (("notify", "-1"), ["-1"])):
            with self.subTest(words=words):
                self.assertEqual(self.complete("hyprctl", *words), expected)

    def test_plugin_paths_keep_spaces(self):
        plugin = self.directory / "plugin with spaces.so"
        plugin.touch()
        for action in ("load", "unload"):
            with self.subTest(action=action):
                self.assertEqual(self.complete("hyprctl", "plugin", action, str(self.directory / "plugin ")),
                                 [str(plugin)])

    def test_batch_and_lua_arguments_are_not_executed(self):
        for words in (("--batch", "reload; $(touch unexpected)"),
                      ("eval", "os.execute('touch unexpected')", ""),
                      ("dispatch", "hl.dsp.exec('touch unexpected')", ""),
                      ("--help", ""), ("unrecognized", "")):
            with self.subTest(words=words):
                self.assertEqual(self.complete("hyprctl", *words), [])
        self.assertFalse(self.log.exists())
        self.assertFalse((self.directory / "unexpected").exists())

    def test_absolute_executable_path(self):
        self.assertEqual(self.complete(str(self.executable), "rep"), ["repl"])

    def test_missing_binary_is_silent(self):
        self.assertEqual(self.complete(str(self.directory / "absent-hyprctl"), ""), [])


if __name__ == "__main__":
    unittest.main()
