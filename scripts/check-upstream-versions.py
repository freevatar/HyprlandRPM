#!/usr/bin/env python3
import json
import re
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

"""
This script checks specfile versions vs upstream GitHub

Usage:
    ./scripts/check-upstream-versions.py [DIR]

If DIR is omitted, it defaults to the current directory.
"""

SPEC_VERSION_PATTERN = re.compile(
    r"^\s*Version\s*:\s*(?P<version>\S+)",
    re.MULTILINE,
)

SPEC_URL_PATTERN = re.compile(
    r"^\s*(?:URL\d*)\s*:\s*(?P<url>\S+)",
    re.MULTILINE,
)


def parse_spec(spec_path: Path):
    """
    Return (version, url) from a spec file

    - version: value of the first 'Version:' line
    - url: value of 'URL:' field
    """
    spec_version = None
    spec_url = ""

    spec_file_content = spec_path.read_text(encoding="utf-8")

    match = SPEC_VERSION_PATTERN.search(spec_file_content)
    if match:
        spec_version = match.group("version")

    match = SPEC_URL_PATTERN.search(spec_file_content)
    if match:
        spec_url = match.group("url")

    return spec_version, spec_url


def extract_repo_info(url: str):
    """
    Try to detect (owner, repo) from a URL

    owner: first path component
    repo: second path component (without .git)
    """

    parsed = urlparse(url)

    path_parts = [path_part for path_part in parsed.path.strip("/").split("/")]

    if len(path_parts) < 2:
        return None

    owner, repo = path_parts[0], path_parts[1]
    if repo.endswith(".git"):
        repo = repo[:-4]

    return (owner, repo)


def http_json(url: str):
    """GET URL and return JSON or raise exception"""
    headers = {
        "Accept": "application/vnd.github+json, application/json",
    }
    req = Request(url, headers=headers)
    with urlopen(req, timeout=10) as resp:
        data = resp.read()
    return json.loads(data.decode("utf-8"))


def github_latest_version(owner: str, repo: str):
    """
    Return (github_tag, error)

    - First try /releases/latest.
    - If that fails, fall back to /tags (first tag)
    """

    # 1) releases/latest
    try:
        data = http_json(f"https://api.github.com/repos/{owner}/{repo}/releases/latest")
        if isinstance(data, dict):
            tag = data.get("tag_name") or data.get("name")
            if tag:
                return tag, None
    except HTTPError as e:
        # 404 means "no releases"
        # 403 could be "no access" or rate limiting
        # Fall back to tags
        if e.code not in (404, 403):
            return None, f"GitHub HTTP {e.code}"
    except URLError as e:
        return None, f"network error: {e.reason}"
    except Exception as e:
        return None, f"error: {e}"

    # 2) tags
    try:
        data = http_json(f"https://api.github.com/repos/{owner}/{repo}/tags")
        if isinstance(data, list) and data:
            first_tag = data[0]
            tag = first_tag.get("name")
            if tag:
                return tag, None
        return None, "no tags"
    except HTTPError as e:
        return None, f"GitHub HTTP {e.code}"
    except URLError as e:
        return None, f"network error: {e.reason}"
    except Exception as e:
        return None, f"error: {e}"


def normalize_tag(tag: str) -> str:
    """Strip 'v' from tags"""
    return tag.lstrip("vV")


def main():
    if len(sys.argv) > 1:
        root_dir = Path(sys.argv[1]).resolve()
    else:
        root_dir = Path(".").resolve()

    spec_files = sorted(root_dir.rglob("*.spec"))
    if not spec_files:
        print(f"No .spec files found under {root_dir}")
        return 1

    output_header = f"{'Package':25} {'Spec':18} {'Upstream':18} {'Status':8} Source"
    print(output_header)
    print("-" * len(output_header))

    for spec_file in spec_files:
        pkg_name = spec_file.parent.name

        spec_version, spec_url = parse_spec(spec_file)
        if not spec_version:
            print(
                f"{pkg_name:25} {'?':18} {'?':18} {'NO_VER':8} (Can't parse 'Version' in spec file)"
            )
            continue

        repo_info = extract_repo_info(spec_url)

        if not repo_info:
            print(
                f"{pkg_name:25} {spec_version:18} {'?':18} {'NO_URL':8} (Can't parse 'upstream URL')"
            )
            continue

        repo_owner, repo_name = repo_info

        upstream_version, err = github_latest_version(repo_owner, repo_name)

        if not upstream_version:
            print(
                f"{pkg_name:25} {spec_version:18} {'?':18} {'ERROR':8} {err or 'unknown error'}"
            )
            continue

        spec_normalized_version = normalize_tag(spec_version)
        upstream_normalized_version = normalize_tag(upstream_version)
        status = (
            "OK" if spec_normalized_version == upstream_normalized_version else "DIFF"
        )

        source_str = f"{repo_owner}/{repo_name}"
        print(
            f"{pkg_name:25} {spec_version:18} {upstream_version:18} {status:8} {source_str}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
