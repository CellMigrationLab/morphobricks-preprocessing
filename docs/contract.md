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
| Leaf folder | A folder that contains images and no subfolders. Only images directly in leaf folders are analysed. A folder holding both images and subfolders is the error `tiff_outside_leaf` (OME-Zarr images included): its images have no level, and every MorphoBricks brick refuses such a folder rather than skip them. |
| Missing levels | A branch may stop early; the missing labels are `single`. If the root itself is the leaf, all three labels are `single`. |
| Skipped levels | Not possible: a level only exists below its parent level. |
| Folders named `single` | `single` is the MorphoBricks value of a level with one member, the value missing levels get. A folder named `single` in lower case is accepted as that value when it is the only folder in its place (system folders aside): `<condition>/single/<image>.tif` gives the biological repeat and the technical repeat `single`, the labels of `<condition>/<image>.tif`. It stays an error (`reserved_folder_name`) when it is spelled in another case (`Single`, a different label) or has other folders beside it (its level would then have several members, one called `single`). |
| Uniqueness | Folder names need only be unique among siblings, ignoring case. Two folder names of one level (condition, biological repeat or technical repeat) that differ only in case are the error `case_conflict`, whether siblings or anywhere in the experiment (`ctrl/Rep1` and `treated/rep1`; decision D-e, §16; siblings only before D86). |
| Names | Every label and `position_id` passes `check_identity_text` (§16): no leading or trailing whitespace, and not `""`, `NA`, `nan`, `none`, `null`, `n/a` or `<NA>` in any case, which table readers turn into a missing value. A folder or image name that fails is the error `invalid_name` (decision D-e; accepted before D86). |
| Depth | At most three folder levels below the root. A folder below the technical-repeat level is an error. |
| System files | Dot-files and dot-folders (`.DS_Store`, `._*`, `.ipynb_checkpoints`), `Thumbs.db`, `desktop.ini` and `__MACOSX` are ignored without a warning. |
| Scope | A group is identified by its full label path, e.g. `(control, bio_rep1)`. |
| Positions | Every image is exactly one position (field of view): files with several positions, scenes, series or tiles are refused (§2). Its `position_id` is the image name without its extension (`.tif`, `.tiff` or `.zarr`, any case, with a preceding `.ome`): `img_01.ome.tif` gives `img_01`; a name that is only an extension (`.tif`, `.ome.zarr`) is refused. `discovery.position_id` is the one function every MorphoBricks brick names positions with (cross-brick decision D85, 29 October 2026), so it also removes the other image extensions bricks read (`.png`, `.jpg`, `.jpeg`, `.bmp`, `.gif`, `.npy`; naming only: this brick still analyses TIFF and Zarr images only). A position is identified by `condition`, `biological_repeat`, `technical_repeat` and `position_id` (cross-brick decision D02, 26 October 2026), so equal names in two technical repeats are two positions. Two images in one leaf folder with the same `position_id`, ignoring case (`img_01.tif` and `img_01.ome.zarr`), are an error (`duplicate_position_id`): the brick does not guess which is which. |
| Masks in the experiment | Masks belong in a masks folder outside the experiment (§15); discovery screens for masks it would analyse as images (decision D-i, `mask_name_issues`, §16): `image_named_as_mask` (an error since v0.4.0, design v0.4 §3: rename the file or exclude it; an excluded file is not reported) for an image named after another image's `position_id`, then `_mask`, `_masks`, `_cp_masks`, `_labels`, `_label`, `_seg` or `_segmentation` (any case, anywhere in the experiment: `f1_mask.tif` beside `f1.tif`; `A1_2.tif` and `f1_DNA.tif` are images), and `mask_folder_in_experiment` for a folder named like a masks folder (`masks`, `ctrl_masks`, `CellLabels`) holding images named as masks of the other images (`<position>` or `<position>_<anything>`), a warning: such files may be images. |
| Paths | `source_relative_path` is relative to the experiment root, with `/` separators. |

## 2. Axes

Roles X, Y, Z, C, T. Exactly one X and one Y; each role at most once. Non-singleton axes
must have a supported role. One confirmed mapping per experiment. A row of the table is
an XYC plane: every channel of an image at one Z and T position (§5). Plane coordinates
`z` and `t` are zero-based and always written: an axis the image does not have, or has
with length 1 and no role, has coordinate 0. Channels are numbered from 0 in the image's
channel order; an image without a C axis has one channel.

| Rule | Detail |
|---|---|
| Prefill | Axis labels from metadata: `X`, `Y`, `Z`, `C`, `T` keep their role; the RGB samples axis `S` becomes C when there is no other `C`. Every other label is left unassigned for the user to choose. |
| Axis check (v0.4.0) | Every TIFF's confirmed roles are compared with the axes its own header names (`named_axes`, §16), position by position (`header_axis_issue`, `axis_disagreements`): a named axis keeps its letter as role, except that the RGB samples `S` may be C when the header has no C, and a length-1 axis may have no role. Confirmed roles that give the named Y and X the other way round are the **error** `axes_yx_swapped`; any other role for a named axis (`ZYX` confirmed `TYX`, a named angle or lambda axis given a role) is the **error** `axes_contradict_header` (a named axis longer than 1 without a role is `unassigned_axis`, a position axis `unsupported_axis`). Roles confirmed for a plain stack that put X before Y, such as `TXY`, are the **error** `axes_x_before_y` (owner decision of 1 November 2026): nothing is ever read transposed, so the images are saved again with Y before X, or as OME-TIFF copies with Y before X. Each error names every file with its header axes and confirmed roles and the likely cause (`likely_axis_cause`: tifffile labels a 3-D stack saved with `imagej=True` or `ome=True` `CYX`; an ImageJ stack of slices reads as Z; rows and columns exchanged), and offers two fixes: correct the axes, or save named-axis copies and run preprocessing on them. Such an image is never read: not transposed, not under a contradicted declaration. An axis the metadata does not name (a plain stack `IYX`, `QYX`, `QQYX`, or shaped metadata with a placeholder `QYX`; a plain 2D `YX` page and RGB `YXS` are named, decision D-d) takes the role the user confirmed: the image is read with it, with the **warning** `axes_not_named` recommending named copies before segmenting in other tools, and recorded as `axes_from: "confirmed"` (`AxisReview.axes_from`; `"header"` for every other image). A confirmation covers only the files it was given for, as they were then (`axis_check.ConfirmedAxes`, made by `fingerprint.confirm_axes` with each file's fingerprint; audit 2 B-1): a plain stack outside it, new or changed since (another fingerprint), is the **error** `plain_stack_not_confirmed` and must be confirmed again; it is never read with roles confirmed for other images. An `AxisMapping` given to `run_experiment` confirms every file the run finds. OME-Zarr images take their roles from their OME-NGFF axes and never have these issues. Since one mapping covers each TIFF arrangement, all files of one arrangement pass or fail together. The notebook shows these codes as one card; when the roles agree with every header, the notebook and `run` say so ("Axes checked"). Before v0.4.0 a swap was read transposed and a contradiction under the declaration, silently. |
| Unsupported axes | Position-like axes (tile `R`, mosaic `M`, row `K`, column `J`) cannot have a role. If they hold more than one plane, the file is an error. |
| Singleton axes | An axis of length 1 needs no role; its index is always 0. |
| Missing axes | A file lacking axes the mapping has is read with those axes as length 1 only when the user confirmed this and only one arrangement fits; its coordinates for them are 0. |
| Notebook readiness | Confirming a valid singleton mapping resolves its missing-axis error for running; folder, exclusion and all other metadata errors still block processing. |
| Series | Only the first image series, at full-resolution level 0. A file with more than one series is an error. |
| OME-Zarr axes | Taken from the OME-NGFF metadata (0.4 and 0.5; 0.1–0.3 fixed `t, c, z, y, x` or axis names): a time axis is T, a channel axis C, space axes named z, y, x are Z, Y, X; any other axis has no role. An OME-Zarr image with the experiment mapping's arrangement uses the mapping; others are matched by role: a Z, C or T axis the experiment lacks is allowed only with length 1, an axis without a role only with length 1, and experiment roles the image lacks follow the missing-axes rule. Otherwise `ome_zarr_axes_mismatch` is an error. |
| OME-Zarr images | Only level 0 (the first dataset of the first multiscales image) is read. A store with more than one image (several multiscales, a bioformats2raw folder with several series, a plate, a well with several fields) is an error (`multiple_series`). |
| OME-Zarr labels | The label images of an image's OME-NGFF `labels` group (0.4 and 0.5) are listed with `ngff_label_names` and opened, level 0, with `open_ngff_labels` (by name; the name may be left out when there is one). They are never analysed; later bricks such as patch extraction use them as masks. |
| Experiment mapping | Defined on the TIFF arrangement when the experiment has TIFF files; in an experiment of OME-Zarr images only, on their arrangement with the most axes. |
| Channel names, significant bits | Recorded in the run manifest when metadata provides them; not part of the CSV. Channel names that differ at the same channel index are the error `channel_names_differ` (issue #37): channels are matched by their number, so two stains at one number would be pooled as one channel. Only named channels are compared; a channel without a name in some images is not. Names are read without leading and trailing whitespace, for every image (TIFF, OME-Zarr and this brick's own copies), so `DAPI ` and `DAPI` are one name; the manifest records it (`channel_names_trimmed`, §9). Whitespace inside a name is compared exactly (`DA PI` and `DAPI` differ), with no override. An OME-Zarr channel label that is not text is malformed metadata: the image is unreadable (`unreadable_ome_zarr`), never read as an unnamed channel. |
| Physical sizes | Read with the header, not part of the CSV or the manifest, and compared between images in the folder check (§11, in micrometres with each axis's own unit; a size without a known unit cannot be compared): `PhysicalSizes` with the size of one step along X, Y, Z and T and each axis's unit (OME-NGFF unit names). TIFF: OME `PhysicalSize{X,Y,Z}` with their own units (micrometres when left out) and `TimeIncrement`, or ImageJ resolution, `spacing` and `finterval` with `unit`, `yunit`, `zunit`. OME-Zarr (0.4 and 0.5): the `scale` of level 0's `coordinateTransformations`, times the multiscales entry's own `scale` when given, with each axis's `unit` (common spellings such as `µm`, `um`, `micron` and `s` given as UDUNITS-2 names, others as written). Length units are the microscopy units of MorphoBricks decision D-a2 (§16): `pm`, `Å`/`angstrom`, `nm`, `µm` (`um`, `micron(s)`, `micrometer(s)`, `micrometre(s)`), `mm`, `cm`, `m`, and the names of each in US and British spelling (`nanometre`, ...); ImageJ's escaped micro sign (`\u00B5m`) is recognised. A size in any other unit (`inch`, `decimeter`, ...) is not converted to micrometres, so it cannot be compared: that axis is uncalibrated, and the unit as written is kept in `PhysicalSizes.unsupported_units` (axis letter and unit, information only, not part of equality) so that the folder check names it (`unsupported_pixel_size_unit`, §11). A plain TIFF's resolution tags (no OME or ImageJ metadata, for example 300 dpi with the unit inch) are never a pixel size (decision D-c): such an image has no calibration. Nothing is guessed: what the metadata does not give is None; a scale in a separate file (`path`) or not one number per axis gives no sizes; an OME-Zarr scale without a unit is kept with the unit None, except on the axes a multiscales entry's `metadata` lists as `unknown_scale` (copies written by this brick up to v0.3, §10), whose sizes are None. |
| Plane counts | Images may have different numbers of depth slices, time points or planes along an unassigned dimension; this is a warning (§11). |
| Channel counts | Every image of an experiment has the same number of channels (normalization plan N43): along the C axis, 1 without one (also when a missing C axis is confirmed as length 1). A different number is the error `different_channel_counts`, found before any image is read: in the folder check from the headers (the `C` dimension, or the RGB samples `S` when there is no `C`), and again with the confirmed axis roles, which decide (§11). |

## 3. Pixel types

Defined by the [normalization brick's contract][normalization contract] §1.2.1: integers
int8–uint64 and float32/float64, everything else rejected, with their default and allowed
bin counts. This brick adds: one dtype per experiment (`mixed_dtypes` error), and all
planes of an experiment use the same bin count.

## 4. Histogram semantics

Defined by the [normalization brick's contract][normalization contract] §1.2.2 and §1.4:
each plane is binned over its own exact range by default; integer bins have whole-number
limits, float bins equal widths; every pixel is within half a bin of its representative; a
plane read in blocks gives exactly the row of the whole plane.

The histogram range and its check (follow-up P6, normalization brick N54–N60):

| Rule | Detail |
|---|---|
| Range | `HistogramSettings.histogram_range` of the normalization brick: `minmax` (default, each plane's minimum to maximum) or `robust` (bins between the plane's `robust_quantiles`, default 0.01 % and 99.99 %, the pixels outside counted as underflow and overflow). It applies to histograms that are not exact (float planes, and integer planes with more values than bins); exact 8- and 16-bit histograms are the same either way. One setting per run, recorded in the manifest (`settings.histogram`, §9); changing it, or any other `HistogramSettings` value, recomputes every row. |
| Range check | Every plane is binned with the brick's `histogram_with_range_check`, which returns the histogram and its `RangeCheck` in the same passes over the pixels (whole or in blocks, with the same result). The memory estimate of a plane includes the check (`estimate_histogram_memory(..., settings, range_check=True)`). |
| Warning | `narrow_histogram_range`, one per channel, from the brick's `range_check_issues`: in the named planes the central range (1st to 99.8th percentile) covers fewer than `min_central_bins` (100) bins. With the default range it says how many bins the robust range would give and suggests computing the statistics again with it. Only this brick reports it (N57). The narrow planes are recorded in the manifest (`narrow_histogram_ranges`, §9), so a later run that keeps their rows reports them again without reading the images. |
| Run and command line | `run_experiment(..., histogram_settings=...)`; `run --histogram-range minmax|robust` and `--robust-quantiles LOW HIGH`. The notebook offers the range in step 3's **Settings** (the recommended settings use `minmax`). |

## 5. CSV schema (`plane_histograms.csv`, schema version 4)

Defined by the [normalization brick's contract][normalization contract] §6.2 (columns) and
§6.1 (shared and per-channel columns). This brick writes it with that brick's writer next
to `run_manifest.json` (§9), with plane coordinates and labels as in §1–§2: one row per
XYC plane (an image at one Z and T position), each channel's extrema, pixel counts and
bins in its own columns (`Intensity_MinIntensity_C0`, `Histogram_Count_C1_00042`, …),
`plane_id` = `<source_relative_path>::z=…,t=…`, rows sorted by path, then `z`, then `t`
(follow-up P3b, plan §12 question 27). Version 4 adds each channel's underflow and overflow
(`Histogram_{Underflow,Overflow}{Count,Sum,SumOfSquares}_C<i>`, normalization brick N56),
zero with the default range (§4, follow-up P6).

| Rule | Detail |
|---|---|
| Reading | The channels of a plane are read and binned one at a time; only one channel's pixels are in memory, next to the finished histograms of the plane's other channels. The memory estimate of a plane (plan §5) includes those histograms' bin counts, and one decision, whole or in blocks, covers every channel of every plane of an image. |
| Histograms | Each channel's histogram is the one schema version 2 held in that channel's row, whole or in blocks (tested against a table written by version 2). |
| Earlier versions | A table of schema version 1 or 2 (one row per plane and channel, with a `c` column), or 3 (without the underflow and overflow columns), is recomputed completely on the next run, like any change of version (§9). |

This brick fills `position_id` in every row, with the image's position identifier (§1).
The file-name fallback follows cross-brick decision D02 (one file is exactly one position
here); the normalization contract's note that `position_id` is never inferred from file
names predates D02. A table written before this rule, with `position_id` blank, is filled
on the next run without reading pixels.

## 6. Group statistics

Defined by the [normalization brick's contract][normalization contract] §1.2.3: groups
pool their planes' (representative, count) pairs and never average per-plane statistics.

## 7. Normalization parameters

Defined by the [normalization brick's contract][normalization contract] §1.2.4: every
method is an offset and a scale, `(pixels − offset) / scale`.

## 8. Validation severities

| Severity | Situations | Effect |
|---|---|---|
| Error | Experiment design not confirmed (`design_not_confirmed`) or a repeat group holding two folders of one condition (`repeat_group_conflict`), when a design is used (§12); image type RGB for images that are not three channels of an integer type (`rgb_not_possible`, §13); mixed dtypes; images with different numbers of channels (`different_channel_counts`, §2) or different channel names at the same channel number (`channel_names_differ`, §2); unreadable TIFF (header or pixel data) or OME-Zarr folder in a leaf folder that is not excluded; OME-Zarr images without the `zarr` extra; OME-Zarr images whose declared axes do not fit the experiment's roles; every image excluded; more than one image series in a file; unsupported sample format or axis; missing/duplicate X or Y, duplicate roles, unassigned non-singleton axis, inconsistent mapping; an image whose confirmed roles swap Y and X, contradict its header or put X before Y (`axes_yx_swapped`, `axes_contradict_header`, `axes_x_before_y`, §2); plane that cannot be read within the memory limit, not even in blocks (for example a plane stored compressed in one piece); folder named `single` in another case, or beside other folders (§1); folders of one level differing only in case, anywhere in the experiment (`case_conflict`); folder or image names with outer whitespace or reading as a missing value (`invalid_name`, §1); images in one leaf folder with the same position identifier (§1); folder below the technical-repeat level; images in a folder that also holds subfolders (`tiff_outside_leaf`, §1); output folder inside the experiment or inside the masks folder (`output_inside_masks`, §15); images named as masks of other images (`image_named_as_mask`, §1); no usable planes; when masks are given, a masks folder missing or inside the experiment, mask names differing only in case, two masks of one type for an image, a mask whose name could belong to two images or types, or a mask that is unreadable, has axes other than Y, X and named or stated Z/T (`mask_not_2d`), has stated axes that do not fit it or contradict the axes its metadata names (`invalid_mask_axes`, `mask_axes_contradict_header`), is stored with X before Y or with its image's Y and X sizes swapped (`mask_layout`; never transposed), is of another size than its image (in `run`, its image's Y and X under the confirmed roles; not reported for an image with an axis error), is not of integers or booleans, has other depth slices or time points than the image's other masks or, in `run`, than the image (§15) | Processing is blocked |
| Warning | Images whose metadata does not name their axes, read with the confirmed roles (`axes_not_named`, §2); folders named like a masks folder holding images named as masks (`mask_folder_in_experiment`, §1); intensity checks of the written table (`image_intensity_outlier`, `repeat_intensity_shift`, `saturated_image`, `no_intensity_spread`, §14), reported apart from the run's issues; files that are not images; empty folders; images excluded by the user, or excluded paths that match no image; non-finite pixels; different image widths or heights; different numbers of depth slices, time points or other planes; physical pixel sizes that differ or are known for only some images; unbalanced numbers of images; biological repeats missing from some conditions (§11); planes without finite pixels while others remain; channels whose central intensity range covers too few histogram bins (`narrow_histogram_range`, §4); planes over the memory limit that are read in blocks; images without a mask of a type other images have, masks matching no image, masks of excluded images, files outside the mask-type folders (§15) | Processing continues; the item is reported |

Every message states what happened in plain language, lists the affected paths, and
suggests an action. Technical details go to the diagnostic log.

In notebook step 1, message details start collapsed with orange warning and red error
counts visible outside the dropdown. Collapsing details does not change severity or
whether processing is blocked. The consistency issues of §11 are shown in their own
**Dataset consistency** panel, next to the balance table, and not repeated under the
other messages.

## 9. Run manifest (`run_manifest.json`, manifest version 2)

UTF-8 JSON next to `plane_histograms.csv`, written after the table and only when the run
succeeded.

Both files are prepared in a temporary folder before either earlier output is replaced.
Earlier files are backed up before publication. A caught replacement failure restores the
previous state, including the absence of files on a first run. If restoration fails, the
backup folder is retained and its location is reported for manual recovery. Failures to
remove temporary files after a successful publication are logged.

The two replacements are not jointly atomic: forced process termination between them can
leave a mismatched pair, detected by `csv_sha256` on the next run and rebuilt. Only one run
may write to a given output folder at a time. This write protocol does not change either
file's schema or statistical interpretation.

| Key | Meaning |
|---|---|
| `manifest_version`, `schema_version` | Manifest format (`"2"` since v0.4.0) and CSV format (`"4"`), written as JSON **strings**, not numbers. A manifest whose `schema_version` is not the current one makes the next run recompute every row. |
| `written_at` | UTC time of writing, ISO 8601. |
| `experiment_root` | Absolute path of the experiment folder, for information. |
| `dtype` | Pixel type of the experiment. |
| `settings` | `bin_count`, `memory_limit` (bytes), `missing_as_singleton`, `exclude` (sorted relative paths), `histogram` (`HistogramSettings.to_dict()` of the normalization brick: `library_version`, `histogram_range`, `robust_quantiles`, `min_central_bins`, §4; the default `minmax` range when absent, in manifests written before follow-up P6). |
| `axis_mapping` | `axes` (file axis labels) and `roles` (`X`, `Y`, `Z`, `C`, `T` or null per axis). |
| `labels` | One entry per leaf folder: `folder` (relative path, `.` for the root) and its three labels. |
| `files` | One entry per analysed file, sorted by path: `relative_path`, `size` (bytes), `mtime_ns`, the three labels, `axes`, `singleton_roles`, `plane_count` (its rows in the table: XYC planes), `channel_names` (list, null for unnamed channels; empty if none in metadata), `significant_bits` (null if not in metadata), `read_in_blocks` (true when the image's planes are over the memory limit and are read in blocks; information only, false when absent), `position_id` (the image's position identifier, §1; null when absent, in manifests written before it), `channel_names_trimmed` (true when leading or trailing whitespace was removed from the image's channel names as they were read, §2; `channel_names` holds the trimmed names; false when absent), `roles` (the role of each of the file's `axes`, `X`, `Y`, `Z`, `C`, `T` or null, from the file's own mapping: an OME-Zarr image in another order than `axis_mapping` has its own roles, so later bricks read these rather than rebuild them from `axis_mapping`; null when absent, in manifests written before them; optional key, version 1). For an OME-Zarr image, `size` is the total size of its files and `mtime_ns` the time of its newest file. **Version 2 (v0.4.0):** `roles` is required, with `axes_from` (required): `header` when the metadata names every axis and the roles agree, `confirmed` for a plain stack whose roles the user confirmed (§2); and `pixel_sizes`, one entry per role `X`, `Y`, `Z`, `T` with `value`, `unit` (OME-NGFF name) and `source` (`header` for now; `stated` and `pixels` with stated calibration), `value` and `source` null for an uncalibrated axis (never guessed). The reader refuses a version 2 entry without `roles` or `axes_from`, and the writer refuses to write one. |
| `csv_sha256` | SHA-256 of the table written with this manifest. |
| `experiment_design_sha256` | SHA-256 of the `experiment_design.csv` written with this run (§12), so later bricks can check that the design they read is the one used; null when the run had no design, or absent in manifests written before it. Optional key: the manifest stays version 1. |
| `masks` | The masks paired with the images (§15), when the run was given a masks folder or its OME-Zarr images have label images (always used): `folder` (absolute path of the masks folder, null when only OME-Zarr label images were used), `types` (sorted mask-type names) and `files` (one entry per image and mask type, sorted by image then type: `image`, `type`, `path`, `source` `folder` or `ome-zarr`, `axes` and `shape`: the mask's axes and lengths without its length-1 axes other than Y and X, `YX` for a 2D mask (`[1, 512]` for a mask one pixel high), with `Z` and/or `T` for a mask with depth slices or time points, e.g. `TYX` and `[50, 512, 512]`; §15). Absent when the run had no masks. Optional key: the table does not depend on the masks. `axes` and `shape` are absent in manifests written before them, whose masks are all 2D; a later brick must read `axes` before using a mask and never apply a 2D mask to several planes unless `shared` is true. **Version 2:** `axes` are the mask's axes as stored (its header's letters: `IYX` for a plain stack), and each mask entry also has `size` and `mtime_ns` (its fingerprint, as `source_fingerprint` gives it: the mask file, or the label image's folder), `roles` (one per axis of `axes`, what each is read as: `TYX` for that plain stack stated `--mask-axes Cells=TYX`), `axes_from` (`header`, or `stated` when the axes were stated for its mask type) and `shared` (true only for a 2-D mask stated `YX` for its type: one plane shared by every plane of its image). A mask is read as stored, never reordered. |
| `image_type` | `fluorescence` or `rgb`, the image type the user confirmed (§13); null when the run was given none, or absent in manifests written before it. Optional key: the manifest stays version 1. Changing it recomputes nothing. |
| `channels_from_rgb_samples` | `true` when the C role is the RGB samples axis of images stored as RGB colour (§13), `false` otherwise; absent in manifests written before it. Optional key. |
| `intensity_checks` | The limits of the intensity checks run on the table (§14): `IntensityCheckSettings.to_dict()` of the normalization brick (`library_version`, `outlier_limit`, `saturation_limit`, `min_images`); absent in manifests written before it. Optional key. Changing the limits recomputes nothing. |
| `narrow_histogram_ranges` | The channel planes whose range check found the central range too narrow (§4), sorted by plane and channel: `relative_path`, `plane_id`, `channel` and the fields of the normalization brick's `RangeCheck` (`histogram_range`, `central_percentiles`, `central_bins`, `robust_quantiles`, `robust_limits`, `outside_robust_count`, `robust_central_bins`, `min_central_bins`). Empty when there are none; absent in manifests written before follow-up P6. Optional key: the manifest stays version 1. |
| `conversion_record_sha256` | SHA-256 of the conversion record in the experiment root when the experiment is a tree of named-axis copies (design v0.4 §5); null otherwise. |
| Version 1 | A manifest written before v0.4.0 (`"1"`) is still read, for reporting, but no row of it is reused: its axes were never checked. The next run recomputes every row ("the earlier run was written before v0.4.0, without the axis check"). Bricks refuse it. |
| Row reuse | A file's rows are reused only when its row key is the earlier run's (`fingerprint.RowKey`, one rule for "the same file, read the same way", audit D5): its fingerprint (`fingerprint.Fingerprint`, size and modification time), labels, axes, roles, `singleton_roles`, `axes_from` and the path it is read from (so moving or converting the experiment recomputes it), and the settings that change rows (bins, histogram range, robust quantiles, range-check limit). |
| Removed keys | `settings.convert_tiffs`, `settings.conversion` and `files[].ome_zarr_path` were written by v0.3 runs that converted TIFF images to OME-Zarr in the output folder (removed in v0.4.0, §10); a manifest that holds them is still read, and they are ignored. The copies such a run left in `<output>/ome_zarr` are not touched. |
| Flags | `missing_as_singleton`, `read_in_blocks`, `channel_names_trimmed` and `channels_from_rgb_samples` are JSON booleans (`true`/`false`); any other value (`"false"`, `0`) makes the manifest malformed, never read as true or false, and `write_manifest` refuses to write one (`ValueError`), so a written manifest always reads back. |
| `versions` | Versions of `morphobricks-preprocessing`, `morphobricks-normalization` (which computes the histograms and writes the CSV; absent in manifests written before it), `numpy`, `tifffile` and Python. Information only: a different version never causes a rebuild. |

Channel names and significant bits come from OME metadata. Significant bits are for
information only; channel names must agree between images at each channel number (§2), and
later bricks that name channels should check their names against `channel_names` when it
is not empty.

### 9.1 Reading a clean run (`RunInputs`, v0.4.0)

Bricks read a run's files only through `RunInputs.open(output_folder, experiment_root=None,
masks_root=None)` (design v0.4 §6). It refuses (`RunInputsError`) a version 1 manifest, a
`plane_histograms.csv` or `experiment_design.csv` whose SHA-256 is not the manifest's, and an
image changed or gone since the run; every read checks the file's fingerprint again. It
gives `fovs()` (`Fov`: the FOV key, `plane_id`s, axes, roles, `axes_from`, channel names,
dtype, `significant_bits`, and the Z, T and C counts, from the manifest and the table, never
parsed), `pixel_sizes(fov)` (value, unit and source per role, or unknown),
`read_plane(fov, z, t)` (a `(C, Y, X)` array of the stored values), `read_image(fov)` (a lazy
`(T, Z, C, Y, X)` `LazyImage`), `mask_types()`, `read_mask(fov, type, z=None, t=None)` (the
recorded pairing only, read by preprocessing's reader with `check_labels`; a mask changed
since the run is refused; a stated shared 2-D mask serves every plane), `histograms()` (the
table as read when opened), `design()` and `image_type()`. It is picklable.
`morphobricks_preprocessing.testing.make_clean_run(folder, ...)` writes a tiny experiment of
named-axis OME-TIFFs (optionally with masks) and its clean run, for the bricks' tests.

## 10. Converting TIFF images to OME-Zarr (removed in v0.4.0)

Up to v0.3, a run could convert every TIFF image to OME-Zarr in `<output>/ome_zarr` and
compute its rows from the copies (`convert_tiffs`, `zarr_export`, CLI `--convert-tiff`, the
notebook's "(Optional) Image conversion" card). The bricks never read those copies, and v0.4
reads every image through the run's own readers, so the option was removed (spec revision 4,
§8 PP-2). Its rows were the TIFF's own, so a later run keeps them. Saving images with axis
errors as named-axis copies is a separate step, planned for v0.4.0 as OME-TIFF.

## 11. Dataset consistency (folder check)

The folder check reads only folder names and image headers. It gathers every consistency
issue of an experiment in one place (normalization plan N37). The new checks are warnings:
they never block processing, and each names the affected images or folders and what to check.
The exception is the number of channels, an error since the channels are columns of one
row (follow-up P3b, normalization plan N43).

| Code | Severity | Rule |
|---|---|---|
| `unbalanced_images` | Warning | Images are counted per condition and biological repeat, technical repeats added up. A count below half or above twice the median of all counts (exactly half or twice is balanced) is reported, with the biological-repeat folders. |
| `repeat_missing_conditions` | Warning | With more than one condition, a biological repeat not found in every condition is reported, with its folders. Repeats are matched across conditions by their repeat group (§12; by default the folder name). It is still used. |
| `different_pixel_sizes` | Warning | The physical pixel size (X by Y) differs between images, or the z-step differs between images with more than one depth slice. Sizes are compared in micrometres by the shared rule of decision D-a (§16): two sizes are the same when they differ by at most 0.1 % of the larger, on every axis. The warning is given exactly when, on some axis, the smallest and the largest size differ, that is when some two images differ. The images are grouped by single linkage (`pixel_size_groups`) and those outside the largest group are listed. When every image is linked to the others by near-equal sizes (1.0, 1.0009, 1.0018 µm), the sizes drift, no image stands out, and the message says that none is listed for that size. Until D86 sizes were rounded to four significant digits, so 1.0004 and 1.0006 µm differed (1 and 1.001); they are now the same. |
| `missing_pixel_size` | Warning | The pixel size (or the z-step of images with more than one depth slice) is known for some images and not for others. Nothing is reported when no image has it. |
| `unsupported_pixel_size_unit` | Warning | An image's metadata give a pixel size or z-step (OME `PhysicalSize{X,Y,Z}`, ImageJ resolution or `spacing`, OME-Zarr level-0 scale) in a length unit that is not one of decision D-a2 (§16), for example OME `in` or ImageJ `inch`. The size is not converted and the axis is treated as uncalibrated (as before); the warning names each image, its axes and the unit as written, so the missing calibration is never silent. A unit without a size, and a plain TIFF's resolution tags (decision D-c), are not reported. The run is not refused. |
| `different_channel_counts` | Error | The number of channels differs between images: the `C` dimension, or the RGB samples `S` when there is no `C`; once the axis roles are confirmed, the length of the C axis (1 without one), which replaces the header's count. The images not having the most common number are listed, with each number's image count. Every image of an experiment must have the same channels: split the others into another experiment or exclude them. |
| `different_plane_counts` | Warning | The number of depth slices (`Z`), time points (`T`) or planes along any other dimension except X, Y, C and S differs between images. A missing dimension counts as 1. Channels are compared by `different_channel_counts`. |
| `different_sizes` | Warning | The image width or height differs between images (TIFF and OME-Zarr). The other dimensions are compared by `different_plane_counts`. |

| Rule | Detail |
|---|---|
| Counts | Only images to analyse count: excluded images are left out, and a folder whose images are all excluded is not a biological repeat. A biological repeat with images directly in its folder has no technical-repeat folders (count 0). |
| Balance table | Conditions as columns, repeat groups as rows (§12; by default the biological-repeat folder name, `single` when there are no biological-repeat folders); each cell holds the number of images and of technical-repeat folders. |
| Pixel sizes | TIFF: OME `PhysicalSizeX`, `PhysicalSizeY`, `PhysicalSizeZ` with their units (micrometres when not given), or ImageJ resolution tags and `spacing` with the ImageJ unit (unknown without a unit). OME-Zarr: the level-0 scale (`coordinateTransformations` of the first dataset, times the image's own scale when given) with the axis units; a scale without a unit is unknown. Sizes are never guessed. |
| Panel | The notebook shows the balance table with the issues `mixed_dtypes`, `inconsistent_dimensions`, `missing_axes`, `inconsistent_mapping`, `different_sizes`, `different_channel_counts`, `different_plane_counts`, `channel_names_differ`, `different_pixel_sizes`, `missing_pixel_size`, `unsupported_pixel_size_unit`, `unbalanced_images` and `repeat_missing_conditions`. The command line `check` prints the balance table and every issue; `run` reports the same warnings, and stops on `different_channel_counts` and `channel_names_differ`. |
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
| Reading | `read_experiment_design` reads labels as text; `control` and `confirmed` accept `true`/`false` in any case, also `yes`/`no` and `1`/`0`, and empty as false; a byte-order mark is ignored. The user's edits are checked (owner decision of 31 October 2026): `repeat_group` follows the identity rules of §16 (D-e: not empty, `NA`, `nan`, `none`, `null`, `n/a` or `<NA>` in any case, no leading or trailing whitespace, and spelled one way: two groups differing only in case are refused) and `confirmed_on` is empty or a date `YYYY-MM-DD`; a bad edit raises `DesignFormatError` naming the line and column. `design_issues` gives the error `invalid_experiment_design` for such values in a design built in code, so a run stops before writing, and `write_experiment_design` refuses to write them. A missing column, an invalid value, a folder listed twice, rows of one condition disagreeing on `control` or another `schema_version` raise `DesignFormatError`. `ExperimentDesign.repeat_groups()` gives the `{(condition, biological_repeat): repeat_group}` mapping the normalization brick takes. This lenient reader is for the user's own edits; other bricks read the file with the strict `read_confirmed_design` (§16). `load_design(output_folder, repeats)` reads the file with it and brings it up to date with the folders (Reruns), returning `(design, changes)`, the default design when there is no file; a file that cannot be read raises `DesignCheckError` (`unreadable_experiment_design`, §16) and is left as it is (owner decision of 31 October 2026, plan §12 question 43). |
| Notebook | Step 1 asks once the labels are confirmed and writes the file after every answer (unconfirmed groups with `confirmed=false`). A design confirmed for the same folders is loaded with the questions folded away behind **Change the experiment design**. A saved file that cannot be read (`load_design` raises `DesignCheckError`, `unreadable_experiment_design`, e.g. for a `confirmed_on` a spreadsheet program rewrote as `26/10/2026`) is refused and left unchanged (owner decision of 31 October 2026, plan §12 question 42): step 1 shows the refusal, which names the line and column, starts the questions unanswered and writes nothing until the user corrects the file and confirms the labels again, or answers a question, which replaces the file. Nothing falls back to a default design silently, and step 3 does not run until every group is confirmed again. Step 3 runs only once every group is confirmed. A brick embedding step 1 with `folder_step(show_design=False)` (plan §12 question 31) gets no design: nothing is asked or written, and step 3 runs without one, as `run_experiment(design=None)`. |
| Command line | `run` reads the file from the output folder, or `--design FILE`. Without a fully confirmed design it writes the design brought up to date with the folders (a template with `confirmed=false` for the groups still to confirm, keeping the answers already given) to the output folder, explains the columns, and stops with status 1. Confirmed rows without a date get the date of the run. |
| Run | `run_experiment(..., design=...)` brings the design up to date with the folders and stops with `design_not_confirmed` or `repeat_group_conflict` if it is not fully confirmed. Otherwise the file is written with the table and published before the manifest, which records its SHA-256 (§9). Without a design the run is unchanged: no file is written, an existing one is left as it is, and `experiment_design_sha256` is null. `plane_histograms.csv` does not depend on the design. |

## 13. Image type

Later bricks normalize `fluorescence` images channel by channel and `rgb` images with
StarDist's percentile method over the three colour channels together ([normalization
contract][normalization contract] §3.3 and §5). The type is the user's choice (normalization
plan N34); this brick suggests it, asks for it after the axis roles, and records it.

| Rule | Detail |
|---|---|
| Types | `fluorescence` (any number of channels, every pixel type) and `rgb` (exactly three channels of an integer pixel type, the rule of the normalization brick's `check_rgb`). |
| RGB samples | A TIFF image is stored as RGB colour when its first series has the photometric interpretation RGB with three samples per pixel (`tifffile` axis `S`; `TiffMetadata.rgb_samples`). RGB with an alpha sample, several grey samples and OME-Zarr images are not. |
| Suggestion | `rgb` when every analysed image is stored as RGB colour, the confirmed C role is on its samples axis, and `rgb` is possible; `fluorescence` otherwise. `review_image_type` gives the suggestion, why, and why `rgb` is not possible (from the headers and the confirmed axis roles; no pixels are read). |
| Confirmation | Never applied without the user's confirmation. The notebook asks in step 2 once the axis roles are confirmed, with the suggestion preselected and a **Yes, this is the right image type** button; `rgb` cannot be chosen, with the reason shown, when it is not possible. Changing the axis roles or the folder asks again. Step 3 runs only once the type is confirmed. The command line takes `--image-type`, or uses the suggestion and prints it. |
| Run | `run_experiment(..., image_type=...)` records the type and `channels_from_rgb_samples` in the manifest (§9). `rgb` for images that cannot be RGB is the error `rgb_not_possible`; any other value raises `ValueError`. Without a type the manifest records null. |
| Outputs | `plane_histograms.csv` and `experiment_design.csv` do not depend on the type: a plane of an RGB image is one row with channels C0, C1 and C2, as for any image. Changing the type on a later run reads no image again. |

## 14. Intensity checks

After the table is written, the images' intensities are checked from its rows alone, with
the normalization brick's `image_summaries` and `intensity_checks` ([normalization
contract][normalization contract] §7, plan N38): no image is read and nothing is written
apart from the limits in the manifest.

| Rule | Detail |
|---|---|
| Summaries | One per image, all Z and T planes pooled per channel: median, 1st and 99.8th percentiles, saturated fraction (normalization contract §7.1). |
| Warnings | `image_intensity_outlier` (an image's median or 99.8th percentile in a channel more than `outlier_limit` robust deviations from the other images of its condition and biological repeat), `repeat_intensity_shift` (a biological repeat's median of image medians far from the other repeats' images of its condition), `saturated_image` (more than `saturation_limit` of the pixels at the type's maximum), `no_intensity_spread` (1st and 99.8th percentiles equal). One warning per image or repeat, channel and compared value, naming the channel `C<i>`, the image or `<condition>/<biological repeat>`, the value and the group value or limit. Comparisons stay within a condition, so treatment effects are never reported. |
| Limits | `IntensityCheckSettings`: `outlier_limit` 3.0, `saturation_limit` 0.01, `min_images` 5 (provisional in the normalization brick). `run_experiment(..., intensity_settings=...)` uses them and records them in the manifest (`intensity_checks`, §9); they change no row. |
| Run | `RunResult.intensity` holds the summaries and warnings (`IntensityReport`) of a written table, None otherwise. The warnings never block and are kept apart from `RunResult.issues`. |
| Notebook | Step 3 shows the checks of the table just written after every successful run, step 4 those of any chosen table: a status line with the limits, counts per kind, a table of the warnings (check, channel, image or repeat, what was found) and what to do about each kind, a collapsed **Check settings** panel (changing a limit updates the warnings at once; in step 3 the next run records it), and per channel (chosen with buttons) box plots of the image medians and high percentiles per condition and biological repeat, with images that have a warning in that channel (`Issue.channel`, N52) as red diamonds and shifted repeats as amber boxes, and the cumulative intensity distribution of each biological repeat, one panel per condition (shifted repeats dashed; at most 12 conditions). The plots are drawn in the notebook only; no file is written. |
| Command line | `run` prints the warnings after the run, grouped by kind with what to do once per kind, and draws no plots; `--outlier-limit`, `--saturation-limit` (a fraction) and `--min-images` set the limits. |

## 15. Masks (optional)

Masks are segmentations made elsewhere: label images of cells, nuclei, nucleoli, vesicles or
any other object, one per image and mask type. This brick finds them, checks them and
describes how they relate, for the bricks that measure objects. It never changes a mask, and
`plane_histograms.csv` does not depend on the masks.

```
masks/                         masks folder, outside the experiment folder
    Cells/                     one folder per mask type, named by the user
        control/bio_rep1/tech_rep1/img_01.tif
    Nuclei/
        control/bio_rep1/tech_rep1/img_01.tif
```

| Rule | Detail |
|---|---|
| Layout | Either one subfolder per mask type, each mirroring the experiment's folders (`<type>/<condition>/.../<image>.tif`), or the experiment's folders with the type after the image name (`<condition>/.../<image>_<type>.tif`, as segmentation tools often write it, e.g. MorphoRelate's JUMP-Target-2 conversion); the two may be mixed. Any number of mask types. The label images of an OME-Zarr image (its OME-NGFF `labels` group, with the `zarr` extra) are masks too, of the type named by the label image; since v0.4.0 they are always used when present, with or without a masks folder. Each label image has its own fingerprint in the manifest, and the `labels` group is left out of its image's fingerprint (`source_fingerprint`), so adding or changing a label image does not recompute the image's rows. |
| Pairing | A mask belongs to the image in the same leaf folder with the same `position_id` (§1), ignoring upper/lower case; its file is a TIFF (`.tif`, `.tiff`, optionally `.ome`, any case). Excluded images have no masks: their masks are reported apart (`mask_of_excluded_image`, warning) and not used. |
| Names | A mask type is named by its folder, by the suffix after the image name, or by its label image. A file that could belong to two images (an image name that starts another's, `a_b_cells.tif` beside images `a` and `a_b`) or fit both layouts is the error `ambiguous_mask`. Two names differing only in case are the error `mask_names_differ_in_case`; one image with two masks of one type (two extensions, or a file and a label image) is the error `duplicate_mask`. |
| Header checks | From the headers only, no pixels read (`check`, `run`, `preflight`): once its length-1 axes other than Y and X are removed (Y and X are kept whatever their length, so a mask one pixel high or wide, or a single pixel, is a 2D `YX` mask; a TIFF whose series has no Y or X but is one 2D page, such as a 1D array `tifffile` saved as `{"shape": [N]}` and reads as `X`, is that page, `YX` of 1 row; plan §12 question 44), a mask has Y and X and at most a depth axis Z and a time axis T, each named so in its own metadata (TIFF axes as `tifffile` reads them, e.g. an ImageJ hyperstack or OME-TIFF; OME-NGFF axes for a label image); any other or unnamed axis, such as a plain stack that could be depth or time, is `mask_not_2d` and is never guessed, unless the axes are stated for its mask type (`--mask-axes Cells=TYX`, `mask_axes={"Cells": "TYX"}`: one role, Y, X, Z or T, per axis of the header with length-1 axes other than Y and X left out; recorded as `axes_from: "stated"`; a statement that does not fit is `invalid_mask_axes`, one that contradicts an axis the metadata names is `mask_axes_contradict_header`, one for a type not found is `invalid_mask_axes`). Masks are never converted or reordered: a mask whose X is stored before its Y, or whose Y and X are its image's swapped, is `mask_layout`, with a `numpy.swapaxes` hint. A mask has the Y and X sizes of its image (`mask_size_mismatch`; `check` and `preflight` compare with the Y and X the image's header names, and not for a plain stack, whose Y and X are not named; `run` compares with the image's Y and X under the confirmed roles, `mask_size_issues`, and leaves out the masks of an image with an axis error (§2), which is never read — before v0.4.0 `run` compared with the header's letters, so a swapped image gave a misleading size error) and integer or boolean pixels (`mask_pixel_type`; a mask holding a negative label or a label above 2,147,483,647, the int32 limit every brick shares (§16, decision D-b), raises an error when its pixels are read); the masks of one image have the same depth slices and time points (`mask_planes_differ`); a mask that cannot be opened, or holds several series, is `unreadable_mask`, as is an OME-Zarr image's `labels` group that cannot be read or does not list its label images (path `<image>/labels`), so its mask types never disappear unreported. All five are errors. |
| Planes | A mask belongs to the image's planes (issue #36): its Z and T lengths (1 for an axis it lacks) must equal the image's under the confirmed axis roles, checked by `run` (`mask_planes_mismatch`, error; `mask_plane_issues`; not for an image with an axis error, §2). So a 2D mask belongs only to an image with one depth slice and one time point, and is refused for a Z-stack or a time-lapse, unless its type is stated `YX` (`--mask-axes Cells=YX`): then it is one plane shared by every plane of its image (`shared: true` in the manifest), read for any `z` and `t`; a mask's plane at index `i` along Z or T belongs to the image's rows with `z = i` or `t = i`. `read_mask(..., z=, t=)` reads one plane and requires `z` and `t` for a mask with those axes. `check` and the `masks` command have no confirmed roles and do not compare with the image. |
| Folder | The masks folder must exist (`masks_folder_not_found`) and be outside the experiment folder (`masks_inside_experiment`), where its folders would be read as labels; the output folder may not be inside it (`output_inside_masks`), where the run's files would be read as masks. All errors. |
| Warnings | `mask_missing` (images without a mask of a type that other images have: their objects of that type cannot be measured), `mask_without_image` (files matching no image of the experiment), `mask_of_excluded_image` (masks of excluded images, not used), `mask_outside_type_folder` (files directly in the masks folder that are not TIFF masks). |
| Run | `run_experiment(..., masks_folder=..., mask_axes=...)` stops on a mask error and records the masks in the manifest's `masks` key (§9). Without masks, the run and its outputs are unchanged. |

### 15.1 The mask step (`mask_relationships.json`, report version 1)

The optional mask step reads every image's masks, one image at a time, and describes each
mask type and every ordered pair of types over the whole experiment. Pieces are counted with
4-connectivity; label 0 is background and every object has a positive label: a mask holding
a negative value (possible in a signed integer type, which the header check accepts) raises
an error when its pixels are read, and no report is written, so the background or a negative
value is never counted as an object. Every conclusion is a suggestion with its evidence:
the user names the masks and confirms or corrects the relationships.

| Kind of mask | Rule |
|---|---|
| `empty` | No foreground in any image. |
| `one_value` | Every image's mask has at most one foreground value: a binary mask, or a single object (only the user can tell which). |
| `grouped` | The median number of pieces per label, over every label of every image, is more than 2: each label is spread over many pieces, as in a mask of mitochondria whose pixels carry their cell's label. |
| `instances` | Otherwise: each label is one object. |

A mask with depth slices or time points is scanned one plane at a time, each plane like an
image of its own: objects are never matched across planes, `objects` counts a label once per
plane it is in, and `images` still counts images.

For a child type and a parent type, the counts are summed over the images (and planes) with
both masks.
Each child object's parent is the parent object sharing most of its pixels.

| Relation of child to parent | Rule (default thresholds; the values used are written in the report) |
|---|---|
| `same_objects` | At least 90% of the children and 90% of the parents are matched one to one at an intersection over union of at least 0.5: two segmentations of the same objects. |
| `grouped_by` | The child is `grouped`, at least 90% of its objects are inside as below, and at least 99% of its pixels carry the label of the parent object under them. |
| `inside` | At least 90% of the child objects have at least half of their pixels in one parent object, and that parent is larger than the child for at least 90% of them. |
| `none` | Otherwise. |

| Evidence (per pair) | Meaning |
|---|---|
| `children`, `parents` | Objects of each type. |
| `inside`, `inside_parent_larger` | Children with at least half of their pixels in one parent; of those, children whose parent is larger. |
| `split_between_parents`, `outside_parents` | Children touching parents but with less than half of their pixels in any one; children touching no parent. |
| `parents_with_0_children`, `parents_with_1_child`, `parents_with_2_children`, `parents_with_3_or_more_children` | How many children each parent holds (two nuclei in one cell counts as 2). |
| `child_pixels`, `child_pixels_with_parent_label` | Child pixels; those whose label equals the parent label under them. |
| `children_matched_one_to_one`, `parents_matched_one_to_one` | Objects with a partner at an intersection over union of at least 0.5. |

**Thresholds.** The numbers above are defaults (`DEFAULT_THRESHOLDS`), and each may be
changed: `inside_pixel_share` (0.5), `inside_object_share` (0.9), `parent_larger_share`
(0.9), `same_objects_iou` (0.5), `same_objects_share` (0.9), `grouped_label_share` (0.99)
and `grouped_median_pieces` (2). Shares and the intersection over union must be above 0
and at most 1, the median number of pieces at least 1; an unknown name or another value
raises `ValueError` before any mask is read (`mask_thresholds`). The command line takes
one option per threshold (`--inside-object-share 0.8`, ...).

**Hierarchy.** A relation `inside` or `grouped_by` is **direct** unless another type lies
between: nucleoli inside nuclei inside cells have the direct parent nuclei, and the
relation nucleoli inside cells is kept in the report but not in the hierarchy. A type may
have several direct parents (cells inside an organoid and inside a region of the slide), and
two types that are the same objects are both kept.

The report is UTF-8 JSON (two-space indent, LF), written atomically to the output folder:

| Key | Meaning |
|---|---|
| `report_version` | `1`. |
| `thresholds` | The thresholds used, all seven. |
| `images` | Images scanned (every image with at least one mask). |
| `masks` | Per mask type: `kind`, `images`, `objects` (labels over all images) and `median_pieces_per_label`. |
| `hierarchy` | Per mask type, its direct parents (sorted; empty for none). |
| `same_objects` | Pairs of types that are the same objects, each pair sorted. |
| `relationships` | Every ordered pair with a relation other than `none`: `child`, `parent`, `relation`, `direct` and `evidence`. |
| `pairs` | Every ordered pair of types found together in at least one image, `none` included, in the same form. |
| `versions` | As in the manifest (§9); information only. |

The step is the command `masks` and the functions `find_masks` and `scan_masks`; it reads
each image's masks once and holds only one image's masks in memory. A mask that cannot be read
as the planes its header gave, or holds a label outside 0 to 2,147,483,647 (§16), when its pixels
are read raises an error.

## 16. Shared rules for other bricks (MorphoBricks D86)

Rules every MorphoBricks brick applies in one way, so they live here as public functions
(cross-brick decision D86, owner decisions of 30 October 2026) and other bricks call them
instead of keeping copies.

| Rule | Function | Detail |
|---|---|---|
| Length units (D-a2) | `space_unit(text)`, `to_micrometers(value, unit)`, `SPACE_UNITS`, `UnsupportedUnitError` (`physical_sizes`) | Microscopy units only: `pm`; `Å`/`angstrom`; `nm`; `µm`/`um`/`micron(s)`/`micrometer(s)`/`micrometre(s)`; `mm`; `cm`; `m`; with each name in US and British spelling and in the plural (`nanometre`, `millimeters`, ...). Symbols of two letters or fewer are matched exactly (`Mm` is not `mm`), longer names in any case; ImageJ's `\u00B5` is decoded. `space_unit` returns the OME-NGFF name (`"micrometer"`), None when no unit is given, and refuses any other unit by name (`UnsupportedUnitError`); `to_micrometers` converts a positive finite length, None when the length or its unit is not known. This brick's readers (§2) use the same table: since D86, ImageJ and OME-NGFF sizes in British spellings (`micrometre`) are calibrated, where they were unknown before. |
| Plain-TIFF resolution (D-c) | `read_physical_sizes` | A TIFF's resolution tags give a pixel size only with OME or ImageJ metadata and a unit; a plain TIFF (scanner or image-editor dpi) is uncalibrated, never converted from inches or centimetres. |
| Labels (D-b) | `check_labels(pixels, name)`, `LARGEST_LABEL` (`masks`) | A mask holds whole-number labels from 0 (background) to `LARGEST_LABEL` = 2,147,483,647: MorphoBricks labels are int32 in every brick. A negative label, a label above the limit, or a pixel type that is not integer or boolean is refused with a `ValueError` naming the mask and the value. `read_mask`, `piece_counts`, `pair_counts` and `scan_masks` call it. Since D86 the limit is int32; before, this brick refused only labels above 2^63 − 1 (int64), so a `uint32` or wider mask with a label of 3,000,000,000 is now refused. |
| Header axes (D-d) | `header_axes(meta)`, `named_axes(meta)`, `series_axes_from_metadata(series)`, `PLACEHOLDER_AXES`, `OPEN_AXIS` (`metadata`) | The axes an image's own metadata names, as `tifffile` reads them with length-1 axes left out (`tifffile` keeps Y and X even of length 1, so an image one pixel high is `YX`): OME, ImageJ and `tifffile` "shaped" metadata (`{"axes": "CYX"}`, decision D-d), a plain 2D `YX` (or `YXS` RGB) TIFF, and an OME-Zarr image's OME-NGFF axes. None when an axis is a placeholder (`I`: a plain stack of pages; `Q`: shaped without axes, or an OME-NGFF axis of another type), or the image does not open: the file does not say which dimension is depth, time or channel. `named_axes` gives the rule position by position, with `?` (`OPEN_AXIS`) for a dimension the metadata does not name: a placeholder, and, in a plain stack of pages without axis metadata (`IYX`, `QYX`, `QQYX`; `TiffMetadata.axes_from_metadata` False, from `series_axes_from_metadata`; True, the strict reading, for a header built by hand), the `Y` and `X` too, which only say where each page's rows and columns are stored (a (Y, X, C) array written without metadata reads as `QYX`). `header_axes` gives axes exactly when nothing is open. The axis check (§2) compares confirmed roles with `named_axes`. |
| Declared axes (removed) | `check_declared_axes`, `declared_axes_transposed` | **Removed in v0.4.0** (spec revision 4): they accepted a declaration with Y and X swapped and read it transposed, which v0.4 forbids. Bricks take no axis declarations; preprocessing's axis check (§2) compares the confirmed roles with the header. Former text, for reference: | **Deprecated in v0.4.0, removed in v0.5.0** (they warn with a `DeprecationWarning`): bricks take no axis declarations and read the files of a preprocessing run whose axes were checked (§2); nothing is read transposed. Until then: axes a brick or user declares for an image or mask (`image_axes="CYX"`) may name what the header leaves open, never contradict it: one letter per header axis (any case), roles X, Y, Z, C, T each at most once, with `Y` and `X` declared when the header has them (a plain stack too); an axis the metadata names (`named_axes`) keeps its letter, except that the named `Y` and `X` may be declared swapped (header `CYX` declared `CXY`, `ZYX` declared `ZXY`): the image is read transposed, and `declared_axes_transposed` returns True so the caller can record it (owner decision of 31 October 2026; it raises `ValueError` for a declaration that does not fit); any other disagreement is refused; a dimension it leaves open may be declared any role, so a plain `QYX` stack may be declared `YXC` as well as `CYX` (since v0.3.4; before, its positional `Y` and `X` were binding and a placeholder could not be declared X or Y), while an ImageJ `ZYX` declared `CYX`, an OME `CYX` declared `YXC` or a shaped `CYX` declared `YXC` are refused; the RGB samples `S` may be declared C when the header has no C; any other header axis (tile `R`, ...) cannot be declared. Otherwise the error `declared_axes_mismatch` naming the image and both axes (`unreadable_tiff` or `unreadable_ome_zarr` when the header does not open). |
| Mask files | `read_mask_file(path, z=, t=)` (`masks`) | Reads one plane of a TIFF mask or of an OME-Zarr label image (`<image>.zarr/labels/<name>`) given by its path, with `read_mask`'s rules (§15): its axes are its own metadata's, Y and X plus at most a Z and a T axis named so, length-1 axes other than Y and X left out (a mask one pixel high or wide is read as a 2D array, plan §12 question 44); any other or unnamed dimension (a plain stack, `IYX`; a `C` axis) is refused, the message giving the header's axes and shape and the dimension that is not named, never taken for depth or time; `z` and `t` are required for a mask with that axis; integer or boolean pixels; labels checked with `check_labels`. A `ValueError` names the file and the problem. |
| Identity (D-e) | `FOV_KEY`, `check_identity_text(value, field)`, `check_plane_index(value, axis)`, `check_identity_case(keys)`, `MISSING_TEXT`, `IdentityError` (`discovery`) | `FOV_KEY` = (`condition`, `biological_repeat`, `technical_repeat`, `position_id`), the position key of §1. `check_identity_text` accepts a label or `position_id` that is text without leading or trailing whitespace and not `""`, `NA`, `nan`, `none`, `null`, `n/a` or `<NA>` in any case (`MISSING_TEXT`); `check_plane_index` accepts None or an integer of at least 0, a Python `int` or a NumPy integer (returned as a plain `int`), and refuses everything else (bool, float, `"1"`) without converting it. Both raise `IdentityError` (a `ValueError`) naming the field and the value. Discovery applies the text rule to every folder and image name (`invalid_name`) and refuses names of one level that differ only in case anywhere in the experiment (`case_conflict`, §1). `position_id` keeps its own scope: unique within its technical repeat, ignoring case (`duplicate_position_id`). `check_identity_case` applies the same case rule to identities a brick reads from a table, one key per row in `FOV_KEY` order (or the three labels): a label of one level spelled two ways anywhere in the run, or two `position_id` values of one technical repeat equal ignoring case, raise `IdentityError` naming both spellings; the same key may repeat (several planes of one position). |
| Sources of a run | `check_sources(manifest, experiment_root, relative_paths, image_axes=, identities=)`, `source_fingerprint`, `file_sha256` (`manifest`) | The checks a brick makes before using a run's table for some images (formerly CellPatcher `check_images` and MorphoFeature `histogram_fit`). Each image must be in the manifest (`images_not_in_manifest`), still exist (`images_missing`), and be unchanged by `source_fingerprint`, its size and modification time, for an OME-Zarr folder its files' total size and newest time, so a rewritten chunk counts (`images_changed`). Its recorded `position_id` must be the one its name gives; with `identities` (`FOV_KEY` order), its labels and `position_id` must be the caller's (`identity_differs`; `position_id_not_recorded` for a manifest written before positions were recorded). With `image_axes`, the caller's axes must give each axis the role recorded for the file (`FileEntry.roles`, or the run's mapping resolved for the header in older manifests); an axis without a role (length 1) fits any letter (`axis_mapping_mismatch`). All errors, naming their images; the table's own hash is checked with `file_sha256` against `csv_sha256`. |
| Masks named as images (D-i) | `mask_name_issues(leaves, mask_names=())`, `images_named_as_masks(leaves, suffixes=MASK_NAME_SUFFIXES)`, `mask_folder_part(path)`, `MASK_NAME_SUFFIXES`, `MASK_FOLDER_WORDS` (`discovery`) | The screen MorphoRelate's `init` ran (`project_image_names`), now discovery's warnings `image_named_as_mask` and `mask_folder_in_experiment` (§1) for every brick. Names are compared in lower case across the whole experiment; the longest `position_id` owns a file, an image of the same folder first. `mask_names` adds the caller's expected mask names (`cells` for `f1_cells.tif`) to `MASK_NAME_SUFFIXES`; `discover_experiment` gives none. A folder counts as a masks folder when a word of its name (`CellLabels` is `Cell` and `Labels`) is in `MASK_FOLDER_WORDS`. `images_named_as_masks` returns the `(image, owner)` pairs of that detection for the caller's own name endings (`["_cells"]`), for a brick that acts on the files itself (MorphoRelate leaves out the images named with a declared mask name). |
| Pixel sizes (D-a) | `same_pixel_size(a, b, roles=("X", "Y"))`, `pixel_size_groups(sizes, roles=)`, `PIXEL_SIZE_TOLERANCE` (`physical_sizes`) | Two pixel sizes are the same when `|a - b| <= 1e-3 * max(a, b)` on every compared axis, in micrometres; one rule for a declared size against a header and for two headers. `same_pixel_size` takes `PhysicalSizes` (converted with `micrometers`) or sizes already in micrometres, one per role, and returns None when either is unknown (no size, or a unit that cannot be converted), never True. The rule is not transitive (1.0 ≈ 1.0009 ≈ 1.0018, but 1.0 ≠ 1.0018), so there is no per-image grouping key: `pixel_size_groups` links images by single linkage, so images of two groups always differ while the extremes of one group may drift; unknown sizes are left out; the largest group first. The folder check's `different_pixel_sizes` uses both (§11). |
| Experiment design | `read_confirmed_design(path, expected_sha256=)`, `ConfirmedDesign`, `DesignCheckError` (`design`) | The one reader of `experiment_design.csv` (§12) for a brick that pools biological repeats (formerly CellPatcher `load_experiment_design` and MorphoFeature `ExperimentDesign.read`). It takes the file as this brick writes it and fills nothing in: UTF-8 without a byte-order mark; the header exactly the §12 columns in order; one value per column in every row (an unclosed quote, a missing or extra value, or a blank line is refused); no value with outer whitespace; `control` and `confirmed` exactly `true` or `false`; counts written as whole numbers of at least 0; `schema_version` 1; `confirmed_on` empty or `YYYY-MM-DD`; `condition`, `biological_repeat` and `repeat_group` pass `check_identity_text` and do not differ only in case (`check_identity_case`); no folder twice, one `control` per condition, at least one row; every group confirmed with one folder per condition (`design_issues`); with `expected_sha256` (the manifest's `experiment_design_sha256`) the file must be that one. The SHA-256 is of the bytes parsed. `ConfirmedDesign` gives `path`, `sha256`, the `design`, read-only `repeat_groups` (`{(condition, biological_repeat): repeat_group}`, the normalization brick's input), `group_members()` (`{repeat_group: {condition: biological_repeat}}`), `conditions`, `controls` and `groups`. Refusals raise `DesignCheckError` (a `ValueError`) with its `issues`: `missing_experiment_design`, `unreadable_experiment_design`, `invalid_experiment_design`, `repeat_group_conflict`, `design_not_confirmed` or `changed_experiment_design`, each an error naming the file (or the group's folders). This brick's step 1 and `check` read the file being edited with the lenient `load_design(output_folder, repeats)` (§12), which returns `(design, changes)`: the saved design brought up to date with the folders, or the default design when there is no file. A saved file that cannot be read raises `DesignCheckError` with the one error `unreadable_experiment_design`, whose message is the same "The experiment design cannot be read: …" as here followed by the lenient reader's refusal, naming the line and column where it can (the `DesignFormatError` is its `__cause__`); no default design is returned and the file is not touched (owner decision of 31 October 2026, plan §12 question 43). **Breaking change for the next release:** up to v0.3.4 `load_design` returned a third value, `problem`, the reason as text, together with the default design. |

