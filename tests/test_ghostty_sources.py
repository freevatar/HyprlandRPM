"""Exercise the COPR source step with a target compiler that cannot run."""

import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest

PACKAGE = Path(__file__).resolve().parents[1] / "ghostty"


@unittest.skipUnless(all(shutil.which(tool) for tool in ("rpmspec", "curl", "git", "tar", "zstd")),
                     "requires Fedora source packaging tools")
class GhosttySourceTests(unittest.TestCase):
    def test_source_worker_only_collects_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outdir = root / "out"
            outdir.mkdir()
            compiler = root / "zig-x86_64-linux-test.tar.xz"
            compiler.write_bytes(b"opaque target toolchain")
            dependency = root / "dependency.tar.gz"
            dependency.write_bytes(b"upstream source archive")
            manifest = root / "zig-packages.json"
            manifest.write_text(json.dumps([{
                "type": "archive", "url": dependency.as_uri(), "dest": "vendor/p/example",
                "sha256": hashlib.sha256(dependency.read_bytes()).hexdigest(),
            }]))
            release = root / "ghostty-test.tar.gz"
            with tarfile.open(release, "w:gz") as archive:
                archive.add(manifest, arcname="ghostty-test/flatpak/zig-packages.json")

            spec = (PACKAGE / "ghostty.spec").read_text()
            for index, path in enumerate((release, compiler)):
                spec = re.sub(rf"(?m)^Source{index}:.*$", f"Source{index}: {path.as_uri()}", spec)
            (root / "ghostty.spec").write_text(spec)
            (root / "sources.sha256").write_text("".join(
                f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
                for path in (release, compiler)
            ))
            subprocess.run([sys.executable, PACKAGE / "prepare-zig-sources.py",
                            root / "ghostty.spec", outdir], check=True, capture_output=True)

            self.assertEqual((outdir / compiler.name).read_bytes(), compiler.read_bytes())
            contents = subprocess.check_output(["tar", "-xOf", outdir / "ghostty-test-deps.tar.zst",
                                                "zig-sources/example/dependency.tar.gz"])
            self.assertEqual(contents, dependency.read_bytes())
