"""Save an experiment again as OME-Zarr with the confirmed axes (contract §17).

The remedy for the axis errors of :func:`.axes.review_axes` (``axes_not_named``,
``axes_yx_swapped``, ``axes_contradict_header``), and an option for any experiment: every
analysed image is copied to a new folder, the copy tree, at the same relative path, as
``<name>.ome.zarr`` (OME-NGFF 0.4). The copies name their axes in their metadata, so the
second preprocessing run, on the copy tree, has no axis to confirm against a header, and
that run is the one the other bricks use.

- The user's confirmed axes are used **only** to write the copies. A TIFF is read plane by
  plane under the confirmed mapping (:func:`.planes.iter_planes`) and written in OME-NGFF
  order ``t, c, z, y, x``; level 0 holds the source values unchanged. OME-Zarr images are
  copied as they are, with their label images.
- Pixel sizes follow the stored dimension, not the header letter (B2): when the confirmed
  roles swap Y and X, the copy's X size is the header's Y size and the other way round. An
  axis given another role (a header Z read as T) gets an unknown scale, never the header's
  size for another kind of axis, and the conversion record names it.
- Masks paired by :func:`.masks.find_masks` are written as OME-NGFF label images
  ``<copy>/labels/<mask type>``, int32 after :func:`.masks.check_labels`. Their axes are
  user-based: the user may state them per mask type (``mask_axes``); otherwise each mask
  follows its image's confirmed axes, with the same reordering (the mask's header letter
  that is the image's header Y gets the image's role for it, and so on). No shape decides
  anything. A mask that does not fit its copy (Y and X, depth slices, time points) is an
  error naming it, and nothing is written. The record states the rule used for each mask.
- The folder structure and names are kept, so condition, biological and technical repeat
  and ``position_id`` are unchanged (``position_id`` drops ``.ome.zarr``).
- ``<target>/.morphobricks_conversion.json`` (the conversion record) lists, for each copy,
  its source's path, size and modification time, the header and confirmed axes, the axis
  error it had, and its masks' sources, axes and rule. Discovery skips dot-files.

Needs the ``zarr`` extra.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import shutil
import tempfile
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

import numpy as np
import tifffile

from .axes import (
    AXIS_ERROR_CODES,
    AxisMapping,
    AxisReview,
    header_axis_issue,
    prefill_mapping,
    review_axes,
)
from .design import DESIGN_FILENAME, ConfirmedDesign, DesignCheckError, read_confirmed_design
from .discovery import default_output_folder, discover_experiment, position_id
from .manifest import package_versions, source_fingerprint
from .masks import FOLDER, MaskFile, MaskInventory, _tiff_axes, check_labels
from .metadata import TiffMetadata, named_axes
from .physical_sizes import PhysicalSizes
from .planes import DEFAULT_MEMORY_LIMIT, PlaneReadError, iter_planes, plane_layout
from .run import Preflight, preflight
from .validation import ValidationIssue, error, has_errors, plural, warning
from .zarr_export import (
    OME_ZARR_SUFFIX,
    ConversionEstimate,
    ConversionSettings,
    OmeZarrWriter,
    _compressor,
    _create_array,
    estimate_conversion,
    read_physical_sizes,
    zarr_available,
)
from .zarr_input import _zarr, _zarr_major

logger = logging.getLogger(__name__)

CONVERSION_RECORD = ".morphobricks_conversion.json"
"""The conversion record in the copy tree; a dot-file, so discovery never reads it."""

CONVERSION_RECORD_VERSION = "1"

CONVERSION_FOLDER_SUFFIX = "_ome_zarr"
"""The default copy tree is ``<experiment>_ome_zarr``, beside the experiment."""

RULE_IMAGE = "image"
"""A mask written following its image's confirmed axes (the default)."""

RULE_STATED = "stated"
"""A mask written with the axes the user stated for its mask type."""

RULE_COPIED = "copied"
"""A label image of an OME-Zarr image, copied with the image as it is."""

_STAGING = ".morphobricks-staging"

# Mask issues of find_masks that the conversion decides itself, by the rule of each mask:
# its axes (stated, or its image's), its size and planes against its copy.
_DECIDED_HERE = frozenset({"mask_not_2d", "mask_size_mismatch", "mask_planes_differ"})

_LABEL_NAME = re.compile(r"[^/\\\x00-\x1f]+")


@dataclass(frozen=True)
class ConvertedMask:
    """One mask written as a label image of a copy (or copied with an OME-Zarr image).

    Attributes
    ----------
    mask_type
        The mask type, the label image's name.
    source_path
        The mask's path as :class:`.masks.MaskFile` gives it: relative to the masks
        folder, or ``<image>/labels/<name>`` for a label image of an OME-Zarr image.
    source
        ``"folder"`` or ``"ome-zarr"``.
    size, mtime_ns
        The source's size and modification time (:func:`.manifest.source_fingerprint`).
    header_axes
        The mask's axes as its header gives them, length-1 axes other than Y and X left
        out (``"YX"``, ``"QYX"``, ...).
    roles
        The role of each of ``header_axes`` in the label image (``"YX"``, ``"TYX"``).
    rule
        ``"image"`` (followed its image's confirmed axes), ``"stated"`` (the axes stated
        for its mask type) or ``"copied"`` (a label image copied with its OME-Zarr image).
    """

    mask_type: str
    source_path: str
    source: str
    size: int
    mtime_ns: int
    header_axes: str
    roles: str
    rule: str

    def to_dict(self) -> dict:
        """Return the entry as JSON-compatible data."""
        return {
            "type": self.mask_type,
            "source_path": self.source_path,
            "source": self.source,
            "size": self.size,
            "mtime_ns": self.mtime_ns,
            "header_axes": self.header_axes,
            "roles": self.roles,
            "rule": self.rule,
        }

    @classmethod
    def from_dict(cls, data: Mapping) -> ConvertedMask:
        """Read an entry written by :meth:`to_dict`."""
        return cls(
            str(data["type"]),
            str(data["source_path"]),
            str(data["source"]),
            int(data["size"]),
            int(data["mtime_ns"]),
            str(data["header_axes"]),
            str(data["roles"]),
            str(data["rule"]),
        )


@dataclass(frozen=True)
class ConvertedImage:
    """One copy of the copy tree.

    Attributes
    ----------
    source_path
        The source image, relative to the experiment root.
    copy_path
        The copy, relative to the copy tree: the same folders, ``<name>.ome.zarr``.
    kind
        ``"tiff"`` or ``"ome-zarr"`` (copied as it is).
    size, mtime_ns
        The source's size and modification time.
    header_axes
        The axes ``tifffile`` (or OME-NGFF) reports for the source.
    named_axes
        The axes its metadata names, ``?`` where it names none (:func:`.metadata.named_axes`).
    confirmed_roles
        The confirmed role of each of ``header_axes`` (``"-"`` for none), as the copy was
        written.
    axis_error
        The axis error the source had under these roles, or None.
    unknown_scale
        The copy's axes (``"t"``, ``"z"``, ...) whose size is unknown (written as 1 and
        listed as ``unknown_scale`` in its metadata).
    scale_not_carried
        Header sizes deliberately not written because the axis was given another role,
        as ``"Z->T"``: a z-step is not a time step.
    masks
        The masks written as label images of the copy, by type.
    """

    source_path: str
    copy_path: str
    kind: str
    size: int
    mtime_ns: int
    header_axes: str
    named_axes: str
    confirmed_roles: str
    axis_error: str | None = None
    unknown_scale: tuple[str, ...] = ()
    scale_not_carried: tuple[str, ...] = ()
    masks: tuple[ConvertedMask, ...] = ()

    def to_dict(self) -> dict:
        """Return the entry as JSON-compatible data."""
        return {
            "source_path": self.source_path,
            "copy_path": self.copy_path,
            "kind": self.kind,
            "size": self.size,
            "mtime_ns": self.mtime_ns,
            "header_axes": self.header_axes,
            "named_axes": self.named_axes,
            "confirmed_roles": self.confirmed_roles,
            "axis_error": self.axis_error,
            "unknown_scale": list(self.unknown_scale),
            "scale_not_carried": list(self.scale_not_carried),
            "masks": [m.to_dict() for m in self.masks],
        }

    @classmethod
    def from_dict(cls, data: Mapping) -> ConvertedImage:
        """Read an entry written by :meth:`to_dict`."""
        return cls(
            source_path=str(data["source_path"]),
            copy_path=str(data["copy_path"]),
            kind=str(data["kind"]),
            size=int(data["size"]),
            mtime_ns=int(data["mtime_ns"]),
            header_axes=str(data["header_axes"]),
            named_axes=str(data["named_axes"]),
            confirmed_roles=str(data["confirmed_roles"]),
            axis_error=None if data.get("axis_error") is None else str(data["axis_error"]),
            unknown_scale=tuple(str(a) for a in data.get("unknown_scale") or ()),
            scale_not_carried=tuple(str(a) for a in data.get("scale_not_carried") or ()),
            masks=tuple(ConvertedMask.from_dict(m) for m in data.get("masks") or ()),
        )


@dataclass(frozen=True)
class ConversionRecord:
    """The record of a copy tree, ``<target>/.morphobricks_conversion.json``.

    Attributes
    ----------
    source_experiment
        The experiment the copies were made from (absolute path).
    masks_folder
        The masks folder their masks came from (absolute path), or None.
    mapping
        The confirmed experiment mapping the copies were written with.
    missing_as_singleton
        Whether missing axes were confirmed as length 1.
    settings
        How the copies were written.
    images
        One entry per copy, sorted by source path.
    written_at
        When the record was written (UTC, ISO 8601).
    versions
        Package versions, as in the run manifest.
    record_version
        Version of the record format.
    """

    source_experiment: str
    masks_folder: str | None
    mapping: AxisMapping
    missing_as_singleton: bool
    settings: ConversionSettings
    images: tuple[ConvertedImage, ...]
    written_at: str = ""
    versions: Mapping[str, str] = field(default_factory=dict)
    record_version: str = CONVERSION_RECORD_VERSION

    def image(self, source_path: str) -> ConvertedImage | None:
        """Return the entry of one source image, or None."""
        return next((i for i in self.images if i.source_path == source_path), None)

    def by_copy(self, copy_path: str) -> ConvertedImage | None:
        """Return the entry whose copy is ``copy_path`` (relative to the copy tree), or None."""
        return next((i for i in self.images if i.copy_path == copy_path), None)

    def to_dict(self) -> dict:
        """Return the record as JSON-compatible data."""
        return {
            "record_version": self.record_version,
            "written_at": self.written_at,
            "source_experiment": self.source_experiment,
            "masks_folder": self.masks_folder,
            "axis_mapping": {"axes": self.mapping.axes, "roles": list(self.mapping.roles)},
            "missing_as_singleton": self.missing_as_singleton,
            "settings": self.settings.to_dict(),
            "images": [i.to_dict() for i in self.images],
            "versions": dict(self.versions),
        }

    @classmethod
    def from_dict(cls, data: Mapping) -> ConversionRecord:
        """Read a record written by :meth:`to_dict`.

        Raises
        ------
        ValueError
            If the data is not a conversion record of this version.
        """
        try:
            if data["record_version"] != CONVERSION_RECORD_VERSION:
                raise ValueError(
                    f"conversion record version {data['record_version']!r} is not supported"
                )
            mapping = data["axis_mapping"]
            flag = data["missing_as_singleton"]
            if not isinstance(flag, bool):
                raise ValueError(f"missing_as_singleton is not true or false: {flag!r}")
            return cls(
                source_experiment=str(data["source_experiment"]),
                masks_folder=None
                if data.get("masks_folder") is None
                else str(data["masks_folder"]),
                mapping=AxisMapping(str(mapping["axes"]), tuple(mapping["roles"])),
                missing_as_singleton=flag,
                settings=ConversionSettings.from_dict(data.get("settings")),
                images=tuple(ConvertedImage.from_dict(i) for i in data["images"]),
                written_at=str(data.get("written_at", "")),
                versions={str(k): str(v) for k, v in (data.get("versions") or {}).items()},
            )
        except (KeyError, TypeError, AttributeError) as exc:
            raise ValueError(f"the conversion record is incomplete or malformed: {exc}") from exc


def read_conversion_record(path: str | os.PathLike) -> ConversionRecord:
    """Read a conversion record: the file itself, or the copy tree that holds it.

    Raises
    ------
    FileNotFoundError
        If there is no record.
    ValueError
        If the file is not a conversion record this version can read.
    """
    path = Path(path)
    if path.is_dir():
        path = path / CONVERSION_RECORD
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{path} is not a valid conversion record: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a valid conversion record")
    return ConversionRecord.from_dict(data)


@dataclass(frozen=True)
class ConversionResult:
    """What :func:`convert_experiment` did.

    Attributes
    ----------
    target
        The copy tree.
    written
        True if the copies and the record were written.
    issues
        Errors that stopped the conversion, and warnings (errors first). The axis errors
        of the source images are not among them: they are what the conversion fixes, and
        are in the record.
    record
        The conversion record written, or None.
    converted
        Source images whose copy was written in this conversion.
    kept
        Source images whose earlier copy was unchanged and kept.
    removed
        Copies of an earlier conversion removed because their image is no longer analysed.
    axis_errors
        The axis errors of the source images under the confirmed roles (what the copies
        fix).
    estimate
        The disk-space estimate of the TIFF copies.
    """

    target: Path
    written: bool
    issues: tuple[ValidationIssue, ...] = ()
    record: ConversionRecord | None = None
    converted: tuple[str, ...] = ()
    kept: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    axis_errors: tuple[ValidationIssue, ...] = ()
    estimate: ConversionEstimate | None = None

    @property
    def ok(self) -> bool:
        """True if no issue blocked the conversion."""
        return not has_errors(self.issues)

    @property
    def record_path(self) -> Path:
        """Location of the conversion record."""
        return self.target / CONVERSION_RECORD

    @property
    def suggested_roles(self) -> str | None:
        """The ``--axes`` for the run on the copies: their own NGFF axes, or None."""
        if self.record is None or not self.record.images:
            return None
        return copy_axes(self.target, self.record)

    @property
    def next_commands(self) -> tuple[str, ...]:
        """The commands that check the copies and run preprocessing on them."""
        if not self.written:
            return ()
        target = _quote(self.target)
        output = _quote(default_output_folder(self.target))
        labels = (
            " --ome-zarr-labels" if self.record and any(i.masks for i in self.record.images) else ""
        )
        roles = self.suggested_roles or "<ROLES>"
        return (
            f"morphobricks-preprocessing check {target}{labels}",
            f"morphobricks-preprocessing run {target} --axes {roles} --output {output}{labels}",
        )

    @property
    def next_command(self) -> str:
        """The commands of :attr:`next_commands`, one per line."""
        return "\n".join(self.next_commands)


def default_conversion_target(experiment_root: str | os.PathLike) -> Path:
    """Return the default copy tree, ``<experiment>_ome_zarr`` beside the experiment."""
    root = Path(experiment_root).resolve()
    return root.with_name(root.name + CONVERSION_FOLDER_SUFFIX)


def copy_relative_path(source_relative_path: str, is_ome_zarr: bool = False) -> str:
    """Return a copy's path in the copy tree: the same folders, ``<name>.ome.zarr``.

    ``"ctrl/rep1/img_01.tif"`` gives ``"ctrl/rep1/img_01.ome.zarr"`` (``.ome.tif`` loses
    its whole suffix); an OME-Zarr image keeps its own name. The name without the
    extension is the ``position_id``, so the copy has the source's position.
    """
    if is_ome_zarr:
        return source_relative_path
    path = PurePosixPath(source_relative_path)
    return (path.parent / (position_id(path.name) + OME_ZARR_SUFFIX)).as_posix()


def is_valid_label_name(name: str) -> bool:
    """Return True if a mask type can be the name of an OME-NGFF label image.

    The name is a Zarr group name inside ``labels``: not empty, without a slash or
    backslash or control characters, not starting with ``.`` (hidden, and Zarr's own
    metadata files), and without leading or trailing whitespace.
    """
    return (
        bool(name)
        and name == name.strip()
        and not name.startswith(".")
        and _LABEL_NAME.fullmatch(name) is not None
    )


def sizes_by_role(
    sizes: PhysicalSizes, meta: TiffMetadata, mapping: AxisMapping
) -> tuple[PhysicalSizes, tuple[str, ...]]:
    """Give each role the size of the stored dimension that has it (B2).

    The header gives sizes by its own letters (:func:`.physical_sizes.read_physical_sizes`).
    A role takes the size of the dimension it is confirmed on when that dimension's named
    letter is the role, or when the two are Y and X (a swap: the X role gets the header's
    Y size, which belongs to that dimension). A dimension the header does not name, or
    names as another kind of axis (a header Z confirmed as T), gives no size: the copy
    writes the axis as unknown, never a z-step as a time step.

    Returns
    -------
    tuple
        The sizes by role, and the sizes deliberately not carried, as ``"Z->T"``.
    """
    named = named_axes(meta)
    values = {"X": sizes.x, "Y": sizes.y, "Z": sizes.z, "T": sizes.t}
    taken: dict[str, str] = {}
    dropped: list[str] = []
    for letter, role in zip(named, mapping.roles, strict=True):
        if role not in values:
            continue
        if letter == role or (letter in "YX" and role in "YX"):
            taken[role] = letter
        elif letter in values and values[letter] is not None:
            dropped.append(f"{letter}->{role}")
    unsupported = dict(sizes.unsupported_units)
    result = PhysicalSizes(
        x=values[taken["X"]] if "X" in taken else None,
        y=values[taken["Y"]] if "Y" in taken else None,
        z=values[taken["Z"]] if "Z" in taken else None,
        t=values[taken["T"]] if "T" in taken else None,
        time_unit=sizes.time_unit if "T" in taken else None,
        x_unit=sizes.unit(taken["X"]) if "X" in taken else None,
        y_unit=sizes.unit(taken["Y"]) if "Y" in taken else None,
        z_unit=sizes.unit(taken["Z"]) if "Z" in taken else None,
        unsupported_units=tuple(
            (role, unsupported[letter])
            for role, letter in sorted(taken.items())
            if letter in unsupported
        ),
    )
    return result, tuple(dropped)


@dataclass(frozen=True)
class _MaskPlan:
    """How one TIFF mask is written: its full ``tifffile`` axes and their roles."""

    mask: MaskFile
    path: Path
    axes: str
    shape: tuple[int, ...]
    roles: tuple[str | None, ...]
    header_axes: str
    compact_roles: str
    rule: str
    size: int
    mtime_ns: int

    def converted(self) -> ConvertedMask:
        return ConvertedMask(
            self.mask.mask_type,
            self.mask.path,
            self.mask.source,
            self.size,
            self.mtime_ns,
            self.header_axes,
            self.compact_roles,
            self.rule,
        )


def convert_experiment(
    experiment_root: str | os.PathLike,
    mapping: AxisMapping,
    target: str | os.PathLike | None = None,
    *,
    masks_folder: str | os.PathLike | None = None,
    mask_axes: Mapping[str, str] | None = None,
    missing_as_singleton: bool = False,
    exclude: Iterable[str] = (),
    settings: ConversionSettings | None = None,
    output_folder: str | os.PathLike | None = None,
    memory_limit: int = DEFAULT_MEMORY_LIMIT,
    progress: Callable[[int, int, str], None] | None = None,
) -> ConversionResult:
    """Save every analysed image of an experiment as OME-Zarr with the confirmed axes.

    The same preflight and axis review as a run (:func:`.run.preflight`,
    :func:`.axes.review_axes`). The three axis errors are expected here and allowed: the
    confirmed mapping is what the copies are written with. Every other error stops the
    conversion before anything is written: unreadable files, ``unsupported_axis``,
    ``inconsistent_mapping``, channel counts, folder errors and mask errors.

    Parameters
    ----------
    experiment_root
        The experiment folder; it is never written to.
    mapping
        The axis mapping the user confirmed for the experiment.
    target
        The copy tree, a new folder; defaults to :func:`default_conversion_target`. It must
        not be the experiment, the masks folder or an output folder, nor inside one, nor
        hold the experiment. A folder that is not empty is refused unless its conversion
        record names the same experiment: it is then updated (unchanged copies kept,
        changed ones written again, copies of images no longer analysed removed).
    masks_folder
        The masks folder (contract §15). Every mask paired with an image is written as a
        label image of its copy.
    mask_axes
        The axes the user states for the masks of a mask type, ``{type: roles}``: one role
        (``Y``, ``X``, ``Z`` or ``T``) per axis of the mask's header with length-1 axes
        other than Y and X left out (``"TYX"`` for a ``QYX`` plain stack of time points).
        Masks of other types follow their image's confirmed axes.
    missing_as_singleton
        The user confirmed that images lacking some axes have one plane along them.
    exclude
        Images to leave out; they are not copied.
    settings
        How the copies are written (chunk size, compression, smaller levels).
    output_folder
        The output folder of the run on the experiment (default
        :func:`.discovery.default_output_folder`); the target may not be inside it.
    memory_limit
        Working-memory limit per plane while reading, in bytes.
    progress
        Called as ``progress(done, total, relative_path)`` before each image is copied,
        and ``progress(total, total, "")`` at the end.

    Returns
    -------
    ConversionResult
        With the record written, or the errors and ``written`` False.

    Raises
    ------
    ValueError
        If ``missing_as_singleton`` is not True or False.
    """
    if not isinstance(missing_as_singleton, bool):
        raise ValueError(
            f"missing_as_singleton must be True or False, found {missing_as_singleton!r}"
        )
    root = Path(experiment_root).resolve()
    target = default_conversion_target(root) if target is None else Path(target).resolve()
    output = default_output_folder(root) if output_folder is None else Path(output_folder).resolve()
    settings = settings or ConversionSettings()
    masks = None if masks_folder is None else Path(masks_folder).resolve()
    # The label images of OME-Zarr images are listed too: they are copied with their image.
    pre = preflight(root, output, exclude=exclude, masks_folder=masks, ome_zarr_labels=True)
    review = review_axes(pre.metadata, mapping, missing_as_singleton=missing_as_singleton)
    headers = {f.relative_path: f for f in pre.metadata.files}

    issues: list[ValidationIssue] = [
        *(i for i in pre.discovery.issues if i.code != "output_inside_experiment"),
        *pre.exclusion_issues,
        *(i for i in review.combined_issues(pre.metadata) if i.code not in AXIS_ERROR_CODES),
    ]
    issues.extend(_target_issues(root, target, masks, output))
    if not zarr_available():
        issues.append(
            error(
                "zarr_not_installed",
                "Saving the images as OME-Zarr needs the zarr package, which is not installed.",
                action='Install it with pip install "morphobricks-preprocessing[zarr]".',
            )
        )
    previous = None
    if not has_errors(issues) and target.exists():
        previous, problem = _previous_record(target, root)
        if problem is not None:
            issues.append(problem)

    mask_problems, plans, copied_labels = _plan_masks(
        pre, review, headers, dict(mask_axes or {}), masks
    )
    issues.extend(mask_problems)
    if mask_axes and masks is None:
        issues.append(
            error(
                "invalid_mask_axes",
                "Mask axes were stated, but no masks folder was given.",
                sorted(mask_axes),
                "Give the masks folder, or leave out the mask axes.",
            )
        )
    estimate = estimate_conversion(root, pre.metadata.files, settings)

    def stop() -> ConversionResult:
        return ConversionResult(
            target,
            False,
            _sorted(issues),
            axis_errors=review.axis_errors,
            estimate=estimate,
        )

    if has_errors(issues):
        return stop()

    entries: dict[str, ConvertedImage] = {}
    to_write: list[str] = []
    kept: list[str] = []
    for rel, file_mapping in sorted(review.file_mappings.items()):
        meta = headers[rel]
        size, mtime = source_fingerprint(root / rel)
        masks_here = tuple(p.converted() for p in plans.get(rel, ())) + tuple(
            _copied_mask(m, root) for m in copied_labels.get(rel, ())
        )
        entry = ConvertedImage(
            source_path=rel,
            copy_path=copy_relative_path(rel, meta.is_ome_zarr),
            kind="ome-zarr" if meta.is_ome_zarr else "tiff",
            size=size,
            mtime_ns=mtime,
            header_axes=meta.axes,
            named_axes=named_axes(meta),
            confirmed_roles=file_mapping.roles_string,
            axis_error=header_axis_issue(meta, file_mapping),
            masks=tuple(sorted(masks_here, key=lambda m: m.mask_type)),
        )
        before = previous.image(rel) if previous is not None else None
        if (
            before is not None
            and previous.settings == settings
            and _same_source(before, entry)
            and (target / entry.copy_path).is_dir()
        ):
            entries[rel] = before
            kept.append(rel)
        else:
            entries[rel] = entry
            to_write.append(rel)

    created = not target.exists()
    target.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=_STAGING + "-", dir=target))
    try:
        for done, rel in enumerate(to_write):
            if progress is not None:
                progress(done, len(to_write), rel)
            entry = entries[rel]
            destination = staging / entry.copy_path
            try:
                if entry.kind == "tiff":
                    entries[rel] = _write_tiff_copy(
                        root / rel,
                        destination,
                        entry,
                        headers[rel],
                        review.file_mappings[rel],
                        settings,
                        memory_limit,
                    )
                else:
                    shutil.copytree(root / rel, destination)
                for plan in plans.get(rel, ()):
                    _write_label(destination, plan, settings, memory_limit)
            except (PlaneReadError, ValueError, OSError) as exc:
                logger.warning("Cannot save %s as OME-Zarr", rel, exc_info=True)
                issues.append(
                    error(
                        "conversion_failed",
                        f"{rel} could not be saved as OME-Zarr: {exc}",
                        [rel],
                        "Check that the image and its masks can be read; the technical "
                        "details are in the log. Nothing was changed in the copy folder.",
                    )
                )
                shutil.rmtree(staging, ignore_errors=True)
                if created and not any(target.iterdir()):
                    target.rmdir()
                return stop()
        if progress is not None:
            progress(len(to_write), len(to_write), "")
        removed = _publish(
            target, staging, [entries[rel].copy_path for rel in to_write], previous, entries
        )
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    record = ConversionRecord(
        source_experiment=root.as_posix(),
        masks_folder=None if masks is None else masks.as_posix(),
        mapping=mapping,
        missing_as_singleton=missing_as_singleton,
        settings=settings,
        images=tuple(entries[rel] for rel in sorted(entries)),
        written_at=datetime.now(UTC).isoformat(timespec="seconds"),
        versions=package_versions(),
    )
    write_conversion_record(record, target / CONVERSION_RECORD)
    issues.extend(_scale_warnings(record))
    return ConversionResult(
        target,
        True,
        _sorted(issues),
        record=record,
        converted=tuple(to_write),
        kept=tuple(kept),
        removed=tuple(removed),
        axis_errors=review.axis_errors,
        estimate=estimate,
    )


def write_conversion_record(record: ConversionRecord, path: str | os.PathLike) -> Path:
    """Write a conversion record as JSON, replacing the file only once writing succeeded."""
    path = Path(path)
    text = json.dumps(record.to_dict(), indent=2, ensure_ascii=False) + "\n"
    handle, temporary = tempfile.mkstemp(prefix=f"{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", newline="\n", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return path


def copy_axes(target: str | os.PathLike, record: ConversionRecord) -> str | None:
    """Return the roles the run on the copies is given: the copies' own OME-NGFF axes.

    Every copy names its axes, so the experiment mapping of the second run is its axes'
    prefill (:func:`.axes.prefill_mapping`), with nothing for the user to decide. None
    when the copies cannot be read.
    """
    from .axes import reference_axes
    from .metadata import inspect_ome_zarr

    files = [
        inspect_ome_zarr(Path(target) / image.copy_path, image.copy_path) for image in record.images
    ]
    axes = reference_axes(files)
    return None if axes is None else prefill_mapping(axes).roles_string


def carry_design(
    design: str | os.PathLike, target_output_folder: str | os.PathLike, target: str | os.PathLike
) -> ConfirmedDesign:
    """Carry a confirmed ``experiment_design.csv`` into the run on the copy tree.

    The copies keep the folder names, so the repeat groups confirmed for the experiment
    hold for its copies; nobody answers the questions again. The file is checked with
    :func:`.design.read_confirmed_design` (every group confirmed), its folders must be the
    copy tree's (conditions and biological repeats), and it is copied byte for byte to
    the output folder of the run on the copies, where that run reads it and records its
    SHA-256 anew.

    Parameters
    ----------
    design
        The confirmed design: ``experiment_design.csv`` of the run on the experiment.
    target_output_folder
        The output folder of the run on the copies.
    target
        The copy tree.

    Returns
    -------
    ConfirmedDesign
        The design as read from its new place.

    Raises
    ------
    DesignCheckError
        If the design is not a confirmed design (``read_confirmed_design``'s errors), its
        folders are not the copy tree's (``design_folders_differ``), or the output folder
        already holds another design (``design_exists``).
    """
    from .dataset import biological_repeats

    confirmed = read_confirmed_design(design)
    repeats = biological_repeats(discover_experiment(target, target_output_folder))
    have = {(row.condition, row.biological_repeat) for row in confirmed.design.rows}
    want = {(r.condition, r.biological_repeat) for r in repeats}
    if have != want:
        differ = sorted("/".join(k) for k in have ^ want)
        raise DesignCheckError(
            [
                error(
                    "design_folders_differ",
                    "The experiment design does not describe the folders of the copies: "
                    + ", ".join(differ)
                    + ".",
                    [Path(design).as_posix()],
                    "Confirm the experiment design for the copies in step 1, or with 'run'.",
                )
            ]
        )
    destination = Path(target_output_folder) / DESIGN_FILENAME
    data = Path(design).read_bytes()
    if destination.exists() and destination.read_bytes() != data:
        raise DesignCheckError(
            [
                error(
                    "design_exists",
                    f"The output folder of the copies already holds another experiment design "
                    f"({destination}).",
                    [destination.as_posix()],
                    "Keep it, or delete it to carry this design over.",
                )
            ]
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{DESIGN_FILENAME}.", dir=destination.parent)
    try:
        with os.fdopen(handle, "wb") as fh:
            fh.write(data)
        os.replace(temporary, destination)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return read_confirmed_design(destination, expected_sha256=confirmed.sha256)


def conversion_summary(result: ConversionResult) -> str:
    """Describe a conversion in plain text, for the CLI and the notebook (ASCII-safe)."""
    if not result.written:
        return f"Nothing was written to {result.target}."
    record = result.record
    lines = [
        f"Saved {plural(len(record.images), 'image')} as OME-Zarr in {result.target} "
        f"({len(result.converted)} written, {len(result.kept)} kept, "
        f"{len(result.removed)} removed).",
    ]
    fixed = [i for i in record.images if i.axis_error]
    if fixed:
        lines.append(
            f"  {plural(len(fixed), 'image')} had an axis error; the copies name the axes "
            "you confirmed. Their orientation and axes come from your confirmation, not from "
            "the original files (recorded in the conversion record)."
        )
    masks = [m for i in record.images for m in i.masks]
    if masks:
        rules = {
            rule: sum(m.rule == rule for m in masks)
            for rule in (RULE_IMAGE, RULE_STATED, RULE_COPIED)
        }
        lines.append(
            f"  {plural(len(masks), 'mask')} written as label images: "
            f"{rules[RULE_IMAGE]} following their image's axes, {rules[RULE_STATED]} with the "
            f"axes you stated, {rules[RULE_COPIED]} copied with their OME-Zarr image."
        )
    lines.append(f"  Record: {result.record_path}")
    return "\n".join(lines)


# Masks ----------------------------------------------------------------------------------


def _plan_masks(
    pre: Preflight,
    review: AxisReview,
    headers: Mapping[str, TiffMetadata],
    stated: dict[str, str],
    folder: Path | None,
) -> tuple[list[ValidationIssue], dict[str, list[_MaskPlan]], dict[str, list[MaskFile]]]:
    """Decide the roles of every mask and check that it fits its copy."""
    inventory: MaskInventory = pre.masks
    folder_masks = {m.path for m in inventory.files if m.source == FOLDER}
    issues = []
    for issue in inventory.issues:
        if issue.code in _DECIDED_HERE:
            # Decided below for the masks written here; a copied label image keeps its error.
            paths = [p for p in issue.paths if p not in folder_masks]
            if not paths:
                continue
            issue = replace(issue, paths=tuple(paths))
        issues.append(issue)
    unknown = sorted(set(stated) - set(inventory.types))
    if unknown:
        issues.append(
            error(
                "invalid_mask_axes",
                f"Mask axes were stated for {', '.join(unknown)}, which "
                f"{'is not a mask type' if len(unknown) == 1 else 'are not mask types'} of "
                f"this experiment (found: {', '.join(inventory.types) or 'none'}).",
                unknown,
                "State the axes by the mask type's name, as its folder or suffix gives it.",
            )
        )
    bad_names = sorted(t for t in inventory.types if not is_valid_label_name(t))
    if bad_names:
        issues.append(
            error(
                "invalid_label_name",
                "Some mask types cannot name an OME-Zarr label image (a name with a slash, "
                "starting with a dot, or with spaces around it): " + ", ".join(bad_names) + ".",
                bad_names,
                "Rename the mask folders (or the suffix after the image name).",
            )
        )
    found: dict[str, list[tuple[str, str]]] = {}
    plans: dict[str, list[_MaskPlan]] = {}
    copied: dict[str, list[MaskFile]] = {}
    if has_errors(issues):
        return issues, plans, copied
    for mask in inventory.files:
        meta = headers.get(mask.image)
        mapping = review.file_mappings.get(mask.image)
        if meta is None or mapping is None:
            continue
        if mask.source != FOLDER:
            copied.setdefault(mask.image, []).append(mask)
            continue
        if meta.is_ome_zarr and not (pre.discovery.root / mask.image / ".zattrs").exists():
            found.setdefault("labels_not_writable", []).append((mask.path, ""))
            continue
        plan, problem = _plan_mask(mask, folder, meta, mapping, stated.get(mask.mask_type))
        if problem is not None:
            found.setdefault(problem[0], []).append((mask.path, problem[1]))
        else:
            plans.setdefault(mask.image, []).append(plan)
    for code, items in sorted(found.items()):
        message, action = _MASK_TEXTS[code]
        details = "; ".join(f"{path}: {detail}" for path, detail in items if detail)
        message = message.format(n=plural(len(items), "mask")) + (
            f" ({details})." if details else ""
        )
        issues.append(error(code, message, sorted(path for path, _ in items), action))
    return issues, plans, copied


_MASK_TEXTS = {
    "mask_axes_not_applicable": (
        "The axes of {n} cannot follow their image's confirmed axes: the mask has an axis "
        "its image's header does not have, or one that would be a channel",
        "State the axes of these masks (one role, Y, X, Z or T, per axis of the mask's "
        "header), or save the masks with their axes named like their image's.",
    ),
    "invalid_mask_axes": (
        "The axes stated for {n} do not fit them",
        "State one role per axis of the mask's header (length-1 axes left out): Y and X "
        "once each, and at most one Z and one T. Masks have no channels.",
    ),
    "mask_size_mismatch": (
        "{n} do not have the height and width (Y and X) of their image's copy",
        "Check the mask's axes (stated, or its image's), and that the mask was made from "
        "its image at full resolution.",
    ),
    "mask_planes_mismatch": (
        "{n} do not have the depth slices and time points of their image's copy",
        "Check the mask's axes (stated, or its image's): one mask plane per depth slice "
        "and time point of the image.",
    ),
    "labels_not_writable": (
        "{n} belong to OME-Zarr images stored in Zarr format 3 (OME-NGFF 0.5), to which "
        "this version adds no label images",
        "Add these masks to the image's own labels, or save the image as OME-NGFF 0.4.",
    ),
}


def _plan_mask(
    mask: MaskFile,
    folder: Path,
    image: TiffMetadata,
    mapping: AxisMapping,
    stated: str | None,
) -> tuple[_MaskPlan | None, tuple[str, str] | None]:
    """Give each axis of one mask a role, by the stated axes or its image's; check the fit."""
    path = folder / mask.path
    with tifffile.TiffFile(path) as tif:
        axes, shape = _tiff_axes(tif.series[0])
    compact = [i for i, (a, n) in enumerate(zip(axes, shape, strict=True)) if n != 1 or a in "YX"]
    header = "".join(axes[i] for i in compact)
    if stated is not None:
        roles = stated.upper()
        rule = RULE_STATED
        problem = _mask_roles_problem(roles, len(compact))
        if problem:
            return None, ("invalid_mask_axes", f"axes {header}, stated {stated}: {problem}")
    else:
        rule = RULE_IMAGE
        letters = {
            letter: role
            for letter, role in zip(image.axes, mapping.roles, strict=True)
            if image.axes.count(letter) == 1
        }
        missing = [axes[i] for i in compact if letters.get(axes[i]) is None]
        if missing:
            return None, (
                "mask_axes_not_applicable",
                f"axes {header}; its image's header is {image.axes}, confirmed "
                f"{mapping.roles_string}, which gives no role to {', '.join(sorted(set(missing)))}",
            )
        roles = "".join(letters[axes[i]] for i in compact)
        problem = _mask_roles_problem(roles, len(compact))
        if problem:
            return None, (
                "mask_axes_not_applicable",
                f"axes {header} would be {roles} following its image's axes: {problem}",
            )
    full: list[str | None] = [None] * len(axes)
    for index, role in zip(compact, roles, strict=True):
        full[index] = role
    lengths = {role: shape[index] for index, role in zip(compact, roles, strict=True)}
    expected = _copy_lengths(image, mapping)
    how = f"axes {header} read as {roles}" + (
        " following its image's confirmed axes" if rule == RULE_IMAGE else " as stated"
    )
    if (lengths["Y"], lengths["X"]) != (expected["Y"], expected["X"]):
        return None, (
            "mask_size_mismatch",
            f"{how}: {lengths['Y']} x {lengths['X']}, its copy {expected['Y']} x {expected['X']}",
        )
    planes = {r: lengths.get(r, 1) for r in "ZT"}
    if planes != {r: expected[r] for r in "ZT"}:
        return None, (
            "mask_planes_mismatch",
            f"{how}: Z {planes['Z']}, T {planes['T']}; its copy Z {expected['Z']}, "
            f"T {expected['T']}",
        )
    size, mtime = source_fingerprint(path)
    return (
        _MaskPlan(mask, path, axes, shape, tuple(full), header, roles, rule, size, mtime),
        None,
    )


def _mask_roles_problem(roles: str, count: int) -> str:
    if len(roles) != count:
        return f"{len(roles)} roles for {count} axes"
    if set(roles) - set("YXZT"):
        others = sorted(set(roles) - set("YXZT"))
        return f"{', '.join(others)} is not a mask role (Y, X, Z or T; masks have no channels)"
    if roles.count("Y") != 1 or roles.count("X") != 1:
        return "Y and X are needed once each"
    if roles.count("Z") > 1 or roles.count("T") > 1:
        return "Z and T may appear once each"
    return ""


def _copy_lengths(image: TiffMetadata, mapping: AxisMapping) -> dict[str, int]:
    """Return the copy's Y, X, Z and T lengths (1 for an axis it lacks)."""
    lengths = {}
    for role in "YXZT":
        index = mapping.index_of(role)
        lengths[role] = 1 if index is None else int(image.shape[index])
    return lengths


def _copied_mask(mask: MaskFile, root: Path) -> ConvertedMask:
    size, mtime = source_fingerprint(root / mask.path)
    axes = mask.axes or ""
    return ConvertedMask(
        mask.mask_type, mask.path, mask.source, size, mtime, axes, axes, RULE_COPIED
    )


def _mask_planes(plan: _MaskPlan, memory_limit: int) -> Iterator[tuple[dict[str, int], np.ndarray]]:
    """Yield ``({"z": .., "t": ..}, plane)`` of a TIFF mask, its plane in role order (Y, X).

    Read one page at a time when the planes are stored one per page (the usual layout);
    otherwise the whole mask is read once.
    """
    with tifffile.TiffFile(plan.path) as tif:
        series = tif.series[0]
        y, x = plan.roles.index("Y"), plan.roles.index("X")
        planes = [i for i, role in enumerate(plan.roles) if role in ("Z", "T")]
        pages = len(series.pages)
        split = next(
            (k for k in range(len(plan.shape) + 1) if math.prod(plan.shape[:k]) == pages), None
        )
        whole = None
        for position in np.ndindex(*(plan.shape[i] for i in planes)):
            index: list[int | slice] = [0] * len(plan.shape)
            index[y] = index[x] = slice(None)
            for dim, value in zip(planes, position, strict=True):
                index[dim] = value
            if (
                split is not None
                and pages > 1
                and all(isinstance(i, int) for i in index[:split])
                and (y >= split and x >= split)
            ):
                key = int(np.ravel_multi_index(index[:split], plan.shape[:split]))
                pixels = tif.asarray(key=key, series=0).reshape(plan.shape[split:])
                pixels = pixels[tuple(index[split:])]
            else:
                if whole is None:
                    whole = series.asarray().reshape(plan.shape)
                pixels = whole[tuple(index)]
            if x < y:
                pixels = pixels.T
            coordinates = {
                plan.roles[dim].lower(): value for dim, value in zip(planes, position, strict=True)
            }
            yield coordinates, np.ascontiguousarray(pixels)


def _write_label(
    copy: Path, plan: _MaskPlan, settings: ConversionSettings, memory_limit: int
) -> None:
    """Write one mask as the OME-NGFF 0.4 label image ``<copy>/labels/<mask type>``."""
    attrs = json.loads((copy / ".zattrs").read_text(encoding="utf-8"))
    multiscale = attrs["multiscales"][0]
    axes = [str(a["name"]).lower() for a in multiscale["axes"]]
    level0 = multiscale["datasets"][0]
    shape = json.loads((copy / str(level0["path"]) / ".zarray").read_text(encoding="utf-8"))[
        "shape"
    ]
    label_shape = tuple(1 if a == "c" else int(n) for a, n in zip(axes, shape, strict=True))
    zarr = _zarr()
    kwargs = {"zarr_format": 2} if _zarr_major() >= 3 else {}
    labels = zarr.open_group(str(copy / "labels"), mode="a", **kwargs)
    group = zarr.open_group(str(copy / "labels" / plan.mask.mask_type), mode="w", **kwargs)
    side = settings.chunk_size
    chunks = (
        *(1,) * (len(label_shape) - 2),
        min(label_shape[-2], side),
        min(label_shape[-1], side),
    )
    array = _create_array(group, "0", label_shape, chunks, np.dtype("int32"), _compressor(settings))
    name = f"the mask {plan.mask.path}"
    for coordinates, pixels in _mask_planes(plan, memory_limit):
        check_labels(pixels, name)
        index = tuple(coordinates.get(a, 0) for a in axes if a not in ("y", "x"))
        array[index] = pixels.astype(np.int32)
    group.attrs.update(
        {
            "multiscales": [
                {
                    "version": "0.4",
                    "name": plan.mask.mask_type,
                    "axes": multiscale["axes"],
                    "datasets": [
                        {
                            "path": "0",
                            "coordinateTransformations": level0["coordinateTransformations"],
                        }
                    ],
                    "metadata": {
                        "description": "int32 labels; 0 is the background",
                        **{
                            k: v
                            for k, v in (multiscale.get("metadata") or {}).items()
                            if k == "unknown_scale"
                        },
                    },
                }
            ],
            "image-label": {"version": "0.4", "source": {"image": "../../"}},
        }
    )
    names = list(labels.attrs.get("labels", []))
    if plan.mask.mask_type not in names:
        names.append(plan.mask.mask_type)
    labels.attrs.update({"labels": names})


# Images ---------------------------------------------------------------------------------


def _write_tiff_copy(
    source: Path,
    destination: Path,
    entry: ConvertedImage,
    meta: TiffMetadata,
    mapping: AxisMapping,
    settings: ConversionSettings,
    memory_limit: int,
) -> ConvertedImage:
    """Write one TIFF as OME-Zarr under its confirmed mapping; return its record entry."""
    sizes, dropped = sizes_by_role(read_physical_sizes(source), meta, mapping)
    writer = OmeZarrWriter.create(
        destination,
        mapping,
        plane_layout(source, mapping, memory_limit=memory_limit),
        sizes=sizes,
        channel_names=meta.channel_names,
        name=entry.copy_path,
        settings=settings,
        memory_limit=memory_limit,
    )
    for plane in iter_planes(source, mapping, memory_limit=memory_limit):
        writer.write(plane)
    writer.finish()
    unknown = writer.attributes()["multiscales"][0]["metadata"].get("unknown_scale", [])
    return replace(entry, unknown_scale=tuple(unknown), scale_not_carried=dropped)


def _publish(
    target: Path,
    staging: Path,
    copies: list[str],
    previous: ConversionRecord | None,
    entries: Mapping[str, ConvertedImage],
) -> list[str]:
    """Move the new copies into place and remove copies of images no longer analysed."""
    wanted = {entry.copy_path for entry in entries.values()}
    removed = []
    for image in previous.images if previous is not None else ():
        if image.copy_path in wanted:
            continue
        old = (target / image.copy_path).resolve()
        # A damaged record cannot point the removal outside the copy tree.
        if target in old.parents and old.name.endswith(".zarr") and old.is_dir():
            shutil.rmtree(old)
            removed.append(image.source_path)
    for copy in copies:
        destination = target / copy
        if destination.exists():
            shutil.rmtree(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staging / copy, destination)
    return sorted(removed)


def _same_source(before: ConvertedImage, now: ConvertedImage) -> bool:
    return (
        before.copy_path == now.copy_path
        and before.kind == now.kind
        and (before.size, before.mtime_ns) == (now.size, now.mtime_ns)
        and before.header_axes == now.header_axes
        and before.confirmed_roles == now.confirmed_roles
        and before.masks == now.masks
    )


# Target ---------------------------------------------------------------------------------


def _target_issues(
    root: Path, target: Path, masks: Path | None, output: Path
) -> list[ValidationIssue]:
    places = [("the experiment folder", root), ("the output folder", output)]
    if masks is not None:
        places.append(("the masks folder", masks))
    for name, place in places:
        if target == place or place in target.parents:
            return [
                error(
                    "conversion_target_inside",
                    f"The folder for the OME-Zarr copies ({target}) is {name} or inside it.",
                    [target.as_posix()],
                    "Choose a new folder outside it, for example "
                    f"{default_conversion_target(root)}.",
                )
            ]
        if target in place.parents:
            return [
                error(
                    "conversion_target_inside",
                    f"The folder for the OME-Zarr copies ({target}) holds {name}.",
                    [target.as_posix()],
                    "Choose a new folder beside it, for example "
                    f"{default_conversion_target(root)}.",
                )
            ]
    if target.exists() and not target.is_dir():
        return [
            error(
                "conversion_target_not_empty",
                f"{target} is a file, not a folder for the OME-Zarr copies.",
                [target.as_posix()],
                "Choose a new folder.",
            )
        ]
    return []


def _previous_record(
    target: Path, root: Path
) -> tuple[ConversionRecord | None, ValidationIssue | None]:
    """Return the record of an earlier conversion to ``target``, or why it cannot be updated."""
    contents = [p for p in target.iterdir() if not p.name.startswith(_STAGING)]
    if not contents:
        return None, None
    refusal = error(
        "conversion_target_not_empty",
        f"The folder for the OME-Zarr copies ({target}) is not empty and holds no copies of "
        "this experiment.",
        [target.as_posix()],
        "Choose a new folder, or empty this one.",
    )
    try:
        record = read_conversion_record(target)
    except (FileNotFoundError, ValueError):
        logger.info("No usable conversion record in %s", target, exc_info=True)
        return None, refusal
    if record.source_experiment != root.as_posix():
        return None, error(
            "conversion_target_not_empty",
            f"The folder for the OME-Zarr copies ({target}) holds copies of another experiment "
            f"({record.source_experiment}).",
            [target.as_posix()],
            "Choose a new folder for this experiment's copies.",
        )
    return record, None


def _scale_warnings(record: ConversionRecord) -> list[ValidationIssue]:
    relabelled = [i for i in record.images if i.scale_not_carried]
    if not relabelled:
        return []
    details = "; ".join(
        f"{i.source_path}: {', '.join(i.scale_not_carried)}" for i in relabelled[:3]
    )
    return [
        warning(
            "scale_not_carried",
            f"The header sizes of {plural(len(relabelled), 'image')} were not written to their "
            f"copies because the axes were given another role ({details}): a z-step is not a "
            "time step. Those axes have an unknown scale in the copies.",
            sorted(i.source_path for i in relabelled),
            "Nothing to do if the confirmed axes are right; add the step sizes to the copies' "
            "metadata if they are known.",
        )
    ]


def _quote(path: Path) -> str:
    text = Path(path).as_posix()
    return f'"{text}"' if any(c.isspace() for c in text) else text


def _sorted(issues: list[ValidationIssue]) -> tuple[ValidationIssue, ...]:
    return tuple(sorted(issues, key=lambda issue: not issue.is_error))
