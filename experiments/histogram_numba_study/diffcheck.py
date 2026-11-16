"""Differential check: every histogram field, bit for bit, original vs patched (MODE argv[1])."""

import struct
import sys

import numpy as np

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import morphobricks_normalization.histogram as H
from morphobricks_normalization.dtypes import is_integer
from morphobricks_normalization.ranges import HistogramSettings


def bits(x):
    if x is None:
        return None
    if isinstance(x, float):
        return ("f", struct.pack("<d", x))
    if isinstance(x, np.ndarray):
        return (x.dtype.str, x.tobytes())
    if isinstance(x, (np.floating,)):
        return ("f", struct.pack("<d", float(x)))
    return repr(x)


def fingerprint(h, rc):
    f = [
        h.dtype.str,
        h.bin_count,
        bits(h.binning_min),
        bits(h.binning_max),
        bits(h.counts),
        h.total_pixel_count,
        h.valid_pixel_count,
        bits(h.pixel_min),
        bits(h.pixel_max),
        (h.underflow.count, bits(h.underflow.mean), bits(h.underflow.std)),
        (h.overflow.count, bits(h.overflow.mean), bits(h.overflow.std)),
        bits(h.representatives),
        bits(h.max_representative_error),
        h.is_exact,
    ]
    if is_integer(h.dtype) and not h.is_empty:
        f.append(repr(h.integer_bin_limits()))
    f.append(
        repr(rc)
        if rc is None
        else repr(
            sorted((k, bits(v) if isinstance(v, float) else repr(v)) for k, v in vars(rc).items())
        )
        if hasattr(rc, "__dict__")
        else repr(rc)
    )
    return f


def cases():
    rng = np.random.default_rng(12345)
    shapes = [(1,), (7, 5), (64, 64), (300, 257)]
    for dt in [
        "uint8",
        "int8",
        "uint16",
        "int16",
        "uint32",
        "int32",
        "uint64",
        "int64",
        "float32",
        "float64",
    ]:
        d = np.dtype(dt)
        for shape in shapes:
            for kind in range(6):
                if d.kind in "iu":
                    info = np.iinfo(d)
                    lo, hi = [
                        (info.min, info.max),
                        (0, 10),
                        (5, 5),
                        (int(info.min), int(info.min) + 3),
                        (max(info.min, -1000), min(info.max, 70000)),
                        (info.max - 2, info.max),
                    ][kind]
                    a = rng.integers(lo, hi, size=shape, dtype=d, endpoint=True)
                else:
                    a = (rng.standard_normal(shape) * [1, 1e-310, 0, 1e300, 1e5, 1][kind]).astype(d)
                    if kind == 5:
                        a.flat[::7] = np.nan
                        a.flat[1::11] = np.inf
                        a.flat[2::13] = -np.inf
                    if kind == 4:
                        a.flat[0] = np.finfo(d).max
                        a.flat[-1] = np.finfo(d).min
                    if kind == 2 and a.size > 3:
                        a.flat[:3] = np.nan
                yield dt, shape, kind, a
        yield dt, (0,), 9, np.zeros(0, d)
    a = np.full((4, 4), np.nan, np.float32)
    yield "float32", a.shape, 10, a
    # Values on or next to bin edges: integer-valued floats over [0, n] and k * step grids.
    for dt in ("float32", "float64"):
        for n in (3, 256, 500, 10000):
            for lo, hi in ((0.0, float(n)), (-1.5, 7.25), (1e-3, 1.0), (-1e6, 3e6), (0.1, 0.7)):
                grid = lo + (hi - lo) * np.arange(n + 1) / n
                edges = np.concatenate(
                    [grid, np.nextafter(grid, -np.inf), np.nextafter(grid, np.inf)]
                )
                a = np.clip(edges, lo, hi).astype(dt)
                a = np.concatenate([a, np.array([np.nan, np.inf, -np.inf], dt)])
                yield dt, (a.size,), 100 + n, a


def run():
    out = []
    for dt, shape, kind, a in cases():
        flat = a.ravel()
        for bins in [kind - 100] if kind >= 100 else [None, 1, 3, 256, 500, 10000]:
            try:
                H.validate_bin_count(
                    np.dtype(dt), bins if bins is not None else H.default_bin_count(np.dtype(dt))
                )
            except Exception:
                continue
            for settings in [None, HistogramSettings(histogram_range="robust")]:
                for nblocks in [1, 3]:
                    parts = np.array_split(flat, nblocks)

                    def blocks(parts=parts):
                        return iter(parts)

                    h = H.compute_block_histogram(blocks, np.dtype(dt), bins, settings=settings)
                    h2, rc = H.histogram_with_range_check(
                        blocks, np.dtype(dt), bins, settings=settings
                    )
                    out.append(
                        (
                            (dt, shape, kind, bins, settings is not None, nblocks),
                            fingerprint(h, None),
                            fingerprint(h2, rc),
                        )
                    )
    return out


if __name__ == "__main__":
    reference = run()
    import numba_patch

    numba_patch.install(sys.argv[1])
    patched = run()
    bad = [r[0] for r, p in zip(reference, patched) if r != p]
    print(f"mode={sys.argv[1]} cases={len(reference)} differing={len(bad)}", bad[:5])
    sys.exit(1 if bad else 0)
