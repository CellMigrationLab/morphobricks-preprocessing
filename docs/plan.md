# MorphoBricks Preprocessing Brick — Implementation Plan

**Status:** design brief, revised after the binning study of October 2026
**Scope:** folder validation, TIFF metadata review, per-plane intensity histograms, the
histogram CSV, and a normalization library for later bricks
**Out of scope for this milestone:** applying normalization (done by the patch-extraction
brick), OME-Zarr conversion, spatial correction methods

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
3. Inspect TIFF metadata, allow axis confirmation, and preview the resulting XY plane.
4. Read multidimensional TIFF data as individual 2D planes.
5. Create one histogram per plane, with exact extrema and pixel counts.
6. Write a self-describing CSV that later bricks use to calculate normalization
   parameters for any group of planes.
7. Support incremental updates without rereading unchanged images.
8. Fail clearly when a plane exceeds the configured in-memory limit.
9. Provide the normalization methods (min-max, percentile range, z-score, robust
   z-score) as an importable library, without applying them.

## 2. Folder hierarchy

The selected directory is the experiment root:

Experiment root → biological condition → biological repeat → technical repeat → TIFF images

Folder names are arbitrary labels and need only be unique among siblings. Each branch may
stop at any level; omitted levels receive the reserved value `single`. Levels cannot be
skipped. Labels are scoped to their parents: `bio_rep1` under two conditions are two
different samples, so groups are always identified by the full label path.

A folder containing images and no subfolders is a leaf folder. Only TIFF files directly
inside leaf folders are analysed. If the experiment root itself contains TIFFs and has no
subfolders, it is the leaf folder and every label is `single`.

During preflight, show a tree/summary of the inferred labels and ask the user to confirm
or correct it. The folder name `single` is reserved; report it as an error. Report
warnings for non-TIFF files, empty folders and TIFF files outside leaf folders, and
explain that they will not be analysed.

## 3. TIFF metadata and axis review

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

**Pass 1, preflight:** walk the tree, identify leaf folders and candidate TIFFs, inspect
headers, detect dtype/axis conflicts, and collect what the confirmation UI needs. Do not
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
its own plane. Write to a temporary file and replace the previous output only after a
complete successful run.

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

Block processing for: mixed dtypes; unreadable TIFFs in leaf folders; unsupported sample
formats or axes; missing/duplicate X or Y, duplicate roles or inconsistent mapping; a plane
over the memory limit; a folder named `single`; sibling folders whose names differ only in
case; a folder below the technical-repeat level; an output folder inside the experiment;
no usable planes. System files (dot-files, `Thumbs.db`, `desktop.ini`, `__MACOSX`) are
ignored silently.

Continue with warnings for: non-TIFF files; empty folders; TIFFs outside leaf folders;
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
  mapping saved to the manifest. *Core implemented: axis mapping, prefill and validation
  (`axes`), plane-by-plane reading with a memory limit and the preview plane (`planes`);
  the widgets follow with the notebook.*
- **Milestone 3 — Histogram engine:** per-plane histograms, extrema, non-finite and
  constant planes, pooling, normalization library. *Core implemented and tested.*
- **Milestone 4 — CSV and reruns:** write/update the CSV and manifest, detect
  new/changed/deleted planes, force rebuild. *Implemented: run manifest (`manifest`),
  preflight and incremental runs with exclusions (`run`).*
- **Milestone 5 — Integration and docs:** public API, notebook, CLI, user documentation.
- **Later:** OME-Zarr and chunked processing for planes above the memory limit.

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
