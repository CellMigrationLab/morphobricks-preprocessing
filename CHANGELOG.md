# Changelog

All notable changes to `morphobricks-preprocessing`. Numbers are pull requests (PR) and
issues of [CellMigrationLab/morphobricks-preprocessing](https://github.com/CellMigrationLab/morphobricks-preprocessing).
The design is `docs/restart/preprocessing-design-v0.4.md` in MorphoFeature. The decisions
are in [docs/plan.md](docs/plan.md) §12. Upgrading from v0.3: see the
[migration guide](docs/migration-0.3-to-0.4.md).

## 0.4.0 (unreleased; release candidates rc1 to rc4)

Preprocessing becomes the one step that discovers, validates, records and serves an
experiment. Every later brick reads the run through `RunInputs`. Nothing is guessed: axes,
calibration, masks and design come from the headers or from the user's recorded answers.

Release candidates: **v0.4.0rc1** (after PP-6, #112), **v0.4.0rc2** (after PP-8d, #118),
**v0.4.0rc3** (#125, after PP-10d) and **v0.4.0rc4** (PP-13, for the round-2 data-flow audit
and the bricks' migration).

### Breaking

- **Manifest version 2** (PP-4, #110). It records per file `roles`, `axes_from` and
  `pixel_sizes` by role with unit and source, and per mask `size`, `mtime_ns`, `roles`,
  `axes_from` and `shared`. A v1 manifest gives no reusable row and no recorded input:
  **every v0.3 run must be run again.** A row is reused only when its `RowKey` matches.
- **The axis check** (PP-1, #64). Roles that swap the header's Y and X, contradict the
  header, or put X before Y in a plain stack are errors (`axes_yx_swapped`,
  `axes_contradict_header`, `axes_x_before_y`). Nothing is read transposed. A plain stack's
  confirmation covers only the files it was given for (`plain_stack_not_confirmed`, PP-4).
- **Modes replace `force_rebuild`** (PP-7, #113): `mode="restart"` (default) and
  `mode="full"`, CLI `--mode`. A later run reuses every recorded input. A value given that
  differs is reported with its effect. `--no-masks` and `--no-exclude` drop the recorded
  masks and exclusions.
- **Lock** (PP-7, #113): an `O_EXCL` lock file in the output folder. A second run is refused
  with `output_locked`. `--remove-stale-lock` / `remove_stale_lock()` removes a stale lock
  only on request.
- **`image_named_as_mask` is an error** (PP-5, #111). Masks are checked as stored and never
  converted. OME-Zarr `labels/` are always used. Plain-stack masks need `--mask-axes` per
  type. A 2-D mask of a multi-plane image needs `--mask-axes TYPE=YX` (shared). A mask whose
  header names X before Y is refused (`mask_layout`, PP-8b, #115).
- **Bricks read runs through `RunInputs`** (PP-6, #112), which refuses v1 manifests,
  changed files and checksums, and files the run did not record (`UnrecordedFilesError`,
  PP-8b, #115).
- **OME-Zarr**: OME-NGFF 0.1 and 0.2 stores, and stores without `axes`, are
  `unreadable_ome_zarr`. Every OME-Zarr image is matched by its declared roles
  (`ome_zarr_axes_mismatch`) (#129; issues #72, #73). An OME-Zarr image's fingerprint
  includes its file inventory: images recorded by v0.4.0rc3 or earlier count as changed, and
  `RunInputs` refuses such runs (PP-12, #132; issue #86).
- **Links** in the experiment and masks folders are followed only inside them
  (`link_outside_folder`, `linked_twice`, `link_loop`) (#128; issue #81).
- **Exit status**: every command exits with 1 when it prints an error. `check` and `design`
  exited with 0 on some errors (#133; issue #93).
- **Experiment design**: every design reader refuses a header with a duplicate or unnamed
  column. `RunInputs.design()` uses the strict reader (#130; issue #83). `load_design`
  returns `(design, changes)` and raises `DesignCheckError` for an unreadable file (#62).
- **Conversion records** are version 2, with each copy's SHA-256. Version 1 records
  (v0.4.0rc3 or earlier) are refused (PP-11c, #126; issue #87).
- `copy_changed_after_conversion` is an error that stops the run (PP-12, #132).
- `--memory-limit` must be a whole number of bytes above 0 (PP-11c, #126; issue #80).

### Added

- `RunInputs.open(output, experiment_root=None, masks_root=None)`, with `fovs()`,
  `pixel_sizes()`, `read_plane()`, `read_image()` (lazy), `mask_types()`, `read_mask()`
  (labels checked), `histograms()`, `design()` and `image_type()`. It is picklable.
  `testing.make_clean_run` writes a clean run for the bricks' tests (PP-6, #112).
  `unrecorded_files()` (PP-8b, #115).
- `discovery_report.json` and `.txt` on every run, also when it stops, with what was
  reused, recomputed, resumed and replaced (PP-7, #113).
- Calibration: `calibration.csv` in the output folder (a size or `pixels` per experiment,
  folder or image), recorded per axis with its source. CLI `calibrate`, `write_calibration`,
  `read_calibration`, `review_calibration` (PP-8, #114). No Z or T size is recorded for an
  image without that axis (`calibration_axis_absent`, PP-8c, #117).
- CLI `design` (`--repeat-group`, `--control`, `--no-control`, `--confirm`,
  `--confirm-all`) and `edit_design` / `DesignEdit`. `check` writes a prefilled design
  template (PP-8c, #117).
- Named-axis copies: CLI `convert EXPERIMENT --to FOLDER --axes ROLES` and
  `convert_experiment`. Images with axis errors are written as OME-TIFF with the confirmed
  roles; other images are copied byte for byte; the conversion record
  `.morphobricks-conversion.json` is written last (PP-11, #124). Copy SHA-256 and a single
  metadata epoch (PP-11c, #126; issues #87, #88, #92, #96). `preview_conversion` (PP-11b,
  #127).
- Staging and resume: each image's rows are staged in `.staging` and reused by the next run
  when their key and fingerprint still match. Crash-consistent publication through
  `.publish`, committed by the manifest's `os.replace` (PP-12, #132). Images that change
  during the run are `source_changed_during_run` (issue #90).
- Notebook: Google Colab and JupyterLab support, pinned to `RELEASE_TAG` (PP-8d, #118). The
  **Pixel sizes** and **Experiment design** cards, the **Mode** choice, the report, and
  **Remove the lock of a stopped run** (PP-10a, #120). The **Masks folder** field with mask
  axes per type (PP-10c, #122). **Describe the masks** (PP-10d, #123). **Make named-axis
  copies** (PP-11b, #127).
- `_cp_masks` and `_masks` mask suffixes (PP-5, #111).
- Code standards (design v0.4 §9) enforced by `tools/check.py --rules` with a shrink-only
  baseline (PP-0, #65). The pinned embedding API for MorphoRelate (PP-9, #119).
- Docs: the [migration guide](docs/migration-0.3-to-0.4.md), this changelog, and the
  [Colab hand check](docs/colab-release-check.md) for the major release (PP-13). The roadmap
  (#116).
- Warnings `partial_pixel_size` (a pixel size along only one of X and Y) and
  `non_square_pixels` (X and Y not exactly equal in micrometres): bricks that measure
  lengths refuse such images (audit m5, plan §12 question 70).

### Fixed

- Preprocessing audit of 3 November 2026 (plan §12 question 70): `read_plane` and
  `RunInputs.read_plane` no longer count histogram memory, so a 5000² float32 plane is read
  within the 1 GiB limit (I2); a header size in an unsupported unit (OME `pixel`) or without
  a unit is unknown in `pixel_sizes`, never a value without a unit (I3); `RunInputs.read_mask`
  and `read_plane` refuse a `z` or `t` outside the image with `IndexError`, a shared 2-D
  mask included (I5); `to_micrometers` accepts NumPy real scalars (m4); OME-XML in a TIFF
  that cannot be parsed is `invalid_physical_calibration`, no longer read as uncalibrated,
  and other errors reading TIFF sizes are no longer hidden (m6).
- Output files get the permissions of a plain `open()` (your umask) instead of `0600`
  (PP-8c, #117).
- A stopped run says what it wrote (PP-8c, #117).
- `read_plane` and `read_mask` raise `TypeError` for a `z` or `t` that is not an integer
  (PP-8c, #117).
- The `masks` command uses the recorded inputs (PP-8c, #117).
- `--no-masks` after a run with stated mask axes was refused (PP-10c, #122).
- Malformed OME-Zarr calibrations, and header sizes that overflow or are not positive
  finite, give unknown sizes with `invalid_physical_calibration` (#129, #133; issues #94,
  #93). Every conversion to micrometres is positive and finite (`to_micrometers`).
- Technical-repeat folders whose images are all excluded are not counted (#130; issue #82).
- Changing the notebook's output folder reloads that folder's design (#130; issue #98).
- `--log` is checked before it is opened, and refused inside the inputs or on a protected
  output (#128; issue #99).
- `same_objects` is a one-to-one maximum matching; `mask_relationships.json` is report
  version 2 (#131; issue #67).
- `different_sizes` and `different_plane_counts` follow the confirmed roles (#131; issue
  #74).
- The conversion checks memory and free space before writing and refuses 64-bit integer
  images (PP-11, #124; issues #75, #76). The run reads the copies themselves (issue #79).
- Masks one pixel high or wide are read as 2-D `YX` masks (#63).
- Step 1 keeps an unreadable `experiment_design.csv` untouched (#61).

### Removed

- `convert_tiffs` and `zarr_export` (OME-Zarr copies in `<output>/ome_zarr`):
  `ConversionSettings`, `ConversionEstimate`, `estimate_conversion`, `OmeZarrWriter`,
  `ome_zarr_relative_path`. Also the CLI `--convert-tiff`, `--chunk-size`, `--compression`,
  `--compression-level` and `--pyramid-levels`, and the notebook's conversion card (PP-2,
  #66).
- The deprecated stand-ins of the code moved to `morphobricks_normalization` (`_moved`,
  `csv_io`, `dtypes`, `histogram`, `normalization`, `pooling`, `records` and their
  top-level names) (PP-2, #66).
- `check_declared_axes` and `declared_axes_transposed` (PP-2, #66).
- `find_masks(ome_zarr_labels=)` (PP-5, #111).
- `run_experiment(force_rebuild=)` and `--force-rebuild` (PP-7, #113).
- `notebook_options.PYRAMID_OPTIONS` (PP-10b, #121).
- The backup-and-restore publication protocol (PP-12, #132).
- Compatibility of `PhysicalSizes` pickles across versions is not guaranteed (owner
  decision, design v0.4 change log).

## 0.3.4 and earlier

See the release notes of the tags and [docs/plan.md](docs/plan.md) §12, questions 33 to 41.
