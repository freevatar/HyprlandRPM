"""Read stable GitHub releases, with an explicit fallback to numeric version tags."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from http.client import HTTPException
import json
import logging
import os
from pathlib import Path
import re
import shlex
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from package_graph import compare_versions


LOGGER = logging.getLogger(__name__)
GITHUB_API_VERSION = "2022-11-28"
HTTP_TIMEOUT_SECONDS = 10
HTTP_ATTEMPTS = 3
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_TAG_PAGES = 20
STABLE_TAG = re.compile(r"[vV]?(\d+(?:\.\d+)*)", re.ASCII)


class GitHubError(RuntimeError):
    """GitHub could not supply a usable upstream version."""


class RateLimitError(GitHubError):
    """Further requests must wait for GitHub's rate limit to reset."""


class _HTTPStatusError(GitHubError):
    def __init__(self, status: int):
        super().__init__(f"GitHub returned HTTP {status}")
        self.status = status


@dataclass(frozen=True)
class UpstreamVersion:
    tag: str
    version: str
    url: str
    kind: str


def _validated_token(token: str | None) -> str | None:
    if token and any(not 33 <= ord(character) <= 126 for character in token):
        raise GitHubError("GITHUB_TOKEN must contain only printable ASCII without whitespace")
    return token or None


def read_local_token(path: Path) -> str | None:
    """Read an optional assignment file as data; an existing environment value wins.

    Quotes, comments, and an optional export prefix are accepted. Variables and
    shell commands are never expanded. An empty token means anonymous access.
    """
    if "GITHUB_TOKEN" in os.environ:
        return None
    try:
        contents = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError):
        raise GitHubError("Cannot read the local .secrets file") from None

    token = None
    found = False
    for number, line in enumerate(contents.splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        assignment = re.fullmatch(
            r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)", line
        )
        if assignment is None:
            raise GitHubError(f"Invalid assignment in .secrets at line {number}")
        name, value = assignment.groups()
        try:
            values = shlex.split(value, comments=True, posix=True)
        except ValueError:
            raise GitHubError(f"Invalid assignment in .secrets at line {number}") from None
        if len(values) > 1:
            raise GitHubError(f"Invalid assignment in .secrets at line {number}")
        if name == "GITHUB_TOKEN":
            if found:
                raise GitHubError("Duplicate GITHUB_TOKEN assignment in .secrets")
            token = _validated_token(values[0] if values else None)
            found = True
    return token


def _rate_limit_message(headers: dict[str, str]) -> str:
    retry = headers.get("retry-after", "")
    if retry.isdecimal() and len(retry) < 12:
        return f"GitHub rate limit reached; retry after {int(retry)} seconds"
    reset = headers.get("x-ratelimit-reset", "")
    if reset.isdecimal() and len(reset) < 12:
        try:
            moment = datetime.fromtimestamp(int(reset), timezone.utc)
            return f"GitHub rate limit reached; resets {moment:%Y-%m-%d %H:%M UTC}"
        except (ValueError, OverflowError, OSError):
            pass
    return "GitHub rate limit reached; retry later"


def _get_json(url: str, *, token: str | None = None) -> tuple[object, dict[str, str]]:
    for attempt in range(HTTP_ATTEMPTS):
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "HyprlandRPM-version-check/1",
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
        }
        request_token = _validated_token(token if token is not None else os.environ.get("GITHUB_TOKEN"))
        if request_token:
            headers["Authorization"] = f"Bearer {request_token}"
        request = Request(url, headers=headers)
        LOGGER.debug("GitHub GET %s (attempt %d/%d)", url, attempt + 1, HTTP_ATTEMPTS)
        try:
            with urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
                payload = response.read(MAX_RESPONSE_BYTES + 1)
                response_headers = {key.lower(): value for key, value in response.headers.items()}
                LOGGER.debug("GitHub HTTP %s for %s (%d bytes)",
                             getattr(response, "status", "?"), url, len(payload))
        except HTTPError as error:
            LOGGER.debug("GitHub HTTP %s for %s", error.code, url)
            response_headers = {key.lower(): value for key, value in (error.headers or {}).items()}
            try:
                # Secondary limits may omit rate headers; inspect their message,
                # but never include response content in diagnostics.
                body = error.read(4096) if error.code == 403 else b""
            except (OSError, HTTPException):
                body = b""
            finally:
                error.close()
            limited = error.code == 429 or error.code == 403 and (
                response_headers.get("x-ratelimit-remaining") == "0"
                or "retry-after" in response_headers
                or b"rate limit" in body.lower()
            )
            if limited:
                LOGGER.debug("Stopping requests after GitHub rate limit")
                raise RateLimitError(_rate_limit_message(response_headers)) from None
            if not 500 <= error.code < 600 or attempt + 1 == HTTP_ATTEMPTS:
                raise _HTTPStatusError(error.code) from None
        except (URLError, OSError, HTTPException) as error:
            LOGGER.debug("GitHub transport failure (%s) for %s", type(error).__name__, url)
            if attempt + 1 == HTTP_ATTEMPTS:
                raise GitHubError("GitHub request failed after three attempts") from None
        else:
            if len(payload) > MAX_RESPONSE_BYTES:
                raise GitHubError("GitHub response exceeds the size limit")
            try:
                return json.loads(payload), response_headers
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise GitHubError("GitHub returned invalid JSON") from None
        delay = 0.25 * 2 ** attempt
        LOGGER.debug("Retrying GitHub request in %.2f seconds", delay)
        time.sleep(delay)
    raise AssertionError("HTTP retry loop exhausted")


def _stable_version(tag: object) -> str | None:
    if not isinstance(tag, str):
        return None
    match = STABLE_TAG.fullmatch(tag)
    return match[1] if match else None


def latest_version(owner: str, repo: str, *, allow_tags: bool = False,
                   token: str | None = None) -> UpstreamVersion:
    """Prefer GitHub's latest full release; only a 404 permits tag fallback."""
    repository = f"{quote(owner, safe='')}/{quote(repo, safe='')}"
    api = f"https://api.github.com/repos/{repository}"
    web = f"https://github.com/{repository}"
    try:
        release, _ = _get_json(f"{api}/releases/latest", token=token)
    except _HTTPStatusError as error:
        if error.status != 404:
            raise
        if not allow_tags:
            raise GitHubError(
                f"No stable GitHub release for {owner}/{repo}; use --allow-tags to check version tags"
            ) from None
        LOGGER.debug("No published release for %s; checking version tags", repository)
    else:
        if not isinstance(release, dict):
            raise GitHubError("GitHub returned an invalid release")
        if release.get("draft") is not False or release.get("prerelease") is not False:
            raise GitHubError("GitHub latest release is not a published stable release")
        tag = release.get("tag_name")
        version = _stable_version(tag)
        if version is None:
            raise GitHubError("GitHub release tag is not a numeric stable version")
        return UpstreamVersion(tag, version, f"{web}/releases/tag/{quote(tag, safe='')}", "release")

    selected: UpstreamVersion | None = None
    for page in range(1, MAX_TAG_PAGES + 1):
        query = urlencode({"per_page": 100, "page": page})
        tags, headers = _get_json(f"{api}/tags?{query}", token=token)
        if not isinstance(tags, list) or any(not isinstance(tag, dict) for tag in tags):
            raise GitHubError("GitHub returned an invalid tag list")
        LOGGER.debug("Read %d tags on page %d for %s", len(tags), page, repository)
        for entry in tags:
            tag = entry.get("name")
            version = _stable_version(tag)
            if version is not None and (
                selected is None or compare_versions(version, selected.version) > 0
            ):
                selected = UpstreamVersion(tag, version, f"{web}/tree/{quote(tag, safe='')}", "tag")
        if not re.search(r'\brel="next"', headers.get("link", "")):
            if selected is None:
                raise GitHubError("No numeric stable version tags found")
            return selected
    raise GitHubError(f"GitHub tag list exceeds {MAX_TAG_PAGES} pages; no partial result was selected")
