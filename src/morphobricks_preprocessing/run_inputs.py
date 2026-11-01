"""What a clean preprocessing run gives the other bricks: :class:`RunInputs` (design v0.4 §6).

A brick that works on files reads them only through a clean run: the images, their axis
roles, pixel sizes, masks, histograms, design and image type, all as the run recorded them.
Nothing is parsed from names, guessed or transposed, and no file is read once it changed:

- :meth:`RunInputs.open` refuses a version 1 manifest (a run before the axis check), a table
  or design whose SHA-256 is not the manifest's, and an image changed or gone since the run;
- every read checks the file's fingerprint again (:class:`.fingerprint.Fingerprint`);
- masks are read by the recorded pairing only, with preprocessing's reader and its label
  check (:func:`.masks.check_labels`); a stated shared 2-D mask serves every plane.

A :class:`RunInputs` is picklable: it holds paths and the records read when it opened (its
snapshot of the table), and opens files only to read them.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from morphobricks_normalization.csv_io import OUTPUT_FILENAME, read_plane_histograms
from morphobricks_normalization.records import PlaneRecord

from .axes import AxisMapping
from .design import DESIGN_FILENAME, ExperimentDesign, read_experiment_design
from .fingerprint import Fingerprint
from .manifest import (
    LEGACY_MANIFEST_VERSION,
    MANIFEST_FILENAME,
    FileEntry,
    RunManifest,
    file_sha256,
    read_manifest,
)
from .masks import FOLDER, MaskFile, read_mask
from .masks.header import _mask_header
from .planes import DEFAULT_MEMORY_LIMIT, read_plane
from .recorded_sizes import RecordedSize


class RunInputsError(ValueError):
    """The run cannot be used as it is: run preprocessing again (the message says why)."""


@dataclass(frozen=True)
class Fov:
    """One position (field of view) of a clean run, as the manifest and the table record it.

    Attributes
    ----------
    key
        ``(condition, biological_repeat, technical_repeat, position_id)``
        (:data:`.discovery.FOV_KEY`).
    relative_path
        The image, relative to the experiment root.
    plane_ids
        The ``plane_id`` of each of its rows in the table, in table order.
    axes, roles
        The image's axes and the role of each (``None`` for a length-1 axis without one).
    axes_from
        ``"header"`` or ``"confirmed"`` (a plain stack whose roles the user confirmed).
    channel_names
        The channel names from the metadata; empty when not given.
    dtype
        The experiment's pixel type.
    significant_bits
        Significant bits from the metadata, or None.
    z_count, t_count, c_count
        Depth slices, time points and channels.
    """

    key: tuple[str, str, str, str]
    relative_path: str
    plane_ids: tuple[str, ...]
    axes: str
    roles: tuple[str | None, ...]
    axes_from: str
    channel_names: tuple[str | None, ...]
    dtype: str
    significant_bits: int | None
    z_count: int
    t_count: int
    c_count: int


@dataclass(frozen=True)
class RunInputs:
    """The inputs of a clean preprocessing run, read as the run recorded them.

    Use :meth:`open`; the attributes are the snapshot taken then.

    Attributes
    ----------
    output_folder
        The run's output folder.
    experiment_root
        Where the images are read from.
    masks_root
        Where the masks folder's masks are read from, or None.
    manifest
        The run manifest.
    records
        The table's rows, as read when opened.
    """

    output_folder: Path
    experiment_root: Path
    masks_root: Path | None
    manifest: RunManifest
    records: tuple[PlaneRecord, ...]

    @classmethod
    def open(
        cls,
        output_folder: str | os.PathLike,
        experiment_root: str | os.PathLike | None = None,
        masks_root: str | os.PathLike | None = None,
    ) -> RunInputs:
        """Open a clean run, checking its table, design and images against its manifest.

        Parameters
        ----------
        output_folder
            The run's output folder (``run_manifest.json``, ``plane_histograms.csv``).
        experiment_root
            Where the experiment is now; by default where the run read it.
        masks_root
            Where the masks folder is now; by default where the run read it.

        Raises
        ------
        RunInputsError
            For a run before v0.4.0, a table or design changed since the run, or an image
            changed or gone.
        """
        output = Path(output_folder).resolve()
        manifest = _manifest(output)
        _check_sha(output / OUTPUT_FILENAME, manifest.csv_sha256, "histogram table")
        if manifest.experiment_design_sha256 is not None:
            _check_sha(output / DESIGN_FILENAME, manifest.experiment_design_sha256, "design")
        root = Path(experiment_root or manifest.experiment_root).resolve()
        recorded_masks = (manifest.masks or {}).get("folder")
        masks = masks_root or recorded_masks
        inputs = cls(
            output_folder=output,
            experiment_root=root,
            masks_root=None if masks is None else Path(masks).resolve(),
            manifest=manifest,
            records=tuple(read_plane_histograms(output / OUTPUT_FILENAME)),
        )
        changed = [e.relative_path for e in manifest.files if not inputs._unchanged(e)]
        if changed:
            raise RunInputsError(_changed_text(changed))
        return inputs

    def fovs(self) -> tuple[Fov, ...]:
        """Return every position of the run, sorted by image path."""
        rows: dict[str, list[PlaneRecord]] = {}
        for record in self.records:
            rows.setdefault(record.source_relative_path, []).append(record)
        return tuple(_fov(e, rows.get(e.relative_path, []), self.manifest) for e in self.files)

    @property
    def files(self) -> tuple[FileEntry, ...]:
        """The manifest entry of every image, sorted by path."""
        return tuple(sorted(self.manifest.files, key=lambda e: e.relative_path))

    def pixel_sizes(self, fov: Fov | str) -> dict[str, RecordedSize]:
        """Return the recorded size of one step along X, Y, Z and T, by role.

        A size the run does not know has no value and no source (uncalibrated).
        """
        return {size.role: size for size in self._entry(fov).pixel_sizes}

    def read_plane(
        self, fov: Fov | str, z: int = 0, t: int = 0, *, memory_limit: int = DEFAULT_MEMORY_LIMIT
    ) -> np.ndarray:
        """Read one plane, every channel, as a ``(C, Y, X)`` array of the stored values.

        Raises
        ------
        RunInputsError
            If the image changed since the run.
        IndexError
            If ``z`` or ``t`` is outside the image.
        """
        entry = self._checked(fov)
        mapping = _mapping(entry)
        path = self.experiment_root / entry.relative_path
        coordinates = {}
        for role, index in (("Z", z), ("T", t)):
            if mapping.index_of(role) is None:
                if index != 0:
                    raise IndexError(f"{entry.relative_path} has no {role} {index}")
                continue
            coordinates[role.lower()] = index
        has_c = mapping.index_of("C") is not None
        channels = self._fov(entry).c_count if has_c else 1
        planes = [
            read_plane(
                path, mapping, c=c if has_c else None, memory_limit=memory_limit, **coordinates
            ).pixels
            for c in range(channels)
        ]
        return np.stack(planes)

    def read_image(self, fov: Fov | str) -> LazyImage:
        """Return the image as a lazy ``(T, Z, C, Y, X)`` array: planes are read when indexed."""
        entry = self._checked(fov)
        first = self.read_plane(fov)
        found = self._fov(entry)
        shape = (found.t_count, found.z_count, *first.shape)
        return LazyImage(self, entry.relative_path, shape, first.dtype)

    def mask_types(self) -> tuple[str, ...]:
        """Return the mask types the run recorded, sorted."""
        return tuple((self.manifest.masks or {}).get("types", ()))

    def read_mask(
        self, fov: Fov | str, mask_type: str, z: int | None = None, t: int | None = None
    ) -> np.ndarray:
        """Read one mask plane by the recorded pairing, checking its labels.

        A stated shared 2-D mask is the same plane for every ``z`` and ``t``.

        Raises
        ------
        RunInputsError
            If the image has no recorded mask of that type, or the mask changed since the run.
        ValueError
            For a label outside 0 to the int32 limit (:func:`.masks.check_labels`).
        """
        entry = self._checked(fov)
        recorded = self._mask_entry(entry.relative_path, mask_type)
        mask = MaskFile(recorded["image"], recorded["type"], recorded["path"], recorded["source"])
        folder = self.masks_root if mask.source == FOLDER else None
        where = (folder if folder is not None else self.experiment_root) / mask.path
        if Fingerprint.of(where) != Fingerprint.recorded(recorded):
            raise RunInputsError(_changed_text([mask.path], "mask"))
        header_axes, shape, dtype = _mask_header(mask, folder, self.experiment_root)
        if list(shape) != list(recorded["shape"]):
            raise RunInputsError(_changed_text([mask.path], "mask"))
        mask = MaskFile(
            mask.image,
            mask.mask_type,
            mask.path,
            mask.source,
            tuple(shape),
            dtype,
            str(recorded["roles"]),
            header_axes,
            str(recorded["axes_from"]),
        )
        return read_mask(mask, folder, self.experiment_root, z=z, t=t)

    def histograms(self) -> tuple[PlaneRecord, ...]:
        """Return the table's rows, as read when the run was opened."""
        return self.records

    def design(self) -> ExperimentDesign | None:
        """Return the run's experiment design, or None when it had none."""
        if self.manifest.experiment_design_sha256 is None:
            return None
        path = self.output_folder / DESIGN_FILENAME
        _check_sha(path, self.manifest.experiment_design_sha256, "design")
        return read_experiment_design(path)

    def image_type(self) -> str | None:
        """Return the image type the user confirmed (``"fluorescence"`` or ``"rgb"``), or None."""
        return self.manifest.image_type

    def _fov(self, entry: FileEntry) -> Fov:
        """Describe one position from its entry and its rows of the snapshot."""
        rows = [r for r in self.records if r.source_relative_path == entry.relative_path]
        return _fov(entry, rows, self.manifest)

    def _entry(self, fov: Fov | str) -> FileEntry:
        """Return the manifest entry of a position, given as a :class:`Fov` or its image path."""
        relative = fov.relative_path if isinstance(fov, Fov) else str(fov)
        entry = self.manifest.file(relative)
        if entry is None:
            raise RunInputsError(f"{relative} is not an image of this run.")
        return entry

    def _checked(self, fov: Fov | str) -> FileEntry:
        """Return a position's entry after checking its image is unchanged since the run."""
        entry = self._entry(fov)
        if not self._unchanged(entry):
            raise RunInputsError(_changed_text([entry.relative_path]))
        return entry

    def _unchanged(self, entry: FileEntry) -> bool:
        """Return True when an image's fingerprint is the one the run recorded."""
        try:
            now = Fingerprint.of(self.experiment_root / entry.relative_path)
        except FileNotFoundError:
            return False
        return now == Fingerprint.recorded(entry)

    def _mask_entry(self, image: str, mask_type: str) -> Mapping:
        """Return the recorded mask of an image and type."""
        for recorded in (self.manifest.masks or {}).get("files", ()):
            if recorded["image"] == image and recorded["type"] == mask_type:
                return recorded
        raise RunInputsError(f"The run recorded no {mask_type} mask for {image}.")


@dataclass(frozen=True)
class LazyImage:
    """An image of a clean run as a ``(T, Z, C, Y, X)`` array whose planes are read on demand.

    Attributes
    ----------
    inputs
        The run.
    relative_path
        The image.
    shape
        ``(T, Z, C, Y, X)``.
    dtype
        The stored pixel type.
    """

    inputs: RunInputs
    relative_path: str
    shape: tuple[int, ...]
    dtype: np.dtype

    def __getitem__(self, index: tuple[int, int]) -> np.ndarray:
        """Read the ``(C, Y, X)`` plane at ``[t, z]``."""
        t, z = index
        return self.inputs.read_plane(self.relative_path, z=z, t=t)

    def __array__(self, dtype: np.dtype | None = None, copy: bool | None = None) -> np.ndarray:
        """Read every plane into one array."""
        data = np.stack(
            [np.stack([self[t, z] for z in range(self.shape[1])]) for t in range(self.shape[0])]
        )
        return data if dtype is None else data.astype(dtype)


def _manifest(output: Path) -> RunManifest:
    """Read an output folder's manifest, refusing runs written before v0.4.0."""
    path = output / MANIFEST_FILENAME
    if not path.exists():
        raise RunInputsError(f"{output} holds no {MANIFEST_FILENAME}: run preprocessing first.")
    manifest = read_manifest(path)
    if manifest.manifest_version == LEGACY_MANIFEST_VERSION:
        raise RunInputsError(
            f"The run in {output} was written before v0.4.0, without the axis check. Run "
            "preprocessing again on the experiment."
        )
    return manifest


def _check_sha(path: Path, expected: str, what: str) -> None:
    """Refuse a file whose SHA-256 is not the one the manifest recorded."""
    if not path.exists() or file_sha256(path) != expected:
        raise RunInputsError(
            f"The {what} {path.name} is not the one the run wrote (missing, or changed since). "
            "Run preprocessing again."
        )


def _changed_text(paths: list[str], what: str = "image") -> str:
    """Say which files changed or went missing since the run."""
    shown = ", ".join(sorted(paths)[:5]) + (" and more" if len(paths) > 5 else "")
    return (
        f"{len(paths)} {what}{'' if len(paths) == 1 else 's'} changed or missing since the "
        f"run: {shown}. Run preprocessing again on the experiment as it is."
    )


def _mapping(entry: FileEntry) -> AxisMapping:
    """Return an image's own mapping as the run recorded it."""
    return AxisMapping(entry.axes, tuple(entry.roles or ()), entry.singleton_roles)


def _fov(entry: FileEntry, rows: list[PlaneRecord], manifest: RunManifest) -> Fov:
    """Describe one position from its manifest entry and its table rows."""
    zs = {r.z for r in rows} or {0}
    ts = {r.t for r in rows} or {0}
    channels = rows[0].channel_count if rows else len(entry.channel_names) or 1
    return Fov(
        key=(*entry.labels, entry.position_id or ""),
        relative_path=entry.relative_path,
        plane_ids=tuple(r.plane_id for r in rows),
        axes=entry.axes,
        roles=tuple(entry.roles or ()),
        axes_from=entry.axes_from or "",
        channel_names=entry.channel_names,
        dtype=manifest.dtype,
        significant_bits=entry.significant_bits,
        z_count=max(zs) + 1,
        t_count=max(ts) + 1,
        c_count=channels,
    )
