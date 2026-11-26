"""Traced peak and plan of read_plane(preview=True) on a real store (measure only).

Usage: python -I preview_trace.py STORE AXES MEMORY_LIMIT HARNESS_DIR
"""

import json
import sys
import tracemalloc


def main():
    path, axes, limit = sys.argv[1], sys.argv[2], int(sys.argv[3])
    sys.path.insert(0, sys.argv[4])
    import lazy529 as L

    from morphobricks_preprocessing.plane_binning import Binning
    from morphobricks_preprocessing.planes import (
        _open_zarr,
        _whole_or_preview,
        _zarr_layout,
        _zarr_reblock,
        read_plane,
    )
    from morphobricks_preprocessing.windows import read_window

    m = L.mapping_of(axes)
    im = _open_zarr(path)
    layout = _zarr_layout(im, m, path, Binning(histogram=False), limit)
    lay = _whole_or_preview(layout, path, True, _zarr_reblock(im, m, path))
    read_window(path, m, bounds=(0, 1, 0, 1), channels=[0], t=0, z=0)  # warm-up: imports, codecs
    L.count_fetches()
    base = L.reset_peak()
    tracemalloc.start()
    s0 = tracemalloc.get_traced_memory()[0]
    p = read_plane(path, m, memory_limit=limit, preview=True, t=0, z=0, c=0)
    traced = tracemalloc.get_traced_memory()[1] - s0
    tracemalloc.stop()
    peak = L.peak_since(base)
    print(
        json.dumps(
            {
                "limit": limit,
                "step": p.step,
                "in_blocks": lay.in_blocks,
                "block_shape": lay.block_shape,
                "block_estimate": lay.block_memory_estimate,
                "whole_estimate": lay.memory_estimate,
                "traced_peak": traced,
                "traced/limit": round(traced / limit, 3),
                "rss_peak": peak,
                "rss/limit": round(peak / limit, 3),
            }
        )
    )


if __name__ == "__main__":
    main()
