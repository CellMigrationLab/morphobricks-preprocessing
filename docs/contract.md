# Preprocessing Brick Contract (Milestone 0)

This document fixes the rules other bricks can rely on. Changing any of them requires a
new `schema_version`.

Sections 3–7 (pixel types, histograms, the CSV, group statistics, normalization
parameters) are defined by the [normalization brick's contract][normalization contract]
(§1.2 and §8), whose code computes them (`morphobricks_normalization`, plan §12 question
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

Defined by the [normalization brick's contract][normalization contract] §8.2 (columns) and
§8.1 (shared format). This brick writes it next to `run_manifest.json` (§9), with plane
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
| Error | Mixed dtypes; unreadable TIFF (header or pixel data) or OME-Zarr folder in a leaf folder that is not excluded; OME-Zarr images, or converting TIFF images to OME-Zarr, without the `zarr` extra; OME-Zarr images whose declared axes do not fit the experiment's roles; every image excluded; more than one image series in a file; unsupported sample format or axis; missing/duplicate X or Y, duplicate roles, unassigned non-singleton axis, inconsistent mapping; plane that cannot be read within the memory limit, not even in blocks (for example a plane stored compressed in one piece); folder named `single` in another case, or beside other folders (§1); sibling folders differing only in case; images in one leaf folder with the same position identifier (§1); folder below the technical-repeat level; output folder inside the experiment; no usable planes | Processing is blocked |
| Warning | Files that are not images; empty folders; images outside leaf folders; images excluded by the user, or excluded paths that match no image; channel names differing between files; non-finite pixels; different image sizes with a valid mapping; planes without finite pixels while others remain; planes over the memory limit that are read in blocks | Processing continues; the item is reported |

Every message states what happened in plain language, lists the affected paths, and
suggests an action. Technical details go to the diagnostic log.

In notebook step 1, message details start collapsed with orange warning and red error
counts visible outside the dropdown. Collapsing details does not change severity or
whether processing is blocked.

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
| Scale | Physical pixel sizes, z-step and time step from OME or ImageJ metadata, with OME-NGFF units; 1 when unknown |
| Channels | `omero` block with channel names from metadata (or `Channel <i>`) and each channel's observed minimum and maximum as display window |
| Updates | Copies of unchanged images are kept, of new or changed images rewritten, and of images no longer analysed removed; a failed run leaves the earlier copies as they were (see §9 for publication) |
| Switching | Turning conversion on or off recomputes every TIFF row; turning it off removes the copies the manifest recorded |
| Missing copy | A converted TIFF whose copy was deleted counts as changed and is converted again |
| Settings change | Copies written with other conversion settings (recorded in the manifest) are written again; their rows do not change |
