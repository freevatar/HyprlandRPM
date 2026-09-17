"""GitHub version lookup without network access or credentials."""

import contextlib
from http.client import IncompleteRead
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import github_versions as github


class Response:
    def __init__(self, payload=None, *, headers=None, raw=None):
        self.payload = raw if raw is not None else json.dumps(payload).encode()
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self, size):
        return self.payload[:size]


def http_error(status, *, headers=None, body=b""):
    return HTTPError("https://api.github.com/repos/example/repo/releases/latest", status,
                     "fixture-secret", headers or {}, io.BytesIO(body))


def release(tag="v2.1.0", **changes):
    return {"tag_name": tag, "draft": False, "prerelease": False, **changes}


class LocalTokenTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / ".secrets"

    def test_missing_file_or_token_uses_anonymous_access(self):
        self.assertIsNone(github.read_local_token(self.path))
        for contents in ("", "# No token\nOTHER='another value'\n", "GITHUB_TOKEN= # empty\n",
                         'GITHUB_TOKEN=""\n'):
            self.path.write_text(contents)
            self.assertIsNone(github.read_local_token(self.path))

    def test_assignment_formats_comments_and_unrelated_settings(self):
        for assignment in ("GITHUB_TOKEN=fixture-token", "export GITHUB_TOKEN='fixture-token'",
                           ' GITHUB_TOKEN = "fixture-token" # note', "GITHUB_TOKEN=fixture-token # note"):
            with self.subTest(assignment=assignment):
                self.path.write_text("# Local credentials\nOTHER='another value'\n\n" + assignment)
                self.assertEqual(github.read_local_token(self.path), "fixture-token")
                self.assertNotIn("GITHUB_TOKEN", os.environ)

    def test_environment_presence_skips_file_even_when_empty(self):
        for token in ("environment-fixture", ""):
            with patch.dict(os.environ, {"GITHUB_TOKEN": token}):
                with patch.object(Path, "read_text", side_effect=AssertionError("must not read")):
                    self.assertIsNone(github.read_local_token(self.path))

    def test_assignments_are_data_without_expansion_or_execution(self):
        marker = Path(self.directory.name) / "should-not-exist"
        self.path.write_text(f"OTHER='$(touch {marker})'\nGITHUB_TOKEN='${{FIXTURE_TOKEN}}'\n")
        with patch.dict(os.environ, {"FIXTURE_TOKEN": "expanded-fixture"}):
            self.assertEqual(github.read_local_token(self.path), "${FIXTURE_TOKEN}")
        self.assertFalse(marker.exists())

    def test_malformed_and_duplicate_assignments_have_safe_errors(self):
        for contents in ("GITHUB_TOKEN='fixture-secret", "GITHUB_TOKEN fixture-secret",
                         "GITHUB_TOKEN=fixture-secret extra", "GITHUB_TOKEN=fixture-secret\nGITHUB_TOKEN=second",
                         "GITHUB_TOKEN='fixture-secret with-space'", "GITHUB_TOKEN=fixture-secreté",
                         "GITHUB_TOKEN='fixture-secret\x00'"):
            with self.subTest(contents=contents):
                self.path.write_text(contents)
                with self.assertRaises(github.GitHubError) as caught:
                    github.read_local_token(self.path)
                self.assertNotIn("fixture-secret", str(caught.exception))

    def test_unreadable_file_has_safe_error(self):
        for error in (PermissionError("fixture-secret"), UnicodeError("fixture-secret")):
            with patch.object(Path, "read_text", side_effect=error):
                with self.assertRaises(github.GitHubError) as caught:
                    github.read_local_token(self.path)
            self.assertEqual(str(caught.exception), "Cannot read the local .secrets file")


class GitHubVersionTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {"GITHUB_TOKEN": ""})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_prefers_published_release_even_when_tags_are_allowed(self):
        with patch.object(github, "urlopen", return_value=Response(release())) as request:
            result = github.latest_version("example", "repo", allow_tags=True)
        self.assertEqual(result, github.UpstreamVersion(
            "v2.1.0", "2.1.0", "https://github.com/example/repo/releases/tag/v2.1.0", "release"))
        self.assertEqual(request.call_count, 1)
        self.assertEqual(request.call_args.kwargs["timeout"], 10)
        self.assertIsNone(request.call_args.args[0].get_header("Authorization"))
        self.assertEqual(request.call_args.args[0].get_header("X-github-api-version"), "2022-11-28")

    def test_token_is_read_for_each_request(self):
        with patch.object(github, "urlopen", return_value=Response(release())) as request:
            for token in ("first-fixture-token", "second-fixture-token"):
                with patch.dict(os.environ, {"GITHUB_TOKEN": token}):
                    github.latest_version("example", "repo")
        self.assertEqual([call.args[0].get_header("Authorization") for call in request.call_args_list],
                         ["Bearer first-fixture-token", "Bearer second-fixture-token"])

    def test_explicit_token_is_used_for_release_and_all_tag_pages(self):
        responses = [http_error(404),
                     Response([{"name": "v1.0"}], headers={"Link": '<next>; rel="next"'}),
                     Response([{"name": "v2.0"}])]
        with patch.dict(os.environ, {"GITHUB_TOKEN": "environment-fixture"}):
            with patch.object(github, "urlopen", side_effect=responses) as request:
                result = github.latest_version("example", "repo", allow_tags=True, token="file-fixture")
            self.assertEqual(os.environ["GITHUB_TOKEN"], "environment-fixture")
        self.assertEqual(result.version, "2.0")
        self.assertEqual([call.args[0].get_header("Authorization") for call in request.call_args_list],
                         ["Bearer file-fixture"] * 3)

    def test_invalid_tokens_are_rejected_without_leaking_or_requesting(self):
        for token in ("fixture-secret\n", "fixture-secret\r", "fixture-secret\t", "fixture-secret x",
                      "fixture-secreté", "fixture-secret\x00", "fixture-secret\x7f"):
            for explicit in (False, True):
                with self.subTest(token=token, explicit=explicit):
                    kwargs = {"token": token} if explicit else {}
                    with patch.object(github.os, "environ", {"GITHUB_TOKEN": token}):
                        with patch.object(github, "urlopen") as request:
                            with self.assertRaises(github.GitHubError) as caught:
                                github.latest_version("example", "repo", **kwargs)
                    request.assert_not_called()
                    self.assertNotIn("fixture-secret", str(caught.exception))

    def test_explicit_token_is_not_logged_on_http_failure(self):
        with patch.object(github, "urlopen", side_effect=http_error(401, body=b"fixture-secret")):
            with self.assertLogs(github.LOGGER, level="DEBUG") as logs:
                with self.assertRaises(github.GitHubError) as caught:
                    github.latest_version("example", "repo", token="fixture-secret")
        self.assertNotIn("fixture-secret", str(caught.exception))
        self.assertNotIn("fixture-secret", "\n".join(logs.output))

    def test_http_failure_does_not_expose_token_headers_or_response_body(self):
        output = io.StringIO()
        with patch.dict(os.environ, {"GITHUB_TOKEN": "fixture-secret"}):
            with patch.object(github, "urlopen", side_effect=http_error(401, body=b"fixture-secret")):
                with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                    with self.assertLogs(github.LOGGER, level="DEBUG") as logs:
                        with self.assertRaises(github.GitHubError) as caught:
                            github.latest_version("example", "repo")
        self.assertIn("HTTP 401", str(caught.exception))
        self.assertNotIn("fixture-secret", str(caught.exception))
        self.assertNotIn("fixture-secret", "\n".join(logs.output))
        self.assertNotIn("Authorization", "\n".join(logs.output))
        self.assertIn("GitHub HTTP 401", "\n".join(logs.output))
        self.assertEqual(output.getvalue(), "")

    def test_missing_release_requires_explicit_tag_opt_in(self):
        with patch.object(github, "urlopen", side_effect=http_error(404)) as request:
            with self.assertRaisesRegex(github.GitHubError, "--allow-tags"):
                github.latest_version("example", "repo")
        self.assertEqual(request.call_count, 1)

    def test_forbidden_never_falls_back_to_tags(self):
        with patch.object(github, "urlopen", side_effect=http_error(403)) as request:
            with self.assertRaisesRegex(github.GitHubError, "HTTP 403"):
                github.latest_version("example", "repo", allow_tags=True)
        self.assertEqual(request.call_count, 1)

    def test_rate_limits_stop_without_retries_or_fallback(self):
        errors = [
            http_error(403, headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "2000000000"}),
            http_error(403, headers={"Retry-After": "3600"}),
            http_error(403, body=b'{"message":"You have exceeded a secondary rate limit"}'),
            http_error(429),
        ]
        for error in errors:
            with self.subTest(status=error.code, headers=error.headers):
                with patch.object(github, "urlopen", side_effect=error) as request:
                    with patch.object(github.time, "sleep") as sleep:
                        with self.assertRaises(github.RateLimitError) as caught:
                            github.latest_version("example", "repo", allow_tags=True)
                self.assertEqual(request.call_count, 1)
                sleep.assert_not_called()
                self.assertNotIn("fixture-secret", str(caught.exception))

    def test_rate_limit_message_shows_reset_or_retry_delay(self):
        self.assertIn("2033-05-18", github._rate_limit_message({"x-ratelimit-reset": "2000000000"}))
        self.assertIn("3600 seconds", github._rate_limit_message({"retry-after": "3600"}))
        self.assertEqual(github._rate_limit_message({"retry-after": "secret-header"}),
                         "GitHub rate limit reached; retry later")

    def test_truncated_forbidden_body_preserves_rate_limit_headers(self):
        class TruncatedBody(io.BytesIO):
            def read(self, size):
                raise IncompleteRead(b"fixture-secret", 10)

        error = HTTPError("https://api.github.com", 403, "fixture-secret",
                          {"X-RateLimit-Remaining": "0"}, TruncatedBody())
        with patch.object(github, "urlopen", side_effect=error) as request:
            with patch.object(github.time, "sleep") as sleep:
                with self.assertLogs(github.LOGGER, level="DEBUG") as logs:
                    with self.assertRaises(github.RateLimitError):
                        github.latest_version("example", "repo", allow_tags=True)
        self.assertEqual(request.call_count, 1)
        sleep.assert_not_called()
        self.assertNotIn("fixture-secret", "\n".join(logs.output))

    def test_transient_server_failure_is_retried(self):
        with patch.object(github, "urlopen", side_effect=[http_error(502), Response(release())]) as request:
            with patch.object(github.time, "sleep") as sleep:
                result = github.latest_version("example", "repo")
        self.assertEqual(result.version, "2.1.0")
        self.assertEqual(request.call_count, 2)
        sleep.assert_called_once_with(0.25)

    def test_network_failure_has_three_attempts_and_safe_error(self):
        with patch.object(github, "urlopen", side_effect=URLError("fixture-secret")) as request:
            with patch.object(github.time, "sleep") as sleep:
                with self.assertRaises(github.GitHubError) as caught:
                    github.latest_version("example", "repo")
        self.assertEqual(request.call_count, 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [0.25, 0.5])
        self.assertNotIn("fixture-secret", str(caught.exception))

    def test_repeated_server_failures_have_three_attempts(self):
        with patch.object(github, "urlopen", side_effect=[http_error(503) for _ in range(3)]) as request:
            with patch.object(github.time, "sleep"):
                with self.assertRaisesRegex(github.GitHubError, "HTTP 503"):
                    github.latest_version("example", "repo")
        self.assertEqual(request.call_count, 3)

    def test_invalid_release_metadata_is_rejected_without_tag_fallback(self):
        invalid = [[], {}, release(prerelease=True), release(draft=True),
                   release(tag="v2.0.0-rc1"), release(tag=42),
                   {"name": "v2.0.0", "draft": False, "prerelease": False}]
        for payload in invalid:
            with self.subTest(payload=payload):
                with patch.object(github, "urlopen", return_value=Response(payload)) as request:
                    with self.assertRaises(github.GitHubError):
                        github.latest_version("example", "repo", allow_tags=True)
                self.assertEqual(request.call_count, 1)

    def test_tag_fallback_paginates_filters_and_compares_versions(self):
        responses = [
            http_error(404),
            Response([{"name": "v9.0"}, {"name": "v12.0-rc1"}, {"name": "nightly"}],
                     headers={"Link": '<https://api.github.com/next>; rel="next"'}),
            Response([{"name": "v10.0"}, {"name": "V2.1"}, {"name": "v8.0-beta"}]),
        ]
        with patch.object(github, "urlopen", side_effect=responses) as request:
            result = github.latest_version("example", "repo", allow_tags=True)
        self.assertEqual(result, github.UpstreamVersion(
            "v10.0", "10.0", "https://github.com/example/repo/tree/v10.0", "tag"))
        self.assertEqual([call.args[0].full_url for call in request.call_args_list], [
            "https://api.github.com/repos/example/repo/releases/latest",
            "https://api.github.com/repos/example/repo/tags?per_page=100&page=1",
            "https://api.github.com/repos/example/repo/tags?per_page=100&page=2",
        ])

    def test_tag_page_limit_does_not_return_partial_maximum(self):
        page = Response([{"name": "v1.0"}], headers={"Link": '<https://api.github.com/next>; rel="next"'})
        with patch.object(github, "MAX_TAG_PAGES", 2):
            with patch.object(github, "urlopen", side_effect=[http_error(404), page, page]) as request:
                with self.assertRaisesRegex(github.GitHubError, "no partial result"):
                    github.latest_version("example", "repo", allow_tags=True)
        self.assertEqual(request.call_count, 3)

    def test_invalid_or_nonstable_tag_lists_do_not_claim_a_version(self):
        for payload in ({"name": "v1.0"}, ["v1.0"], [], [{"name": "v2.0-rc1"}], [{"name": 42}]):
            with self.subTest(payload=payload):
                with patch.object(github, "urlopen", side_effect=[http_error(404), Response(payload)]):
                    with self.assertRaises(github.GitHubError):
                        github.latest_version("example", "repo", allow_tags=True)

    def test_tag_endpoint_not_found_is_not_reported_as_no_stable_release(self):
        with patch.object(github, "urlopen", side_effect=[http_error(404), http_error(404)]):
            with self.assertRaisesRegex(github.GitHubError, "HTTP 404"):
                github.latest_version("example", "repo", allow_tags=True)

    def test_response_size_and_json_are_checked(self):
        responses = [Response(raw=b"x" * (github.MAX_RESPONSE_BYTES + 1)), Response(raw=b"not-json")]
        for response in responses:
            with self.subTest(size=len(response.payload)):
                with patch.object(github, "urlopen", return_value=response) as request:
                    with self.assertRaises(github.GitHubError):
                        github.latest_version("example", "repo")
                self.assertEqual(request.call_count, 1)


if __name__ == "__main__":
    unittest.main()
