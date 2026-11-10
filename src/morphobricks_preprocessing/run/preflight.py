"""Pass 1: discover the experiment and read its headers, without pixels (contract §1, §2).

:func:`preflight` walks the folder, inspects every header, resolves each image's pixel
sizes with the output folder's ``calibration.csv`` (design v0.4 §3) and suggests an axis
mapping; :func:`sample_planes` reads the preview planes. Moved from ``run.py`` (PP-3).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path

from ..axes import (
    AxisMapping,
    AxisReview,
    file_mapping_for,
    prefill_experiment_mapping,
)
from ..calibration import CalibrationReview, fit_to_roles, review_calibration
from ..calibration_io import load_calibration
from ..conversion_record import ConversionReview, review_conversion
from ..dataset import DatasetReport, check_dataset
from ..discovery import DiscoveryResult, default_output_folder, discover_experiment
from ..fingerprint import Fingerprint, current_fingerprint
from ..mask_names import MASK_NAME_CODES, mask_name_issues
from ..masks import MaskFile, MaskInventory, find_masks
from ..metadata import MetadataReport, check_metadata, inspect_image, inspect_images
from ..name_issues import non_nfc_folders_issue
from ..pixel_size_checks import PIXEL_SIZE_CODES, pixel_size_issues
from ..planes import (
    DEFAULT_MEMORY_LIMIT,
    Plane,
    PlaneReadError,
    read_sample_plane,
)
from ..validation import ValidationIssue, error, has_errors, plural, warning
from .recorded import normalise_relative_path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Preflight:
    """Pass 1: what was found in the experiment, before any pixel is read.

    Attributes
    ----------
    discovery
        Folder structure and labels.
    metadata
        Image headers of the images to analyse (excluded images left out).
    excluded
        Images the user excluded, sorted.
    exclusion_issues
        Warnings about excluded images, and an error if nothing is left.
    suggested_mapping
        Axis mapping prefilled from the headers, or None if the images'
        dimensions conflict.
    dataset
        Images per condition and biological repeat, and the warnings about
        their balance.
    masks
        The masks of the images (contract §15), found from their headers, when
        masks were asked for; None otherwise.
    calibration
        Each analysed image's pixel sizes from its header and ``calibration.csv``
        (:mod:`.calibration`). Its issues, and the pixel-size checks on these sizes,
        are among the metadata's issues.
    conversion
        The conversion record of a tree of named-axis copies (design v0.4 §5), checked
        against the sources and copies; None for an ordinary experiment.
    header_fingerprints
        With ``bind_headers``: each inspected image's fingerprint, taken before and after
        its header was read, or None when the two differ (or it went missing): the
        header read is then not known to describe the file as fingerprinted. Empty
        otherwise.
    """

    discovery: DiscoveryResult
    metadata: MetadataReport
    excluded: tuple[str, ...] = ()
    exclusion_issues: tuple[ValidationIssue, ...] = ()
    suggested_mapping: AxisMapping | None = None
    dataset: DatasetReport = field(default_factory=DatasetReport)
    masks: MaskInventory | None = None
    calibration: CalibrationReview = field(default_factory=CalibrationReview)
    conversion: ConversionReview | None = None
    header_fingerprints: Mapping[str, Fingerprint | None] = field(default_factory=dict)

    @property
    def issues(self) -> tuple[ValidationIssue, ...]:
        """All issues found so far, errors first."""
        merged = [
            *self.discovery.issues,
            *self.exclusion_issues,
            *self.metadata.issues,
            *self.dataset.issues,
            *(self.masks.issues if self.masks is not None else ()),
            *(self.conversion.issues if self.conversion is not None else ()),
        ]
        return tuple(sorted(merged, key=lambda issue: not issue.is_error))

    @property
    def ok(self) -> bool:
        """True if no issue blocks processing."""
        return not has_errors(self.issues)


@dataclass(frozen=True)
class SamplePlane:
    """A plane shown in the axis preview.

    Attributes
    ----------
    relative_path
        The file the plane comes from.
    axes
        The file's axis labels; one sample is taken per arrangement of axes.
    plane
        The plane, or None if it could not be read.
    error
        Why the plane could not be read, in plain language.
    """

    relative_path: str
    axes: str
    plane: Plane | None = None
    error: str | None = None


def sample_planes(
    pre: Preflight,
    mapping: AxisMapping,
    *,
    memory_limit: int = DEFAULT_MEMORY_LIMIT,
) -> list[SamplePlane]:
    """Read one preview plane per format and arrangement of axes, from the first image.

    Images lacking some of the mapping's axes are previewed with those axes as
    length 1, so the user can see them before confirming that this is right.
    """
    samples = []
    seen: set[tuple[str, str]] = set()
    for f in pre.metadata.files:
        if not f.readable or (f.kind, f.axes) in seen:
            continue
        seen.add((f.kind, f.axes))
        file_mapping = file_mapping_for(f, mapping)
        if file_mapping is None:
            samples.append(
                SamplePlane(f.relative_path, f.axes, error="The axis mapping does not fit it.")
            )
            continue
        try:
            plane = read_sample_plane(
                pre.discovery.root / f.relative_path, file_mapping, memory_limit=memory_limit
            )
        except PlaneReadError as exc:
            logger.debug("Cannot preview %s", f.relative_path, exc_info=True)
            samples.append(SamplePlane(f.relative_path, f.axes, error=str(exc)))
            continue
        samples.append(SamplePlane(f.relative_path, f.axes, plane))
    return samples


def preflight(
    experiment_root: str | os.PathLike,
    output_folder: str | os.PathLike | None = None,
    *,
    exclude: Iterable[str] = (),
    masks_folder: str | os.PathLike | None = None,
    mask_axes: Mapping[str, str] | None = None,
    bind_headers: bool = False,
    progress: Callable[[str, int, int, str], None] | None = None,
) -> Preflight:
    """Pass 1: check the folder and the image headers without reading pixels.

    Parameters
    ----------
    experiment_root
        The experiment folder.
    output_folder
        The output folder; defaults to :func:`default_output_folder`.
    exclude
        Relative paths of images to leave out (``/`` or backslash separators),
        for example damaged files.
    masks_folder
        The masks folder (contract §15): one subfolder per mask type, each
        mirroring the experiment's folders. The masks are paired with the images
        and checked from their headers. The label images of OME-Zarr images are
        always used as masks, also without a masks folder (design v0.4 §2).
    mask_axes
        The axes stated per mask type, ``{type: roles}`` (:func:`.masks.find_masks`).
    bind_headers
        Fingerprint each image before and after its header is read
        (:attr:`Preflight.header_fingerprints`), so a caller that writes from the headers
        (:func:`.conversion_plan.plan_conversion`) never mixes a header with a later file.
        Each mask is bound the same way (:attr:`.masks.MaskFile.bound`, issue #160).
    progress
        Called as ``progress(phase, done, total, path)`` while the check runs, with a phase of
        :data:`.phases.PHASES` (``folders``, ``headers``, ``masks``, ``acquisitions``): a phase
        is announced as ``(phase, 0, total, "")`` and ends with ``(phase, total, total, "")``;
        the headers phase also reports ``(phase, done, total, path)`` before each image, the
        masks phase ``("masks", done, total, mask_path)`` before each mask's header, and the
        folders phase ``("folders", 0, 1, folder)`` before each folder is listed.
        :func:`.phases.progress_text` words it. It changes nothing about the result.

    Returns
    -------
    Preflight
    """
    report = progress or _no_progress
    root = Path(experiment_root).resolve()
    output = default_output_folder(root) if output_folder is None else Path(output_folder)
    report("folders", 0, 1, "")
    discovery = discover_experiment(
        root, output, progress=lambda folder: report("folders", 0, 1, folder)
    )
    # Every run is described: a folder label that is not NFC cannot be a source folder.
    discovery = replace(
        discovery, issues=(*discovery.issues, *non_nfc_folders_issue(discovery.leaves))
    )
    report("folders", 1, 1, "")
    excluded = tuple(sorted({normalise_relative_path(path) for path in exclude}))
    discovery = _without_excluded_masks(discovery, excluded)
    images = [path for path in discovery.images if path not in excluded]
    inspected, bound = _inspected(root, images, bind_headers, progress)
    metadata, calibration = _calibrated(inspected, discovery.images, output)
    report("masks", 0, 1, "")
    masks = _masks(
        discovery,
        metadata,
        masks_folder,
        mask_axes,
        output,
        bind_headers,
        lambda done, total, path: report("masks", done, total, path),
    )
    report("masks", 1, 1, "")
    report("acquisitions", 0, 1, "")
    result = Preflight(
        discovery=discovery,
        metadata=metadata,
        excluded=excluded,
        exclusion_issues=tuple(_exclusion_issues(discovery, excluded)),
        suggested_mapping=prefill_experiment_mapping(metadata),
        dataset=check_dataset(discovery, excluded=excluded),
        masks=masks,
        calibration=calibration,
        conversion=review_conversion(
            root, discovery.images, images, [(m.image, m.path) for m in _mask_files(masks)]
        ),
        header_fingerprints=bound,
    )
    report("acquisitions", 1, 1, "")
    return result


def _mask_files(masks: MaskInventory | None) -> tuple[MaskFile, ...]:
    """Return the masks found for the analysed images (none when masks were not asked for)."""
    return () if masks is None else masks.files


def _no_progress(phase: str, done: int, total: int, path: str) -> None:
    """Report nothing (the default of :func:`preflight`'s ``progress``)."""


def _inspected(
    root: Path,
    images: list[str],
    bind: bool,
    progress: Callable[[str, int, int, str], None] | None = None,
) -> tuple[MetadataReport, dict[str, Fingerprint | None]]:
    """Inspect the headers; with ``bind``, fingerprint each image before and after.

    With ``progress``, each image is reported before its header is read (phase ``headers``).
    """
    if not bind and progress is None:
        return inspect_images(root, images), {}
    report = progress or _no_progress
    files, bound = [], {}
    ordered = sorted(images)
    report("headers", 0, len(ordered), "")
    for done, rel in enumerate(ordered):
        report("headers", done, len(ordered), rel)
        before = current_fingerprint(root / rel) if bind else None
        files.append(inspect_image(root / rel, rel))
        if bind:
            after = current_fingerprint(root / rel)
            bound[rel] = before if before is not None and before == after else None
    report("headers", len(ordered), len(ordered), "")
    return MetadataReport(files=tuple(files), issues=tuple(check_metadata(files))), bound


def remasked(
    pre: Preflight,
    masks_folder: str | os.PathLike | None,
    mask_axes: Mapping[str, str] | None,
    output_folder: str | os.PathLike,
    progress: Callable[[int, int, str], None] | None = None,
) -> Preflight:
    """Return the preflight with its masks paired again, as :func:`preflight` pairs them.

    The notebook calls it when the user changes the masks folder or states the axes of a
    mask type, so the masks follow without checking the folder again (the answers given
    so far are kept). Only the masks' headers are read. A preflight whose headers were bound
    (``bind_headers``: :attr:`Preflight.header_fingerprints` is not empty) binds the masks
    paired again the same way (:attr:`.masks.MaskFile.bound`), so their evidence is of the
    masks as this pairing read them.

    Parameters
    ----------
    pre
        The preflight.
    masks_folder, mask_axes
        As :func:`preflight`.
    output_folder
        The output folder (it may not be inside the masks folder).
    progress
        Called as ``progress(done, total, mask_path)`` before each mask's header is read.

    Returns
    -------
    Preflight
        With :attr:`Preflight.masks` replaced.
    """
    masks = _masks(
        pre.discovery,
        pre.metadata,
        masks_folder,
        mask_axes,
        Path(output_folder),
        bind=bool(pre.header_fingerprints),
        progress=progress,
    )
    return replace(pre, masks=masks)


def _masks(
    discovery: DiscoveryResult,
    metadata: MetadataReport,
    masks_folder: str | os.PathLike | None,
    mask_axes: Mapping[str, str] | None,
    output: Path,
    bind: bool = False,
    progress: Callable[[int, int, str], None] | None = None,
) -> MaskInventory | None:
    """Pair the analysed images with their masks; None without masks folder or label image."""
    masks = find_masks(
        masks_folder,
        discovery,
        metadata.files,
        mask_axes=mask_axes,
        output_folder=output,
        bind=bind,
        progress=progress,
    )
    if masks_folder is None and not masks.files and not masks.issues:
        return None  # no masks folder and no label images: the run has no masks
    return masks


def _calibrated(
    metadata: MetadataReport, images: tuple[str, ...], output: Path
) -> tuple[MetadataReport, CalibrationReview]:
    """Resolve the pixel sizes with ``calibration.csv``; check them instead of the headers'.

    The pixel-size checks (contract §11) run on the sizes each image is recorded with, and
    the calibration's own issues join the metadata's.
    """
    calibration, sha256, refusal = load_calibration(output)
    readable = [f for f in metadata.files if f.readable]
    review = review_calibration(readable, images, calibration, sha256=sha256, refusal=refusal)
    kept = [issue for issue in metadata.issues if issue.code not in PIXEL_SIZE_CODES]
    checked = pixel_size_issues(readable, review.sizes)
    return replace(metadata, issues=(*kept, *checked, *review.issues)), review


def recalibrated(pre: Preflight, output_folder: str | os.PathLike) -> Preflight:
    """Return the preflight with the output folder's ``calibration.csv`` read again.

    The notebook calls it after the user states a size, so the sizes, their sources and the
    calibration and pixel-size issues follow the file without checking the folder again (the
    answers given so far are kept). Nothing else is read.

    Parameters
    ----------
    pre
        The preflight, as :func:`preflight` returned it (before :func:`fit_calibration`).
    output_folder
        The output folder holding ``calibration.csv``.

    Returns
    -------
    Preflight
        With the sizes resolved again, and the calibration and pixel-size issues replaced.
    """
    earlier = pre.calibration.issues
    kept = tuple(issue for issue in pre.metadata.issues if issue not in earlier)
    metadata, calibration = _calibrated(
        replace(pre.metadata, issues=kept), pre.discovery.images, Path(output_folder)
    )
    return replace(pre, metadata=metadata, calibration=calibration)


def fit_calibration(
    pre: Preflight, review: AxisReview
) -> tuple[Preflight, tuple[ValidationIssue, ...]]:
    """Record a Z or T size only for the images that have that role (design v0.4 §3).

    Parameters
    ----------
    pre
        The preflight, its sizes resolved from the headers and ``calibration.csv``.
    review
        The axis review of the roles the images are read with.

    Returns
    -------
    tuple
        The preflight with those sizes removed and ``calibration_axis_absent`` among its
        issues, and the new issues alone (:func:`.calibration.fit_to_roles`).
    """
    roles = {path: mapping.roles for path, mapping in review.file_mappings.items()}
    fitted, issues = fit_to_roles(pre.calibration, roles)
    metadata = replace(pre.metadata, issues=(*pre.metadata.issues, *issues))
    return replace(pre, metadata=metadata, calibration=fitted), issues


def _without_excluded_masks(
    discovery: DiscoveryResult, excluded: tuple[str, ...]
) -> DiscoveryResult:
    """Screen the mask names again, leaving out excluded images: excluding one clears it."""
    if not excluded:
        return discovery
    kept = [issue for issue in discovery.issues if issue.code not in MASK_NAME_CODES]
    names = mask_name_issues(discovery.leaves, excluded=excluded)
    return replace(discovery, issues=(*kept, *names))


def _exclusion_issues(discovery: DiscoveryResult, excluded: tuple[str, ...]) -> list:
    issues = []
    found = [path for path in excluded if path in discovery.images]
    unknown = [path for path in excluded if path not in discovery.images]
    if found:
        issues.append(
            warning(
                "excluded_images",
                f"{plural(len(found), 'image')} {'is' if len(found) == 1 else 'are'} excluded "
                "as requested and will not be analysed. Their rows from earlier runs are "
                "removed.",
                found,
                "Remove them from the exclusion list to analyse them again.",
            )
        )
    if unknown:
        issues.append(
            warning(
                "excluded_path_not_found",
                f"{plural(len(unknown), 'excluded path')} {'does' if len(unknown) == 1 else 'do'} "
                "not match any analysed image of the experiment.",
                unknown,
                "Check the spelling of the paths; they are relative to the experiment folder.",
            )
        )
    if discovery.images and not set(discovery.images) - set(excluded):
        issues.append(
            error(
                "no_images",
                "Every image of the experiment is excluded, so there is nothing to analyse.",
                ["."],
                "Remove images from the exclusion list.",
            )
        )
    return issues
