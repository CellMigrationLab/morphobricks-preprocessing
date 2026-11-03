"""Mount points below the experiment and masks folders (owner decision, 3 November 2026).

A mount point below the folder the user selected brings in the files of another place:
another file system, or (a Linux bind mount) another folder of the same file system. To
the walk it is an ordinary folder, not a link, so the rule of :mod:`.links` ("nothing
outside the selected folder is read") would not see it. A folder (or file) below the
experiment folder or the masks folder that these rules find to be a mount point is refused
and named: the error ``mounted_folder`` (:class:`.links.LinkGuard`). The selected folder
itself may be on any mount.

A path below the selected folder is a mount point (:class:`MountCheck`) when

- it is listed as one in ``/proc/self/mountinfo`` (Linux): this is the only way to see a
  bind mount of the same file system, whose device and inode look like any folder's
  (:func:`os.path.ismount` reports False for it); or
- it is a folder, or an image or mask file, whose device (``st_dev``) is not the selected
  folder's: another file system (on Windows, another volume), or a Btrfs subvolume, which
  is refused too.

What is not seen: a mount made after the walk started (the table is read once per walk);
and where ``/proc/self/mountinfo`` cannot be read (macOS, BSD, Windows, a Linux without
``/proc``), a mount of the same device (a BSD ``nullfs`` mount) and a single file mounted
from the same device; inside an OME-Zarr store, a file is held to the mount table only, as
its files are not stat'ed. macOS ``bindfs`` (FUSE) and Windows mounted folders have
another device. On Windows a mounted folder is also a mount-point reparse point, which
:func:`.links.is_link` treats as a link.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

logger = logging.getLogger(__name__)

MOUNTINFO = "/proc/self/mountinfo"
"""The Linux kernel's list of the mounts this process sees."""

_OCTAL = re.compile(rb"\\([0-7]{3})")


def mount_points(table: str | os.PathLike = MOUNTINFO) -> frozenset[Path]:
    r"""Return every mount point listed in a ``mountinfo`` table; none when it cannot be read.

    Parameters
    ----------
    table
        The table, ``/proc/self/mountinfo`` by default.

    Returns
    -------
    frozenset of Path
        The mount points (the fifth field of each line, its octal escapes (``\040``
        for a space) undone).
    """
    try:
        text = Path(table).read_bytes()
    except OSError:
        logger.debug("Cannot read %s", table, exc_info=True)
        return frozenset()
    points = set()
    for line in text.splitlines():
        fields = line.split(b" ")
        if len(fields) > 4:
            points.add(Path(os.fsdecode(_OCTAL.sub(_unescape, fields[4]))))
    return frozenset(points)


def _unescape(match: re.Match[bytes]) -> bytes:
    """Return the byte an octal escape of ``mountinfo`` stands for."""
    return bytes([int(match.group(1), 8)])


class MountCheck:
    """Tell the mount points below one folder (resolved).

    Parameters
    ----------
    real_root
        The selected folder, its real path. It may itself be a mount point.
    """

    def __init__(self, real_root: Path) -> None:
        self.real_root = real_root
        try:
            self.device: int | None = os.stat(real_root).st_dev
        except OSError:  # a folder that does not exist holds nothing
            self.device = None
        self.below = frozenset(point for point in mount_points() if real_root in point.parents)

    def is_mount(self, real: Path, entry: os.DirEntry) -> bool:
        """Return True when an entry of the walk, at real path ``real``, is a mount point.

        Parameters
        ----------
        real
            The entry's real path, below the selected folder.
        entry
            The entry; a folder's device is read with :func:`os.stat` of its path, which
            follows a link (``entry.stat()`` would not do: on Windows it reports
            ``st_dev`` 0 for every entry).

        Returns
        -------
        bool
        """
        if real in self.below:
            return True
        if self.device is None or not entry.is_dir():
            return False
        try:
            return os.stat(entry.path).st_dev != self.device
        except OSError:  # cannot be read: its reader reports it
            return False

    def on_other_device(self, found: os.stat_result) -> bool:
        """Return True when a file's ``stat`` (taken by its walk) is on another device.

        The device rule of :meth:`is_mount` for files, which the walk stats once
        anyway (:meth:`.links.LinkGuard.claim`): a file mounted on its own from another
        file system or volume. A file inside an OME-Zarr store is not stat'ed, so it
        is seen only through the mount table.

        Parameters
        ----------
        found
            The file's :func:`os.stat` (links followed).

        Returns
        -------
        bool
        """
        return self.device is not None and found.st_dev != self.device
