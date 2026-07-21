#!/usr/bin/env python3
"""Update Hyprland RPM snapshot metadata and trigger COPR builds.

Dry runs are transactional: validated changes are displayed and then restored before the process exits.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import random
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from http.client import HTTPMessage
from pathlib import Path
from typing import Any, Final, NoReturn
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MINIMUM_PYTHON: Final = (3, 14, 6)

GITHUB_API: Final = "https://api.github.com"
GITHUB_API_VERSION: Final = "2026-03-10"
GITHUB_REPOSITORY: Final = "hyprwm/Hyprland"
COPR_BASE_URL: Final = "https://copr.fedorainfracloud.org/webhooks/custom"

HTTP_TIMEOUT_SECONDS: Final = 10.0
HTTP_ATTEMPTS: Final = 8
MAX_RESPONSE_BYTES: Final = 8 * 1024 * 1024
RETRYABLE_HTTP_STATUSES: Final = frozenset({408, 425, 429, 500, 502, 503, 504})
SHA1_RE: Final = re.compile(r"[0-9a-f]{40}\Z")
BUILD_ID_RE: Final = re.compile(r"[0-9]+\Z")

WEEKDAY_ABBR: Final = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
MONTH_ABBR: Final = (
    "",
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)
MAX_COMMIT_TITLE_BYTES: Final = 4096
SHELL_SCRIPTLET_NAMES: Final = frozenset({"prep", "build", "install", "check"})
SPEC_SECTION_RE: Final = re.compile(r"^%(?P<name>[A-Za-z][A-Za-z0-9_]*)\b")


class UpdateError(RuntimeError):
    """A user-actionable updater failure."""


@dataclass(frozen=True, slots=True)
class Config:
    git_spec: Path
    release_spec: Path
    dry_run: bool
    github_token: str | None = field(default=None, repr=False)


@dataclass(slots=True)
class SpecDocument:
    """An RPM spec file held in memory until an atomic write is requested."""

    path: Path
    original: str
    text: str
    mode: int

    @classmethod
    def load(cls, path: Path) -> SpecDocument:
        try:
            metadata = path.stat()
            if not stat.S_ISREG(metadata.st_mode):
                raise UpdateError(f"spec path is not a regular file: {path}")
            raw = path.read_bytes()
            text = raw.decode("utf-8")
        except FileNotFoundError as exc:
            raise UpdateError(f"spec file not found: {path}") from exc
        except UnicodeDecodeError as exc:
            raise UpdateError(f"spec file is not valid UTF-8: {path}") from exc
        except OSError as exc:
            raise UpdateError(f"unable to read spec file {path}: {exc}") from exc

        return cls(
            path=path,
            original=text,
            text=text,
            mode=stat.S_IMODE(metadata.st_mode),
        )

    @property
    def changed(self) -> bool:
        return self.text != self.original

    def get_global(self, name: str) -> str:
        match = _find_exact_global(self.text, self.path, name)
        return match.group("value")

    def update_globals(self, updates: Mapping[str, str]) -> None:
        for name, value in updates.items():
            if "\n" in value or "\r" in value:
                raise UpdateError(f"value for %global {name} must be one line")

            pattern = _global_pattern(name)
            matches = list(pattern.finditer(self.text))
            if len(matches) != 1:
                raise UpdateError(
                    f"expected exactly one %global {name} definition in "
                    f"{self.path}, found {len(matches)}"
                )

            # A callback inserts the replacement literally. Unlike a replacement
            # string, it cannot interpret backslashes from an upstream commit title.
            self.text = pattern.sub(
                lambda match, replacement=value: (
                    f"{match.group('prefix')} {replacement}"
                ),
                self.text,
                count=1,
            )

    def write(self) -> None:
        _atomic_write(self.path, self.text.encode("utf-8"), self.mode)

    def restore(self) -> None:
        _atomic_write(self.path, self.original.encode("utf-8"), self.mode)


def _global_pattern(name: str) -> re.Pattern[str]:
    return re.compile(
        rf"^(?P<prefix>%global[ \t]+{re.escape(name)})[ \t]+(?P<value>.*)$",
        flags=re.MULTILINE,
    )


def _find_exact_global(text: str, path: Path, name: str) -> re.Match[str]:
    matches = list(_global_pattern(name).finditer(text))
    if len(matches) != 1:
        raise UpdateError(
            f"expected exactly one %global {name} definition in {path}, "
            f"found {len(matches)}"
        )
    return matches[0]


def _atomic_write(path: Path, payload: bytes, mode: int) -> None:
    """Durably replace *path* with *payload* while retaining its permission bits."""

    directory = path.parent
    temporary_path: Path | None = None

    try:
        file_descriptor, temporary_name = tempfile.mkstemp(
            dir=directory,
            prefix=f".{path.name}.",
            suffix=".tmp",
        )
        temporary_path = Path(temporary_name)

        with os.fdopen(file_descriptor, "wb", closefd=True) as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())

        os.chmod(temporary_path, mode)
        os.replace(temporary_path, path)
        temporary_path = None

        # Persist the directory entry on POSIX systems. Some filesystems or
        # platforms do not allow opening directories; the data replacement is
        # still complete in that case.
        try:
            directory_fd = os.open(
                directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            )
        except OSError:
            pass
        else:
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    except OSError as exc:
        raise UpdateError(f"unable to atomically update {path}: {exc}") from exc
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass


def log(message: str) -> None:
    print(f"==> {message}", file=sys.stderr, flush=True)


def fail(message: str) -> NoReturn:
    raise UpdateError(message)


def require_python() -> None:
    if sys.version_info < MINIMUM_PYTHON:
        required = ".".join(map(str, MINIMUM_PYTHON))
        running = ".".join(map(str, sys.version_info[:3]))
        fail(f"Python {required} or newer is required; running {running}")


def require_commands(commands: Sequence[str]) -> None:
    missing = [command for command in commands if shutil.which(command) is None]
    if missing:
        fail(f"required command(s) not found: {', '.join(missing)}")


def run_command(
    arguments: Sequence[str | os.PathLike[str]],
    *,
    check: bool = True,
    capture_output: bool = False,
    stdout: int | None = None,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a command with deterministic UTF-8 text handling."""

    command = [os.fspath(argument) for argument in arguments]

    try:
        result = subprocess.run(
            command,
            check=False,
            input=input_text,
            stdin=subprocess.DEVNULL if input_text is None else None,
            stdout=subprocess.PIPE if capture_output else stdout,
            stderr=subprocess.PIPE if capture_output else None,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        raise UpdateError(f"unable to execute {command[0]}: {exc}") from exc

    if check and result.returncode != 0:
        details = (result.stderr or "").strip()
        suffix = f": {details}" if details else ""
        raise UpdateError(
            f"command failed with status {result.returncode}: {command[0]}{suffix}"
        )

    return result


def run_git(
    arguments: Sequence[str | os.PathLike[str]],
    *,
    check: bool = True,
    capture_output: bool = False,
    stdout: int | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run Git with paging disabled, regardless of user Git configuration."""

    return run_command(
        ["git", "--no-pager", *arguments],
        check=check,
        capture_output=capture_output,
        stdout=stdout,
    )


def git_diff_is_quiet(paths: Sequence[Path], *, cached: bool = False) -> bool:
    arguments: list[str | os.PathLike[str]] = ["diff", "--quiet"]
    if cached:
        arguments.append("--cached")
    arguments.extend(("--", *paths))

    result = run_git(arguments, check=False)
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    raise UpdateError(f"git diff failed with status {result.returncode}")


def parse_boolean(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return default

    normalized = value.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off", ""}:
        return False
    fail(f"invalid boolean value: {value!r}")


def parse_arguments() -> Config:
    parser = argparse.ArgumentParser(
        description="Update Hyprland RPM snapshot metadata and trigger COPR builds."
    )
    parser.add_argument(
        "--git-spec",
        type=Path,
        default=Path(os.environ.get("GIT_SPEC", "hyprland-git.spec")),
        help="snapshot spec path (default: GIT_SPEC or hyprland-git.spec)",
    )
    parser.add_argument(
        "--release-spec",
        type=Path,
        default=Path(os.environ.get("REL_SPEC", "../hyprland/hyprland.spec")),
        help="release spec path (default: REL_SPEC or ../hyprland/hyprland.spec)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=parse_boolean(os.environ.get("DRY_RUN")),
        help="validate and show changes without committing or building",
    )
    arguments = parser.parse_args()

    return Config(
        git_spec=arguments.git_spec,
        release_spec=arguments.release_spec,
        dry_run=arguments.dry_run,
        github_token=os.environ.get("GITHUB_TOKEN") or None,
    )


class HttpClient:
    """Small bounded HTTP client with retry and secret-safe diagnostics."""

    def __init__(self, default_headers: Mapping[str, str] | None = None) -> None:
        self._default_headers = dict(default_headers or {})

    def request(
        self,
        url: str,
        *,
        label: str,
        method: str = "GET",
        data: bytes | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> tuple[bytes, HTTPMessage]:
        request_headers = self._default_headers | dict(headers or {})

        for attempt in range(HTTP_ATTEMPTS):
            request = Request(
                url,
                data=data,
                headers=request_headers,
                method=method,
            )
            try:
                with urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
                    payload = _bounded_read(response, label)
                    return payload, response.headers
            except HTTPError as exc:
                body = _read_http_error_body(exc)
                if exc.code in RETRYABLE_HTTP_STATUSES and attempt + 1 < HTTP_ATTEMPTS:
                    _sleep_before_retry(attempt, exc.headers.get("Retry-After"))
                    continue
                detail = _compact_http_body(body)
                suffix = f": {detail}" if detail else ""
                raise UpdateError(
                    f"{label} failed with HTTP status {exc.code}{suffix}"
                ) from exc
            except (TimeoutError, URLError, ConnectionError) as exc:
                if attempt + 1 < HTTP_ATTEMPTS:
                    _sleep_before_retry(attempt, None)
                    continue
                reason = getattr(exc, "reason", exc)
                raise UpdateError(f"{label} failed after retries: {reason}") from exc

        raise AssertionError("HTTP retry loop exhausted unexpectedly")

    def get_json(
        self,
        url: str,
        *,
        label: str,
    ) -> tuple[Any, HTTPMessage]:
        payload, headers = self.request(url, label=label)
        try:
            return json.loads(payload), headers
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise UpdateError(f"{label} returned invalid JSON") from exc


def _bounded_read(response: Any, label: str) -> bytes:
    payload = response.read(MAX_RESPONSE_BYTES + 1)
    if len(payload) > MAX_RESPONSE_BYTES:
        raise UpdateError(f"{label} response exceeds {MAX_RESPONSE_BYTES} bytes")
    return payload


def _read_http_error_body(error: HTTPError) -> bytes:
    try:
        return error.read(64 * 1024)
    except OSError:
        return b""


def _compact_http_body(body: bytes) -> str:
    text = body.decode("utf-8", errors="replace")
    text = " ".join(text.split())
    return text[:500]


def _retry_after_seconds(value: str | None) -> float | None:
    if value is None:
        return None

    stripped = value.strip()
    if stripped.isdecimal():
        return max(0.0, float(stripped))

    try:
        target = parsedate_to_datetime(stripped)
        if target.tzinfo is None:
            target = target.replace(tzinfo=UTC)
        return max(0.0, (target - datetime.now(UTC)).total_seconds())
    except (TypeError, ValueError, OverflowError):
        return None


def _sleep_before_retry(attempt: int, retry_after: str | None) -> None:
    server_delay = _retry_after_seconds(retry_after)
    exponential_delay = min(30.0, 0.5 * (2**attempt))
    jitter = random.uniform(0.0, 0.25 * exponential_delay)
    time.sleep(max(server_delay or 0.0, exponential_delay + jitter))


class GitHubClient:
    def __init__(self, token: str | None) -> None:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
            "User-Agent": "HyprlandRPM-update/2",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._http = HttpClient(headers)

    def get_json(
        self,
        endpoint: str,
        *,
        label: str,
        query: Mapping[str, str | int] | None = None,
    ) -> tuple[Any, HTTPMessage]:
        url = f"{GITHUB_API}{endpoint}"
        if query:
            url = f"{url}?{urlencode(query)}"
        return self._http.get_json(url, label=label)

    def latest_release(self) -> str:
        payload, _ = self.get_json(
            f"/repos/{GITHUB_REPOSITORY}/releases/latest",
            label="fetching the latest GitHub release",
        )
        release = _expect_mapping(payload, "latest release")
        tag = _expect_string(release.get("tag_name"), "latest release tag")
        return tag.removeprefix("v")

    def main_commit(self) -> tuple[str, str, str]:
        payload, _ = self.get_json(
            f"/repos/{GITHUB_REPOSITORY}/commits/main",
            label="fetching the main-branch commit",
        )
        root = _expect_mapping(payload, "main commit")
        sha = _expect_sha(root.get("sha"), "Hyprland commit")

        commit = _expect_mapping(root.get("commit"), "main commit metadata")
        committer = _expect_mapping(commit.get("committer"), "main commit committer")
        timestamp = _expect_string(committer.get("date"), "main commit date")
        message = _expect_text(commit.get("message"), "main commit message")
        return sha, timestamp, normalize_commit_title(message)

    def commit_count(self, commit: str) -> int:
        payload, headers = self.get_json(
            f"/repos/{GITHUB_REPOSITORY}/commits",
            label="counting Hyprland commits",
            query={"sha": commit, "per_page": 1},
        )
        commits = _expect_list(payload, "commit list")

        last_page = _last_page_from_link_header(headers.get("Link"))
        count = last_page if last_page is not None else len(commits)
        if count < 0:
            fail(f"invalid Hyprland commit count: {count}")
        return count

    def submodule_commit(self, path: str, commit: str) -> str:
        payload, _ = self.get_json(
            f"/repos/{GITHUB_REPOSITORY}/contents/{path}",
            label=f"fetching {path} commit",
            query={"ref": commit},
        )
        content = _expect_mapping(payload, f"GitHub content entry for {path}")
        return _expect_sha(content.get("sha"), f"{path} commit")


def _expect_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        fail(f"{label} is not a JSON object")
    return value


def _expect_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        fail(f"{label} is not a JSON array")
    return value


def _expect_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        fail(f"{label} is missing or invalid")
    return value


def _expect_string(value: Any, label: str) -> str:
    text = _expect_text(value, label)
    if "\n" in text or "\r" in text:
        fail(f"{label} must be one line")
    return text


def _expect_sha(value: Any, label: str) -> str:
    sha = _expect_string(value, label)
    if SHA1_RE.fullmatch(sha) is None:
        fail(f"invalid {label} returned by GitHub: {sha!r}")
    return sha


def _last_page_from_link_header(link_header: str | None) -> int | None:
    if not link_header:
        return None

    for link in link_header.split(","):
        match = re.fullmatch(r'\s*<(?P<url>[^>]+)>\s*;\s*rel="(?P<rel>[^"]+)"\s*', link)
        if match is None or "last" not in match.group("rel").split():
            continue

        query = parse_qs(urlsplit(match.group("url")).query)
        pages = query.get("page", [])
        if len(pages) != 1 or not pages[0].isdecimal():
            fail("GitHub pagination header contains an invalid last page")
        return int(pages[0])

    return None


def normalize_commit_title(message: str) -> str:
    """Return a bounded, single-line title suitable for package metadata."""

    lines = message.splitlines()
    title = unicodedata.normalize("NFC", lines[0] if lines else "")
    title = "".join(
        " " if unicodedata.category(character) == "Cc" else character
        for character in title
    )
    title = " ".join(title.split())

    if not title:
        fail("the upstream commit title is empty after normalization")

    size = len(title.encode("utf-8"))
    if size > MAX_COMMIT_TITLE_BYTES:
        fail(f"the upstream commit title exceeds {MAX_COMMIT_TITLE_BYTES} UTF-8 bytes")

    return title


def encode_text_base64(value: str) -> str:
    """Encode arbitrary UTF-8 text into an RPM-macro-safe alphabet."""

    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def format_commit_date(iso_timestamp: str) -> str:
    try:
        timestamp = datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00"))
    except ValueError as exc:
        raise UpdateError(
            f"invalid commit timestamp returned by GitHub: {iso_timestamp!r}"
        ) from exc

    if timestamp.tzinfo is None:
        fail(f"commit timestamp has no UTC offset: {iso_timestamp!r}")

    try:
        eastern = timestamp.astimezone(ZoneInfo("America/New_York"))
    except ZoneInfoNotFoundError as exc:
        raise UpdateError(
            "IANA timezone data for America/New_York is unavailable"
        ) from exc

    return (
        f"{WEEKDAY_ABBR[eastern.weekday()]} "
        f"{MONTH_ABBR[eastern.month]} "
        f"{eastern.day:02d} "
        f"{eastern.hour:02d}:{eastern.minute:02d}:{eastern.second:02d} "
        f"{eastern.year:04d}"
    )


def compare_rpm_versions(old_version: str, new_version: str) -> int:
    result = run_command(
        ["rpmdev-vercmp", old_version, new_version],
        check=False,
        capture_output=True,
    )
    if result.returncode not in {0, 11, 12}:
        details = (result.stderr or "").strip() or (result.stdout or "").strip()
        suffix = f": {details}" if details else ""
        fail(f"rpmdev-vercmp failed with status {result.returncode}{suffix}")
    return result.returncode


def extract_shell_scriptlets(expanded_spec: str) -> dict[str, str]:
    """Extract shell-backed build sections from an expanded RPM spec."""

    sections: dict[str, list[str]] = {}
    current: str | None = None

    for line in expanded_spec.splitlines(keepends=True):
        match = SPEC_SECTION_RE.match(line)
        if match is not None:
            section = match.group("name")
            current = section if section in SHELL_SCRIPTLET_NAMES else None
            if current is not None:
                sections.setdefault(current, [])
            continue

        if current is not None:
            sections[current].append(line)

    return {
        section: "".join(lines)
        for section, lines in sections.items()
        if any(line.strip() for line in lines)
    }


def validate_shell_scriptlets(path: Path, expanded_spec: str) -> None:
    """Reject malformed generated shell before committing or invoking COPR."""

    for section, script in extract_shell_scriptlets(expanded_spec).items():
        result = run_command(
            ["/bin/sh", "-n"],
            check=False,
            capture_output=True,
            input_text=script,
        )
        if result.returncode == 0:
            continue

        details = (result.stderr or "").strip()
        suffix = f": {details}" if details else ""
        fail(f"{path}: invalid %{section} shell syntax{suffix}")


def validate_specs(specs: Sequence[SpecDocument]) -> None:
    paths = [spec.path for spec in specs]
    run_git(["diff", "--check", "--", *paths])

    for spec in specs:
        expanded_spec = run_command(
            ["rpmspec", "-P", spec.path],
            capture_output=True,
        ).stdout
        validate_shell_scriptlets(spec.path, expanded_spec)


def restore_specs(specs: Sequence[SpecDocument]) -> None:
    restoration_errors: list[str] = []
    for spec in specs:
        try:
            spec.restore()
        except UpdateError as exc:
            restoration_errors.append(str(exc))
    if restoration_errors:
        raise UpdateError(
            "failed to restore spec files after validation error: "
            + "; ".join(restoration_errors)
        )


def show_git_diff(paths: Sequence[Path]) -> None:
    """Print the managed diff without invoking a pager or external diff driver."""

    run_git(
        [
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--",
            *paths,
        ]
    )


def finish_dry_run(
    specs: Sequence[SpecDocument],
    paths: Sequence[Path],
) -> None:
    """Display the validated diff and always restore the working tree."""

    try:
        show_git_diff(paths)
    except BaseException as display_error:
        try:
            restore_specs(specs)
        except BaseException as restore_error:
            raise BaseExceptionGroup(
                "dry-run diff failed and spec restoration also failed",
                [display_error, restore_error],
            ) from None
        raise
    else:
        restore_specs(specs)


def trigger_copr_build(package: str, webhook_id: str, webhook_token: str) -> None:
    # Keep this URL out of diagnostics because it embeds credentials.
    url = f"{COPR_BASE_URL}/{webhook_id}/{webhook_token}/{package}"
    client = HttpClient({"User-Agent": "HyprlandRPM-update/2"})
    payload, _ = client.request(
        url,
        label=f"triggering the {package} COPR build",
        method="POST",
        data=b"",
    )
    build_id = payload.decode("utf-8", errors="replace").strip()
    if BUILD_ID_RE.fullmatch(build_id) is None:
        fail(f"unexpected COPR webhook response for {package}: {build_id!r}")
    run_command(["copr", "watch-build", build_id])


def require_secret(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        fail(f"{name} must be set")
    if any(character in value for character in "\r\n/"):
        fail(f"{name} contains an invalid character")
    return value


def update(config: Config) -> int:
    require_commands(("git", "rpmdev-vercmp", "rpmspec"))

    git_spec = SpecDocument.load(config.git_spec)
    release_spec = (
        SpecDocument.load(config.release_spec)
        if config.release_spec.is_file()
        else None
    )
    managed_specs = [git_spec]
    if release_spec is not None:
        managed_specs.append(release_spec)

    managed_paths = [spec.path for spec in managed_specs]
    if not git_diff_is_quiet(managed_paths):
        fail("managed spec files have uncommitted changes")
    if not git_diff_is_quiet(managed_paths, cached=True):
        fail("managed spec files have staged changes")

    old_tag = git_spec.get_global("upstream_version")

    log("Fetching upstream release and main-branch metadata")
    github = GitHubClient(config.github_token)
    new_tag = github.latest_release()
    new_commit, commit_timestamp, commit_title = github.main_commit()
    commit_count = github.commit_count(new_commit)
    commit_date = format_commit_date(commit_timestamp)
    protocols_commit = github.submodule_commit(
        "subprojects/hyprland-protocols", new_commit
    )
    udis86_commit = github.submodule_commit("subprojects/udis86", new_commit)

    git_spec.update_globals(
        {
            "hyprland_commit": new_commit,
            "hyprland_commits": str(commit_count),
            "hyprland_commit_date": commit_date,
            "hyprland_commit_message_b64": encode_text_base64(commit_title),
            "protocols_commit": protocols_commit,
            "udis86_commit": udis86_commit,
        }
    )

    new_release = False
    comparison = compare_rpm_versions(old_tag, new_tag)
    match comparison:
        case 0:
            pass
        case 12:
            log(f"Detected new upstream release: {old_tag} -> {new_tag}")
            git_spec.update_globals(
                {
                    "upstream_version": new_tag,
                    "snapshot": "0",
                }
            )
            if release_spec is not None:
                release_spec.update_globals({"upstream_version": new_tag})
            new_release = True
        case 11:
            fail(f"configured version {old_tag} is newer than latest release {new_tag}")
        case _:
            raise AssertionError(f"unexpected comparison status: {comparison}")

    if not any(spec.changed for spec in managed_specs):
        log("Already up to date")
        return 0

    snapshot_text = git_spec.get_global("snapshot")
    if not snapshot_text.isdecimal():
        fail(f"invalid snapshot value: {snapshot_text!r}")
    git_spec.update_globals({"snapshot": str(int(snapshot_text) + 1)})

    try:
        for spec in managed_specs:
            spec.write()
        log("Validating updated spec files")
        validate_specs(managed_specs)
    except BaseException:
        restore_specs(managed_specs)
        raise

    if config.dry_run:
        log("Dry run; displaying validated changes")
        finish_dry_run(managed_specs, managed_paths)
        log("Dry run complete; restored managed spec files")
        return 0

    require_commands(("copr",))
    webhook_id = require_secret("COPR_WEBHOOK_ID")
    webhook_token = require_secret("COPR_WEBHOOK_TOKEN")

    log("Committing and pushing the update")
    run_git(["add", "--", *managed_paths])
    run_git(
        [
            "commit",
            "-m",
            f"up rev hyprland-git-{new_tag}+{new_commit[:7]}",
        ]
    )
    run_git(["push"])

    log("Starting the hyprland-git COPR build")
    trigger_copr_build("hyprland-git", webhook_id, webhook_token)

    if new_release:
        log("Starting the release Hyprland COPR build")
        trigger_copr_build("hyprland", webhook_id, webhook_token)

    return 0


def main() -> int:
    require_python()
    config = parse_arguments()
    return update(config)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("error: interrupted", file=sys.stderr)
        raise SystemExit(130) from None
    except UpdateError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
