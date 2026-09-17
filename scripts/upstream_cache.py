"""Persist successful upstream lookups and reuse them for one hour."""

from __future__ import annotations

from dataclasses import asdict
import json
import logging
import math
import os
from pathlib import Path
import re
import tempfile
import time

from github_versions import STABLE_TAG, UpstreamVersion


LOGGER = logging.getLogger(__name__)
CACHE_TTL_SECONDS = 3600
_REPOSITORY = re.compile(r"[a-z0-9][a-z0-9_.-]*/[a-z0-9][a-z0-9_.-]*", re.ASCII)


def _valid_record(key: object, record: object) -> bool:
    if not isinstance(key, str) or not isinstance(record, dict):
        return False
    repository, separator, policy = key.partition("|")
    if not separator or policy not in ("releases", "tags") or not _REPOSITORY.fullmatch(repository):
        return False
    if set(record) != {"checked_at", "tag", "version", "url", "kind"}:
        return False
    timestamp = record["checked_at"]
    try:
        if type(timestamp) not in (int, float) or not math.isfinite(timestamp):
            return False
    except OverflowError:
        return False
    if not all(isinstance(record[field], str) for field in ("tag", "version", "url", "kind")):
        return False
    match = STABLE_TAG.fullmatch(record["tag"])
    if not match or match[1] != record["version"]:
        return False
    if record["kind"] not in ("release", "tag") or policy == "releases" and record["kind"] == "tag":
        return False
    prefix = f"https://github.com/{repository}/"
    suffix = "releases/tag/" if record["kind"] == "release" else "tree/"
    return (record["url"][:len(prefix)].lower() == prefix
            and record["url"][len(prefix):] == suffix + record["tag"])


def _fresh(record: dict) -> bool:
    return 0 <= time.time() - record["checked_at"] < CACHE_TTL_SECONDS


class VersionCache:
    def __init__(self, path: Path):
        self.path = path
        self._entries: dict[str, dict] = {}
        self._dirty = False
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except OSError:
            LOGGER.warning("Cannot read upstream cache; checking GitHub instead")
            return
        except (UnicodeError, ValueError):
            LOGGER.warning("Ignoring corrupt upstream cache; checking GitHub instead")
            return
        if (not isinstance(data, dict) or type(data.get("version")) is not int
                or data["version"] != 1 or not isinstance(data.get("entries"), dict)):
            LOGGER.warning("Ignoring invalid upstream cache; checking GitHub instead")
            return
        invalid = False
        for key, record in data["entries"].items():
            if not _valid_record(key, record):
                invalid = True
            else:
                self._entries[key] = record
        if invalid:
            LOGGER.warning("Ignoring invalid upstream cache entries; checking GitHub for those packages")

    @staticmethod
    def _key(owner: str, repo: str, allow_tags: bool) -> str:
        return f"{owner.lower()}/{repo.lower()}|{'tags' if allow_tags else 'releases'}"

    def get(self, owner: str, repo: str, *, allow_tags: bool = False) -> UpstreamVersion | None:
        record = self._entries.get(self._key(owner, repo, allow_tags))
        if record is None or not _fresh(record):
            return None
        return UpstreamVersion(**{field: record[field] for field in ("tag", "version", "url", "kind")})

    def put(self, owner: str, repo: str, value: UpstreamVersion, *, allow_tags: bool = False) -> None:
        key = self._key(owner, repo, allow_tags)
        record = {"checked_at": time.time(), **asdict(value)}
        if not _valid_record(key, record):
            LOGGER.warning("Ignoring invalid upstream version cache entry")
            return
        self._entries[key] = record
        self._dirty = True

    def save(self) -> None:
        if not self._dirty:
            return
        temporary: Path | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                             prefix=f".{self.path.name}.", delete=False) as stream:
                temporary = Path(stream.name)
                json.dump({"version": 1, "entries": self._entries}, stream, indent=2, sort_keys=True)
                stream.write("\n")
            os.replace(temporary, self.path)
            self._dirty = False
        except OSError:
            LOGGER.warning("Cannot save upstream cache; results are still valid")
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
