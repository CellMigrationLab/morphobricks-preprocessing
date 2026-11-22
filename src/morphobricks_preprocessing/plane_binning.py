"""What measuring a plane costs in working memory beyond its pixels (audit I2, contract §19).

The histogram's temporary arrays, the finished histograms of the plane's channels and the
image QC sums (:func:`.image_qc.qc_memory`) of the same pass. The layouts of :mod:`.planes` add
it to the memory estimate of a whole plane and of a block, so a plane is read in blocks
exactly when the whole measurement would not fit the memory limit.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from morphobricks_normalization.dtypes import default_bin_count
from morphobricks_normalization.histogram import estimate_histogram_memory
from morphobricks_normalization.ranges import HistogramSettings

from .image_qc import qc_memory


@dataclass(frozen=True)
class Binning:
    """What a plane's histogram and QC metrics cost in memory (nothing when only read).

    Attributes
    ----------
    bin_count
        Histogram bins; the pixel type's default if None.
    settings
        The histogram range of the run.
    histogram
        False when the plane is only read (a mask, a preview): no histogram, no QC.
    """

    bin_count: int | None = None
    settings: HistogramSettings | None = None
    histogram: bool = True

    def memory(self, rows: int, cols: int, dtype: np.dtype, transposed: bool = False) -> int:
        """Temporary memory of measuring ``rows`` x ``cols`` pixels (none when only read).

        Parameters
        ----------
        rows, cols
            The part measured, as stored; the QC sums work on it as (Y, X).
        dtype
            Its pixel type.
        transposed
            True when the stored rows are the plane's X (the part is turned before it is
            measured, so the QC band runs along the stored columns).
        """
        if not self.histogram:
            return 0
        qc = qc_memory(*((cols, rows) if transposed else (rows, cols)), dtype.kind == "f")
        histogram = estimate_histogram_memory(
            rows * cols, dtype, self.bin_count, settings=self.settings, range_check=True
        )
        return histogram + qc

    def finished(self, dtype: np.dtype, channels: int) -> int:
        """Memory of the finished histograms of an XYC plane's channels.

        The bin counts of every channel, kept until the plane's row is made.
        """
        if not self.histogram:
            return 0
        return (
            channels * 8 * (default_bin_count(dtype) if self.bin_count is None else self.bin_count)
        )
