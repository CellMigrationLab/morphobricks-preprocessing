# Lazy windows of large images: one access layer for every consumer

Status: **design note** (17 November 2026), with its first implementation step in the same pull
request (deterministic close and a window plan on the reader). It records where lazy, windowed
reading of a large tiled image lives, the API every consumer uses, what exists at v0.7.0 and what
is still missing. Nothing here is a Spec decision; the window geometry and coordinate types are
MorphoBricks-Spec ADR 0004's (EW1, EW2, EW4, EW5) and are reused, not redefined.

## 1. Principle

Reading belongs to this brick. Lazy access to a large image (a whole-slide RGB TIFF, a tiled
OME-TIFF, an OME-Zarr) is one **processing-agnostic** layer here: it reads rectangles of stored
pixels and knows nothing of features, patches, providers, scaling or results. Every consumer
(MorphoFeature's windowed units, CellPatcher, a brick run directly, a LabConstrictor tool behind
QuPath) uses the same layer, so a window is the same bytes whoever asks, and no consumer keeps
its own cache of decoded pixels.

Three concerns stay apart:

| Concern | Owner | What |
| --- | --- | --- |
| Reading (this note) | preprocessing | open an image once, read any window of any channels, a bounded cache of decoded pieces, memory plan, close |
| Window geometry | CellPatcher (ADR 0004 EW5) | tiles of a plane (grid patch sets from the plane's shape), a tile grown by a margin and clipped at the plane edge (`context_bounds`, the window record EW4) |
| What is computed on a window, and the results | each consumer | features, patches, partial results, their table in the file frame (ADR 0004 EW1 conversions) |

## 2. The access API

All coordinates are in the **file frame**: the plane of the stored image at full resolution
(level 0), `pixel_edge`, half-open `(y0, y1, x0, x1)`, Y before X whatever the stored order;
ADR 0004 EW2's frame with `axes_offset = (0, 0)` and `downsample = 1`. A window is never padded
or clipped: bounds outside the plane are refused.

| Need | API (v0.7.0 unless marked) | Notes |
| --- | --- | --- |
| Open once per image, from a run | `RunInputs.window_reader(fov, *, cache_bytes, memory_limit)` -> `RunWindowReader` | the image's recorded axes, channels and fingerprint; nothing read but the fingerprint |
| Open once per image, from a file path | `window_reader.WindowReader(path, mapping, *, cache_bytes, memory_limit, fingerprint=None)` | the axis mapping is given (never guessed); channels by C index |
| Plane size | `RunInputs.plane_shape(fov)`; a `WindowReader`'s `window_plan(...).plane_shape` | header only |
| Read a window, any channel subset | `read_window(*, t, z, bounds, channels, level=0)` | channels by canonical ID (run) or C index (file), in the order asked; the stored dtype; bitwise the slice of `read_plane` |
| Plan memory before reading | `WindowReader.window_plan(...)`; **new:** `RunWindowReader.window_plan(*, t, z, bounds, channels, level=0)` | `shape`, `dtype`, `pieces`, `memory_estimate` with the cache counted; header only |
| Size the cache for a grid | **new:** `window_reader.cache_plan(path, mapping, *, window, margin, channels)`, `RunInputs.window_cache_plan(fov, *, window, margin, channels)` | the bytes a row-by-row grid with margins needs to decode each piece once (below), for the consumer to cap with its budget; header only |
| Threads | one reader per image, shared by every thread | the cache has one lock, and a piece asked by several threads at once is decoded once (`block_cache`); a read opens its own file handle, so no handle is shared; no per-thread reader is needed |
| Cache | `cache_bytes`, least recently used dropped first | decoded compressed TIFF strips or tiles and OME-Zarr chunks; a piece larger than the bound is used once, not kept; uncompressed pieces are read from the file, never cached; keyed by the image's path, recorded fingerprint, level and page and piece or chunk; `stats()` gives decodes, hits, evictions and bytes |
| Identity | the run's recorded fingerprint, checked before and after each window | an image changed or gone is refused (`RunInputsError`, `WindowSourceChangedError`) and the cache emptied, never served |
| Close | **new:** `close()`, `closed`, and `with reader:` | drops the cache at once, also when the `with` block fails; a later window or plan is refused (`WindowReaderClosedError`); a window being read meanwhile finishes, bitwise, and keeps nothing; the reader holds no open file between windows |
| Masks | `RunInputs.read_mask_window`, `mask_window_plan` | the stored labels of a window; **missing:** no cached mask reader yet |
| Levels | level 0 only (`WindowLevelError` otherwise) | pyramid levels and series need ADR 0004's announced EW2 amendment first |

**Memory for planning.** `window_plan` (and a reader's, which adds its `cache_bytes`) is an
**upper bound** of a window read's peak working memory, taken from the header before any pixel
is read, and a read over its limit is refused before any pixel is read
(`window_memory`; review of this PR, finding 1). It counts the result, the pieces read, the
decode's buffers (five piece-sized buffers for a compressed TIFF piece, three for an OME-Zarr
chunk), the codec's state (deflate 64 KiB; LZMA 66 MiB, for presets up to 9), the parsed header
(256 KiB, 2 KiB a page, 80 B a piece) and, through a cache, the frozen copy of the piece being
kept; the cache never holds more than its bound, not even while a piece is added. The bound is
**checked, not proven**: `tests/test_window_memory.py` measures the peak with `tracemalloc` and
asserts it is never over the estimate, for TIFF `uint8`, `uint16` and `float32`, uncompressed,
deflate, deflate with a predictor and LZMA, in 64- and 128-px tiles and 32-row strips, interleaved
RGB and a multi-page OME stack, and for OME-Zarr 0.4 and 0.5 with chunks across 1 to 3
channels, every one uncached and through a full cache of two sizes, for windows from 64 px to the
whole plane (worst measured peak: 0.98 of the estimate). **Not covered** (#342): codecs only
imagecodecs decodes (JPEG, LZW, Zstandard, WebP, JPEG 2000), estimated with deflate's state and
not verified; memory a C library allocates outside Python's allocators; the plane block reader
(`read_plane`, `planes._tiff_piece_memory`) and `objects_read_plan`, unchanged here. (The run's
own worker plan, `plan_workers` and `max_ram`, is being fixed separately and is not part of this
note.) A consumer that reads `n` windows at once from one reader holds at most
`n` window working memories plus one cache: `sum(plan.memory_estimate - cache_bytes) +
cache_bytes`. Each `plan.memory_estimate` already counts the cache, and a window over the
reader's `memory_limit` is refused before any pixel is read. A consumer that wants a window
refused exactly when `RunInputs.read_window` refuses it opens the reader with `memory_limit =
DEFAULT_MEMORY_LIMIT + cache_bytes` (MorphoFeature does).

**Cache size** (review of this PR, finding 2; the "about two rows" of the first version was
wrong). A window and its margins span `ceil((window_h + 2 × margin) / tile_h) + 1` rows of pieces
(one more when it is not aligned on them: a 512-px window with 67-px margins touches the rows
r − 1, r and r + 1), and the next row of windows reuses them, so a grid read row by row decodes
each piece once only when the cache holds that many rows of pieces **across the whole plane**,
for every group of channels stored apart. Below it a least-recently-used cache does not degrade
gently: it drops the pieces the next row needs and decodes most of them again (a cliff).
`window_reader.cache_plan(path, mapping, *, window, margin, channels)` and
`RunInputs.window_cache_plan(fov, *, window, margin, channels)` compute that figure from the
header (`CachePlan`: `cache_bytes`, `piece_rows`, `per_row`, `piece_nbytes`, `groups`; 0 for
uncompressed TIFF pieces, which are never cached). It is a stated figure, not a setting: the
consumer caps it with its own explicit memory budget and records both, so a cap below it is a
known, recorded re-decode rather than a silent one. On the synthetic 8192² RGB image in
512-px zlib tiles (256 windows of 512 px, 67-px margins, one thread) the plan is 3 rows × 16
tiles × 768 KiB = **36 MiB**, and the measured decodes are: 24 MiB 728, 30 MiB 700, 35 MiB 624,
**36 MiB 256** (one per tile), 128 MiB 256; with 4 threads, 36, 37 and 38 MiB each gave 256 (one
run each). The tests check the threshold on three grids: with the plan every piece is decoded
once, with one row of pieces less many are decoded again.

## 3. Iteration: tiles and halos are CellPatcher's geometry

The layer takes bounds; it does not tile. A consumer that iterates over tiles of `tile` pixels
with a halo of `margin` pixels uses CellPatcher's pure geometry, which already works from the
plane's shape alone:

```python
from cellpatcher import context_bounds
from cellpatcher.config import GridConfig
from cellpatcher.strategies.grid import GridStrategy

height, width = inputs.plane_shape(fov)
tiles = GridStrategy(GridConfig(tile_size=tile, include_partial=True)).generate_regions(
    image_shape=(height, width), axes="YX"
)
needed = inputs.window_cache_plan(fov, window=(tile, tile), margin=(margin, margin), channels=ids)
cache_bytes = max(1, min(needed.cache_bytes, budget))  # the consumer's own budget caps it
with inputs.window_reader(fov, cache_bytes=cache_bytes) as reader:
    for tile_region in tiles:  # or from a thread pool sharing the reader
        bounds = (tile_region.y0, tile_region.y1, tile_region.x0, tile_region.x1)
        record = context_bounds(bounds, (margin, margin), (height, width))
        window = reader.read_window(t=0, z=0, bounds=record.context_bounds, channels=ids)
        # compute on window; the tile's interior in it is record.local_interior (EW4)
```

A helper here that grows bounds by a margin would be a second definition of EW5; there is none.

## 4. Consumers

| Consumer | How it uses the layer | Exists | Missing |
| --- | --- | --- | --- |
| MorphoFeature windowed units (`window_reads`) | one `RunWindowReader` per plane, shared by the window thread pool; CellPatcher cuts the grid and gives `context_bounds`; results keep the patch's global `bounds` (EW4) | `read_window` per window (v0.5.0); one reader per plane in MorphoFeature's consumer PR | `close()` once MorphoFeature pins a preprocessing release with it (today it drops its last reference after the plane) |
| CellPatcher patch extraction from a large image | open a reader per image, read each patch's window (or its `context_bounds`) instead of `read_plane`; `WindowedMask` already counts mask fractions by window | `read_window`, `read_mask_window`, `WindowedMask` | a windowed extraction route in CellPatcher (it reads whole planes today) and a cached mask reader here |
| A brick run directly (fiberfeatures, MorphoRelate, MorphoObjects, MorphoEmbed) | the same loop as section 3 with the brick's own margin (its filter footprint, ADR 0004 EW6); objects that cross tiles are the brick's concern (ADR 0004: object tiling not decided) | everything in section 2 | per-brick entry points; object tiling (Spec) |
| LabConstrictor tool behind QuPath | file path in (`WindowReader(path, mapping)` with the mapping the tool states, or a run), tiles out (section 3); each tile's results written as soon as it is done (partial results), coordinates converted to the file frame with ADR 0004 EW1's table (`pixel_edge` + origin; `full_res` for what QuPath draws) | the reader, geometry and conversion table | the bridge tool, its per-tile result table and resume (LabConstrictor-QuPath #50/#51; MorphoFeature roadmap F4) |

## 5. Concurrency

The owner asked whether lazy windows can be read by several threads, and how a consumer is given
that parallelism. The answers below come from the code at v0.7.0 (tifffile 2026.3.3, zarr 3.1,
numcodecs 0.16).

### 5.1 Decoding and the cache

- **TIFF.** A compressed strip or tile is read under the file handle's lock
  (`tiff_pieces._decode_segment`: seek and read only), and then decoded outside it by tifffile's
  codec. Without imagecodecs (not a dependency), zlib/deflate falls back to the standard
  library's `zlib.decompress`, which releases the GIL while it inflates. With imagecodecs, its
  codecs, JPEG for whole-slide images among them, also decode without the GIL. Every window
  opens its own file handle (`windows._source`), so concurrent windows never share one.
- **OME-Zarr.** zarr 3 decodes chunks through its codec pipeline. numcodecs' compressors (Blosc,
  Zstd, LZ4) release the GIL, and zlib/gzip use the standard library. zarr runs its own
  event-loop thread and has an `async.concurrency` setting, which is zarr's own and is never
  changed here. This was not measured for this note.
- **Cache: safe under threads, single-flight.** One lock guards the pieces. The first thread to
  ask for a missing piece decodes it outside the lock, and threads that ask for the same piece
  meanwhile wait for that decode. Different pieces decode in parallel. The cache is bounded in
  bytes and drops the least recently used piece first.
- **Single-flight also covers pieces the cache does not keep** (review finding F4, fixed in this
  PR). At v0.7.0 a waiting thread looked in the cache again after the decode. A piece larger
  than the bound, or one dropped at once (by eviction or `close`), was not there, so the waiting
  thread decoded it again. Now each in-flight decode hands its piece to every thread that waited
  for it, whether or not the piece is kept. Requests that overlap in time therefore decode a
  piece once. A request that starts after the decode has finished decodes again when the piece
  is not held: that is the bound working, not a duplicate. A failed decode is not shared: one
  waiting thread decodes again and gets its own result or error. Tests cover an oversized piece,
  a piece dropped by `close`, and a failed decode, with every request waiting on the flight
  before it finishes.
- **Read only, enforced** (F5). At v0.7.0 a kept piece was only marked `write=False`, and NumPy
  lets an array that owns its memory be made writable again. A kept piece is now copied once into
  an immutable `bytes` buffer, outside the cache's lock (review of this PR, finding 4: under the
  lock the copy held every other thread back). Neither the piece nor its storage can be made writable
  (`setflags(write=True)` raises), and the decoder's own array is not the cached one. Windows are
  new arrays, so changing a window changes no cached piece. Tests cover all three points. The
  cost is one copy per kept piece, inside the cache's budget: the measured grid below went from
  1.90/1.99 s to 2.09/2.11 s with 1 thread and from 1.52/1.66 s to 1.96/1.77 s with 4 threads, on
  a shared machine, with identical window digests. A piece that is not kept (too large, or
  dropped by a close) but handed to the threads that waited for it is frozen the same way.
- **Per process** (F6). A cache belongs to the reader, and the reader belongs to the process that
  opened it (`WindowReader.cache`). Nothing is shared between processes or between bricks: a
  worker process, or another brick in the same process, opens its own reader and pays its own
  `cache_bytes`. Every window estimate of a reader counts that reader's `cache_bytes`, so a
  process holding `r` readers at once plans for `r` caches. Sharing a cache across processes or
  bricks would need an owner, a lifetime and a memory account first, and none is proposed here.
- **Measured** (`tools/benchmark_window_reads.py`, the synthetic 8192² RGB image, 512-px zlib
  tiles, 256 windows of 512 px with a 67-px margin; on a 4-core machine shared with other jobs,
  so timings are indicative):

  | threads | no cache: decodes / wall s | cache 128 MiB: decodes / wall s | windows SHA-256 |
  | --- | --- | --- | --- |
  | 1 | 2116 / 11.9, 11.4 | 256 / 1.90, 1.99 | `7e492fcd1034284e…` |
  | 2 | 2116 / 8.20, 8.01 | 256 / 1.78, 1.97 | `7e492fcd1034284e…` |
  | 4 | 2116 / 7.44, 7.15 | 256 / 1.52, 1.66 | `7e492fcd1034284e…` |

  For decoding alone (a one-off measurement, not a tool), the 256 tiles (152 MiB compressed) passed through `zlib.decompress`
  in a thread pool reach 154, 254 and 282 MiB/s with 1, 2 and 4 threads, with the same bytes
  out. Decoding does run in parallel. With the cache, each tile is decoded only once, so the rest
  of a window read (Python bookkeeping and copies into the result) takes a larger share of the
  time, and threads gain less. The cache is the larger gain, and threads add to it.

### 5.2 How a consumer is given parallelism

These options are processing-agnostic. Each takes window bounds from CellPatcher's geometry
(section 3) and an opaque consumer function. None of them knows what the function computes.

| Option | What it is | Fits | Where |
| --- | --- | --- | --- |
| (a) ordered iterator with prefetch | `iter_windows(reader, bounds, *, t, z, channels, prefetch=k)` yields `(bounds, window)` in the given order while up to `k` later windows are read in background threads | a single-threaded consumer; decoding overlaps its compute | preprocessing (proposed) |
| (b) `map_windows` | `map_windows(reader, bounds, fn, *, t, z, channels, workers=n)` calls `fn(bounds, window)` in `n` threads and yields the results **in window order**, whatever finishes first | a consumer whose compute releases the GIL (NumPy, SciPy, torch, numba `nogil`); MorphoFeature's window pool is this pattern | preprocessing (proposed); (a) is (b) with `fn` returning the window |
| (c) process workers | each process opens its **own** reader on the image and is assigned windows by index (for example, contiguous rows of the grid, so its cache still sees neighbours); caches are not shared between processes, and results are merged by window index | GIL-bound consumers | the consumer (MorphoFeature's worker processes already open a run once per process, `fov_files.RunHandle`); preprocessing adds nothing but the reader |
| (d) streaming partial results | (b) whose results the consumer writes per tile as they arrive in order, each row in the file frame (EW1's conversions from the window `origin`) with the tile's bounds, so a stopped run keeps the finished tiles | the LabConstrictor/QuPath bridge, MorphoFeature F4 | the bridge or consumer; its result-table contract needs Spec text first |

### 5.3 Rules for every option

- **Order.** Results are delivered in the order of the bounds given, never in completion order,
  so outputs are identical for any number of workers. A test compares 1 and n workers bitwise.
- **Memory.** At most `workers + prefetch` windows are in flight. The peak is about
  `in_flight × (plan.memory_estimate - cache_bytes) + cache_bytes`, plus the consumer's own
  results. `window_plan` gives each term before anything is read, and that figure goes into the
  consumer's existing memory plan (MorphoFeature plans no RAM for windows yet; preprocessing's
  `max_ram` estimate is the model). A window over the reader's limit is refused before any pixel
  is read.
- **Close.** Leaving the iteration early (a `break`, an error in `fn`, the consumer's
  cancellation or `generator.close()`) cancels the windows not started, waits for the running
  ones, re-raises the first error in window order, and leaves the reader as the caller owns it.
  Closing the reader is the caller's job (`with reader:`). A window read after the reader is
  closed is refused.
- **Workers are recorded but are not part of identity.** The consumer records the worker count
  and prefetch with its run (MorphoFeature's execution record already does, ADR 0004 EW9). They
  never enter a cache key or a result's identity.
- **No implicit fallback.** An invalid worker count is refused. Threads are never switched to
  processes, a failed window is never re-read serially, and the number of threads is never
  lowered on its own. A codec that needs the GIL is simply slower, never replaced.

### 5.4 Recommendation

Implement **(b) `map_windows` first**, in preprocessing, with (a) as its special case: one small
pure-orchestration function over a `RunWindowReader` or `WindowReader`, with an ordered,
bounded, cancel-safe thread pool and a test that 1, 2 and 4 workers give the same bytes. It
serves MorphoFeature's window pool (which then needs no pool of its own for reading),
CellPatcher's windowed extraction, a brick run directly, and the bridge's per-tile loop (d).
(c) needs nothing from preprocessing beyond opening one reader per process. (d) waits for its
Spec result contract.

## 6. Spec

Nothing new is needed for this step: EW1 (coordinate types and conversions), EW2 (the frame:
level 0, offset 0, downsample 1), EW4 (window record) and EW5 (`context_bounds` is CellPatcher's)
cover it. Later steps need Spec text first: pyramid levels and series (the announced EW2
amendment), object tiling, and a result-table contract for partial results tile by tile.

## 7. Steps

1. **This pull request (preprocessing):** this note; `close()`, `closed` and the context manager
   on `WindowReader` and `RunWindowReader` (`BlockCache.close`); `RunWindowReader.window_plan`;
   the review's fixes: single-flight for unkept pieces, immutable pieces frozen outside the lock,
   window estimates that bound the measured peak, and `cache_plan` / `window_cache_plan`. Every
   window is unchanged, bitwise.
2. **MorphoFeature (separate pull request):** a thin consumer: one reader per plane shared by the
   window threads, released after the plane's units, also on error; no window or cache logic of
   its own. It moves to `close()` when it pins a release with it.
3. Next here: `map_windows` / `iter_windows` (section 5.4).
4. Later, each in its owner: a cached mask reader (here); CellPatcher's windowed extraction; the
   LabConstrictor/QuPath tool and its per-tile results; levels after the Spec amendment.
