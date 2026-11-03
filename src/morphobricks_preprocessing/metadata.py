"""Image metadata review: inspect image headers without reading pixels.

Images are TIFF files or OME-Zarr folders (:mod:`.zarr_input`). For each
candidate image this records whether it opens, its shape, pixel type, axis
labels and series/page counts. Across the experiment it checks the rules
that must hold before any pixel is read (``docs/contract.md`` §3 and §8): one
supported pixel type, the same arrangement of dimensions and the same number
of channels in every file. Images that differ in width or height, in their
numbers of depth slices or time points, or in their physical pixel sizes are
allowed with a warning (contract §11).

Axis roles are not assigned here; the axis labels are the ones ``tifffile``
reports from the file's metadata, used to prefill the axis review.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable, Hashable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

import numpy as np
import tifffile
from morphobricks_normalization.dtypes import UnsupportedDtypeError, check_supported

from .axis_names import OPEN_AXIS, named_letters
from .discovery import DiscoveryResult
from .physical_sizes import PhysicalSizes, _tiff_sizes
from .pixel_size_checks import pixel_size_issues
from .size_checks import (
    breakdown,
    image_size_issues,
    lettered_plane_counts,
    plane_count_issues,
    role_plane_counts,
    role_size,
)
from .validation import ValidationIssue, error, has_errors, plural
from .zarr_input import (
    NgffError,
    ZarrUnavailableError,
    is_zarr_name,
    open_ngff,
    read_channel_names,
)

logger = logging.getLogger(__name__)

TIFF = "tiff"
OME_ZARR = "ome-zarr"


@dataclass(frozen=True)
class TiffMetadata:
    """Header information of one image: a TIFF file or an OME-Zarr folder.

    The name is kept from when only TIFF files were read; :data:`ImageMetadata`
    is the same class.

    Attributes
    ----------
    relative_path
        Path relative to the experiment root, with ``/`` separators.
    readable
        False if the image could not be opened; the other fields are then empty.
    shape
        Shape of the first image series (TIFF) or of the level-0 array of the
        first image (OME-Zarr).
    dtype
        Pixel type of the first series, in native byte order.
    axes
        One character per dimension, e.g. ``"ZCYX"``. ``tifffile`` uses ``Q`` or
        ``I`` for dimensions without a known role; OME-Zarr axes are translated
        by :func:`.zarr_input.ngff_axes`.
    series_count
        Number of image series in the file, or images in the OME-Zarr store.
    page_count
        Number of TIFF pages (IFDs) in the file; 0 for OME-Zarr.
    error
        Technical reason the file could not be opened, for the diagnostic log.
    channel_names
        Channel names from OME metadata, by channel index; None for a channel
        without a name. Empty when the file has no OME channel information.
        Leading and trailing whitespace is removed from every name; whitespace
        inside a name is kept and compared exactly.
    channel_names_trimmed
        True when leading or trailing whitespace was removed from a channel name
        (recorded in the run manifest).
    significant_bits
        Bits per pixel that carry data (e.g. 12 for a 12-bit camera in 16-bit
        files), from OME metadata; None when not given. For information only.
    kind
        ``"tiff"`` or ``"ome-zarr"``.
    physical_sizes
        Pixel sizes, z-step and time step with their units, from OME or ImageJ
        metadata (TIFF) or the OME-NGFF scale (OME-Zarr), as
        :func:`.physical_sizes.read_physical_sizes` gives them; all None when
        not given or the image does not open. For information only.
    rgb_samples
        True for a TIFF file stored as RGB colour: photometric interpretation
        RGB with three samples per pixel, which ``tifffile`` labels ``S``. It is
        the metadata the image type suggestion uses (:mod:`.image_type`). False
        for other TIFF files (also RGB with an alpha sample) and OME-Zarr images.
    axes_from_metadata
        True when the axes come from metadata that names them: OME, ImageJ or other
        microscope metadata ``tifffile`` reads, ``tifffile`` "shaped" metadata with axes
        (decision D-d), or OME-NGFF. False for a plain TIFF (no axis metadata), whose
        ``Y`` and ``X`` only say where each page's rows and columns are stored
        (:func:`series_axes_from_metadata`, :func:`named_axes`). Defaults to True, the
        strict reading, for a header built by hand.
    """

    relative_path: str
    readable: bool
    shape: tuple[int, ...] = ()
    dtype: np.dtype | None = None
    axes: str = ""
    series_count: int = 0
    page_count: int = 0
    error: str | None = None
    channel_names: tuple[str | None, ...] = ()
    significant_bits: int | None = None
    kind: str = TIFF
    physical_sizes: PhysicalSizes = PhysicalSizes()
    rgb_samples: bool = False
    channel_names_trimmed: bool = False
    axes_from_metadata: bool = True

    def length(self, axis: str) -> int:
        """Return the length of the dimension labelled ``axis``, 1 if there is none."""
        return self.shape[self.axes.index(axis)] if axis in self.axes else 1

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

    @property
    def is_ome_zarr(self) -> bool:
        """True for an OME-Zarr image, False for a TIFF file."""
        return self.kind == OME_ZARR


ImageMetadata = TiffMetadata
"""Header information of one image (TIFF or OME-Zarr)."""


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


def named_axes(meta: TiffMetadata) -> str:
    """Return the image's axes with every dimension its metadata does not name as ``?``.

    The one rule behind :func:`header_axes` and the axis check
    (:func:`.axis_check.header_axis_issue`)
    (MorphoBricks D86): a dimension is named when ``tifffile`` (or OME-NGFF) gives it a
    letter from the file's metadata. Left open (:data:`OPEN_AXIS`) are

    - placeholder axes (:data:`PLACEHOLDER_AXES`: ``I``, ``Q``), and
    - in a plain stack of pages (a placeholder axis and no axis metadata, e.g. ``QYX``,
      ``QQYX`` or ``IYX``), the ``Y`` and ``X`` too: they are only where each page's
      rows and columns are stored. A (Y, X, C) array written without metadata reads as
      ``QYX`` with its channels as ``X``.

    The ``Y`` and ``X`` of a single plain page (``YX``, RGB ``YXS``) are named (decision
    D-d), as is ``S``, which comes from the samples-per-pixel tag.

    Parameters
    ----------
    meta
        The image's header (:func:`inspect_image`).

    Returns
    -------
    str
        One character per dimension, e.g. ``"CYX"``, ``"?YX"`` (shaped axes ``QYX``) or
        ``"???"`` (a plain ``QYX`` stack); ``""`` for an unreadable image.
    """
    return named_letters(meta.axes, meta.axes_from_metadata) if meta.readable else ""


def header_axes(meta: TiffMetadata) -> str | None:
    """Return the axes an image's own metadata gives, or None when it does not name them all.

    These are the axes every MorphoBricks brick trusts (MorphoBricks D86, decision D-d):
    the series axes ``tifffile`` reads from OME, ImageJ and "shaped" metadata (the
    ``{"axes": ...}`` ``tifffile`` writes), a plain 2D ``YX`` image (or ``YXS`` RGB), and
    the OME-NGFF axes of an OME-Zarr image. When any dimension is left open
    (:func:`named_axes`: a placeholder axis, so a plain multi-page TIFF ``IYX`` or a shaped
    one without axes ``QYX``), the file does not say which dimension is depth, time or
    channel, so there are no header axes and nothing may be guessed.
    Length-1 axes are left out, as ``tifffile`` reads them.

    Parameters
    ----------
    meta
        The image's header (:func:`inspect_image`).

    Returns
    -------
    str or None
        For example ``"ZYX"``; None for an unreadable image or one with a placeholder axis.
    """
    named = named_axes(meta)
    if not named or OPEN_AXIS in named:
        return None
    return meta.axes


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
                **_ome_details(tif),
                physical_sizes=_tiff_sizes(tif),
                rgb_samples=_rgb_samples(first),
                axes_from_metadata=series_axes_from_metadata(first),
            )
    except Exception as exc:  # any failure to parse means the file cannot be used
        logger.debug("Cannot read the TIFF header of %s", path, exc_info=True)
        return TiffMetadata(
            relative_path=relative_path, readable=False, error=f"{type(exc).__name__}: {exc}"
        )


def inspect_ome_zarr(path: str | os.PathLike, relative_path: str | None = None) -> TiffMetadata:
    """Read the metadata of one OME-Zarr image without reading its pixels.

    Returns
    -------
    TiffMetadata
        With ``kind="ome-zarr"``; ``readable`` is False if the folder is not an
        OME-Zarr image that can be read (or ``zarr`` is not installed). No
        exception is raised.
    """
    path = Path(path)
    relative_path = path.name if relative_path is None else relative_path
    try:
        image = open_ngff(path)
        return TiffMetadata(
            relative_path=relative_path,
            readable=True,
            shape=image.shape,
            dtype=image.dtype,
            axes=image.axes,
            series_count=image.image_count,
            page_count=0,
            channel_names=image.channel_names,
            channel_names_trimmed=image.channel_names_trimmed,
            kind=OME_ZARR,
            physical_sizes=image.physical_sizes,
        )
    except (NgffError, ZarrUnavailableError) as exc:
        logger.debug("Cannot read the OME-Zarr image %s", path, exc_info=True)
        return TiffMetadata(
            relative_path=relative_path,
            readable=False,
            error=f"{type(exc).__name__}: {exc}",
            kind=OME_ZARR,
        )
    except Exception as exc:  # anything else in a damaged store means it cannot be used
        logger.debug("Cannot read the OME-Zarr image %s", path, exc_info=True)
        return TiffMetadata(
            relative_path=relative_path,
            readable=False,
            error=f"{type(exc).__name__}: {exc}",
            kind=OME_ZARR,
        )


def inspect_image(path: str | os.PathLike, relative_path: str | None = None) -> TiffMetadata:
    """Read the header of a TIFF file or the metadata of an OME-Zarr folder."""
    if is_zarr_name(Path(path).name):
        return inspect_ome_zarr(path, relative_path)
    return inspect_tiff(path, relative_path)


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
    files = tuple(inspect_image(root / rel, rel) for rel in sorted(relative_paths))
    return MetadataReport(files=files, issues=tuple(check_metadata(files)))


def inspect_experiment(discovery: DiscoveryResult) -> MetadataReport:
    """Inspect the headers of every image found by folder discovery."""
    return inspect_images(discovery.root, discovery.images)


def check_metadata(files: Iterable[TiffMetadata]) -> list[ValidationIssue]:
    """Check image headers against the experiment-wide rules.

    Reports, as errors: images that do not open (``unreadable_tiff``,
    ``unreadable_ome_zarr``, or ``zarr_not_installed`` for OME-Zarr images
    without the ``zarr`` package), files or stores with more than one image,
    unsupported pixel types, more than one pixel type, TIFF files whose
    dimensions are arranged differently, and images with different numbers of
    channels (``different_channel_counts``; the C axis, or the RGB samples
    without one, until the axis roles are confirmed), and channel names that differ at the
    same channel (``channel_names_differ``). OME-Zarr images declare their axes,
    so their arrangement is checked by role during the axis review instead.

    Reports, as warnings: images of different widths or heights
    (``different_sizes``), different numbers of depth slices, time points
    or planes along another dimension (``different_plane_counts``),
    physical pixel sizes that differ (``different_pixel_sizes``) or are
    known for only some images (``missing_pixel_size``), and pixel sizes given
    in a length unit that is not converted (``unsupported_pixel_size_unit``,
    decision D-a2): those images are uncalibrated, and the unit is named.
    Sizes and plane counts are compared by the axes the headers name; a plain stack is
    left out until its roles are confirmed (:func:`dimension_issues`, issue #74).

    Parameters
    ----------
    files
        Headers from :func:`inspect_image`.

    Returns
    -------
    list of ValidationIssue
        The issues found, errors first.
    """
    files = list(files)
    readable = [f for f in files if f.readable]
    issues: list[ValidationIssue] = []

    unreadable = [f.relative_path for f in files if not f.readable and not f.is_ome_zarr]
    no_zarr = [
        f.relative_path
        for f in files
        if not f.readable and f.is_ome_zarr and (f.error or "").startswith("ZarrUnavailableError")
    ]
    bad_zarr = [
        f.relative_path
        for f in files
        if not f.readable and f.is_ome_zarr and f.relative_path not in no_zarr
    ]
    if no_zarr:
        issues.append(
            error(
                "zarr_not_installed",
                f"{plural(len(no_zarr), 'OME-Zarr image')} cannot be read because the zarr "
                "package is not installed.",
                no_zarr,
                'Install it with pip install "morphobricks-preprocessing[zarr]", or exclude '
                "these images.",
            )
        )
    if bad_zarr:
        reasons = sorted(
            {(f.error or "").split(": ", 1)[-1] for f in files if f.relative_path in bad_zarr}
        )
        issues.append(
            error(
                "unreadable_ome_zarr",
                f"{plural(len(bad_zarr), 'folder')} named like OME-Zarr images could not be "
                "read: " + "; ".join(reasons) + ".",
                bad_zarr,
                "Export the images again as OME-Zarr (OME-NGFF 0.4 or 0.5), or exclude them.",
            )
        )
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
                f"{plural(len(multi_series), 'image')} contain more than one image, for "
                "example several positions or scenes in one TIFF file, or several series, "
                "wells or fields in one OME-Zarr folder. Such images are not supported "
                "yet. (Resolution levels of a pyramidal image are not separate images; "
                "only the full-resolution level is read.)",
                multi_series,
                "Export each position, scene or field as a separate image, or exclude these "
                "images.",
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

    issues.extend(_check_dimensions([f for f in readable if not f.is_ome_zarr]))
    issues.extend(_check_channel_names(readable))
    issues.extend(_check_channel_counts(readable))
    issues.extend(dimension_issues(readable))
    issues.extend(pixel_size_issues(readable))
    return issues


def _ome_details(tif: tifffile.TiffFile) -> dict:
    """Channel names and significant bits of the first OME image, if present."""
    if not tif.is_ome:
        return {}
    try:
        root = ElementTree.fromstring(tif.ome_metadata)
    except (ElementTree.ParseError, TypeError, ValueError):
        logger.debug("Cannot parse the OME metadata of %s", tif.filename, exc_info=True)
        return {}
    pixels = next((e for e in root.iter() if _local_name(e.tag) == "Pixels"), None)
    if pixels is None:
        return {}
    names, trimmed = read_channel_names(
        channel.get("Name") for channel in pixels if _local_name(channel.tag) == "Channel"
    )
    bits = pixels.get("SignificantBits")
    return {
        "channel_names": names,
        "channel_names_trimmed": trimmed,
        "significant_bits": int(bits) if bits and bits.isdigit() else None,
    }


def _rgb_samples(series: tifffile.TiffPageSeries) -> bool:
    """Return True if the series stores RGB colour: photometric RGB, three samples per pixel."""
    keyframe = series.keyframe
    return (
        "S" in series.axes
        and keyframe.photometric == tifffile.PHOTOMETRIC.RGB
        and keyframe.samplesperpixel == 3
    )


def series_axes_from_metadata(series: tifffile.TiffPageSeries) -> bool:
    """Return True when axis metadata names the axes of a ``tifffile`` series.

    False for a plain TIFF: a series ``tifffile`` reads as ``generic`` or ``uniform`` pages,
    or as "shaped" without axes (``{"shape": ...}`` only). True for OME, ImageJ, shaped
    metadata with ``"axes"`` (decision D-d) and the other microscope formats ``tifffile``
    reads axes from. :func:`inspect_tiff` records it for the first series as
    :attr:`TiffMetadata.axes_from_metadata`; a brick that builds a :class:`TiffMetadata`
    for another series passes it the same way.

    Parameters
    ----------
    series
        A series of an open ``tifffile.TiffFile``.

    Returns
    -------
    bool
        Whether the series' axes come from metadata rather than from where its pages are
        stored.
    """
    kind = str(series.kind).lower()
    if kind in ("generic", "uniform"):
        return False
    if kind == "shaped":
        try:
            shaped = json.loads(series.keyframe.shaped_description or "")
        except (AttributeError, TypeError, ValueError):
            return False
        return isinstance(shaped, dict) and "axes" in shaped
    return True


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _check_channel_names(files: list[TiffMetadata]) -> list[ValidationIssue]:
    named = [f for f in files if f.channel_names]
    if len(named) < 2:
        return []
    differing: list[str] = []
    descriptions: list[str] = []
    for index in range(max(len(f.channel_names) for f in named)):
        at_index = [f for f in named if index < len(f.channel_names) and f.channel_names[index]]
        names = {f.channel_names[index] for f in at_index}
        if len(names) > 1:
            # Names are compared exactly. When they differ only in whitespace, quote them
            # and say so: the difference would otherwise be invisible in the message.
            only_spaces = len({" ".join(name.split()) for name in names}) == 1
            summary, minority = _breakdown(
                at_index,
                lambda f, i=index: f.channel_names[i],
                (lambda name: f'"{name}"') if only_spaces else str,
            )
            note = " (the names differ only in spaces or other whitespace)" if only_spaces else ""
            descriptions.append(f"channel {index}: {summary}{note}")
            differing.extend(minority)
    if not differing:
        return []
    # An error (issue #37): channels are matched by number, so a different stain at the
    # same number would be pooled and scaled as one channel.
    return [
        error(
            "channel_names_differ",
            "The images name their channels differently at the same channel position ("
            + "; ".join(descriptions)
            + "). Channels are matched by their number, so different stains would be pooled "
            "as one channel.",
            sorted(set(differing)),
            "Check the channel order of these images. Export them again with the same channel "
            "order and names, or exclude the images whose channels differ.",
        )
    ]


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
                CONVERT_KEEPS_ORDER,
            )
        ]
    return []


CONVERT_KEEPS_ORDER = (
    "Export the images again with the same dimension order, or process the differently "
    "arranged images as a separate experiment. Named-axis copies ('convert') keep each "
    "image's stored order, so they do not solve this."
)
"""What to do about images arranged differently (cross-brick test F8, plan question 73)."""


def header_channel_count(f: TiffMetadata) -> int:
    """Return the channel count the header suggests: the C axis, else the RGB samples."""
    return f.length("C") if "C" in f.axes else f.length("S")


def channel_count_issues(counts: Mapping[str, int]) -> list[ValidationIssue]:
    """Report images with different numbers of channels (normalization plan N43).

    Each row of ``plane_histograms.csv`` holds every channel of a plane, so all
    images of an experiment must have the same number of channels.

    Parameters
    ----------
    counts
        Number of channels of each image, by relative path.

    Returns
    -------
    list of ValidationIssue
        The error ``different_channel_counts`` naming the images not having the
        most common number, or nothing.
    """
    groups: dict[int, list[str]] = {}
    for path, count in sorted(counts.items()):
        groups.setdefault(count, []).append(path)
    if len(groups) < 2:
        return []
    ordered = sorted(groups.items(), key=lambda item: (-len(item[1]), item[0]))
    summary = ", ".join(
        f"{plural(count, 'channel')} ({plural(len(paths), 'image')})" for count, paths in ordered
    )
    return [
        error(
            "different_channel_counts",
            f"The images have different numbers of channels: {summary}. Each row of the "
            "histogram table holds every channel of a plane, so every image of an experiment "
            "must have the same channels. The images listed are those without the most "
            "common number of channels.",
            sorted(path for _, paths in ordered[1:] for path in paths),
            "Split the images with other channels into a separate experiment, or exclude "
            "them. If a channel axis was assigned the wrong role, correct the axis roles.",
        )
    ]


def _check_channel_counts(files: list[TiffMetadata]) -> list[ValidationIssue]:
    return channel_count_issues({f.relative_path: header_channel_count(f) for f in files})


def dimension_issues(
    files: Iterable[TiffMetadata], roles: Mapping[str, Sequence[str | None]] | None = None
) -> list[ValidationIssue]:
    """Report images of different sizes or plane counts (contract §11).

    Each readable image is measured by its confirmed roles when ``roles`` has them, else by
    the letters its header names. A plain stack without confirmed roles is left out: the Y
    and X of its header are only where its rows and columns are stored (:func:`named_axes`),
    so its sizes are compared only once its roles are confirmed (issue #74).

    Parameters
    ----------
    files
        The image headers.
    roles
        The confirmed role of each axis, by relative path (:attr:`.axes.AxisReview.file_mappings`).

    Returns
    -------
    list of ValidationIssue
        The warnings ``different_sizes`` and ``different_plane_counts``, or nothing.
    """
    roles = roles or {}
    sizes: dict[str, tuple[int, int]] = {}
    planes: dict[str, dict[str, int]] = {}
    for f in (f for f in files if f.readable):
        path, given = f.relative_path, roles.get(f.relative_path)
        if given is not None and {"Y", "X"} <= set(given):
            sizes[path], planes[path] = role_size(f.shape, given), role_plane_counts(f.shape, given)
        elif given is None and {"Y", "X"} <= set(named_axes(f)):
            sizes[path] = f.length("Y"), f.length("X")
            planes[path] = lettered_plane_counts(f.axes, f.shape)
    return [*image_size_issues(sizes), *plane_count_issues(planes)]


def _breakdown(
    files: list[TiffMetadata],
    key: Callable[[TiffMetadata], Hashable],
    describe: Callable[[Hashable], str],
) -> tuple[str, list[str]]:
    """Summarise files by ``key``, most common first (:func:`.size_checks.breakdown`)."""
    return breakdown({f.relative_path: key(f) for f in files}, describe)


def _is_subsequence(short: str, long: str) -> bool:
    remaining = iter(long)
    return all(char in remaining for char in short)
