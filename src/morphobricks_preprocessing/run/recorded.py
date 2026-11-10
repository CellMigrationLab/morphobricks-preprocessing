"""A later run reuses every input and setting the earlier run recorded (design v0.4 §1).

One output folder belongs to one experiment. A run given no value for an input uses the
value its folder's manifest records: the experiment root, masks folder, exclusions,
plain-stack confirmations, mask-axis statements, image type, bins, histogram and
intensity settings, ``missing_as_singleton`` and ``verify_content`` (the experiment is the
published ``morphobricks.toml`` in the folder, and the stated calibration its
``calibration.csv``, which a run never writes). A value given explicitly that differs from the
recorded one is used, and an :class:`InputChange` names the change and its effect. The
masks are dropped only on request (``masks_folder=False``, CLI ``--no-masks``).

A manifest of an older version is not read (:mod:`..manifest_versions`): nothing is taken
from it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal

from morphobricks_normalization.checks import IntensityCheckSettings
from morphobricks_normalization.ranges import HistogramSettings

from ..axes import AxisMapping
from ..axis_check import ConfirmedAxes
from ..calibration import CalibrationReview
from ..fingerprint import Fingerprint
from ..manifest import RunManifest
from ..masks.model import OME_ZARR

RECOMPUTED = "every row is recomputed"
UNAFFECTED = "no row is affected"


@dataclass(frozen=True)
class GivenInputs:
    """The inputs given to a run; None means "as recorded" (or the default).

    Attributes
    ----------
    experiment_root
        The experiment folder, resolved.
    mapping
        The axis roles confirmed by the user.
    exclude
        Excluded images, relative to the experiment root.
    masks_folder
        The masks folder, resolved; False drops the recorded masks folder.
    mask_axes
        Mask axes stated per mask type; they add to (and replace per type) the recorded ones.
    image_type, bin_count, missing_as_singleton, intensity_settings, histogram_settings
        As :func:`.pipeline.run_experiment`.
    verify_content
        Strict reuse by content SHA-256 (:class:`.modes.RunMode`).
    """

    experiment_root: Path | None = None
    mapping: AxisMapping | ConfirmedAxes | None = None
    exclude: tuple[str, ...] | None = None
    masks_folder: Path | Literal[False] | None = None
    mask_axes: Mapping[str, str] | None = None
    image_type: str | None = None
    bin_count: int | None = None
    missing_as_singleton: bool | None = None
    intensity_settings: IntensityCheckSettings | None = None
    histogram_settings: HistogramSettings | None = None
    verify_content: bool | None = None


@dataclass(frozen=True)
class InputChange:
    """An input given explicitly that differs from the one the earlier run recorded.

    Attributes
    ----------
    name
        The input, e.g. ``"exclude"``.
    recorded, given
        The two values, in words.
    effect
        What changing it does, e.g. ``"every row is recomputed"``.
    """

    name: str
    recorded: str
    given: str
    effect: str

    def to_dict(self) -> dict:
        """Return the change as JSON-compatible data."""
        return {
            "input": self.name,
            "recorded": self.recorded,
            "given": self.given,
            "effect": self.effect,
        }


@dataclass(frozen=True)
class ResolvedInputs:
    """The inputs a run uses: those given, else those recorded, else the defaults.

    Attributes
    ----------
    experiment_root, mapping, masks_folder, image_type, intensity_settings
        None when neither given nor recorded.
    exclude, mask_axes, missing_as_singleton, histogram_settings
        Never None.
    bin_count
        None for the pixel type's default.
    bins_recorded_for
        The pixel type the recorded bin count was for, when it is the recorded one (a
        bin count is not reused for another pixel type); None when given or default.
    verify_content
        Whether rows are reused only for the same content SHA-256 (False by default).
    changes
        The inputs given that differ from the recorded ones.
    """

    experiment_root: Path | None
    mapping: AxisMapping | ConfirmedAxes | None
    exclude: tuple[str, ...]
    masks_folder: Path | None
    mask_axes: dict[str, str]
    image_type: str | None
    bin_count: int | None
    missing_as_singleton: bool
    intensity_settings: IntensityCheckSettings | None
    histogram_settings: HistogramSettings
    bins_recorded_for: str | None = None
    verify_content: bool = False
    changes: tuple[InputChange, ...] = field(default=())


def usable(manifest: RunManifest | None) -> RunManifest | None:
    """Return the manifest when its inputs can be reused: every manifest that could be read.

    Only the current manifest version is read (:mod:`..manifest_versions`), so a manifest that
    reads is usable; an older one is not read at all.
    """
    return manifest


def normalise_relative_path(path: str) -> str:
    """Return a relative path with ``/`` separators and no leading ``./``."""
    path = str(path).replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    return path


def resolve_inputs(given: GivenInputs, previous: RunManifest | None) -> ResolvedInputs:
    """Resolve the run's inputs: given, else recorded in ``previous``, else the default.

    Parameters
    ----------
    given
        The inputs given to the run.
    previous
        The earlier run's manifest, or None.

    Returns
    -------
    ResolvedInputs
        With every given input that differs from its recorded value in ``changes``.
    """
    before = usable(previous)
    if before is None:
        return _defaults(given)
    settings = before.settings
    exclude = None if given.exclude is None else _exclusions(given.exclude)
    recorded_masks = recorded_masks_folder(before)
    masks = recorded_masks if given.masks_folder is None else (given.masks_folder or None)
    # Dropping the masks folder drops the statements made for its masks; label images stay.
    recorded_axes = recorded_mask_axes(before, labels_only=given.masks_folder is False)
    mask_axes = {**recorded_axes, **dict(given.mask_axes or {})}
    resolved = ResolvedInputs(
        experiment_root=given.experiment_root or Path(before.experiment_root),
        mapping=_mapping(given, before),
        exclude=settings.exclude if exclude is None else exclude,
        masks_folder=masks,
        mask_axes=mask_axes,
        image_type=given.image_type or before.image_type,
        bin_count=settings.bin_count if given.bin_count is None else given.bin_count,
        missing_as_singleton=_first(given.missing_as_singleton, settings.missing_as_singleton),
        intensity_settings=given.intensity_settings or before.intensity_checks,
        histogram_settings=given.histogram_settings or settings.histogram,
        bins_recorded_for=before.dtype if given.bin_count is None else None,
        verify_content=_first(given.verify_content, settings.verify_content),
    )
    return _with_changes(resolved, given, before)


def recorded_confirmation(manifest: RunManifest) -> ConfirmedAxes:
    """Return the recorded mapping, confirmed for the plain stacks the earlier run read.

    A plain stack's confirmation covers it only as it was then (its recorded
    fingerprint), in the recorded experiment folder; a header-named file needs none.

    Parameters
    ----------
    manifest
        The earlier run's manifest (version 2).

    Returns
    -------
    ConfirmedAxes
    """
    files = {
        entry.relative_path: Fingerprint.recorded(entry)
        for entry in manifest.files
        if entry.axes_from == "confirmed"
    }
    return ConfirmedAxes(manifest.mapping, files)


def _mapping(given: GivenInputs, before: RunManifest) -> AxisMapping | ConfirmedAxes:
    """Return the mapping given, else the recorded one for the earlier run's own files.

    A plain stack's confirmation covers the files it was given for: in another experiment
    folder they are other files, so none of them is covered (each is confirmed again).
    """
    if given.mapping is not None:
        return given.mapping
    recorded = recorded_confirmation(before)
    if _moved(given, before):
        return ConfirmedAxes(recorded.mapping, {})
    return recorded


def _moved(given: GivenInputs, before: RunManifest) -> bool:
    """Return True when the experiment folder given is not the one the earlier run read."""
    root = given.experiment_root
    return root is not None and _comparable(root, "experiment_root") != _comparable(
        before.experiment_root, "experiment_root"
    )


def calibration_change(
    recorded: RunManifest | None, review: CalibrationReview
) -> tuple[InputChange, ...]:
    """Return the change of ``calibration.csv`` since the earlier run, if any.

    The file is the input (design v0.4 §3): added, edited or removed since the earlier run
    recorded its SHA-256. The effect names how many images are recorded with other pixel
    sizes; no row depends on them.

    Parameters
    ----------
    recorded
        The earlier run's manifest (version 2), or None.
    review
        The calibration as read now.

    Returns
    -------
    tuple
        One :class:`InputChange` named ``"calibration"``, or nothing.
    """
    if recorded is None or recorded.calibration_sha256 == review.sha256:
        return ()
    before = {entry.relative_path: entry.pixel_sizes for entry in recorded.files}
    moved = [
        path for path, sizes in review.sizes.items() if path in before and before[path] != sizes
    ]
    was = "none" if recorded.calibration_sha256 is None else "calibration.csv as recorded"
    if review.sha256 is None:
        now = "none (calibration.csv removed or refused)"
    else:
        now = "calibration.csv " + ("added" if recorded.calibration_sha256 is None else "edited")
    images = "image" if len(moved) == 1 else "images"
    effect = f"no row is affected; the pixel sizes of {len(moved)} {images} change"
    return (InputChange("calibration", was, now, effect),)


def recorded_mask_axes(manifest: RunManifest, *, labels_only: bool = False) -> dict[str, str]:
    """Return the mask axes stated per mask type in the earlier run, ``{type: roles}``.

    Parameters
    ----------
    manifest
        The earlier run's manifest (version 2).
    labels_only
        True for the statements of OME-Zarr label images only: those of the masks folder
        go with it when it is dropped (``masks_folder=False``, ``--no-masks``).

    Returns
    -------
    dict
        Without ``YX``: a release candidate's shared 2-D mask, an option removed by the
        owner decision of 5 November 2026. Reapplied, it would refuse every later run with
        no way to withdraw it; left out, each such mask is checked as the 2-D mask it is.
    """
    masks = manifest.masks or {}
    return {
        str(entry["type"]): str(entry["roles"])
        for entry in masks.get("files", ())
        if entry.get("axes_from") == "stated"
        and entry.get("roles")
        and str(entry["roles"]) != "YX"
        and (not labels_only or entry.get("source") == OME_ZARR)
    }


def recorded_masks_folder(manifest: RunManifest) -> Path | None:
    """Return the masks folder the earlier run recorded, or None (version 2 manifest)."""
    folder = (manifest.masks or {}).get("folder")
    return None if folder is None else Path(folder)


def _defaults(given: GivenInputs) -> ResolvedInputs:
    """Resolve the inputs without an earlier run: given, else the default."""
    return ResolvedInputs(
        experiment_root=given.experiment_root,
        mapping=given.mapping,
        exclude=_exclusions(given.exclude or ()),
        masks_folder=given.masks_folder or None,
        mask_axes=dict(given.mask_axes or {}),
        image_type=given.image_type,
        bin_count=given.bin_count,
        missing_as_singleton=bool(given.missing_as_singleton),
        intensity_settings=given.intensity_settings,
        histogram_settings=given.histogram_settings or HistogramSettings(),
        # Content hashing is on unless the caller opted out (#159); an earlier run's choice is
        # kept by _with_changes, so an existing output is not silently recomputed.
        verify_content=True if given.verify_content is None else bool(given.verify_content),
    )


def _with_changes(
    resolved: ResolvedInputs, given: GivenInputs, before: RunManifest
) -> ResolvedInputs:
    """Return ``resolved`` with the given inputs that differ from the recorded ones."""
    settings = before.settings
    found = [
        _change("experiment_root", before.experiment_root, given.experiment_root, _root_effect),
        _change("axis mapping", _roles(before.mapping), _given_roles(given.mapping), RECOMPUTED),
        _change("exclude", settings.exclude, given.exclude, _exclude_effect),
        _change(
            "masks_folder",
            recorded_masks_folder(before),
            _given_masks(given.masks_folder),
            "no row is affected; the masks are recorded again and mask_relationships.json "
            "is removed",
        ),
        _change("mask_axes", *_mask_axes_change(given, before), UNAFFECTED),
        _change("image_type", before.image_type, given.image_type, UNAFFECTED),
        _change("bin_count", settings.bin_count, given.bin_count, RECOMPUTED),
        _change(
            "missing_as_singleton",
            settings.missing_as_singleton,
            given.missing_as_singleton,
            "the rows of images lacking some axes are recomputed",
        ),
        _change(
            "intensity_settings", before.intensity_checks, given.intensity_settings, UNAFFECTED
        ),
        _change("histogram_settings", settings.histogram, given.histogram_settings, RECOMPUTED),
        _change("verify_content", settings.verify_content, given.verify_content, RECOMPUTED),
    ]
    changes = tuple(change for change in found if change is not None)
    return replace(resolved, changes=changes)


_root_effect = (
    "every row is recomputed (the images are read from the new place); plain stacks must be "
    "confirmed again"
)
_exclude_effect = (
    "the rows of newly excluded images are removed; images no longer excluded are read"
)


def _change(name: str, recorded: object, given: object, effect: str) -> InputChange | None:
    """Return the change of one input, or None when it was not given or is the same."""
    if given is None or _comparable(given, name) == _comparable(recorded, name):
        return None
    return InputChange(name, _words(recorded), _words(given), effect)


def _comparable(value: object, name: str) -> object:
    """Return a value in a form that compares equal when the input is the same."""
    if name == "exclude":
        return _exclusions(value or ())
    if isinstance(value, Path):
        return value.as_posix()
    if name == "experiment_root" and isinstance(value, str):
        return Path(value).as_posix()
    if name == "mask_axes":
        return dict(value or {})
    return value


def _words(value: object) -> str:
    """Describe an input value in a few words for the report."""
    if value is None:
        return "none"
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, tuple | list):
        return ", ".join(str(item) for item in value) or "none"
    if isinstance(value, Mapping):
        return ", ".join(f"{key}={item}" for key, item in sorted(value.items())) or "none"
    if hasattr(value, "to_dict"):
        return ", ".join(f"{key}={item}" for key, item in value.to_dict().items())
    return str(value)


def _roles(mapping: AxisMapping) -> str:
    """Describe a mapping as ``"ZCYX as ZCYX"``."""
    return f"{mapping.axes} as {mapping.roles_string}"


def _given_roles(mapping: AxisMapping | ConfirmedAxes | None) -> str | None:
    """Describe a given mapping, or None when none was given."""
    if mapping is None:
        return None
    return _roles(mapping.mapping if isinstance(mapping, ConfirmedAxes) else mapping)


def _given_masks(folder: Path | Literal[False] | None) -> Path | str | None:
    """Describe a given masks folder: None when not given, ``"none"`` when dropped."""
    if folder is False:
        return "none (dropped)"
    return folder


def _mask_axes_change(
    given: GivenInputs, before: RunManifest
) -> tuple[dict[str, str], dict[str, str] | None]:
    """Return the recorded mask axes the run keeps, and those after the given statements.

    The second is None when no statement was given. A dropped masks folder takes its
    statements with it (:func:`resolve_inputs`); that is the ``masks_folder`` change, so
    the statements compared are those kept.
    """
    kept = recorded_mask_axes(before, labels_only=given.masks_folder is False)
    if not given.mask_axes:
        return kept, None
    return kept, {**kept, **dict(given.mask_axes)}


def _exclusions(paths: Iterable[str]) -> tuple[str, ...]:
    """Return excluded paths normalised and sorted, without repeats."""
    return tuple(sorted({normalise_relative_path(path) for path in paths}))


def _first(given: bool | None, recorded: bool) -> bool:
    """Return the given flag, or the recorded one when none was given."""
    return recorded if given is None else given
