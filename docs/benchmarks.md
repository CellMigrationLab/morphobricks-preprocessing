# Kernel benchmark baselines

Baselines of `tools/benchmark_kernels.py` (#106) and `tools/benchmark_description_read.py`
(#505), recorded so a later optimisation has something to be compared with. **They are
baselines, not performance claims**: one machine, one run per case set, warm disk cache. A claim
needs a run of the same tool on the same machine before and after the change, with **equal
digests** for every case (the full reports, with every digest and every timed call, are
[`2026-11-25-kernels.json`](benchmark_results/2026-11-25-kernels.json) and
[`2026-11-25-description-read.json`](benchmark_results/2026-11-25-description-read.json)). The protocol of each tool is its docstring;
`docs/development.md` (*Benchmarks*) has the summary.

## Machine and run (25 November 2026)

| | |
|---|---|
| Code | `main` at `b683c7c` (`src` unchanged by these tools) |
| Processor | Intel(R) Xeon(R) Processor @ 2.10GHz, 4 CPUs usable, not pinned |
| Memory | 15.7 GiB (`MemTotal` 16,868,675,584 bytes) |
| System | Linux 6.18 x86_64, a cloud container (other tenants not controlled) |
| Python and libraries | Python 3.13.18, numpy 2.5.5, scipy 1.18.1, tifffile 2026.11.16, imagecodecs 2026.11.11, zarr 3.4.2, morphobricks-normalization 0.8.0 |
| Load average at start | 1.78 (kernels), 2.00 (description reader), 1-minute; no other benchmark or test run was running |
| Commands | `python tools/benchmark_kernels.py` (7 timed calls per case after one warm-up) and `python tools/benchmark_description_read.py --profile 3000` (3 timed calls per size) |
| Peak resident memory | 885 MiB (kernels process), 334 MiB (description reader process), `VmHWM` |

Times are the median of the timed calls (min to max in brackets); "traced" is the peak of the
memory `tracemalloc` saw in one extra call (Python objects and NumPy arrays, not a decoder's
own buffers).

## Plane histogram (`compute_plane_histogram`, default bins)

"Region" is `check_region` of the cells mask (50.3 % of the plane) and the histogram of the
pixels in it.

| Pixel type | Side | Whole plane | Traced | Region | Traced |
|---|---:|---:|---:|---:|---:|
| uint8 | 2048 | 7.9 ms (7.4–10.0) | 32 MiB | 7.8 ms (7.5–25.6) | 22 MiB |
| uint16 | 2048 | 9.8 ms (9.2–13.2) | 33 MiB | 9.7 ms (9.2–23.8) | 25 MiB |
| float32 | 2048 | 56.7 ms (55.2–60.8) | 112 MiB | 30.3 ms (29.8–31.9) | 68 MiB |
| uint8 | 4096 | 72.4 ms (70.8–74.0) | 128 MiB | 58.3 ms (55.1–59.6) | 88 MiB |
| uint16 | 4096 | 71.6 ms (70.2–76.4) | 129 MiB | 61.0 ms (58.7–66.6) | 98 MiB |
| float32 | 4096 | 303 ms (276–314) | 448 MiB | 188 ms (171–240) | 274 MiB |

## Plane and window reads (`uint16`)

`read_plane` of the whole plane; `read_window` of the 512 px windows on the diagonal (4 at
2048 px, 8 at 4096 px). Every layout gives the same digest (checked against the written pixels
before timing). Tiles are 512 px, strips 64 rows, Zarr chunks 1024 px.

| Layout | 2048 plane | 2048 windows | 4096 plane | 4096 windows | Traced (4096 plane) |
|---|---:|---:|---:|---:|---:|
| TIFF tiled, zlib | 21.1 ms | 8.7 ms | 96.4 ms | 18.1 ms | 83 MiB |
| TIFF tiled, none | 4.0 ms | 1.5 ms | 35.5 ms | 3.5 ms | 96 MiB |
| TIFF stripped, zlib | 16.3 ms | 28.9 ms | 86.4 ms | 113.6 ms | 83 MiB |
| TIFF stripped, none | 1.1 ms | 2.3 ms | 7.4 ms | 6.9 ms | 32 MiB |
| OME-Zarr (NGFF 0.4) | 10.7 ms | 22.0 ms | 34.4 ms | 49.0 ms | 57 MiB |

## Mask step (two `int32` instance masks: cells, and a nucleus inside each)

| Kernel | 2048 px, 1,024 objects | Traced | 4096 px, 4,096 objects | Traced |
|---|---:|---:|---:|---:|
| `check_labels` (cells) | 1.3 ms | 0 | 4.9 ms | 0 |
| `piece_counts` (cells) | 317 ms (289–351) | 132 MiB | 2,068 ms (2,006–2,121) | 528 MiB |
| `pair_counts` (nuclei in cells) | 42 ms (39–52) | 27 MiB | 241 ms (227–277) | 110 MiB |
| scan of one position (`_scan_plane`: both masks' pieces, both pairs) | 708 ms (689–765) | 132 MiB | 4,848 ms (4,576–5,047) | 528 MiB |

## Channel identity (`check_channel_identity`, three channels)

| Units | Images per unit | Time | Traced |
|---:|---:|---:|---:|
| 72 | 10 | 74 ms (72–80) | 0.4 MiB |
| 400 | 5 | 716 ms (689–805) | 2.1 MiB |

## Strict description reader (#505)

`read_description_bytes` of a canonical description with two conditions (two members per
repeat), one technical repeat per member, two channels and two mask types.

| Repeats | Members | Bytes | Time | Traced | Phase 5 | of which `_repeat_membership` |
|---:|---:|---:|---:|---:|---:|---:|
| 100 | 200 | 45,320 | 20.3 ms (20.2–23.0) | 1.1 MiB | 4.9 ms | 3.8 ms |
| 1,000 | 2,000 | 449,420 | 567 ms (556–576) | 10.8 MiB | 387 ms | 362 ms |
| 3,000 | 6,000 | 1,347,420 | 5.64 s (5.61–5.68) | 32.4 MiB | 5.36 s | 5.24 s |
| 10,000 | 20,000 | 4,490,420 | 60.8 s (60.4–61.2) | 107.7 MiB | 58.9 s | 59.1 s |

Growth exponent of the median time (`log(t2/t1) / log(n2/n1)`): 1.45 (100 to 1,000), 2.09
(1,000 to 3,000), 1.97 (3,000 to 10,000): quadratic. Decoding (`tomllib`) is 1.1 s at 10,000
repeats and linear; phases 2 to 4 and the warnings together are under 0.3 s.

`cProfile` at 3,000 repeats (32.7 s under the profiler): `_repeat_membership` 15.3 s own time,
31.4 s cumulative, of 32.7 s; `description_identity.nfc` 36,102,060 calls, `str.casefold`
36,132,071 calls, `unicodedata.normalize` 36,102,060 calls. The step is the last loop of
`description_semantics._repeat_membership` (the `E_REPEAT_ID_SHADOWS_SOURCE` check): for every
repeat it walks every member of every repeat and compares
`nfc(member["source_repeat"]).casefold()` with `nfc(repeat).casefold()`, so it makes
2 × repeats × members NFC normalisations (2 × 3,000 × 6,000 = 36,000,000), recomputing both keys
on every pair. Not fixed here (#505).
