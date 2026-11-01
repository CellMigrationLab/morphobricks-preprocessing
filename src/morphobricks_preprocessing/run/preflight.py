"""Pass 1: discover the experiment and read its headers, without pixels (contract §1, §2).

:func:`preflight` walks the folder, inspects every header and suggests an axis mapping;
:func:`sample_planes` reads the preview planes. Moved from ``run.py`` (PP-3).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from ..axes import (
    AxisMapping,
    file_mapping_for,
    prefill_experiment_mapping,
)
from ..dataset import DatasetReport, check_dataset
from ..discovery import DiscoveryResult, default_output_folder, discover_experiment
from ..masks import MaskInventory, find_masks
from ..metadata import MetadataReport, inspect_images
from ..planes import (
    DEFAULT_MEMORY_LIMIT,
    Plane,
    PlaneReadError,
    read_sample_plane,
)
from ..validation import ValidationIssue, error, has_errors, plural, warning

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
    """

    discovery: DiscoveryResult
    metadata: MetadataReport
    excluded: tuple[str, ...] = ()
    exclusion_issues: tuple[ValidationIssue, ...] = ()
    suggested_mapping: AxisMapping | None = None
    dataset: DatasetReport = field(default_factory=DatasetReport)
    masks: MaskInventory | None = None

    @property
    def issues(self) -> tuple[ValidationIssue, ...]:
        """All issues found so far, errors first."""
        merged = [
            *self.discovery.issues,
            *self.exclusion_issues,
            *self.metadata.issues,
            *self.dataset.issues,
            *(self.masks.issues if self.masks is not None else ()),
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
    ome_zarr_labels: bool = False,
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
        and checked from their headers; the label images of OME-Zarr images are
        included.
    ome_zarr_labels
        Use the label images of OME-Zarr images as masks, also without a masks
        folder.

    Returns
    -------
    Preflight
    """
    root = Path(experiment_root).resolve()
    output = default_output_folder(root) if output_folder is None else Path(output_folder)
    discovery = discover_experiment(root, output)
    excluded = tuple(sorted({_normalise(path) for path in exclude}))
    images = [path for path in discovery.images if path not in excluded]
    metadata = inspect_images(root, images)
    masks = None
    if masks_folder is not None or ome_zarr_labels:
        masks = find_masks(masks_folder, discovery, metadata.files)
    return Preflight(
        discovery=discovery,
        metadata=metadata,
        excluded=excluded,
        exclusion_issues=tuple(_exclusion_issues(discovery, excluded)),
        suggested_mapping=prefill_experiment_mapping(metadata),
        dataset=check_dataset(discovery, excluded=excluded),
        masks=masks,
    )


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


def _normalise(path: str) -> str:
    path = str(path).replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    return path
