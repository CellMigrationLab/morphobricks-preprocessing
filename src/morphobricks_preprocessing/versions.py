"""The versions of the packages that determine a run's output (moved from :mod:`.manifest`).

:mod:`.manifest` re-exports :func:`package_versions`.
"""

from __future__ import annotations

import platform
from importlib.metadata import PackageNotFoundError, version

import morphobricks_normalization
import numpy as np
import tifffile


def package_versions() -> dict[str, str]:
    """Return the versions of the packages that determine the output.

    Returns
    -------
    dict
        ``{package: version}`` for this package, the normalization brick (it computes the
        histograms and writes the CSV), NumPy, tifffile and Python.
    """
    try:
        own = version("morphobricks-preprocessing")
    except PackageNotFoundError:  # running from a source checkout without installing
        own = "0.0.0+unknown"
    return {
        "morphobricks-preprocessing": own,
        "morphobricks-normalization": morphobricks_normalization.__version__,
        "numpy": np.__version__,
        "tifffile": tifffile.__version__,
        "python": platform.python_version(),
    }
