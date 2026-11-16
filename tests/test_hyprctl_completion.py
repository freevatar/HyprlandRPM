"""Exercise Bash completion with help output and no running compositor."""

import os
from pathlib import Path
import runpy
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
COMPLETION = ROOT / "hyprland-git/hyprctl.bash"
CHECKER = runpy.run_path(str(ROOT / "hyprland-git/check-hyprctl-completion.py"))
HELP = """usage: hyprctl [flags] <command> [args...|--help]

commands:
    activewindow        → Gets the active window
    dispatch <dispatcher> [args] → Issue a dispatch
    eval <code>         → Execute Lua
    hyprpaper ...       → Issue a wallpaper request
    hyprsunset ...      → Issue a color temperature request
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
FLAG_PARSER = '''int main(int argc, char** argv) {
    if (ARGS[i] == "-j" && !fullArgs.contains("j")) {
        fullArgs += "j";
        json = true;
    } else if (ARGS[i] == "-r" && !fullArgs.contains("r")) {
        fullArgs += "r";
    } else if (ARGS[i] == "-a" && !fullArgs.contains("a")) {
        fullArgs += "a";
    } else if ((ARGS[i] == "-c" || ARGS[i] == "--config") && !fullArgs.contains("c")) {
        fullArgs += "c";
    } else if ((ARGS[i] == "-f" || ARGS[i] == "--follow") && !fullArgs.contains("f")) {
        fullArgs += "f";
        needRoll = true;
    } else if (ARGS[i] == "-q" || ARGS[i] == "--quiet") {
        quiet = true;
    }
}
'''
PAPER_HANDLER = '''std::expected<void, std::string> Hyprpaper::makeHyprpaperRequest(const std::string_view& rq) {
    if (!rq.starts_with("/hyprpaper "))
        return std::unexpected("Invalid request");
    return {};
}
'''


class HyprctlSourceTests(unittest.TestCase):
    def test_hyprpaper_restrictions_follow_request_encoding(self):
        rejected = CHECKER["hyprpaper_rejected_flags"](FLAG_PARSER, PAPER_HANDLER)
        self.assertEqual(rejected, {"-j", "-r", "-a", "-c", "--config", "-f", "--follow"})
        renamed = FLAG_PARSER.replace('"--config"', '"--new-config"')
        self.assertEqual(CHECKER["hyprpaper_rejected_flags"](renamed, PAPER_HANDLER),
                         rejected - {"--config"} | {"--new-config"})
        reordered = FLAG_PARSER.replace('fullArgs += "j";\n        json = true;', 'json = true;\n        fullArgs += "j";')
        self.assertEqual(CHECKER["hyprpaper_rejected_flags"](reordered, PAPER_HANDLER), rejected)

    def test_hyprpaper_accepting_prefixed_requests_requires_review(self):
        changed = PAPER_HANDLER.replace('"/hyprpaper "', '"hyprpaper "')
        with self.assertRaisesRegex(ValueError, "hyprpaper request validation changed"):
            CHECKER["hyprpaper_rejected_flags"](FLAG_PARSER, changed)

    def test_unrecognized_request_flag_encoding_requires_review(self):
        changed = FLAG_PARSER.replace('fullArgs += "j";', 'fullArgs += encodeFlag("j");')
        with self.assertRaisesRegex(ValueError, "unrecognized request flag encoding"):
            CHECKER["hyprpaper_rejected_flags"](changed, PAPER_HANDLER)

    def test_client_commands_follow_exact_dispatch(self):
        source = '''int main(int argc, char** argv) {
    if (cmd == "setprop")
        std::println("{}", SETPROP_HELP);

    const auto COMMAND = fullRequest.substr(0, fullRequest.find(' '));
    if (COMMAND == "instances") {
        instancesRequest(json);
        return 0;
    }
    if (needRoll && COMMAND != "rollinglog")
        return 1;
    if (COMMAND == "--batch")
        exitStatus = batchRequest(fullRequest, json);
    else if (COMMAND == "hyprpaper") {
        auto result = Hyprpaper::makeHyprpaperRequest(fullRequest);
        exitStatus = !result;
    } else if (COMMAND == "hyprsunset")
        exitStatus = requestHyprsunset(fullRequest);
    else if (COMMAND == "setprop")
        exitStatus = request(fullRequest, 3);
}
'''
        expected = {"instances", "hyprpaper", "hyprsunset"}
        self.assertEqual(CHECKER["client_commands"](source), expected)
        # Missing handlers must not be filled in from a hard-coded fallback.
        for command in expected:
            with self.subTest(command=command):
                changed = source.replace(f'COMMAND == "{command}"', 'COMMAND == "removed"')
                self.assertEqual(CHECKER["client_commands"](changed), expected - {command})

    def test_unrecognized_client_dispatch_requires_review(self):
        with self.assertRaisesRegex(ValueError, "review the completion check"):
            CHECKER["client_commands"]('''int main(int argc, char** argv) {
    return dispatchCommand(fullRequest);
}
''')


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
        self.assertCountEqual(self.complete("hyprctl", ""),
                              ["activewindow", "dispatch", "eval", "hyprpaper", "hyprsunset", "monitors", "plugin", "repl",
                               "deprecated-config", "descriptions", "locked", "reloadshaders", "submap"])
        self.help.write_text(HELP.replace("    activewindow", "    future-command"))
        self.assertEqual(self.complete("hyprctl", "future"), ["future-command"])
        self.assertEqual(self.complete("hyprctl", "active"), [])

    def test_source_corrections_do_not_duplicate_updated_help(self):
        self.help.write_text(HELP.replace("    activewindow", "    descriptions"))
        self.assertEqual(self.complete("hyprctl", "desc"), ["descriptions"])

    def test_flags_include_short_aliases_and_help(self):
        self.assertCountEqual(self.complete("hyprctl", "-"),
                              ["-j", "-r", "-a", "-c", "--config", "--batch", "--instance", "-i",
                               "--quiet", "-q", "--help"])
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
                self.assertCountEqual(self.complete("hyprctl", command, prefix), expected)

    def test_hyprpaper_does_not_suggest_request_flags(self):
        for args in (("hyprpaper",), ("hyprpaper", "listactive"), ("hyprpaper", "wallpaper", "monitor,path")):
            with self.subTest(args=args):
                self.assertCountEqual(self.complete("hyprctl", *args, "-"),
                                      ["--batch", "--instance", "-i", "--quiet", "-q", "--help"])
                self.assertEqual(self.complete("hyprctl", *args, "--c"), [])

    def test_request_flags_do_not_suggest_hyprpaper(self):
        commands = set(self.complete("hyprctl", ""))
        self.assertIn("hyprpaper", commands)
        for flag in ("-j", "-r", "-a", "-c", "--config"):
            with self.subTest(flag=flag):
                self.assertCountEqual(self.complete("hyprctl", flag, ""), commands - {"hyprpaper"})
                self.assertEqual(self.complete("hyprctl", flag, "hyprp"), [])
                self.assertEqual(self.complete("hyprctl", flag, "--", "hyprp"), [])
                self.assertEqual(self.complete("hyprctl", flag, "hyprs"), ["hyprsunset"])

    def test_hyprpaper_with_request_flags_has_no_further_suggestions(self):
        for flag in ("-j", "-r", "-a", "-c", "--config", "-f", "--follow"):
            for words in ((flag, "hyprpaper"), ("hyprpaper", flag), ("hyprpaper", "listactive", flag)):
                for current in ("", "-"):
                    with self.subTest(words=words, current=current):
                        self.assertEqual(self.complete("hyprctl", *words, current), [])
        self.assertFalse(self.log.exists())

    def test_hyprpaper_keeps_client_options_and_option_terminator(self):
        for prefix in (("-q",), ("--quiet",), ("-i", "0"), ("--instance", "session_signature"), ("--",)):
            with self.subTest(prefix=prefix):
                self.assertEqual(self.complete("hyprctl", *prefix, "hyprp"), ["hyprpaper"])
                self.assertCountEqual(self.complete("hyprctl", *prefix, "hyprpaper", ""), ["wallpaper", "listactive"])
                self.assertCountEqual(self.complete("hyprctl", "hyprpaper", *prefix, ""), ["wallpaper", "listactive"])
        self.assertEqual(self.complete("hyprctl", "hyprpaper", "--", "-"), [])
        # A flag-looking instance value must not be parsed as a request flag.
        self.assertEqual(self.complete("hyprctl", "-i", "--config", "hyprp"), ["hyprpaper"])

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
                self.assertCountEqual(self.complete("hyprctl", *words), expected)

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
