"""Helpers that build small experiment folders with TIFF files for tests."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import tifffile


def write_tiff(path, shape=(8, 8), dtype=np.uint16, axes=None, **kwargs):
    """Write a small TIFF, creating parent folders; ``axes`` is stored as metadata."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.zeros(shape, dtype=dtype)
    metadata = {"axes": axes} if axes is not None else {}
    # Without this, tifffile stores 3-sample stacks as RGB planes (deprecated behaviour).
    kwargs.setdefault("photometric", "minisblack")
    tifffile.imwrite(path, data, metadata=metadata, **kwargs)
    return path


def write_file(path, content=b"not an image"):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def make_tree(root, relative_paths):
    """Create files under ``root``: TIFF names get a small image, others text, ``/`` a folder."""
    root = Path(root)
    for rel in relative_paths:
        path = root / rel
        if rel.endswith("/"):
            path.mkdir(parents=True, exist_ok=True)
        elif rel.lower().endswith((".tif", ".tiff")):
            write_tiff(path)
        else:
            write_file(path)
    return root
