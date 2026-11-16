"""Study prototype (not for merge): Numba kernels for the histogram step, installed by monkeypatch.

The histogram arithmetic is owned by morphobricks-normalization (``histogram.py``,
``integer_representatives.py``); a production version belongs there. This module replaces,
at run time and only when :func:`install` is called, these pieces of
``morphobricks_normalization.histogram`` with versions that must give the same bits:

``pixels``  (per pixel, GIL released)
    * 8/16-bit per-value table: ``table += np.bincount(unsigned)`` -> ``_count_small``.
    * Float planes: ``floor(float_bin_position(finite))``, clip, ``bincount`` ->
      ``_float_counts`` (same IEEE operations in the same order, no fastmath, so no FMA
      contraction; non-finite values skipped as ``values[np.isfinite(values)]`` does).

``bins``  (per bin, O(bin_count) Python loops today)
    * ``_integer_bin_starts`` (exact Python ints) -> int64 kernel when ``n * width < 2**62``;
      otherwise the original exact Python code (both exact: no result can differ).
    * ``_counts_from_per_value`` (float64 bincount weights + rint) -> exact int64 sums; the
      same integers while every bin holds fewer than 2**53 pixels (always, for a plane).
    * ``PlaneHistogram.representatives`` / ``max_representative_error`` /
      ``_integer_bin_limits`` for integer planes below 2**51 (``FLOAT_EXACT_LIMIT``), where
      ``(first + last) / 2`` of Python ints and of int64 -> float64 are the same float.

``MODE`` = "pixels", "bins", "all" (Numba), or "numpy-bins" (the per-bin part vectorised in
NumPy, no Numba). Tail moments (exact rationals) and 32/64-bit integer binning are unchanged.
"""

from __future__ import annotations

import os

import numpy as np

MODE = os.environ.get("MB_PATCH_MODE", "all")
_SAFE = 2**62


def _kernels():
    from numba import njit

    @njit(nogil=True, cache=True)
    def count_small(values, table, flip):
        for i in range(values.size):
            table[values[i] ^ flip] += 1

    @njit(nogil=True, cache=True)
    def float_counts(values, counts, mode, a, b, n):
        # mode 0: (v * 0.5 - a) * b    (a = lo * 0.5, b = n / half_range)
        # mode 1: (v - a) / b * n      (a = lo, b = span)
        # mode 2: (v * 0.5 - a) / b * n (a = lo * 0.5, b = half_range)
        # mode 3: constant robust range: every inside pixel in bin 0
        last = counts.size - 1
        for i in range(values.size):
            v = np.float64(values[i])
            if not np.isfinite(v):
                continue
            if mode == 3:
                counts[0] += 1
                continue
            if mode == 0:
                p = (v * 0.5 - a) * b
            elif mode == 1:
                p = (v - a) / b * n
            else:
                p = (v * 0.5 - a) / b * n
            k = np.int64(np.floor(p))
            if k < 0:
                k = 0
            elif k > last:
                k = last
            counts[k] += 1

    @njit(nogil=True, cache=True)
    def bin_starts(width, n):
        out = np.empty(n + 1, np.int64)
        if width <= n:
            for i in range(n + 1):
                out[i] = i if i < width else width
        else:
            for i in range(n + 1):
                out[i] = -((-i * width) // n)
        return out

    @njit(nogil=True, cache=True)
    def sum_bins(per_value, starts, n):
        counts = np.zeros(n, np.int64)
        for b in range(n):
            s = 0
            for j in range(starts[b], starts[b + 1]):
                s += per_value[j]
            counts[b] = s
        return counts

    return count_small, float_counts, bin_starts, sum_bins


def _numpy_bin_starts(width, n):
    i = np.arange(n + 1, dtype=np.int64)
    if width <= n:
        return np.minimum(i, width)
    return -((-i * width) // n)


def _numpy_sum_bins(per_value, starts, n):
    cumulative = np.concatenate(([0], np.cumsum(per_value, dtype=np.int64)))
    return cumulative[starts[1:]] - cumulative[starts[:-1]]


def install(mode: str | None = None) -> None:
    """Patch the normalization histogram module in this process."""
    import morphobricks_normalization.histogram as H
    from morphobricks_normalization import integer_representatives as IR
    from morphobricks_normalization.bin_centres import _halving_is_exact
    from morphobricks_normalization.dtypes import is_integer

    mode = mode or MODE
    pixels = mode in ("pixels", "all")
    bins = mode in ("bins", "all", "numpy-bins")
    if mode == "numpy-bins":
        starts_kernel, sums_kernel = _numpy_bin_starts, _numpy_sum_bins
    else:
        count_small, float_counts, starts_kernel, sums_kernel = _kernels()

    original_starts = H._integer_bin_starts

    def starts_array(width, n):
        width, n = int(width), int(n)
        if n * max(width, 1) < _SAFE:
            return starts_kernel(width, n)
        return None

    if bins:

        def _integer_bin_starts(width, bin_count):
            found = starts_array(width, bin_count)
            return original_starts(width, bin_count) if found is None else found.tolist()

        def _counts_from_per_value(per_value, starts, n):
            if per_value.size and int(per_value.sum()) >= 2**53:  # never for one plane
                raise AssertionError("prototype: bin totals beyond float64-exact")
            return sums_kernel(
                np.ascontiguousarray(per_value, dtype=np.int64), np.asarray(starts, np.int64), n
            )

        H._integer_bin_starts = _integer_bin_starts
        H._counts_from_per_value = _counts_from_per_value

        def _limits_arrays(self):
            lo, hi = self.binning_min, self.binning_max
            if max(-int(self.pixel_min), int(self.pixel_max)) >= IR.FLOAT_EXACT_LIMIT:
                return None
            starts = starts_array(hi + 1 - lo, self.bin_count)
            return None if starts is None else (starts[:-1], starts[1:] - 1)

        original_reps = H.PlaneHistogram.representatives.func
        original_error = H.PlaneHistogram.max_representative_error.fget

        def representatives(self):
            if self.is_empty or not is_integer(self.dtype):
                return original_reps(self)
            found = _limits_arrays(self)
            if found is None:
                return original_reps(self)
            first, last = found
            lo = int(self.binning_min)
            return ((first + lo) + (last + lo)).astype(np.float64) / 2

        def max_representative_error(self):
            if self.is_empty or not is_integer(self.dtype):
                return original_error(self)
            found = _limits_arrays(self)
            if found is None:
                return original_error(self)
            first, last = found
            widest = int((last - first + 1).max())
            bins_error = (widest - 1) / 2
            if not self.has_tails:
                return bins_error
            errors = [bins_error]
            if not self.underflow.is_empty:
                mean = self.underflow.mean
                errors.append(max(mean - self.pixel_min, self.binning_min - mean))
            if not self.overflow.is_empty:
                mean = self.overflow.mean
                errors.append(max(mean - self.binning_max, self.pixel_max - mean))
            return float(max(errors))

        from functools import cached_property

        prop = cached_property(representatives)
        prop.__set_name__(H.PlaneHistogram, "representatives")
        H.PlaneHistogram.representatives = prop
        H.PlaneHistogram.max_representative_error = property(max_representative_error)

    if pixels:
        original_small = H._small_integer_histogram
        np_ = np

        class _NumbaBincount:
            """``np`` for ``_small_integer_histogram``: only ``bincount`` of the table changes."""

            def __getattr__(self, name):
                return getattr(np_, name)

            @staticmethod
            def bincount(x, weights=None, minlength=0):
                if (
                    weights is None
                    and x.dtype in (np_.uint8, np_.uint16)
                    and minlength
                    in (
                        256,
                        65536,
                    )
                ):
                    table = np_.zeros(minlength, np_.int64)
                    count_small(x, table, x.dtype.type(0))
                    return table
                return np_.bincount(x, weights=weights, minlength=minlength)

        # Re-bind ``np`` inside a copy of the function only: the code itself is unchanged.
        import types

        small = types.FunctionType(
            original_small.__code__,
            {**original_small.__globals__, "np": _NumbaBincount()},
            original_small.__name__,
            original_small.__defaults__,
            original_small.__closure__,
        )
        H._small_integer_histogram = small

        def _float_bins(blocks, dtype, n, lo, hi, valid, extrema, selection, under, over):
            counts = np.zeros(n, dtype=np.int64)
            if lo == hi and extrema is None:
                counts[0] = valid
                return counts
            if lo != hi:
                half_range = hi * 0.5 - lo * 0.5
                if _halving_is_exact(lo, hi, half_range, n):
                    mode, a, b = 0, lo * 0.5, n / half_range
                elif np.isfinite(hi - lo):
                    mode, a, b = 1, float(lo), float(hi - lo)
                else:
                    mode, a, b = 2, lo * 0.5, half_range
            else:
                mode, a, b = 3, 0.0, 1.0
            for values in H.checked_blocks(blocks(), dtype):
                if selection is None and extrema is None:
                    if values.size:
                        float_counts(values, counts, mode, float(a), float(b), float(n))
                    continue
                finite = values[np.isfinite(values)]
                if finite.size == 0:
                    continue
                if selection is not None:
                    selection.feed(H._ordered_keys(finite, dtype))
                finite = finite.astype(np.float64, copy=False)
                if extrema is not None:
                    finite = H._split_tails(finite, lo, hi, under, over)
                if finite.size:
                    float_counts(finite, counts, mode, float(a), float(b), float(n))
            return counts

        H._float_bins = _float_bins
