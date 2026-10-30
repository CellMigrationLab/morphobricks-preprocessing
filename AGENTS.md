# AGENTS.md — MorphoBricks preprocessing brick

Guidance for AI coding agents (and humans) working on
[CellMigrationLab/morphobricks-preprocessing](https://github.com/CellMigrationLab/morphobricks-preprocessing).
It summarises the agreed plan, the decisions taken so far, the programming style, and what
was learned while building the notebooks. Read it before changing anything.

The authoritative documents are in `docs/`. This file points to them and adds what they
do not say: how we work, why things are the way they are, and the traps we already hit.

| File | What it fixes |
|---|---|
| `docs/plan.md` | Goals, processing passes, milestones, open questions and their decisions (§12) |
| `docs/contract.md` | Rules other bricks rely on: folders, axes, dtypes, binning, CSV schema, statistics, normalization, validation severities, manifest format. Changing it may need a new `schema_version` |
| `docs/development.md` | Coding conventions |
| `docs/user_guide.md` | Biologist-facing guide: notebook, CLI, outputs, every message code |
| [morphobricks-normalization](https://github.com/CellMigrationLab/morphobricks-normalization) `docs/contract.md` | Pixel types, binning, group statistics and normalization parameters (§1.2) and the `plane_histograms.csv` columns (§6.2); this repo's contract §3–§7 only point there |

---

## 1. What the brick does

MorphoBricks is a modular microscopy pipeline. This repository is its **preprocessing
brick**.

- **Input:** one experiment folder of TIFF images and/or OME-Zarr images (OME-NGFF 0.4/0.5,
  `zarr` extra).
- **Output:** `plane_histograms.csv` (schema version 4): one row per XYC plane (an image at
  one Z and T), with one intensity histogram per channel in its own columns, plus
  `experiment_design.csv` (repeat groups, confirmation, controls) and `run_manifest.json`,
  all in an output folder outside the experiment.
- **Statistics code (from the normalization brick):** the per-plane histogram, the CSV
  reader and writer, pooling (`PooledHistogram`) and normalization parameters (`min_max`,
  `percentile_range`, `z_score`, `robust_z_score`) live in the required dependency
  `morphobricks_normalization` (pinned to its release v0.3.2; first pinned at v0.2.0, plan §12 questions 27 and
  28). This brick runs them over
  the experiment. Later bricks such as patch extraction use them to normalise any group of
  planes without rereading pixels. The old names stay importable from here, deprecated,
  for one version (follow-up P1, normalization plan N6).
- **Never:** modifies source images, writes inside the experiment folder, or applies a
  normalization itself.
- **Optional (`zarr` extra, plan §12 question 12):** OME-Zarr input, and converting TIFF
  images to OME-Zarr in the output folder before processing them (contract §10).
- **Large planes (plan §12 question 15):** planes over the memory limit are read in blocks,
  automatically and announced, with identical rows.
- **Out of scope:** spatial corrections (flat-field, BaSiC, CLAHE).

Pipeline:

1. **Pass 1 (preflight):** folder discovery, then TIFF header inspection without reading
   pixels, then a suggested axis mapping.
2. **The user confirms** the labels, the experiment design (repeat groups comparable) and
   the axis roles.
3. **Pass 2:** read one 2D plane at a time (in blocks when it is over the memory limit),
   compute exact extrema and the histogram, write the CSV and the manifest. Reruns only
   read new or changed files.

---

## 2. Repository map (one module per job)

```
src/morphobricks_preprocessing/
  discovery.py      folder walk, leaf folders, labels (condition / biological_repeat / technical_repeat),
                    position_id per image and the duplicate check
  metadata.py       TIFF header inspection (tifffile, no pixel reads), dtype/dimension/series checks,
                    OME channel names + significant bits, physical sizes (TiffMetadata.physical_sizes);
                    header consistency warnings (width/height, plane counts, pixel sizes)
  physical_sizes.py PhysicalSizes (pixel sizes, z-step, time step, a unit per axis, micrometers()
                    for comparing), read_physical_sizes (OME / ImageJ TIFF metadata; also importable
                    from zarr_export) and ngff_physical_sizes (OME-NGFF scale and axis units)
  dataset.py        dataset consistency from the folders: biological repeats per condition, balance
                    table, unbalanced_images, repeat_missing_conditions, CONSISTENCY_CODES (P2)
  design.py         experiment design (P3): ExperimentDesign/DesignRow, default pairing, reconcile with
                    the folders on reruns, design_issues, read/write experiment_design.csv, load_design
  image_type.py     image type (P4): FLUORESCENCE/RGB, review_image_type (suggestion from
                    TiffMetadata.rgb_samples and the confirmed roles, whether RGB is possible via the
                    brick's check_rgb), image_type_issues (rgb_not_possible)
  intensity.py      intensity checks (P5): check_intensities/check_table (the brick's image_summaries
                    and intensity_checks, IntensityReport), repeat_boxes and repeat_distributions for
                    the notebook's plots, intensity_text for the CLI
  validation.py     ValidationIssue (severity, code, message, paths, action), error()/warning() builders
  axes.py           AxisMapping (role per dimension), prefill, check_mapping, review_axes (one mapping
                    applied to every file, missing axes as singletons only when confirmed)
  planes.py         plane-by-plane reader (iter_planes, read_plane, read_sample_plane), table rows as
                    XYC planes whose channels are read one at a time (iter_xyc_planes, XycPlane),
                    memory estimate,
                    check_planes (preflight memory / layout errors); planes over the limit read in
                    blocks of TIFF strips/tiles or zarr chunks (Plane.blocks, BlockReader), reduced
                    previews
  _moved.py         deprecated access to the code moved to morphobricks_normalization: the moved
                    names, warn-once DeprecationWarning, __getattr__ helpers
  dtypes.py, histogram.py, records.py, csv_io.py, pooling.py, normalization.py
                    deprecated stand-ins (module __getattr__ only, no code) for one version
  manifest.py       RunManifest / RunSettings / FileEntry, JSON read/write, SHA-256 of the CSV
  run.py            preflight(), run_experiment() (incremental), sample_planes() for previews
  report.py         plain-language summaries shared by CLI and notebook, group statistics tables
  cli.py, __main__  `morphobricks-preprocessing check|run|masks` (also `python -m morphobricks_preprocessing`)
  masks.py          masks (Q32): find_masks pairs every image with its masks (masks folder with one
                    folder per type mirroring the experiment, or <image>_<type>.tif in the
                    experiment's folders, mixed; OME-Zarr label images), header-only
                    checks (2D, image YX, integer/bool), MaskInventory (to_manifest), read_mask
  mask_scan.py      the optional mask step (Q32): piece_counts (scipy.ndimage), pair_counts,
                    scan_masks -> MaskReport (kinds, relations, direct-parent hierarchy, evidence),
                    write_mask_report (mask_relationships.json); DEFAULT_THRESHOLDS,
                    mask_thresholds (checked changes)
  zarr_input.py     OME-Zarr input: open_ngff (OME-NGFF 0.4/0.5 metadata -> level-0 array, axis
                    letters and physical sizes from the scale and axis units),
                    ngff_label_names/open_ngff_labels (label images, for later bricks),
                    zarr 2/3 helpers, canonical_dtype; needs the `zarr` extra
  zarr_export.py    TIFF -> OME-Zarr conversion (OmeZarrWriter, OME-NGFF 0.4, plane by plane or
                    block by block),
                    ConversionSettings (chunk size, compression, pyramid levels), estimate_conversion
  notebook_style.py notebook look and feel: cards, status cards, tiles, tables, ConfirmToggle, Browse dialogs
  notebook_ui.py    PreprocessingNotebook: the four notebook steps (needs the `notebook` extra);
                    folder_step/axis_step take title, purpose, intro and show_next (and
                    show_output and show_design for step 1) so other bricks can embed them
                    (plan §12 Q26, Q31)
notebooks/MorphoBricks_Preprocessing.ipynb   thin notebook: every code cell calls notebook_ui only
tests/              pytest with simulated TIFFs (tests/tiff_tree.py, tests/simulation.py); the
                    schema-version-2 fixture (tests/v2_fixture.py, tests/data)
experiments/binning_study/      the study behind the binning decisions (not part of the package)
```

Moved to [morphobricks-normalization](https://github.com/CellMigrationLab/morphobricks-normalization) (P1), imported from
`morphobricks_normalization.<module>` by the code here:

```
  dtypes.py         supported dtypes, default and allowed bin counts
  histogram.py      per-plane histogram (PlaneHistogram, compute_plane_histogram, and
                    compute_block_histogram: two passes over blocks, identical result), memory estimate
  records.py        PlaneRecord (one CSV row: an XYC plane, one histogram per channel),
                    make_plane_id, group_records
  csv_io.py         write/read plane_histograms.csv (atomic write, labels always text)
  pooling.py        PooledHistogram: pooled counts, never mean-of-means
  normalization.py  NormalizationParameters (offset, scale) and the four methods
```

Their tests (`test_histogram.py`, `test_csv_io.py`, `test_pooling.py`,
`test_normalization.py`) moved with them. `tests/test_moved.py` checks the deprecated
names here.

The notebook and the CLI contain **no processing logic**. They call the same public
functions the tests use.

---

## 3. How we work

1. **Read first.** Before coding a milestone, read `docs/plan.md`, `docs/contract.md`,
   `docs/development.md` and the modules you will touch.
2. **Ask before deciding.** Open questions in plan §12, and any choice that is genuinely
   the user's (product behaviour, UX, file formats), are asked *before* coding. Offer 2–4
   options with a recommended one first and explain the trade-off in one line each.
3. **Record every decision in the docs** using the established pattern in plan §12:
   `*Decided (October 2026): ...*`. Also update the affected rules in `contract.md`
   (tables in §1, §2, §8, §9) and the milestone status in plan §10.
4. **Local review first (user request, October 2026).** Keep new changes local for the
   user to review. Do not commit, push or open a PR until the user requests it. Once
   requested, use **one milestone or feedback round per branch and PR**, against `main`.
   PR description:
   *Summary* (by module), *Decisions*, *Notes for review* (limitations, what was not
   verified), *Tests* (exact counts, e.g. "455 passed, 2 skipped").
5. **Done means:** `ruff check .`, `ruff format --check .` and `pytest` all pass (CI runs
   lint on Ubuntu and tests on Ubuntu, Windows and macOS with Python 3.11 and 3.13), docs
   updated, and local changes ready for review. Open a PR only when the user requests it.
6. **Be honest in reports.** Say what was verified and what was not (for example: "widgets
   tested headless; not looked at in a real JupyterLab").
7. **Scope discipline.** Do the task asked; list unrelated problems as follow-ups instead
   of fixing them silently.
8. **Keep generated or local files out of commits.** That includes test images, outputs
   (`*_MorphoBricks_Preprocessing/`), editor/agent config and launch files.

---

## 4. Rules of the brick (summary of the contract)

### Folders and labels (contract §1)

- **Images:** TIFF files and OME-Zarr folders (name ends in `.zarr`). A store is one image,
  never a folder level, and discovery never enters it.
- **Hierarchy:** `<experiment>/<condition>/<biological repeat>/<technical repeat>/<image>.tif`.
- **Leaf folders:** a leaf folder has images and no subfolders. Only TIFFs directly in
  leaf folders are analysed.
- **Missing levels:** a branch may stop early; missing labels are the value `single`. A
  folder *named* `single` (lower case) that is the only folder in its place is that level's
  value `single` (a level with one member, plan §12 question 23); spelled in another case,
  or beside other folders, it is the error `reserved_folder_name`.
- **Uniqueness:** labels are unique only among siblings, compared case-insensitively;
  siblings differing only in case are an error. Groups are identified by the full label
  path.
- **Depth:** a folder below the technical-repeat level is an **error** (so a layer of images
  is never dropped silently).
- **Positions (cross-brick decision D02):** one image is one position. Its `position_id`
  (`discovery.position_id`) is the name without `.tif`/`.tiff`/`.zarr` and a preceding
  `.ome`. The position key is `condition` + `biological_repeat` + `technical_repeat` +
  `position_id`. Two images in one leaf folder with the same identifier, ignoring case,
  are the error `duplicate_position_id`: never guess.
- **System files:** dot-files and dot-folders, `Thumbs.db`, `desktop.ini` and `__MACOSX` are
  ignored **without** a warning.
- **Mixed folders:** images in a folder that also holds subfolders are the error
  `tiff_outside_leaf` (owner decision, October 2026; every brick refuses such a folder).
- **Warnings:** non-TIFF files, empty folders.
- **Paths:** paths in outputs are relative to the experiment root, with `/`.

### Axes (contract §2)

- **Roles:** X, Y, Z, C, T. Exactly one X and one Y; each role at most once; every axis
  longer than 1 needs a role. One confirmed mapping per experiment.
- **Prefill from tifffile axis labels:**
  - X/Y/Z/C/T keep their role;
  - `S` (RGB samples) becomes C when there is no other C;
  - everything else (`I`, `Q`, …) is left unassigned: **never guess** depth vs time.
- **Position-like axes** (`R` tile, `M` mosaic, `J` column, `K` row) cannot get a role. If
  they hold more than one plane: `unsupported_axis` error.
- **Missing axes:** a file lacking some of the mapping's axes (e.g. `ZYX` next to `ZCYX`) is
  read with them as length 1 **only** if the user confirmed it (`missing_as_singleton`) and
  exactly one arrangement fits. The coordinate is then 0, not blank.
- **Series and pyramids:** only the first image series, at full-resolution level 0. A file
  with more than one series is an error (`multiple_series`). OME pyramids are one series;
  non-OME SubIFD pyramids appear as two series in tifffile and are therefore blocked.
- **Coordinates:** a row is an XYC plane; its coordinates `z, t` are zero-based and always
  written: 0 when the image has no such axis (question 17). Channels are columns,
  numbered from 0 (question 27). `PlaneRecord` stores None as 0; `Plane` (2D, with
  `z, c, t`) and `XycPlane` coordinates stay None for axes the file lacks.
- **Channel counts (N43, question 27):** every image has the same number of channels (the
  C axis, 1 without one, also for a confirmed missing C), else the error
  `different_channel_counts`: from the headers in `metadata.check_metadata` (C, or S
  without C) and from the confirmed roles in `review_axes`, which supersedes it
  (`axes._SUPERSEDED_METADATA_CODES`). Shared builder `metadata.channel_count_issues`.
- **OME-Zarr axes:** translated from OME-NGFF metadata (`zarr_input.ngff_axes`: time -> T,
  channel -> C, space z/y/x -> Z/Y/X, anything else -> Q). The experiment mapping is
  defined on the TIFF arrangement if there are TIFFs; OME-Zarr images with another order
  are matched by role (`axes.declared_mapping`, `file_mapping_for`); a misfit is
  `ome_zarr_axes_mismatch`. Use `file_mapping_for`, never `mapping.for_axes`, to get an
  image's mapping.

### Pixel types and binning (normalization contract §1.2.1–§1.2.2)

- **Supported dtypes:** int8/16/32/64, uint8/16/32/64, float32, float64. Everything else
  (bool, complex, float16, …) is rejected. One dtype per experiment (`mixed_dtypes` error).
  Source values are never converted.
- **Every plane is binned over its own min–max range**, with the same bin count for all
  planes of an experiment. The binning study showed per-plane ranges cut percentile errors
  5–30× compared with binning over the whole dtype range, and adding images never changes
  existing rows. Optionally (follow-up P6, question 34) histograms that are not exact use
  the brick's robust range instead: bins between the plane's 0.01 % and 99.99 % quantiles,
  the pixels outside counted as underflow and overflow. Every plane is binned with the
  brick's `histogram_with_range_check`; channels whose central range (1st to 99.8th
  percentile) covers fewer than 100 bins give the warning `narrow_histogram_range`.
- **Integer planes:** whole-number bin limits whose widths differ by at most one value.
  Exact when the range fits the bin count.
  - Default bins: 256 for 8-bit and 65,536 for 16-bit (one per value, so exact), 65,536 for
    wider types.
  - Allowed: 1 to min(65,536, 2^bits).
- **Float planes:** equal-width bins over the finite range. NaN and ±inf are ignored and
  counted.
  - Default 10,000 bins, allowed 2 to 65,536.
- **Special planes:**
  - A constant plane puts every pixel in bin 0.
  - A plane with no finite pixels has blank extrema and zero counts; it is a warning
    unless no plane is usable (error).
- **Representative value** of a bin is its midpoint; no pixel is further than half a bin
  from it.

### Statistics and normalization (normalization contract §1.2.3–§1.2.4)

- **Groups pool their planes' (representative, count) pairs. Never average per-plane
  statistics.** In the study, mean-of-means underestimated std by up to 22% and p99.9 by up
  to 36%.
- **Accuracy:** min and max are exact from stored extrema. Percentiles use numpy
  `inverted_cdf` on pooled counts.
- **Normalization:** each method is an offset and a scale: `(pixels − offset) / scale`.
  Percentile range defaults to 1–99.8. Robust z-score uses MAD × 1.4826. A zero or
  non-finite scale is an error.

### CSV (normalization contract §6.2, schema version 4, follow-ups P3b and P6)

- **Rows:** one per XYC plane (image, Z, T), written with the brick's writer from
  `PlaneRecord(path, *labels, histograms=…, z=, t=, position_id=)`.
- **Columns:**
  `position_id, image_file, z, t, condition, biological_repeat, technical_repeat,
  source_relative_path, plane_id, dtype, bit_depth, channel_count, total_pixel_count,
  bin_count, schema_version`, then per channel `i`: `Intensity_MinIntensity_C<i>`,
  `Intensity_MaxIntensity_C<i>`, `Histogram_ValidPixelCount_C<i>`,
  `Histogram_BinningMin_C<i>`, `Histogram_BinningMax_C<i>`, `Histogram_Count_C<i>_00000…`.
- **`plane_id`** = relative path plus `::z=…,t=…`, always both (`img.tif::z=0,t=0` for a
  2D image). It is the row key.
- **Rows** are sorted by path, then z, t.
- **Earlier versions:** the reader refuses schema 1 and 2 (`CsvFormatError`); a run
  recomputes them completely (manifest `schema_version` differs). Each channel's
  histogram equals the version-2 row's, whole and in blocks
  (`tests/test_schema_v2_equivalence.py` with the committed fixture in `tests/data`,
  written by `tests/data/make_v2_fixture.py` with the pre-P3b code).
- **Step 4** pools every channel of a group together, as with version 2
  (`report.channel_histograms`), so its table and plot are unchanged.
- **`position_id`** is filled in every row (D02, plan §12 question 20). Earlier tables with
  it blank are filled on the next run without reading pixels.
- **Reading:** labels are always read back as text (`NA`, `001`, `TRUE` stay as typed).
  Floats are written with `repr`.

### Run manifest and reruns (contract §9, plan §7)

- **Manifest contents:**
  - settings, the confirmed labels per leaf, and the axis mapping;
  - per-file size, `mtime_ns`, axes, `singleton_roles`, plane count, `position_id` and
    `roles` (the file's own axis roles; optional keys, null when absent; manifest version
    stays 1); flags are JSON booleans only and the versions are strings (`"1"`, `"4"`);
  - channel names and significant bits (OME only, information only);
  - the CSV's SHA-256 and package versions (including `morphobricks-normalization`).
- **A file is unchanged** when its size, mtime, labels and axis layout match and the table
  still holds all its rows. Unchanged rows are reused; new and changed files are read;
  deleted, renamed or excluded files lose their rows.
- **Full rebuild** happens on `force_rebuild`, when there is no readable manifest, when
  the dtype, bin count, histogram settings (`RunSettings.histogram`) or axis mapping
  changed, or when the CSV's hash no longer matches.
  Changing only the memory limit or the exclusions recomputes nothing.
- **Writes:** the CSV is written first, then the manifest, each via a temporary file and
  replace. Nothing is written if any error is found. An interrupted run is caught next
  time by the hash check.
- **Exclusions:** the user may exclude images explicitly (`exclude=[...]`, e.g. damaged
  files). Excluded images are not read, are reported (`excluded_images` warning), are
  recorded in the manifest, and lose their rows.

### Memory (plan §5)

- **Unit of reading:** read one TIFF page at a time and yield the planes inside it. Never
  load a full Z/C/T volume unless the file stores it as a single page.
- **Channels of a row (question 27, the owner's choice):** `iter_xyc_planes` reads and bins
  them one at a time (`XycPlane.histograms`), keeping only the finished histograms; the
  last decoded page is kept, so a page holding several channels (RGB) is decoded once.
  When C is a page axis and pages hold several Z/T planes (volumetric), pages are decoded
  again per XYC plane: time, never memory.
- **Estimate per plane, from the header:** decoded page + encoded bytes + plane copy +
  histogram temporaries (`histogram.estimate_histogram_memory`) + the finished bin counts
  of every channel of the XYC plane (`8 × bins × channel_count`). It is deliberately an
  upper estimate, and one whole/blocks decision covers every channel.
- **Limit:** default 1 GiB. A plane over the limit is read in blocks (question 15):
  - Blocks are whole TIFF strips or tiles, or OME-Zarr chunks, in full-width bands first,
    as many as fit the limit (`planes._choose_block`, estimate `planes._block_memory`).
    Uncompressed TIFF pieces (read straight from the file) and zarr chunks may also be read
    a few rows at a time. No block setting: the limit decides.
  - Announced, never silent: warning `planes_read_in_blocks` in `check_planes`, and
    `read_in_blocks` per file in the manifest (information only, not part of
    `same_source`).
  - Histograms: `compute_block_histogram` reads the blocks twice (extrema, then counts);
    8- and 16-bit integers once (a count per possible value). Identical to the whole plane,
    and `compute_plane_histogram` uses the same code with one block.
  - A plane read in blocks is a `Plane` with `pixels=None`; `Plane.blocks()` reads from
    the open file, so use it before asking `iter_planes` for the next plane.
  - Previews (`read_plane(preview=True)`, `read_sample_plane`) are reduced to every n-th
    pixel (`Plane.step`, at most 4,096 px per side).
  - A compressed TIFF storing a plane in one strip/tile cannot be split: still the
    `plane_over_memory_limit` error, like a plane whose smallest block is over the limit.
    The readers raise `PlaneMemoryError` before reading.
  - Wide integers (32/64-bit) reserve up to 512 MiB for the per-value count table in
    `estimate_histogram_memory`, so they need a limit above about 520 MiB even in blocks.

### Dataset consistency (contract §11, follow-up P2)

- **Warnings only**, from folder names and headers; never block.
- **Balance:** images per condition (columns) and biological repeat (rows, matched by
  folder name), technical repeats added up; below half or above twice the median of the
  non-empty cells is `unbalanced_images`. Excluded images are not counted.
- **`repeat_missing_conditions`:** a biological repeat not in every condition (only with
  more than one condition).
- **Pixel sizes:** `different_pixel_sizes` (X by Y; z-step only for images with Z > 1;
  micrometres, 4 significant digits) and `missing_pixel_size` (known for some only).
  OME-Zarr sizes come from the level-0 scale with axis units; no unit means unknown.
- **`different_plane_counts`:** Z, T or any other dimension except X, Y, C and S;
  channels are the error `different_channel_counts` (question 27), also in this panel;
  `different_sizes` is width/height only, so no dimension is reported twice.
- **Notebook:** the consistency codes (`dataset.CONSISTENCY_CODES`) appear only in step 1's
  "Dataset consistency" panel with the balance table; the Messages card keeps the rest.
  The CLI `check` prints the balance table; `run` reports the same warnings.

### Experiment design (contract §12, follow-up P3)

- **Repeat group** = one biological repeat across conditions; same folder name pairs by
  default; one folder per condition per group (`repeat_group_conflict` otherwise).
- **Confirmation** per group (`design_not_confirmed` until then); all-`single` experiments
  get one whole-experiment question. Controls are recorded only.
- **`experiment_design.csv`** (schema 1): one row per condition x biological-repeat folder,
  sorted; counts at confirmation; `confirmed_on` ISO date. Lenient reader (any-case
  booleans, BOM, empty group = folder name); `DesignFormatError` otherwise.
- **Reruns:** `reconcile_design` keeps answers for known folders, reopens only groups that
  gain a folder, drops removed folders.
- **Notebook** writes the file after every answer once the labels are confirmed; a
  confirmed design for the same folders is folded away behind **Change the experiment
  design**; step 3 is gated on it. `folder_step(show_design=False)` (embedding, Q31) asks
  and writes nothing, and step 3 runs without a design. **CLI** `run` reads it (or `--design`), else writes the
  reconciled template and exits 1. **API** `run_experiment(design=None)` is optional; with
  a design it is written with the table and `experiment_design_sha256` goes in the manifest.

### Image type (contract §13, follow-up P4)

- **Types:** `fluorescence` and `rgb`; RGB needs three channels of an integer type
  (the normalization brick's `check_rgb`), else `rgb_not_possible` in a run.
- **Suggestion:** `rgb` only when every analysed image is a TIFF stored as RGB colour
  (`TiffMetadata.rgb_samples`: photometric RGB, three samples per pixel, axis `S`), the
  confirmed C role is on `S`, and RGB is possible. Never applied without confirmation.
- **Notebook:** step 2's "Image type" card appears once the axis roles are confirmed
  (also when embedded); radio buttons with the suggestion preselected, disabled at
  fluorescence when RGB is impossible; `ConfirmToggle` **Yes, this is the right image
  type**; changing roles or folder resets it; step 3 gated on `confirmed_image_type`.
- **CLI:** `--image-type`, else the suggestion, printed with its reason.
- **Manifest:** optional top-level `image_type` (null without one) and
  `channels_from_rgb_samples`; changing the type recomputes nothing.

### Intensity checks (contract §14, follow-up P5)

- **From the table only:** `run_experiment` checks its rows in memory (`RunResult.intensity`,
  apart from `RunResult.issues`, never blocking); `check_table` reads any table. The four
  codes are the normalization brick's; comparisons stay within a condition.
- **Limits:** `IntensityCheckSettings` (3.0, 0.01, 5), recorded in the manifest
  (`intensity_checks`, optional); changing them recomputes nothing.
- **Notebook:** `IntensityPanel` in step 3 (after a successful run, `run_checks`) and step 4
  (`table_checks`): status, tiles, warnings table with actions, collapsed **Check
  settings**, channel buttons, box plots (`Issue.channel` marks images) and cumulative
  distributions per biological repeat. Nothing written. CLI prints them, no plots.

### Validation messages (contract §8)

Every expected problem is a `ValidationIssue` with:

- a stable snake_case `code` (e.g. `missing_axes`, `unreadable_tiff`), listed in user guide §8;
- a severity: errors block processing, warnings do not;
- a plain-language `message` saying what happened (no jargon, no stack traces);
- `paths`: sorted relative paths of the affected files;
- an `action`: what the user can do.

Technical details go to `logging` (shown in the notebook's "Technical details" panel, or
`--log FILE` in the CLI). Custom exceptions are only for genuine failures.

---

## 5. Decisions log

| Topic | Decision |
|---|---|
| Binning | Per-plane min–max, same bin count per experiment, pooled counts for groups (binning study) |
| Core dependencies | `numpy`, `tifffile`, `scipy` (the mask step's piece counts, Q32) and `morphobricks-normalization` (git dependency pinned to its release tag v0.3.2, whose CSV reader checks each row's stored `plane_id`, `image_file` and `bit_depth`; `[tool.hatch.metadata] allow-direct-references`). `ipywidgets` and `matplotlib` in the `notebook` extra; `zarr` in the `zarr` extra; `pytest` and `ruff` in `dev` |
| OME-Zarr (Q12) | Input may be TIFF and/or OME-Zarr (folders ending in `.zarr`), mixed. OME-Zarr axes come from their metadata and are matched by role; level 0 of a single image only, several images per store is `multiple_series`. TIFF choice once per experiment (`convert_tiffs`): read directly (default) or convert to `<output>/ome_zarr` (OME-NGFF 0.4) and compute rows from the copies; switching recomputes TIFF rows and removes unused copies. Reads OME-NGFF 0.4 and 0.5 (0.5 needs zarr 3). In the notebook: its own step-3 card "(Optional) Image conversion" after Settings (Q13): radio buttons (read directly = recommended), a disk-space warning (amber, red when the estimate exceeds free space, never blocking), and conversion settings (chunk size, compression + level, pyramid levels) shown only when converting. CLI `--convert-tiff` with `--chunk-size`, `--compression`, `--compression-level`, `--pyramid-levels`. No overlap setting: chunks never overlap |
| System files (Q5) | Ignored silently; `single` check and sibling uniqueness are case-insensitive |
| Too-deep folders (Q7) | Error |
| Folders named `single` (Q23) | Accepted as the level's value `single` when lower case and the only folder in its place (MorphoBricks one-member level, MorphoRelate M3); another case or other folders beside it stay `reserved_folder_name` |
| Channel names (Q1) | Manifest only (from OME); `channel_names_differ` was a warning, an error since issue #37 (channels are matched by number, no override); names trimmed of leading/trailing whitespace at read time for every image (manifest `channel_names_trimmed`), inner whitespace compared exactly; copies leave unnamed channels unlabelled; CSV stays at schema 1 |
| Plane coordinates (Q17) | `z`, `c`, `t` always written (0 for absent axes), `plane_id` always `::z=…,c=…,t=…`; CSV schema 2, version-1 tables recomputed on the next run. Superseded for the CSV by Q27: rows have `z`, `t` and channels in columns |
| Channels across columns (Q27, P3b) | CSV schema 3 with the brick's writer: one row per XYC plane, per-channel CellProfiler-style columns, `plane_id` `::z=…,t=…`. Channels read one at a time (owner's choice); memory estimate adds the plane's finished bin counts. `different_channel_counts` error before any read (headers in step 1, confirmed roles in step 2); `different_plane_counts` without channels. Schema 1/2 tables recomputed; manifest version 1 records schema 3; step 4 output unchanged. Brick first pinned by commit `0e53e67`, now by its tag v0.2.0 (Q28) |
| OME-NGFF labels (Q18) | Read-only helpers `ngff_label_names`/`open_ngff_labels` for later bricks (CellPatcher masks); label images are never analysed and the outputs do not change |
| Multi-series / pyramids (Q2) | Level 0 only; more than one series is an error |
| RGB `S` axis (Q3) | Prefilled as C, confirmed like any role |
| Significant bits (Q4) | Manifest only, information only; `bit_depth` and binning unchanged |
| Corrupt files (Q6) | Explicit exclusion list, recorded in manifest, rows removed |
| Notebook target | Local Jupyter (not Colab-first) |
| CLI | `check <folder>` (summary, label tree, issues, suggested `--axes`) and `run <folder> --axes ROLES` with `--output`, `--exclude`, `--bins`, `--histogram-range`, `--robust-quantiles`, `--memory-limit 2GiB`, `--missing-as-singleton`, `--force-rebuild`, `--log`, and the conversion options above. Axis roles are always stated explicitly, never taken silently from metadata. Exit 1 on errors |
| Large planes (Q15) | Planes over the memory limit are read in blocks automatically, announced by the warning `planes_read_in_blocks` and `read_in_blocks` in the manifest. Block size derived from the limit (whole strips/tiles/chunks, uncompressed pieces and chunks also by rows). Compressed single-strip/tile planes keep the `plane_over_memory_limit` error. Two-pass histograms identical to whole planes. OME-Zarr copies written block by block, smaller levels built from the stored level above. Axis preview reduced (every n-th pixel) with a caption saying so |
| Normalization brick (P1) | The modules `dtypes`, `histogram`, `records`, `csv_io`, `pooling` and `normalization` moved unchanged to `morphobricks_normalization` (normalization plan N1, N2, N6) and are imported from there. Their public names stay importable from `morphobricks_preprocessing` and from the old submodules for one version: module `__getattr__` stand-ins (`_moved.py`), one `DeprecationWarning` per old path naming `morphobricks_normalization.<module>.<name>`, same objects. The package's own code never uses the old paths. Outputs unchanged (CSV schema 2); the manifest's `versions` adds `morphobricks-normalization`; Step 0 checks for it; contract §3–§7 text moved to the normalization contract (§1.2, now §6.2 for the CSV) |
| Dataset consistency (Q21, P2) | Step 1 "Dataset consistency" panel: balance table (images and technical-repeat folders per condition x biological repeat), `unbalanced_images` (< 0.5 or > 2 x median), `repeat_missing_conditions`, `different_pixel_sizes`, `missing_pixel_size` (OME-Zarr scale included), `different_plane_counts`; `different_sizes` narrowed to width/height; consistency issues shown only in that panel, not in Messages. Warnings only; outputs unchanged |
| Experiment design (Q22, P3) | Step 1 after the labels: pairing dropdowns, one yellow "Yes, they are comparable" question per repeat group (single whole-experiment question without biological-repeat folders), control checkboxes; `experiment_design.csv` written after every answer; reruns load it and fold the questions behind a Change button; step 3 gated. CLI `run` reads it or `--design`, else writes a template and exits 1. `run_experiment(design=...)` optional; manifest `experiment_design_sha256` (optional key, version 1). Two folders of one condition in a group block that group |
| Image type (Q29, P4) | Step 2 card after the axis roles: Fluorescence or RGB, suggestion preselected, confirmed with **Yes, this is the right image type**; RGB suggested for TIFF photometric RGB with three samples on the C axis; RGB needs three integer channels (`check_rgb`), else disabled with the reason and `rgb_not_possible` in a run. Manifest keys `image_type`, `channels_from_rgb_samples` (optional, version 1); type changes recompute nothing. CLI `--image-type`, default the printed suggestion |
| Intensity checks (Q30, P5) | Warnings from the table alone with the brick's `image_summaries`/`intensity_checks`, shown automatically after step 3 and in step 4 for any table; limits under a collapsed **Check settings** panel, recorded in the manifest (`intensity_checks`); per-channel box plots with flagged images and cumulative distributions per biological repeat, notebook only; the CLI prints the warnings (`--outlier-limit`, `--saturation-limit`, `--min-images`) |
| Histogram range (Q34, P6) | Step 3 **Settings**: the brick's `HistogramSettings` (`minmax` default, or `robust` with adjustable quantiles); recorded in the manifest's `settings.histogram`, any change recomputes every row. Every plane binned with `histogram_with_range_check` (memory estimate with `range_check=True`, through `planes._Binning`); `narrow_histogram_range` warnings from `range_check_issues`, one per channel, with the narrow planes recorded in the manifest's `narrow_histogram_ranges` so kept rows still report them. CSV schema 4 (underflow and overflow columns, zero with `minmax`); schema 3 tables recomputed. CLI `--histogram-range`, `--robust-quantiles`. Normalization brick pinned to v0.3.1 |
| Position identity (Q20, D02) | `position_id` = image name without extension, in every CSV row and manifest file entry; FOV key = labels + `position_id`; duplicates in one leaf folder are the error `duplicate_position_id`. CSV columns and schema 2 unchanged; manifest version 1 with an optional key |
| Masks (Q32) | Optional masks folder outside the experiment, one subfolder per mask type mirroring the experiment's folders, or the experiment's folders with `<image>_<type>.tif` (both may be mixed; `ambiguous_mask` when a name could fit two images or both layouts), paired by leaf folder + `position_id`; OME-Zarr label images are masks too. Header-only errors (`masks_folder_not_found`, `masks_inside_experiment`, `mask_names_differ_in_case`, `duplicate_mask`, `ambiguous_mask`, `unreadable_mask`, `mask_not_2d`, `mask_size_mismatch`, `mask_pixel_type`) and warnings (`mask_missing`, `mask_without_image`, `mask_outside_type_folder`) in `check`/`run`; manifest key `masks` (optional, version 1). Optional mask step (`masks` command, `scan_masks`): kinds, relations with evidence, direct-parent hierarchy, `mask_relationships.json` (report version 1); default thresholds the user may change (`thresholds=` keyword, one CLI option each, checked by `mask_thresholds`, written in the report); suggestions only, the user names and confirms. SciPy core dependency. Confirmed by the owner: 2D masks at the image's YX, integer/bool pixels, masks folder outside the experiment. Next: notebook step and a file of the user's answers; tag v0.2.0 proposed after merge. Plane identity (issue #36): masks may have Z and/or T named in their own metadata (never guessed: unnamed axes are `mask_not_2d`); the masks of an image share their planes (`mask_planes_differ`) and `run` checks them against the image's confirmed roles (`mask_planes_mismatch`, `mask_plane_issues`), so a 2D mask is refused for a Z-stack or time-lapse; manifest mask entries gain `axes` and `shape` (optional keys, version 1); `read_mask(z=, t=)` reads one plane; `scan_masks` scans plane by plane |
| Notebook style | Follow the lab's NucleiSky and LabChronicle notebooks (see §7) |
| Widget widths | **99%**, never 100% |
| Paths and filenames in widget HTML and notebook text | **`<b>…</b>` / `**…**`, never `<code>` or inline code** |
| Things the user must confirm | **Large buttons** in a highlighted question box (`ConfirmToggle`), never small checkboxes |
| Statistics step | "Step 4 · Calculate the statistics (optional)": clearly optional, writes nothing. A CSV chooser (Browse…) is prefilled with the table just computed, otherwise empty. Not calculated automatically |

---

## 6. Programming style

- **Python 3.11+** (MorphoBricks decision D01). Put `from __future__ import annotations` in every module, and type
  hints on all public functions.
- **Ruff is the authority.** Line length 100. Rules E, W, F, I, B, UP, NPY and D, with
  numpy docstring convention; D105 and D107 ignored; tests are exempt from D.
  - D401 needs an **imperative first line** in docstrings of functions and methods
    ("Return…", "Build…", "Show…"). Properties may say "True if…".
  - B023: bind loop variables in lambdas (`lambda f, i=index: …`).
- **NumPy-style docstrings** (Parameters / Returns / Raises) on public modules, classes and
  functions. Module docstrings explain the module's job and point to the contract section.
- **Comments explain *why*,** not *what*. Keep the density of the surrounding code.
- **Data and state:**
  - Small **frozen dataclasses** for settings and results; no global state.
  - Mappings exposed from results are read-only (`MappingProxyType`).
  - Functions take explicit inputs and return results.
- **Determinism:**
  - Sort folders, files and outputs.
  - The same inputs give byte-identical CSVs; tests check this.
  - Write files as UTF-8 with LF line endings (`newline="\n"`), atomically (temp file,
    then `os.replace`).
- **Messages are for biologists:** short sentences, plain words, a suggested action, the
  affected paths. Use `validation.plural()` for counts ("1 image", "3 images"). User-facing
  text uses British spelling ("analysed", "normalise", "colour"); identifiers keep existing
  names (`normalization`).
- **Console output (CLI) must be ASCII-safe.** Windows consoles (cp1252) turn "→" into
  "?", so use "->" in texts the CLI prints. `cli.py` also sets `errors="replace"`.
- **No new core dependencies** without asking. tkinter (for folder dialogs) is optional
  and imported lazily with a fallback.
- **Public API** is re-exported in `__init__.py`. `__all__` is sorted: constants, then
  classes, then functions. It still lists the deprecated names moved to the normalization
  brick; they are served by `__getattr__` (see `_moved.py`), never imported in
  `__init__.py`.
- **Code from the normalization brick:** import it as
  `from morphobricks_normalization.<module> import ...`, never through the deprecated
  `morphobricks_preprocessing` paths (`tests/test_moved.py` checks this).

### Tests

- **Simulated data only**, generated in `tmp_path`: `tests/tiff_tree.py` (`write_data`,
  `write_tiff`, `make_tree`, `write_file`) and `tests/simulation.py`. Do not rely on
  example files.
- **Every acceptance check in plan §11 has a test.** A bug fix comes with a test that fails
  without it.
- **Proving "no pixels are read":** monkeypatch `asarray` on `TiffFile`, `TiffPage`,
  `TiffFrame` and `TiffPageSeries` to raise.
- **OME-Zarr test data:** `tests/zarr_tree.py` (`write_ngff`, `write_bioformats2raw`,
  `write_plate`) writes stores with zarr directly, independent of `zarr_export`; 0.5 tests
  skip with zarr 2.18.
- **Proving "one page at a time":** spy on `planes._read_page`. To prove "only changed
  files are read", spy on `run.iter_xyc_planes` (`run.iter_planes` only converts TIFF
  images to OME-Zarr). For planes in blocks, spy on
  `planes._read_segment` and make `asarray` of every tifffile class fail.
- **Planes in blocks with small test images:** the memory limit
  `plane_layout(path, mapping, memory_limit=1).block_memory_estimate` gives the smallest
  blocks a file can be read in. Consume each lazy plane (and each `XycPlane`'s channels)
  before the next one (the file closes when `iter_planes`/`iter_xyc_planes` ends). The histogram tests, with an independent copy of the
  original whole-plane formulas (`reference_histogram`), are in the normalization brick.
- **Speed:** rerun tests use 8-bit images (256 bins). Wide 65,536-bin CSVs make every run
  about 1 s.
- **Platform-specific tests** (case-sensitive file system, POSIX permissions) skip with a
  reason on Windows.
- **Notebook widgets are tested headless:** build the pages, drive them with
  `button.click()` and `.value = …`, and assert on `.value` of HTML widgets. Tests skip
  if ipywidgets/matplotlib are missing; CI installs `.[dev,notebook,zarr]`. OME-Zarr
  tests skip without zarr; CI's Python 3.11 jobs pin zarr 2.18 and 3.13 jobs get zarr 3.
- **The notebook file is tested too:**
  - valid nbformat 4, with no outputs;
  - code cells hidden and titled `# @markdown ### Run to …`;
  - Step 0 bootstraps the environment; later code cells only call `notebook_ui`;
  - the buttons it names exist;
  - its cells execute.

---

## 7. Notebook UI guidelines

The lab's standard is the notebooks in
[CellMigrationLab/nucleisky](https://github.com/CellMigrationLab/nucleisky) and
[CellMigrationLab/LabChronicle](https://github.com/CellMigrationLab/LabChronicle). They use
plain ipywidgets 8 with injected CSS and copy their helpers into each notebook; here the
helpers live in `notebook_style.py` so all steps stay consistent. The user found the first,
plainer version "not interactive enough". Descriptive, guided and forgiving is the bar.

### Notebook file (`notebooks/MorphoBricks_Preprocessing.ipynb`)

- **Title cell:** `# 🔬 **MorphoBricks | Preprocessing**`, a purpose paragraph, "What the
  notebook does", "Quick workflow" (numbered, naming every button), a "Before you start"
  checklist, a 🛡️ safety blockquote, and an example folder tree in a `text` block with
  📂/📄.
- **Per step:** `___`, then `## <emoji> **Step N: <Verb phrase>**`, then numbered
  instructions naming each button in bold. Tips are blockquotes (`> 💡 Tip:`, `> 🩹`,
  `> ℹ️`). Mark optional steps "(optional)" in the heading and say why.
- **Code cells:**
  - Hidden, with metadata
    `{"cellView": "form", "collapsed": false, "jupyter": {"source_hidden": true}, "jupyterlabHideCode": {"locked": true}}`.
  - The first line is a Colab-style title: `# @markdown ### Run to …`.
  - Outputs are stripped.
  - Step 0 is a standalone standard-library bootstrap: nearby checkout, installed package,
    or GitHub installation, with missing requirements installed using the kernel's Python.
  - Later cells are one call each, e.g. `notebook.folder_step()`.
- **Step 0** starts the notebook and shows a green "ready" card with the package version.
- **Closing cell:** "✅ After running" checklist and "Common problems" that quote the real
  message texts (check they still match the code).
- **Text:** use bold, not inline code, in the markdown (fenced text blocks such as the
  folder tree are fine).

### Page layout (`notebook_style`)

- **Embedding steps 1 and 2 (Q26, Q31):** `folder_step` and `axis_step` keep this notebook's
  words by default; another brick (MorphoRelate) passes its own `title`, `purpose` and
  `intro` (HTML; `""` leaves the box out), `show_output=False` to hide the output folder
  card (shown again when the check reports `output_inside_experiment`), and `show_next=False` to hide the "What next?" box naming this notebook's
  steps. The widgets and their behaviour are the same either way. `show_design=False`
  leaves out the "Experiment design" card: confirming the labels then writes nothing
  (no `experiment_design.csv`, no output folder), the step's texts name neither the design
  nor step 3, and step 3 runs without a design.
- **Page and cards:** `ui.page(title, subtitle, children)` wraps a step in a grey page
  (`mb-root`) with a title and a one-sentence purpose. `ui.card(header, children)` gives a
  white card with an upper-case header. `ui.intro(html)` adds a blue "what happens here"
  box at the top of each step.
- **Help text:** `ui.help_text(html)` adds a **grey help line under every control**: what
  it is, what the default does, what happens if changed.
- **Status cards:** `ui.status(kind, title, lines)` is the single status-card component.
  - Green "ok": done, next step named.
  - Blue "info": waiting or neutral.
  - Amber "warn": please check.
  - Red "error": action required.
  - Errors are phrased as fixes ("Read the messages below; each one says what to do").
    Successes end with what to do next.
- **Other building blocks:**
  - `ui.working(text)`: spinner status while something runs.
  - `ui.tiles([(emoji, value, label)])`: headline numbers.
  - `ui.table(rows, numeric_from)`: scrollable, sticky header, right-aligned numbers.
  - `ui.checklist([(done, text)])`: ✅/⭕ lists.
  - `ui.path_list(paths)`: long lists folded behind "Show N more".
  - `ui.button(…, main=True)`: full-width (99%), 42 px tall main action at the bottom of
    a step, with a Font Awesome icon and a tooltip. Button style follows meaning: success
    = do it, info = look or preview, warning = change or undo or exclude.
  - `ui.ConfirmToggle(question, explanation, confirm_label, confirmed_text, undo_label)`:
    a yellow question box with a big green "Yes, …" button. Once confirmed the box turns
    green and shows an amber "Undo: …" button. Use it for **every** "the user checked
    this" moment (labels correct, preview looks right, missing axes are single planes).
- **Paths:**
  - `ui.ask_directory()` and `ui.ask_open_file()` open native Tk dialogs (topmost). They
    return None when no dialog is possible, so show "paste the path instead".
  - `ui.clean_path()` strips quotes and smart quotes and expands `~` and environment
    variables.
- **CSS:**
  - Forced light colour scheme and a system sans-serif font.
  - `overflow-x: hidden` on boxes inside pages, so ipywidgets boxes never show stray
    horizontal scrollbars.
  - Escape all dynamic text with `html.escape`.

### Interaction rules (learned)

1. **Gate, don't surprise.** Later steps are locked until earlier ones are confirmed. Step
   3 shows a "Before you run" checklist naming the first thing still to do, and Run stays
   disabled until it is empty.
2. **Inputs reset what depends on them.** Changing the experiment folder resets the
   preflight, the confirmations and the axis roles. Changing an axis role resets the
   preview confirmation. Choosing another CSV clears the old statistics.
3. **Explain every choice live.** Each axis card shows its length, the suggested role and a
   dropdown with descriptive options ("Z - depth (focal planes)"). Under it, a sentence
   updates with the choice ("3 depth slices, numbered z = 0 to 2; each becomes its own
   plane"), and a summary says how many planes of which size each image gives.
4. **Show what will happen before it happens.** "What will happen" lists the output folder,
   the files to be written, whether an earlier run exists and what will be reread, and
   (once the axis roles are confirmed, from the headers only, cached) which images will
   be read in blocks or cannot be read within the memory limit. A
   live line under the output folder shows where results go and warns if that is inside
   the experiment.
5. **Recommended settings first.** A ticked "Use the recommended settings" hides the
   advanced controls (bins with dtype-specific bounds and help, memory limit, recompute
   everything). Unticking reveals them, and re-ticking restores the defaults.
6. **Busy discipline.** Disable the button, change its label ("Computing…", spinner
   icon), and restore it in `finally`. The progress bar names the current image. Turn the
   bar green or red at the end.
7. **Previews must be checkable.** A file picker plus z/c/t sliders (hidden for axes of
   length 1 and until the preview is shown; the middle Z slice first). The image is
   stretched for display only (0.5–99.5 percentiles), and the caption gives the file,
   coordinates, size, dtype and value range. Problems reading a plane show as a status
   card, not a traceback.
8. **Results:** tiles (planes, read, kept, new, changed, removed, excluded, checks). The
   statistics table explains its columns. A cumulative intensity plot per group (up to
   12). Unexpected exceptions become a red card with the type and message; the traceback
   goes to the collapsed "Technical details (log)" accordion, which is fed by a
   `logging.Handler` attached during each action.
9. **Optional means optional.** Say so in the title and intro, write nothing, and do not
   compute it automatically.
10. **Visual check.** Before calling UI work done, render pages with
    `ipywidgets.embed.embed_minimal_html(...)`, serve the folder with
    `python -m http.server`, and look at them in a browser. This catches overflow and
    scrollbars, awkward order, and hidden-vs-shown states. Standalone renders lack
    JupyterLab styles and icons; say so when reporting.

---

## 8. tifffile knowledge (version 2026.x)

- **Axis codes** (`TIFF.AXES_NAMES`): X width, Y height, Z depth, S sample, I sequence, T
  time, C channel, A angle, P phase, R tile, H lifetime, E lambda, L exposure, V event,
  M mosaic, Q other, J column, K row.
- **How tifffile labels a plain stack:** it depends on how the file was written. Without
  metadata, `imwrite(..., metadata=None)` gives `IYX`; with `metadata={}` (a "shaped" series)
  it gives `QYX`. Both must be assigned by the user.
- **When writing test files:**
  - Pass `photometric="minisblack"`, or 3-sample stacks become RGB.
  - RGB contiguous is `YXS`; planar is `SYX` (`planarconfig="separate"`).
  - `volumetric=True` with `tile=(z, y, x)` stores a whole Z stack in one page.
- **Series:** `tif.series[0]` is level 0; OME pyramids have `series[0].levels`. Non-OME
  SubIFD writes create a second series.
- **Planes as pages:** `series.pages[i]` may be a `TiffFrame` (OME, compressed) and may be
  None for missing OME IFDs. `page.asarray()` decodes one page.
- **Splitting a series into pages:** the shape is page-selecting leading axes followed by
  the page shape. Find the split `k` where `prod(shape[:k]) == len(series.pages)` and
  `prod(shape[k:]) == keyframe.size`, with X and Y at or after `k`. Otherwise the layout
  is unsupported (planes would span pages).
- **Encoded size:** use `keyframe.databytecounts`.
- **Byte order:** dtypes may be big-endian; normalise them with `.newbyteorder("=")`.
- **OME details:** `tif.is_ome` and `tif.ome_metadata` hold the XML.
  - Channel names: `Pixels/Channel@Name`. Significant bits: `Pixels@SignificantBits`.
  - Write them in tests with
    `metadata={"axes": ..., "Channel": {"Name": [...]}, "SignificantBits": 12}`.
  - ImageJ "Labels" are slice labels, not channel names.

---

## 9. Environment gotchas (seen on Windows)

- **Line endings:** files must stay LF. Python's `Path.write_text` on Windows writes CRLF:
  write bytes, or pass `newline="\n"`.
- **Worktrees and the editable install:** an editable install in the main checkout's venv
  imports the *main* checkout's `src`. In a git worktree, run tests with `PYTHONPATH=src`
  so the worktree code is used.
- **Encoding:** set `PYTHONIOENCODING=utf-8` when printing widget HTML (emojis) to a
  Windows console.
- **Long inline scripts:** shell heredocs with non-ASCII characters ("…", emojis) can
  break; write the script to a file and run it instead.
- **What ruff touches:** ruff also lints and formats `.ipynb` code cells and Python code
  blocks inside Markdown files (it reformatted README/user-guide examples). Run
  `ruff format .` before `ruff format --check .`.

---

## 10. Commands

```bash
python -m venv .venv
pip install -e ".[dev,notebook,zarr]"
ruff check .
ruff format --check .
pytest
morphobricks-preprocessing check /data/exp1
morphobricks-preprocessing run /data/exp1 --axes ZCYX
morphobricks-preprocessing masks /data/exp1 --masks /data/exp1_masks
jupyter lab notebooks/MorphoBricks_Preprocessing.ipynb
```

---

## 11. Status (October 2026)

- **Done:** Milestones 0–5 (contract, scanner and validator, axis review, histogram engine,
  CSV and reruns, notebook + CLI + user guide), the notebook redesign plus feedback
  round (PRs #6 and #7), OME-Zarr input with optional TIFF -> OME-Zarr conversion
  (PR #5), planes over the memory limit read in blocks (PR #12, plan §12 question 15),
  naming those images before the run (PR #13, question 16), plane coordinates always
  written (PR #14, question 17), OME-NGFF label readers (PR #15, question 18), and
  follow-up P1 of the normalization plan: the dependency on `morphobricks-normalization`
  v0.1.0 with deprecated re-exports, its version in the manifest, Step 0 checking for it,
  and contract §3–§7 moved to the normalization contract (PRs #16 and #17, question 19),
  and `position_id` filled per cross-brick decision D02 (question 20), and follow-ups
  P2 (dataset consistency warnings, question 21) and P3 (experiment design, question 22),
  and P3b (`plane_histograms.csv` schema version 3 with the channels in columns, question
  27).
- **Input support for MorphoRelate milestone M3:** folders named `single` read as a
  one-member level (question 23), a unit per axis in `PhysicalSizes` and physical sizes in
  `inspect_image` (question 24), OME-Zarr physical sizes from the OME-NGFF scale (question
  25), and notebook steps 1 and 2 embeddable with their own words (question 26).
- **Released as v0.1.0** (27 October 2026), with everything above; the normalization brick
  is pinned to its v0.2.0 (question 28).
- **Released as v0.2.0** (27 October 2026, question 33): follow-ups P4 (the image type,
  question 29) and P5 (intensity checks and plots, question 30) of the normalization plan
  (PRs #28 and #29), step 1 embeddable without the experiment design,
  `folder_step(show_design=False)` (question 31, PR #27), and masks: pairing every image
  with its masks, header checks, the manifest's `masks` key, and the mask step with
  `mask_relationships.json`, as library, command line and documentation (question 32,
  PR #26). The normalization brick stays pinned to its v0.2.0.
- **Released as v0.3.0** (28 October 2026, question 35): follow-up P6 of the
  normalization plan (question 34, PR #31): the histogram range in step 3 and the
  `narrow_histogram_range` warning, `plane_histograms.csv` schema version 4, the
  normalization brick pinned to its v0.3.1.
- **v0.3.1** (to be tagged, question 36): `discovery.position_id`, the one function every
  MorphoBricks brick names positions with (D85), also removes `.png`, `.jpg`, `.jpeg`,
  `.bmp`, `.gif` and `.npy` (naming only) and refuses a name that is only an extension
  (PR #33). Outputs unchanged.
- **Next (not started):** the notebook step for the masks (name each mask, confirm or
  correct the relationships) and a file saving those answers for the other bricks
  (question 32); 3D and time-lapse masks later.
- **Not yet verified:**
  - the exact look of the notebook in a real JupyterLab;
  - the optional `jl-hidecode` JupyterLab extension, which keeps code cells collapsed. It
    is mentioned in the user guide but is not a dependency.
