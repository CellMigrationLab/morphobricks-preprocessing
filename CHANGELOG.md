# Changelog

All notable changes to `morphobricks-preprocessing`. Numbers are pull requests (PR) and
issues of [CellMigrationLab/morphobricks-preprocessing](https://github.com/CellMigrationLab/morphobricks-preprocessing).
The design is `docs/restart/preprocessing-design-v0.4.md` in MorphoFeature. The decisions
are in [docs/plan.md](docs/plan.md) §12. Upgrading from v0.3: see the
[migration guide](docs/migration-0.3-to-0.4.md).

## 0.4.0rc5 (since v0.4.0rc4)

What changed between v0.4.0rc4 and v0.4.0rc5: PRs #139 (cross-brick test of 3 November
2026), #140 (review of rc4), #142 (round-2 audit), #143 (this release candidate) and #145
(normalization v0.3.3). Each item is described in full under 0.4.0 below. Decisions: [plan](docs/plan.md) §12 questions
66, 72, 73 and 74.

### Migration notes (from rc4)

- **Mount points are refused** (#140): a mount point below the experiment or masks folder
  is the error `mounted_folder` in discovery, `check`, `run`, `convert` and `RunInputs`.
  Copy the files into the experiment, or select the mounted folder itself. A file with
  several hard links is the warning `hard_linked_file`.
- **`position_id` is required in a version 2 manifest** (#142, A-m5), equal to the image's
  name without its extension; anything else is a `ManifestFormatError`. Manifests written
  by rc1 to rc4 open unchanged (rc4's is tested); a hand-edited one may not.
- **Sizes in micrometres may change by up to 1 ULP** (#142, B-m7) when computed from a unit
  other than µm (`9 nm` is now `0.009`, not `0.009000000000000001`). Manifests are
  unchanged; exact comparisons of the same length in two units now agree.
- **`--verify-content`** (#140, I3), opt-in: rows are reused only for the same content
  SHA-256 (`RunMode(verify_content=True)`, the notebook's **Verify each image's content**).
  Turning it on or off recomputes every row; a default run's manifest has the rc4 keys.
- **A child tied between parents has no parent** (#143, plan Q74): a child lying exactly as
  much in two or more parents is no longer given the one with the lower label. When that
  share reaches `inside_pixel_share` (at the default, exactly half in each of two parents)
  it is counted as `ambiguous_parent`, not `inside`; a tie below it stays
  `split_between_parents`, as before. `mask_relationships.json` is report version
  3; `inside` and `parents_with_N_children` may be lower than with rc4 when masks have such
  ties. CellPatcher and MorphoFeature read neither count.

- **The normalization brick is pinned to v0.3.3** (#145). It refuses corrupt or ambiguous
  statistics and parameters instead of using them (its README, "Migrating to v0.3.3"):
  among others a `plane_histograms.csv` with the header but no rows or with two rows of one
  `plane_id`, and `z` or `t` that are not whole numbers 0 or above. Valid inputs give
  bit-identical results, so tables written by rc4 read unchanged; the full test suite
  needed no change for the new refusals. A hand-edited table may now be refused.
- **A new intensity warning, `channel_without_finite_pixels`** (#145, normalization #57):
  an image channel whose pixels are all NaN or infinite has blank summaries, which the four
  intensity checks cannot see. It is now named, after the other intensity warnings, in
  `check_intensities`, `check_table`, `run` (its report and the command line) and the
  notebook (its own tile, **channels not checked**). `INTENSITY_CODES` has five codes;
  code that expects four should list the new one.

### Changed and fixed

- Normalization brick v0.3.3; `channel_without_finite_pixels` from its `unchecked_channels`
  is an intensity warning (#145, normalization #57).
- `pair_counts`: an exact tie between parents gives no parent; the new evidence
  `ambiguous_parent` is shown by `masks`, the notebook's **Describe the masks** card and its
  relationship table; report version 3 (#143, plan Q74).
- An ImageJ TIFF with a unit but neither resolution tag is `invalid_physical_calibration`
  with the reason; before, uncalibrated with no reason. `read_physical_sizes` gives a TIFF
  without pages no sizes with the reason in `invalid`; in `check` and `run` such a file is
  the error `unreadable_tiff`, as before (#143).
- A TIFF calibration that cannot be read is invalid, and defects are raised (#142, A-m6);
  `position_id` required in v2 (#142, A-m5); one length in any unit is one double (#142,
  B-m7).
- Mount points refused, hard links named (#140, B1/I1); the output folder flushed after the
  commit (#140, I2); the identity rule stated and `--verify-content` added (#140, I3).
- `masks` accepts per-plane masks with a stated shared 2-D mask (F1); `run` needs no
  `--axes` when every image names its axes (F9); a rerun that changes nothing keeps the
  manifest byte-identical (F10); stale locks and resumed runs are worded (F11); `convert
  --output` and the order of copies are explained (F8) (#139, plan Q73).
- Plan Q66 confirmed by the owner; Q73's follow-up (a stated page reshape or reordered
  copies) is not in v0.4 (issue #144).
- Version 0.4.0rc5; the notebook installs `v0.4.0rc5`.

## 0.4.0 (unreleased; release candidates rc1 to rc5)

Preprocessing becomes the one step that discovers, validates, records and serves an
experiment. Every later brick reads the run through `RunInputs`. Nothing is guessed: axes,
calibration, masks and design come from the headers or from the user's recorded answers.

Release candidates: **v0.4.0rc1** (after PP-6, #112), **v0.4.0rc2** (after PP-8d, #118),
**v0.4.0rc3** (#125, after PP-10d), **v0.4.0rc4** (PP-13, for the round-2 data-flow audit
and the bricks' migration) and **v0.4.0rc5** (#143, after the round-2 audit fixes).

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

- The intensity warning `channel_without_finite_pixels`, from the normalization brick's
  `unchecked_channels` (v0.3.3, normalization #57): an image channel without finite pixels,
  which the four intensity checks cannot see (#145).
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
  lengths refuse such images (audit m5, plan §12 question 72).

### Fixed

- **A TIFF calibration that cannot be read is invalid, not absent** (round-2 audit, A-m6):
  `read_physical_sizes` caught every exception and logged it at debug level, so a file
  it could not read, or a programming error, read as an uncalibrated image. Now a file that
  cannot be opened as a TIFF (`TIFF_OPEN_ERRORS`: `OSError`, `ValueError` including
  `TiffFileError`, and the `TypeError` and `struct.error` tifffile raises on some damaged
  headers) and metadata whose bytes cannot be read (`METADATA_READ_ERRORS`: `OSError`,
  `struct.error`, `TiffFileError`; module `tiff_size_values`) give no sizes with the reason
  in `PhysicalSizes.invalid`, named by `invalid_physical_calibration`; no metadata is still
  no calibration; any other exception is raised, and a value that is not a path is a
  `TypeError`. An ImageJ resolution tag that tifffile dropped because it could not read it
  (it only logs), next to the other one, is named instead of leaving that axis silently
  uncalibrated. An ImageJ unit with neither resolution tag ("a unit without a resolution")
  is `invalid_physical_calibration` too, no longer uncalibrated with no reason (owner, 3
  November 2026; #143). `read_physical_sizes` gives a TIFF file without pages ("no pages")
  no sizes with the reason in `invalid`; the folder check reports such a file as the error
  `unreadable_tiff`, as before.

- **A child lying equally in two or more parents has no parent** (owner, 3 November 2026;
  plan §12 question 74, #143): `pair_counts` gave it the parent with the lower label index,
  so `inside` and `parents_with_N_children` depended on the label numbering. An exact equal
  largest overlap now gives no parent; when the share would make the child inside it is
  counted as `ambiguous_parent` (new evidence), shown by `masks`, the notebook and the
  relationship table. `mask_relationships.json` is report version 3.

- **A version 2 manifest names every image's position by its name** (round-2 audit, A-m5):
  the reader accepted a file entry without `position_id`, or with null, and `RunInputs`
  then keyed that position by `""`; an edited `position_id` was read as given. Version 2
  now requires it, equal to `position_id(relative_path)` (the image's name without its
  extension, D85), and refuses anything else naming the entry and the identifier its name
  gives (`ManifestFormatError`); the writer refuses the same, so a written manifest always
  opens. Every v0.4.0 writer recorded it this way, rc4 included (a committed rc4 manifest
  is read in the tests). Version 1 manifests are read for reporting as before.

- **One length written in any unit is one double in micrometres** (round-2 audit, B-m7):
  `to_micrometers` multiplied by a binary approximation of the factor, so `9` nm became
  `0.009000000000000001` µm while `0.009` µm stayed `0.009`, and a header in nm next to a
  `calibration.csv` in µm for the same size was not the same number (an exact comparison
  called the pixels non-square). Dividing by the integer factor would still leave about a
  fifth of such decimals 1 ULP apart (`108.37 / 1000` is `0.10837000000000001`). A length is
  now converted from the shortest decimal of its double (`repr`), its point moved by the
  unit's power of ten and rounded once by `float` (no binary factor, no `decimal` context):
  for a value parsed from a decimal of at most 15 significant digits that is the decimal
  written, so the same length in pm, Å, nm, µm, mm, cm or m gives the double `float` gives
  for it written in µm (`108.37` nm is `0.10837`). At 16 or 17 digits two decimals can give
  one double and the one written is not known. The result is still always positive and
  finite, and an overflow or underflow is still refused. **Sizes in micrometres computed
  from a unit other than µm may change by 1 ULP** (the last bit of the double); manifests
  are unchanged, as they record each size in its own unit; the 0.1 % rule
  (`same_pixel_size`) is not affected.

- **The identity rule is stated, and strict reuse is offered** (review of rc4, I3; owner, 3
  November 2026): reuse, resume and `RunInputs` identify a source by its metadata
  fingerprint (size and `mtime_ns`; for OME-Zarr the inventory of paths, sizes and times),
  whose known blind spot is a same-size rewrite with the time set back; the contract, the
  user guide and the docstrings now say so. The opt-in `RunMode(verify_content=True)` (CLI
  `--verify-content`, the notebook's "Verify each image's content" box) records each
  source's content SHA-256 (`files[].content_sha256`, `settings.verify_content`) and
  reuses earlier or staged rows only for the same content; `RunInputs` of such a run checks
  it at `open`. It is a recorded input; turning it on or off recomputes every row and is
  named. The default run is unchanged: its manifest has neither key (`verify_content` is
  written only when true).

- **Mount points below the experiment or masks folder are refused** (owner, 3 November
  2026): a bind mount is an ordinary folder to the walk, so its files from outside were
  read. A folder (or file) listed in `/proc/self/mountinfo`, or on another device than the
  selected folder, is the error `mounted_folder` naming it, in discovery, OME-Zarr stores,
  the masks folder, `check`, `run`, `convert` and `RunInputs` (`mounts.MountCheck`). Where
  no mount table can be read (macOS, Windows), only a mount of another device is seen. The
  device is read with `os.stat` of the path: `os.DirEntry.stat()` reports `st_dev` 0 on
  Windows, which refused every folder there (review of #140).
- **The commit is flushed to disk** (review of rc4, I2): the output folder is flushed
  (`fsync`, POSIX) right after the manifest's rename, so a power cut after a run returned
  keeps the new generation. A run that finishes an earlier publication flushes it first.
- **Hard-linked images and masks are named** (owner, 3 November 2026): a file whose link
  count is above 1 is the warning `hard_linked_file`; it is read as before.

- Cross-brick test of 3 November 2026 (plan §12 question 73): `masks` accepts per-plane masks
  with a stated shared 2-D mask (`--mask-axes Nuclei=YX`) as `run` does, instead of
  `mask_planes_differ` (F1); `run` needs no `--axes` when every image names its axes (F9);
  a rerun that changes nothing leaves `run_manifest.json` byte-identical, keeping its
  `written_at`, so pinned manifest SHA-256s stay valid (F10); `output_locked` says a lock
  left by a process no longer running on this computer looks stale and how to remove it
  (also `convert`'s, for the folder of the copies; it is never removed on its own), `run`
  without `--axes` names the images' differing arrangements instead of `--axes None`, and a resumed run no longer says all rows were computed (F11); `convert --output` names
  the runs' output folder in the printed next commands, and `inconsistent_dimensions` says
  that named-axis copies keep each image's order (F8).
- Preprocessing audit of 3 November 2026 (plan §12 question 72): `read_plane` and
  `RunInputs.read_plane` no longer count histogram memory, so a 5000² float32 plane is read
  within the 1 GiB limit (I2); a header size in an unsupported unit (OME `pixel`) or without
  a unit is unknown in `pixel_sizes`, never a value without a unit (I3); `RunInputs.read_mask`
  and `read_plane` refuse a `z` or `t` outside the image with `IndexError`, a shared 2-D
  mask included (I5); `to_micrometers` accepts NumPy real scalars (m4); OME-XML in a TIFF
  that cannot be parsed is `invalid_physical_calibration`, no longer read as uncalibrated,
  and other errors reading TIFF sizes are no longer hidden (m6). An ImageJ resolution tag
  of another type is `invalid_physical_calibration`, and an OME-Zarr scale without a unit
  is named by `unsupported_pixel_size_unit` (review of #136).
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
- An experiment or named-axis copies inside the masks folder are refused
  (`experiment_inside_masks`, `copies_inside_masks`), and so is a copy folder overlapping it
  (`copy_folder_overlaps_masks`; `convert --masks`, `masks_folder=`); `RunInputs.open`
  refuses a recorded run that breaks these rules (audit B1, 3 November 2026).
- `check`, `run`, `masks`, `design` and `calibrate` check the output folder against the
  masks folder, given or recorded, before writing anything (audit I1).
- `output_inside_experiment` and `copy_folder_overlaps_experiment` compare folders by
  device and inode too, as the masks-folder rules do, so `Exp/out` is inside `exp/` on a
  file system that ignores case (macOS, Windows), and discovery still skips that output
  folder (#135 review follow-up).
- Masks one pixel high or wide are read as 2-D `YX` masks (#63).
- Step 1 keeps an unreadable `experiment_design.csv` untouched (#61).
- The notebook starts from the inputs the earlier run in the output folder recorded: the
  exclusions, bins, histogram and intensity settings, image type and missing-axes answer,
  from the run's own rule (`run.planned.recorded_inputs`). An untouched notebook kept
  neither a CLI run's `--bins` nor its `--exclude` (audit I4; plan §12 question 71).

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
