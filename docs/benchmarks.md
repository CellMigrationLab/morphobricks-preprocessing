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

### `piece_counts` by runs or by boxes (#69), before and after

`python tools/benchmark_kernels.py --group masks` (7 timed calls per case after one warm-up)
as of this change, with its three pixel-scale masks (`benchmark_inputs.pixel_mask`: a
two-label `uint16` checkerboard, a one-label `int32` checkerboard, and half the pixels
carrying labels 1 to 4), run on 26 November 2026 on the machine and libraries above, five
times in turn: **before** (`main` at `88a1c42`, the per-label function), **after** (this
change), **first** (the first version of PR #568 at `85715dc`, runs only), before, after.
The source measured was chosen with `PYTHONPATH`; the reports' `machine.commit` is the
tool's checkout (`85715dc`), not the source measured. Every case has the same digest in all
five runs, and the inputs the same digests in all five (and, for the cells and nuclei, as in
the baseline above). Another job's tests were running (1-minute load 1.9 to 2.1 at the
starts), shared by every run. Reports:
[before](benchmark_results/2026-11-26-kernels-masks-before-69.json),
[first](benchmark_results/2026-11-26-kernels-masks-first-69.json) and
[after](benchmark_results/2026-11-26-kernels-masks-after-69.json) (first round).

| Case | Before (runs 1, 2) | After (runs 1, 2) | First | Traced: before / after / first | Digest (first 16) |
|---|---:|---:|---:|---:|---|
| `piece_counts` cells 2048 | 322, 321 ms | 15.9, 16.9 ms | 35.9 ms | 132 / 8.5 / 12.1 MiB | `4abd144b846d176c` |
| `piece_counts` cells 4096 | 2,327, 2,061 ms | 103, 65.4 ms | 168 ms | 528 / 33.6 / 48.0 MiB | `c575615afca962cf` |
| scan of one position 2048 | 766, 821 ms | 206, 173 ms | 214 ms | 132 / 78.5 / 78.5 MiB | `69de16bd28281cc3` |
| scan of one position 4096 | 5,095, 4,889 ms | 965, 923 ms | 1,100 ms | 528 / 314 / 314 MiB | `ec5910c05e04c1c7` |
| two-label checkerboard 2048 | 195, 210 ms | 191, 178 ms | 1,481 ms | 116 / 116 / 852 MiB | `e90ded1a1d3176d9` |
| one-label checkerboard 2048 | 135, 112 ms | 147, 121 ms | 678 ms | 132 / 132 / 430 MiB | `6543cf267d7ad990` |
| noise, four labels 2048 | 250, 231 ms | 234, 237 ms | 549 ms | 132 / 132 / 253 MiB | `484c7df9ddc9e95d` |
| two-label checkerboard 4096 | 931, 932 ms | 1,009, 961 ms | 7,374 ms | 464 / 464 / 3,407 MiB | `e90ded1a1d3176d9` |
| one-label checkerboard 4096 | 574, 570 ms | 556, 568 ms | 3,366 ms | 528 / 528 / 1,720 MiB | `6543cf267d7ad990` |
| noise, four labels 4096 | 1,248, 1,215 ms | 1,049, 1,235 ms | 2,917 ms | 528 / 528 / 1,011 MiB | `99f3f03132d213f6` |

Medians (the reports hold the min to max). `pair_counts` is unchanged (241 to 259 ms at 4096 px
in every run). Peak resident memory of the benchmark process: 905 and 891 MiB before, 889 and
889 MiB after, 3,720 MiB first.

**The trade-off.** The runs route costs time and memory per run (a run is consecutive pixels
of one label in a row), and the former route per pixel (a sort of every pixel, about 32 bytes
each, then a labelling per label in its bounding box). On masks of objects (tens of pixels
across) runs are a few per hundred pixels and the runs route is 10 to 30 times faster with a
tenth of the memory or less. On pixel-scale masks (noise, checkerboards) runs are almost
pixels: the first version took up to 8 times longer and 7 times the memory there (3.4 GiB on
the 4096 px two-label checkerboard). `piece_counts` now counts the runs first and takes
the former route above `RUNS_PER_PIXEL_LIMIT` = 0.25 runs per pixel, so these masks
cost what they cost before (the counting adds one boolean plane, 16 MiB at 4096 px, freed
before the former route starts). The limit was chosen from a sweep at 4096 px outside the tool
(median of 3 calls, `tracemalloc` peak, equal outputs): runs route / former route 0.29 / 0.85 s
and 100 / 528 MiB at 0.08 runs per pixel (8 px blocks of 4 labels), 0.54 / 0.92 s and 183 /
528 MiB at 0.16, 0.74 / 0.91 s and 238 / 528 MiB at 0.21, 0.89 / 0.81 s and 220 / 528 MiB at
0.25 (noise of one label at half the pixels, the most runs one label can make), 1.15 / 1.05 s
and 346 / 528 MiB at 0.32, 1.57 / 1.10 s at 0.44, 2.02 / 0.49 s at 0.5 and 5.0 / 0.92 s and
1,136 / 464 MiB at 1. Below the limit the runs route never used more memory than the former
one and was at most about 10 % slower (at the limit itself); above it the route is the former
one. A mask just below the limit that the runs route handles badly in time is possible but
was not found.

Outside the tool (the former function kept in `tests/test_mask_pieces.py` against the new
one, median of 3 calls, equal outputs): a 2048 px mask of 1,000 labels scattered over 20 % of
the pixels (0.20 runs per pixel) took 15.2 s before, 0.11 s after; 4,000 touching Voronoi
cells at 2048 px, 0.38 s before, 0.041 s after. The former function's time on the cells was
mostly its sort of every pixel (about 2.5 s of the 4096 px case alone), not its loop of
per-label `ndimage.label` (about 0.15 s); the loop dominates when labels are spread out.

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
