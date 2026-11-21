"""Image QC metrics of one channel of one plane, measured in the histogram pass (contract §19).

The metrics describe how a plane looks, not how bright it is: the share of the plane above
its Otsu threshold, the histogram's entropy, focus (normalized variance, Tenengrad,
Laplacian variance, local focus on a grid of tiles), the correlation of each pixel with its
neighbours 1, 4 and 16 pixels away, and how much the tile means vary. Every ratio metric is
divided by the plane's mean or its square, so it does not change when the intensities are
multiplied by a constant (an 8-bit, 16-bit or float copy of one image gives the same value);
the raw intensities stay in the histogram table. Nothing is normalised before measuring.

:class:`PlaneQc` takes the plane's blocks as the histogram reads them (no extra read) and
:meth:`PlaneQc.finish` adds the histogram metrics once the histogram is known. Every metric
is built from sums that combine across blocks (:mod:`.image_qc_moments`), so a plane read in
blocks gives the whole plane's values, except that neighbour pairs crossing a block edge are
left out of the neighbour metrics (gradients, Laplacian, correlations). A plane held whole is
one block. The metrics are flags for review, never a reason to exclude an image.
"""

from __future__ import annotations

from dataclasses import dataclass, fields

import numpy as np
from morphobricks_normalization.histogram import PlaneHistogram

from .image_qc_moments import CoMoments, Moments
from .image_qc_sums import BlockSums, Window, valid_window

QC_VERSION = "1"
"""Version of the metric definitions; a change recomputes every row (contract §19)."""
LAGS = (1, 4, 16)
"""Neighbour distances (pixels) of the ``correlation_<lag>`` metrics."""
TILE_GRID = 8
"""The tiles of the local focus and tile mean metrics: an 8 x 8 grid over the plane."""
ENTROPY_BINS = 64
"""Equal bins over the plane's own range for the entropy."""
OFFSETS = (
    *((0, lag) for lag in sorted({1, 2, *LAGS})),
    *((lag, 0) for lag in sorted({1, 2, *LAGS})),
    (1, 1),
    (1, -1),
)
"""Every neighbour offset ``(dy, dx)`` a pair sum is taken at (correlations, gradients and
the Laplacian)."""
MEASURED, NON_FINITE, NO_FINITE = "measured", "non_finite_pixels", "no_finite_pixels"
"""The values of ``qc_status``."""


def qc_settings() -> dict:
    """Return the fixed settings of the metrics, as the manifest records them."""
    return {
        "qc_version": QC_VERSION,
        "correlation_lags": list(LAGS),
        "tile_grid": [TILE_GRID, TILE_GRID],
        "entropy_bins": ENTROPY_BINS,
    }


@dataclass(frozen=True)
class ImageQc:
    """The QC metrics of one channel of one plane; None where a metric is undefined.

    Attributes
    ----------
    status
        ``measured``; ``non_finite_pixels`` (a float plane with NaN or infinite pixels: the
        pixel metrics are left blank, the histogram metrics use the finite pixels); or
        ``no_finite_pixels`` (every metric blank).
    """

    status: str
    otsu_threshold: float | None = None
    otsu_separation: float | None = None
    foreground_fraction: float | None = None
    entropy: float | None = None
    normalized_variance: float | None = None
    tenengrad: float | None = None
    laplacian_variance: float | None = None
    correlation_1: float | None = None
    correlation_4: float | None = None
    correlation_16: float | None = None
    local_focus_median: float | None = None
    local_focus_p10: float | None = None
    tile_mean_cv: float | None = None


METRICS = tuple(f.name for f in fields(ImageQc) if f.name != "status")
"""The metric columns, in table order."""


class PlaneQc:
    """Accumulates the QC sums of one plane, block by block.

    Parameters
    ----------
    shape
        (Y, X) shape of the whole plane.
    dtype
        Its pixel type.
    """

    def __init__(self, shape: tuple[int, int], dtype: np.dtype) -> None:
        self.shape = (int(shape[0]), int(shape[1]))
        self.floating = np.dtype(dtype).kind == "f"
        self.non_finite = False
        self.seen = 0
        self.pixels = Moments()
        self.laplacian = Moments()
        self.gradient = [0.0, 0, 0.0, 0]  # sum and count of x, then y, squared differences
        self.pairs = {(lag, axis): CoMoments() for lag in LAGS for axis in (0, 1)}
        self.tiles = [[Moments()] * TILE_GRID for _ in range(TILE_GRID)]
        self.edges = tuple(
            tuple((i * side) // TILE_GRID for i in range(TILE_GRID + 1)) for side in self.shape
        )

    def add(self, y: int, x: int, pixels: np.ndarray) -> None:
        """Add one block whose first pixel is at plane row ``y`` and column ``x``."""
        self.seen += pixels.size
        if self.non_finite or pixels.size == 0:
            return
        if self.floating and not np.isfinite(pixels).all():
            self.non_finite = True  # the pixel metrics stay blank (contract §19)
            return
        # A constant near the mean keeps the sums small (well conditioned); any value works.
        sample = pixels[:: max(1, pixels.shape[0] // 16), :: max(1, pixels.shape[1] // 16)]
        bands = self._tile_rows(y, pixels.shape[0])
        offset = round(float(sample.mean()))
        sums = BlockSums(pixels, offset, OFFSETS, [top for _, top in bands])
        self.pixels = self.pixels.merged(
            Moments.of_sums(pixels.size, sums.offset, sums.total, sums.total_sq)
        )
        for (lag, axis), found in self.pairs.items():
            dy, dx = (lag, 0) if axis else (0, lag)
            n, pair = _pair_sums(sums, dy, dx)
            self.pairs[lag, axis] = found.merged(CoMoments.of_sums(n, sums.offset, pair))
        for axis in (0, 1):
            n, (_, _, saa, sbb, sab) = _pair_sums(sums, *((2, 0) if axis else (0, 2)))
            self.gradient[2 * axis] += saa + sbb - 2.0 * sab
            self.gradient[2 * axis + 1] += n
        self.laplacian = self.laplacian.merged(_laplacian(sums))
        self._add_tiles([i for i, _ in bands], x, sums)

    def _tile_rows(self, y: int, height: int) -> list[tuple[int, int]]:
        """Return each tile row a block of ``height`` rows at ``y`` meets, with its top in it."""
        rows = self.edges[0]
        return [
            (i, max(rows[i], y) - y)
            for i in range(TILE_GRID)
            if max(rows[i], y) < min(rows[i + 1], y + height)
        ]

    def _add_tiles(self, tile_rows: list[int], x: int, sums: BlockSums) -> None:
        """Add the block's part of each tile of the grid, from the column sums of its bands."""
        width, cols = sums.shape[1], self.edges[1]
        for i, (top, bottom, column, column_sq) in zip(tile_rows, sums.bands, strict=True):
            for j in range(TILE_GRID):
                left, right = max(cols[j], x), min(cols[j + 1], x + width)
                if left >= right:
                    continue
                part = slice(left - x, right - x)
                found = Moments.of_sums(
                    (bottom - top) * (right - left),
                    sums.offset,
                    float(column[part].sum()),
                    float(column_sq[part].sum()),
                )
                self.tiles[i][j] = self.tiles[i][j].merged(found)

    def finish(self, histogram: PlaneHistogram) -> ImageQc:
        """Return the metrics, with those of the plane's ``histogram`` (its finite pixels).

        Parameters
        ----------
        histogram
            The channel plane's histogram from the same pass.

        Returns
        -------
        ImageQc

        Raises
        ------
        ValueError
            If the blocks added do not hold every pixel of the plane once.
        """
        if self.seen != self.shape[0] * self.shape[1]:
            raise ValueError(f"the QC pass saw {self.seen} pixels of a {self.shape} plane")
        if histogram.is_empty:
            return ImageQc(NO_FINITE)
        found = histogram_metrics(histogram)
        if self.non_finite:
            return ImageQc(NON_FINITE, **found)
        return ImageQc(MEASURED, **found, **self._pixel_metrics())

    def _pixel_metrics(self) -> dict[str, float | None]:
        """Return the metrics of the pixel pass."""
        mean = self.pixels.mean
        found: dict[str, float | None] = {
            f"correlation_{lag}": _mean_of(
                self.pairs[lag, 0].correlation, self.pairs[lag, 1].correlation
            )
            for lag in LAGS
        }
        if self.pixels.n == 0 or mean <= 0.0:
            return found  # the ratio metrics need a positive mean (contract §19)
        square = mean * mean
        sx, nx, sy, ny = self.gradient
        found["normalized_variance"] = self.pixels.variance / square
        if nx and ny:
            found["tenengrad"] = (sx / (4.0 * nx) + sy / (4.0 * ny)) / square
        if self.laplacian.n:
            found["laplacian_variance"] = self.laplacian.variance / square
        if min(self.shape) >= 2 * TILE_GRID:
            tiles = [tile for row in self.tiles for tile in row]
            focus = np.array([tile.variance for tile in tiles]) / square
            found["local_focus_median"] = float(np.quantile(focus, 0.5))
            found["local_focus_p10"] = float(np.quantile(focus, 0.1))
            means = np.array([tile.mean for tile in tiles])
            if means.mean() > 0.0:
                found["tile_mean_cv"] = float(means.std() / means.mean())
        return found


def _mean_of(a: float | None, b: float | None) -> float | None:
    """Return the mean of the two directions' values, or None when either is undefined."""
    return None if a is None or b is None else (a + b) / 2.0


def _pair_sums(sums: BlockSums, dy: int, dx: int) -> tuple[int, tuple[float, ...]]:
    """Return the number of pairs at ``(dy, dx)`` and the sums of a, b, a², b² and ab."""
    window = valid_window(sums.shape, dy, dx)
    if window.size == 0:
        return 0, (0.0,) * 5
    partner = window.shifted(dy, dx)
    return window.size, (
        sums.window_sum(window),
        sums.window_sum(partner),
        sums.window_sum(window, squared=True),
        sums.window_sum(partner, squared=True),
        sums.pair_sum(window, dy, dx),
    )


def _laplacian(sums: BlockSums) -> Moments:
    """Return the moments of the 4-neighbour Laplacian over the block's inner pixels.

    ``L = up + down + left + right - 4 centre``, expanded into window sums and pair sums
    (its coefficients add up to 0, so the block's offset cancels).
    """
    height, width = sums.shape
    centre = Window(1, height - 1, 1, width - 1)
    if centre.size == 0:
        return Moments()
    up, down = centre.shifted(-1, 0), centre.shifted(1, 0)
    left, right = centre.shifted(0, -1), centre.shifted(0, 1)
    side = [up, down, left, right]
    total = sum(sums.window_sum(w) for w in side) - 4.0 * sums.window_sum(centre)
    square = sum(sums.window_sum(w, squared=True) for w in side)
    square += 16.0 * sums.window_sum(centre, squared=True)
    neighbours = (
        (up, 2, 0),  # up x down
        (left, 0, 2),  # left x right
        (up, 1, -1),  # up x left
        (up, 1, 1),  # up x right
        (left, 1, 1),  # left x down
        (right, 1, -1),  # right x down
    )
    square += 2.0 * sum(sums.pair_sum(w, dy, dx) for w, dy, dx in neighbours)
    with_centre = ((up, 1, 0), (centre, 1, 0), (left, 0, 1), (centre, 0, 1))
    square -= 8.0 * sum(sums.pair_sum(w, dy, dx) for w, dy, dx in with_centre)
    return Moments.of_sums(centre.size, 0.0, total, square)


def histogram_metrics(histogram: PlaneHistogram) -> dict[str, float | None]:
    """Return the Otsu threshold, its separation, the foreground fraction and the entropy.

    From the bins of a histogram with finite pixels: each bin stands for its pixels at its
    representative value, and each tail of a robust-range histogram for its pixels at the
    tail's mean (exact for integer histograms with one bin per value, the default for 8- and
    16-bit images).

    Parameters
    ----------
    histogram
        A plane's histogram with at least one finite pixel.

    Returns
    -------
    dict
        ``otsu_threshold`` (the value of the highest bin of the lower class),
        ``otsu_separation`` (between-class variance over total variance, 0 to 1),
        ``foreground_fraction`` (pixels above the threshold over finite pixels; the three
        are None for a plane of one value) and ``entropy`` (bits, over
        :data:`ENTROPY_BINS` equal bins from the plane's minimum to its maximum).
    """
    values = np.concatenate(
        ([histogram.underflow.mean], histogram.representatives, [histogram.overflow.mean])
    )
    counts = np.concatenate(
        ([histogram.underflow.count], histogram.counts, [histogram.overflow.count])
    ).astype(np.float64)
    keep = counts > 0
    values, counts = values[keep], counts[keep]
    total = counts.sum()
    found = dict.fromkeys(("otsu_threshold", "otsu_separation", "foreground_fraction"))
    found["entropy"] = _entropy(values, counts, histogram)
    if len(values) < 2:
        return found
    centred = values - float(np.dot(counts, values)) / total
    weight = np.cumsum(counts)[:-1] / total
    moment = np.cumsum(counts * centred)[:-1] / total
    between = moment * moment / (weight * (1.0 - weight))
    best = int(np.argmax(between))
    variance = float(np.dot(counts, centred * centred)) / total
    found["otsu_threshold"] = float(values[best])
    found["otsu_separation"] = min(1.0, float(between[best]) / variance)
    found["foreground_fraction"] = float(1.0 - weight[best])
    return found


def _entropy(values: np.ndarray, counts: np.ndarray, histogram: PlaneHistogram) -> float:
    """Return the Shannon entropy (bits) of the counts in equal bins over the plane's range."""
    low, high = float(histogram.pixel_min), float(histogram.pixel_max)
    if high <= low:
        return 0.0
    index = np.floor((values - low) / (high - low) * ENTROPY_BINS).astype(np.int64)
    coarse = np.bincount(np.clip(index, 0, ENTROPY_BINS - 1), counts, ENTROPY_BINS)
    share = coarse[coarse > 0] / coarse.sum()
    return float(-np.sum(share * np.log2(share))) + 0.0
