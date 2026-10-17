"""TIFF metadata review: inspect image headers without reading pixels.

For each candidate image this records whether it opens, its shape, pixel type,
axis labels and series/page counts. Across the experiment it checks the rules
that must hold before any pixel is read (``docs/contract.md`` §3 and §8): one
supported pixel type, and the same arrangement of dimensions in every file.
Different dimension lengths are allowed with a warning.

Axis roles are not assigned here; the axis labels are the ones ``tifffile``
reports from the file's metadata, used to prefill the axis review.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Hashable, Iterable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import tifffile

from .discovery import DiscoveryResult
from .dtypes import UnsupportedDtypeError, check_supported
from .validation import ValidationIssue, error, has_errors, plural, warning

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TiffMetadata:
    """Header information of one TIFF file.

    Attributes
    ----------
    relative_path
        Path relative to the experiment root, with ``/`` separators.
    readable
        False if the file could not be opened as a TIFF; the other fields are
        then empty.
    shape
        Shape of the first image series, as ``tifffile`` reports it.
    dtype
        Pixel type of the first series, in native byte order.
    axes
        One character per dimension of the first series, e.g. ``"ZCYX"``.
        ``tifffile`` uses ``Q`` or ``I`` for dimensions without a known role.
    series_count
        Number of image series in the file.
    page_count
        Number of TIFF pages (IFDs) in the file.
    error
        Technical reason the file could not be opened, for the diagnostic log.
    """

    relative_path: str
    readable: bool
    shape: tuple[int, ...] = ()
    dtype: np.dtype | None = None
    axes: str = ""
    series_count: int = 0
    page_count: int = 0
    error: str | None = None

    @property
    def ndim(self) -> int:
        """Number of dimensions of the first series."""
        return len(self.shape)

    @property
    def dtype_supported(self) -> bool:
        """True if the pixel type is one this brick can process."""
        if self.dtype is None:
            return False
        try:
            check_supported(self.dtype)
        except UnsupportedDtypeError:
            return False
        return True


@dataclass(frozen=True)
class MetadataReport:
    """Headers of every image in an experiment and the issues found in them.

    Attributes
    ----------
    files
        One entry per image, in sorted path order.
    issues
        Errors and warnings about the images.
    """

    files: tuple[TiffMetadata, ...]
    issues: tuple[ValidationIssue, ...]

    @property
    def ok(self) -> bool:
        """True if no issue blocks processing."""
        return not has_errors(self.issues)

    @property
    def dtype(self) -> np.dtype | None:
        """The experiment's pixel type, or None if files disagree or none opened."""
        dtypes = {f.dtype for f in self.files if f.readable}
        return dtypes.pop() if len(dtypes) == 1 else None


def inspect_tiff(path: str | os.PathLike, relative_path: str | None = None) -> TiffMetadata:
    """Read the header of one TIFF file without decoding its pixels.

    Parameters
    ----------
    path
        Location of the file.
    relative_path
        Path to record, relative to the experiment root; defaults to the file name.

    Returns
    -------
    TiffMetadata
        The header information; ``readable`` is False if the file does not open.
        No exception is raised for unreadable files.
    """
    path = Path(path)
    relative_path = path.name if relative_path is None else relative_path
    try:
        with tifffile.TiffFile(path) as tif:
            series = tif.series
            if not series:
                raise ValueError("the file contains no image data")
            first = series[0]
            return TiffMetadata(
                relative_path=relative_path,
                readable=True,
                shape=tuple(int(n) for n in first.shape),
                dtype=np.dtype(first.dtype).newbyteorder("="),
                axes=str(first.axes),
                series_count=len(series),
                page_count=len(tif.pages),
            )
    except Exception as exc:  # any failure to parse means the file cannot be used
        logger.debug("Cannot read the TIFF header of %s", path, exc_info=True)
        return TiffMetadata(
            relative_path=relative_path, readable=False, error=f"{type(exc).__name__}: {exc}"
        )


def inspect_images(
    experiment_root: str | os.PathLike, relative_paths: Iterable[str]
) -> MetadataReport:
    """Inspect the headers of the given images and check them together.

    Parameters
    ----------
    experiment_root
        The experiment folder.
    relative_paths
        Image paths relative to the root, with ``/`` separators.

    Returns
    -------
    MetadataReport
        The headers, in sorted path order, and the issues from
        :func:`check_metadata`.
    """
    root = Path(experiment_root)
    files = tuple(inspect_tiff(root / rel, rel) for rel in sorted(relative_paths))
    return MetadataReport(files=files, issues=tuple(check_metadata(files)))


def inspect_experiment(discovery: DiscoveryResult) -> MetadataReport:
    """Inspect the headers of every image found by folder discovery."""
    return inspect_images(discovery.root, discovery.images)


def check_metadata(files: Iterable[TiffMetadata]) -> list[ValidationIssue]:
    """Check image headers against the experiment-wide rules.

    Reports, as errors: files that do not open, files with more than one image
    series, unsupported pixel types, more than one pixel type, and files whose
    dimensions are arranged differently.
    Reports, as a warning: files with the same arrangement but different
    dimension lengths.

    Parameters
    ----------
    files
        Headers from :func:`inspect_tiff`.

    Returns
    -------
    list of ValidationIssue
        The issues found, errors first.
    """
    files = list(files)
    readable = [f for f in files if f.readable]
    issues: list[ValidationIssue] = []

    unreadable = [f.relative_path for f in files if not f.readable]
    if unreadable:
        issues.append(
            error(
                "unreadable_tiff",
                f"{plural(len(unreadable), 'image')} could not be opened as TIFF files. "
                "They may be damaged, incomplete or not really TIFF files.",
                unreadable,
                "Export the images again from the acquisition software, or remove them "
                "from the experiment folder.",
            )
        )

    multi_series = [f.relative_path for f in readable if f.series_count > 1]
    if multi_series:
        issues.append(
            error(
                "multiple_series",
                f"{plural(len(multi_series), 'image')} contain more than one image series, "
                "for example several positions or scenes in one file. Such files are not "
                "supported yet. (Resolution levels of a pyramidal image are not separate "
                "series; only the full-resolution level is read.)",
                multi_series,
                "Export each position or scene as a separate image file.",
            )
        )

    unsupported = [f for f in readable if not f.dtype_supported]
    if unsupported:
        summary, _ = _breakdown(unsupported, lambda f: f.dtype.name, str)
        issues.append(
            error(
                "unsupported_dtype",
                f"Some images use a pixel type that is not supported: {summary}. "
                "Supported types are 8-, 16-, 32- and 64-bit "
                "integers, float32 and float64.",
                [f.relative_path for f in unsupported],
                "Convert these images to a supported pixel type before processing.",
            )
        )

    if len({f.dtype for f in readable}) > 1:
        summary, minority = _breakdown(readable, lambda f: f.dtype.name, str)
        issues.append(
            error(
                "mixed_dtypes",
                f"The images use different pixel types: {summary}. One experiment must "
                "use a single pixel type so that its histograms can be combined. The "
                "files listed are those not of the most common type.",
                minority,
                "Process each pixel type as a separate experiment, or convert the images "
                "to one pixel type.",
            )
        )

    issues.extend(_check_dimensions(readable))
    return issues


def _check_dimensions(files: list[TiffMetadata]) -> list[ValidationIssue]:
    arrangements = {f.axes for f in files}
    if len(arrangements) > 1:
        summary, minority = _breakdown(files, lambda f: f.axes, str)
        longest = max(arrangements, key=len)
        if all(_is_subsequence(axes, longest) for axes in arrangements) and (
            sum(len(axes) == len(longest) for axes in arrangements) == 1
        ):
            # The common case: one channel or one z-slice, which tifffile drops.
            return [
                error(
                    "missing_axes",
                    f"Some images lack dimensions that the others have: {summary}. "
                    "Processing is blocked until it is confirmed how the missing "
                    "dimensions should be read.",
                    minority,
                    "If the missing dimensions simply have one plane in these files (for "
                    "example a single channel), confirm this during axis review. Otherwise "
                    "export the images again with the same dimensions.",
                )
            ]
        return [
            error(
                "inconsistent_dimensions",
                f"The images have their dimensions arranged differently: {summary}. One "
                "axis mapping cannot describe all of them.",
                minority,
                "Export the images again with the same dimension order, or process the "
                "differently arranged images as a separate experiment.",
            )
        ]

    if len({f.shape for f in files}) > 1:
        axes = files[0].axes
        summary, minority = _breakdown(files, lambda f: f.shape, _format_shape)
        return [
            warning(
                "different_sizes",
                f"The images have different dimension lengths ({axes}): {summary}. They "
                "can still be processed with one axis mapping.",
                minority,
                "No action needed if the sizes are expected, for example different numbers "
                "of time points or z-slices.",
            )
        ]
    return []


def _breakdown(
    files: list[TiffMetadata],
    key: Callable[[TiffMetadata], Hashable],
    describe: Callable[[Hashable], str],
) -> tuple[str, list[str]]:
    """Summarise files by ``key``, most common first.

    Returns the summary text, e.g. ``"ZCYX (10 images), ZYX (2 images)"``, and the
    paths of the files outside the most common group.
    """
    groups: dict[Hashable, list[str]] = {}
    for f in files:
        groups.setdefault(key(f), []).append(f.relative_path)
    ordered = sorted(groups.items(), key=lambda item: (-len(item[1]), describe(item[0])))
    summary = ", ".join(f"{describe(k)} ({plural(len(paths), 'image')})" for k, paths in ordered)
    minority = [path for _, paths in ordered[1:] for path in paths]
    return summary, minority


def _format_shape(shape: Hashable) -> str:
    return " x ".join(str(n) for n in shape)


def _is_subsequence(short: str, long: str) -> bool:
    remaining = iter(long)
    return all(char in remaining for char in short)
