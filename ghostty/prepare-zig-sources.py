#!/usr/bin/python3
"""Collect Ghostty's pinned sources for the offline RPM build."""

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tempfile
from urllib.parse import urlsplit


def download(url, directory):
    archive = directory / Path(urlsplit(url).path).name
    subprocess.run(["curl", "--fail", "--silent", "--show-error", "--location", "--retry", "3",
                    "--connect-timeout", "20", "--speed-limit", "1", "--speed-time", "60",
                    "--output", archive, url], check=True)
    return archive


def collect_dependencies(release, work):
    # The release's Flatpak manifest pins each archive digest or Git commit.
    name = release.name.removesuffix(".tar.gz")
    with tarfile.open(release) as sources:
        packages = json.load(sources.extractfile(f"{name}/flatpak/zig-packages.json"))

    git = ["git", "-C", work / "git"]
    subprocess.run(["git", "init", "--bare", "--quiet", work / "git"], check=True)
    for package in packages:
        dest = work / "zig-sources" / Path(package["dest"]).name
        dest.mkdir(parents=True)
        if package["type"] == "archive":
            archive = download(package["url"], dest)
            subprocess.run(["sha256sum", "--check", "--strict"], cwd=dest, check=True,
                           input=f"{package['sha256']}  {archive.name}\n", text=True)
        elif package["type"] == "git":
            commit = package["commit"]
            subprocess.run([*git, "fetch", "--quiet", "--depth=1", package["url"], commit], check=True)
            subprocess.run([*git, "archive", "--format=tar.gz", "--prefix=source/",
                            "-o", dest / "source.tar.gz", commit], check=True)
        else:
            raise ValueError(f"Unsupported dependency type: {package['type']}")

    archive = work / f"{name}-deps.tar.zst"
    subprocess.run(["tar", "--sort=name", "--mtime=@0", "--owner=0", "--group=0",
                    "--numeric-owner", "-caf", archive, "zig-sources"], cwd=work, check=True)
    return archive


def main():
    os.umask(0o022)
    spec, outdir = (Path(arg).resolve() for arg in sys.argv[1:])
    parsed = subprocess.check_output(["rpmspec", "--target", "x86_64", "-P", spec], text=True)
    release_url, compiler_url = re.findall(r"^Source[01]:\s+(\S+)", parsed, re.MULTILINE)
    with tempfile.TemporaryDirectory(dir=outdir) as tmp:
        work = Path(tmp)
        release = download(release_url, work)
        compiler = download(compiler_url, work)
        subprocess.run(["sha256sum", "--check", "--strict", spec.with_name("sources.sha256")],
                       cwd=work, check=True)
        dependencies = collect_dependencies(release, work)
        for archive in (release, compiler, dependencies):
            archive.replace(outdir / archive.name)


if __name__ == "__main__":
    main()
