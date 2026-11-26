# Lazy OME-Zarr reads on real data (#529, 26 November 2026)

This record is **measure only**. No code in `src` was changed. It tests the lazy OME-Zarr routes
on public, real OME-NGFF stores: windows, `map_windows`, `read_xyc_plane`, the block reader,
histograms and previews. For each route it reports chunk fetches, peak memory against the plan,
and wall time. Every output is checked against one eager zarr read of the whole level-0 array.
It records measurements, not performance claims: one machine, mostly one run per case. Defects
found: #569 (memory) and #570 (thread scaling). Existing issues got new evidence: #78, #342 and
#378.

## Set-up

| | |
| --- | --- |
| Code | `main` at `316be56`, installed with `pip install --no-cache-dir -e ".[zarr,dev]"` in a fresh venv |
| Python and libraries | Python 3.13.18, numpy 2.5.5, zarr 3.4.2, numcodecs 0.17.1, imagecodecs 2026.11.11, morphobricks-normalization 0.8.0 |
| Machine | Intel Xeon @ 2.10 GHz, 4 CPUs (not pinned), 15.7 GiB; Linux 6.18 cloud container. Load average 0.0 to 1.9 during runs; nothing else heavy was running |
| Scripts | [`experiments/lazy_zarr_realdata/`](../../experiments/lazy_zarr_realdata/): `fetch_level0.py` (download), `lazy529.py` (measurements and the eager check), `preview529.py`, `preview_trace.py`, `profile_workers.py`, and the run scripts `run_ebi.sh` / `run_rest.sh` (take the data and output directories from `ZARR529_DATA` and `ZARR529_OUT`; see the experiment README) |
| Results | [`docs/benchmark_results/2026-11-26-lazy-zarr-realdata.json`](../benchmark_results/2026-11-26-lazy-zarr-realdata.json): every measured number and every output digest summary (long read lists are aggregated) |

Measurement method (in `lazy529.py`):

- **One case per process.** Every case runs in a fresh `python -I` process.
- **Peak RSS** is `VmHWM` above `VmRSS`, read after `malloc_trim(0)` and `/proc/self/clear_refs`
  = 5 just before the measured call.
- **Traced peak.** Where stated, `tracemalloc` peak above the memory held at the start.
- **Fetches** are calls and bytes of zarr's `LocalStore.get`, counted by a wrapper that returns
  zarr's value unchanged. "Ranged" calls carry a byte range: a shard index or an inner chunk.
- **Axes** were stated from each store's NGFF axes (`AxisMapping(axes, axes)`), not guessed.
- **Eager check.** `verify` reads level 0 once with `zarr.open_array(...)[...]`. It then compares
  every recorded window, XYC plane and block (SHA-256 of the bytes, and that the blocks cover the
  plane exactly once) and every histogram. A histogram is compared on its `counts` digest, binning
  range, totals, minimum and maximum, against `compute_plane_histogram` of the eager plane.

## Datasets

`uk1s3.embassy.ebi.ac.uk` (IDR's OME-NGFF samples, idr0054/idr0062) was **not reachable**. The
proxy's CONNECT succeeded, then the TLS handshake was reset (`curl: (35) Recv failure`) on every
try. So IDR was not used. The stores below come from the OME 2024 NGFF challenge sample lists and
the SciVis OME-Zarr collection. Only the root metadata and the level-0 array were downloaded,
chunk keys derived from the array metadata. Downloaded byte totals equal the challenge's
`written` figures, and no chunk was absent. Total download: 574 MiB, deleted at the end.

| Name here | Source | NGFF / zarr | Level 0 | Chunks (shards) | Codec | Bytes |
| --- | --- | --- | --- | --- | --- | --- |
| `ebi_cy5` | `livingobjects.ebi.ac.uk/…/ebi-ngff-challenge-2024/7cc10a97-23f1-4e3e-80f2-d91dc39f2ab9.zarr` (S-BIAD1083, human bronchial organoid, "SD Cy5", CC BY 4.0) | 0.5 / 3 | TCZYX (1,1,1,11898,11898) uint16 | (1,1,1,256,256) in (1,1,1,2048,2048) | blosc zstd 5, shuffle | 214,114,712 |
| `ssbd_rgb` | `dmss3gw.riken.jp/ssbd/zarr/v0.5/0620364b-b91a-44b6-a290-24fd84d38264.zarr` (SSBD 199-Ichimura, AMATERAS mouse brain slice, 3 channels, CC BY-NC 4.0) | 0.5 / 3 | TCZYX (1,3,1,9180,13264) uint8 | (1,1,1,2295,3316) in one shard of the whole array | blosc zstd 5, bitshuffle | 41,746,026 |
| `ssbd_smovie` | `dmss3gw.riken.jp/ssbd/zarr/v0.5/71df7f94-2661-4438-9a6d-3b317cd23409.zarr` (SSBD 170-Tsutsui, Airyscan hook basement membrane, 3 channels × 58 Z, CC BY 4.0) | 0.5 / 3 | TCZYX (1,3,58,844,844) uint16 | (1,1,1,844,844) in one shard of the whole array | blosc zstd 5, shuffle | 120,567,402 |
| `marmoset_v04` | `ome-zarr-scivis.s3.us-east-1.amazonaws.com/v0.4/64x0/marmoset_neurons.ome.zarr` | 0.4 / 2 | ZYX (314,1024,1024) uint8 | (64,64,64) | zstd 0 | 109,113,270 (1287 files) |
| `marmoset_v05` | the same volume, `…/v0.5/64x0/marmoset_neurons.ome.zarr` | 0.5 / 3 | ZYX (314,1024,1024) uint8 | (64,64,64) | zstd 0 | 109,113,537 (1283 files) |

The two marmoset stores hold the same pixels: the eager SHA-256 of both level-0 arrays is
`c4949b80253fd9aa…`. They are a zarr v2 against zarr v3 pair, both read with zarr 3.4.2. The
three challenge stores are **sharded**, a layout outside the synthetic matrix (section 2 of
`docs/design/lazy-windows.md`). So are their codecs: blosc zstd with shuffle or bitshuffle is
not in `ZARR_MEASURED`.

Header only: `open_ngff` took 3 to 4 ms on each store, and 127 ms on the first store opened
(imports).

## Identity

**Every lazy output equals the eager read.** `verify` reported 0 problems in every run:

| Store | Items checked | Eager level-0 SHA-256 |
| --- | --- | --- |
| `ebi_cy5` | 726 + 2 | `f552c99f68f7a722…` |
| `ssbd_rgb` | 642 + 6 | `bfcba7f94af19e51…` |
| `ssbd_smovie` | 833 | `604394a43934b243…` |
| `marmoset_v04` | 750 | `c4949b80253fd9aa…` |
| `marmoset_v05` | 750 | `c4949b80253fd9aa…` |

The items are windows, XYC-plane channels, blocks plus coverage, and histograms. Window digests
are also identical:

- **Across worker counts and caches.** For example, `ebi_cy5` gives `596f1d71fe7c…` for 1 and 4
  workers, with the planned cache and with none.
- **Across zarr formats.** `marmoset_v04` and `marmoset_v05` both give `87a453c53c94…`.

Histograms are identical whether a plane was read whole or in blocks. Previews are bitwise
`full[::step, ::step]`.

## Windows and `map_windows`

The grid uses tiles of `tile` px, grown by `margin` and clipped at the plane edge (the EW5 rule;
the grid is made in the script). The cache is at `cache_plan`'s figure ("plan") or 1 byte
("none"). The budget is `plan.memory_estimate`, so a plan over it would have been refused.

| Store, grid | workers / in flight | cache | plan (MiB) | peak RSS (MiB) | RSS / plan | wall s | fetches (calls / MiB) | decodes / hits |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `ebi_cy5`, 144 × (1024 + 2·64), ch 0 | 1 / 2 | plan 35.2 MiB | 54.5 | 45.0 | 0.83 | 5.81 | 5138 / 206.7 | 2209 / 2552 |
| | 4 / 8 | plan | 104.5 | 69.5 | 0.67 | 9.23 | 5138 / 206.7 | 2209 / 2552 |
| | 1 / 2 | none | 19.2 | 8.7 | 0.45 | 11.22 | 10242 / 456.3 | 4761 / 0 |
| | 4 / 8 | none | 69.3 | 28.4 | 0.41 | 15.18 | 9522 / 421.0 | 4401 / 360 |
| `ssbd_rgb`, 35 × (2048 + 2·128), ch 0,1,2 | 1 / 2 | plan 261.3 MiB | 385.7 | 335.1 | 0.87 | 1.73 | 271 / 39.9 | 48 / 192 |
| | 4 / 8 | plan | 713.2 | 545.5 | 0.76 | 0.84 | 271 / 39.9 | 48 / 192 |
| | 4 / 8, ch 2,0 | none | 406.4 | 242.6 | 0.60 | 1.01 | 409 / 121.4 | 117 / 43 |
| `ssbd_smovie` z 29, 16 × (256 + 2·32), ch 0,1,2 | 1 / 2 | plan 4.1 MiB | 18.0 | 8.6 | 0.48 | 0.10 | 86 / 2.1 | 3 / 45 |
| | 4 / 8 | plan | 57.6 | 11.0 | 0.19 | 0.17 | 86 / 2.1 | 3 / 45 |
| `marmoset_v04` z 157, 16 × (256 + 2·32) | 1 / 2 | plan 24.0 MiB | 27.2 | 26.3 | 0.97 | 1.22 | 320 / 23.1 | 256 / 228 |
| | 4 / 8 | plan | 36.3 | 39.4 | **1.09** | 3.53 | 320 / 23.1 | 256 / 228 |
| `marmoset_v05`, same | 1 / 2 | plan | 27.2 | 25.9 | 0.95 | 1.24 | 320 / 23.1 | 256 / 228 |
| | 4 / 8 | plan | 36.3 | 40.6 | **1.12** | 3.54 | 320 / 23.1 | 256 / 228 |

**With the planned cache, each chunk is decoded once.** For `ebi_cy5`, 2209 decodes = 47² inner
chunks, and 206.7 MiB fetched ≈ the store's 204 MiB. That confirms `cache_plan` on a real
sharded store.

**On a sharded array, every cached chunk read fetches the shard index again.** That is 2 ranged
gets per chunk (4418 for 2209 chunks). On top of that, each window opens the store again: about
5 unranged metadata gets per window (720 for 144 windows).

**The marmoset 4-worker RSS above the plan (1.06 to 1.17, 3 runs each, both formats) is a
cold-process cost.** Two repeats show it:

- After a warm-up map of 8 windows on a separate reader in the same process, the same run peaks
  at 0.80 to 0.93 of the plan.
- The traced peak is 0.77 to 0.79 of the plan.

So the extra RSS is process-wide first use (the map threads and zarr's private read pools,
allocator arenas), which the estimate does not claim to count. It is about 6 MiB, so it shows
on small plans only. Recorded on #342 (item 5, RSS-based checks).

**More workers are slower on these stores.** The cause is split by
`profile_workers.py`. On `marmoset` (1283 files) the reader's fingerprint walk is 84% of the
1-worker time, and the walks contend under threads. On `ebi_cy5` the walks are cheap, but the
per-chunk zarr reads hold the GIL. Filed as #570, with the #378 measurement added there.

## `read_xyc_plane`

| Store | Planes | Estimate (MiB) | Peak RSS, max (MiB) | RSS / estimate, max | Wall s | Fetches per plane |
| --- | --- | --- | --- | --- | --- | --- |
| `ebi_cy5` (limit 2 GiB; at the default 1 GiB it is refused: estimate 1.34 GiB for a 270 MiB plane) | 1 | 1370.4 | 633.8 | 0.46 | 1.14 | 52 / 204.2 MiB |
| `ssbd_rgb` (limit 2 GiB) | 1 | 814.9 | 657.9 | 0.81 | 0.63 | 12 / 39.8 MiB |
| `ssbd_smovie` | all 58 | 11.5 | 10.6 | 0.92 | 1.03 total | 11 / 2.0 MiB |
| `marmoset_v04` / `v05` | z 0, 63, 64, 157, 313 | 196.0 | 8.1 | 0.04 | 0.86 / 0.90 total | 260 / 18.3 MiB |

The bound holds on every store. For 64³ chunks it is very loose (0.04). Each Z plane fetches
and decodes 256 chunks of 64 planes, a 64× read amplification inherent to that chunking.

## Planes, blocks and histograms (`iter_xyc_planes`, `Plane.blocks`, `Plane.histogram`)

| Store | Limit | Read as | Block | Estimate (MiB) | Peak RSS max (MiB) | RSS / estimate max | Total s | Fetches (calls / MiB) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `ebi_cy5` | 64 MiB | blocks | 256 × 5888 | 62.5 | 36.9 | 0.59 | 3.49 | 1504 / 409 |
| | 1 GiB | blocks (whole-plane estimate 4.77 GiB) | 2304 × 11898 | 1007.8 | 363.7 | 0.36 | 3.29 | 254 / 408 |
| | 6 GiB | whole | | 4882.4 | 1432.7 | 0.29 | 2.20 | 47 / 204 |
| `ssbd_rgb` | 64 MiB | blocks | 55 × 13264 | 63.7 | 54.0 | 0.85 | 15.79 | 2016 / 3345 |
| | 1 GiB | blocks (whole estimate 3.87 GiB) | 2295 × 9948 | 771.6 | 263.2 | 0.34 | 3.76 | 96 / 80 |
| | 6 GiB | whole | | 3962.3 | 1092.8 | 0.28 | 3.29 | 7 / 40 |
| `ssbd_smovie` | 1 GiB | whole, all 174 channel-planes | | 29.0 | 9.5 | 0.33 | 1.85 | 348 / 115 |
| | 8 MiB | blocks (4 MiB is refused, correctly: one chunk needs 7.2 MiB) | 29 × 844 | 8.0 | 6.8 | 0.85 | 1.99 | 1080 / 361 |
| `marmoset_v04` | 1 GiB | whole, all 314 planes | | 98.3 | 15.4 | 0.16 | 56.7 | 80384 / 6549 |
| `marmoset_v05` | 1 GiB | whole, all 314 planes | | 98.3 | 15.8 | 0.16 | 58.1 | 80384 / 6549 |
| `marmoset_v04` | 8 MiB | blocks | 64 × 1024 | 7.2 | 8.6 | **1.20** | 1.95 | 2560 / 199 |
| `marmoset_v05` | 8 MiB | blocks | 64 × 1024 | 7.2 | 8.5 | **1.18** | 2.15 | 2560 / 199 |

**Fetch counts.** Each plane is read twice: once by the script's `blocks()` pass and once by
`histogram()`. An 8- or 16-bit histogram takes one pass.

**Blocks thinner than a chunk decode the whole chunk for each block.**

- `ssbd_smovie`, 8 MiB: 29-row blocks of 844-row chunks, so 120 store reads and about 37 MiB per
  channel-plane against 2 MiB stored.
- `ssbd_rgb`, 64 MiB: 55-row blocks, so 3345 MiB read for a 40 MiB store.

This measures #78.

**Marmoset at 8 MiB.** Over three repeats per format, with and without a warm-up read, only the
**first** block-read plane of a process exceeds the estimate: 1.10 to 1.23. The later planes
stay at 0.09 to 0.27, and the traced peak is 0.46 to 0.53. That is the same cold-process cost as
above (recorded on #342).

**Histogram estimates are very conservative.** The measured whole-plane peak is 0.28 to 0.29 of
the estimate. As a result, a 270 MiB `uint16` plane is read in blocks at the default 1 GiB.

## Previews (`read_plane(..., preview=True)`): over the limit (#569)

These are traced runs after a one-pixel warm-up (`preview_trace.py`). The RSS column is from
`preview529.py`, which also checks the values.

| Store | Limit | Read | Estimate (MiB) | Traced peak (MiB) | Traced / limit | RSS / limit | Values |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `ebi_cy5` | 64 MiB | blocks 768 × 11898, step 13 | 52.5 | 78.3 | **1.22** | 1.48 to 1.65 | identical |
| | 256 MiB | blocks 3584 × 11898, step 7 | 244.9 | 301.2 | **1.18** | 1.29 | identical |
| `ssbd_rgb` | 64 MiB | blocks 2295 × 6632, step 12 | 43.5 | 60.6 | 0.95 | 0.99 to 1.15 | identical |
| | 256 MiB | whole, step 1 | 232.2 | 312.3 | **1.22** | 1.10 to 1.24 | identical |
| `ssbd_smovie` | 4 MiB | whole | 2.72 | 4.75 | **1.19** | 1.14 | |

The OME-Zarr whole-plane and block estimates (`_zarr_layout`, and the zarr branch of
`_block_memory`) count each chunk **once**. The window estimate counts three copies, and on the
same stores it held. So #529's item "Previews and thumbnails stay within the read budget" is
**not met for OME-Zarr**. Filed as #569.

## Not covered by this record

- **Level reads.** Windows read level 0 only (`WindowLevelError` otherwise, by design until the
  EW2 amendment), and only level 0 was downloaded.
- **`RunInputs`.** The `RunInputs.window_reader` route on a run folder was not tested. It wraps
  the same `WindowReader`; #378 notes its extra fingerprint walks.
- **Discovery over a folder of stores.**
- **Reordered axes** (#388, #452) and **NGFF label images.** None of these stores has labels or
  reordered axes.
- **Remote or slow stores, and transient errors.** The reader takes local paths only.
- **zarr-python 2.18** (only zarr 3.4.2 was used) and the codec sensitivity matrix.
- **Repeated timings.** Wall times are single runs on a shared 4-CPU machine and are indicative
  only.

## Defects and issues

- **#569 (new).** OME-Zarr whole-plane, preview and block reads exceed `memory_limit` on real
  data: traced up to 1.22 of the limit, RSS up to 1.65.
- **#570 (new).** `map_windows` over real OME-Zarr stores is slower with more workers:
  fingerprint walks (#378) and per-chunk zarr reads.
- **Evidence added to existing issues:**
  - #78: sub-chunk blocks decode whole chunks again.
  - #342: cold-process RSS above the plan for small OME-Zarr plans; traced peaks within it.
  - #378: the fingerprint walk measured on a 1283-file store.
