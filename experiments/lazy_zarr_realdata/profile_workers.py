"""Split map_windows wall time on a real store: fingerprint checks vs reads (measure only).

Usage: python -I profile_workers.py STORE AXES TILE MARGIN Z_JSON T_JSON TIME_CHECKS HARNESS_DIR
The reader's ``_check`` is wrapped to time it (its result and effect unchanged).
"""

import json
import sys
import time


def main():
    path, axes = sys.argv[1], sys.argv[2]
    tile, margin = int(sys.argv[3]), int(sys.argv[4])
    z, t, timed_checks = json.loads(sys.argv[5]), json.loads(sys.argv[6]), sys.argv[7] == "1"
    sys.path.insert(0, sys.argv[8])
    from lazy529 import grid, sha

    from morphobricks_preprocessing.axes import AxisMapping
    from morphobricks_preprocessing.window_map import map_plan, map_windows
    from morphobricks_preprocessing.window_reader import WindowReader, cache_plan
    from morphobricks_preprocessing.zarr_input import open_ngff

    m = AxisMapping(axes, tuple(axes))
    im = open_ngff(path)
    plane = (im.shape[axes.index("Y")], im.shape[axes.index("X")])
    wins = grid(plane, tile, margin)
    cp = cache_plan(path, m, window=(tile, tile), margin=(margin, margin), channels=[0])
    for w in (1, 2, 4):
        r = WindowReader(path, m, cache_bytes=cp.cache_bytes, memory_limit=2**30 + cp.cache_bytes)
        calls = [0, 0.0]
        if timed_checks:

            def timed(orig=r._check, calls=calls):
                t0 = time.perf_counter()
                orig()
                calls[0] += 1
                calls[1] += time.perf_counter() - t0

            r._check = timed
        p = map_plan(r, wins, channels=[0], workers=w, z=z, t=t)
        t0 = time.perf_counter()
        with map_windows(p, lambda b, a: sha(a), memory_budget=p.memory_estimate) as res:
            list(res)
        print(
            f"workers={w} wall={time.perf_counter() - t0:.2f}s checks={calls[0]} "
            f"check_time={calls[1]:.2f}s(sum over threads) decodes={r.stats().decodes}"
        )
        r.close()


if __name__ == "__main__":
    main()
