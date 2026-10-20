"""MorphoBricks preprocessing brick.

Builds one intensity histogram per 2D plane and stores them, with each plane's
experimental labels, in a CSV table. Later bricks pool these histograms to get
group statistics and normalization parameters without rereading the images.
"""

from importlib.metadata import PackageNotFoundError, version

from .axes import (
    AxisMapping,
    AxisReview,
    axis_length_ranges,
    check_mapping,
    declared_mapping,
    file_mapping_for,
    prefill_experiment_mapping,
    prefill_mapping,
    reference_axes,
    review_axes,
)
from .csv_io import OUTPUT_FILENAME, read_plane_histograms, write_plane_histograms
from .discovery import (
    DiscoveryResult,
    LeafFolder,
    check_output_folder,
    default_output_folder,
    discover_experiment,
)
from .dtypes import UnsupportedDtypeError, default_bin_count
from .histogram import PlaneHistogram, compute_plane_histogram
from .manifest import (
    MANIFEST_FILENAME,
    FileEntry,
    ManifestFormatError,
    RunManifest,
    RunSettings,
    read_manifest,
    write_manifest,
)
from .metadata import (
    ImageMetadata,
    MetadataReport,
    TiffMetadata,
    check_metadata,
    inspect_experiment,
    inspect_image,
    inspect_images,
    inspect_ome_zarr,
    inspect_tiff,
)
from .normalization import (
    NormalizationParameters,
    min_max,
    percentile_range,
    robust_z_score,
    z_score,
)
from .planes import (
    DEFAULT_MEMORY_LIMIT,
    Plane,
    PlaneLayout,
    PlaneMemoryError,
    PlaneReadError,
    check_planes,
    iter_planes,
    plane_layout,
    read_plane,
    read_sample_plane,
)
from .pooling import NoValidPixelsError, PooledHistogram, PooledStatistics, pooled_statistics
from .records import SINGLE, PlaneRecord, group_records, make_plane_id
from .report import GroupSummary, group_summaries
from .run import Preflight, RunResult, SamplePlane, preflight, run_experiment, sample_planes
from .validation import Severity, ValidationIssue, has_errors
from .zarr_export import (
    ConversionEstimate,
    ConversionSettings,
    OmeZarrWriter,
    PhysicalSizes,
    estimate_conversion,
    ome_zarr_relative_path,
    read_physical_sizes,
)
from .zarr_input import (
    NgffError,
    NgffImage,
    ZarrUnavailableError,
    is_zarr_store,
    open_ngff,
    zarr_available,
)

try:
    __version__ = version("morphobricks-preprocessing")
except PackageNotFoundError:  # running from a source checkout without installing
    __version__ = "0.0.0+unknown"

__all__ = [
    "DEFAULT_MEMORY_LIMIT",
    "MANIFEST_FILENAME",
    "OUTPUT_FILENAME",
    "SINGLE",
    "AxisMapping",
    "AxisReview",
    "ConversionEstimate",
    "ConversionSettings",
    "DiscoveryResult",
    "FileEntry",
    "GroupSummary",
    "ImageMetadata",
    "LeafFolder",
    "ManifestFormatError",
    "MetadataReport",
    "NgffError",
    "NgffImage",
    "NoValidPixelsError",
    "NormalizationParameters",
    "OmeZarrWriter",
    "PhysicalSizes",
    "Plane",
    "PlaneHistogram",
    "PlaneLayout",
    "PlaneMemoryError",
    "PlaneReadError",
    "PlaneRecord",
    "PooledHistogram",
    "PooledStatistics",
    "Preflight",
    "RunManifest",
    "RunResult",
    "RunSettings",
    "SamplePlane",
    "Severity",
    "TiffMetadata",
    "UnsupportedDtypeError",
    "ValidationIssue",
    "ZarrUnavailableError",
    "axis_length_ranges",
    "check_mapping",
    "check_metadata",
    "check_output_folder",
    "check_planes",
    "compute_plane_histogram",
    "declared_mapping",
    "default_bin_count",
    "default_output_folder",
    "discover_experiment",
    "estimate_conversion",
    "file_mapping_for",
    "group_records",
    "group_summaries",
    "has_errors",
    "inspect_experiment",
    "inspect_image",
    "inspect_images",
    "inspect_ome_zarr",
    "inspect_tiff",
    "is_zarr_store",
    "iter_planes",
    "make_plane_id",
    "min_max",
    "ome_zarr_relative_path",
    "open_ngff",
    "percentile_range",
    "plane_layout",
    "pooled_statistics",
    "prefill_experiment_mapping",
    "prefill_mapping",
    "preflight",
    "read_manifest",
    "read_physical_sizes",
    "read_plane",
    "read_plane_histograms",
    "read_sample_plane",
    "reference_axes",
    "review_axes",
    "robust_z_score",
    "run_experiment",
    "sample_planes",
    "write_manifest",
    "write_plane_histograms",
    "z_score",
    "zarr_available",
]
