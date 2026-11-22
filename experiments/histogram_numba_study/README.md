# Numba for the histogram step? (study, November 2026; not for merge)

**Question (owner):** would Numba speed up the histogram step of a preprocessing run, now
that image workers (v0.6.0, #107) gave only about 1.2× on 4 CPUs?

**Short answer: no for Numba.** The per-pixel binning is a small share of the run, and
`np.bincount` already releases the GIL. A Numba kernel for it gives 1.01–1.09×, which is
inside the run-to-run spread. The measurable win is elsewhere: pure-Python loops that run
once per bin (65,536 per plane), in `morphobricks_normalization`. Plain NumPy int64 code
removes them with byte-identical outputs and **no new dependency**: 1.48× serial and 1.31×
with 4 workers, the same as Numba within the spread.

Base: preprocessing v0.6.1 (`37c583e`), normalization v0.7.0. The scripts here are kept as
they were run; nothing in `src/` changes.

## Who owns the hot code

Every hot function below is in **morphobricks-normalization** (`histogram.py`,
`integer_representatives.py`, `csv_io.py`). Preprocessing only calls it
(`planes.Plane.histogram_with_check`, staging, `check_intensities`). Any real change belongs
in that repository. Here the prototype is a run-time monkeypatch (`numba_patch.py`), used
only to run it through the real preprocessing pipeline. The normalization repository was not
modified.

## Machine and data

- Linux x86_64, 4 CPUs **shared with other jobs**: the load average was 2.7–4.2 during the
  runs. Python 3.13.18, NumPy 2.5.5, Numba 0.68.1, llvmlite 0.50.0.
- Data: the dataset from `tools/benchmark_run_workers.py` at scale 1. That is 14 images
  (seeded uint16, tiled zlib OME-TIFF and OME-Zarr, 4 read in blocks), 304 MB on disk and
  76 planes. Memory limit 64 MiB unless stated.
- Every run is a fresh process with a warm disk cache. CPU is user + system time, including
  worker threads and any child processes. Raw numbers: `measurements.json`.

## 1. Profile: where the time goes

cProfile, serial run (10.0 s under the profiler; 7.9 s median without it):

| Part | Seconds | Thread with workers |
|---|---:|---|
| Decode/read (zlib tiles, zarr chunks) | 1.16 | worker |
| **Per-pixel binning**: `np.bincount` of the 16-bit per-value table + `table +=` | ≈0.61 | worker |
| Min/max | ≈0 (from the per-value table) | worker |
| Bin limits for the counts (`min_max` → `_integer_bin_starts`: a 65,537-item Python list per call) | 1.08 | worker |
| Content SHA-256 after the read | 0.83 | worker |
| Tail moments (exact rationals) | 0 (min–max range, exact uint16 histograms: never called) | – |
| Staging CSV per image (`write_plane_histograms`; `table_header` alone builds the 131,110-name header, 15 times: 1.11 s) | 2.16 | **calling thread** |
| Intensity checks and pooling (`representatives`, `integer_bin_limits`, `max_representative_error`: per-bin Python) | 2.93 | **calling thread** |
| Final CSV, imports, discovery, the rest | ≈1.2 | calling thread |

Only about 3.6 s of the 10 s run in workers (the read and check of one image, plan §12
q. 82). Staging, checks and publication stay on the calling thread, so Amdahl's law caps
threads at about 1.4× here, whatever the histogram kernel. That matches the 1.2–1.4×
measured for #107.

GIL holders (py-spy `--gil`, workers = 4, share of samples where some thread held the GIL):

| | current (1,366 samples) | Numba "all" (805 samples) |
|---|---:|---:|
| CSV writing (header names, `writerow`, cell text) | 36.7% | 53.2% |
| Per-bin Python loops (bin starts/limits, representatives, error) | 31.5% | 4.7% |
| Imports, discovery, staging, other | 13.0% | 23.5% |
| Decode/read | 9.6% | 11.4% |
| Per-pixel binning | 7.0% | 5.1% |
| Hashing | 1.3% | 1.2% |

GIL-releasing or not:

- `np.bincount` on 9.4 M uint16 values ran at 1.8–2.6× aggregate throughput with 4 threads,
  so it is mostly GIL-free already.
- `np.add.at` (1.3×) holds the GIL more.
- zlib, hashlib and the NumPy ufuncs release it.
- What holds it is Python: list comprehensions over bins, f-strings for column names and
  `csv.writer`.

## 2. Prototype (`numba_patch.py`)

There are four modes:

- **`pixels`**: Numba `@njit(nogil=True, cache=True)` kernels for the per-pixel work.
  - 8/16-bit per-value counting replaces `np.bincount`.
  - Float binning replaces `floor(float_bin_position(...))`, clip and `bincount`. It uses
    the same IEEE operations in the same order, with no fastmath (so no FMA contraction),
    in each of the three `float_bin_position` branches. Non-finite values are skipped as
    `values[np.isfinite(values)]` does, and float32 is widened to float64 exactly as
    `astype` does.
- **`all`**: `pixels`, plus Numba kernels for the per-bin work:
  - `_integer_bin_starts` in int64 when `n * width < 2**62`;
  - `_counts_from_per_value` as exact int64 sums (today: float64 weights plus `rint`, which
    is the same integer below 2**53 pixels per bin);
  - `representatives` and `max_representative_error` for integer planes below 2**51.
- **`numpy-bins`**: the same per-bin work in plain vectorised NumPy int64, without Numba.
- Unchanged in every mode: tail moments (exact `Fraction` sums, which cannot be done bit for
  bit in Numba's fixed-width types), 32/64-bit integer binning, and the radix selection.

Outside the int64 guards the original exact Python code runs. Both paths are exact, so this
is not a fallback that can change a result. Integer counts are exact by construction. The
only floats produced are the float bin positions (same operations) and the representatives
(`(first + last) / 2`), which are the same float64 below 2**51.

## 3. Identity

| Check | Result |
|---|---|
| Differential test (`diffcheck.py`), modes `all` and `numpy-bins`: every `PlaneHistogram` field, counts, both tails, `representatives` and `max_representative_error` compared as raw bits, `integer_bin_limits`, and the `RangeCheck`. Cases: 10 dtypes, NaN/±inf, subnormal, constant, empty and dtype-extreme planes, bin-edge float grids, 1/3/256/500/10,000/default bins, min–max and robust range, whole and in 3 blocks | **0 of 5,580 cases differ** |
| Negative control: the float kernel moved by one ulp before `floor` | caught (66 cases differ) |
| `tests/test_run_workers_bytes.py` (Spec fixtures **v05, v09**: every file as raw bytes, workers 1/2/4/auto, fixed clock), histogram goldens (uint16 at 500 bins, float32), robust range, workers, planes, run, intensity | 307 passed with `pixels`, `all` and `numpy-bins` (307 passed without the patch) |
| Whole test suite with `all` installed (`-p numba_patch_plugin`, notebook extra installed) | 6,021 passed, 4 skipped. The skips are platform skips: a case-sensitive file system and root ignoring file modes |
| Benchmark data: all 6 output files as raw bytes (fixed clock, one shared output folder), current / `pixels` / `all` / `numpy-bins` × workers 1/2/4/auto | **all 16 identical** to current serial |

## 4. Speed

Wall time of `run_experiment`, 5 repeats, variants interleaved in rotating order. Median
(min–max); the speed-up is against current code at the same worker count.

| workers | current | Numba `pixels` | Numba `all` | NumPy `numpy-bins` |
|---|---|---|---|---|
| 1 | 7.93 s (6.61–8.85) | 7.25 (6.37–9.26) **1.09×** | 5.77 (4.83–5.97) **1.37×** | 5.37 (5.12–6.70) **1.48×** |
| 2 | 6.95 (5.73–8.22) | 6.56 (5.86–7.49) 1.06× | 4.78 (3.89–5.77) 1.45× | 5.16 (4.41–5.63) 1.35× |
| 4 | 6.67 (5.50–7.20) | 6.61 (5.99–7.03) 1.01× | 4.89 (4.04–5.44) 1.36× | 5.10 (4.62–5.75) 1.31× |
| auto (4) | 6.39 (5.86–7.63) | 6.05 (5.70–7.03) 1.06× | 4.43 (4.24–5.05) 1.44× | 4.90 (4.40–5.42) 1.30× |

Median CPU seconds at workers 1 and 4:

| | workers 1 | workers 4 |
|---|---:|---:|
| current | 7.7 | 7.6 |
| `pixels` | 6.8 | 7.1 |
| `all` | 5.4 | 5.5 |
| `numpy-bins` | 5.3 | 6.1 |

The best combination against current serial is `all` at auto: 1.79×.

How to read the table:

- The `pixels` speed-up (the "histogram step" proper) is inside the spread of the current
  code.
- `all` and `numpy-bins` overlap. Their gain comes almost entirely from the per-bin part.

**JIT cost** (6 kernel signatures used here; 3 trials each):

| Cost | Seconds |
|---|---:|
| `import numba` | 0.14–0.31 |
| First compile, empty cache | 0.84–1.01 |
| First call with `cache=True` warm | 0.23–0.25 (mostly the first kernel's load) |

The cache is written next to the module (or `NUMBA_CACHE_DIR`) and is keyed by file path, so
a new install or a new Colab session compiles again. That costs about 1 s per process, or per
worker process with process workers.

## 5. Alternatives (measured briefly)

**Process workers.** The `ThreadPoolExecutor` was swapped for a fork-based
`ProcessPoolExecutor`. This is a measurement hack: `FileJob` cannot be pickled, so it is
inherited at fork. 3 repeats; all output files identical.

| workers | current threads | processes | Numba `all` threads | processes + `all` |
|---|---|---|---|---|
| 4 | 6.55 (6.42–6.90) | 4.80 (4.46–6.00) | 4.16 (3.88–4.56) | 3.95 (3.51–4.16) |
| auto | 5.79 (5.70–6.52) | 5.09 (4.30–5.30) | 4.18 (4.04–5.10) | 3.90 (3.60–4.48) |

Processes help (1.1–1.4×), but less than removing the per-bin loops. They would need a
picklable job, a copy of every image's rows back through a pipe, more memory, and the
`spawn` start method on Windows and macOS.

**GIL-releasing NumPy.** `np.bincount` already releases the GIL (see §1). The `numpy-bins`
variant is the GIL-light NumPy alternative for the per-bin part, and it matches Numba.

**Chunked reads** (memory limit = block size; `current` and `all`, workers 1 and 4,
3 repeats):

| Memory limit | current, w1 | current, w4 |
|---|---:|---:|
| 8 MiB | 10.58 s | 9.05 s |
| 64 MiB | 6.74 s | 6.45 s |
| 256 MiB | 7.68 s | 6.59 s |

Smaller blocks are slower and whole planes are no faster. `plane_histograms.csv` and
`experiment_design.csv` are identical at every limit. The manifest records the limit and the
block reads, so it differs by design.

## 6. Recommendation

1. **No-go: Numba for the histogram step.**
   - The per-pixel kernels give ≤ 1.09×, inside the noise, because `np.bincount` is already
     C code that releases the GIL.
   - The costs are high:
     - a required or optional dependency of about 207 MB installed (numba 34 MB, llvmlite
       173 MB);
     - wheels (0.68.1) for Linux x86_64/aarch64, Windows amd64/arm64 and **macOS arm64
       only**: no Intel-Mac wheel;
     - Numba caps NumPy (`numpy<2.6` for 0.68.1), so it would hold back NumPy upgrades;
     - about 1 s of compile per fresh environment (every new Colab session), 0.25 s per
       process with a warm cache;
     - a second code path whose identity would need guarding forever.
   - An "optional" Numba path is only worth that cost for a clear gain, and none was
     measured.
2. **Go (in morphobricks-normalization): vectorise the per-bin loops in exact NumPy int64.**
   - Targets: `_integer_bin_starts`, `_counts_from_per_value`, `representatives`,
     `max_representative_error` and `integer_bin_limits` users.
   - Keep the existing Python code where int64 or float64 exactness is not guaranteed.
   - Measured here: 1.48× serial and 1.31× at 4 workers, byte-identical, no new dependency.
   - Proposed as a normalization issue; `numba_patch.py`'s `numpy-bins` mode is the sketch.
3. **Next target: the CSV writer** (also normalization: `csv_io.py`).
   - After step 2 it holds more than half of all GIL time.
   - `table_header` rebuilds the same 131,110 column names for every staged file. Caching
     it, or writing counts with `np.savetxt`-style formatting, needs its own byte-identity
     study.
   - With staging on the calling thread, this caps what more workers can give.
4. Process workers and smaller blocks: not now (see §5).

## Files

| File | What |
|---|---|
| `numba_patch.py` | The prototype (modes `pixels`, `all`, `bins`, `numpy-bins`); `install(mode)` |
| `numba_patch_plugin.py` | `pytest -p numba_patch_plugin` installs it for a test run (`MB_PATCH_MODE`) |
| `diffcheck.py` | The differential identity check, original against patched |
| `runonce.py`, `bench.py` | One run (fixed clock, SHA-256 of every file, CPU) and the interleaved benchmark |
| `jit_cost.py` | Numba import, compile and cached first-call times |
| `spycat.py` | Groups py-spy raw stacks into the categories of §1 |
| `measurements.json` | Every run's wall and CPU time and the summaries |

To reproduce, from the repository root, in an environment with `numba`, `py-spy` and the
`zarr` extra (`PYTHONPATH` must hold this folder for the plugin):

```bash
python experiments/histogram_numba_study/diffcheck.py all
MB_PATCH_MODE=all PYTHONPATH=experiments/histogram_numba_study \
    python -m pytest -p numba_patch_plugin tests/test_run_workers_bytes.py
python experiments/histogram_numba_study/bench.py BENCH_FOLDER WORK_FOLDER 5 \
    current pixels all numpy-bins -- 1 2 4 auto
```

`BENCH_FOLDER` is a folder written by `tools/benchmark_run_workers.py`'s `write_dataset`
(holding `experiment/` and the published description).
