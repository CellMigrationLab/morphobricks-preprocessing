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

Folder names are arbitrary labels and need only be unique among siblings. Each branch may
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
- **Milestone 5 — Integration and docs:** public API, notebook, CLI, user documentation.
  *Implemented: summaries (`report`), notebook widgets (`notebook_ui`,
  `notebooks/MorphoBricks_Preprocessing.ipynb`), `check`/`run` command line (`cli`) and the
  [user guide](user_guide.md).*
  *Step-4 feedback: show an animated spinner through table loading, statistics and plotting;
  disable the calculation button while busy and restore it on success or failure.*
  *Setup feedback: standalone Step 0 detects the environment, prefers a nearby checkout,
  reuses an installed package, or installs missing requirements into the active kernel.*
  *Step-1 feedback: centre the folder-label table's headers and contents consistently.*
  *Notebook guidance feedback: separate "What next?" boxes show the next action in larger
  text, following the current confirmations. Step-1 message details are collapsed with
  persistent orange warning and red error counts. Step-3 conversion settings use an
  expandable panel with separate storage, compression and preview-resolution cards.*
- **OME-Zarr (optional `zarr` extra):** OME-Zarr images (OME-NGFF 0.4 and 0.5) are read
  as input next to TIFF files, plane by plane from level 0, with their declared axes
  (`zarr_input`). For TIFF images the user chooses once per experiment: read them
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
  planes (`histogram.compute_block_histogram`). OME-Zarr copies of such planes are written
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
    than one slice, compared in micrometres to four significant digits) or are known for
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
    TIFF's resolution tags are never a calibration (D-c, unchanged).*

