# Kernel benchmark baselines

Baselines of `tools/benchmark_kernels.py` (#106) and `tools/benchmark_description_read.py`
(#505), recorded so a later optimisation has something to be compared with. **They are
baselines, not performance claims**: one machine, one run per case set, warm disk cache. A claim
needs a run of the same tool on the same machine before and after the change, with **equal
digests** for every case that has an output identity (the full reports, with every digest, the
digest of every generated input and every timed call, are
[`2026-11-25-kernels.json`](benchmark_results/2026-11-25-kernels.json) and
[`2026-11-25-description-read.json`](benchmark_results/2026-11-25-description-read.json)). The protocol of each tool is its docstring;
`docs/development.md` (*Benchmarks*) has the summary.

## Machine and run (25 November 2026)

| | |
|---|---|
| Code | `main` at `b683c7c` (`src` unchanged by these tools); the tools as of the review-fix commit of PR #544 |
| Processor | Intel(R) Xeon(R) Processor @ 2.10GHz, 4 CPUs usable, not pinned |
| Memory | 15.7 GiB (`MemTotal` 16,868,675,584 bytes) |
| System | Linux 6.18 x86_64, a cloud container (other tenants not controlled) |
| Python and libraries | Python 3.13.18, numpy 2.5.5, scipy 1.18.1, tifffile 2026.11.16, imagecodecs 2026.11.11, zarr 3.4.2, morphobricks-normalization 0.8.0 |
| Load average at start | 0.45 (kernels), 1.00 (description reader), 1-minute; no other benchmark or test run was running |
| Commands | `python tools/benchmark_kernels.py` (7 timed calls per case after one warm-up) and `python tools/benchmark_description_read.py --profile 3000` (3 timed calls per size) |
| Peak resident memory | 943 MiB (kernels process), 334 MiB (description reader process), `VmHWM` |

Times are the median of the timed calls (min to max in brackets); "traced" is the peak of the
memory `tracemalloc` saw in one extra call (Python objects and NumPy arrays, not a decoder's
own buffers).

`check_labels` returns `None`, so its case digests what it decides: the cells mask is accepted
and a copy with one negative pixel is refused (`ValueError`, with its message). The `inputs` of
each report hold the SHA-256 of every generated input: they depend on NumPy's random streams
(stable for one NumPy version, 2.5.5 here), so a comparison needs equal input digests too.

## Plane histogram (`compute_plane_histogram`, default bins)

"Region" is `check_region` of the cells mask (50.3 % of the plane) and the histogram of the
pixels in it.

| Pixel type | Side | Whole plane | Traced | Region | Traced |
|---|---:|---:|---:|---:|---:|
| uint8 | 2048 | 8.4 ms (8.3–10.7) | 32 MiB | 9.3 ms (8.4–16.2) | 22 MiB |
| uint16 | 2048 | 9.3 ms (9.1–11.5) | 33 MiB | 9.4 ms (8.7–10.1) | 25 MiB |
| float32 | 2048 | 56.4 ms (53.4–78.6) | 112 MiB | 29.3 ms (28.7–31.2) | 68 MiB |
| uint8 | 4096 | 74.2 ms (71.0–84.8) | 128 MiB | 61.1 ms (58.8–62.1) | 88 MiB |
| uint16 | 4096 | 77.9 ms (77.4–109) | 129 MiB | 64.8 ms (63.5–70.5) | 97 MiB |
| float32 | 4096 | 312 ms (309–367) | 448 MiB | 178 ms (173–206) | 274 MiB |

## Plane and window reads (`uint16`)

`read_plane` of the whole plane; `read_window` of the 512 px windows on the diagonal (4 at
2048 px, 8 at 4096 px). Every layout gives the same digest (checked against the written pixels
before timing). Tiles are 512 px, strips 64 rows, Zarr chunks 1024 px.

| Layout | 2048 plane | 2048 windows | 4096 plane | 4096 windows | Traced (4096 plane) |
|---|---:|---:|---:|---:|---:|
| TIFF tiled, zlib | 19.0 ms | 9.4 ms | 91.9 ms | 18.0 ms | 83 MiB |
| TIFF tiled, none | 3.8 ms | 1.6 ms | 35.3 ms | 3.3 ms | 96 MiB |
| TIFF stripped, zlib | 17.9 ms | 30.8 ms | 87.7 ms | 114 ms | 83 MiB |
| TIFF stripped, none | 1.1 ms | 2.2 ms | 6.6 ms | 7.3 ms | 32 MiB |
| OME-Zarr (NGFF 0.4) | 9.7 ms | 22.3 ms | 29.3 ms | 48.2 ms | 57 MiB |

## Mask step (two `int32` instance masks: cells, and a nucleus inside each)

| Kernel | 2048 px, 1,024 objects | Traced | 4096 px, 4,096 objects | Traced |
|---|---:|---:|---:|---:|
| `check_labels` (cells accepted, and one negative pixel refused) | 2.1 ms (1.8–2.5) | 906 bytes | 7.1 ms (6.9–8.6) | 906 bytes |
| `piece_counts` (cells) | 299 ms (293–327) | 132 MiB | 2,152 ms (1,953–2,219) | 528 MiB |
| `pair_counts` (nuclei in cells) | 41.5 ms (40.3–50.0) | 27 MiB | 245 ms (235–260) | 110 MiB |
| scan of one position (`_scan_plane`: both masks' pieces, both pairs) | 701 ms (674–734) | 132 MiB | 4,704 ms (4,601–4,933) | 528 MiB |

## Channel identity (`check_channel_identity`, three channels)

| Units | Images per unit | Time | Traced |
|---:|---:|---:|---:|
| 72 | 10 | 74.5 ms (73.2–79.3) | 0.4 MiB |
| 400 | 5 | 731 ms (712–793) | 2.1 MiB |

## Strict description reader (#505)

`read_description_bytes` of a canonical description with two conditions (two members per
repeat), one technical repeat per member, two channels and two mask types.

| Repeats | Members | Bytes | Time | Traced | Phase 5 | of which `_repeat_membership` |
|---:|---:|---:|---:|---:|---:|---:|
| 100 | 200 | 45,320 | 21.1 ms (20.6–24.2) | 1.1 MiB | 5.0 ms | 3.9 ms |
| 1,000 | 2,000 | 449,420 | 595 ms (574–602) | 10.8 MiB | 407 ms | 421 ms |
| 3,000 | 6,000 | 1,347,420 | 6.02 s (5.92–6.29) | 32.4 MiB | 5.36 s | 5.33 s |
| 10,000 | 20,000 | 4,490,420 | 64.6 s (62.0–66.1) | 107.7 MiB | 66.4 s | 63.7 s |

Growth exponent of the median time (`log(t2/t1) / log(n2/n1)`): 1.45 (100 to 1,000), 2.11
(1,000 to 3,000), 1.97 (3,000 to 10,000): quadratic. Decoding (`tomllib`) is 1.5 s at 10,000
repeats and linear; phases 2 to 4 and the warnings together are under 0.4 s.

The per-phase times (Phase 5 and `_repeat_membership` columns, and the `phase_seconds` of the
JSON) are **single calls**, made after the timed ones, not medians: they carry the run-to-run
spread of one call (a few per cent here), so a phase can read slightly below one of its own
parts (phase 5 below `_repeat_membership`: at 1,000 repeats in this run, and at 10,000 in an earlier run of this baseline, where 58.9 s was read against 59.1 s). Only the
total time column is a median of timed calls.

**Profile analysis, not a benchmark result** (one run under `cProfile`, which slows the code;
the call counts are exact, the times are not comparable with the table above):
`cProfile` at 3,000 repeats (35.9 s under the profiler): `_repeat_membership` 16.9 s own time,
34.4 s cumulative, of 35.9 s; `description_identity.nfc` 36,102,060 calls, `str.casefold`
36,132,071 calls, `unicodedata.normalize` 36,102,060 calls. The step is the last loop of
`description_semantics._repeat_membership` (the `E_REPEAT_ID_SHADOWS_SOURCE` check): for every
repeat it walks every member of every repeat and compares
`nfc(member["source_repeat"]).casefold()` with `nfc(repeat).casefold()`, so it makes
2 × repeats × members NFC normalisations (2 × 3,000 × 6,000 = 36,000,000), recomputing both keys
on every pair. Not fixed here (#505); a fix is a separate change, measured with the table above.
