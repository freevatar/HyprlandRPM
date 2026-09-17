"""Upstream-checker CLI behavior with real RPM metadata and no network access."""

from contextlib import chdir, redirect_stderr, redirect_stdout
import importlib.util
import io
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from github_versions import GitHubError, RateLimitError, UpstreamVersion


MODULE_SPEC = importlib.util.spec_from_file_location(
    "check_upstream_versions_tests_module", SCRIPTS / "check-upstream-versions.py"
)
assert MODULE_SPEC and MODULE_SPEC.loader
checker = importlib.util.module_from_spec(MODULE_SPEC)
sys.modules[MODULE_SPEC.name] = checker
MODULE_SPEC.loader.exec_module(checker)

TARGET = "fedora-44-x86_64"


@unittest.skipUnless(shutil.which("rpmspec") and shutil.which("git"), "requires RPM and Git")
class CheckerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="upstream-check-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def spec(self, folder="package", *, name=None, version="1.0", url=None, prefix="", metadata=""):
        name = name or folder
        url = url or f"https://github.com/example/{name}"
        directory = self.root / folder
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{folder}.spec"
        path.write_text(
            prefix
            + f"Name: {name}\nVersion: {version}\nRelease: 1\n"
            + f"Summary: Test package\nLicense: MIT\nURL: {url}\n"
            + metadata
            + "\n%description\nTest package.\n\n%files\n"
        )
        return path

    @staticmethod
    def upstream(version="1.0", *, repo="package", kind="release"):
        tag = f"v{version}"
        url = (f"https://github.com/example/{repo}/releases/tag/{tag}"
               if kind == "release" else f"https://github.com/example/{repo}/tree/{tag}")
        return UpstreamVersion(tag, version, url, kind)

    def run_checker(self, *arguments, response=None, side_effect=None, default_directory=False):
        output, errors = io.StringIO(), io.StringIO()
        argv = list(arguments) if default_directory else [str(self.root), *arguments]
        with patch.object(checker, "latest_version", return_value=response or self.upstream(),
                          side_effect=side_effect) as request:
            with redirect_stdout(output), redirect_stderr(errors):
                code = checker.main(argv)
        return code, output.getvalue(), errors.getvalue(), request

    def assert_summary(self, output, **counts):
        summary = next(line for line in output.splitlines() if line.startswith("Summary:"))
        for status, count in counts.items():
            self.assertRegex(summary, rf"\b{count}\s+{re.escape(status.upper())}\b")

    @staticmethod
    def package_row(output, name):
        return next(line for line in output.splitlines() if re.match(rf"^{re.escape(name)}\s", line))

    def test_rpm_expands_name_version_url_and_target_conditionals(self):
        path = self.spec(
            "source-directory", name="%{project}", version="%{upstream}",
            url="https://github.com/example/%{project}",
            prefix=("%global project actual-package\n"
                    "%if 0%{?fedora} >= 45\n%global upstream 10.0\n"
                    "%else\n%global upstream 9.0\n%endif\n"),
        )
        first = checker.parse_spec(path, TARGET)
        second = checker.parse_spec(path, "fedora-45-x86_64")
        self.assertEqual((first.name, first.version, first.url, first.path),
                         ("actual-package", "9.0", "https://github.com/example/actual-package", path))
        self.assertEqual(second.version, "10.0")

    def test_package_name_comes_from_rpm_metadata_not_directory(self):
        self.spec("source-directory", name="actual-package")
        code, output, _, request = self.run_checker()
        self.assertEqual(code, 0)
        self.assertIn("CURRENT", self.package_row(output, "actual-package"))
        self.assertNotIn("source-directory", output)
        self.assertEqual(request.call_args.args[:2], ("example", "actual-package"))

    def test_main_passes_explicit_target_to_rpm(self):
        self.spec(version="%{fedora}")
        code, output, _, _ = self.run_checker(
            "--target", "fedora-45-x86_64", response=self.upstream("45")
        )
        self.assertEqual(code, 0)
        self.assertIn("CURRENT", self.package_row(output, "package"))

    def test_default_target_is_fedora44(self):
        self.spec(version="%{fedora}")
        code, output, _, _ = self.run_checker(response=self.upstream("44"))
        self.assertEqual(code, 0)
        self.assertIn("CURRENT", self.package_row(output, "package"))

    def test_wrong_hosts_fail_without_github_requests(self):
        for url in (
            "https://gitlab.com/example/package",
            "https://github.com.evil.example/example/package",
            "https://example.com/github.com/example/package",
        ):
            with self.subTest(url=url):
                self.spec(url=url)
                code, output, _, request = self.run_checker()
                self.assertEqual(code, 1)
                request.assert_not_called()
                self.assertIn("ERROR", self.package_row(output, "package"))
                with self.assertRaises(checker.CheckError):
                    checker.extract_repo_info(url)

    def test_github_http_and_https_urls_are_supported(self):
        self.assertEqual(checker.extract_repo_info("https://github.com/example/package.git"),
                         ("example", "package"))
        self.assertEqual(checker.extract_repo_info("http://github.com/example/package"),
                         ("example", "package"))

    def test_ignored_build_copies_are_not_checked(self):
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        (self.root / ".gitignore").write_text("results_*/\n")
        self.spec()
        self.spec("results_fedora44", name="package", version="0.1")
        code, output, _, request = self.run_checker()
        self.assertEqual(code, 0)
        self.assertEqual(request.call_count, 1)
        self.assert_summary(output, current=1, update=0, error=0)

    def test_hyprland_names_are_visibly_skipped(self):
        self.spec("stable-source", name="hyprland")
        self.spec("snapshot-source", name="hyprland-git")
        self.spec()
        code, output, _, request = self.run_checker()
        self.assertEqual(code, 0)
        self.assertEqual(request.call_count, 1)
        self.assertIn("SKIP", self.package_row(output, "hyprland"))
        self.assertIn("SKIP", self.package_row(output, "hyprland-git"))
        self.assert_summary(output, current=1, skip=2)

    def test_missing_version_and_rpm_parse_failure_return_error(self):
        path = self.spec()
        valid = path.read_text()
        for content in (valid.replace("Version: 1.0\n", ""), valid + "\n%if 1\n"):
            with self.subTest(content=content):
                path.write_text(content)
                code, output, _, request = self.run_checker()
                self.assertEqual(code, 1)
                request.assert_not_called()
                self.assert_summary(output, error=1)
                with self.assertRaises(checker.CheckError):
                    checker.parse_spec(path)

    def test_parse_error_does_not_prevent_checking_other_packages(self):
        invalid = self.spec("a-broken")
        invalid.write_text(invalid.read_text().replace("Version: 1.0\n", ""))
        self.spec("b-valid")
        code, output, _, request = self.run_checker()
        self.assertEqual(code, 1)
        self.assertEqual(request.call_count, 1)
        self.assertIn("CURRENT", self.package_row(output, "b-valid"))
        self.assert_summary(output, error=1, current=1)

    def test_all_network_errors_return_nonzero(self):
        self.spec("first")
        self.spec("second")
        code, output, _, request = self.run_checker(side_effect=GitHubError("network unavailable"))
        self.assertEqual(code, 1)
        self.assertEqual(request.call_count, 2)
        self.assert_summary(output, error=2, current=0)

    def test_network_error_does_not_prevent_other_results(self):
        self.spec("first")
        self.spec("second")
        code, output, _, request = self.run_checker(
            side_effect=[GitHubError("network unavailable"), self.upstream()]
        )
        self.assertEqual(code, 1)
        self.assertEqual(request.call_count, 2)
        self.assertIn("CURRENT", self.package_row(output, "second"))
        self.assert_summary(output, error=1, current=1)

    def test_rpm_ordering_distinguishes_update_current_and_ahead(self):
        self.spec("update-package", version="9.0")
        self.spec("current-package", version="10.0")
        self.spec("ahead-package", version="10.0")
        versions = {"update-package": "10.0", "current-package": "10.0", "ahead-package": "9.0"}

        def upstream(owner, repo, **options):
            return self.upstream(versions[repo], repo=repo)

        code, output, _, _ = self.run_checker(side_effect=upstream)
        self.assertEqual(code, 0)
        for name, status in (("update-package", "UPDATE"), ("current-package", "CURRENT"),
                             ("ahead-package", "AHEAD")):
            self.assertIn(status, self.package_row(output, name))
        self.assert_summary(output, update=1, current=1, ahead=1, error=0)

    def test_updates_only_keeps_errors_and_counts_hidden_rows(self):
        self.spec("update-package", version="9.0")
        self.spec("current-package", version="10.0")
        self.spec("ahead-package", version="11.0")
        self.spec("error-package")
        self.spec("managed-source", name="hyprland")

        def upstream(owner, repo, **options):
            if repo == "error-package":
                raise GitHubError("network unavailable")
            return self.upstream("10.0", repo=repo)

        code, output, _, _ = self.run_checker("--updates-only", side_effect=upstream)
        self.assertEqual(code, 1)
        self.assertIn("UPDATE", self.package_row(output, "update-package"))
        self.assertIn("ERROR", self.package_row(output, "error-package"))
        for hidden in ("current-package", "ahead-package", "hyprland"):
            self.assertNotIn(hidden, output)
        self.assert_summary(output, update=1, current=1, ahead=1, error=1, blocked=0, skip=1)

    def constrained_glaze(self, requirements="glaze-static >= 7\nBuildRequires: glaze-static < 8"):
        self.spec("glaze", version="7.4.0",
                  metadata="Provides: glaze-static = %{version}-%{release}\n")
        return self.spec("hyprland", metadata=f"BuildRequires: {requirements}\n")

    def test_incompatible_update_is_held_and_visible_with_updates_only(self):
        self.constrained_glaze()
        code, output, _, request = self.run_checker(
            "--updates-only", response=self.upstream("8.4.0", repo="glaze")
        )
        self.assertEqual(code, 0)
        request.assert_called_once()
        self.assertIn("HELD", self.package_row(output, "glaze"))
        self.assertIn("v8.4.0", self.package_row(output, "glaze"))
        self.assertIn("hyprland", output)
        self.assertRegex(output, r"glaze-static\s*<\s*8")
        self.assertIn("https://github.com/example/glaze/releases/tag/v8.4.0", output)
        self.assert_summary(output, held=1, update=0, error=0, skip=1)

    def test_compatible_glaze_update_remains_available(self):
        self.constrained_glaze()
        code, output, _, _ = self.run_checker(response=self.upstream("7.5.0", repo="glaze"))
        self.assertEqual(code, 0)
        self.assertIn("UPDATE", self.package_row(output, "glaze"))
        self.assert_summary(output, held=0, update=1)

    def test_constraint_matches_provide_from_devel_subpackage(self):
        self.constrained_glaze()
        path = self.root / "glaze" / "glaze.spec"
        path.write_text(path.read_text().replace("Provides: glaze-static = %{version}-%{release}\n", "")
                        .replace("\n%files\n", "\n%package devel\nSummary: Development files\n"
                                 "Provides: glaze-static = %{version}-%{release}\n"
                                 "%description devel\nDevelopment files.\n%files devel\n"))
        code, output, _, _ = self.run_checker(response=self.upstream("8.4.0", repo="glaze"))
        self.assertEqual(code, 0)
        self.assertIn("HELD", self.package_row(output, "glaze"))
        self.assert_summary(output, held=1, update=0)

    def test_removing_upper_bound_allows_new_major_version(self):
        consumer = self.constrained_glaze()
        consumer.write_text(consumer.read_text().replace("BuildRequires: glaze-static < 8\n", ""))
        code, output, _, _ = self.run_checker(response=self.upstream("8.4.0", repo="glaze"))
        self.assertEqual(code, 0)
        self.assertIn("UPDATE", self.package_row(output, "glaze"))
        self.assert_summary(output, held=0, update=1)

    def test_constraints_follow_active_target_conditionals(self):
        consumer = self.constrained_glaze(requirements="glaze-static >= 7")
        consumer.write_text(consumer.read_text().replace(
            "\n%description", "\n%if 0%{?fedora} >= 45\nBuildRequires: glaze-static < 8\n%endif\n%description"
        ))
        code, output, _, _ = self.run_checker(response=self.upstream("8.4.0", repo="glaze"))
        self.assertEqual(code, 0)
        self.assertIn("UPDATE", self.package_row(output, "glaze"))
        self.assert_summary(output, held=0, update=1)

        code, output, _, _ = self.run_checker(
            "--target", "fedora-45-x86_64", response=self.upstream("8.4.0", repo="glaze")
        )
        self.assertEqual(code, 0)
        self.assertIn("HELD", self.package_row(output, "glaze"))
        self.assert_summary(output, held=1, update=0)

    def test_unversioned_requirement_does_not_hold_update(self):
        self.constrained_glaze(requirements="glaze-static")
        code, output, _, _ = self.run_checker(response=self.upstream("8.4.0", repo="glaze"))
        self.assertEqual(code, 0)
        self.assertIn("UPDATE", self.package_row(output, "glaze"))
        self.assert_summary(output, held=0, update=1)

    def test_unrelated_requirement_does_not_hold_update(self):
        self.constrained_glaze(requirements="unrelated-library < 8")
        code, output, _, _ = self.run_checker(response=self.upstream("8.4.0", repo="glaze"))
        self.assertEqual(code, 0)
        self.assertIn("UPDATE", self.package_row(output, "glaze"))
        self.assert_summary(output, held=0, update=1)

    def test_constraints_match_generic_provided_capabilities(self):
        self.spec("library", version="2.0",
                  metadata="Provides: pkgconfig(example-api) = %{version}\n")
        self.spec("consumer", metadata="BuildRequires: pkgconfig(example-api) < 3\n")

        def upstream(owner, repo, **options):
            return self.upstream("3.0" if repo == "library" else "1.0", repo=repo)

        code, output, _, _ = self.run_checker(side_effect=upstream)
        self.assertEqual(code, 0)
        self.assertIn("HELD", self.package_row(output, "library"))
        self.assertIn("consumer", output)
        self.assertRegex(output, r"pkgconfig\(example-api\)\s*<\s*3")
        self.assert_summary(output, held=1, current=1, update=0)

    def test_independently_versioned_provide_is_not_projected(self):
        self.spec("library", version="2.0", metadata="Provides: example-abi = 1\n")
        self.spec("consumer", metadata="BuildRequires: example-abi < 2\n")

        def upstream(owner, repo, **options):
            return self.upstream("3.0" if repo == "library" else "1.0", repo=repo)

        code, output, _, _ = self.run_checker(side_effect=upstream)
        self.assertEqual(code, 0)
        self.assertIn("UPDATE", self.package_row(output, "library"))
        self.assert_summary(output, held=0, current=1, update=1)

    def test_explicit_fixed_pkgconfig_version_overrides_file_inference(self):
        library = self.spec("library", version="2.0",
                            metadata="Provides: pkgconfig(example-api) = 1\n")
        library.write_text(library.read_text() + "%{_libdir}/pkgconfig/example-api.pc\n")
        self.spec("consumer", metadata="BuildRequires: pkgconfig(example-api) < 2\n")

        def upstream(owner, repo, **options):
            return self.upstream("3.0" if repo == "library" else "1.0", repo=repo)

        code, output, _, _ = self.run_checker(side_effect=upstream)
        self.assertEqual(code, 0)
        self.assertIn("UPDATE", self.package_row(output, "library"))
        self.assert_summary(output, held=0, current=1, update=1)

    def test_constraints_match_pkgconfig_and_cmake_files(self):
        for capability, file in (
            ("pkgconfig(example-api)", "%{_libdir}/pkgconfig/example-api.pc"),
            ("cmake(ExampleApi)", "%{_libdir}/cmake/ExampleApi/"),
        ):
            with self.subTest(capability=capability):
                library = self.spec("library", version="2.0")
                library.write_text(library.read_text() + file + "\n")
                self.spec("consumer", metadata=f"BuildRequires: {capability} < 3\n")

                def upstream(owner, repo, **options):
                    return self.upstream("3.0" if repo == "library" else "1.0", repo=repo)

                code, output, _, _ = self.run_checker(side_effect=upstream)
                self.assertEqual(code, 0)
                self.assertIn("HELD", self.package_row(output, "library"))
                self.assertIn(f"consumer requires {capability} < 3", output)
                self.assert_summary(output, held=1, current=1, update=0)

    def test_long_versions_release_link_and_tag_kind_are_not_clipped(self):
        local = "2026.123456789.987654320"
        remote = "2026.123456789.987654321"
        self.spec(version=local)
        release = self.upstream(remote, kind="tag")
        code, output, _, _ = self.run_checker("--allow-tags", response=release)
        self.assertEqual(code, 0)
        row = self.package_row(output, "package")
        self.assertIn(local, row)
        self.assertIn(remote, row)
        self.assertIn("tag", row.lower())
        self.assertIn(release.url, output)

    def test_tag_fallback_requires_explicit_option(self):
        self.spec()
        _, _, _, default_request = self.run_checker()
        _, _, _, allowed_request = self.run_checker("--allow-tags")
        self.assertFalse(default_request.call_args.kwargs.get("allow_tags", False))
        self.assertTrue(allowed_request.call_args.kwargs["allow_tags"])

    def test_local_token_comes_from_selected_root_and_is_not_persisted_elsewhere(self):
        self.spec()
        secrets = self.root / ".secrets"
        secrets.write_text("GITHUB_TOKEN=fixture-local-token\n")
        original = secrets.read_bytes()
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / ".secrets").write_text("GITHUB_TOKEN=fixture-wrong-token\n")

        with patch.dict(os.environ):
            os.environ.pop("GITHUB_TOKEN", None)
            environment = dict(os.environ)
            with chdir(elsewhere):
                code, output, errors, request = self.run_checker()
            self.assertTrue(dict(os.environ) == environment)

        self.assertEqual(code, 0)
        request.assert_called_once_with(
            "example", "package", allow_tags=False, token="fixture-local-token"
        )
        self.assertEqual(secrets.read_bytes(), original)
        self.assertNotIn("fixture-local-token", output + errors)
        self.assertNotIn("fixture-local-token", (self.root / checker.CACHE_PATH).read_text())

    def test_local_token_is_resolved_once_for_the_scan(self):
        self.spec("first")
        self.spec("second")
        secrets = self.root / ".secrets"
        secrets.write_text("GITHUB_TOKEN=fixture-first-token\n")

        def upstream(owner, repo, **options):
            secrets.write_text("GITHUB_TOKEN=fixture-changed-token\n")
            return self.upstream(repo=repo)

        with patch.dict(os.environ):
            os.environ.pop("GITHUB_TOKEN", None)
            code, _, _, request = self.run_checker(side_effect=upstream)

        self.assertEqual(code, 0)
        self.assertEqual(request.call_count, 2)
        self.assertTrue(all(call.kwargs.get("token") == "fixture-first-token"
                            for call in request.call_args_list))

    def test_malformed_local_token_prevents_requests_and_bumps(self):
        spec = self.spec()
        original_spec = spec.read_bytes()
        secrets = self.root / ".secrets"
        secrets.write_text("GITHUB_TOKEN='fixture-unterminated-token\n")
        original_secrets = secrets.read_bytes()
        with patch.dict(os.environ):
            os.environ.pop("GITHUB_TOKEN", None)
            environment = dict(os.environ)
            code, output, errors, request = self.run_checker("--bump", response=self.upstream("2.0"))
            self.assertTrue(dict(os.environ) == environment)

        self.assertEqual(code, 1)
        request.assert_not_called()
        self.assertEqual(spec.read_bytes(), original_spec)
        self.assertEqual(secrets.read_bytes(), original_secrets)
        self.assertNotIn("fixture-unterminated-token", output + errors)

    def test_existing_token_environment_bypasses_local_file_including_empty_value(self):
        self.spec()
        secrets = self.root / ".secrets"
        secrets.write_text("GITHUB_TOKEN='fixture-unterminated-token\n")
        original = secrets.read_bytes()
        for token in ("fixture-environment-token", ""):
            with self.subTest(anonymous=not token), patch.dict(os.environ, {"GITHUB_TOKEN": token}):
                environment = dict(os.environ)
                code, output, errors, request = self.run_checker("--refresh")
                self.assertTrue(dict(os.environ) == environment)
                self.assertEqual(code, 0)
                request.assert_called_once_with("example", "package", allow_tags=False)
                self.assertEqual(secrets.read_bytes(), original)
                self.assertNotIn("fixture-environment-token", output + errors)
                self.assertNotIn("fixture-unterminated-token", output + errors)

    def test_successful_results_are_cached_between_runs(self):
        self.spec()
        code, first, _, request = self.run_checker(response=self.upstream("2.0"))
        self.assertEqual(code, 0)
        request.assert_called_once()
        self.assertTrue((self.root / ".cache" / "upstream-versions.json").is_file())

        code, second, errors, request = self.run_checker(
            side_effect=AssertionError("fresh cached results must not request GitHub")
        )
        self.assertEqual(code, 0)
        request.assert_not_called()
        self.assertEqual(first, second)
        self.assertIn("Reused 1 cached upstream result", errors)
        self.assertIn("--refresh", errors)

    def test_refresh_bypasses_cache_and_saves_new_result(self):
        self.spec()
        self.run_checker(response=self.upstream("2.0"))
        code, output, _, request = self.run_checker("--refresh", response=self.upstream("3.0"))
        self.assertEqual(code, 0)
        request.assert_called_once()
        self.assertIn("v3.0", self.package_row(output, "package"))

        code, output, _, request = self.run_checker()
        self.assertEqual(code, 0)
        request.assert_not_called()
        self.assertIn("v3.0", self.package_row(output, "package"))

    def test_cached_upstream_version_is_compared_with_current_specs(self):
        self.spec(version="1.0")
        self.run_checker(response=self.upstream("2.0"))
        for local, status in (("2.0", "CURRENT"), ("3.0", "AHEAD")):
            with self.subTest(local=local):
                self.spec(version=local)
                code, output, _, request = self.run_checker()
                self.assertEqual(code, 0)
                request.assert_not_called()
                row = self.package_row(output, "package")
                self.assertIn(local, row)
                self.assertIn(status, row)
                self.assert_summary(output, **{status.lower(): 1})

    def test_cached_upstream_version_uses_current_dependency_constraints(self):
        consumer = self.constrained_glaze()
        original = consumer.read_text()
        code, output, _, _ = self.run_checker(response=self.upstream("8.4.0", repo="glaze"))
        self.assertEqual(code, 0)
        self.assertIn("HELD", self.package_row(output, "glaze"))

        for content, status in (
            (original.replace("BuildRequires: glaze-static < 8\n", ""), "UPDATE"),
            (original, "HELD"),
        ):
            with self.subTest(status=status):
                consumer.write_text(content)
                code, output, _, request = self.run_checker()
                self.assertEqual(code, 0)
                request.assert_not_called()
                self.assertIn(status, self.package_row(output, "glaze"))
                self.assert_summary(output, **{status.lower(): 1})

    def test_release_only_and_tag_fallback_have_separate_cached_results(self):
        self.spec()
        self.run_checker("--allow-tags", response=self.upstream("2.0", kind="tag"))
        code, output, _, request = self.run_checker(response=self.upstream("1.0"))
        self.assertEqual(code, 0)
        request.assert_called_once()
        self.assertIn("CURRENT", self.package_row(output, "package"))
        self.assertIn("release", self.package_row(output, "package"))

        code, output, _, request = self.run_checker("--allow-tags")
        self.assertEqual(code, 0)
        request.assert_not_called()
        self.assertIn("v2.0", self.package_row(output, "package"))
        self.assertIn("tag", self.package_row(output, "package"))

    def test_failed_results_are_retried_while_successes_are_reused(self):
        self.spec("a-failed")
        self.spec("b-cached")
        code, _, _, _ = self.run_checker(side_effect=[
            GitHubError("network unavailable"), self.upstream(repo="b-cached")
        ])
        self.assertEqual(code, 1)

        code, output, errors, request = self.run_checker(response=self.upstream(repo="a-failed"))
        self.assertEqual(code, 0)
        request.assert_called_once_with("example", "a-failed", allow_tags=False)
        self.assert_summary(output, current=2, error=0)
        self.assertIn("Reused 1 cached upstream result", errors)

    def test_rate_limit_still_allows_later_cached_results(self):
        self.spec("z-cached")
        self.run_checker(response=self.upstream(repo="z-cached"))
        self.spec("a-uncached")
        self.spec("b-uncached")

        code, output, errors, request = self.run_checker(
            side_effect=RateLimitError("GitHub rate limit exceeded")
        )
        self.assertEqual(code, 1)
        request.assert_called_once()
        self.assertIn("ERROR", self.package_row(output, "a-uncached"))
        self.assertIn("BLOCKED", self.package_row(output, "b-uncached"))
        self.assertIn("CURRENT", self.package_row(output, "z-cached"))
        self.assert_summary(output, current=1, error=1, blocked=1)
        self.assertIn("Reused 1 cached upstream result", errors)

    def test_changing_upstream_repository_does_not_reuse_old_result(self):
        self.spec()
        self.run_checker(response=self.upstream("2.0"))
        self.spec(url="https://github.com/example/replacement")
        code, output, _, request = self.run_checker(response=self.upstream("3.0", repo="replacement"))
        self.assertEqual(code, 0)
        request.assert_called_once_with("example", "replacement", allow_tags=False)
        self.assertIn("v3.0", self.package_row(output, "package"))

    def test_rate_limit_blocks_remaining_requests_even_with_updates_only(self):
        for name in ("a-package", "b-package", "c-package"):
            self.spec(name)
        code, output, _, request = self.run_checker(
            "--updates-only", side_effect=RateLimitError("GitHub rate limit exceeded")
        )
        self.assertEqual(code, 1)
        self.assertEqual(request.call_count, 1)
        self.assertIn("ERROR", self.package_row(output, "a-package"))
        for name in ("b-package", "c-package"):
            self.assertIn("BLOCKED", self.package_row(output, name))
        self.assert_summary(output, error=1, blocked=2)

    def test_default_directory_is_repository_root_not_working_directory(self):
        self.spec()
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        with patch.object(checker, "REPO_ROOT", self.root), chdir(elsewhere):
            code, output, _, request = self.run_checker(default_directory=True)
        self.assertEqual(code, 0)
        self.assertEqual(request.call_count, 1)
        self.assert_summary(output, current=1)

    def test_no_specs_returns_nonzero_without_requests(self):
        code, _, _, request = self.run_checker()
        self.assertEqual(code, 1)
        request.assert_not_called()

    def test_only_managed_packages_are_visible_but_not_an_eligible_scan(self):
        self.spec("managed-source", name="hyprland")
        code, output, _, request = self.run_checker()
        self.assertEqual(code, 1)
        request.assert_not_called()
        self.assertIn("SKIP", self.package_row(output, "hyprland"))
        self.assert_summary(output, skip=1, current=0, error=0)

    def test_checker_never_edits_specs(self):
        path = self.spec(version="9.0")
        before = path.read_bytes()
        self.run_checker(response=self.upstream("10.0"))
        self.assertEqual(path.read_bytes(), before)

    def test_bump_updates_only_eligible_versions_and_resets_release(self):
        updated = self.spec("update-package")
        updated.write_text(updated.read_text().replace("Release: 1\n", "Release: 5%{?dist}\n"))
        self.spec("ahead-package", version="3.0")
        self.spec("current-package", version="2.0")
        self.constrained_glaze()
        originals = {path: path.read_bytes() for path in self.root.glob("*/*.spec")}
        plan = self.root / "build-plan.json"
        plan.write_text('{"existing": "plan"}\n')

        def upstream(owner, repo, **options):
            return self.upstream("8.4.0" if repo == "glaze" else "2.0", repo=repo)

        code, output, _, _ = self.run_checker("--bump", side_effect=upstream)
        self.assertEqual(code, 0)
        self.assertEqual(updated.read_bytes(), originals[updated]
                         .replace(b"Version: 1.0\n", b"Version: 2.0\n")
                         .replace(b"Release: 5%{?dist}\n", b"Release: 1%{?dist}\n"))
        for path, original in originals.items():
            if path != updated:
                self.assertEqual(path.read_bytes(), original, path)
        self.assertEqual(plan.read_text(), '{"existing": "plan"}\n')
        self.assertIn("HELD", self.package_row(output, "glaze"))
        self.assertIn("AHEAD", self.package_row(output, "ahead-package"))
        self.assertIn("CURRENT", self.package_row(output, "current-package"))
        self.assertIn("SKIP", self.package_row(output, "hyprland"))

    def test_bump_selected_rpm_names_checks_only_selected_packages(self):
        selected = self.spec("source-directory", name="selected-package")
        untouched = self.spec("not-selected", url="https://gitlab.com/example/not-selected")
        original = untouched.read_bytes()
        code, _, _, request = self.run_checker(
            "--bump", "selected-package", response=self.upstream("2.0", repo="selected-package")
        )
        self.assertEqual(code, 0)
        request.assert_called_once_with("example", "selected-package", allow_tags=False)
        self.assertEqual(checker.parse_spec(selected).version, "2.0")
        self.assertEqual(untouched.read_bytes(), original)

    def test_selected_bump_still_checks_unselected_consumers_constraints(self):
        self.constrained_glaze()
        originals = {path: path.read_bytes() for path in self.root.glob("*/*.spec")}
        code, output, _, request = self.run_checker(
            "--bump", "glaze", response=self.upstream("8.4.0", repo="glaze")
        )
        self.assertEqual(code, 0)
        request.assert_called_once_with("example", "glaze", allow_tags=False)
        self.assertIn("HELD", self.package_row(output, "glaze"))
        self.assertRegex(output, r"glaze-static\s*<\s*8")
        for path, original in originals.items():
            self.assertEqual(path.read_bytes(), original)

    def test_bump_dry_run_prints_unified_diff_without_spec_changes(self):
        path = self.spec(version="1.0")
        original = path.read_bytes()
        code, output, _, _ = self.run_checker(
            "--bump", "--dry-run", response=self.upstream("2.0")
        )
        self.assertEqual(code, 0)
        self.assertEqual(path.read_bytes(), original)
        self.assertRegex(output, r"(?m)^--- .*package\.spec$")
        self.assertRegex(output, r"(?m)^\+\+\+ .*package\.spec$")
        self.assertIn("-Version: 1.0\n", output)
        self.assertIn("+Version: 2.0\n", output)

    def test_unknown_bump_name_fails_before_requests_and_edits(self):
        path = self.spec()
        original = path.read_bytes()
        code, output, errors, request = self.run_checker("--bump", "package", "does-not-exist")
        self.assertEqual(code, 1)
        self.assertIn("does-not-exist", output + errors)
        request.assert_not_called()
        self.assertEqual(path.read_bytes(), original)

    def test_any_selected_scan_failure_prevents_all_bumps(self):
        for name in ("a-update", "b-fails", "c-later"):
            self.spec(name)
        originals = {path: path.read_bytes() for path in self.root.glob("*/*.spec")}
        for failure in (GitHubError("network unavailable"), RateLimitError("GitHub rate limit exceeded")):
            with self.subTest(failure=type(failure).__name__):
                def upstream(owner, repo, **options):
                    if repo == "b-fails":
                        raise failure
                    return self.upstream("2.0", repo=repo)

                code, output, _, _ = self.run_checker("--bump", "--refresh", side_effect=upstream)
                self.assertEqual(code, 1)
                self.assertIn("UPDATE", self.package_row(output, "a-update"))
                self.assertIn("ERROR", self.package_row(output, "b-fails"))
                if isinstance(failure, RateLimitError):
                    self.assertIn("BLOCKED", self.package_row(output, "c-later"))
                for path, original in originals.items():
                    self.assertEqual(path.read_bytes(), original)

    def test_invalid_unselected_spec_prevents_selected_bump(self):
        selected = self.spec("selected-package")
        broken = self.spec("broken-consumer")
        broken.write_text(broken.read_text().replace("Version: 1.0\n", ""))
        originals = {path: path.read_bytes() for path in (selected, broken)}
        code, _, _, _ = self.run_checker(
            "--bump", "selected-package", response=self.upstream("2.0", repo="selected-package")
        )
        self.assertEqual(code, 1)
        for path, original in originals.items():
            self.assertEqual(path.read_bytes(), original)

    def test_unsupported_version_edit_prevents_entire_batch(self):
        self.spec("a-literal")
        self.spec("b-macro", version="%{upstream}", prefix="%global upstream 1.0\n")
        originals = {path: path.read_bytes() for path in self.root.glob("*/*.spec")}
        code, _, _, _ = self.run_checker("--bump", response=self.upstream("2.0"))
        self.assertEqual(code, 1)
        for path, original in originals.items():
            self.assertEqual(path.read_bytes(), original)

    def test_bump_reuses_cached_candidates_and_preserves_cache(self):
        path = self.spec()
        self.run_checker(response=self.upstream("2.0"))
        cache = self.root / ".cache" / "upstream-versions.json"
        original_cache = cache.read_bytes()

        code, _, errors, request = self.run_checker(
            "--bump", side_effect=AssertionError("bump should reuse cached candidate")
        )
        self.assertEqual(code, 0)
        request.assert_not_called()
        self.assertEqual(checker.parse_spec(path).version, "2.0")
        self.assertEqual(cache.read_bytes(), original_cache)
        self.assertIn("Reused 1 cached upstream result", errors)

        code, output, _, request = self.run_checker("--bump")
        self.assertEqual(code, 0)
        request.assert_not_called()
        self.assertIn("CURRENT", self.package_row(output, "package"))


if __name__ == "__main__":
    unittest.main()
