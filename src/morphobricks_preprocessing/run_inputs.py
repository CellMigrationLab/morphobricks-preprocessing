"""What a clean preprocessing run gives the other bricks: :class:`RunInputs` (design v0.4 §6).

A brick that works on files reads them only through a clean run: the images, their axis
roles, pixel sizes, masks, histograms, design and image type, all as the run recorded them.
Nothing is parsed from names, guessed or transposed, and no file is read once it changed:

- :meth:`RunInputs.open` refuses a version 1 manifest (a run before the axis check), a table,
  design or ``calibration.csv`` whose SHA-256 is not the manifest's (also a
  ``calibration.csv`` added since the run), a conversion record changed or gone since a
  run on named-axis copies (``conversion_record_sha256``) or one of its copies changed since
  it was written (the copies' SHA-256, read whole once at open), an image or mask changed or gone
  since the run, a mask recorded without axes (a v0.4.0rc1 run of a mask whose header puts X before
  Y), and an image or mask in the experiment the run neither recorded nor excluded
  (:class:`UnrecordedFilesError`, from :func:`.unrecorded.unrecorded_files`: folders are
  listed, no pixel is read). OME-Zarr label images are never skipped: a run holding
  OME-Zarr images is refused without the ``zarr`` extra, and so is a recorded image whose
  ``labels`` group cannot be read;
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
from .calibration import CALIBRATION_FILENAME
from .conversion_check import conversion_problem
from .design import DESIGN_FILENAME, DesignCheckError, ExperimentDesign, read_confirmed_design
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
from .run.generation import published_path, read_stable
from .run_inputs_text import (
    _ZARR_MISSING,
    _changed_text,
    _labels_unreadable_text,
    _unrecorded_text,
    _without_axes_text,
)
from .unrecorded import UnrecordedFiles, unrecorded_files
from .zarr_input import NgffError, ZarrUnavailableError


class RunInputsError(ValueError):
    """The run cannot be used as it is: run preprocessing again (the message says why)."""


class UnrecordedFilesError(RunInputsError):
    """The experiment holds images or masks the run neither recorded nor excluded.

    A brick never discovers files itself and never leaves them out without a word: run
    preprocessing again on the experiment as it is, or exclude the images.

    Attributes
    ----------
    files
        The unrecorded images and masks (:class:`.unrecorded.UnrecordedFiles`).
    """

    def __init__(self, files: UnrecordedFiles) -> None:
        super().__init__(_unrecorded_text(files))
        self.files = files

    def __reduce__(self) -> tuple:
        """Pickle with the files, so a worker process can raise it to its parent."""
        return (type(self), (self.files,))


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
            For a run before v0.4.0, a table, design, calibration or conversion record (of a
            run on named-axis copies) changed or gone since the run, a named-axis copy whose
            SHA-256 is not its record's (or a record of version 1, without them),
            an image or mask changed or gone, a mask recorded without axes, OME-Zarr images
            without the ``zarr`` extra (``morphobricks-preprocessing[zarr]``), or an OME-Zarr
            image whose ``labels`` group cannot be read.
        UnrecordedFilesError
            For images or masks in the experiment that the run neither recorded nor
            excluded (added since the run): it names them.
        """
        output = Path(output_folder).resolve()
        # One generation of the outputs (contract §9): read again if a run published
        # meanwhile.
        inputs = read_stable(output, lambda: cls._read(output, experiment_root, masks_root))
        if inputs is None:
            raise RunInputsError(
                f"Another run kept publishing new results in {output} while they were being "
                "opened. Open the run again once it has finished."
            )
        inputs._check_files()
        return inputs

    @classmethod
    def _read(
        cls,
        output: Path,
        experiment_root: str | os.PathLike | None,
        masks_root: str | os.PathLike | None,
    ) -> RunInputs:
        """Read the manifest and the files it names, checking their SHA-256."""
        manifest = _manifest(output)
        table = published_path(output, OUTPUT_FILENAME, manifest.csv_sha256)
        _check_sha(table, manifest.csv_sha256, "histogram table")
        if manifest.experiment_design_sha256 is not None:
            design = published_path(output, DESIGN_FILENAME, manifest.experiment_design_sha256)
            _check_sha(design, manifest.experiment_design_sha256, "design")
        _check_calibration(output / CALIBRATION_FILENAME, manifest.calibration_sha256)
        root = Path(experiment_root or manifest.experiment_root).resolve()
        _check_conversion(root, manifest)
        masks = masks_root or (manifest.masks or {}).get("folder")
        return cls(
            output_folder=output,
            experiment_root=root,
            masks_root=None if masks is None else Path(masks).resolve(),
            manifest=manifest,
            records=tuple(read_plane_histograms(table)),
        )

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

        Each has its value, unit and source: ``"header"`` (the image's metadata),
        ``"stated"`` (``calibration.csv``) or ``"pixels"`` (stated as deliberately
        uncalibrated, no value). A size nothing gives has no value and no source
        (uncalibrated). Nothing is guessed.
        """
        return {size.role: size for size in self._entry(fov).pixel_sizes}

    def read_plane(
        self, fov: Fov | str, z: int = 0, t: int = 0, *, memory_limit: int = DEFAULT_MEMORY_LIMIT
    ) -> np.ndarray:
        """Read one plane, every channel, as a ``(C, Y, X)`` array of the stored values.

        Raises
        ------
        TypeError
            If ``z`` or ``t`` is not a whole number (a ``plane_id`` is not a coordinate).
        RunInputsError
            If the image changed since the run.
        IndexError
            If ``z`` or ``t`` is outside the image.
        """
        z, t = (_coordinate(v, k, "read_plane(fov, z, t)") for k, v in (("z", z), ("t", t)))
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
        TypeError
            If ``z`` or ``t`` is neither None nor a whole number.
        RunInputsError
            If the image has no recorded mask of that type, or the mask changed since the run.
        ValueError
            For a label outside 0 to the int32 limit (:func:`.masks.check_labels`).
        """
        call = "read_mask(fov, mask_type, z, t)"
        z, t = (None if v is None else _coordinate(v, k, call) for k, v in (("z", z), ("t", t)))
        entry = self._checked(fov)
        recorded = self._mask_entry(entry.relative_path, mask_type)
        if not self._mask_unchanged(recorded):
            raise RunInputsError(_changed_text([recorded["path"]], "mask"))
        mask = MaskFile(recorded["image"], recorded["type"], recorded["path"], recorded["source"])
        folder = self.masks_root if mask.source == FOLDER else None
        stored, shape, dtype = _mask_header(mask, folder, self.experiment_root)
        # The mask is read through the axes it is stored with, as the run recorded them.
        if stored != recorded["axes"] or list(shape) != list(recorded["shape"]):
            raise RunInputsError(_changed_text([mask.path], "mask"))
        mask = MaskFile(
            mask.image,
            mask.mask_type,
            mask.path,
            mask.source,
            tuple(shape),
            dtype,
            str(recorded["roles"]),
            str(recorded["axes"]),
            str(recorded["axes_from"]),
        )
        return read_mask(mask, folder, self.experiment_root, z=z, t=t)

    def histograms(self) -> tuple[PlaneRecord, ...]:
        """Return the table's rows, as read when the run was opened."""
        return self.records

    def design(self) -> ExperimentDesign | None:
        """Return the run's experiment design, or None when it had none.

        Read with the strict :func:`.design.read_confirmed_design` (issue #83), checked
        against the manifest's ``experiment_design_sha256`` on the bytes it parses.

        Raises
        ------
        RunInputsError
            If the design is missing, changed since the run, or not one the strict reader
            takes (its refusal is the ``__cause__``).
        """
        expected = self.manifest.experiment_design_sha256
        if expected is None:
            return None
        path = published_path(self.output_folder, DESIGN_FILENAME, expected)
        try:
            return read_confirmed_design(path, expected_sha256=expected).design
        except DesignCheckError as exc:
            raise RunInputsError(
                f"The design {DESIGN_FILENAME} cannot be used: {exc.issues[0].message} "
                "Run preprocessing again."
            ) from exc

    def image_type(self) -> str | None:
        """Return the image type the user confirmed (``"fluorescence"`` or ``"rgb"``), or None."""
        return self.manifest.image_type

    def _check_files(self) -> None:
        """Refuse changed or gone files, masks without axes, unchecked labels, unrecorded files."""
        changed = [e.relative_path for e in self.manifest.files if not self._unchanged(e)]
        if changed:
            raise RunInputsError(_changed_text(changed))
        recorded = (self.manifest.masks or {}).get("files", ())
        changed = [m["path"] for m in recorded if not self._mask_unchanged(m)]
        if changed:
            raise RunInputsError(_changed_text(changed, "mask"))
        without_axes = [m["path"] for m in recorded if not m.get("axes") or not m.get("roles")]
        if without_axes:
            raise RunInputsError(_without_axes_text(without_axes))
        try:
            unrecorded = unrecorded_files(
                self.manifest, self.experiment_root, self.masks_root, self.output_folder
            )
        except ZarrUnavailableError as exc:
            raise RunInputsError(_ZARR_MISSING) from exc
        except NgffError as exc:
            raise RunInputsError(_labels_unreadable_text(exc)) from exc
        if unrecorded:
            raise UnrecordedFilesError(unrecorded)

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

    def _mask_unchanged(self, recorded: Mapping) -> bool:
        """Return True when a mask's fingerprint is the one the run recorded."""
        if recorded["source"] == FOLDER:
            if self.masks_root is None:
                return False
            where = self.masks_root / recorded["path"]
        else:
            where = self.experiment_root / recorded["path"]
        try:
            return Fingerprint.of(where) == Fingerprint.recorded(recorded)
        except FileNotFoundError:
            return False

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


def _coordinate(value: object, name: str, call: str) -> int:
    """Return a plane coordinate as an int, refusing anything but a whole number."""
    if isinstance(value, bool) or not isinstance(value, int | np.integer):
        raise TypeError(
            f"{call} takes the plane's zero-based coordinates z and t as whole numbers; "
            f"{name} is {value!r} ({type(value).__name__}). A row's plane_id is not a "
            "coordinate: give the row's z and t (PlaneRecord.z, PlaneRecord.t)."
        )
    return int(value)


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


def _check_calibration(path: Path, expected: str | None) -> None:
    """Refuse a ``calibration.csv`` changed, removed or added since the run."""
    if expected is not None:
        _check_sha(path, expected, "calibration")
    elif path.exists():
        raise RunInputsError(
            f"{path.name} was added since the run, so the pixel sizes it records may not be "
            "the ones stated now. Run preprocessing again."
        )


def _check_conversion(root: Path, manifest: RunManifest) -> None:
    """Refuse a changed or gone conversion record, or a copy changed since it was written."""
    if manifest.conversion_record_sha256 is None:
        return
    paths = [e.relative_path for e in manifest.files]
    problem = conversion_problem(root, manifest.conversion_record_sha256, paths)
    if problem is not None:
        raise RunInputsError(problem)


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
