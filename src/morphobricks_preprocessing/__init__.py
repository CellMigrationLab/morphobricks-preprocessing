"""MorphoBricks preprocessing brick.

Builds one intensity histogram per channel of every plane and stores them, with
each plane's experimental labels, in a CSV table: one row per plane (an image at
one Z and T position), the channels in columns. Later bricks pool these histograms to get
group statistics and normalization parameters without rereading the images.
"""

from importlib.metadata import PackageNotFoundError, version

# The modules that moved to morphobricks_normalization stay importable as
# deprecated stand-ins for one version (see _moved).
from . import _moved, csv_io, dtypes, histogram, normalization, pooling, records  # noqa: F401
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
from .dataset import (
    CONSISTENCY_CODES,
    BalanceCell,
    BalanceTable,
    BiologicalRepeat,
    DatasetReport,
    balance_table,
    biological_repeats,
    check_dataset,
    default_repeat_groups,
)
from .design import (
    DESIGN_COLUMNS,
    DESIGN_FILENAME,
    DESIGN_SCHEMA_VERSION,
    DesignChanges,
    DesignFormatError,
    DesignRow,
    ExperimentDesign,
    default_design,
    design_issues,
    load_design,
    read_experiment_design,
    reconcile_design,
    write_experiment_design,
)
from .discovery import (
    DiscoveryResult,
    LeafFolder,
    check_output_folder,
    default_output_folder,
    discover_experiment,
    position_id,
)
from .image_type import (
    FLUORESCENCE,
    IMAGE_TYPES,
    RGB,
    ImageTypeReview,
    check_image_type,
    image_type_issues,
    review_image_type,
)
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
from .physical_sizes import PhysicalSizes, read_physical_sizes
from .planes import (
    DEFAULT_MEMORY_LIMIT,
    Block,
    BlockReader,
    Plane,
    PlaneLayout,
    PlaneMemoryError,
    PlaneReadError,
    XycPlane,
    check_planes,
    iter_planes,
    iter_xyc_planes,
    plane_layout,
    read_plane,
    read_sample_plane,
)
from .report import GroupSummary, group_summaries
from .run import Preflight, RunResult, SamplePlane, preflight, run_experiment, sample_planes
from .validation import Severity, ValidationIssue, has_errors
from .zarr_export import (
    ConversionEstimate,
    ConversionSettings,
    OmeZarrWriter,
    estimate_conversion,
    ome_zarr_relative_path,
)
from .zarr_input import (
    NgffError,
    NgffImage,
    ZarrUnavailableError,
    is_zarr_store,
    ngff_label_names,
    open_ngff,
    open_ngff_labels,
    zarr_available,
)


def __getattr__(name: str) -> object:
    # Called only for names not defined here: the moved ones warn and come from
    # the normalization brick, anything else is missing.
    module = _moved.TOP_LEVEL_NAMES.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return _moved.moved_attribute(__name__, module, name)


def __dir__() -> list[str]:
    return sorted({*globals(), *_moved.TOP_LEVEL_NAMES})


try:
    __version__ = version("morphobricks-preprocessing")
except PackageNotFoundError:  # running from a source checkout without installing
    __version__ = "0.0.0+unknown"

__all__ = [
    "CONSISTENCY_CODES",
    "DEFAULT_MEMORY_LIMIT",
    "DESIGN_COLUMNS",
    "DESIGN_FILENAME",
    "DESIGN_SCHEMA_VERSION",
    "FLUORESCENCE",
    "IMAGE_TYPES",
    "MANIFEST_FILENAME",
    "OUTPUT_FILENAME",
    "RGB",
    "SINGLE",
    "AxisMapping",
    "AxisReview",
    "BalanceCell",
    "BalanceTable",
    "BiologicalRepeat",
    "Block",
    "BlockReader",
    "ConversionEstimate",
    "ConversionSettings",
    "DatasetReport",
    "DesignChanges",
    "DesignFormatError",
    "DesignRow",
    "DiscoveryResult",
    "ExperimentDesign",
    "FileEntry",
    "GroupSummary",
    "ImageMetadata",
    "ImageTypeReview",
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
    "XycPlane",
    "ZarrUnavailableError",
    "axis_length_ranges",
    "balance_table",
    "biological_repeats",
    "check_dataset",
    "check_image_type",
    "check_mapping",
    "check_metadata",
    "check_output_folder",
    "check_planes",
    "compute_block_histogram",
    "compute_plane_histogram",
    "declared_mapping",
    "default_bin_count",
    "default_design",
    "default_output_folder",
    "default_repeat_groups",
    "design_issues",
    "discover_experiment",
    "estimate_conversion",
    "file_mapping_for",
    "group_records",
    "group_summaries",
    "has_errors",
    "image_type_issues",
    "inspect_experiment",
    "inspect_image",
    "inspect_images",
    "inspect_ome_zarr",
    "inspect_tiff",
    "is_zarr_store",
    "iter_planes",
    "iter_xyc_planes",
    "load_design",
    "make_plane_id",
    "min_max",
    "ngff_label_names",
    "ome_zarr_relative_path",
    "open_ngff",
    "open_ngff_labels",
    "percentile_range",
    "plane_layout",
    "pooled_statistics",
    "position_id",
    "prefill_experiment_mapping",
    "prefill_mapping",
    "preflight",
    "read_experiment_design",
    "read_manifest",
    "read_physical_sizes",
    "read_plane",
    "read_plane_histograms",
    "read_sample_plane",
    "reconcile_design",
    "reference_axes",
    "review_axes",
    "review_image_type",
    "robust_z_score",
    "run_experiment",
    "sample_planes",
    "write_experiment_design",
    "write_manifest",
    "write_plane_histograms",
    "z_score",
    "zarr_available",
]
