"""Deprecated access to the code that moved to the normalization brick.

The modules ``dtypes``, ``histogram``, ``records``, ``csv_io``, ``pooling`` and
``normalization`` moved unchanged to the package ``morphobricks_normalization``
(normalization plan, decision N6). Their public names stay importable from
``morphobricks_preprocessing`` and from the old submodules for one version. The
first access through each old path emits a ``DeprecationWarning`` that names the
new location; the package's own code imports from ``morphobricks_normalization``
and never triggers it.
"""

from __future__ import annotations

import importlib
import warnings
from collections.abc import Callable

#: Public names of each moved module (normalization contract §1.1).
MOVED_NAMES: dict[str, tuple[str, ...]] = {
    "dtypes": (
        "MAX_BIN_COUNT",
        "DEFAULT_FLOAT_BIN_COUNT",
        "MIN_FLOAT_BIN_COUNT",
        "INTEGER_DTYPES",
        "FLOAT_DTYPES",
        "UnsupportedDtypeError",
        "check_supported",
        "is_integer",
        "bit_depth",
        "default_bin_count",
        "validate_bin_count",
    ),
    "histogram": (
        "PlaneHistogram",
        "compute_plane_histogram",
        "compute_block_histogram",
        "estimate_histogram_memory",
    ),
    "records": ("SINGLE", "LEVELS", "PlaneRecord", "make_plane_id", "group_records"),
    "csv_io": (
        "SCHEMA_VERSION",
        "OUTPUT_FILENAME",
        "METADATA_COLUMNS",
        "BIN_COLUMN_PREFIX",
        "CsvFormatError",
        "bin_column",
        "write_plane_histograms",
        "read_plane_histograms",
    ),
    "pooling": (
        "DEFAULT_PERCENTILES",
        "NoValidPixelsError",
        "PooledStatistics",
        "PooledHistogram",
        "pooled_statistics",
    ),
    "normalization": (
        "NORMAL_CONSISTENCY",
        "NormalizationParameters",
        "min_max",
        "percentile_range",
        "z_score",
        "robust_z_score",
    ),
}

#: The moved names the package itself exported, and the module each came from.
TOP_LEVEL_NAMES: dict[str, str] = {
    "OUTPUT_FILENAME": "csv_io",
    "read_plane_histograms": "csv_io",
    "write_plane_histograms": "csv_io",
    "UnsupportedDtypeError": "dtypes",
    "default_bin_count": "dtypes",
    "PlaneHistogram": "histogram",
    "compute_block_histogram": "histogram",
    "compute_plane_histogram": "histogram",
    "NormalizationParameters": "normalization",
    "min_max": "normalization",
    "percentile_range": "normalization",
    "robust_z_score": "normalization",
    "z_score": "normalization",
    "NoValidPixelsError": "pooling",
    "PooledHistogram": "pooling",
    "PooledStatistics": "pooling",
    "pooled_statistics": "pooling",
    "SINGLE": "records",
    "PlaneRecord": "records",
    "group_records": "records",
    "make_plane_id": "records",
}

# Old paths that have already warned in this session.
_warned: set[str] = set()


def moved_attribute(old_module: str, module: str, name: str) -> object:
    """Return a moved name, warning the first time it is read through its old path.

    Parameters
    ----------
    old_module
        The module it was read from, e.g. ``"morphobricks_preprocessing"``.
    module
        The moved module that defines it, e.g. ``"histogram"``.
    name
        The public name.

    Returns
    -------
    object
        The object from ``morphobricks_normalization.<module>``, so types and
        exceptions are the very same ones.
    """
    old_path = f"{old_module}.{name}"
    if old_path not in _warned:
        _warned.add(old_path)
        warnings.warn(
            f"{old_path} has moved to morphobricks_normalization.{module}.{name}; import it "
            "from there. The old path will be removed in the next version of "
            "morphobricks-preprocessing.",
            DeprecationWarning,
            # Point at the caller's import or attribute access, not at this helper.
            stacklevel=3,
        )
    return getattr(importlib.import_module(f"morphobricks_normalization.{module}"), name)


def module_attributes(module: str) -> tuple[Callable[[str], object], Callable[[], list[str]]]:
    """Build the ``__getattr__`` and ``__dir__`` of the stand-in for a moved module.

    Parameters
    ----------
    module
        The moved module, e.g. ``"histogram"``.

    Returns
    -------
    tuple
        ``(__getattr__, __dir__)`` for ``morphobricks_preprocessing.<module>``.
    """
    old_module = f"morphobricks_preprocessing.{module}"
    names = MOVED_NAMES[module]

    def __getattr__(name: str) -> object:
        if name in names:
            return moved_attribute(old_module, module, name)
        raise AttributeError(f"module {old_module!r} has no attribute {name!r}")

    def __dir__() -> list[str]:
        return sorted(names)

    return __getattr__, __dir__
