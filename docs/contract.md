# Preprocessing Brick Contract (Milestone 0)

This document fixes the rules other bricks can rely on. Changing any of them requires a
new `schema_version`.

Sections 3–7 (pixel types, histograms, the CSV, group statistics, normalization
parameters) are defined by the [normalization brick's contract][normalization contract]
(§1.2 and §6), whose code computes them (`morphobricks_normalization`, plan §12 question
19). They keep their numbers here as short pointers, with the rules this brick adds for a
whole experiment; any change to them goes through the normalization brick.

[normalization contract]: https://github.com/CellMigrationLab/morphobricks-normalization/blob/main/docs/contract.md

## 1. Folder hierarchy and labels

```
<experiment root>/<condition>/<biological repeat>/<technical repeat>/<image>.tif
```

| Rule | Detail |
|---|---|
| Images | TIFF files (`.tif`, `.tiff`, any case) and OME-Zarr images: folders whose names end in `.zarr` (any case, usually `.ome.zarr`). An OME-Zarr folder is one image, never a subfolder, and is not entered. Both may be mixed in one experiment. |
| Leaf folder | A folder that contains images and no subfolders. Only images directly in leaf folders are analysed. |
| Missing levels | A branch may stop early; the missing labels are `single`. If the root itself is the leaf, all three labels are `single`. |
| Skipped levels | Not possible: a level only exists below its parent level. |
| Folders named `single` | `single` is the MorphoBricks value of a level with one member, the value missing levels get. A folder named `single` in lower case is accepted as that value when it is the only folder in its place (system folders aside): `<condition>/single/<image>.tif` gives the biological repeat and the technical repeat `single`, the labels of `<condition>/<image>.tif`. It stays an error (`reserved_folder_name`) when it is spelled in another case (`Single`, a different label) or has other folders beside it (its level would then have several members, one called `single`). |
| Uniqueness | Folder names need only be unique among siblings, ignoring case: siblings whose names differ only in case are an error. |
| Depth | At most three folder levels below the root. A folder below the technical-repeat level is an error. |
| System files | Dot-files and dot-folders (`.DS_Store`, `._*`, `.ipynb_checkpoints`), `Thumbs.db`, `desktop.ini` and `__MACOSX` are ignored without a warning. |
| Scope | A group is identified by its full label path, e.g. `(control, bio_rep1)`. |
| Positions | Every image is exactly one position (field of view): files with several positions, scenes, series or tiles are refused (§2). Its `position_id` is the image name without its extension (`.tif`, `.tiff` or `.zarr`, any case, with a preceding `.ome`): `img_01.ome.tif` gives `img_01`. A position is identified by `condition`, `biological_repeat`, `technical_repeat` and `position_id` (cross-brick decision D02, 26 October 2026), so equal names in two technical repeats are two positions. Two images in one leaf folder with the same `position_id`, ignoring case (`img_01.tif` and `img_01.ome.zarr`), are an error (`duplicate_position_id`): the brick does not guess which is which. |
| Paths | `source_relative_path` is relative to the experiment root, with `/` separators. |

## 2. Axes

Roles X, Y, Z, C, T. Exactly one X and one Y; each role at most once. Non-singleton axes
must have a supported role. One confirmed mapping per experiment. Plane coordinates `z`,
`c`, `t` are zero-based and always written: an axis the image does not have, or has with
length 1 and no role, has coordinate 0.

| Rule | Detail |
|---|---|
| Prefill | Axis labels from metadata: `X`, `Y`, `Z`, `C`, `T` keep their role; the RGB samples axis `S` becomes C when there is no other `C`. Every other label is left unassigned for the user to choose. |
| Unsupported axes | Position-like axes (tile `R`, mosaic `M`, row `K`, column `J`) cannot have a role. If they hold more than one plane, the file is an error. |
| Singleton axes | An axis of length 1 needs no role; its index is always 0. |
| Missing axes | A file lacking axes the mapping has is read with those axes as length 1 only when the user confirmed this and only one arrangement fits; its coordinates for them are 0. |
| Notebook readiness | Confirming a valid singleton mapping resolves its missing-axis error for running; folder, exclusion and all other metadata errors still block processing. |
| Series | Only the first image series, at full-resolution level 0. A file with more than one series is an error. |
| OME-Zarr axes | Taken from the OME-NGFF metadata (0.4 and 0.5; 0.1–0.3 fixed `t, c, z, y, x` or axis names): a time axis is T, a channel axis C, space axes named z, y, x are Z, Y, X; any other axis has no role. An OME-Zarr image with the experiment mapping's arrangement uses the mapping; others are matched by role: a Z, C or T axis the experiment lacks is allowed only with length 1, an axis without a role only with length 1, and experiment roles the image lacks follow the missing-axes rule. Otherwise `ome_zarr_axes_mismatch` is an error. |
| OME-Zarr images | Only level 0 (the first dataset of the first multiscales image) is read. A store with more than one image (several multiscales, a bioformats2raw folder with several series, a plate, a well with several fields) is an error (`multiple_series`). |
| OME-Zarr labels | The label images of an image's OME-NGFF `labels` group (0.4 and 0.5) are listed with `ngff_label_names` and opened, level 0, with `open_ngff_labels` (by name; the name may be left out when there is one). They are never analysed; later bricks such as patch extraction use them as masks. |
| Experiment mapping | Defined on the TIFF arrangement when the experiment has TIFF files; in an experiment of OME-Zarr images only, on their arrangement with the most axes. |
| Channel names, significant bits | Recorded in the run manifest when metadata provides them; not part of the CSV. Channel names that differ at the same channel index are a warning. |
| Physical sizes | Read with the header, not part of the CSV or the manifest, and compared between images in the folder check (§11, in micrometres with each axis's own unit; a size without a known unit cannot be compared): `PhysicalSizes` with the size of one step along X, Y, Z and T and each axis's unit (OME-NGFF unit names). TIFF: OME `PhysicalSize{X,Y,Z}` with their own units (micrometres when left out) and `TimeIncrement`, or ImageJ resolution, `spacing` and `finterval` with `unit`, `yunit`, `zunit`. OME-Zarr (0.4 and 0.5): the `scale` of level 0's `coordinateTransformations`, times the multiscales entry's own `scale` when given, with each axis's `unit` (common spellings such as `µm`, `um`, `micron` and `s` given as UDUNITS-2 names, others as written). Ångström (`Å`) and ImageJ's escaped micro sign (`\u00B5m`) are recognised. Nothing is guessed: what the metadata does not give is None; a scale in a separate file (`path`) or not one number per axis gives no sizes; an OME-Zarr scale without a unit is kept with the unit None. |
| Plane counts | Images may have different numbers of channels, depth slices, time points or planes along an unassigned dimension; this is a warning (§11). |

## 3. Pixel types

Defined by the [normalization brick's contract][normalization contract] §1.2.1: integers
int8–uint64 and float32/float64, everything else rejected, with their default and allowed
bin counts. This brick adds: one dtype per experiment (`mixed_dtypes` error), and all
planes of an experiment use the same bin count.

## 4. Histogram semantics

Defined by the [normalization brick's contract][normalization contract] §1.2.2: each plane
is binned over its own exact range; integer bins have whole-number limits, float bins
equal widths; every pixel is within half a bin of its representative; a plane read in
blocks gives exactly the row of the whole plane.

## 5. CSV schema (`plane_histograms.csv`, schema version 2)

Defined by the [normalization brick's contract][normalization contract] §6.2 (columns) and
§6.1 (shared columns). This brick writes it next to `run_manifest.json` (§9), with plane
coordinates and labels as in §1–§2.

This brick fills `position_id` in every row, with the image's position identifier (§1).
The file-name fallback follows cross-brick decision D02 (one file is exactly one position
here); the normalization contract's note that `position_id` is never inferred from file
names predates D02. The columns and `schema_version` are unchanged. A table written before
this rule, with `position_id` blank, is filled on the next run without reading pixels.

## 6. Group statistics

Defined by the [normalization brick's contract][normalization contract] §1.2.3: groups
pool their planes' (representative, count) pairs and never average per-plane statistics.

## 7. Normalization parameters

Defined by the [normalization brick's contract][normalization contract] §1.2.4: every
method is an offset and a scale, `(pixels − offset) / scale`.

## 8. Validation severities

| Severity | Situations | Effect |
|---|---|---|
| Error | Experiment design not confirmed (`design_not_confirmed`) or a repeat group holding two folders of one condition (`repeat_group_conflict`), when a design is used (§12); mixed dtypes; unreadable TIFF (header or pixel data) or OME-Zarr folder in a leaf folder that is not excluded; OME-Zarr images, or converting TIFF images to OME-Zarr, without the `zarr` extra; OME-Zarr images whose declared axes do not fit the experiment's roles; every image excluded; more than one image series in a file; unsupported sample format or axis; missing/duplicate X or Y, duplicate roles, unassigned non-singleton axis, inconsistent mapping; plane that cannot be read within the memory limit, not even in blocks (for example a plane stored compressed in one piece); folder named `single` in another case, or beside other folders (§1); sibling folders differing only in case; images in one leaf folder with the same position identifier (§1); folder below the technical-repeat level; output folder inside the experiment; no usable planes | Processing is blocked |
| Warning | Files that are not images; empty folders; images outside leaf folders; images excluded by the user, or excluded paths that match no image; channel names differing between files; non-finite pixels; different image widths or heights; different numbers of channels, depth slices, time points or other planes; physical pixel sizes that differ or are known for only some images; unbalanced numbers of images; biological repeats missing from some conditions (§11); planes without finite pixels while others remain; planes over the memory limit that are read in blocks | Processing continues; the item is reported |

Every message states what happened in plain language, lists the affected paths, and
suggests an action. Technical details go to the diagnostic log.

In notebook step 1, message details start collapsed with orange warning and red error
counts visible outside the dropdown. Collapsing details does not change severity or
whether processing is blocked. The consistency issues of §11 are shown in their own
**Dataset consistency** panel, next to the balance table, and not repeated under the
other messages.

## 9. Run manifest (`run_manifest.json`, manifest version 1)

UTF-8 JSON next to `plane_histograms.csv`, written after the table and only when the run
succeeded.

Both files are prepared in a temporary folder before either earlier output is replaced.
OME-Zarr copies of TIFF images (§10), when requested, are written to the same temporary
folder and published first, so the manifest never names a copy that is not in place. Earlier files
are backed up before publication; earlier copies that are replaced or removed are moved
into the backup folder. A caught replacement failure restores the
previous state, including the absence of files on a first run. If restoration fails, the
backup folder is retained and its location is reported for manual recovery. Failures to
remove temporary files after a successful publication are logged.

The two replacements are not jointly atomic: forced process termination between them can
leave a mismatched pair, detected by `csv_sha256` on the next run and rebuilt. Only one run
may write to a given output folder at a time. This write protocol does not change either
file's schema or statistical interpretation.

| Key | Meaning |
|---|---|
| `manifest_version`, `schema_version` | Manifest format (`1`) and CSV format (`2`). |
| `written_at` | UTC time of writing, ISO 8601. |
| `experiment_root` | Absolute path of the experiment folder, for information. |
| `dtype` | Pixel type of the experiment. |
| `settings` | `bin_count`, `memory_limit` (bytes), `missing_as_singleton`, `exclude` (sorted relative paths), `convert_tiffs` (whether TIFF images are converted to OME-Zarr and processed from the copies; false when absent), `conversion` (`chunk_size`, `compression`, `compression_level`, `pyramid_levels` — null for automatic; defaults when absent). |
| `axis_mapping` | `axes` (file axis labels) and `roles` (`X`, `Y`, `Z`, `C`, `T` or null per axis). |
| `labels` | One entry per leaf folder: `folder` (relative path, `.` for the root) and its three labels. |
| `files` | One entry per analysed file, sorted by path: `relative_path`, `size` (bytes), `mtime_ns`, the three labels, `axes`, `singleton_roles`, `plane_count`, `channel_names` (list, null for unnamed channels; empty if none in metadata), `significant_bits` (null if not in metadata), `ome_zarr_path` (for a TIFF converted to OME-Zarr, the copy relative to the output folder; null otherwise or when the key is absent), `read_in_blocks` (true when the image's planes are over the memory limit and are read in blocks; information only, false when absent), `position_id` (the image's position identifier, §1; null when absent, in manifests written before it). For an OME-Zarr image, `size` is the total size of its files and `mtime_ns` the time of its newest file. |
| `csv_sha256` | SHA-256 of the table written with this manifest. |
| `experiment_design_sha256` | SHA-256 of the `experiment_design.csv` written with this run (§12), so later bricks can check that the design they read is the one used; null when the run had no design, or absent in manifests written before it. Optional key: the manifest stays version 1. |
| `versions` | Versions of `morphobricks-preprocessing`, `morphobricks-normalization` (which computes the histograms and writes the CSV; absent in manifests written before it), `numpy`, `tifffile` and Python. Information only: a different version never causes a rebuild. |

Channel names and significant bits come from OME metadata and are for information only.

## 10. Converting TIFF images to OME-Zarr (optional)

When `convert_tiffs` is on, every TIFF image is converted to OME-Zarr in the output folder
and its rows are computed from the converted copy, like for any OME-Zarr image. The copy
holds the source values, so the rows equal those of reading the TIFF directly; the
`source_relative_path` of the rows stays the TIFF's path. OME-Zarr input images are never
converted.

The notebook groups conversion controls in an expandable panel with storage block size,
lossless compression and preview-resolution cards. Choosing conversion opens the panel;
collapsing it preserves the settings and leaves the disk-space warning visible. This
presentation does not change the conversion rules below.

| Rule | Detail |
|---|---|
| Location | `<output>/ome_zarr/<source_relative_path without .tif/.tiff/.ome.tif>.ome.zarr` |
| Format | OME-NGFF 0.4, Zarr format 2, `/` chunk separator |
| Compression | Lossless: Blosc LZ4 (default), Blosc Zstandard or none; level 1–9 (default 5) |
| Axes | `t`, `c`, `z`, `y`, `x` in that order, only the roles of the confirmed mapping (missing axes resolved as singletons have length 1; axes without a role are dropped) |
| Level 0 | The source values, unchanged, in the source pixel type; chunks of one plane along Z, C and T and at most the chunk size (16 to 8,192 px, default 1,024) in X and Y |
| Further levels | 2 x 2 block means rounded to the pixel type; automatic (default: until a plane fits in 512 x 512, at most 8 levels in all), none, or 1 to 7 levels; for viewing only |
| Large planes | A plane read in blocks is written block by block; its further levels are then built from the stored level above, in regions of even size, and equal those of a plane written whole |
| Scale | Physical pixel sizes, z-step and time step from OME or ImageJ metadata, with OME-NGFF units, each axis with its own unit (OME `PhysicalSizeXUnit`, `PhysicalSizeYUnit`, `PhysicalSizeZUnit`, micrometres when left out; ImageJ `unit`, `yunit`, `zunit`); 1 when unknown |
| Channels | `omero` block with channel names from metadata (or `Channel <i>`) and each channel's observed minimum and maximum as display window |
| Updates | Copies of unchanged images are kept, of new or changed images rewritten, and of images no longer analysed removed; a failed run leaves the earlier copies as they were (see §9 for publication) |
| Switching | Turning conversion on or off recomputes every TIFF row; turning it off removes the copies the manifest recorded |
| Missing copy | A converted TIFF whose copy was deleted counts as changed and is converted again |
| Outdated units | A copy whose axis units differ from those its TIFF's metadata gives now (copies written before each axis had its own unit, plan §12 question 24) counts as changed and is converted again; its rows do not change. Only the copy's `.zattrs` is read to tell |
| Settings change | Copies written with other conversion settings (recorded in the manifest) are written again; their rows do not change |

## 11. Dataset consistency (folder check)

The folder check reads only folder names and image headers. It gathers every consistency
issue of an experiment in one place (normalization plan N37). The new checks are warnings:
they never block processing, and each names the affected images or folders and what to check.

| Code | Severity | Rule |
|---|---|---|
| `unbalanced_images` | Warning | Images are counted per condition and biological repeat, technical repeats added up. A count below half or above twice the median of all counts (exactly half or twice is balanced) is reported, with the biological-repeat folders. |
| `repeat_missing_conditions` | Warning | With more than one condition, a biological repeat not found in every condition is reported, with its folders. Repeats are matched across conditions by their repeat group (§12; by default the folder name). It is still used. |
| `different_pixel_sizes` | Warning | The physical pixel size (X by Y) differs between images, or the z-step differs between images with more than one depth slice. Sizes are compared in micrometres, rounded to four significant digits. |
| `missing_pixel_size` | Warning | The pixel size (or the z-step of images with more than one depth slice) is known for some images and not for others. Nothing is reported when no image has it. |
| `different_plane_counts` | Warning | The number of channels (the `C` dimension, or the RGB samples `S` when there is no `C`), depth slices (`Z`), time points (`T`) or planes along any other dimension except X and Y differs between images. A missing dimension counts as 1. |
| `different_sizes` | Warning | The image width or height differs between images (TIFF and OME-Zarr). The other dimensions are compared by `different_plane_counts`. |

| Rule | Detail |
|---|---|
| Counts | Only images to analyse count: excluded images are left out, and a folder whose images are all excluded is not a biological repeat. A biological repeat with images directly in its folder has no technical-repeat folders (count 0). |
| Balance table | Conditions as columns, repeat groups as rows (§12; by default the biological-repeat folder name, `single` when there are no biological-repeat folders); each cell holds the number of images and of technical-repeat folders. |
| Pixel sizes | TIFF: OME `PhysicalSizeX`, `PhysicalSizeY`, `PhysicalSizeZ` with their units (micrometres when not given), or ImageJ resolution tags and `spacing` with the ImageJ unit (unknown without a unit). OME-Zarr: the level-0 scale (`coordinateTransformations` of the first dataset, times the image's own scale when given) with the axis units; a scale without a unit is unknown. Sizes are never guessed. |
| Panel | The notebook shows the balance table with the issues `mixed_dtypes`, `inconsistent_dimensions`, `missing_axes`, `inconsistent_mapping`, `different_sizes`, `different_plane_counts`, `channel_names_differ`, `different_pixel_sizes`, `missing_pixel_size`, `unbalanced_images` and `repeat_missing_conditions`. The command line `check` prints the balance table and every issue; `run` reports the same warnings. |
| Outputs | Unchanged: the checks write nothing, and `plane_histograms.csv` and `run_manifest.json` do not change. |

## 12. Experiment design (`experiment_design.csv`, schema version 1)

A **repeat group** is one biological repeat across all conditions (normalization plan
N30): later bricks pool it over the conditions. Before processing, the user pairs the
biological repeats, confirms that each repeat group is comparable (N35), and may mark
control conditions (N36). The answers are written to `experiment_design.csv` in the output
folder.

| Column | Meaning |
|---|---|
| `condition`, `biological_repeat` | Folder labels, as in `plane_histograms.csv` (`single` when absent) |
| `repeat_group` | Label of the repeat group the folder belongs to (default: `biological_repeat`) |
| `control` | `true` when the condition is marked as a control |
| `image_count`, `technical_repeat_count` | Images (technical repeats added up) and technical-repeat folders (0 when the images are directly in the biological-repeat folder), at the time of confirmation; the current counts while unconfirmed |
| `confirmed` | `true` once the repeat group is confirmed |
| `confirmed_on` | Date of the confirmation (ISO 8601), empty while unconfirmed |
| `schema_version` | `1` |

| Rule | Detail |
|---|---|
| Rows | One per condition and biological-repeat folder with images to analyse, sorted by condition, then biological repeat. UTF-8, LF line endings, header row, the columns in the order above; booleans written `true`/`false`. The same design gives the same bytes. |
| Pairing | By default repeats with the same folder name in different conditions are one group, named after the folder. The user may move a folder into another group. A group holds at most one folder per condition (`repeat_group_conflict`, an error). A group not found in every condition is used as it is, with the warning `repeat_missing_conditions` (§11). |
| Confirmation | Per repeat group: "Were these images prepared and acquired under the same circumstances, so they are comparable with each other?". A group is confirmed when all its rows are. Without biological-repeat folders (every condition `single`), the whole experiment is one group and the question is asked once (N31). Missing technical-repeat folders need no question. |
| Controls | None, one or several conditions; every row of a condition has the same value. Recorded only. |
| Reruns | The file in the output folder is read and brought up to date with the folders found: folders in it keep their group, control mark and confirmation (added or removed images within a folder keep it too, with the counts recorded at confirmation); a new folder joins the group named after it and inherits its condition's control mark, and that group is asked again; folders no longer found are dropped without asking again. |
| Reading | `read_experiment_design` reads labels as text; `control` and `confirmed` accept `true`/`false` in any case, also `yes`/`no` and `1`/`0`, and empty as false; an empty `repeat_group` means the folder name; a byte-order mark is ignored. A missing column, an invalid value, a folder listed twice, rows of one condition disagreeing on `control` or another `schema_version` raise `DesignFormatError`. `ExperimentDesign.repeat_groups()` gives the `{(condition, biological_repeat): repeat_group}` mapping the normalization brick takes. |
| Notebook | Step 1 asks once the labels are confirmed and writes the file after every answer (unconfirmed groups with `confirmed=false`). A design confirmed for the same folders is loaded with the questions folded away behind **Change the experiment design**. Step 3 runs only once every group is confirmed. |
| Command line | `run` reads the file from the output folder, or `--design FILE`. Without a fully confirmed design it writes the design brought up to date with the folders (a template with `confirmed=false` for the groups still to confirm, keeping the answers already given) to the output folder, explains the columns, and stops with status 1. Confirmed rows without a date get the date of the run. |
| Run | `run_experiment(..., design=...)` brings the design up to date with the folders and stops with `design_not_confirmed` or `repeat_group_conflict` if it is not fully confirmed. Otherwise the file is written with the table and published before the manifest, which records its SHA-256 (§9). Without a design the run is unchanged: no file is written, an existing one is left as it is, and `experiment_design_sha256` is null. `plane_histograms.csv` does not depend on the design. |
