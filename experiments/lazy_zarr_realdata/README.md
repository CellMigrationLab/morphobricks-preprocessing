# Lazy OME-Zarr on real data (#529)

The scripts behind [`docs/archive/2026-11-26-lazy-zarr-realdata.md`](../../docs/archive/2026-11-26-lazy-zarr-realdata.md).
They measure only and never change `src`. Downloaded stores are untrusted: keep them in their
own directory and run every script with `python -I`.

- `fetch_level0.py BASE_URL DEST LEVEL0_PATH`: download the root metadata and the level-0 array
  of a public store, with chunk keys derived from the array metadata. Writes a manifest of
  SHA-256 per file.
- `lazy529.py CMD JSON OUT`: `windows`, `xyc`, `planes` (lazy, with RSS, fetches and time) and
  `verify` (eager zarr read, every digest and histogram checked).
- `preview529.py`, `preview_trace.py`: `read_plane(preview=True)` against the memory limit.
- `profile_workers.py`: `map_windows` wall time for 1, 2 and 4 workers, with the fingerprint
  checks timed.
- `run_ebi.sh`, `run_rest.sh`: the runs as made. Set `ZARR529_DATA` (directory holding the stores
  at `ebi_cy5/cy5.ome.zarr`, `ssbd_rgb/brainslice.ome.zarr`, `ssbd_smovie/smovie.ome.zarr`,
  `marmoset_v04/` and `marmoset_v05/marmoset_neurons.ome.zarr`) and `ZARR529_OUT` (where the JSON
  records are written); both are required and the scripts stop with a message if either is
  missing. `ZARR529_PYTHON` optionally names the interpreter (default `python3`; it needs the
  repository's dependencies). Example:
  `ZARR529_DATA=/path/to/data ZARR529_OUT=/path/to/out bash run_ebi.sh`.
