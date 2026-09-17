"""Local upstream cache behavior without network requests."""

from dataclasses import asdict
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import upstream_cache as cache
from github_versions import UpstreamVersion


def release(owner="example", repo="repo", tag="v2.1.0"):
    return UpstreamVersion(tag, tag.lstrip("vV"),
                           f"https://github.com/{owner}/{repo}/releases/tag/{tag}", "release")


class UpstreamCacheTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "cache" / "upstream.json"
        self.clock = patch.object(cache.time, "time", return_value=10000)
        self.now = self.clock.start()
        self.addCleanup(self.clock.stop)

    def write(self, entries, *, version=1):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"version": version, "entries": entries}))

    def record(self, value=None, timestamp=10000):
        return {"checked_at": timestamp, **asdict(value or release())}

    def test_persists_successful_versions_and_reuses_repository_casing(self):
        store = cache.VersionCache(self.path)
        value = release("Example", "Repo")
        store.put("Example", "Repo", value)
        store.save()
        reopened = cache.VersionCache(self.path)
        self.assertEqual(reopened.get("EXAMPLE", "REPO"), value)
        self.assertEqual(json.loads(self.path.read_text()), {
            "version": 1, "entries": {"example/repo|releases": self.record(value)},
        })

    def test_release_and_tag_policies_are_isolated(self):
        store = cache.VersionCache(self.path)
        value = release()
        tag = UpstreamVersion("v3.0", "3.0", "https://github.com/example/repo/tree/v3.0", "tag")
        store.put("example", "repo", value)
        self.assertIsNone(store.get("example", "repo", allow_tags=True))
        store.put("example", "repo", tag, allow_tags=True)
        store.save()
        reopened = cache.VersionCache(self.path)
        self.assertEqual(reopened.get("example", "repo"), value)
        self.assertEqual(reopened.get("example", "repo", allow_tags=True), tag)

    def test_expiry_boundary_and_future_timestamp_never_hit(self):
        for timestamp, expected in ((10000, True), (6401, True), (6400, False),
                                    (6399, False), (10001, False)):
            with self.subTest(timestamp=timestamp):
                self.write({"example/repo|releases": self.record(timestamp=timestamp)})
                self.assertEqual(cache.VersionCache(self.path).get("example", "repo") is not None,
                                 expected)

    def test_fresh_record_expires_while_cache_is_open(self):
        store = cache.VersionCache(self.path)
        store.put("example", "repo", release())
        self.now.return_value += cache.CACHE_TTL_SECONDS
        self.assertIsNone(store.get("example", "repo"))

    def test_corrupt_file_warns_without_disclosing_its_contents(self):
        self.path.parent.mkdir()
        self.path.write_text("fixture-secret\nnot json")
        with self.assertLogs(cache.LOGGER, level="WARNING") as logs:
            store = cache.VersionCache(self.path)
        self.assertIsNone(store.get("example", "repo"))
        self.assertNotIn("fixture-secret", "\n".join(logs.output))
        store.put("example", "repo", release())
        store.save()
        self.assertEqual(cache.VersionCache(self.path).get("example", "repo"), release())

    def test_invalid_schema_is_ignored(self):
        for data in ([], {}, {"version": True, "entries": {}},
                     {"version": 2, "entries": {}}, {"version": 1, "entries": []}):
            with self.subTest(data=data):
                self.path.parent.mkdir(exist_ok=True)
                self.path.write_text(json.dumps(data))
                with self.assertLogs(cache.LOGGER, level="WARNING"):
                    store = cache.VersionCache(self.path)
                self.assertIsNone(store.get("example", "repo"))

    def test_invalid_records_are_ignored_but_valid_records_survive(self):
        invalid = [None, [], {},
                   self.record() | {"checked_at": True},
                   self.record() | {"checked_at": "10000"},
                   self.record() | {"checked_at": float("nan")},
                   self.record() | {"checked_at": float("inf")},
                   self.record() | {"checked_at": 10 ** 400},
                   self.record() | {"tag": "v2.1.0-rc1"},
                   self.record() | {"version": "2.0"},
                   self.record() | {"version": 2},
                   self.record() | {"kind": "error"},
                   self.record() | {"url": "https://github.com.evil/example/repo/releases/tag/v2.1.0"},
                   self.record() | {"url": "https://github.com/example/other/releases/tag/v2.1.0"},
                   self.record() | {"token": "fixture-secret"},
                   self.record(UpstreamVersion("v2.1.0", "2.1.0",
                                              "https://github.com/example/repo/tree/v2.1.0", "tag"))]
        good = release(repo="good")
        for record in invalid:
            with self.subTest(record=record):
                self.write({"example/repo|releases": record,
                            "example/good|releases": self.record(good)})
                with self.assertLogs(cache.LOGGER, level="WARNING"):
                    store = cache.VersionCache(self.path)
                self.assertIsNone(store.get("example", "repo"))
                self.assertEqual(store.get("example", "good"), good)

    def test_invalid_repository_keys_are_ignored(self):
        for key in ("example/repo", "example/repo|unknown", "example/repo/extra|releases",
                    "example/repo|releases|tags", "Example/repo|releases"):
            with self.subTest(key=key):
                self.write({key: self.record()})
                with self.assertLogs(cache.LOGGER, level="WARNING"):
                    store = cache.VersionCache(self.path)
                self.assertIsNone(store.get("example", "repo"))

    def test_missing_cache_and_cache_hits_do_not_write_files(self):
        store = cache.VersionCache(self.path)
        store.save()
        self.assertFalse(self.path.parent.exists())
        self.write({"example/repo|releases": self.record()})
        original = self.path.read_bytes()
        store = cache.VersionCache(self.path)
        store.get("example", "repo")
        with patch.object(cache.os, "replace") as replace:
            store.save()
        replace.assert_not_called()
        self.assertEqual(self.path.read_bytes(), original)

    def test_new_success_preserves_other_valid_records(self):
        self.write({"example/repo|releases": self.record()})
        store = cache.VersionCache(self.path)
        other = release(repo="other")
        store.put("example", "other", other)
        store.save()
        reopened = cache.VersionCache(self.path)
        self.assertEqual(reopened.get("example", "repo"), release())
        self.assertEqual(reopened.get("example", "other"), other)

    def test_expired_records_remain_on_disk_until_successfully_refreshed(self):
        expired = self.record(timestamp=6400)
        self.write({"example/repo|releases": expired})
        original = self.path.read_bytes()
        store = cache.VersionCache(self.path)
        self.assertIsNone(store.get("example", "repo"))
        # A failed refresh does not put a new value and must leave the file alone.
        store.save()
        self.assertEqual(self.path.read_bytes(), original)
        store.put("example", "other", release(repo="other"))
        store.save()
        entries = json.loads(self.path.read_text())["entries"]
        self.assertEqual(entries["example/repo|releases"], expired)
        store.put("example", "repo", release(tag="v3.0"))
        store.save()
        self.assertEqual(cache.VersionCache(self.path).get("example", "repo"), release(tag="v3.0"))

    def test_unreadable_cache_does_not_fail_lookup_or_expose_error_contents(self):
        with patch.object(Path, "read_text", side_effect=PermissionError("fixture-secret")):
            with self.assertLogs(cache.LOGGER, level="WARNING") as logs:
                store = cache.VersionCache(self.path)
        self.assertIsNone(store.get("example", "repo"))
        self.assertNotIn("fixture-secret", "\n".join(logs.output))

    def test_failed_atomic_save_preserves_previous_cache_and_cleans_temporary_file(self):
        self.write({"example/repo|releases": self.record()})
        original = self.path.read_bytes()
        store = cache.VersionCache(self.path)
        value = release(tag="v3.0")
        store.put("example", "repo", value)
        with patch.object(cache.os, "replace", side_effect=PermissionError("fixture-secret")):
            with self.assertLogs(cache.LOGGER, level="WARNING") as logs:
                store.save()
        self.assertNotIn("fixture-secret", "\n".join(logs.output))
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])
        self.assertEqual(store.get("example", "repo"), value)
        store.save()
        self.assertEqual(cache.VersionCache(self.path).get("example", "repo"), value)


if __name__ == "__main__":
    unittest.main()
