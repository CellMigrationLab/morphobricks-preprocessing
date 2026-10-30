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

import logging
import os
from collections.abc import Callable, Hashable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

import numpy as np
import tifffile
from morphobricks_normalization.dtypes import UnsupportedDtypeError, check_supported

from .discovery import DiscoveryResult
from .physical_sizes import PhysicalSizes, _tiff_sizes
from .validation import ValidationIssue, error, has_errors, plural, warning
from .zarr_input import NgffError, ZarrUnavailableError, is_zarr_name, open_ngff

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
    and physical pixel sizes that differ (``different_pixel_sizes``) or are
    known for only some images (``missing_pixel_size``).

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
    issues.extend(_check_image_sizes(readable))
    issues.extend(_check_channel_names(readable))
    issues.extend(_check_channel_counts(readable))
    issues.extend(_check_plane_counts(readable))
    issues.extend(_check_pixel_sizes(readable))
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
    names = tuple(
        channel.get("Name") or None for channel in pixels if _local_name(channel.tag) == "Channel"
    )
    bits = pixels.get("SignificantBits")
    return {
        "channel_names": names if any(names) else (),
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
                "Export the images again with the same dimension order, or process the "
                "differently arranged images as a separate experiment.",
            )
        ]
    return []


def _check_image_sizes(files: list[TiffMetadata]) -> list[ValidationIssue]:
    # Width and height only: the other dimensions are compared by _check_plane_counts.
    sized = [f for f in files if "X" in f.axes and "Y" in f.axes]
    if len({(f.length("Y"), f.length("X")) for f in sized}) < 2:
        return []
    summary, minority = _breakdown(sized, lambda f: (f.length("Y"), f.length("X")), _format_shape)
    return [
        warning(
            "different_sizes",
            "The images have different widths or heights (height x width in pixels): "
            f"{summary}. They can still be processed together.",
            minority,
            "No action needed if the sizes are expected, for example images cropped "
            "differently. Otherwise check that these images were acquired with the same "
            "camera settings.",
        )
    ]


# What the planes along each dimension are called in messages.
_PLANE_NAMES = {"Z": "depth slices (Z)", "T": "time points"}
# Dimensions that do not hold planes: the image itself, and channels (or RGB samples),
# which are compared by _check_channel_counts.
_NOT_PLANES = frozenset("XYSC")


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


def _plane_counts(f: TiffMetadata) -> dict[str, int]:
    counts = {"Z": 1, "T": 1}
    counts.update({axis: f.length(axis) for axis in f.axes if axis not in _NOT_PLANES})
    return counts


def _check_plane_counts(files: list[TiffMetadata]) -> list[ValidationIssue]:
    counts = {f.relative_path: _plane_counts(f) for f in files}
    axes = sorted({axis for c in counts.values() for axis in c}, key=lambda a: ("ZT".find(a), a))
    descriptions: list[str] = []
    differing: list[str] = []
    for axis in axes:
        if len({c.get(axis, 1) for c in counts.values()}) < 2:
            continue
        summary, minority = _breakdown(
            files, lambda f, a=axis: counts[f.relative_path].get(a, 1), str
        )
        descriptions.append(f"{_PLANE_NAMES.get(axis, f'planes along {axis}')}: {summary}")
        differing.extend(minority)
    if not differing:
        return []
    return [
        warning(
            "different_plane_counts",
            "The images have different numbers of planes ("
            + "; ".join(descriptions)
            + "). Every plane is still analysed, but images with more planes weigh more "
            "in pooled statistics.",
            sorted(set(differing)),
            "No action needed if this is expected, for example time series of different "
            "lengths. Otherwise check that these images were acquired with the same "
            "depth range and time points.",
        )
    ]


def _check_pixel_sizes(files: list[TiffMetadata]) -> list[ValidationIssue]:
    # The z-step only matters for images with more than one depth slice.
    checks = (
        ("pixel size (x by y)", files, ("X", "Y")),
        ("z-step", [f for f in files if f.length("Z") > 1], ("Z",)),
    )
    descriptions: list[str] = []
    differing: list[str] = []
    missing: dict[str, list[str]] = {}
    for name, members, roles in checks:
        sizes = {f.relative_path: _micrometers(f, roles) for f in members}
        known = [f for f in members if sizes[f.relative_path] is not None]
        unknown = [f.relative_path for f in members if sizes[f.relative_path] is None]
        if known and unknown:
            missing[name] = unknown
        if len({sizes[f.relative_path] for f in known}) > 1:
            summary, minority = _breakdown(
                known, lambda f, s=sizes: s[f.relative_path], _format_micrometers
            )
            descriptions.append(f"{name}: {summary}")
            differing.extend(minority)
    issues: list[ValidationIssue] = []
    if differing:
        issues.append(
            warning(
                "different_pixel_sizes",
                "The physical pixel sizes in the image metadata differ ("
                + "; ".join(descriptions)
                + "). Objects then look larger or smaller in some images than in others.",
                sorted(set(differing)),
                "No action needed if the images were acquired with different objectives on "
                "purpose. Otherwise check the microscope settings or the metadata of these "
                "images.",
            )
        )
    if missing:
        issues.append(
            warning(
                "missing_pixel_size",
                "The image metadata give the "
                + " and the ".join(missing)
                + " for some images but not for others, so it cannot be checked that they "
                "match.",
                sorted({path for paths in missing.values() for path in paths}),
                "No action needed if the sizes are known to be the same. Otherwise export "
                "these images again with their metadata.",
            )
        )
    return issues


def _micrometers(f: TiffMetadata, roles: tuple[str, ...]) -> tuple[float, ...] | None:
    values = [f.physical_sizes.micrometers(role) for role in roles]
    if any(v is None for v in values):
        return None
    # Metadata store sizes with rounding noise (0.1625 and 0.16250001); four
    # significant digits still tell different objectives apart.
    return tuple(float(f"{v:.4g}") for v in values)


def _format_micrometers(sizes: Hashable) -> str:
    return " x ".join(f"{v:g}" for v in sizes) + " micrometres"


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
