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
- every read checks the file's fingerprint again (:class:`.fingerprint.Fingerprint`); for a
  run that verified content (``verify_content``), :meth:`RunInputs.open` also checks every
  image's content SHA-256 once (:func:`.source_content.content_changed`), and refuses one
  whose content changed even with its fingerprint kept;
- masks are read by the recorded pairing only, with preprocessing's reader and its label
  check (:func:`.masks.check_labels`); a run that recorded a 2-D mask shared by every
  plane (``shared: true``, v0.4.0 release candidates) is refused.

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
from .description import Description
from .design import DESIGN_FILENAME, DesignCheckError, ExperimentDesign, read_confirmed_design
from .fingerprint import Fingerprint
from .folder_rules import masks_folder_issues
from .links import LinkRefusedError
from .manifest import (
    LEGACY_MANIFEST_VERSION,
    MANIFEST_FILENAME,
    FileEntry,
    RunManifest,
    file_sha256,
    read_manifest,
)
from .mask_record import shared_mask_text
from .masks import FOLDER, MaskFile, read_mask
from .masks.header import _mask_header
from .planes import DEFAULT_MEMORY_LIMIT, read_plane
from .recorded_sizes import RecordedSize
from .run.generation import published_path, read_stable
from .run_inputs_error import RunInputsError as RunInputsError  # re-exported (moved)
from .run_inputs_image import LazyImage as LazyImage  # re-exported (moved)
from .run_inputs_semantic import SemanticInputs, check_description, with_identity
from .run_inputs_text import (
    _ZARR_MISSING,
    _changed_text,
    _labels_unreadable_text,
    _unrecorded_text,
    _without_axes_text,
)
from .source_content import content_changed
from .unrecorded import UnrecordedFiles, unrecorded_files
from .validation import plural
from .zarr_input import NgffError, ZarrUnavailableError


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
    repeat_group, condition_id, technical_repeat_id
        The canonical repeat, condition and technical repeat IDs of the position, added
        beside the unchanged ``key`` (experiment-v1 §3.4). None for a run without a
        published description.
    channel_ids
        The canonical channel ID of each C index, in order; None without a description.
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
    repeat_group: str | None = None
    condition_id: str | None = None
    technical_repeat_id: str | None = None
    channel_ids: tuple[str, ...] | None = None


@dataclass(frozen=True)
class RunInputs(SemanticInputs):
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
            For a run before v0.4.0, an experiment, named-axis copies or an output folder
            inside the masks folder or a masks folder inside the experiment
            (:mod:`.folder_rules`), a table, design, calibration or conversion record (of a
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
        check_description(output, manifest)  # A_DRAFT_REFUSED, or changed since the run
        root = Path(experiment_root or manifest.experiment_root).resolve()
        _check_conversion(root, manifest)
        masks = masks_root or (manifest.masks or {}).get("folder")
        refused = [] if masks is None else masks_folder_issues(masks, root, output)
        if refused:
            # A run recorded before these folder rules (audit B1) is refused as a new one is.
            raise RunInputsError(f"The run cannot be used: {refused[0].message}")
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
        described = self.description()
        return tuple(
            _fov(e, rows.get(e.relative_path, []), self.manifest, described) for e in self.files
        )

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
        _check_planes(entry.relative_path, self._fov(entry), z, t)
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

        Raises
        ------
        TypeError
            If ``z`` or ``t`` is neither None nor a whole number.
        IndexError
            If ``z`` or ``t`` is outside the image (``Fov.z_count``, ``Fov.t_count``).
        RunInputsError
            If the image has no recorded mask of that type, or the mask changed since the run.
        ValueError
            For a label outside 0 to the int32 limit (:func:`.masks.check_labels`).
        """
        call = "read_mask(fov, mask_type, z, t)"
        z, t = (None if v is None else _coordinate(v, k, call) for k, v in (("z", z), ("t", t)))
        entry = self._checked(fov)
        _check_planes(entry.relative_path, self._fov(entry), z, t)
        recorded = self._mask_entry(entry.relative_path, mask_type)
        self.require_checked(recorded, mask_type)  # A_MASK_UNCHECKED (a described run)
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
        """Refuse changed or gone files, masks without axes or shared, unrecorded files."""
        changed = [e.relative_path for e in self.manifest.files if not self._unchanged(e)]
        if not changed and self.manifest.settings.verify_content:
            changed = content_changed(self.manifest.files, self.experiment_root)
        if changed:
            raise RunInputsError(_changed_text(changed))
        recorded = (self.manifest.masks or {}).get("files", ())
        changed = [m["path"] for m in recorded if not self._mask_unchanged(m)]
        if changed:
            raise RunInputsError(_changed_text(changed, "mask"))
        without_axes = [m["path"] for m in recorded if not m.get("axes") or not m.get("roles")]
        if without_axes:
            raise RunInputsError(_without_axes_text(without_axes))
        shared = shared_mask_text(recorded)
        if shared:
            raise RunInputsError(shared)
        try:
            unrecorded = unrecorded_files(
                self.manifest, self.experiment_root, self.masks_root, self.output_folder
            )
        except ZarrUnavailableError as exc:
            raise RunInputsError(_ZARR_MISSING) from exc
        except NgffError as exc:
            raise RunInputsError(_labels_unreadable_text(exc)) from exc
        except LinkRefusedError as exc:
            raise RunInputsError(f"The run cannot be checked:\n{exc}") from exc
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


def _check_planes(relative_path: str, fov: Fov, z: int | None, t: int | None) -> None:
    """Refuse a ``z`` or ``t`` outside the image (audit I5)."""
    for name, index, count in (("z", z, fov.z_count), ("t", t, fov.t_count)):
        if index is not None and not 0 <= index < count:
            raise IndexError(
                f"{relative_path} has {plural(count, 'plane')} along "
                f"{name.upper()} ({name} 0 to {count - 1}); {name}={index} is outside it"
            )


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


def _fov(
    entry: FileEntry,
    rows: list[PlaneRecord],
    manifest: RunManifest,
    description: Description | None = None,
) -> Fov:
    """Describe one position from its manifest entry and its table rows.

    With the published ``description`` the canonical identity is added beside the key; the
    unit's channels must be the image's channels (nothing is guessed when they are not).
    """
    zs = {r.z for r in rows} or {0}
    ts = {r.t for r in rows} or {0}
    channels = rows[0].channel_count if rows else len(entry.channel_names) or 1
    if entry.position_id is None:  # never an empty name in the key (round-2 audit A-m5)
        raise RunInputsError(f"The run records no position_id for {entry.relative_path}.")
    fov = Fov(
        key=(*entry.labels, entry.position_id),
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
    return fov if description is None else with_identity(fov, entry, description)
