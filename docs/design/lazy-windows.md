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
| Threads | one reader per image, shared by every thread | the cache has one lock, and a piece asked by several threads at once is decoded once (`block_cache`); a read opens its own file handle, so no handle is shared; no per-thread reader is needed |
| Cache | `cache_bytes`, least recently used dropped first | decoded compressed TIFF strips or tiles and OME-Zarr chunks; a piece larger than the bound is used once, not kept; uncompressed pieces are read from the file, never cached; keyed by the image's path, recorded fingerprint, level and page and piece or chunk; `stats()` gives decodes, hits, evictions and bytes |
| Identity | the run's recorded fingerprint, checked before and after each window | an image changed or gone is refused (`RunInputsError`, `WindowSourceChangedError`) and the cache emptied, never served |
| Close | **new:** `close()`, `closed`, and `with reader:` | drops the cache at once, also when the `with` block fails; a later window or plan is refused (`WindowReaderClosedError`); a window being read meanwhile finishes, bitwise, and keeps nothing; the reader holds no open file between windows |
| Masks | `RunInputs.read_mask_window`, `mask_window_plan` | the stored labels of a window; **missing:** no cached mask reader yet |
| Levels | level 0 only (`WindowLevelError` otherwise) | pyramid levels and series need ADR 0004's announced EW2 amendment first |

**Memory for planning.** A consumer that reads `n` windows at once from one reader holds at most
`n` window working memories plus one cache: `sum(plan.memory_estimate - cache_bytes) +
cache_bytes`. Each `plan.memory_estimate` already counts the cache, and a window over the
reader's `memory_limit` is refused before any pixel is read. A consumer that wants a window
refused exactly when `RunInputs.read_window` refuses it opens the reader with `memory_limit =
DEFAULT_MEMORY_LIMIT + cache_bytes` (MorphoFeature does).

**Cache size.** About two rows of the image's pieces decode each piece once for a grid read row
by row with margins (#306: 24 MiB for an 8192-pixel-wide RGB `uint8` image in 512-pixel tiles).
A smaller cache decodes some pieces again and never reads another value.

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
with inputs.window_reader(fov, cache_bytes=128 * 2**20) as reader:
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

## 5. Spec

Nothing new is needed for this step: EW1 (coordinate types and conversions), EW2 (the frame:
level 0, offset 0, downsample 1), EW4 (window record) and EW5 (`context_bounds` is CellPatcher's)
cover it. Later steps need Spec text first: pyramid levels and series (the announced EW2
amendment), object tiling, and a result-table contract for partial results tile by tile.

## 6. Steps

1. **This pull request (preprocessing):** this note; `close()`, `closed` and the context manager
   on `WindowReader` and `RunWindowReader` (`BlockCache.close`); `RunWindowReader.window_plan`.
   Additive: every window is unchanged.
2. **MorphoFeature (separate pull request):** a thin consumer: one reader per plane shared by the
   window threads, released after the plane's units, also on error; no window or cache logic of
   its own. It moves to `close()` when it pins a release with it.
3. Later, each in its owner: a cached mask reader (here); CellPatcher's windowed extraction; the
   LabConstrictor/QuPath tool and its per-tile results; levels after the Spec amendment.
