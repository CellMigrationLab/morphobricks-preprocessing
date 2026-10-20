# MorphoBricks Preprocessing Brick — Implementation Plan

**Status:** design brief, revised after the binning study of October 2026
**Scope:** folder validation, TIFF and OME-Zarr metadata review, per-plane intensity
histograms, the histogram CSV, and a normalization library for later bricks. OME-Zarr input
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
8. Fail clearly when a plane exceeds the configured in-memory limit.
9. Provide the normalization methods (min-max, percentile range, z-score, robust
   z-score) as an importable library, without applying them.

## 2. Folder hierarchy

The selected directory is the experiment root:

Experiment root → biological condition → biological repeat → technical repeat → images

Images are TIFF files (`.tif`, `.tiff`) or OME-Zarr images: folders whose names end in
`.zarr` (usually `.ome.zarr`). An OME-Zarr folder counts as one image, not as a subfolder.
TIFF and OME-Zarr images may be mixed in one experiment.

Folder names are arbitrary labels and need only be unique among siblings. Each branch may
stop at any level; omitted levels receive the reserved value `single`. Levels cannot be
skipped. Labels are scoped to their parents: `bio_rep1` under two conditions are two
different samples, so groups are always identified by the full label path.

A folder containing images and no subfolders is a leaf folder. Only images directly
inside leaf folders are analysed. If the experiment root itself contains images and has no
subfolders, it is the leaf folder and every label is `single`.

During preflight, show a tree/summary of the inferred labels and ask the user to confirm
or correct it. The folder name `single` is reserved; report it as an error. Report
warnings for files that are not images, empty folders and images outside leaf folders, and
explain that they will not be analysed.

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
when only a plane is needed.

Estimate each plane's working memory before loading it, including the decoded I/O unit and
temporary arrays. Use a configurable limit (default 1 GiB) and stop with a clear error when
it would be exceeded. Do not convert or silently switch readers.

## 6. Output CSV contract

Write one row per 2D plane to `plane_histograms.csv`. The default output folder is a
sibling of the experiment folder named `<experiment>_MorphoBricks_Preprocessing`; the user
may choose another, but never one inside the experiment folder or a raw image folder.

Columns, in order: `position_id, image_file, z, c, t, condition, biological_repeat,
technical_repeat, source_relative_path, plane_id, dtype, bit_depth, pixel_min, pixel_max,
total_pixel_count, valid_pixel_count, binning_min, binning_max, bin_count,
schema_version`, then `histogram_bin_00000` onwards. Details in contract §5.

The wide CSV is for computational use; spreadsheet programs may not open all columns. The
package provides `read_plane_histograms()`, which keeps labels as text. Alongside the CSV,
a run manifest (`run_manifest.json`) records the confirmed labels and axis mapping, the
settings, package versions and each source file's size and modification time. It also
records each file's channel names and significant bits when metadata provides them, for
information only; channel names that differ between files at the same channel index are
reported as a warning.

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
over the memory limit; a folder named `single`; sibling folders whose names differ only in
case; a folder below the technical-repeat level; an output folder inside the experiment;
no usable planes. System files (dot-files, `Thumbs.db`, `desktop.ini`, `__MACOSX`) are
ignored silently.

Continue with warnings for: files that are not images; empty folders; images outside leaf
folders;
non-finite pixels (ignored and counted); different image sizes with a valid axis
interpretation; planes with no finite values while others remain usable.

Show a preflight summary (files to analyse, files/folders skipped, dtype, axis mapping,
warnings). Never show raw stack traces as the primary message; keep technical details in a
diagnostic log.

## 9. Group statistics and normalization

This brick does not apply transformations. The package provides:

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
- **Later:** chunked processing for planes above the memory limit.

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
- The CSV round-trips through `read_plane_histograms()` with labels unchanged, and the
  notebook shows understandable summaries and validation messages.

## 12. Open questions

Raised during review; to be decided before the milestones that need them:

1. Channel identity: record channel names from metadata in an optional `channel_name`
   column and warn when names at the same index differ between files?
   *Decided (October 2026): record channel names per file in the run manifest, not in the
   CSV, and warn when the names at the same channel index differ between files. The CSV
   stays at schema version 1.*
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
