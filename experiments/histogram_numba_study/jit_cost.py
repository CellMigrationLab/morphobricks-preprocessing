"""Time import numba, then the first call of each kernel (compile, or load from NUMBA_CACHE_DIR)."""

import importlib
import json
import sys
import time
from pathlib import Path

import numpy as np

t = time.perf_counter()
importlib.import_module("numba")
t_import = time.perf_counter() - t
sys.path.insert(0, str(Path(__file__).resolve().parent))
numba_patch = importlib.import_module("numba_patch")

count_small, float_counts, bin_starts, sum_bins = numba_patch._kernels()
out = {"import_numba_s": t_import}


def first(name, f, *a):
    t = time.perf_counter()
    f(*a)
    out[name] = time.perf_counter() - t


first(
    "count_small_u16", count_small, np.zeros(10, np.uint16), np.zeros(65536, np.int64), np.uint16(0)
)
first("count_small_u8", count_small, np.zeros(10, np.uint8), np.zeros(256, np.int64), np.uint8(0))
first(
    "float_counts_f32",
    float_counts,
    np.zeros(10, np.float32),
    np.zeros(4, np.int64),
    0,
    0.0,
    1.0,
    4.0,
)
first(
    "float_counts_f64",
    float_counts,
    np.zeros(10, np.float64),
    np.zeros(4, np.int64),
    0,
    0.0,
    1.0,
    4.0,
)
first("bin_starts", bin_starts, 100, 10)
first("sum_bins", sum_bins, np.zeros(100, np.int64), np.arange(11, dtype=np.int64) * 10, 10)
out["total_first_calls_s"] = sum(v for k, v in out.items() if k != "import_numba_s")
print(json.dumps(out))
