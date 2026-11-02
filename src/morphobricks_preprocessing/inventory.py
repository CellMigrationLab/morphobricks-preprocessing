"""A source image's fingerprint as it is on disk now (contract §9, design v0.4 §1).

Moved from :mod:`.manifest` (PP-12): the size and modification time that tell whether an
image changed. :mod:`.manifest` re-exports :func:`source_fingerprint` and
:data:`LABELS_GROUP` from here.
"""

from __future__ import annotations

import os
from pathlib import Path

LABELS_GROUP = "labels"
"""An OME-Zarr image's group of label images (masks): outside the image's fingerprint."""


def source_fingerprint(path: str | os.PathLike) -> tuple[int, int]:
    """Size and modification time used to tell whether an image changed.

    A TIFF file gives its own size and time. An OME-Zarr folder gives the total
    size of its files and the time of the newest one, so rewriting any chunk or
    metadata file counts as a change. Its ``labels`` group is left out: label
    images are masks, fingerprinted one by one (design v0.4 §2), so adding or
    changing one does not change the image.
    """
    path = Path(path)
    if not path.is_dir():
        stat = path.stat()
        return stat.st_size, stat.st_mtime_ns
    size, newest = 0, path.stat().st_mtime_ns
    for folder, subfolders, names in os.walk(path):
        if Path(folder) == path and LABELS_GROUP in subfolders:
            subfolders.remove(LABELS_GROUP)
        for name in names:
            stat = (Path(folder) / name).stat()
            size += stat.st_size
            newest = max(newest, stat.st_mtime_ns)
    return size, newest
