"""MorphoBricks preprocessing brick.

Builds one intensity histogram per 2D plane and stores them, with each plane's
experimental labels, in a CSV table. Later bricks pool these histograms to get
group statistics and normalization parameters without rereading the images.
"""

from importlib.metadata import PackageNotFoundError, version

from .axes import (
    AxisMapping,
    AxisReview,
    check_mapping,
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
from .metadata import (
    MetadataReport,
    TiffMetadata,
    check_metadata,
    inspect_experiment,
    inspect_images,
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
from .validation import Severity, ValidationIssue, has_errors

try:
    __version__ = version("morphobricks-preprocessing")
except PackageNotFoundError:  # running from a source checkout without installing
    __version__ = "0.0.0+unknown"

__all__ = [
    "DEFAULT_MEMORY_LIMIT",
    "OUTPUT_FILENAME",
    "SINGLE",
    "AxisMapping",
    "AxisReview",
    "DiscoveryResult",
    "LeafFolder",
    "MetadataReport",
    "NoValidPixelsError",
    "NormalizationParameters",
    "Plane",
    "PlaneHistogram",
    "PlaneLayout",
    "PlaneMemoryError",
    "PlaneReadError",
    "PlaneRecord",
    "PooledHistogram",
    "PooledStatistics",
    "Severity",
    "TiffMetadata",
    "UnsupportedDtypeError",
    "ValidationIssue",
    "check_mapping",
    "check_metadata",
    "check_output_folder",
    "check_planes",
    "compute_plane_histogram",
    "default_bin_count",
    "default_output_folder",
    "discover_experiment",
    "group_records",
    "has_errors",
    "inspect_experiment",
    "inspect_images",
    "inspect_tiff",
    "iter_planes",
    "make_plane_id",
    "min_max",
    "percentile_range",
    "plane_layout",
    "pooled_statistics",
    "prefill_experiment_mapping",
    "prefill_mapping",
    "read_plane",
    "read_plane_histograms",
    "read_sample_plane",
    "reference_axes",
    "review_axes",
    "robust_z_score",
    "write_plane_histograms",
    "z_score",
]
