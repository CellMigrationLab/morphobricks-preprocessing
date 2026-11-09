"""When a file is unchanged, and when its rows can be reused (contract §9, design v0.4 §1).

One rule for "the same file, read the same way" (plan-structure audit D5), used by the run
when it decides which rows to reuse, and by the bricks' checks:

- :class:`Fingerprint`: a file's size and modification time (for an OME-Zarr folder, the
  total size of its files, the newest time and the digest of its inventory: every file's
  path, size and time, issue #86), as :func:`.inventory.source_signature` gives them;
- :class:`RowKey`: everything a row depends on. A row is reused only when its file is
  unchanged and its roles, ``axes_from`` and every setting that changes rows are unchanged
  (bins, histogram range, robust quantiles, range-check limit, the path it is read from).

"Unchanged" means the same fingerprint: the identity rule is metadata. Its known blind spot
is a file rewritten with the same size whose modification time (for an OME-Zarr folder,
every file's) is set back: its earlier rows would be reused. A run with ``verify_content``
closes it: the key then also holds the content SHA-256 (:mod:`.source_content`).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .axis_check import ConfirmedAxes
from .inventory import INVENTORY_KEY, source_signature
from .manifest import FileEntry, RunSettings

if TYPE_CHECKING:
    from collections.abc import Iterable

    from .axes import AxisMapping


@dataclass(frozen=True)
class Fingerprint:
    """A file's size and modification time, which tell whether it changed.

    Attributes
    ----------
    size
        Size in bytes; for an OME-Zarr folder, the total size of its files.
    mtime_ns
        Modification time in nanoseconds; for an OME-Zarr folder, its newest file's.
    inventory
        For an OME-Zarr folder, the SHA-256 of its inventory (every file's relative path,
        size and modification time), so any chunk added, removed or changed changes the
        fingerprint (issue #86); None for a file.
    """

    size: int
    mtime_ns: int
    inventory: str | None = None

    @classmethod
    def of(cls, path: str | os.PathLike) -> Fingerprint:
        """Return the fingerprint of a file or OME-Zarr folder as it is on disk now.

        Raises
        ------
        FileNotFoundError
            If the file does not exist.
        """
        return cls(*source_signature(path))

    @classmethod
    def recorded(cls, entry: FileEntry | Mapping) -> Fingerprint:
        """Return the fingerprint recorded for a file (a manifest entry or its data).

        A record without an inventory (a file, or a record written before it was kept)
        has None, so an OME-Zarr folder recorded without one never equals it now.
        """
        if isinstance(entry, Mapping):
            inventory = entry.get(INVENTORY_KEY)
            return cls(
                int(entry["size"]),
                int(entry["mtime_ns"]),
                None if inventory is None else str(inventory),
            )
        return cls(entry.size, entry.mtime_ns, entry.inventory_sha256)

    def to_data(self) -> dict:
        """Return the fingerprint as JSON data: ``size``, ``mtime_ns`` (and the inventory).

        Returns
        -------
        dict
            The inventory digest is under ``inventory_sha256``, only for a folder.
        """
        data = {"size": self.size, "mtime_ns": self.mtime_ns}
        if self.inventory is not None:
            data[INVENTORY_KEY] = self.inventory
        return data


@dataclass(frozen=True)
class RowKey:
    """Everything one file's rows depend on: equal keys mean the rows can be reused.

    Attributes
    ----------
    relative_path
        The file, relative to the experiment root.
    fingerprint
        The file's :class:`Fingerprint`.
    labels
        ``(condition, biological_repeat, technical_repeat)``.
    axes, roles, singleton_roles
        The file's axes and the roles it is read with.
    axes_from
        Where the roles come from (``"header"`` or ``"confirmed"``).
    read_path
        The absolute path the rows are read from: moving or converting the experiment
        recomputes them.
    bin_count
        Bins per plane.
    histogram
        The histogram settings (range, robust quantiles, range-check limit), as data.
    content_sha256
        The file's content SHA-256 in a ``verify_content`` run; None otherwise.
    """

    relative_path: str
    fingerprint: Fingerprint
    labels: tuple[str, str, str]
    axes: str
    roles: tuple[str | None, ...] | None
    singleton_roles: tuple[str, ...]
    axes_from: str | None
    read_path: str
    bin_count: int
    histogram: tuple
    content_sha256: str | None = None

    @classmethod
    def of(cls, entry: FileEntry, settings: RunSettings, experiment_root: str) -> RowKey:
        """Return the key of a file's rows from its manifest entry and the run's settings.

        Parameters
        ----------
        entry
            The file's entry (as recorded, or as it is now).
        settings
            The run's settings.
        experiment_root
            The experiment folder the file is read from (absolute).
        """
        return cls(
            relative_path=entry.relative_path,
            fingerprint=Fingerprint.recorded(entry),
            labels=tuple(entry.labels),
            axes=entry.axes,
            roles=None if entry.roles is None else tuple(entry.roles),
            singleton_roles=tuple(entry.singleton_roles),
            axes_from=entry.axes_from,
            read_path=(Path(experiment_root) / entry.relative_path).as_posix(),
            bin_count=settings.bin_count,
            histogram=tuple(sorted(_frozen(settings.histogram.to_dict()).items())),
            content_sha256=entry.content_sha256,
        )


def confirm_axes(
    mapping: AxisMapping, experiment_root: str | os.PathLike, files: Iterable[str]
) -> ConfirmedAxes:
    """Record the user's confirmation of ``mapping`` for ``files`` as they are now.

    Parameters
    ----------
    mapping
        The experiment mapping the user confirmed.
    experiment_root
        The experiment folder.
    files
        The images the user confirmed it for, relative to the experiment root.

    Returns
    -------
    ConfirmedAxes
        The confirmation, with each file's :class:`Fingerprint`.

    Raises
    ------
    FileNotFoundError
        If a file does not exist.
    """
    root = Path(experiment_root)
    return ConfirmedAxes(mapping, {rel: Fingerprint.of(root / rel) for rel in files})


def unchanged_confirmation(
    mapping: AxisMapping | ConfirmedAxes, experiment_root: str | os.PathLike
) -> AxisMapping | ConfirmedAxes:
    """Keep only the confirmed files that are unchanged since they were confirmed.

    A file that changed or is gone is no longer covered, so a plain stack among them is
    ``plain_stack_not_confirmed`` (design v0.4 §3, audit 2 B-1). A plain mapping is
    returned as it is.

    Parameters
    ----------
    mapping
        The confirmation given to the run.
    experiment_root
        The experiment folder.

    Returns
    -------
    AxisMapping or ConfirmedAxes
    """
    if not isinstance(mapping, ConfirmedAxes):
        return mapping
    root = Path(experiment_root)
    kept = {
        rel: recorded
        for rel, recorded in mapping.files.items()
        if current_fingerprint(root / rel) == recorded
    }
    return ConfirmedAxes(mapping.mapping, kept)


def current_fingerprint(path: Path) -> Fingerprint | None:
    """Return a file's fingerprint now, or None when it is gone.

    A path under a file (``somefile/out/run_manifest.json``) is gone too: no file can be
    there.

    Parameters
    ----------
    path
        The file.

    Returns
    -------
    Fingerprint or None
    """
    try:
        return Fingerprint.of(path)
    except (FileNotFoundError, NotADirectoryError):
        return None


def changed_since_check(
    root: Path, bound: Mapping[str, Fingerprint | None], paths: Iterable[str]
) -> tuple[str, ...]:
    """Return the images that are not, now, the revision whose header the check read.

    ``bound`` are the fingerprints the folder check took with each header
    (:attr:`.run.preflight.Preflight.header_fingerprints`); an image without one, changed or
    gone is listed, sorted.
    """
    return tuple(
        rel
        for rel in sorted(paths)
        if bound.get(rel) is None or current_fingerprint(root / rel) != bound[rel]
    )


def _frozen(value: object) -> object:
    """Return ``value`` with lists and dicts made hashable (tuples), for comparing keys."""
    if isinstance(value, Mapping):
        return {key: _frozen(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return tuple(_frozen(item) for item in value)
    return value
