"""Measure preprocessing's lazy OME-Zarr routes on real stores (issue #529); measure only.

Each subcommand runs in a fresh process (``python -I lazy529.py CMD JSON``) and writes JSON:

- ``windows``: a WindowReader (cache sized by ``cache_plan``, or none) and ``map_plan`` /
  ``map_windows`` over a grid of tiles grown by a margin and clipped at the plane edge; each
  window's SHA-256 is recorded.
- ``xyc``: ``read_xyc_plane`` of chosen (t, z) planes; each channel's SHA-256.
- ``planes``: ``iter_xyc_planes`` with a memory limit (blocks when the plane is over it):
  every block's rectangle and SHA-256 and every channel's ``Plane.histogram``.
- ``verify``: reads the level-0 array eagerly with zarr itself (one ``array[...]``) and checks
  every recorded digest and histogram against it.

Measured around each lazy read: wall time, peak RSS above the RSS before the read (Linux:
``malloc_trim`` then ``/proc/self/clear_refs`` = 5, then ``VmHWM``), and the store's reads
(calls and bytes of zarr's LocalStore ``get``, counted by a wrapper that returns zarr's value
unchanged).
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import sys
import time

import numpy as np

_LIBC = ctypes.CDLL("libc.so.6")
FETCH = {"calls": 0, "bytes": 0, "ranged": 0}


def _status(key):
    with open("/proc/self/status", encoding="ascii") as f:
        for line in f:
            if line.startswith(key + ":"):
                return int(line.split()[1]) * 1024
    raise KeyError(key)


def reset_peak():
    _LIBC.malloc_trim(0)
    with open("/proc/self/clear_refs", "w", encoding="ascii") as f:
        f.write("5")
    return _status("VmRSS")


def peak_since(base):
    return _status("VmHWM") - base


def count_fetches():
    import zarr.storage

    store = zarr.storage.LocalStore
    original = store.get

    async def get(self, key, prototype=None, byte_range=None):
        value = await original(self, key, prototype, byte_range)
        FETCH["calls"] += 1
        FETCH["ranged"] += byte_range is not None
        FETCH["bytes"] += 0 if value is None else len(value)
        return value

    store.get = get


TRACE = {"on": False}


def trace_start():
    import tracemalloc

    if TRACE["on"]:
        tracemalloc.start()
        TRACE["start"] = tracemalloc.get_traced_memory()[0]


def trace_stop():
    import tracemalloc

    if not TRACE["on"]:
        return None
    peak = tracemalloc.get_traced_memory()[1] - TRACE["start"]
    tracemalloc.stop()
    return peak


def fetches_since(before):
    return {k: FETCH[k] - before[k] for k in FETCH}


def sha(a):
    return hashlib.sha256(np.ascontiguousarray(a).tobytes()).hexdigest()


def hist_record(h):
    return {
        "bin_count": int(h.bin_count),
        "binning": [
            None if h.binning_min is None else float(h.binning_min),
            None if h.binning_max is None else float(h.binning_max),
        ],
        "counts_sha": sha(np.asarray(h.counts, dtype=np.int64)),
        "total": int(h.total_pixel_count),
        "valid": int(h.valid_pixel_count),
        "min": None if h.pixel_min is None else float(h.pixel_min),
        "max": None if h.pixel_max is None else float(h.pixel_max),
    }


def grid(plane, tile, margin):
    """Tiles of ``tile`` px row by row (partial tiles kept), grown by ``margin``, clipped."""
    rows, cols = plane
    out = []
    for y in range(0, rows, tile):
        for x in range(0, cols, tile):
            y0, y1, x0, x1 = y, min(y + tile, rows), x, min(x + tile, cols)
            out.append(
                (
                    max(0, y0 - margin),
                    min(rows, y1 + margin),
                    max(0, x0 - margin),
                    min(cols, x1 + margin),
                )
            )
    return out


def mapping_of(axes):
    from morphobricks_preprocessing.axes import AxisMapping

    return AxisMapping(axes, tuple(axes))  # stated from the store's NGFF axes, not guessed


def cmd_windows(a):
    from morphobricks_preprocessing.window_map import map_plan, map_windows
    from morphobricks_preprocessing.window_reader import WindowReader, cache_plan
    from morphobricks_preprocessing.zarr_input import open_ngff

    count_fetches()
    mapping = mapping_of(a["axes"])
    image = open_ngff(a["path"])
    y, x = a["axes"].index("Y"), a["axes"].index("X")
    plane = (image.shape[y], image.shape[x])
    windows = grid(plane, a["tile"], a["margin"])
    if a.get("limit_windows"):
        windows = windows[: a["limit_windows"]]
    channels = a["channels"]
    cp = cache_plan(
        a["path"],
        mapping,
        window=(a["tile"], a["tile"]),
        margin=(a["margin"], a["margin"]),
        channels=channels,
    )
    cache = cp.cache_bytes if a["cache"] == "plan" else int(a["cache"])
    cache = max(1, cache)
    limit = a.get("memory_limit", 2**30) + cache
    reader = WindowReader(a["path"], mapping, cache_bytes=cache, memory_limit=limit)
    plan = map_plan(
        reader, windows, channels=channels, workers=a["workers"], t=a.get("t"), z=a.get("z")
    )
    digests = []

    def fn(bounds, window):
        return [bounds, sha(window), list(window.shape)]

    if a.get("warmup"):
        with WindowReader(a["path"], mapping, cache_bytes=cache, memory_limit=limit) as warm:
            wplan = map_plan(
                warm,
                windows[-8:],
                channels=channels,
                workers=a["workers"],
                t=a.get("t"),
                z=a.get("z"),
            )
            with map_windows(wplan, fn, memory_budget=wplan.memory_estimate) as results:
                list(results)
    trace_start()
    before = dict(FETCH)
    base = reset_peak()
    t0 = time.perf_counter()
    with map_windows(plan, fn, memory_budget=plan.memory_estimate) as results:
        for r in results:
            digests.append(r)
    wall = time.perf_counter() - t0
    peak = peak_since(base)
    traced = trace_stop()
    st = reader.stats()
    reader.close()
    return {
        "cmd": "windows",
        "plane": plane,
        "windows": len(windows),
        "workers": a["workers"],
        "max_in_flight": plan.max_in_flight,
        "cache_plan": cp.__dict__ if hasattr(cp, "__dict__") else str(cp),
        "cache_bytes": cache,
        "memory_estimate": plan.memory_estimate,
        "window_bytes": plan.window_bytes,
        "held_bytes": plan.held_bytes,
        "peak_rss": peak,
        "ratio": peak / plan.memory_estimate,
        "wall_s": wall,
        "traced_peak": traced,
        "warmup": bool(a.get("warmup")),
        "fetch": fetches_since(before),
        "cache_stats": {
            k: getattr(st, k) for k in ("decodes", "hits", "evictions", "nbytes") if hasattr(st, k)
        },
        "all_sha": hashlib.sha256(json.dumps([d[1] for d in digests]).encode()).hexdigest(),
        "digests": digests,
        "t": a.get("t"),
        "z": a.get("z"),
        "channels": channels,
    }


def cmd_xyc(a):
    from morphobricks_preprocessing.plane_channels import read_xyc_plane, xyc_plane_memory

    count_fetches()
    mapping = mapping_of(a["axes"])
    out = []
    for t, z in a["planes"]:
        lim = a.get("memory_limit", 2**30)
        est = xyc_plane_memory(a["path"], mapping, z=z, t=t, memory_limit=lim)
        before = dict(FETCH)
        base = reset_peak()
        t0 = time.perf_counter()
        arr = read_xyc_plane(a["path"], mapping, z=z, t=t, memory_limit=lim)
        wall = time.perf_counter() - t0
        peak = peak_since(base)
        out.append(
            {
                "t": t,
                "z": z,
                "shape": list(arr.shape),
                "estimate": est,
                "peak_rss": peak,
                "ratio": peak / est,
                "wall_s": wall,
                "fetch": fetches_since(before),
                "sha": [sha(arr[c]) for c in range(arr.shape[0])],
            }
        )
        del arr
    return {"cmd": "xyc", "reads": out}


def cmd_planes(a):
    from morphobricks_preprocessing.planes import iter_xyc_planes, plane_layout

    count_fetches()
    mapping = mapping_of(a["axes"])
    limit = a["memory_limit"]
    layout = plane_layout(a["path"], mapping, memory_limit=limit)
    wanted = {tuple(p) for p in a["planes"]} if a.get("planes") else None
    out = []
    if a.get("warmup"):
        from morphobricks_preprocessing.windows import read_window

        read_window(a["path"], mapping, bounds=(0, 1, 0, 1), channels=[0])
    t_all = time.perf_counter()
    for xyc in iter_xyc_planes(a["path"], mapping, memory_limit=limit):
        if wanted is not None and (xyc.t, xyc.z) not in wanted:
            continue
        for c in range(xyc.channel_count):
            trace_start()
            before = dict(FETCH)
            base = reset_peak()
            t0 = time.perf_counter()
            plane = xyc.channel(c)
            blocks = []
            if a.get("blocks", True):
                for b in plane.blocks():
                    h, w = b.pixels.shape
                    blocks.append([b.y, b.x, h, w, sha(b.pixels)])
            t_blocks = time.perf_counter() - t0
            hist = plane.histogram()
            wall = time.perf_counter() - t0
            peak = peak_since(base)
            traced = trace_stop()
            out.append(
                {
                    "t": xyc.t,
                    "z": xyc.z,
                    "c": c,
                    "in_blocks": plane.in_blocks,
                    "traced_peak": traced,
                    "blocks": blocks,
                    "hist": hist_record(hist),
                    "peak_rss": peak,
                    "wall_s": wall,
                    "blocks_s": t_blocks,
                    "fetch": fetches_since(before),
                }
            )
            del plane
    est = layout.block_memory_estimate if layout.in_blocks else layout.memory_estimate
    for r in out:
        r["ratio"] = r["peak_rss"] / est
    return {
        "cmd": "planes",
        "memory_limit": limit,
        "in_blocks": layout.in_blocks,
        "block_shape": layout.block_shape,
        "estimate": est,
        "layout_memory_estimate": layout.memory_estimate,
        "block_memory_estimate": layout.block_memory_estimate,
        "total_s": time.perf_counter() - t_all,
        "reads": out,
    }


def cmd_verify(a):
    """Check every lazy record against one eager zarr read of the whole level-0 array."""
    import zarr
    from morphobricks_normalization.histogram import compute_plane_histogram

    from morphobricks_preprocessing.zarr_input import open_ngff

    image = open_ngff(a["path"])
    t0 = time.perf_counter()
    full = np.asarray(zarr.open_array(f"{a['path']}/{image.dataset_path}", mode="r")[...])
    full = full.astype(full.dtype.newbyteorder("="), copy=False)
    eager_s = time.perf_counter() - t0
    axes = a["axes"]

    def plane_of(t=None, z=None, c=None):
        idx = []
        for ax in axes:
            idx.append({"T": t, "Z": z, "C": c}.get(ax) if ax in "TZC" else slice(None))
        p = full[tuple(0 if v is None else v for v in idx)]
        return p if axes.index("Y") < axes.index("X") else p.T

    problems, checked = [], 0
    for rec_path in a["records"]:
        rec = json.load(open(rec_path))
        if rec["cmd"] == "windows":
            for bounds, digest, _shape in rec["digests"]:
                y0, y1, x0, x1 = bounds
                win = (
                    np.stack(
                        [plane_of(rec["t"], rec["z"], c)[y0:y1, x0:x1] for c in rec["channels"]]
                    )
                    if "C" in axes
                    else plane_of(rec["t"], rec["z"])[None, y0:y1, x0:x1]
                )
                checked += 1
                if sha(win) != digest:
                    problems.append([rec_path, "window", bounds])
        elif rec["cmd"] == "xyc":
            for r in rec["reads"]:
                n = full.shape[axes.index("C")] if "C" in axes else 1
                for c in range(n):
                    checked += 1
                    if sha(plane_of(r["t"], r["z"], c if "C" in axes else None)) != r["sha"][c]:
                        problems.append([rec_path, "xyc", r["t"], r["z"], c])
        elif rec["cmd"] == "planes":
            for r in rec["reads"]:
                p = plane_of(r["t"], r["z"], r["c"] if "C" in axes else None)
                cover = np.zeros(p.shape, dtype=np.uint8)
                for y, x, h, w, digest in r["blocks"]:
                    checked += 1
                    cover[y : y + h, x : x + w] += 1
                    if sha(p[y : y + h, x : x + w]) != digest:
                        problems.append([rec_path, "block", r["t"], r["z"], r["c"], y, x])
                if r["blocks"] and not (cover == 1).all():
                    problems.append([rec_path, "coverage", r["t"], r["z"], r["c"]])
                checked += 1
                if hist_record(compute_plane_histogram(p)) != r["hist"]:
                    problems.append([rec_path, "histogram", r["t"], r["z"], r["c"]])
    return {
        "cmd": "verify",
        "eager_s": eager_s,
        "full_shape": list(full.shape),
        "full_sha": sha(full),
        "checked": checked,
        "problems": problems,
    }


if __name__ == "__main__":
    cmd, arg, out = sys.argv[1], json.loads(sys.argv[2]), sys.argv[3]
    TRACE["on"] = bool(arg.get("trace"))
    import zarr

    result = {
        "cmd_args": arg,
        "zarr": zarr.__version__,
        "numpy": np.__version__,
        **globals()[f"cmd_{cmd}"](arg),
    }
    with open(out, "w") as f:
        json.dump(result, f)
    brief = {k: v for k, v in result.items() if k not in ("digests", "reads", "problems")}
    print(json.dumps(brief)[:1500])
    if "reads" in result:
        for r in result["reads"]:
            print({k: v for k, v in r.items() if k not in ("blocks", "sha", "hist")})
    if result.get("problems"):
        print("PROBLEMS", result["problems"][:20])
