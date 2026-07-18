#!/usr/bin/env python3
import argparse
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

"""
This script checks specfile versions vs upstream GitHub.

Usage:
    ./scripts/check-upstream-versions.py [--debug] [DIR]

If DIR is omitted, it defaults to the current directory.
Debug messages are written to stderr.
"""

LOGGER = logging.getLogger(__name__)
HTTP_TIMEOUT_SECONDS = 10
GITHUB_API_VERSION = "2022-11-28"

SPEC_VERSION_PATTERN = re.compile(
    r"^\s*Version\s*:\s*(?P<version>\S+)",
    re.MULTILINE,
)

SPEC_URL_PATTERN = re.compile(
    r"^\s*(?:URL\d*)\s*:\s*(?P<url>\S+)",
    re.MULTILINE,
)


@dataclass(frozen=True)
class ResultRow:
    """One row in the final results table."""

    package: str
    spec: str
    upstream: str
    status: str
    source: str = ""
    details: str = ""


TABLE_COLUMNS = (
    ("Package", "package", 30),
    ("Spec", "spec", 8),
    ("Upstream", "upstream", 8),
    ("Status", "status", 8),
    ("Source", "source", 32),
    ("Details", "details", 72),
)


def parse_args(argv=None):
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Check RPM spec versions against the latest GitHub release or tag.",
    )
    parser.add_argument(
        "directory",
        nargs="?",
        default=Path("."),
        type=Path,
        metavar="DIR",
        help=(
            "directory to scan recursively for .spec files (default: current directory)"
        ),
    )
    parser.add_argument(
        "-d",
        "--debug",
        action="store_true",
        help="emit detailed diagnostic logging to stderr",
    )
    return parser.parse_args(argv)


def configure_logging(debug: bool) -> None:
    """Enable concise debug logging."""
    if not debug:
        return

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    LOGGER.debug("Debug logging enabled")


def parse_spec(spec_path: Path):
    """
    Return (version, url) from a spec file.

    - version: value of the first 'Version:' line
    - url: value of the first 'URL:' or 'URL<n>:' field
    """
    LOGGER.debug("Reading spec file: %s", spec_path)

    spec_version = None
    spec_url = ""
    spec_file_content = spec_path.read_text(encoding="utf-8")

    match = SPEC_VERSION_PATTERN.search(spec_file_content)
    if match:
        spec_version = match.group("version")

    match = SPEC_URL_PATTERN.search(spec_file_content)
    if match:
        spec_url = match.group("url")

    LOGGER.debug(
        "Parsed spec file %s: version=%r url=%r",
        spec_path,
        spec_version,
        spec_url,
    )
    return spec_version, spec_url


def extract_repo_info(url: str):
    """
    Try to detect (owner, repo) from a URL.

    owner: first path component
    repo: second path component (without .git)
    """
    parsed = urlparse(url)
    path_parts = [part for part in parsed.path.strip("/").split("/") if part]

    LOGGER.debug(
        "Parsing upstream URL: url=%r scheme=%r host=%r path=%r",
        url,
        parsed.scheme,
        parsed.netloc,
        parsed.path,
    )

    if len(path_parts) < 2:
        LOGGER.debug("URL does not contain both an owner and repository: %r", url)
        return None

    owner, repo = path_parts[0], path_parts[1]
    if repo.endswith(".git"):
        repo = repo[:-4]

    LOGGER.debug("Resolved repository: %s/%s", owner, repo)
    return (owner, repo)


def _log_http_result(
    url: str, status, headers, elapsed_seconds: float, size=None
) -> None:
    """Log non-sensitive HTTP response metadata."""
    LOGGER.debug(
        "HTTP response: status=%s elapsed=%.3fs bytes=%s request_id=%s "
        "rate_remaining=%s rate_reset=%s url=%s",
        status,
        elapsed_seconds,
        size if size is not None else "?",
        headers.get("X-GitHub-Request-Id", "-") if headers else "-",
        headers.get("X-RateLimit-Remaining", "-") if headers else "-",
        headers.get("X-RateLimit-Reset", "-") if headers else "-",
        url,
    )


def describe_http_error(error: HTTPError) -> str:
    """Return a concise, table-friendly description of a GitHub HTTP error."""
    headers = error.headers or {}
    remaining = headers.get("X-RateLimit-Remaining")
    reset = headers.get("X-RateLimit-Reset")

    if error.code == 403 and remaining == "0":
        detail = "GitHub rate limit exceeded"
        if reset and reset.isdigit():
            reset_time = datetime.fromtimestamp(int(reset), tz=timezone.utc).strftime(
                "%Y-%m-%d %H:%M UTC"
            )
            detail += f"; resets {reset_time}"
        return detail

    reason = str(error.reason).strip() if error.reason else "request failed"
    return f"GitHub HTTP {error.code}: {reason}"


def http_json(url: str):
    """GET a URL and return decoded JSON, or raise the original exception."""
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "check-upstream-versions.py",
        "X-GitHub-Api-Version": GITHUB_API_VERSION,
    }
    request = Request(url, headers=headers)

    LOGGER.debug(
        "HTTP request: method=GET timeout=%ss url=%s",
        HTTP_TIMEOUT_SECONDS,
        url,
    )
    started = time.monotonic()

    try:
        with urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            data = response.read()
            elapsed = time.monotonic() - started
            _log_http_result(
                url,
                getattr(response, "status", "?"),
                response.headers,
                elapsed,
                len(data),
            )
    except HTTPError as error:
        elapsed = time.monotonic() - started
        _log_http_result(url, error.code, error.headers, elapsed)
        LOGGER.debug("HTTP error reason: %s", describe_http_error(error))
        raise
    except URLError as error:
        elapsed = time.monotonic() - started
        LOGGER.debug(
            "Network error after %.3fs: reason=%r url=%s",
            elapsed,
            error.reason,
            url,
        )
        raise

    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        LOGGER.debug(
            "GitHub response was not valid UTF-8 JSON: url=%s bytes=%d",
            url,
            len(data),
            exc_info=True,
        )
        raise


def github_latest_version(owner: str, repo: str):
    """
    Return (github_tag, error).

    - First try /releases/latest.
    - If that fails with 404 or 403, fall back to /tags (first tag).
    """
    repository = f"{owner}/{repo}"
    releases_url = f"https://api.github.com/repos/{repository}/releases/latest"

    LOGGER.debug("Checking latest release for %s", repository)
    try:
        data = http_json(releases_url)
        if isinstance(data, dict):
            tag = data.get("tag_name") or data.get("name")
            if tag:
                LOGGER.debug("Latest release for %s: %r", repository, tag)
                return tag, None
        LOGGER.debug("Latest-release response for %s did not contain a tag", repository)
    except HTTPError as error:
        # 404 means "no releases". A 403 may be access control or rate limiting.
        # Preserve the original behavior and try the tags endpoint in both cases.
        if error.code not in (404, 403):
            detail = describe_http_error(error)
            LOGGER.debug("Latest-release request failed for %s: %s", repository, detail)
            return None, detail
        LOGGER.debug(
            "Falling back to tags for %s after latest-release HTTP %s",
            repository,
            error.code,
        )
    except URLError as error:
        LOGGER.debug(
            "Latest-release network failure for %s: %s",
            repository,
            error.reason,
        )
        return None, f"network error: {error.reason}"
    except Exception as error:
        LOGGER.debug(
            "Unexpected latest-release failure for %s",
            repository,
            exc_info=True,
        )
        return None, f"error: {error}"

    tags_url = f"https://api.github.com/repos/{repository}/tags?per_page=1"
    LOGGER.debug("Checking latest tag for %s", repository)
    try:
        data = http_json(tags_url)
        if isinstance(data, list) and data:
            first_tag = data[0]
            if isinstance(first_tag, dict):
                tag = first_tag.get("name")
                if tag:
                    LOGGER.debug("Latest tag for %s: %r", repository, tag)
                    return tag, None
        LOGGER.debug("No tags found for %s", repository)
        return None, "no tags"
    except HTTPError as error:
        detail = describe_http_error(error)
        LOGGER.debug("Tags request failed for %s: %s", repository, detail)
        return None, detail
    except URLError as error:
        LOGGER.debug("Tags network failure for %s: %s", repository, error.reason)
        return None, f"network error: {error.reason}"
    except Exception as error:
        LOGGER.debug("Unexpected tags failure for %s", repository, exc_info=True)
        return None, f"error: {error}"


def normalize_tag(tag: str) -> str:
    """Strip leading 'v' or 'V' characters from a version tag."""
    return tag.lstrip("vV")


def _clip(value: str, width: int) -> str:
    """Clip a value to a table column width without breaking alignment."""
    if len(value) <= width:
        return value
    if width <= 3:
        return value[:width]
    return value[: width - 3] + "..."


def render_table_header() -> None:
    """Print the fixed-width table header used by streaming rows."""
    header = "  ".join(
        heading.ljust(width) for heading, _attribute, width in TABLE_COLUMNS
    ).rstrip()
    separator = "  ".join("-" * width for _heading, _attribute, width in TABLE_COLUMNS)
    print(header)
    print(separator)


def render_row(row: ResultRow) -> None:
    """Print one result immediately, keeping progress visible."""
    line = "  ".join(
        _clip(str(getattr(row, attribute)), width).ljust(width)
        for _heading, attribute, width in TABLE_COLUMNS
    ).rstrip()
    print(line, flush=True)


def main(argv=None):
    args = parse_args(argv)
    configure_logging(args.debug)

    root_dir = args.directory.expanduser().resolve()
    LOGGER.debug("Scanning root directory: %s", root_dir)

    excluded_packages: set[str] = {
        "hyprland.spec",
        "hyprland-git.spec",
        "hyprland-contrib.spec",
    }
    spec_files = sorted(
        path for path in root_dir.rglob("*.spec") if path.name not in excluded_packages
    )
    LOGGER.debug(
        "Discovered %d eligible spec file(s); excluded names=%s",
        len(spec_files),
        ", ".join(sorted(excluded_packages)),
    )

    if not spec_files:
        print(f"No .spec files found under {root_dir}")
        return 1

    status_counts: dict[str, int] = {}

    def add_row(row: ResultRow) -> None:
        status_counts[row.status] = status_counts.get(row.status, 0) + 1
        render_row(row)

    render_table_header()

    for spec_file in spec_files:
        pkg_name = spec_file.parent.name
        LOGGER.debug("Checking package=%s spec=%s", pkg_name, spec_file)

        try:
            spec_version, spec_url = parse_spec(spec_file)
        except (OSError, UnicodeError) as error:
            LOGGER.debug("Unable to read spec file %s: %s", spec_file, error)
            add_row(
                ResultRow(
                    package=pkg_name,
                    spec="?",
                    upstream="?",
                    status="ERROR",
                    source=str(spec_file.relative_to(root_dir)),
                    details=f"Can't read spec file: {error}",
                )
            )
            continue

        if not spec_version:
            add_row(
                ResultRow(
                    package=pkg_name,
                    spec="?",
                    upstream="?",
                    status="NO_VER",
                    source=str(spec_file.relative_to(root_dir)),
                    details="Can't parse 'Version' in spec file",
                )
            )
            continue

        repo_info = extract_repo_info(spec_url)
        if not repo_info:
            add_row(
                ResultRow(
                    package=pkg_name,
                    spec=spec_version,
                    upstream="?",
                    status="NO_URL",
                    source=spec_url or str(spec_file.relative_to(root_dir)),
                    details="Can't parse upstream URL",
                )
            )
            continue

        repo_owner, repo_name = repo_info
        source = f"{repo_owner}/{repo_name}"
        upstream_version, error = github_latest_version(repo_owner, repo_name)

        if not upstream_version:
            add_row(
                ResultRow(
                    package=pkg_name,
                    spec=spec_version,
                    upstream="?",
                    status="ERROR",
                    source=source,
                    details=error or "unknown error",
                )
            )
            continue

        spec_normalized_version = normalize_tag(spec_version)
        upstream_normalized_version = normalize_tag(upstream_version)
        status = (
            "OK" if spec_normalized_version == upstream_normalized_version else "DIFF"
        )

        LOGGER.debug(
            "Version comparison for %s/%s: spec=%r normalized=%r "
            "upstream=%r normalized=%r status=%s",
            repo_owner,
            repo_name,
            spec_version,
            spec_normalized_version,
            upstream_version,
            upstream_normalized_version,
            status,
        )

        add_row(
            ResultRow(
                package=pkg_name,
                spec=spec_version,
                upstream=upstream_version,
                status=status,
                source=source,
            )
        )

    LOGGER.debug(
        "Completed scan: %s",
        ", ".join(
            f"{status}={count}" for status, count in sorted(status_counts.items())
        )
        or "no results",
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
