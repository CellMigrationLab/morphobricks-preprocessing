# MorphoBricks Preprocessing Brick — Implementation Plan

**Status:** design brief, revised after the binning study of October 2026
**Scope:** folder validation, TIFF and OME-Zarr metadata review, per-plane intensity
histograms and the histogram CSV, computed with the normalization brick's library
(`morphobricks-normalization`, question 19), which later bricks also use. OME-Zarr input
and the optional conversion of TIFF images to OME-Zarr need the `zarr` extra (§10, §12
question 12).
**Out of scope for this milestone:** applying normalization (done by the patch-extraction
brick), spatial correction methods.

The exact rules (folder layout, binning formulas, CSV columns, statistic definitions,
validation severities) are in [contract.md](contract.md). Coding conventions are in
[development.md](development.md).

## Instructions for implementation

Build a reusable preprocessing component for MorphoBricks. Keep image-processing and
validation logic in importable Python modules. Use a notebook with widgets for the
biologist-facing review flow, with an optional CLI wrapper over the same functions. Do
not place core logic only in notebook cells.

Preserve the shared MorphoBricks metadata contract. Do not silently infer experimental
labels, axis roles, or intensity transformations. Never modify source images. Present
validation messages in plain language and include the affected paths and a suggested
action.

## 1. Goals

The brick must:

1. Accept one selected experiment folder.
2. Validate and preview the folder hierarchy.
3. Inspect TIFF and OME-Zarr metadata, allow axis confirmation, and preview the resulting
   XY plane.
4. Read multidimensional TIFF and OME-Zarr data as individual 2D planes; optionally convert
   TIFF images to OME-Zarr first and process the copies.
5. Create one histogram per plane, with exact extrema and pixel counts.
6. Write a self-describing CSV that later bricks use to calculate normalization
   parameters for any group of planes.
7. Support incremental updates without rereading unchanged images.
8. Read planes too large for the configured in-memory limit in blocks, with exactly the
   same results, and fail clearly only when a plane cannot be read within the limit even
   in blocks.
9. Provide the normalization methods (min-max, percentile range, z-score, robust
   z-score) as an importable library, without applying them.

## 2. Folder hierarchy

The selected directory is the experiment root:

Experiment root → biological condition → biological repeat → technical repeat → images

Images are TIFF files (`.tif`, `.tiff`) or OME-Zarr images: folders whose names end in
`.zarr` (usually `.ome.zarr`). An OME-Zarr folder counts as one image, not as a subfolder.
TIFF and OME-Zarr images may be mixed in one experiment.

Folder names are arbitrary labels and need only be unique among siblings; since D86 (question
38) a name is spelled one way, in one case, at its level across the experiment. Each branch may
stop at any level; omitted levels receive the value `single` (a level with one member,
question 23). Levels cannot be skipped. Labels are scoped to their parents: `bio_rep1`
under two conditions are two different samples, so groups are always identified by the
full label path.

A folder containing images and no subfolders is a leaf folder. Only images directly
inside leaf folders are analysed. If the experiment root itself contains images and has no
subfolders, it is the leaf folder and every label is `single`.

During preflight, show a tree/summary of the inferred labels and ask the user to confirm
or correct it. A folder may be named `single` (lower case) when it is the only folder in
its place: it is then a level with one member, labelled `single` like a missing level
(question 23). Spelled in another case, or beside other folders, it is an error. Report
warnings for files that are not images and empty folders, and explain that they will not be
analysed. Images in a folder that also holds subfolders are an error (`tiff_outside_leaf`;
owner decision, October 2026: every brick refuses such a folder).

## 3. Image metadata and axis review

Inspect headers before loading pixels. Record the relative path and file name,
series/page count, shape, dimensionality, dtype and bit depth, axis labels from metadata
when available, and whether the file can be opened and its layout is supported.

Supported axis roles are X, Y, Z, C and T. Require exactly one X and one Y; roles must be
unique. Unsupported axes (position/scene) block processing in this milestone. Prefill axis
selectors from metadata, let the user edit them, show a small preview of the resulting XY
plane, and have the user confirm one mapping for the experiment. X and Y are locked once
the experiment mapping is confirmed. The RGB samples axis (`S`) is prefilled as C when the
file has no other channel axis. Axes that metadata marks only as a generic sequence are
left unassigned for the user to choose; they are never guessed.

Only the first image series is used, at full-resolution level 0 for pyramidal files. A
file with more than one image series (several positions or scenes) is an error in this
milestone.

OME-Zarr images (OME-NGFF 0.4 and 0.5) declare their axes (time, channel, space), so their
roles are taken from that metadata rather than guessed. The experiment mapping is defined
on the TIFF arrangement when there are TIFF files; OME-Zarr images with the same
arrangement use it, and others are matched role by role: their declared roles must fit the
experiment's, and experiment roles they lack are read as length 1 only after confirmation.
Only level 0 of a single image is read; a store with several images (a bioformats2raw
folder with several series, a plate or a well with several fields) is an error, like a
multi-series TIFF.

Files with a conflicting number or arrangement of dimensions block processing until
resolved. Different dimension lengths are allowed with a warning. A file missing an axis
that other files have is treated as a singleton only when that is unambiguous and the user
confirmed the mapping.

## 4. Dtype and histogram policy

Mixed dtypes in one experiment block processing before any pixels are read. Never convert
source values. Supported types: signed and unsigned 8-, 16-, 32- and 64-bit integers,
float32 and float64. Boolean, complex, float16 and other sample formats are rejected with a
clear message.

**Every plane is binned over its own observed range**, using the same bin count for all
planes of an experiment:

- **Integer planes:** the values `pixel_min .. pixel_max` are split into bins with
  whole-number limits whose widths differ by at most one value. When the range fits
  within the bin count, every value has its own bin and the histogram is exact.
  Default bin count: one per possible value for 8- and 16-bit types (256 and 65,536),
  65,536 for wider types. Allowed: 1 to min(65,536, number of representable values).
- **Float planes:** the finite range `[pixel_min, pixel_max]` is split into equal-width
  bins (half-open, last bin closed). NaN and ±infinity are ignored and counted.
  Default 10,000 bins, allowed 2 to 65,536.
- A constant plane puts every pixel in bin 0. A plane with no finite pixels has blank
  extrema and zero counts and is excluded from group statistics; if no plane has finite
  pixels, processing stops with an error.

Why per-plane ranges: a plane's histogram depends only on its own pixels, so adding images
never changes existing rows (no shared range to recalculate), and the bins sit where the
data is. In the binning study, per-plane ranges cut percentile errors 5–30× compared with
binning over the full dtype range at the same bin count.

The representative value of a bin is the midpoint of the integers it holds (integer
planes) or of its edges (float planes). No pixel is further than half a bin width from
its representative, which bounds every percentile error (see contract §4).

## 5. Processing passes and memory

**Pass 1, preflight:** walk the tree, identify leaf folders and candidate images (TIFF
files and OME-Zarr folders), inspect headers and OME-NGFF metadata, detect dtype/axis
conflicts, and collect what the confirmation UI needs. Do not
read every pixel (the axis preview reads one sample plane per distinct layout).

**Pass 2, histograms:** after confirmation, read one 2D plane at a time; compute its exact
finite extrema, pixel counts and histogram in a single read. Never load a full Z/C/T volume
when only a plane is needed. A row of the table is an XYC plane: its channels are read one
after another, and their histograms make the row (question 27).

Estimate each plane's working memory before loading it, including the decoded I/O unit,
temporary arrays and the finished histograms of the other channels of its XYC plane. Use a configurable limit (default 1 GiB). A plane whose estimate is over
the limit is read in blocks instead (question 15): rectangles of the pieces the image is
stored in (TIFF strips or tiles, OME-Zarr chunks), as large as the limit allows, so the
memory limit keeps its meaning as the most one step may use. Uncompressed TIFF pieces and
OME-Zarr chunks may also be read a few rows at a time. Each plane's histogram stays exact:
a first pass over its blocks finds the exact extrema and a second pass counts the pixels
into bins with the same formulas, so the row equals the row of the whole plane (8- and
16-bit integer planes need only one pass, counting every possible value). The switch is
never silent: the notebook's "What will happen" box names the images before the run
(question 16), the run reports them as a warning and the manifest records it. A plane
stored compressed in one piece must be decoded whole and cannot be split; it, and any plane
whose smallest block is still over the limit, stops the run with a clear error. Never
convert source values.

## 6. Output CSV contract

Write one row per XYC plane (every channel of an image at one Z and T position) to
`plane_histograms.csv`, schema version 3 (question 27). The default output folder is a
sibling of the experiment folder named `<experiment>_MorphoBricks_Preprocessing`; the user
may choose another, but never one inside the experiment folder or a raw image folder.

Columns, in order: `position_id, image_file, z, t, condition, biological_repeat,
technical_repeat, source_relative_path, plane_id, dtype, bit_depth, channel_count,
total_pixel_count, bin_count, schema_version`, then for each channel `i`
`Intensity_MinIntensity_C<i>, Intensity_MaxIntensity_C<i>,
Histogram_ValidPixelCount_C<i>, Histogram_BinningMin_C<i>, Histogram_BinningMax_C<i>`
and `Histogram_Count_C<i>_00000` onwards. Details in contract §5 and the normalization
brick's contract §6.2. `position_id` is the image name without its extension (question
20).

The wide CSV is for computational use; spreadsheet programs may not open all columns. The
package provides `read_plane_histograms()`, which keeps labels as text. Alongside the CSV,
a run manifest (`run_manifest.json`) records the confirmed labels and axis mapping, the
settings, package versions and each source file's size and modification time. It also
records each file's channel names and significant bits when metadata provides them, for
information only; channel names that differ between files at the same channel index are
an error (question 1, revised after the data-flow audit, issue #37).

## 7. Incremental updates

Use `plane_id` as the row key and the manifest's file size and modification time to detect
new and changed files without reading unchanged pixels. Provide a force-rebuild option.

On rerun: add rows for new planes; replace rows for changed files or changed axis/bin
settings; remove rows for deleted or renamed files (a rename is a delete plus an add).
Existing rows of unchanged files are never recomputed, because each row depends only on
its own plane. Prepare the CSV and manifest in a temporary folder on the output filesystem
before replacing either output. Back up earlier files before publication; if replacing
either file fails, restore the earlier pair (or remove newly created files on a first run).
If restoration itself fails, keep the backups and report their location for manual recovery.
Temporary-file cleanup failures are logged without treating a completed run as failed.

The two replacements are not a single filesystem transaction. If the process is forcibly
terminated between them, the CSV hash check on the next run detects the mismatch and
rebuilds the output. Runs writing to the same output folder must be performed one at a time.

A file is unchanged when its size, modification time, labels and axis layout match the
manifest and the table still holds all its rows. Every row is recomputed when there is no
readable earlier manifest, when a rebuild is requested, or when the pixel type, bin count
or axis mapping changed. The manifest stores the SHA-256 of the table it was written
with, so a table edited or replaced afterwards is rebuilt rather than trusted. Changing
only the memory limit or the exclusions recomputes nothing.

The user may exclude images explicitly by relative path, for example a damaged file, and
continue. Excluded images are not read, are reported as a warning, are listed in the
manifest, and lose any rows they had.

## 8. Validation behavior and user messages

Block processing for: mixed dtypes; unreadable TIFF files or OME-Zarr folders in leaf
folders (or OME-Zarr images without the `zarr` extra); OME-Zarr images whose declared axes
do not fit the experiment's roles; unsupported sample
formats or axes; missing/duplicate X or Y, duplicate roles or inconsistent mapping; a plane
that cannot be read within the memory limit, not even in blocks; a folder named `single`
in another case or beside other folders; sibling folders whose names differ only in
case; a folder below the technical-repeat level; images in a folder that also holds
subfolders; an output folder inside the experiment; no usable planes. System files (dot-files, `Thumbs.db`, `desktop.ini`, `__MACOSX`) are
ignored silently.

Continue with warnings for: files that are not images; empty folders;
non-finite pixels (ignored and counted); different image sizes with a valid axis
interpretation; planes with no finite values while others remain usable; planes over the
memory limit that are read in blocks.

Show a preflight summary (files to analyse, files/folders skipped, dtype, axis mapping,
warnings). Never show raw stack traces as the primary message; keep technical details in a
diagnostic log.

## 9. Group statistics and normalization

This brick does not apply transformations. The code below moved unchanged to the
normalization brick (`morphobricks_normalization`, question 19); this package re-exports
it, deprecated, for one version. It provides:

- **Pooling** (`PooledHistogram`): combines any selection of planes (an image, a technical
  repeat, a biological repeat, a condition, or any custom set) by merging their
  (representative value, count) pairs. Mean, standard deviation, percentiles, median and
  MAD are computed from the pooled counts; minimum and maximum are exact from the stored
  extrema. Per-plane statistics are never averaged: the study showed averaging
  underestimates the standard deviation by up to 22% and the 99.9th percentile by up to 36%
  at condition level, while pooled histograms stay within half a bin width.
- **Normalization parameters** (`min_max`, `percentile_range`, `z_score`,
  `robust_z_score`), each an offset and a scale, and `NormalizationParameters.apply()` for
  the patch-extraction brick to use.

Spatial methods (flat-field, BaSiC, rolling-ball, CLAHE) are outside this brick.

## 10. Architecture and milestones

The core is importable and independent of widgets: one module per job (folder discovery,
TIFF metadata, axes, histograms, CSV I/O, manifest, validation, pooling, normalization,
API, CLI). The notebook and CLI only call the API.

- **Milestone 0 — Contract:** folder rules, axis rules, histogram semantics, CSV schema,
  validation severities ([contract.md](contract.md)). *Done.*
- **Milestone 1 — Scanner/validator:** folder tree, TIFF headers, inferred hierarchy,
  dtype, axes, actionable validation results. *Folder discovery, header inspection and
  dtype/dimension checks implemented; axis-role checks follow with the axis review.*
- **Milestone 2 — Axis review UI:** metadata-prefilled selectors, plane preview, confirmed
  mapping saved to the manifest. *Implemented: axis mapping, prefill and validation
  (`axes`), plane-by-plane reading with a memory limit and the preview plane (`planes`),
  and the axis review step of the notebook.*
  *Review fix: confirmed singleton axes clear the corresponding readiness error while
  folder, exclusion and other metadata errors continue to block the run.*
- **Milestone 3 — Histogram engine:** per-plane histograms, extrema, non-finite and
  constant planes, pooling, normalization library. *Core implemented and tested.*
- **Milestone 4 — CSV and reruns:** write/update the CSV and manifest, detect
  new/changed/deleted planes, force rebuild. *Implemented: run manifest (`manifest`),
  preflight and incremental runs with exclusions (`run`).*
  *Review fix: stage both files, preserve earlier output on write/publication failures,
  and keep backup files with a recovery message if restoration fails.*
  *v0.4.0 (PP-7, question 48): `restart`/`full` modes replace `force_rebuild`; later runs
  reuse every recorded input; an `O_EXCL` lock guards the output folder; every run writes
  `discovery_report.json` and `.txt` (contract §9.2).*
  *v0.4.0 (PP-8, question 49): stated calibration in the output folder's `calibration.csv`
  (experiment, folder or image; a size or `pixels`), recorded per axis with its source;
  the pixel-size checks run on the resolved sizes; CLI `calibrate` (contract §9.3).*
  *v0.4.0 (PP-8b, questions 50 and 51): a mask whose header names X before Y is refused by
  the run (`mask_layout`); `RunInputs.open` refuses an experiment holding images or masks
  the run did not record (`UnrecordedFilesError`, contract §9.1).*
- **Milestone 5 — Integration and docs:** public API, notebook, CLI, user documentation.
  *Implemented: summaries (`report`), notebook widgets (`notebook_ui`,
  `notebooks/MorphoBricks_Preprocessing.ipynb`), `check`/`run` command line (`cli`) and the
  [user guide](user_guide.md).*
  *Step-4 feedback: show an animated spinner through table loading, statistics and plotting;
  disable the calculation button while busy and restore it on success or failure.*
  *Setup feedback: standalone Step 0 detects the environment, prefers a nearby checkout,
  reuses an installed package, or installs missing requirements into the active kernel.*
  *Step-1 feedback: centre the folder-label table's headers and contents consistently.*
  *v0.4.0 (PP-8d, question 58): the notebook runs on Google Colab and in JupyterLab following
  LabConstrictor's portability guide; its GitHub installation is pinned to a release tag.*
  *Notebook guidance feedback: separate "What next?" boxes show the next action in larger
  text, following the current confirmations. Step-1 message details are collapsed with
  persistent orange warning and red error counts. Step-3 conversion settings use an
  expandable panel with separate storage, compression and preview-resolution cards.*
- **OME-Zarr (optional `zarr` extra):** OME-Zarr images (OME-NGFF 0.4 and 0.5) are read
  as input next to TIFF files, plane by plane from level 0, with their declared axes
  (`zarr_input`). *TIFF to OME-Zarr conversion, described next, was removed in v0.4.0
  (question 46); TIFF images are read directly.* For TIFF images the user chose once per experiment: read them
  directly, or convert each to `<output>/ome_zarr/<relative path>.ome.zarr` (OME-NGFF 0.4)
  and compute its histograms from the copy, like any OME-Zarr image (`zarr_export`). The
  copies keep the source values at level 0; smaller levels (2 x 2 means) are for viewers.
  They follow the incremental rules of §7 and are published with the table and manifest,
  so a failed run leaves the earlier copies in place. In the notebook the choice and the
  conversion settings (chunk size, compression, pyramid levels) are in step 3's
  "(Optional) Image conversion" card. *Implemented, see questions 12 and 13.*
- **Large planes in blocks:** planes over the memory limit are read in blocks of their
  stored strips, tiles or chunks, sized by the memory limit (`planes`), and their
  histograms are computed in two passes over the blocks with results identical to whole
  planes (`histogram.compute_block_histogram`). Up to v0.3, OME-Zarr copies of such planes were written
  block by block, with their smaller levels built from the stored level above
  (`zarr_export`). The axis preview shows them reduced. Step 3's "What will happen" box
  names these images before the run. *Implemented, see questions 15 and 16.*
- **Normalization brick (follow-up P1):** the histogram, bin-count, record, CSV, pooling
  and normalization modules come from `morphobricks-normalization` v0.1.0, a required
  dependency; the old names stay importable here with a `DeprecationWarning` for one
  version. Outputs are unchanged. *Implemented, see question 19.*
- **Position identity (cross-brick decision D02):** every row and every manifest file
  entry carries the image's `position_id`, so `condition`, `biological_repeat`,
  `technical_repeat` and `position_id` identify a position in every brick; images in one
  leaf folder with the same identifier are refused. *Implemented, see question 20.*
- **Dataset consistency (follow-up P2 of the normalization plan):** the folder check counts
  images per condition and biological repeat, warns about unbalanced counts and repeats
  missing from some conditions (`dataset`), and compares the headers' plane counts and
  physical pixel sizes (`metadata`, `physical_sizes`); the notebook shows them, with the
  earlier consistency issues, in step 1's "Dataset consistency" panel. *Implemented, see
  question 21.*
- **Experiment design (follow-up P3):** after the labels, step 1 pairs biological repeats
  into repeat groups across conditions, asks for each group whether it is comparable, and
  records control conditions, in `experiment_design.csv` (`design`); reruns with the same
  folders skip the questions, the command line uses the file or writes a template, and
  the manifest records its hash. *Implemented, see question 22.*
- **Input support for MorphoRelate milestone M3 (released in v0.1.0):** a folder named `single`
  read as a one-member level, physical sizes with a unit per axis in the image header
  (TIFF and OME-Zarr, from the OME-NGFF scale), and steps 1 and 2 of the notebook
  embeddable by another brick. Outputs are unchanged. *Implemented, see questions 23 to
  26.* In v0.2.0, step 1 also embeds without the experiment design
  (`show_design=False`). *Implemented, see question 31.*
- **Channels across columns (follow-up P3b of the normalization plan):**
  `plane_histograms.csv` schema version 3, written with the normalization brick's
  Milestone 2b (now pinned to its v0.2.0, question 28): one row per XYC plane with each channel in its own
  columns; its channels read one at a time (`planes.iter_xyc_planes`); images with
  different numbers of channels are an error before any image is read; earlier tables are
  recomputed on the next run. *Implemented, see question 27.*
- **Image type (follow-up P4 of the normalization plan):** after the axis roles, step 2
  asks whether the images are fluorescence or RGB, suggesting RGB for TIFF images stored as
  RGB colour on the channel axis (`image_type`, `metadata.TiffMetadata.rgb_samples`); RGB
  needs three channels of an integer type. The manifest records the type; the command line
  takes `--image-type` or prints the suggestion. Outputs are otherwise unchanged.
  *Implemented, see question 29.*
- **Intensity checks (follow-up P5 of the normalization plan):** after every successful
  run, and in step 4 for any table, the normalization brick's `image_summaries` and
  `intensity_checks` give the four intensity warnings from the table alone (`intensity`);
  the notebook shows them with adjustable limits and per-channel box plots and
  distributions, the command line prints them, and the manifest records the limits.
  *Implemented, see question 30.*
- **Masks (MorphoFeature decision D78):** every image paired with its masks from a masks
  folder outside the experiment (or the label images of OME-Zarr images), checked from the
  headers (`masks`); an optional mask step reads them and suggests each mask type's kind
  and the relationships between types in `mask_relationships.json` (`mask_scan`); the
  manifest records the masks. Library, command line and documentation; the notebook step
  follows. *Implemented, see question 32.*
- **Release v0.2.0:** P4, P5, step 1 without the experiment design and the masks.
  *Released, see question 33.*
- **Histogram range (follow-up P6 of the normalization plan):** step 3 offers the
  normalization brick's robust histogram range besides each plane's minimum to maximum,
  every plane is binned with its range check, and a `narrow_histogram_range` warning names
  the channels whose central range covers too few bins; `plane_histograms.csv` schema
  version 4. *Implemented, see question 34.*
- **Release v0.3.0:** P6, with the normalization brick's v0.3.1. *Released, see question
  35.*
- **Release v0.3.1:** `position_id` names an image with the other image extensions bricks
  read (MorphoBricks D85, PR #33). *To be tagged, see question 36.*
- **PP-10 (notebook cards):** split into PP-10a (the cards) and PP-10b (the PP-9 debt below),
  two sequential PRs.
  **PP-10a** *(implemented, PR #120)*: the notebook catches up with PP-7, PP-8, PP-8b and
  PP-8c, with the functions the command line uses (contract §9.2, §9.3, §12):
  - **Check folder** writes the report and the design template like `check`
    (`run.check_step.suggested_review`, now shared with the CLI, and `write_check_outputs`),
    and shows the report; when an embedding brick hides the output folder
    (`show_output=False`) the check writes nothing, and without the design card it writes
    the report only.
  - **Pixel sizes** card: each image's sizes with their source; statements per target, axes
    and unit through `calibration_entry`/`update_calibration` under the lock, the file read
    again with `run.preflight.recalibrated` (no new check, answers kept); the calibration
    messages on the card. Z and T are shown as found until the roles are confirmed, then
    fitted to them (`fit_calibration`): "no such axis" for an axis an image lacks.
  - **Experiment design** answers through `apply_design_edit` (the "Undo" through the new
    `DesignEdit.unconfirm`), saved with `run.design_file.write_design` under the lock; a
    refused answer is shown and changes nothing; the template note says nothing is
    confirmed. The "Cannot save the design" warning is logged inside `log.attached()`, so
    it reaches "Technical details".
  - Step 3: a **Mode** choice (restart/full, with what both keep) replaces "Recompute every
    row" (no longer reset by "Use the recommended settings"); "What will happen" lists the
    input changes the run would report (`run.planned.planned_changes`; the run's design
    lookup moved to `run.design_file.design_for_run` for it); after the run the status
    names the mode, the files replaced and the changes, and the report is shown; a stopped
    run says it wrote its report (question 55), a refused one "Nothing was written."
  - The `output_locked` card names the holder (computer, process ID, start time) with a
    **Remove the lock of a stopped run** button (owner, 2 November 2026, PP-8d review): same
    rule as `--remove-stale-lock`, only on that click, never the lock of a process still
    running on the same computer; it explains the Google Drive limit (one session per
    output folder). The "Common problems" code cell is replaced by the button.
  - The preview drawn under roles still being edited is labelled "Preview of the roles
    above, not confirmed yet" (nothing is written); after confirmation "Preview with the
    confirmed axis roles".
  - The PP-8b refusals (`UnrecordedFilesError`, the zarr refusals of `RunInputs.open`) do not
    surface in the notebook, which never opens `RunInputs`; `zarr_not_installed` is a
    folder-check error with its install command, shown with the other messages.
  - Touched functions end within §9: `_build_run_step` and `run` are split, and the plan
    moved to `notebook_run_plan`.

  **PP-10b** *(implemented, the PR after #120)*: the debt PP-9 left (a move-only split):
  - UI modules no longer compute (§9 layers; audit finding D6):
    `notebook_plots.distribution_png` calls the owner `intensity.cumulative_share` instead of
    its copy; `notebook_render.preview_caption` takes the value range from
    `notebook_values.value_range` and `plane_summary_html` the plane counts from
    `notebook_values.plane_counts` (pure); the plan's layouts come from
    `run.planned.plane_layouts` and `memory_plan` (orchestration), the UI keeping only the
    cache and the words (`_plan_layouts`/`_memory_plan` moved to `notebook_run_plan` first,
    move only).
  - Every notebook function is within §9: `_build_folder_step` (140 executable lines),
    `IntensityPanel.__init__` (110), `_build_results_step` (92), `_refresh_axis_step` (90)
    and `_build_axis_step` (82) are split into builders of one card each (PP-10a did
    `_build_run_step` and `run`), and every private notebook function over 10 lines has a
    docstring: the baseline holds no notebook entry any more.
  - `PYRAMID_OPTIONS`, public but unused since `convert_tiffs` was removed (v0.4.0, PP-2),
    is removed from `notebook_options` and `notebook_ui.__all__`; migration-guide note for
    PP-13: nothing replaces it.
- **PP-10c (the notebook's masks field)** *(implemented, PR #122; owner decision of 2 November
  2026: before PP-11)*: design v0.4 §2 "the notebook has a masks field, with mask-axis
  statements per mask type" (contract §9.2 "Notebook masks", §15):
  - Step 1 has an optional **Masks folder** (Browse… as the experiment field, with the Colab
    "Copy path" message); **Check folder** pairs it with `preflight(masks_folder=,
    mask_axes=)`, as `check --masks`. The masks folder and statements are those the run
    would use (`run.planned.planned_masks`: given, else recorded); the **Masks** card shows
    each type (`mask_review.mask_type_rows`), the images without one and the masks' issues
    (by the confirmed roles once there are any: `mask_checks.mask_issues`, as `run`).
  - **Mask axes** per type, asked only where needed (`mask_review.mask_axes_questions`:
    unnamed axes or a statement that does not fit; 2-D masks of multi-plane images once the
    roles are confirmed, answered `YX`), offering only the statements `mask_roles` accepts
    for every mask of the type; nothing preselected, nothing applied until **Check the
    masks** (`run.preflight.remasked`, other answers kept). `MaskFile.named_axes` is new,
    and a mask whose roles are refused keeps its header axes and shape, so the choices can
    be worked out.
  - The run is given the same masks folder and mask axes; **What will happen** names them
    and the changes (`planned_changes` stays equal to the run's `input_changes`, tested);
    **Drop the recorded masks** is `--no-masks`. Step 3 is gated on masks without errors and
    a pairing matching the choices.
  - Fix found on the way: `--no-masks` (`masks_folder=False`) after a run with stated mask
    axes was refused with `invalid_mask_axes`; it now drops the statements recorded for the
    masks folder's masks (label-image statements are kept).
  - Embedding: `folder_step(show_output=False)` shows neither card and pairs no masks
    folder (MorphoRelate has its own masks card); the embedding API is unchanged.
  - Not in this PR: a notebook button for the `masks` step (`mask_relationships.json`); it is
    PP-10d.
- **PP-10d (the notebook's masks-step button)** *(implemented, PR #123; owner decision of 2
  November 2026: right after PP-10c, before PP-11)*: a notebook button for the `masks` step
  (describe the masks: `mask_relationships.json`; contract §9.2 "Notebook mask step", §15):
  - Move only, first: the CLI's pairing with the recorded inputs, scan and write under the
    lock leave `cli_masks.masks_command` for `run.mask_step` (orchestration):
    `prepare_mask_step` (a `MaskStep`) and `describe_masks` (a `MaskStepResult`, the lock
    refusal returned rather than raised). Review (coordinator decision): the lock is held
    from before the first mask is read to the write, so a locked folder is refused before
    any mask is read, in the command and the notebook. The command only prints; its output is unchanged
    (same text and exit status over 13 scenarios, the HPMEC data included).
  - Step 3's **Describe the masks (optional)** card (`notebook_mask_step`, ui): enabled when
    step 1's output folder holds a v2 manifest that recorded masks (`recorded_masks`), after
    a run in this session or for an earlier run in a reopened notebook (review, coordinator
    decision); the help line names the run it describes; runs the step with the run's
    recorded inputs and the default thresholds (the notebook offers no threshold option;
    the command line does), with a progress bar; shows the mask types, the relationships
    with their counts, masks of the same objects and the hierarchy as suggestions to check,
    and the path of `mask_relationships.json`. Refusals use the existing cards (the lock
    card with **Remove the lock of a stopped run**, red cards for mask errors and masks that
    cannot be used). Hidden with `folder_step(show_output=False)`; the pinned embedding API
    is unchanged.
  - The masks' texts and the new table rows moved from `report` to `mask_text` (pure), so
    `report.py` stays within §9's 600 lines.
- **PP-11 (named-axis OME-TIFF copies, the exception path)** *(implemented, this PR; design
  v0.4 §5; contract §17; question 59)*:
  - `convert_experiment` (`conversion`, orchestration) and the CLI `convert EXPERIMENT --to
    FOLDER --axes ROLES`: images with an axis error or unnamed axes are written as
    named-axis OME-TIFF with the confirmed roles (`copy_axes`, pure; `ome_tiff_copy`, io:
    planes from `iter_planes`, in blocks over the memory limit, each block written in
    place; BigTIFF when large); every other image copied byte for byte; masks never
    converted. Any error other than the axis errors stops it before anything is written.
  - Pixel sizes follow the roles; a relabelled axis gets an unknown scale, except that a Y
    or X relabelled as the other keeps the header's size when X and Y are exactly equal
    (owner, question 59 (1)); never exchanged; channel names kept when C keeps its role.
  - Safe: the copy tree's lock; the planes checked against the memory limit and the free
    space before anything is written; `.partial` files moved into place together once all
    are written (any failure or interruption leaves the folder as it was); unchanged copies
    (source, roles, axes and pixel sizes as recorded) reused, anything else refused
    (`copy_differs`, `copy_folder_not_empty`); the conversion record (`conversion_record`,
    `.morphobricks-conversion.json`) written last, and kept as it was when every copy is
    reused. The messages are in `conversion_issues` (pure).
  - The run on the copy tree: `conversion_record_sha256` in the manifest, `conversion` in
    the report, the warnings `conversion_source_changed` and `copy_changed_after_conversion`
    (`review_conversion`, from `preflight`), every row recomputed (the read path changes);
    `RunInputs.open` refuses a record changed or gone since the run (question 59 (14)).
  - The axis messages (`axes_yx_swapped`, `axes_contradict_header`, `axes_x_before_y`,
    `axes_not_named`) and the notebook's axis marks name the `convert` command.
  - The notebook card for the conversion followed in PP-11b.
  - Review (2 November 2026): the obsolete issues of `convert_tiffs`/`zarr_export` had
    acceptance criteria kept here: a memory budget that fails fast (#76), strict settings
    (#80), no 64-bit precision loss (refused, #75), the copy's fingerprint and scales
    checked before reuse (#87, #88, #92), no mixing of metadata epochs (#96); the run reads
    the copies themselves, so the table describes the persisted pixels (#79).
- **PP-11b (notebook card for the conversion)** *(implemented, this PR; owner decision of
  2 November 2026, plan §12 question 59 (15))*: design v0.4 §5 "the notebook offers
  conversion only for files with errors" (contract §17 "Notebook"):
  - Step 2's **Make named-axis copies (optional)** card (`notebook_convert`, ui), right
    under the axis check, shown when it reports `axes_yx_swapped`, `axes_contradict_header`,
    `axes_x_before_y` or `axes_not_named` for the roles being edited; hidden with
    `folder_step(show_output=False)`; the pinned embedding API is unchanged.
  - The roles: those confirmed with **Confirm axis roles**, or, while the axis errors keep
    that button disabled, the roles shown once the preview is checked and the card's
    **Yes, write the copies with these roles** is pressed; changing a role or the
    missing-axes answer takes it back. Nothing is taken from the headers.
  - A required folder for the copies (Browse…, the Colab **Copy path** card); the plan from
    `preview_conversion` (in `conversion_plan`, beside the command's `plan_conversion`
    (PP-11c), with the folder rule `copy_folder_issues` moved there from `conversion`,
    plus the space needed and free; writes nothing): images converted (with their code) and
    copied byte for byte, the space; errors keep the button disabled.
  - **Make named-axis copies** calls `convert_experiment` with a progress bar; refusals on
    the existing cards (the lock card, now naming the folder for the copies, with **Remove
    the lock of a stopped run**).
  - After the copies: the record's path and **Check the copies in step 1** (the experiment
    folder becomes the copy tree, the output folder is kept and filled in, and the folder is
    checked again); the card says the missing-axes answer must be given again and lists the
    copies whose relabelled Y/X sizes are unknown (`ConversionResult.unknown_sizes`, new), to
    state under **Pixel sizes**.
- **PP-11c (conversion integrity)** *(implemented, PR #126; issues #80, #87, #88, #92,
  #96; question 59 (16)-(18))*: `conversion.py` split into `conversion_plan`,
  `conversion_reuse` and `conversion_write` (move only); the copies' SHA-256 in the conversion record (version 2,
  `content_hash`) verified at reuse, by `review_conversion` and by `RunInputs.open`
  (`conversion_check`); channel names, significant bits and the unknown sizes' reasons
  compared at reuse; each header bound to its image's fingerprint
  (`preflight(bind_headers=True)`, `source_changed_during_check`); the copy's pixel type
  from that header; `parse_size` exact.
- **Before the audit: experiment design identity and counts (issues #82, #83 and #98)**
  *(this PR; questions 61, 62 and 63)*: technical-repeat folders whose images are all
  excluded are not counted, and the balance text names them; every design reader refuses a
  header naming a column twice or a column without a name, and `RunInputs.design()` reads
  with `read_confirmed_design`; changing the notebook's output folder reloads the design
  from the new folder and asks again.
- **Before the audit: links and the log (issues #81 and #99)** *(done; questions 64 and
  65)*: `links.LinkGuard` follows a link only inside the folder walked (discovery, OME-Zarr
  stores, the masks folder; `RunInputs` and `convert` refuse the same links), and the CLI
  checks `--log` (`log_target`) before opening it, after the other arguments.
- **Before the audit: micrometre conversion and exit status (issue #93, #130 review)**
  *(this PR; question 66)*: every conversion to micrometres is a positive finite length
  (`length_units.to_micrometers`, moved from `physical_sizes` with the unit table, which
  re-exports them); a header size that overflows or underflows is unknown and named by
  `invalid_physical_calibration`, a stated one is refused, and an infinite size is never
  the same as another; `check` and `design` exit with status 1 whenever they print an
  error.
- **PP-12 (staging and resume)** *(implemented, PR #132; design v0.4 §1, §8; issues #86,
  #90; question 69; contract §9.4)*:
  - Move only, first: `source_fingerprint` and `LABELS_GROUP` from `manifest` to
    `inventory` (io; `manifest` re-exports `source_fingerprint`).
  - #86: an OME-Zarr image's fingerprint adds the SHA-256 of its inventory (each file's
    relative path, size and `mtime_ns`, sorted by path; still metadata only):
    `Fingerprint.inventory`, `FileEntry.inventory_sha256` (OME-Zarr only), kept for label
    images and copies too; `check_sources` compares it; a record without it counts as
    changed. A folder inside the image that cannot be listed raises instead of being
    skipped.
  - Staging (`run.staging`) and resume (`run.resume`): each image's rows staged in
    `.staging` as soon as it is read; the next run reuses them only when the staging key
    (row key, pixel type, schema version, the versions of preprocessing and normalization)
    and the fingerprint still match, and reports `resumed` and `staged_discarded` (reason
    per file); a stopped run keeps `.staging`, a published run removes it; `full` reuses
    none.
  - Crash-consistent publication (`run.generation`, owner decision of 2 November 2026,
    question 69): the generation in `.publish` with a publication record, committed by
    the manifest's `os.replace`, then moved in; the lock finishes or discards an
    interrupted publication; `RunInputs` reads by SHA-256 (`published_path`) and again if
    the manifest changed (`read_stable`). The backup-and-restore protocol is gone.
  - #90: headers bound to fingerprints in the run (`bind_headers=True`), the entry records
    that fingerprint, the image is fingerprinted after its pixels are read (before its rows
    are staged) and every image before the commit: `source_changed_during_run` (error).
  - Owner decision: `copy_changed_after_conversion` is an error that stops the run before
    it writes (§17).
  - Not in this PR: a v0.4.0rc3 run on OME-Zarr images must be run again (its entries
    have no inventory: recomputed, and `RunInputs` refuses them).
  - **Follow-ups (review of PR #132):** (a) masks are fingerprinted when the manifest is
    written, as before, not bound to the fingerprint taken with their headers during the
    run (owner and coordinator: a follow-up, not a blocker); (b) a run killed while an
    atomic write's temporary file is open leaves that hidden `.<name>.<random>.tmp` file in
    the output folder (on main too, for the provisional report); the lock could remove
    them.
- **PP-13 (docs and the migration guide)** *(done, this PR; design v0.4 §8)*:
  - [docs/migration-0.3-to-0.4.md](migration-0.3-to-0.4.md): every breaking change from
    v0.3.4 for users and for bricks (what changed, why, what to do), linked from the README
    and the user guide §7;
  - [CHANGELOG.md](../CHANGELOG.md) for 0.4.0 (breaking, added, fixed, removed);
  - docs pass: README (masks, `RunInputs`, resume), user guide §7 (upgrading), contract
    (§9.3 before §9.4; the removed declared-axes text dropped), the notebook's step 3 note on
    resuming;
  - [docs/colab-release-check.md](colab-release-check.md): the Colab hand checks of PRs
    #118, #123 and #127 in one list, for the major release;
  - version 0.4.0rc4 (pyproject and the notebook pin), for the data-flow audit and the bricks'
    migration; the final 0.4.0 bump comes after the audit.
  - **Follow-ups recorded for the audit (coordinator, 2 November 2026; not changed here):**
    (a) `pair_counts` breaks an exact-half tie by the lower parent index, so `inside` and
    `parents_with_N_children` can depend on label numbering when a child lies exactly half in
    each of two parents (the `same_objects` counts do not, question 67) (*done in v0.4.0rc5:
    a tie gives no parent, `ambiguous_parent`, question 74*); (b) masks are not
    bound to a fingerprint during a run: they are fingerprinted when the manifest is written
    (PP-12 follow-up (a)); (c) a run killed while an atomic write's temporary file is open
    leaves a hidden `.<name>.<random>.tmp` in the output folder, for example after the
    provisional report (PP-12 follow-up (b)); (d) `length_units.to_micrometers` refuses NumPy
    scalars other than `float64` (for example `np.float32`, `np.int64`) instead of
    converting them; (e) the notebook's axis card does not show the size and plane-count
    warnings recomputed under the confirmed roles (question 68): they appear only in the
    run's result.
- **Release v0.4.0 (PP-13) checklist**, for every tag of v0.4.0 including release
  candidates, in the release PR before the tag is pushed:
  - set `version` in `pyproject.toml`;
  - set the notebook's pin (question 58): `RELEASE_TAG` in Step 0 to the new tag,
    `current_version` to the tag without "v", the same version in
    `notebooks/notebook_latest_versions.yaml`, and the tag named in the Step 0 Colab callout
    (`tests/test_notebook_file.py` checks that the four agree);
  - add the tag's changes to [CHANGELOG.md](../CHANGELOG.md), and any breaking change to the
    [migration guide](migration-0.3-to-0.4.md);
  - run the full gate (ruff check and format, pytest with `.[dev,notebook,zarr]`,
    `tools/check.py --rules`) and wait for CI on every platform;
  - **for the major release v0.4.0 only** (owner, 2 November 2026): after the round-2
    data-flow audit and the matrix run, run the notebook by hand on Google Colab with
    [docs/colab-release-check.md](colab-release-check.md) (PRs #118, #123 and #127). Release
    candidates are not checked on Colab: until then the notebook is verified headless only
    (the tests run it in a local kernel).
  - v0.4.0rc4 (PP-13): version and pin set; the owner tags it. The data-flow audit and
    MorphoFeature's migration run against it.
  - v0.4.0rc5: version and pin set, the changelog's rc5 section (PRs #139, #140, #142 and
    the rc5 PR: questions 66, 73 and 74, the round-2 audit fixes); the owner tags it.

## 11. Tests and acceptance criteria

Tests use simulated data generated in the test suite; do not rely only on biological
example files. Fixtures cover the folder layouts and warnings of §2, the axis layouts of
§3, every supported dtype and the edge cases of §4, unreadable files and memory-limit
planes, and new/changed/renamed/deleted files across reruns.

Acceptance checks:

- Folder labels and zero-based plane coordinates are correct in the CSV.
- `total_pixel_count − valid_pixel_count` equals the non-finite pixels and the bin counts
  sum to `valid_pixel_count`.
- Exact integer histograms reproduce the pooled-pixel mean, std, percentiles, median and
  MAD exactly, at every hierarchy level.
- With reduced bins, no percentile differs from the pooled-pixel value by more than half
  the widest bin in the group, at every level and for integer and float data.
- Pooled counts are used at every level; no mean-of-means is used.
- Reruns are idempotent and only new or changed files are read.
- Planes read in blocks give the same rows (and OME-Zarr copies) as planes read whole, for
  every pixel type and for constant, non-finite and empty planes.
- Each channel of a schema-version-3 row has the histogram that version 2 wrote in that
  channel's row, read whole and in blocks (committed version-2 fixture); step 4's
  statistics are those of the version-2 rows.
- The CSV round-trips through `read_plane_histograms()` with labels unchanged, and the
  notebook shows understandable summaries and validation messages.

## 12. Open questions

Raised during review; to be decided before the milestones that need them:

1. Channel identity: record channel names from metadata in an optional `channel_name`
   column and warn when names at the same index differ between files?
   *Decided (October 2026): record channel names per file in the run manifest, not in the
   CSV, and warn when the names at the same channel index differ between files. The CSV
   stays at schema version 1.*
   *Revised (October 2026, data-flow audit, issue #37): names that differ at the same
   channel index are the error `channel_names_differ`, because channels are matched by
   number and a different stain would be pooled and scaled as the same channel. Only named
   channels are compared; there is no override, so such images are exported again or
   excluded. Owner decision (October 2026, review of #42): leading and trailing whitespace
   is removed from channel names at read time, for every image (sources and this brick's
   copies), and recorded in the manifest (`channel_names_trimmed`); whitespace inside a
   name is still compared exactly (`DA PI` and `DAPI` differ, and the message quotes names
   that differ only in whitespace). OME-Zarr copies
   leave unnamed channels without a label instead of the placeholder `Channel <i>`, so a
   copy never names a channel its source does not.*
2. Multi-series and pyramidal TIFFs: use only full-resolution level 0, and block files
   with several positions?
   *Decided (October 2026): yes. Pyramidal files are read at full-resolution level 0 only;
   a file with more than one image series is an error in this milestone.*
3. RGB samples axis (`S`): allow mapping it to C?
   *Decided (October 2026): yes. The axis review prefills `S` as C (when the file has no
   other channel axis) and the user confirms it like any other role.*
4. Bit depth: record significant bits (for example 12-bit cameras in 16-bit files) when
   metadata provides them?
   *Decided (October 2026): record them in the run manifest when metadata provides them,
   for information only. `bit_depth`, binning and the CSV are unchanged.*
5. Ignore system files (`._*`, `Thumbs.db`, `.DS_Store`, `__MACOSX`,
   `.ipynb_checkpoints`) silently, and treat the `single` check and sibling uniqueness as
   case-insensitive? *Decided (October 2026): yes to both; dot-files, `Thumbs.db`,
   `desktop.ini` and `__MACOSX` are skipped without a warning.*
6. Corrupt files: allow the user to exclude one explicitly and continue?
   *Decided (October 2026): yes. The run takes a list of images to exclude; they are not
   read, are reported as a warning and recorded in the manifest, and their rows are
   removed.*
7. Folders deeper than the technical-repeat level: warning or error?
   *Decided (October 2026): error, so that a layer of images is never dropped silently.*
8. High-priority review fixes: how should confirmed singleton axes and failed output
   publication behave?
   *Decided (October 2026): notebook readiness uses the confirmed axis review together
   with all remaining folder, exclusion and metadata issues. Both output files are staged
   before publication, and a caught publication failure restores the previous state.
   Backups are retained if restoration fails. Forced termination remains covered by the
   existing CSV hash check on rerun. The CSV and manifest schemas stay at version 1;
   their fields and interpretation are unchanged.*
9. Step-4 feedback while statistics are calculated?
   *Decided (October 2026): show the existing animated spinner with the current stage,
   change the button to "Calculating…" and disable duplicate calculations. Keep the busy
   state until plotting finishes, and restore the button after success or failure. This
   is a notebook interaction change; statistical definitions and file schemas are unchanged.*
10. Can the notebook initialise its environment before importing the package?
    *Decided (October 2026): Step 0 uses the standard library to detect local Jupyter or
    Google Colab, prefer a nearby source checkout, reuse an installed package, or install
    from GitHub when missing. Install missing notebook requirements with the kernel's
    Python, using an editable install for a checkout. Reuse usable installations without
    automatic upgrades, and request a kernel restart before switching an already loaded
    package to another checkout. All image processing and widget logic stay in the package;
    schemas and statistical definitions are unchanged.*
11. How should the Step-1 folder-label table be aligned?
    *Decided (October 2026): centre all headers and cells, including image counts, so
    labels line up with their column headings. Apply this styling to the folder-label
    table only.*
12. OME-Zarr: which images can be input, how are TIFF images handled, and with which
    dependency?
    *Decided (October 2026), revising an earlier answer that covered only copies of TIFF
    images: the input may be TIFF images or OME-Zarr images, mixed in one experiment.
    OME-Zarr images are folders ending in `.zarr`, read from level 0 with the axes their
    OME-NGFF metadata declares; OME-NGFF 0.4 and 0.5 are readable (0.5 needs zarr 3, so
    Python 3.11 or newer). A store with several images is an error, like a multi-series
    TIFF. For TIFF images the user chooses once per experiment: read them directly
    (default), or convert them to OME-Zarr in the output folder first and compute the
    histograms from the copies, like any other OME-Zarr image. The choice is recorded in
    the manifest, and switching it recomputes every TIFF row; copies no longer used are
    removed. An image's change detection uses its size and modification time; for an
    OME-Zarr folder, the total size of its files and the newest file time. `zarr` (2.18 or
    3.x) is an optional `zarr` extra; the core stays numpy and tifffile. Copies are written
    as OME-NGFF 0.4 (Zarr format 2) for the widest viewer support and keep the exact source
    values at level 0. Chunked processing of planes above the memory limit is not part of
    it. The CSV schema stays at version 1; the manifest gains optional keys. Delivered as a
    draft PR for separate testing.*
13. Notebook feedback on the conversion option: where does it go, and what can the user set?
    *Decided (October 2026): a separate step-3 card, "(Optional) Image conversion", after
    the Settings card and not titled after TIFF images. Reading the images directly is the
    default and recommended choice and saves nothing extra. Choosing to convert shows the
    conversion settings — chunk size (16 to 8,192 px, default 1,024), lossless
    compression (Blosc LZ4 default, Blosc Zstandard or none, level 1–9) and pyramid levels
    (automatic, none, or 1–7) — and a disk-space warning that the images are stored again
    in the converted format, so the space they occupy is duplicated. The warning shows the
    TIFF images' size, an upper estimate of the copies (uncompressed, with the smaller
    levels) and the free space on the output drive; it turns red when the estimate exceeds
    the free space but never blocks the run. Help is given as separate points. Overlap is
    not offered: OME-Zarr chunks never overlap; overlap belongs to patch extraction. The
    settings are recorded in the manifest; changing them rewrites the copies (rows stay
    the same). The CLI offers the same settings.*
14. Notebook feedback: how should next actions, folder messages and conversion settings
    be presented?
    *Decided (October 2026): show successful actions' guidance in a separate "What next?"
    box with larger action text, rather than a continuation bullet. Folder-label and axis
    confirmations control when the next step is suggested; step 4 stays optional. Step-1
    messages start collapsed after each check, with orange warning and red error counts
    visible outside the dropdown; the exclusion action stays inside it. Choosing conversion
    opens an expandable "OME-Zarr conversion settings" panel containing three cards:
    storage block size, lossless compression and preview resolutions, with plain-language
    explanations. Manual collapse and control values survive other setting edits; the
    disk-space warning stays outside the panel. Processing defaults, validation severities,
    output formats and statistical definitions are unchanged.*
15. Planes too large for the memory limit (plan §10 "Later"): process them in blocks
    instead of stopping with `plane_over_memory_limit`? When, in which blocks, what about
    files that cannot be split, and how is the preview shown?
    *Decided (October 2026): yes, automatically and announced. A plane whose estimate is
    over the memory limit is read in blocks without a new setting; the run reports the
    images with the warning `planes_read_in_blocks` (the histograms are the same, reading
    takes longer, raising the limit reads them whole) and the manifest records
    `read_in_blocks` per file, for information only. The block size is derived from the
    memory limit: whole TIFF strips or tiles, or OME-Zarr chunks, in full-width bands
    first, as many as fit; uncompressed TIFF pieces and OME-Zarr chunks may be read a few
    rows at a time. A compressed TIFF that stores a plane in one strip or tile cannot be
    decoded in parts, so it keeps the `plane_over_memory_limit` error (with an action to
    raise the limit or save the images tiled, uncompressed or as OME-Zarr), as does any
    plane whose smallest block is still over the limit. Histograms are computed in two
    passes over the blocks (exact extrema, then bin counts) and are identical to those of
    whole planes; 8- and 16-bit integer planes need one pass. OME-Zarr copies of such
    planes are written block by block, and their smaller levels are built from the stored
    level above in regions of even size, so the copies equal those of whole planes. The
    axis preview of such a plane is read in blocks and reduced (every n-th pixel, at most
    4,096 pixels per side), and its caption says so. The CSV stays at schema version 1;
    the manifest gains an optional key. Built on main, which already includes the
    OME-Zarr work (PR #5).*
16. Should the notebook say before the run which images will be read in blocks, and which
    cannot be read within the memory limit at all?
    *Decided (October 2026): yes, in step 3's "What will happen" box, once the axis roles
    are confirmed. Images read in blocks get one more bullet in the list (information
    only: the histograms are the same, reading takes longer, and raising the memory limit
    in Settings reads them whole), with their names folded behind "Show N more". Images
    that cannot be read within the limit, not even in blocks, get a red card below the
    list saying the run will stop, how much memory they need, and what to do (raise the
    limit, exclude them in step 1, or save them again tiled, uncompressed or as
    OME-Zarr). Run stays enabled: the run reports the same `plane_over_memory_limit`
    error, and the "Before you run" checklist keeps only the folder, label and axis
    steps. The notice is worked out from the headers only (`planes.plane_layout`) and
    kept until the checked folder, the axis roles, the memory limit or the bin count
    change. Nothing else changes: messages, validation severities and output formats
    stay as in question 15.*
17. Should every row carry all three plane coordinates, so that later bricks (patch
    extraction) identify a plane the same way whether or not its file has the axis?
    *Decided (October 2026): yes. `z`, `c` and `t` are always written, 0 for an axis the
    image does not have (or has with length 1 and no role), and `plane_id` always ends in
    `::z=…,c=…,t=…` (`img.tif::z=0,c=0,t=0` for a 2D image). The CSV moves to schema
    version 2; a table of version 1 in the output folder is recomputed on the next run
    ("the earlier table uses a different format version"), and readers of version 1 reject
    version 2 tables. The manifest format stays at version 1.*
18. Should the package read OME-NGFF label images, which CellPatcher uses as masks for
    OME-Zarr images (its normalization contract §1 and §4)?
    *Decided (October 2026): yes, as read-only helpers in `zarr_input`: `ngff_label_names`
    lists the label images of the first image in a store (from its `labels` group, OME-NGFF
    0.4 and 0.5) and `open_ngff_labels` opens level 0 of one by name, which may be left out
    when there is only one. Errors (no labels, unknown or ambiguous name) are `NgffError`.
    This brick does not analyse label images; its outputs do not change.*
19. Where do the histogram, CSV, pooling and normalization code live once the
    normalization brick exists?
    *Decided (October 2026, normalization plan N1, N2 and N6, follow-up P1): in
    `morphobricks_normalization`. The modules `dtypes`, `histogram`, `records`, `csv_io`,
    `pooling` and `normalization` moved there unchanged (from commit `fd923c9`) and this
    brick depends on its release v0.1.0 (a git dependency pinned to the tag). Their public
    names stay importable from `morphobricks_preprocessing` and from the old submodules
    (e.g. `morphobricks_preprocessing.histogram`) for one version: each old path warns
    once with a `DeprecationWarning` naming `morphobricks_normalization.<module>.<name>`
    and returns the same object, so `isinstance` and `except` keep working. The package's
    own code imports from the normalization brick and never warns. Outputs (CSV schema 2,
    OME-Zarr copies) are byte-identical. Agreed in review: the manifest's `versions` also
    records `morphobricks-normalization` (information only, never a reason to rebuild);
    the notebook's Step 0 installs the new dependency into environments set up before it;
    and the text of contract §3–§7 moved to the normalization brick's contract (§1.2 and
    §8.2), leaving short pointers here.*
20. The CSV has a `position_id` column that this brick left blank, and the run manifest
    had no position per file. How are positions identified across bricks (cross-brick
    audit finding X02)?
    *Decided (October 2026, cross-brick decision D02 of 26 October 2026): a position
    (field of view) is identified in every brick by `condition`, `biological_repeat`,
    `technical_repeat` and `position_id`; `position_id` falls back to the image file name
    without its extension only when one file is exactly one position. Here it always is
    (several series and position-like axes are errors: question 2 and contract §2), so
    every row's `position_id` is the image name without `.tif`, `.tiff` or `.zarr` (and a
    preceding `.ome`): `img_01.ome.tif` gives `img_01`. Equal names in two technical
    repeats are two positions. Two images in one leaf folder with the same identifier,
    ignoring case (`img_01.tif` and `img_01.ome.zarr`), are the error
    `duplicate_position_id` rather than a guess; excluding one of them does not resolve
    it, renaming or moving one does. The manifest gains the optional per-file key
    `position_id` (null when absent) and stays at version 1, like the optional keys of
    questions 12 and 15. The CSV columns and schema version 2 are unchanged; an earlier
    table with blank `position_id` is filled on the next run from the paths, without
    reading pixels. The normalization contract's note "never inferred from file names"
    (§8.2) predates D02 and is to be aligned in that repository.*
21. Which dataset warnings does the folder check give (normalization plan N37, follow-up P2),
    and how are they shown?
    *Decided (October 2026): step 1 gathers every consistency issue in a "Dataset
    consistency" panel; warnings never block. A balance table counts images per condition
    (columns) and biological repeat (rows, matched by folder name), technical repeats added
    up, with the number of technical-repeat folders; a count below half or above twice the
    median of the non-empty cells is `unbalanced_images`, and a biological repeat not found
    in every condition is `repeat_missing_conditions`. From the headers: physical pixel
    sizes that differ (`different_pixel_sizes`, X by Y, and the z-step of images with more
    than one slice, compared in micrometres; since D86 by the shared 0.1 % rule of question 38,
    before it to four significant digits) or are known for
    only some images (`missing_pixel_size`), read from OME or ImageJ metadata for TIFF files
    and, agreed in review, from the level-0 scale with axis units for OME-Zarr images (no
    unit: unknown); and different numbers of channels, slices, time points or other planes
    (`different_plane_counts`). Agreed in review: `different_sizes` now covers only width
    and height, for TIFF and OME-Zarr images, so a dimension is never reported twice; and
    the consistency issues (also `mixed_dtypes`, `inconsistent_dimensions`, `missing_axes`,
    `inconsistent_mapping`, `channel_names_differ`) appear only in the new panel, while the
    Messages card keeps the folder, file and readability problems; the tiles still count
    everything. The command line `check` prints the balance table and every warning, and
    `run` reports them too. Outputs do not change.*
22. How are biological repeats paired and confirmed, and controls recorded (normalization
    plan N30, N31, N35, N36, follow-up P3)?
    *Decided (October 2026): after the labels are confirmed, step 1 asks three things and
    writes the answers to `experiment_design.csv` in the output folder (contract §12, schema
    version 1). Repeats with the same folder name form one repeat group by default; a
    dropdown moves a folder into another group. Each group gets a yellow question with a
    **Yes, they are comparable** button (one question for the whole experiment when there
    are no biological-repeat folders), and step 3 stays locked until every group is
    confirmed. Control conditions are checkboxes, recorded only. Reruns with the same
    folders load the file and fold the questions away behind a **Change** button; a new
    folder reopens only the group it joins; added or removed images keep the confirmation.
    The command line reads the file (or `--design FILE`) and, without a fully confirmed
    design, writes it with `confirmed=false` for the groups still to confirm and stops. The
    manifest gains the optional key `experiment_design_sha256` (version 1). Agreed in
    review: the notebook writes the file after every answer, so nothing is lost if it is
    closed; `run_experiment` takes an optional `design` (without one it works as before and
    writes none; an unconfirmed one is the error `design_not_confirmed`), while the notebook
    and the command line always use one; a repeat group with two folders of one condition
    cannot be confirmed (`repeat_group_conflict`). `read_experiment_design` and
    `ExperimentDesign.repeat_groups()` give later bricks the
    `{(condition, biological_repeat): repeat_group}` mapping. `plane_histograms.csv` does
    not change.*
23. A folder named `single` was an error, but in the MorphoBricks convention (CellPatcher's
    trees, MorphoRelate's data such as `<condition>/single/<images>`) a level written
    `single` means "this level exists with one member". Can such a folder be read?
    *Decided (October 2026, for MorphoRelate milestone M3): yes. A folder named `single` in
    lower case is accepted as its level's value `single` when it is the only folder in its
    place (system folders such as `.ipynb_checkpoints` and `__MACOSX` aside), so
    `<condition>/single/<images>` gives `biological_repeat` and `technical_repeat`
    `single`, the labels of `<condition>/<images>`. The reservation had no recorded reason
    beyond keeping `single` for missing levels; what it protects is that `single` keeps one
    meaning, so two cases stay errors (`reserved_folder_name`): another spelling (`Single`
    would be a label different from the `single` written for missing levels), and a
    `single` folder with other folders beside it (the level would have several members,
    one called `single`). The CSV, the manifest and their versions are unchanged.*
24. Physical pixel sizes: `read_physical_sizes` applied the X unit to Y and Z, and callers
    (MorphoRelate milestone M3's `inspect`) had to open each file a second time to get
    them. Should each axis keep its own unit, and should the header inspection carry them?
    *Decided (October 2026): yes to both. `PhysicalSizes` gains `x_unit`, `y_unit` and
    `z_unit`; `unit(role)` returns each axis's own unit, from OME `PhysicalSizeYUnit` and
    `PhysicalSizeZUnit` (the OME default, micrometres, when left out, as for X) or ImageJ
    `yunit` and `zunit` (the X unit when left out, as ImageJ writes them). The per-axis
    units are kept as given and resolved when read: an axis without its own unit has
    `space_unit`, so sizes made with `space_unit` alone keep it for every space axis and
    stay consistent under `dataclasses.replace` and pickles written before; the readers
    give `space_unit` the unit of X, as before (None when a space axis with a size has no
    unit). Equality and hashing compare the resolved units, so existing callers are
    unchanged. `inspect_image` (`TiffMetadata`) gains the
    field `physical_sizes`, read from the header already open, for information only; the
    existing fields are unchanged. OME-Zarr copies of images whose Y or Z unit differs
    from X now write that axis's own unit; on the next run, a copy written before with
    other axis units (read from its `.zattrs`) counts as changed and is converted again
    through the usual staged publication, so one output folder never mixes the two (its
    rows do not change). `PhysicalSizes` and `read_physical_sizes` moved
    to the module `physical_sizes` and stay importable from `zarr_export` and the package.
    The CSV, the manifest and their versions are unchanged.*
25. OME-Zarr physical sizes: `open_ngff` read the axes but not the OME-NGFF scale, so the
    physical size of an OME-Zarr image's pixels was not available (MorphoRelate milestone
    M3 reads OME-Zarr input). Should it be?
    *Decided (October 2026): yes, in the form of question 24. `NgffImage` gains
    `physical_sizes`: the `scale` of level 0's `coordinateTransformations`, multiplied by
    the multiscales entry's own `coordinateTransformations` scale when it has one (OME-NGFF
    applies them in turn; translations are ignored), for X, Y, Z and T, with each axis's
    `unit` (OME-NGFF 0.4 and 0.5), common spellings (`µm`, `um`, `micron`, `s`) given as
    the UDUNITS-2 names TIFF units get, other units as written. `inspect_image` records them for OME-Zarr
    images as for TIFF files, and label images opened with `open_ngff_labels` carry their
    own. Nothing is guessed: without a level-0 scale, with a scale stored in a separate
    file (`path`) or not one number per axis, every size is None; an axis without a unit
    has the unit None, and a scale that is not a positive number is None. A scale given
    without a unit is kept, since only the caller can tell a real calibration from a
    placeholder (this brick's own copies write 1 without a unit when the size is not
    known). Information only: the CSV, the manifest and their versions are unchanged.*
26. Can another brick's notebook reuse steps 1 and 2 (folder check and axis review)?
    MorphoRelate milestone M3's "your data" notebook needs them, but their title, purpose
    and intro speak of this brick's histograms, step 1 has the card for this brick's output
    folder, and step 2's "What next?" box sends the user to compute the histograms.
    *Decided (October 2026): yes. `PreprocessingNotebook.folder_step` and `axis_step` take
    an optional `title` and `purpose` (the sentence under the title), a keyword `intro`
    (the blue box; an empty string leaves it out) and `show_next` (False hides the "What
    next?" box), and `folder_step` takes `show_output` (False hides the "Output folder
    (optional)" card; the check then uses the folder in its box, the default when empty,
    and the card is shown again when the check finds that folder inside the experiment,
    so the box to fix it is never hidden). The defaults give the pages as before, and this notebook calls them
    without arguments; the widgets, the checks and the gating are the same either way.
    Later, `show_design` leaves out the experiment design (question 31).*
27. How does this brick follow the channels moving into columns (normalization plan
    N41–N44, its Milestone 2b, follow-up P3b)?
    *Decided (October 2026): `plane_histograms.csv` is written in schema version 3 with
    the normalization brick's writer and `PlaneRecord`: one row per XYC plane (an image at
    one Z and T position), each channel's extrema, pixel counts and bins in its own
    CellProfiler-style columns (`Intensity_MinIntensity_C0`, `Histogram_Count_C1_00042`,
    …), `plane_id` `<path>::z=…,t=…`, rows sorted by path, z and t. The brick is pinned by
    commit (`0e53e67`, the merge of its Milestone 2b), not by tag, because the owner wants
    no tags before its v0.2.0. Asked of the owner: the channels of a plane are read and
    binned one at a time (`iter_xyc_planes`), so only one channel's pixels are in memory
    next to the finished histograms of the others; the memory estimate adds those bin
    counts (8 bytes per bin and channel), and one decision, whole or in blocks, covers
    every channel of every plane of an image. Each channel's histogram is the one version
    2 wrote, whole and in blocks, which a committed version-2 fixture checks. Images with
    different numbers of channels are the error `different_channel_counts` (N43), before
    any image is read: in step 1 from the headers (the C axis, or the RGB samples without
    one) and in the axis review with the confirmed roles, which replace the header's count
    (a confirmed missing C axis is one channel); the notebook shows it in step 1's
    "Dataset consistency" panel and step 2's messages, the command line in `check` and
    `run`. `different_plane_counts` compares depth, time and other planes only. A table of
    schema version 1 or 2 is recomputed completely on the next run; the manifest stays
    version 1, records schema version 3, and a file's `plane_count` counts its XYC planes.
    Step 4 reads the new layout and, as before, pools every channel of a group together,
    so its table (whose "planes" still counts channel planes) and plot do not change. The
    warning `no_finite_pixels` counts the planes with a channel without finite pixels. The
    deprecated re-exports of the moved names (question 19) keep pointing at the brick's
    current objects, now with its Milestone 2b signatures; names it added later are not
    re-exported. `experiment_design.csv` and the OME-Zarr copies do not change.*
28. When is this brick tagged, and which normalization brick does the tag install?
    *Decided (27 October 2026): this brick's first tag, v0.1.0, holds everything up to
    follow-up P3b and the input support for MorphoRelate milestone M3, which pins it. It
    depends on the normalization brick's release v0.2.0 (tag, not commit) instead of the
    Milestone 2b merge commit `0e53e67`; v0.2.0 adds only RGB and intensity checks, which
    this brick does not call yet. The next tag, v0.2.0, follows follow-ups P4 and P5.*
29. How is the image type, fluorescence or RGB, chosen and recorded (normalization plan
    N33, N34, its contract §5, follow-up P4)?
    *Decided (27 October 2026): after the axis roles are confirmed, step 2 shows an
    "Image type" card with the two types, the suggestion preselected and the yellow question
    "Is this the right image type for your images?" answered with **Yes, this is the right
    image type** (Undo to change it); nothing is used until it is confirmed, and step 3 stays
    locked until then. RGB is suggested when every analysed image is a TIFF stored as RGB
    colour (photometric RGB, three samples per pixel, read from the header as
    `TiffMetadata.rgb_samples`), the confirmed C role is on its samples axis, and RGB is
    possible; fluorescence otherwise (OME-Zarr images, grey samples, RGB with alpha). RGB
    needs exactly three channels of an integer pixel type, the rule of the normalization
    brick's `check_rgb`; otherwise the choice stays at fluorescence with the reason shown
    (ipywidgets cannot disable one radio button, so the whole choice is disabled), and
    `run_experiment(image_type="rgb")` is the error `rgb_not_possible`. Changing the axis
    roles or the folder asks again; the card is the same when step 2 is embedded by another
    brick (question 26). Agreed with the owner: the manifest gains two optional top-level
    keys, `image_type` (null when the run was given none, as `run_experiment` takes it
    optionally like the design of question 22) and `channels_from_rgb_samples`; it stays
    version 1. Changing only the type recomputes nothing. The command line takes
    `--image-type fluorescence|rgb`, or uses the suggestion and prints it with the reason;
    `check` prints the suggestion. `plane_histograms.csv` and `experiment_design.csv` do not
    change.*
30. How are the intensity checks shown (normalization plan N38, N50–N53, its contract §7,
    follow-up P5)?
    *Decided (27 October 2026): `run_experiment` checks the rows it wrote, in memory, with
    the normalization brick's `image_summaries` and `intensity_checks`
    (`intensity.check_intensities`, `RunResult.intensity`), and `check_table` checks any
    table; no image is read. The four warnings are kept apart from the run's issues and
    never block. Agreed with the owner: the warnings and the plots are shown both in step 3,
    automatically after every successful run, and in step 4 for the chosen table; the
    manifest records the limits used (optional key `intensity_checks`,
    `IntensityCheckSettings.to_dict()`; version 1), changing them afterwards only updates
    the warnings on screen and is recorded by the next run; the distributions are
    cumulative curves, like step 4's existing plot. Each panel has a status line, tiles per
    kind, a table (check, channel `C<i>`, image or repeat, what was found) with what to do
    per kind, a collapsed **Check settings** panel (`outlier_limit` 3.0, `saturation_limit`
    1 %, `min_images` 5, and **Restore the defaults**), channel buttons, box plots of the
    image medians and high percentiles per condition and biological repeat (images with a
    warning in that channel, from `Issue.channel`, as red diamonds; shifted repeats as amber
    boxes), and the cumulative distribution of each biological repeat, one panel per
    condition (shifted repeats dashed; at most 12 conditions). Plots are drawn in the
    notebook only and nothing is written. The command line prints the warnings grouped by
    kind with the action once per kind, takes `--outlier-limit`, `--saturation-limit` and
    `--min-images`, and draws no plots. Comparisons stay within a condition, as the brick
    does. `plane_histograms.csv` and `experiment_design.csv` do not change.*
31. Since follow-up P3 (question 22), step 1 asks for the experiment design once the labels
    are confirmed: an "Experiment design" card, `experiment_design.csv` written in the
    output folder (`<images>_MorphoBricks_Preprocessing/` by default, created beside the
    image folder), and texts saying that step 3 runs once every group is confirmed.
    MorphoRelate milestone M3's "your data" notebook embeds `folder_step` (question 26)
    from tag v0.1.0; it does not use the file, and its step 3 is another step. Can a
    brick embedding step 1 leave the design out?
    *Decided (27 October 2026, the owner's choice of an option here rather than a
    workaround in MorphoRelate): yes. `folder_step` takes `show_design` (default True).
    With False the "Experiment design" card is left out of the page; confirming the
    labels asks nothing, `design` stays None and nothing is written, so neither
    `experiment_design.csv` nor the output folder is created; the texts of the step no
    longer name the design or step 3 (the labels question drops "Step 3 can only run once
    the labels are confirmed", and the "What next?" box goes from the labels to step 2).
    The rest of the notebook follows: step 2's "What next?" box does not send the user
    back to the design, and step 3, if shown, does not wait for it and runs without a
    design, as `run_experiment(design=None)` does (no file written, an existing one left as
    it is). Building the page again with the other value opens or closes the design for
    labels already confirmed. With the default the page, its texts and its files are as
    before. Nothing else in step 1 writes files: the folder check and the header
    inspection only read, and with `show_output=False` the output folder is only named.
    The CSV, the manifest and their versions are unchanged.*
32. Can this brick find each image's masks and tell how they relate, so that a user who is
    not a programmer only has to name the masks (MorphoFeature decision D78)? Bricks that
    measure objects (MorphoObjects, MorphoRelate, CellPatcher, MorphoFeature's workflows)
    need every image paired with its masks and the parent-child relationships between mask
    types (two nuclei in one cell, nucleoli inside nuclei inside cells, mitochondria grouped
    by cell), and today each asks the user again.
    *Decided (October 2026), by the MorphoBricks owner: yes, for any number of mask types
    (contract §15). Masks are in a separate masks folder, outside the experiment, one
    subfolder per mask type mirroring the experiment's folders, paired with the images by
    leaf folder and `position_id`; the label images of OME-Zarr images are masks too. The
    checks of `check` and `run` read headers only; reading the pixels is an optional mask
    step (`masks` command, `scan_masks`), which writes `mask_relationships.json` to the
    output folder: each mask type's kind (`instances`, `grouped`, `one_value`, `empty`),
    each ordered pair's relation (`same_objects`, `grouped_by`, `inside`, `none`) with the
    counts behind it (including parents with 0, 1, 2 and 3 or more children), and the
    hierarchy of direct parents. A run given masks records them in the manifest's optional
    `masks` key (manifest version 1); the table never depends on them. The report suggests;
    it decides nothing: users name the masks (by default the folder name, names unique)
    and confirm or correct the relationships in the bricks that use them. Library, command
    line and documentation first; the notebook step follows in a later change. SciPy
    becomes a core dependency (the owner's agreement: "can use scipy if good reasons"):
    counting the connected pieces of every label of one 2048 x 2048 HPMEC cell mask took
    about 5 s with a NumPy union-find, and 0.3 to 0.9 s per HPMEC mask with
    `scipy.ndimage`. Confirmed by the owner (27 October 2026): masks are 2D with the Y and
    X sizes of their image (3D and time-lapse masks are a later follow-up), their pixels
    integers or booleans, and the masks folder outside the experiment. The thresholds are
    defaults the user may change, `scan_masks(..., thresholds=...)` in Python and one
    option per threshold on the `masks` command (`--inside-object-share 0.8`, ...);
    `mask_thresholds` checks them (shares and the intersection over union above 0 and at
    most 1, the median number of pieces at least 1) before any mask is read, and the values
    used are written in every report. Proposed to the brick's maintainer: tag v0.2.0 once
    this is merged, so the other bricks can pin it (with the follow-ups P4 and P5, merged
    before it). Next, in their own changes: a notebook step for the masks (name each mask,
    say what it contains, confirm or correct the relationships) and a file saving those
    answers for the other bricks, as `experiment_design.csv` does for the repeats.
    Also decided (owner, 27 October 2026, after MorphoFeature's end-to-end trial on
    MorphoRelate's JUMP-Target-2 conversion, which writes `<image>_<type>.tif` beside the
    images' folders): the masks folder may instead mirror the experiment with the type after
    the image name, `<condition>/.../<image>_<type>.tif`, and the two layouts may be mixed. A
    file that could name two images or fit both layouts is the error `ambiguous_mask`, never
    guessed. On the four JUMP smoke fields, both layouts give the same report.*
    *Follow-up (October 2026, data-flow audit, issue #36): masks get a plane identity. A
    mask may have a depth axis Z and/or a time axis T, named in its own metadata (never
    guessed: an unnamed stack axis is `mask_not_2d`); the masks of one image share their
    planes (`mask_planes_differ`), and `run` checks them against the image's confirmed
    roles (`mask_planes_mismatch`). A 2D mask is therefore refused for a Z-stack or a
    time-lapse; whether a 2D mask may stand for a projection of such an image is left to
    the owner, so it is refused for now. The manifest's mask entries gain `axes` and
    `shape` (optional keys, manifest version 1); `read_mask(z=, t=)` reads one plane and
    `scan_masks` scans plane by plane.*
33. What does this brick's second tag, v0.2.0, hold?
    *Decided (27 October 2026): v0.2.0 holds everything merged after v0.1.0: the image type
    (follow-up P4, question 29), the intensity checks and plots (follow-up P5, question
    30), step 1 embeddable without the experiment design (question 31, for MorphoRelate)
    and the masks (question 32, for the bricks that measure objects, MorphoFeature decision
    D79: every brick pins one preprocessing tag). The masks' proposal to tag v0.2.0 with
    them and move P4 and P5 to v0.3.0 no longer applied, since P4 and P5 were merged first,
    so one tag holds all four. The normalization brick stays pinned to its v0.2.0, its
    latest release, whose RGB and intensity-check code P4 and P5 use. Outputs:
    `plane_histograms.csv` schema version 3, `experiment_design.csv` schema version 1 and
    manifest version 1, with the optional keys `image_type`, `channels_from_rgb_samples`,
    `intensity_checks` and `masks`.*
34. How does step 3 handle a plane whose histogram range is stretched by a few extreme
    pixels (normalization plan N54–N60, its Milestone 5 and follow-up P6)?
    *Decided (28 October 2026): as P6 specifies. The normalization brick is pinned to its
    v0.3.1 and `plane_histograms.csv` is schema version 4 (each channel's underflow and
    overflow, zero with the default range); schema 3 tables are recomputed. Step 3's
    **Settings** offer the histogram range, **Minimum to maximum of each plane**
    (recommended) or **Robust** with its two quantiles (0.01 % and 99.99 % by default);
    `run_experiment(histogram_settings=...)`, and `--histogram-range` and
    `--robust-quantiles` on the command line. The manifest records the brick's
    `HistogramSettings.to_dict()` as `settings.histogram`; any change of it recomputes every
    row, also of the range check's limit alone, so the recorded checks always match it.
    Every plane is binned with `histogram_with_range_check`, whose memory the estimate
    includes, and the brick's `range_check_issues` gives one `narrow_histogram_range`
    warning per channel among the run's warnings. Beyond P6: the narrow planes are recorded
    in the manifest (`narrow_histogram_ranges`, an optional key of manifest version 1), so
    a later run that keeps their rows reports them again without reading the images; only
    narrow checks are kept, which is all the warnings use.*
35. What does this brick's third tag, v0.3.0, hold?
    *Decided (28 October 2026): v0.3.0 holds everything merged after v0.2.0, follow-up P6
    (question 34, PR #31), with the normalization brick pinned to its v0.3.1 (Milestone 5;
    its tag v0.3.0 holds the same code but reports version 0.2.0). The version in
    `pyproject.toml` is set to 0.3.0 before tagging, so the installed package reports the
    tag's version. Outputs: `plane_histograms.csv` schema version 4 (version 3 and earlier
    are recomputed on the next run), `experiment_design.csv` schema version 1 and manifest
    version 1, with the optional keys of v0.2.0 and `narrow_histogram_ranges`, and
    `settings.histogram`. The other bricks pin this tag to read schema version 4
    (MorphoFeature decision D79); CellPatcher's follow-up C1 raises its pin to it.*
36. What does v0.3.1 hold?
    *Decided (29 October 2026): v0.3.0 and PR #33, MorphoBricks decision D85's naming:
    `discovery.position_id` also removes `.png`, `.jpg`, `.jpeg`, `.bmp`, `.gif` and `.npy`
    (`NAME_SUFFIXES`; naming only, the brick still reads TIFF and Zarr) and refuses a name
    that is only an extension. Outputs and their schema versions are those of v0.3.0, with
    the normalization brick's v0.3.1. The version in `pyproject.toml` is 0.3.1 before
    tagging. Every brick pins this tag (D79), CellPatcher's core dependency included.*
37. What does v0.3.2 hold?
    *Decided (30 October 2026): v0.3.1 and the fixes of the data-flow audit of 29 October
    2026 (issues #35–#39), PRs #40–#47: negative mask labels refused; malformed OME-Zarr
    labels reported as `unreadable_mask`; differing channel names an error (exact after
    trimming leading and trailing whitespace, recorded per file); mask Z/T plane identity,
    with a 2D mask for a multi-plane image refused; strict manifest flags; a folder holding
    images and subfolders an error; the normalization brick's v0.3.2, whose reader checks
    each row's `plane_id`, `image_file` and `bit_depth`. Inputs that passed before may now be
    refused. The version in `pyproject.toml` is 0.3.2 before
    tagging. Every brick pins this tag (D79).*
38. Which rules do the other bricks take from this brick instead of copying them
    (MorphoBricks D86)?
    *Decided (30 October 2026, the owner's decisions D-a to D-k on the D86 de-duplication
    audit): this brick exports them as public functions, listed in contract §16, for a
    v0.3.3. Length units (D-a2): the microscopy units only, `pm` to `m` with US and British
    spellings, any other unit refused by name (`space_unit`, `to_micrometers`); this
    brick's readers use the same table, so British spellings are now calibrated. A plain
    TIFF's resolution tags are never a calibration (D-c, unchanged). Labels (D-b): int32,
    0 to 2,147,483,647 (`check_labels`, `LARGEST_LABEL`), narrowing this brick's earlier
    int64 limit. Header axes (D-d): the axes OME, ImageJ and tifffile "shaped" metadata
    name are trusted, placeholders (`I`, `Q`) never (`header_axes`); a declaration may only
    name what the header leaves open (`check_declared_axes`); `read_mask_file` reads a mask
    by its own header. Identity (D-e): `FOV_KEY`; labels and `position_id` without outer
    whitespace and never `NA`, `nan`, `none`, `null`, `n/a`, `<NA>` or empty
    (`check_identity_text`, discovery's `invalid_name`); plane indices are integers of at
    least 0, NumPy integers returned as plain ints (`check_plane_index`);
    names of one level differing only in case clash anywhere in the experiment
    (`case_conflict`, widened from siblings). Sources of a run: `check_sources` checks
    images against a run's manifest (in it, unchanged, same identity, same axis roles).
    Masks named as images (D-i): discovery warns about images named as masks of other
    images and folders named like a masks folder inside the experiment
    (`image_named_as_mask`, `mask_folder_in_experiment`), MorphoRelate's former screen.
    Pixel sizes (D-a): two sizes are the same within 0.1 % of the larger, per axis, in
    micrometres (`same_pixel_size`, None when either is unknown). The tolerance is not
    transitive, so there is no per-image key: `pixel_size_groups` links images by single
    linkage. `different_pixel_sizes` is given exactly when some axis's smallest and largest
    sizes differ, lists the images outside the largest group, and says so when the sizes
    only drift and no image stands out; it replaces the four-significant-digit rounding.
    Owner's follow-up decisions: `null`, `n/a` and `<NA>` are refused as names too, and
    `check_plane_index` returns NumPy integers as plain ints.*
    *Amended (31 October 2026, for v0.3.4): only the axes the metadata names bind a
    declaration. The `Y` and `X` `tifffile` reports for a plain stack of pages (`IYX`,
    `QYX`, `QQYX`, no axis metadata) only say where each page's rows and columns are
    stored, so like the placeholders they are left open: a plain `QYX` stack may be
    declared `YXC` or `CYX`. `named_axes` is the one rule behind `header_axes` and
    `check_declared_axes`; a declaration the metadata contradicts (ImageJ `ZYX` as `CYX`,
    OME or shaped `CYX` as `YXC`) is still refused. MorphoRelate's workaround (checking only
    when `header_axes` names every axis) is no longer needed. A declaration that differs
    from the named axes only by swapping Y and X (`CYX` declared `CXY`) is accepted: the
    image is read transposed, and `declared_axes_transposed` tells the caller so it can
    record it (as CellPatcher and MorphoFeature allowed). Any other disagreement stays
    refused.*
39. What does v0.3.3 hold?
    *Decided (31 October 2026): v0.3.2 and the shared rules of question 38 (PRs #49–#55),
    with the warning `unsupported_pixel_size_unit` naming an image, axis and unit outside
    the supported list (PR #56). Inputs that passed before may now be refused or warned:
    labels above int32, float masks, names that are NA-like or have outer whitespace,
    names differing only in case anywhere in the experiment; pixel sizes are compared
    within 0.1 % instead of after rounding. The normalization brick stays at v0.3.2. The
    version in `pyproject.toml` is 0.3.3 before tagging. Every brick pins this tag (D79).*
40. Which experiment-design reader do the other bricks use (core fixes for v0.3.4)?
    *Decided (31 October 2026): one strict reader here, `read_confirmed_design(path,
    expected_sha256=)` returning `ConfirmedDesign` (contract §16), replacing CellPatcher's
    `load_experiment_design` and MorphoFeature's `ExperimentDesign.read`. Both copies ran
    this brick's lenient `read_experiment_design` and `design_issues` and compared the
    file's SHA-256 with the manifest's; where they differed, the stricter behaviour is
    kept: CellPatcher's error codes and its separate `missing_experiment_design`, and the
    read-only repeat groups of MorphoFeature. Beyond both, nothing is filled in or
    converted (no outer whitespace trimmed, `true`/`false` only, no empty repeat group or
    count, no blank line), the CSV structure is checked (exact header, one value per
    column, quotes closed), labels follow D-e, an empty design is refused, and the SHA-256
    is of the bytes parsed. Every file this brick writes is read; a file edited in a
    spreadsheet is read once a preprocessing run has written it back.
    `read_experiment_design` stays lenient about the form of the user's own edits in step
    1 and the CLI, but (owner decision, 31 October 2026) repeat-group names follow D-e
    (no NA-like or empty value, no outer whitespace, no two names differing only in case;
    an empty group no longer means the folder name) and `confirmed_on` is empty or
    `YYYY-MM-DD`, in both readers; a bad edit is refused naming the line and column.
    `design_issues` reports such values in a design built in code
    (`invalid_experiment_design`), and the writer refuses them, so the brick only ever
    writes ISO dates.*
41. What does v0.3.4 hold?
    *Decided (31 October 2026): v0.3.3 and the core fixes of the owner's answers of 31
    October 2026 (PRs #58, #59). Declared axes (question 38, amended): a plain stack's
    positional Y and X are left open, so a plain stack declared `YXC` is accepted; a
    declaration that differs from the named axes only by swapping Y and X is accepted and
    the image read transposed (`declared_axes_transposed`); any other disagreement is
    refused. One strict experiment-design reader for every brick, `read_confirmed_design`
    (question 40). Inputs that passed before may now be refused: design edits with an empty
    or NA-like `repeat_group` (an empty group no longer means the folder name), repeat
    groups differing only in case, or a `confirmed_on` that is not `YYYY-MM-DD`. The
    normalization brick stays at v0.3.2. The version in `pyproject.toml` is 0.3.4 before
    tagging. Every brick pins this tag (D79).*
42. What does step 1 do with a saved `experiment_design.csv` it cannot read (PR #59
    follow-up)? Until now it fell back to the default design and wrote it at once, before
    any answer, so the confirmed answers in a file a spreadsheet program had re-saved (a
    `confirmed_on` of `26/10/2026`, refused since question 40) were lost.
    *Decided (31 October 2026): the file is kept untouched and refused. Step 1 shows the
    refusal, naming the line and column, starts the questions unanswered and writes nothing
    until the user corrects the file (and confirms the labels again) or answers a question
    again, which replaces the file. No silent fallback to a default design; step 3 runs
    only once every group is confirmed again. The command line already refused such a file
    without writing it.*
43. What does the public `load_design()` do with a saved `experiment_design.csv` it cannot
    read (PR #61 follow-up)? Until now it returned the default design together with the
    reason as a `problem` string, so a caller that ignored the string worked on a design
    nobody confirmed.
    *Decided (31 October 2026): it raises `DesignCheckError` with the one error
    `unreadable_experiment_design` (the code and the message "The experiment design cannot
    be read: …" of `read_confirmed_design`, followed by the lenient reader's refusal naming
    the line and column; the `DesignFormatError` is its `__cause__`), and returns no
    default design. It now returns `(design, changes)`. Without a file it still returns the
    default design: no file is the first run, not an unreadable one. The notebook keeps the
    behaviour of question 42 (file untouched, refusal shown, an answer replaces it), and
    `check` prints the refusal and writes nothing. Breaking change for the next release.*
44. How is a mask one pixel high or wide read (reported by the fiberfeatures brick)? A valid
    2D label mask of 1 x N, N x 1 or 1 x 1 pixels was refused: `read_mask_file` said it had
    the axes `X` (or `Y`, or none) and `find_masks` gave `mask_not_2d`. `tifffile` (and
    OME-NGFF) give such a mask the axes `YX`, with or without axis metadata; this brick's
    own mask header dropped every length-1 axis, Y and X included, and the plane readers did
    the same. Images were not affected: their axes are `tifffile`'s, which keep Y and X.
    *Decided (31 October 2026, a fix within the existing rules): a mask's length-1 axes are
    left out except Y and X, in the header (`MaskFile.axes`/`shape`, the manifest's `masks`
    entries) and in `read_mask`/`read_mask_file`, which return the 2D (1, N), (N, 1) or
    (1, 1) plane; a named Z or T stack of such planes keeps its planes. `tifffile` gives the
    axes `X` alone only for a 1D array saved with its default metadata (`{"shape": [N]}`);
    such a file is one TIFF page of 1 row and N columns, and a mask series without Y or X
    stored in one 2D page is read as that page (`YX`, (1, N)), as any plain 2D page is
    (decision D-d). Nothing is guessed: Y and X are the ones the file's header gives, and a
    dimension it does not name (a plain stack `IYX`) is still refused, the message now
    giving the header's axes and shape and the dimension that is not named instead of the
    axes left after dropping Y or X. Masks
    accepted before are recorded as before. Ships in the next release (v0.3.5), with the
    fixes of questions 42 and 43. Open for the owner: an image (not a mask) saved as such a
    1D array keeps the header axes `X` (`header_axes`, `check_declared_axes`), so the folder
    check suggests a mapping without Y and the run refuses it with `missing_xy_axis`;
    reading it as one row too would change the shared header-axes rule (§16).*
45. How are the axes checked, now that every brick reads preprocessing's run (MorphoBricks
    axes redesign, design note and audit of 1 November 2026)? Until now a confirmed
    mapping was never compared with the axes the header names: a Y/X swap was read
    transposed and a contradiction (header `ZYX` confirmed `TYX`) read under the
    declaration, with no trace but the manifest's `roles`.
    *Decided (1 November 2026, owner's final design after the audit): preprocessing is the
    only place axes are checked. `review_axes` compares each TIFF's confirmed roles with
    its header (`header_axis_issue`, built from the declared-axes rule). Roles that swap
    the named Y and X (`axes_yx_swapped`) or otherwise contradict them
    (`axes_contradict_header`) are errors naming the files with their header and
    confirmed axes and the likely cause (tifffile's `CYX` default, an ImageJ slice stack),
    with two fixes: correct the axes, or convert (named-axis copies). Nothing is read
    transposed or under a contradicted declaration. A plain stack whose metadata does not
    name its axes is read with the roles the user confirms, with the warning
    `axes_not_named` recommending named copies, recorded per file as `axes_from:
    "confirmed"`. The notebook shows the three codes as one card and marks each role that
    disagrees with the header before confirmation; when the axes are correct, the notebook
    and the CLI say so. Masks are compared with their image's Y and X by the confirmed
    roles (they were compared with the header's letters; the role-based comparison still
    matters for plain stacks, whose positional Y and X need not be the roles), and not at
    all for an image with an axis error. `check_declared_axes` and
    `declared_axes_transposed` are removed in v0.4.0 (question 46). Breaking for
    v0.4.0: experiments run with roles that swap or contradict the header no longer run
    as they are.*
46. What does v0.4.0 remove (spec revision 4, §8 PP-2)?
    *Decided (1 November 2026, owner): `convert_tiffs` with `zarr_export` (OME-Zarr copies in
    the output folder; the bricks never read them, and named-axis copies of images with axis
    errors are a separate OME-TIFF step), the manifest's `convert_tiffs`, `conversion` and
    `ome_zarr_path` (ignored when an older manifest holds them), the CLI `--convert-tiff`,
    `--chunk-size`, `--compression`, `--compression-level` and `--pyramid-levels`, the
    notebook's conversion card; the deprecated stand-ins of the code moved to the
    normalization brick (`_moved` and the `csv_io`, `dtypes`, `histogram`, `normalization`,
    `pooling` and `records` modules); and `check_declared_axes` with
    `declared_axes_transposed`, removed rather than deprecated because they read a Y/X swap
    transposed, which v0.4 forbids. Breaking for v0.4.0.*
47. What does manifest version 2 record, and when is a row reused (design v0.4 §1, §3, §6;
    PP-4)?
    *Decided (1 November 2026, owner, design revision 4): `manifest_version` "2"; per file
    `roles` and `axes_from` (`header` or `confirmed`) are required, with `pixel_sizes` by role
    (value, unit, source); per mask `size`, `mtime_ns`, `roles`, `axes_from` and `shared`; an
    optional `conversion_record_sha256`. A version 1 manifest gives no reusable row. A row is
    reused only when its `RowKey` (fingerprint, labels, axes, roles, `axes_from`, read path and
    the row-changing settings) is the earlier run's. A plain stack's confirmation covers only
    the files it was given for (`ConfirmedAxes`); a new or changed plain stack is
    `plain_stack_not_confirmed` until confirmed again (audit 2 B-1). Breaking: every v1 run is
    recomputed.*
48. How do later runs reuse earlier results and inputs, and what does a run report (design
    v0.4 §1, §4; PP-7)?
    *Decided (2 November 2026, owner): two modes, `restart` (default) and `full`, replace
    `force_rebuild`. A later run takes every input it is not given from the earlier v2
    manifest; a value given that differs is used and reported with its effect; the masks are
    dropped only with `--no-masks` and the exclusions cleared only with `--no-exclude`
    (refused together with `--exclude`). The CLI `run` may be given only `--output`: the
    experiment folder is then the recorded one, and without one the run stops with
    `experiment_not_recorded`; another experiment folder than the recorded one is reported
    as a change. An `O_EXCL` lock file guards the output folder; a stale lock is removed
    only on request. Every run writes `discovery_report.json` and `.txt`, also when it stops;
    `check`'s report keeps `status: "stopped"` with `step: "check"`. Contract §9.2.*
49. How are pixel sizes stated and recorded (design v0.4 §3, §4, §6; PP-8)?
    *Decided (2 November 2026, owner, as implemented in PP-8, PR #114). The choices the
    design left open: `calibration.csv` is written only by the `calibrate` command,
    `write_calibration` or by hand, and the run takes no calibration argument; a target that
    matches nothing is an error (`calibration_target_not_found`); targets are relative paths
    compared exactly; the unit `pixels` records the source `pixels` with no value, and
    against a header size the statement wins with the warning
    `calibration_differs_from_header`; the calibration reader accepts a UTF-8 byte-order
    mark and CRLF line endings (the design reader still refuses a byte-order mark; noted, not
    changed). Each image's X, Y and Z size comes from its header or from a statement in the output folder's `calibration.csv`
    (`target,axis,size,unit`; target `.`, a folder or an image, compared exactly; a length
    unit of D-a2 or `pixels`), the most specific statement applying. The run reads the file
    and never writes it: the user writes it by hand, with `write_calibration` or with the CLI
    `calibrate`, so it survives `full` and failed runs. Each axis is recorded with its source
    (`header`, `stated`, or `pixels` without a value); nothing is guessed and an axis without
    either is uncalibrated. A statement that differs from the header is the warning
    `calibration_differs_from_header` and is used; a target outside the experiment
    (`calibration_target_not_found`) and an unreadable file (`calibration_unreadable`) are
    errors. The §11 checks run on the resolved sizes; `missing_pixel_size` stays a warning
    (owner, 1 November 2026). The manifest records `calibration_sha256`, checked by
    `RunInputs`; a changed file is an input change that recomputes no row. Y and X are
    stated per role and never exchanged. Contract §9.3.*
50. What happens to a mask whose own header names X before Y (PP-8b, from CellPatcher PR #69
    question 2)?
    *Decided (2 November 2026, owner): the run refuses it. It is the existing `mask_layout`
    error (no new code), naming the mask with its header axes and the `numpy.swapaxes` fix,
    from `check` and `run` as from `preflight`. Before, `run` dropped it with the header size
    comparison it supersedes (`mask_issues`) and recorded the mask with `axes: ""`, which
    `RunInputs.read_mask` then called "changed or missing". A manifest never records a mask
    without roles (`mask_manifest` raises), and `RunInputs.open` refuses such an older record
    with its own message. Images differ: an image whose header names X before Y is read in
    canonical order (design change log, 1 November 2026); masks are never reordered.
    Contract §9.1, §15.*
51. How do bricks learn of images added after the run (PP-8b, from CellPatcher PR #69
    question 1)?
    *Decided (2 November 2026, owner): bricks never discover files and never leave an image
    out silently; preprocessing gives the check. `unrecorded_files` lists, with
    preprocessing's own rules, the images discovery would analyse and the masks it would pair
    with a recorded image (masks folder, OME-Zarr label images) that the run neither recorded
    nor excluded; files discovery ignores or refuses do not count. `RunInputs.open` always
    runs it and raises `UnrecordedFilesError` (a `RunInputsError` with the files) telling the
    user to run preprocessing again or exclude them. Chosen over an explicit call because it
    reads no pixel and no TIFF header (a folder walk, plus OME-Zarr label metadata), and a
    check bricks must remember to call could be forgotten; there is no opt-out. Contract
    §9.1.*
    *Also decided (2 November 2026, owner, on the PP-8b review questions): (1) a run that
    v0.4.0rc1 recorded with a mask of empty axes is refused as a whole by `RunInputs.open`,
    not only at `read_mask`; (2) a `masks_root` given for a run that recorded no masks
    folder is refused: every mask there paired with a recorded image is unrecorded, and the
    message says to run preprocessing again with the masks folder (`--masks`); (3) OME-Zarr
    label images are never skipped: without the `zarr` extra, `RunInputs.open` refuses a run
    holding OME-Zarr images, naming `morphobricks-preprocessing[zarr]`, and a recorded
    OME-Zarr image whose `labels` group cannot be read is its own refusal naming the image,
    not a silent pass and not an "unrecorded" file; (4) the version stays 0.3.4 until PP-13,
    and v0.4.0rc2 is tagged by the owner after this PR.*
52. Which permissions do the output files get (PP-8c, from a run on real data)?
    *Decided (2 November 2026, owner): the mode a plain `open()` gives. The atomic write's
    temporary file (`tempfile.mkstemp`) is private, so every output was `0600` whatever the
    umask, and colleagues could not read the results in a shared lab folder. Now a new file
    gets `0666 & ~umask` (the umask read from `/proc/self/status` where the system gives
    it, else by setting it under a lock, to the most restrictive value while it is set), and
    a file that already exists keeps its own mode, as the user set it: the run never widens
    or narrows what the user chose. Files written `0600` by earlier versions therefore stay
    so until the user changes them; the user guide says how. The table, written by the
    normalization brick's writer into the staging folder, is given its mode when it is
    published. Windows: unchanged (no such modes). Contract §9.*
53. Is a pixel size recorded for an axis the image does not have (PP-8c, from a run on
    real data)?
    *Decided (2 November 2026, owner): no. MorphoRelate's HPMEC max projections (`CYX`)
    keep the z-step of their stacks in their ImageJ metadata (0.43 µm), and the run
    recorded it as their Z size from the header. A Z or T size is now recorded only for an
    image whose roles include that axis (`calibration.fit_to_roles`, after the axis
    review; a singleton role of a missing axis does not count); otherwise the axis has no
    value and no source. A `calibration.csv` statement for an axis an image lacks is
    neither refused nor recorded: the warning `calibration_axis_absent` names the images
    (refusing would make a whole-experiment Z statement impossible in an experiment that
    mixes stacks and 2D images; the question 49 rule that a statement matching nothing is
    an error is about targets, not axes). The report's text counts Z and T sizes for the
    images with the axis. The `RowKey` does not include pixel sizes, so earlier rows are
    reused (no row depends on a size, question 49): the next `restart` leaves the table
    byte-identical and rewrites the manifest without the absent sizes. Contract §9.3.*
54. What do `RunInputs.read_plane` and `read_mask` do with a `z` or `t` that is not a number
    (PP-8c, from a run on real data)?
    *Decided (2 November 2026, owner): a `TypeError` naming the expected arguments. A
    `plane_id` passed as `z` gave the misleading `IndexError` "has no Z …" on a 2D image,
    and `read_mask(z=True)` reached the reader. Python and NumPy integers are accepted
    (`bool` is not), and `read_mask` keeps None. A read by `plane_id` is not added: the
    spec's interface is `read_plane(fov, z, t)` (design v0.4 §6), every table row carries
    its `z` and `t`, and a second way to address a plane would be a second rule to keep
    in step. Contract §9.1.*
55. What does a run that stops say it wrote (PP-8c, from a run on real data)?
    *Decided (2 November 2026, owner): what it wrote. Since PP-7 every run that holds the
    lock writes its report, also when it stops, and the CLI `run` writes the design template
    when it stops on the design, yet the summary said "Nothing was written". It now says
    "The run stopped (…): no table or manifest was written." and names the report files and
    the template when written (`RunResult.report_written`, `run_summary(design_template=)`);
    the notebook says "The run stopped: no table or manifest was written." Only a run
    refused before it starts (the lock) still says "Nothing was written."*
56. Does the `masks` command use the recorded inputs (PP-8c, from a run on real data)?
    *Decided (2 November 2026, owner): yes, like `run` (question 48). The experiment
    argument is optional when the output folder holds a v2 manifest recording it (else
    `experiment_not_recorded`); the masks folder and mask axes are the recorded ones unless
    given, and a value given that differs is named with its effect. The exclusions follow
    the same rule (with `--no-exclude`), so `masks` never reads a mask of an image the run
    left out unless asked. `--no-masks` is not needed. A change is used for that scan only:
    `masks` writes only `mask_relationships.json`, never the manifest, so the next `run`
    keeps the recorded value until it is given the new one, and then removes a
    `mask_relationships.json` written for other masks (question 48). The recorded-experiment
    lookup is one helper shared by both commands (`cli_run.use_recorded_experiment`).*
57. Where does the experiment design template come from, and can it be confirmed without
    editing the CSV (PP-8c, from a run on real data)?
    *Decided (2 November 2026, owner): earlier, and from the command line. `check` also
    writes `experiment_design.csv` prefilled from the folders (conditions, biological
    repeats, `repeat_group` the folder name, counts; `confirmed=false`, `control=false`)
    when the output folder holds none, under the lock, and only when the folders have no
    error; it never replaces a design, edited or unreadable. This amends design v0.4 §1
    ("`check` … writes only the report"). A new command `design --output OUT` prints the
    design and what is still unconfirmed, and changes it without editing the file:
    `--repeat-group CONDITION/REPEAT=GROUP`, `--control`/`--no-control CONDITION`,
    `--confirm GROUP` and `--confirm-all` (a separate flag rather than `--confirm all`, so a
    repeat group named `all` stays confirmable). It reads, changes and writes under the lock
    with the run's reader (`read_experiment_design`) and validator (`design_issues`), fills
    `confirmed_on`, refuses unknown names and a group whose rows break the rules, and
    changes nothing when it refuses. It does not rediscover the folders: it works on the
    rows `check` or `run` wrote, and the run reconciles them with the folders as before, so
    a group that gained a folder since is asked again. Python: `edit_design` with
    `DesignEdit` (`apply_design_edit` without files). The confirmation stays an explicit
    act of the user: nothing confirms by default. Contract §12.*
    *Decided in review (2 November 2026, owner): `design` and `calibrate` also take
    `--remove-stale-lock`, as `check`, `run` and `masks` do (the `output_locked` message
    tells users to give it to the command). Same rule: the lock is removed only on that
    request, and never one of a process still running on the same computer.*
58. How does the notebook run on Google Colab, and which release does it install (PP-8d)?
    *Decided (2 November 2026, owner's PP-8d brief; supersedes the "Local Jupyter (not
    Colab-first)" target): one notebook file for Google Colab and JupyterLab, following
    LabConstrictor's notebook portability guide. Every code cell starts with `# @title`, and
    explanations are in Markdown cells. Every code cell keeps its source hidden: the notebook
    is an app-like widget notebook in which nothing is edited (LabConstrictor's code-hiding
    guide), so this differs from fiberfeatures, whose settings cells stay visible. Colab-only
    code runs inside `if "google.colab" in sys.modules:` in Step 0: Google Drive is mounted
    at `/content/drive` and Colab's custom widget manager is enabled (the package needs
    ipywidgets 8; Colab ships 7). Locally Step 0 prints the Python version, platform and
    working folder. No shell or line magics; pip runs through `subprocess` with the kernel's
    Python. When the package is missing, Step 0 installs the release named by `RELEASE_TAG`
    (v0.4.0rc2 first), never an unpinned `main`; a nearby checkout and an installed package
    are still used first, and Step 0 prints which one and its version. `current_version`
    equals the tag without "v" and `notebook_name` the file name, as LabConstrictor's
    version check reads them, with `notebooks/notebook_latest_versions.yaml` as the index;
    the pin is moved at every release (§10, release checklist). Steps 0 and 1 have "Colab
    users" / "JupyterLab users" callouts (data and results on Drive, output folder outside
    the experiment, one session per output folder, a runtime wiped on disconnect). The run's
    semantics are unchanged on Google Drive; its limits there are in the user guide §1.*
    *Decided in review (2 November 2026, owner, PR #118 questions): (1) the lock on Google
    Drive is documented only: one session per output folder, because the lock cannot guard
    an output folder across machines or sessions on Drive; (2) a button that removes the
    lock of a stopped run comes in PP-10 (§10, PP-10 notes); until then a documented code
    cell; (3) no advice to copy the dataset to the runtime's own disk, because reuse
    depends on the absolute path of the images: data and output stay on Drive; (4) on
    Colab, Step 0 installs the `notebook` and `zarr` extras (locally `notebook`, unchanged),
    so OME-Zarr images can be read there; (5) `RELEASE_TAG` stays `v0.4.0rc2`; the owner
    tags it after PP-8c and PP-8d are merged.*
59. How are images with axis errors saved as named-axis copies (design v0.4 §5, PP-11)?
    *Decided (owner, 2 November 2026; implemented in PP-11) as design v0.4 §5 says: images
    only, as named-axis OME-TIFF (BigTIFF when large); copies keep the file name and folder
    structure, so `source_relative_path`, `plane_id` and the FOV key stay the same; pixel
    sizes follow the roles and a relabelled axis gets an unknown scale; `.partial` files, the
    conversion record written last with source and copy fingerprints and relative paths, a
    warning when a source changes after conversion; the run on the copies keeps the output
    folder, reports the new experiment root and recomputes every row, keeping the design,
    image type and calibration; the CLI and API offer it always. Contract §17.*
    (1) **Pixel sizes after a Y/X swap (or any Y or X relabelled as the other)**: when the
    header's X and Y sizes are exactly equal (same value and unit) they are carried to the
    copy, since nothing can be exchanged; otherwise both are unknown and the user states
    them in `calibration.csv`. Never exchanged, never guessed (the B2 proposal of 1 November,
    sizes following the stored dimension, is not adopted). Other relabelled axes (a z-step
    on an axis confirmed as T) stay unknown.
    (2) **Where the copies go**: a folder the user names (`--to`, required; no default),
    outside the experiment and not holding it.
    (3) **What the copy tree holds**: the whole experiment, since it becomes the experiment
    root: converted images, the other images copied byte for byte (OME-Zarr stores with
    their label images), and excluded images copied unread (they stay excluded by the run).
    Non-image files are not copied.
    (4) **Which lock**: the copy tree's own (`.morphobricks-preprocessing.lock` there);
    `convert` takes no output folder and writes nothing there.
    (5) **The record's name**: `.morphobricks-conversion.json`, a dot-file (hidden), so
    discovery skips it without a change to the discovery rules.
    (6) **A source changed since an earlier conversion into the same folder**: refused
    (`copy_differs`), never updated in place: convert into a new folder. New images are
    added and unchanged copies reused.
    (7) **A conversion killed while moving the copies into place** (copies there, no record):
    the folder is refused (`copy_folder_not_empty`) until emptied. (A failure or an
    interruption that Python sees removes what the conversion wrote; only a killed process
    leaves this state.)
    (8) **64-bit integer images**: refused (`cannot_convert_pixel_type`); OME-TIFF has no
    such pixel type and values are never changed.
    (9) **Byte order**: copies are written in the computer's byte order; values are
    identical (a big-endian source gives a little-endian copy on usual computers).
    (10) **Channel names**: written only when the C axis keeps its role and every channel is
    named; otherwise none.
    (11) **Compression**: none (each plane then has a fixed place, so blocks are written
    in place within the memory limit).
    (12) **Axes confirmed as missing single planes** (`missing_as_singleton`) are not added
    to the copies: the run on the copies needs `--missing-as-singleton` again.
    (13) **RGB sources** with an axis error are written with their channels as separate
    pages (`CYX`), so the suggested image type may change; the recorded type is kept.
    (14) **`RunInputs` checks the conversion record**: a run whose manifest records
    `conversion_record_sha256` is refused by `RunInputs.open` when the record changed or
    disappeared, as for `calibration_sha256` and the design's SHA-256.
    (15) **Notebook card**: PP-11b (§10), implemented: step 2's **Make named-axis copies
    (optional)** card under the axis check.
    (16) **The copies' SHA-256** *(owner decision of 2 November 2026, after an independent
    review of 8db1f4a; implemented in PP-11c, issue #87)*: a fingerprint (size and time)
    does not see a change that keeps both, so the record gives each copy's SHA-256,
    computed in one pass after it is written; reuse, the run's `review_conversion` and
    `RunInputs.open` verify it (`RunInputs` once at open, for the copies of every image the
    run recorded: one read of those copies, documented in contract §9.1). Sources keep the
    fingerprint rule (design v0.4 §1). The record is version 2; a version 1 record is
    refused with a message to convert again (its copies cannot be checked). Reuse also
    compares the channel names and significant bits (#88) and the reasons of the unknown
    sizes (#92).
    (17) **One metadata epoch** *(same review; PP-11c, issue #96)*: each image is
    fingerprinted before and after the header read the copy's layout comes from, and is
    refused (`source_changed_during_check`) when the two differ; that fingerprint is the
    one recorded and checked before and after the copy is written. The copy's pixel type
    also comes from that header (the writer no longer opens the header for it), and a
    plane of another type is refused, never cast. Still read again, by design: the planes'
    memory check (`check_planes`, the run's own check) and the reader that reads the pixels
    (it opens the file and parses its header); both are bounded by the same fingerprint.
    (18) **Exact memory sizes on the command line** *(same review; PP-11c, issue #80)*:
    `--memory-limit` is parsed without floating point and must be a whole number of bytes
    above 0 (`1.5GiB` is accepted, `1.5` and `0.5B` are refused before anything is
    read).
60. How are OME-Zarr axes and calibrations checked (issues #72, #73, #94, from the audit)?
    *Decided (2 November 2026, design v0.4 §3: nothing guessed, nothing silent):*
    (1) **Same letters, contradictory roles (#72)**: every OME-Zarr image is matched by the
    roles its OME-NGFF axes declare, also when its letters are the mapping's
    (`axes.ome_zarr_mapping`, in `review_axes` and `file_mapping_for`). A confirmed role
    that is not the declared letter (`ZYX` confirmed `TYX` or `ZXY`, `QYX` confirmed `CYX`)
    is the error `ome_zarr_axes_mismatch`, naming each image with its declared axes and
    the confirmed roles. It is not one of the axis-check errors (`axes_contradict_header`
    and others) because those offer conversion, and an OME-Zarr image is never converted;
    the fix is to correct the roles. An axis of no known type is therefore never given a
    role, in any arrangement.
    (2) **Missing axes (#73)**, by the version the metadata states, read before the axes:
    an OME-NGFF 0.1 or 0.2 store is `unreadable_ome_zarr` whatever it contains, with the
    message to re-save it as OME-NGFF 0.4 or later: no spec-default `t, c, z, y, x` is
    applied (owner, 2 November 2026; the PR had kept it for 5-D arrays). 0.3, 0.4 and 0.5
    require `axes`, so a store without them is `unreadable_ome_zarr`; a store without
    `axes` that states no version or another one is `unreadable_ome_zarr` too. Such an
    image is refused, with no confirmation path, rather than offered for confirmation as a
    plain stack (owner, 2 November 2026): an OME-Zarr image cannot be converted to a
    named-axis copy, and the declared-role match refuses an axis of no known type. No new
    `axes_from` value.
    (3) **Malformed calibration (#94)**: a calibration given but unusable (transformations
    not a list or of another type than scale, translation and identity, two scales, a
    scale in a file or not one number per axis, a scale value of X, Y, Z or T that is not a
    positive finite number) gives unknown sizes on those axes, with what is wrong in
    `PhysicalSizes.invalid`, and the warning `invalid_physical_calibration` naming every
    image (also when every image has it). A warning, not an error: the pixels are readable
    and the image is uncalibrated, as design v0.4 §3 treats an image without a value
    (owner, 2 November 2026: stays a warning). It disappears once every size the malformed
    calibration leaves unknown is stated in `calibration.csv` (owner, 2 November 2026); a
    time step cannot be stated there, so a lost time step of a time series stays named
    (review finding: the PR dropped the warning when only X and Y were stated). Only
    these metadata problems are caught: any other exception while reading the scale makes
    the image unreadable instead of being hidden by a debug log.
61. Which technical-repeat folders are counted (issue #82)?
    *Decided (owner, 2 November 2026):* only those holding at least one image to analyse.
    A technical-repeat folder whose images are all excluded is not counted in
    `BiologicalRepeat.technical_repeat_count`, the balance table or the design's
    `technical_repeat_count` (it was counted before). The balance table's text says what
    was left out (`BalanceTable.not_counted()`: the excluded images and the folders whose
    images are all excluded), in the CLI `check`, the notebook's consistency card and its
    design card. A technical-repeat folder named `single` keeps question 23's meaning
    (the level's value `single`, counted as no technical-repeat folder, like a missing
    level): documented, not changed. Contract §11, §12; user guide §4.
62. Which design file headers are read, and how does `RunInputs.design()` read the file
    (issue #83)?
    *Decided (owner, 2 November 2026):* a header that names a column twice, or has a
    column without a name (empty or only whitespace), is refused by every design reader
    (`read_experiment_design`, so `load_design`, the notebook, `check`, `run --design` and
    the `design` command, and `read_confirmed_design`), with `DesignFormatError` naming
    the column and both positions; the lenient reader kept the last of two columns before,
    and a test asserted it. Names are compared ignoring case and outer whitespace (and,
    from the review, invisible format characters such as a byte-order mark or zero-width
    space inside the header, which made a second `condition` look new). Nothing
    is rewritten on refusal. `RunInputs.design()` reads with `read_confirmed_design`
    checked against the manifest's SHA-256 (it used the lenient reader). The header check
    and the value parsers moved to `design_values` (pure); `DesignFormatError` is
    re-exported from `design` unchanged. Contract §9.1, §12, §16; user guide §8.
63. What happens to the experiment design when the notebook's output folder changes (issue
    #98)?
    *Decided (owner, 2 November 2026):* the design belongs to its output folder. Since
    PP-10a a run reported the other folder's design as a design change but used it, so a
    design (its confirmations and controls) confirmed for one output folder could be
    written into another. Now changing the output folder reads the design from the new
    folder (`load_design`, an unreadable file refused and kept), or asks the questions
    again when it holds none; nothing is carried over and nothing is written until an
    answer (the folder may still be being typed), and the design card says which. The run
    is gated as before, so it runs only on the new folder's confirmed design. No copy
    action: the user answers again, or copies the file. Contract §12; user guide §4.
64. Are symbolic links in the experiment or masks folder followed (issue #81)? *Decided
    (owner, 2 November 2026): contain.* A link (file or folder; on Windows also a directory
    junction) is followed only when its real path stays inside the experiment folder, or the
    masks folder for masks, both resolved; a link that leaves it is refused
    (`link_outside_folder`, naming each link and its target, a broken link too). One file
    reached by two paths is refused, naming both (`linked_twice`); hard links too (the same
    device and inode, from the one `stat` taken per image or mask; owner, 2 November 2026,
    in review). A link back to a folder on its own path is `link_loop`. A broken link that
    stays inside is reported by its reader, never skipped (review: masks skipped it). The
    links inside an OME-Zarr store are held to the same rule (reading and copying a store
    follow them); the cost, about 84 ms per 20,000 store files on each discovery, is
    accepted (owner, 2 November 2026). One helper (`links.LinkGuard`) walks the experiment
    and the masks folder for discovery, the masks pairing, the unrecorded-files check of
    `RunInputs` (raised as `RunInputsError`) and, through discovery, `convert`. Contract §1,
    §8.
65. Where may the command line's `--log` write (issue #99)?
    *Decided (owner, 2 November 2026):* the log path is checked first and the file opened
    only after the other arguments are checked. It is refused inside the experiment or masks
    folder (given or recorded by the earlier run), inside `convert`'s folder of copies, and
    as or inside a protected output artefact (`plane_histograms.csv`, `run_manifest.json`,
    the reports, the design, `calibration.csv`, `mask_relationships.json`, the lock, the
    conversion record, the staging folders). Any other name in the output folder is fine.
    In review: folders are also compared by device and inode and output names ignoring
    case (macOS and Windows), and an existing file with a second name (a hard link) is
    refused. Also in review: the design file given with `--design` (an input) is refused
    (it was appended to). Contract §8; user guide §5.
66. What happens to a size that does not fit in micrometres, and what is the exit status of
    a command that reports an error (issue #93, review of #130)?
    *Decided (2 November 2026, design v0.4 §3: nothing guessed, nothing silent; the
    coordinator's answers in the review of #133); **confirmed by the owner (3 November
    2026)**: a header size that overflows or cannot be converted is a
    warning, the image uncalibrated with the reason, as the owner decided for #94; and
    `design` exiting with 1 after a write that leaves a conflict is accepted):* (1) every conversion to micrometres goes through
    `to_micrometers`, whose result is always a positive finite number: a length that
    overflows (`1e308 m` is infinite in micrometres) or underflows (`1e-320 pm` is zero), or
    a whole number beyond the float range, is a `ValueError` naming the value and unit
    (before, `inf` and `0.0` were returned, and an `OverflowError` raised).
    `PhysicalSizes.micrometers` uses it and is None for such a size, so `same_pixel_size`
    is None and `pixel_size_groups` leaves it out (both raised `ValueError` before), and
    the comparison never takes an infinite or NaN size for another (before, `inf` was "the
    same" as every size, so `1e308 m` beside `0.5 µm` gave no issue at all). (2) A header
    size of TIFF or OME-Zarr metadata that does so, or an OME-Zarr level-0 scale times the
    image's scale that does so, or a TIFF OME or ImageJ size that is itself not a positive
    finite number (`inf`, `NaN`, zero, negative or not a number, before silently unknown;
    review of #133), is treated like the malformed calibrations of question 60
    (3): the size is None, `PhysicalSizes.invalid` gives the axis and value, and the warning
    `invalid_physical_calibration` names the image (a warning, not an error, as for #94);
    a stated size in `calibration.csv` or `calibrate` is refused naming the line. (3) Every
    command exits with status 1 when it prints an error, and 0 otherwise (warnings never
    change it): `check` exited with 0 on an experiment design it could not read or that
    had `repeat_group_conflict` or `invalid_experiment_design` (which `run` stops on;
    review of #133), and `design` with 0 when the design it printed had `repeat_group_conflict` or
    `invalid_experiment_design`, also after writing a change. `run`, `masks`, `calibrate`
    and `convert` already did. Contract §2, §9.2, §9.3, §11, §16; user guide §5, §8.

67. How are the one-to-one matches of `same_objects` counted (issue #67)?
    *Decided (2 November 2026, implemented for issue #67; the rule is the issue's
    acceptance): as a maximum matching of the child and parent objects of each plane whose
    intersection over union is at least `same_objects_iou`
    (`scipy.sparse.csgraph.maximum_bipartite_matching`, a sparse graph, no K x K matrix).
    Each object is matched at most once, so two children halving one parent are one match.
    The number of matches is the same for every maximum matching, so the counts do not
    depend on label numbers or tie-breaking. Above an intersection over union of 0.5 the
    matching is every eligible pair (an object can overlap only one partner that much); at
    the default of exactly 0.5 only an object split into exact halves has two eligible
    partners, so the default changes nothing for real one-to-one segmentations (the HPMEC
    masks give the same report). The evidence adds `children_not_matched`,
    `parents_not_matched`, `children_over_iou_with_several_parents` and
    `parents_over_iou_with_several_children`; `mask_relationships.json` is report version 2.
    Contract §15.1.*
68. Which axes do the size and plane-count warnings use (issue #74)?
    *Decided (2 November 2026, implemented for issue #74): the confirmed roles, once they
    are confirmed. Before (`check`, preflight), `different_sizes` and
    `different_plane_counts` compare the axes each header names, and a plain stack is left
    out, since its header's letters are not its roles (design v0.4 §3). After the
    confirmation, `AxisReview.combined_issues` (used by `run`, the notebook's run and the
    conversion plan) drops the header versions and compares each image under its own
    confirmed roles; an image with an axis error is compared by its header, never by the
    contradicted roles. Images without confirmed roles that the header names are compared
    by their header, as before. The notebook's axis card still shows only the axis review's
    own issues; the corrected warnings appear in the run's result. Contract §11.*
69. How does a run resume after an interruption, and how is its publication made
    crash-consistent (PP-12, design v0.4 §1)? *Decided (owner, 2 November 2026; contract
    §9.4):* (1) **Resume:** each image's rows are staged as soon as it is read; staged rows
    are reused only when their row key, the pixel type, the schema version and the
    versions of the two packages that compute rows are unchanged and the image still has
    its fingerprint; anything else is discarded and reported, never reused. The staging
    folder is kept until a run publishes (design v0.4 §1 says it is removed "at the start
    of the next run"; keeping it until publication means a second interruption loses
    nothing; accepted by the owner and coordinator on 2 November 2026 on the condition,
    checked in review, that every run re-validates every staged row at its start). (2)
    **Publication:** the owner asked that any reader see the old or the new generation,
    never a mix, after a kill between any two steps. Chosen: manifest-last commit, the
    simplest design that keeps the documented file names as plain files on every system:
    the whole generation in `.publish`, a publication record, then one `os.replace` of
    `run_manifest.json` (the commit), then the moves; the next holder of the lock finishes
    or discards it; readers through the manifest read each file by its SHA-256 (from
    `.publish` while unfinished); a plain reader of `plane_histograms.csv` always reads one
    whole table (the old one until moved). Rejected: a generation folder with an atomic
    symbolic link, which would make every reader consistent but needs symbolic links
    (Windows without Developer Mode, Google Drive in Colab and copied or zipped output
    folders do not keep them); generation-named files with the manifest as pointer, which
    changes the documented `plane_histograms.csv` path or doubles the disk space. A plain
    reader that opens several files without the manifest can pair two generations during
    or after an interrupted move; it is told to read through `RunInputs`. (3) **#90:**
    headers bound to fingerprints, each image fingerprinted after its pixels are read and
    every image before the commit; a change is `source_changed_during_run` and nothing is
    published. (4) **#86:** the OME-Zarr inventory digest (each file's path, size and
    time), metadata only as design v0.4 §1 decides; content hashing stays the owner's
    call. (5) **`copy_changed_after_conversion`** is an error that stops the run before it
    writes (owner, 2 November 2026): the bricks would refuse the run anyway.
70. Which folders may hold which: the experiment, the masks folder, the named-axis copies and
    the output folder (audit of 3 November 2026, B1, I1 and m8)? *Decided (3 November 2026,
    audit fixes; contract §15, §17):* everything in the masks folder is read as masks, so
    it may hold none of the others: an experiment inside it is `experiment_inside_masks`, a
    conversion record anywhere in its tree (OME-Zarr stores not entered) is
    `copies_inside_masks`, an output folder inside it is `output_inside_masks`, and a
    `convert` folder inside it or holding it is `copy_folder_overlaps_masks` (the masks
    folder given, `--masks`/`masks_folder=`, else the one recorded by the run in the
    default output folder or, in the notebook, step 1's; when `convert` cannot know it, the
    run refuses the copies). Every command that writes into the output folder (`check`,
    `run`, `masks`, `design`, `calibrate`) checks it against the masks folder, given or
    recorded, before it creates the folder, takes its lock or writes its report; `design`
    and `calibrate` raise `OutputInsideMasksError`. `RunInputs.open` refuses a recorded run
    that breaks these rules. Paths are compared resolved, with the links' containment
    rule, and by device and inode for the folders that exist (a folder spelled in another
    case on a file system that ignores case). An output folder that holds both the experiment and the masks folder (their
    parent, m8) is **allowed and documented**, not refused: the brick writes only its named
    files (and its hidden `.publish`/staging folders) at the output folder's top level, the
    experiment and masks walks never enter the output folder's other contents, and
    `RunInputs.open` reads the outputs by name and SHA-256, so no output is read as an
    input. A brick that writes its own folders into the output folder must not name one
    like the experiment or masks folder.
71. Which inputs does the notebook start from when the output folder holds an earlier run
    (audit I4)? *Decided (audit I4, 2 November 2026):* those the run would keep, worked out
    by the run's own rule (`run.planned.recorded_inputs`, i.e. `resolve_inputs` given
    nothing): the exclusions, the histogram settings (with `min_central_bins`, which has no
    widget) and the intensity limits when the output folder changes (as the design is read
    again, #98); the bin count, only when recorded for the checked pixel type, and the
    missing-axes answer with each check; the image type preselected in step 2 (still to be
    confirmed). The masks folder and mask axes were already kept by leaving the box empty.
    An untouched widget gives the recorded value, so a notebook run after a CLI run with
    `--bins 64 --exclude …` keeps both; an edit is a change named in "What will happen" and
    in `input_changes`. A folder without a run sets back only the values filled from the
    other folder and not edited, so exclusions typed for a new folder are kept.
    A check fills the bin count and the missing-axes answer only while the user has not
    changed them since; a changed one is set as without an earlier run (review of #137).
72. How are the findings I2, I3, I5, m4, m5 and m6 of the preprocessing audit of 3 November
    2026 fixed (they change what `RunInputs` gives the bricks)? *Decided (3 November 2026,
    within the calibration decisions of 2 November 2026 and issues #93 and #94; contract §2,
    §11, §16):* (I2) the memory limit of `read_plane` counts reading only (decoded page,
    encoded bytes, a copy of the plane); the histogram path keeps its binning cost.
    (I3) a header size is recorded only with a supported unit (D-a2 lengths, or a time
    unit); otherwise it is unknown, and `unsupported_pixel_size_unit`, which already names
    the unit as written, reports it (not also `invalid_physical_calibration`: one warning
    per cause). (I5) `RunInputs.read_mask` and `read_plane` check `z` and `t` against
    `Fov.z_count` and `Fov.t_count` (`IndexError`; a non-integer stays a `TypeError`).
    (m4) `to_micrometers` takes any `numbers.Real` except bool, still requiring a positive
    finite result. (m5) two warnings, `partial_pixel_size` and `non_square_pixels` (exact
    equality in micrometres, calibration decision 1), say that bricks measuring lengths
    will refuse the images; the run is not refused. (m6) only an OME-XML parse error is
    caught when reading TIFF sizes; it sets `PhysicalSizes.invalid`, named by
    `invalid_physical_calibration`; any other error makes the header unreadable as before
    for other header failures, never silently uncalibrated. In the same PR (review of #136):
    an ImageJ resolution tag of another type is `invalid_physical_calibration`, and an
    OME-Zarr scale without a unit is named by `unsupported_pixel_size_unit` ("without a
    unit"); `same_pixel_size` takes NumPy real scalars as m4 does.
73. How are the findings F1, F8, F9, F10 and F11 of the cross-brick test of 3 November 2026
    fixed? *Decided (3 November 2026, owner: fix before the round-2 audit; the choices in
    F8 are the conservative ones, within design v0.4 §3 and §5; contract §9, §15, §17):*
    (F1) `scan_masks` leaves a stated shared 2-D mask out of the comparison of an image's
    masks' planes, as `run` does, and compares it with each plane of the other masks; never
    across planes. (F9) `run` needs no `--axes` when every readable image names all its axes
    and the suggested mapping gives each a role; a plain stack still needs it; `convert`
    always needs it. (F10) a rerun whose manifest differs only in `written_at` keeps the
    earlier time, so the manifest's bytes and SHA-256 stay the same; any other change writes
    the new time. (F11) the `output_locked` message says that a lock left by a process on
    this computer that is no longer running looks stale, and how to remove it (also
    `convert`'s, for the folder of the copies); it is never removed automatically. A resumed run's summary says that the staged rows were resumed,
    not that all rows were computed. (F8a) `convert` keeps each image's stored order of
    dimensions (§17): writing copies in the experiment's common order would be a pixel-exact
    transposition under named roles, but it changes §17's copy axes and is left as an open
    question for the owner; for now `inconsistent_dimensions` and `inconsistent_mapping` say
    that named-axis copies do not solve them. (F8b) `convert --output FOLDER` names the output
    folder of the runs on the originals in the printed next commands (and is where the
    recorded masks folder is looked up); without it, the default folder is named with what
    to do if the runs used another one. (F8c) a plain stack whose pages interleave several
    dimensions (`IYX`, no shape metadata) cannot be converted: a reshape is not a role, and
    the spec has no way to state one, so the user guide says to save the image again with
    its dimensions (for example as a Fiji hyperstack). Follow-up for the owner: whether
    `convert` should accept a stated reshape (for example `--pages CZ=2x3`) or reorder copies
    into the common order. *Decided (owner, 3 November 2026):* neither in v0.4, neither a
    stated reshape nor a reorder; F8a and F8c stay as above, and the post-matrix issue #144
    keeps both options.
74. When a child object lies equally in two or more parent objects, which is its parent
    (`pair_counts`; PP-13 follow-up (a), §10)? *Decided (owner, 3 November 2026; contract
    §15.1, implemented in v0.4.0rc5):* none. Before, the parent with the lower label index
    was taken, so `inside` and `parents_with_N_children` depended on the label numbering
    when a child lay exactly half in each of two parents. Every tie path is an exact equal
    largest overlap of two or more parents (`mask_pairs.best_parents`); such a child is
    never `inside` and counts for no parent, and when the tied share would make it inside
    (at least `inside_pixel_share`) it is counted in the new evidence `ambiguous_parent`,
    which `masks`, the notebook's **Describe the masks** and the relationship table show. A
    tie below that share is `split_between_parents`, as before (no parent either way). Each
    child is counted once in `inside`, `ambiguous_parent`, `split_between_parents` or
    `outside_parents`. The `same_objects` counts were already independent of the numbering
    (question 67). `mask_relationships.json` is report version 3. Consumers checked
    read-only on 3 November 2026 (CellPatcher `79adc3a`, MorphoFeature `142c448`, their
    main branches): neither reads `inside`, `parents_with_N_children`,
    `mask_relationships.json` or `scan_masks`; the change reaches no brick's numbers (#143).
75. Matrix finding F10 (matrix run of 3 November 2026): `check` had no
    `--missing-as-singleton`, so it always stopped on an experiment of 2-D images and
    Z-stacks that `run` accepts with the option; and a 2-D mask whose OME header names its
    axes `YX` was refused for a Z image unless stated `--mask-axes Nuclei=YX`, an option
    for files that do not name their axes. *Decided (owner, 4 November 2026; contract §2,
    §9, §15):* `check` takes `run`'s option, with the same default (the earlier run's
    answer, else no) and the same review of the missing axes, so both report the same
    errors for the same roles; the notebook's check report takes step 2's answer. A 2-D
    mask whose own metadata names `YX` (OME, ImageJ, shaped with axes, OME-NGFF) is one
    plane shared by every plane of an image whose own metadata names a Z or T axis longer
    than 1, without a statement, recorded `shared: true` with the stated mask's `axes`,
    `roles` and `shape` and `axes_from: "header"` (not `"stated"`: the next run would take
    that as the user's statement). A plain 2-D TIFF, and any 2-D mask of a plain-stack
    image, still needs `--mask-axes TYPE=YX`: never guessed.
