"""Run the code of an earlier commit or release of this package in a child Python.

The package's ``src`` folder at that revision is taken with ``git archive`` (as
``tests/data/make_rc4_manifest.py`` does) into a temporary folder, put first on
``PYTHONPATH``, and a script is run with it. The script prints one JSON value, which is
returned. The installed dependencies are the current ones (the normalization pin has not
changed since v0.4.0rc4).

The revision must be in the clone: CI checks out the whole history (``fetch-depth: 0``).
Elsewhere a clone without it skips the test by name; in CI (``CI`` set) it fails instead,
so the cross-version check never passes by being skipped.
"""

import io
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RC4 = "v0.4.0rc4"
BEFORE_STRICT_VERSION = "a6eea53a7ce63b71757b3544fb05a76a4f90feb8"
"""main before manifest version 3 (v0.4.0rc5 with #145 merged): the default shape."""


def extract(revision, folder):
    """Extract ``src`` of ``revision`` into ``folder``; return the folder for PYTHONPATH."""
    done = subprocess.run(
        ["git", "-C", str(ROOT), "archive", "--format=tar", revision, "src"],
        capture_output=True,
        check=False,
    )
    if done.returncode:
        why = f"{revision} is not in this clone: {done.stderr.decode().strip()}"
        if os.environ.get("CI"):
            pytest.fail(why)
        pytest.skip(why)
    with tarfile.open(fileobj=io.BytesIO(done.stdout)) as archive:
        archive.extractall(folder, filter="data")
    return Path(folder) / "src"


def run_with(src, script, *args):
    """Run ``script`` with the package at ``src`` first on the path; return its JSON."""
    env = {**os.environ, "PYTHONPATH": str(src)}
    done = subprocess.run(
        [sys.executable, "-c", script, str(src), *map(str, args)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
        cwd=src,
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.strip().splitlines()[-1])


PRELUDE = """
import json, sys
from pathlib import Path
import morphobricks_preprocessing as package
src = Path(sys.argv[1]).resolve()
assert src in Path(package.__file__).resolve().parents, package.__file__
"""
"""Each script's start: the package imported must be the extracted one."""
