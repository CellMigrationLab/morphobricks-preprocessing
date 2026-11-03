"""A small experiment of three Z stacks for the content-verification tests.

Shared by ``test_verify_content.py``, ``test_strict_manifest.py`` and
``test_strict_final_check.py``.
"""

import os

import numpy as np

from morphobricks_preprocessing import RunMode, prefill_mapping
from tiff_tree import write_data

ZYX = prefill_mapping("ZYX")
IMAGES = ["ctrl/r1/a.tif", "ctrl/r1/b.tif", "drug/r1/a.tif"]
STRICT = RunMode("restart", verify_content=True)


def stack(seed):
    """Return a 2 x 8 x 8 uint8 stack of random pixels."""
    return np.random.default_rng(seed).integers(0, 255, (2, 8, 8), dtype=np.uint8)


def rewrite_keeping_metadata(path, seed=99):
    """Rewrite an image with other pixels, the same size, and its modification time back."""
    before = path.stat()
    write_data(path, stack(seed), "ZYX")
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    after = path.stat()
    assert (after.st_size, after.st_mtime_ns) == (before.st_size, before.st_mtime_ns)


def write_experiment(tmp_path):
    """Write the three images into ``tmp_path / "exp"``; return it and ``tmp_path / "out"``."""
    root = tmp_path / "exp"
    for seed, rel in enumerate(IMAGES, start=10):
        write_data(root / rel, stack(seed), "ZYX")
    return root, tmp_path / "out"
