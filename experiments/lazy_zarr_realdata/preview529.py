"""read_plane(preview=True) on a real store: peak RSS vs the memory limit; values vs eager stride.

Usage: python -I preview529.py STORE AXES MEMORY_LIMIT HARNESS_DIR
"""

import json
import sys
import time

import numpy as np


def main():
    path, axes, limit = sys.argv[1], sys.argv[2], int(sys.argv[3])
    sys.path.insert(0, sys.argv[4])
    import lazy529 as L
    import zarr

    from morphobricks_preprocessing.planes import read_plane
    from morphobricks_preprocessing.zarr_input import open_ngff

    m = L.mapping_of(axes)
    L.count_fetches()
    base = L.reset_peak()
    t0 = time.perf_counter()
    coords = {"t": 0, "z": 0, "c": 0} if "C" in axes else {}
    p = read_plane(path, m, memory_limit=limit, preview=True, **coords)
    wall = time.perf_counter() - t0
    peak = L.peak_since(base)
    step, digest = p.step, L.sha(p.pixels)
    del p
    im = open_ngff(path)
    a = zarr.open_array(f"{path}/{im.dataset_path}", mode="r")
    full = np.asarray(a[(0, 0, 0) if "C" in axes else ()])
    print(
        json.dumps(
            {
                "limit": limit,
                "step": step,
                "peak_rss": peak,
                "ratio_to_limit": peak / limit,
                "wall_s": wall,
                "fetch": L.FETCH,
                "identical_to_eager_stride": L.sha(full[::step, ::step]) == digest,
            }
        )
    )


if __name__ == "__main__":
    main()
