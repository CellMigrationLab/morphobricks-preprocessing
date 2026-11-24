# Preprocessing Brick Contract (Milestone 0)

This document fixes the rules other bricks can rely on. Changing any of them requires a
new `schema_version`.

Sections 3–7 (pixel types, histograms, the CSV, group statistics, normalization
parameters) are defined by the [normalization brick's contract][normalization contract]
(§1.2 and §6), whose code computes them (`morphobricks_normalization`, plan §12 question
19). They keep their numbers here as short pointers, with the rules this brick adds for a
whole experiment; any change to them goes through the normalization brick.

[normalization contract]: https://github.com/CellMigrationLab/morphobricks-normalization/blob/main/docs/contract.md

## 0. Every run is described (v0.5.1, Spec ADR 0002 AR10/AR11)

There is no backward compatibility before the first pipeline release (owner policy of 10
November 2026). **Every run needs a published experiment description** (`morphobricks.toml` in
its output folder, experiment-v1). Without one every route refuses the run with the one code
`A_DESCRIPTION_REQUIRED`, before anything is read or written, naming the fix (the notebook's
describe steps and *Check and save*; the command line's `describe` shows what is still open):

| Route | Refusal |
|---|---|
| `run_experiment` | `RunResult(written=False)` whose first issue is the error `A_DESCRIPTION_REQUIRED`, with `output_inside_experiment` / `output_inside_masks` when the folders given say so (their paths are compared; no image header and no recorded run is read); no report, no lock, nothing in the output folder (`required_description.undescribed_issues`). |
| `prepare_mask_step` (and the command line's `masks`) | `MaskStep.refusal` is the error; nothing is read. |
| Command line `check`, `run`, `masks` | The error is printed, exit status 1, before the recorded run is read, `--log` opened or `--remove-stale-lock` applied; nothing written. |
| `RunInputs.open` | `DescriptionRequiredError` (a `RunInputsError`, `code == "A_DESCRIPTION_REQUIRED"`). |
| Notebook | The run waits for *Check and save*; there is no undescribed notebook (`PreprocessingNotebook` has no `describe` argument). |

Removed in v0.5.1 (PP-C), each refused rather than read: the undescribed ("v0.4") run and its
warning; the `with_description` switch (the name refusals `R_FOLDER_NOT_NFC` and
`R_MASK_TYPE_NAME_INVALID` always apply); `experiment_design.csv` as an input (`run_experiment`
has no `design` argument, the command line no `--design` option and no `design` command, `check`
writes no template; the run derives the file from the description, an output only, §12); run
manifests of versions `"1"`, `"2"` and `"3"` (§9: refused, "run preprocessing again"). The FOV
key and `position_id` (the image file stem) are unchanged (owner ruling 2): a position is always
read together with the canonical condition, repeat and technical-repeat IDs (§9.1), never as an
identity alone.

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
| Links | Contain (issue #81, owner 2 November 2026): a symbolic link to a file or folder (on Windows also a directory junction, which Python 3.11 does not report as a link) is followed only when its real path (`os.path.realpath`) is inside the experiment folder, itself resolved. A link that leaves it is not followed and is the error `link_outside_folder`, naming each link and its target (a broken link too); a link to a folder on its own path (the folder holding it, or one above) is `link_loop`; one file reached by two paths (a link and its target, two links, a linked folder and its target, or two hard links: the same device and inode, from the one `stat` taken per image or mask; owner, 2 November 2026) is `linked_twice`, naming both. A broken link that stays inside is kept and reported by its reader (`unreadable_tiff`, `unreadable_mask`), never skipped. Hidden and system names (§1) are skipped before any link is looked at, so a hidden link is never followed; a contained link *to* a hidden folder is followed, because that folder is itself never walked, so its files are found once, under the link's name, which the user chose. The links inside an OME-Zarr store are held to the same rule, though the store is not entered as a folder level: its folders are listed and only its links resolved, about 84 ms per 20,000 store files on each discovery (accepted by the owner, 2 November 2026). The masks folder follows the same rule (§15), with `RunInputs` (§9.1) and `convert` (§17) refusing the same links; one helper, `links.LinkGuard`, walks both folders. |
| Mount points | Refused (owner, 3 November 2026), as far as they can be seen: a folder (or file) below the experiment folder or the masks folder that is a mount point by the rules below is not entered and is the error `mounted_folder`, naming it. A mount point is a path listed in Linux's `/proc/self/mountinfo` (the only way to see a bind mount of the same file system, for which `os.path.ismount` and the device number report an ordinary folder), or a folder, image or mask file on another device (`st_dev`) than the selected folder: another file system or Windows volume, or a Btrfs subvolume (a file's device comes from the one `stat` taken per image or mask; review of rc5). The selected folder itself may be on any mount. The same rule holds inside OME-Zarr stores (their files are held to the mount table only: they are not `stat`ed), and `check`, `run`, `convert` and `RunInputs` refuse the same mount points (a refused run publishes no scientific outputs and leaves the sources untouched; its discovery report is written). **Not seen:** a mount created after the walk started (the table is read once per walk); and where no mount table can be read (macOS, BSD, Windows, a Linux without `/proc`), a mounted single file or a mount of the same device (BSD `nullfs`). macOS `bindfs` (FUSE) and Windows mounted folders have another device, and a Windows mounted folder is also a mount-point reparse point, held to the link rule. **Refused although not mounted:** on an overlay file system (`overlayfs`, as in many containers) whose layers are on different file systems, mounted with `xino=off`, files (not folders) may report another `st_dev` than the selected folder and are then `mounted_folder` (copy the experiment to an ordinary folder, or use `xino=on` or `auto`). |
| Hard links | An image or mask whose link count is above 1 (`st_nlink`, from the one `stat` taken per image or mask; it has other names, perhaps outside the folder, through which it can change) is the warning `hard_linked_file`, naming it and its count (owner, 3 November 2026). It is read and fingerprinted as any other file; the files inside an OME-Zarr store are not counted. |
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
| Axis check (v0.4.0) | Every TIFF's confirmed roles are compared with the axes its own header names (`named_axes`, §16), position by position (`header_axis_issue`, `axis_disagreements`): a named axis keeps its letter as role, except that the RGB samples `S` may be C when the header has no C, and a length-1 axis may have no role. Confirmed roles that give the named Y and X the other way round are the **error** `axes_yx_swapped`; any other role for a named axis (`ZYX` confirmed `TYX`, a named angle or lambda axis given a role) is the **error** `axes_contradict_header` (a named axis longer than 1 without a role is `unassigned_axis`, a position axis `unsupported_axis`). Roles confirmed for a plain stack that put X before Y, such as `TXY`, are the **error** `axes_x_before_y` (owner decision of 1 November 2026): nothing is ever read transposed, so the images are saved again with Y before X, or as OME-TIFF copies with Y before X. Each error names every file with its header axes and confirmed roles and the likely cause (`likely_axis_cause`: tifffile labels a 3-D stack saved with `imagej=True` or `ome=True` `CYX`; an ImageJ stack of slices reads as Z; rows and columns exchanged), and offers two fixes: correct the axes, or save named-axis copies and run preprocessing on them. Such an image is never read: not transposed, not under a contradicted declaration. An axis the metadata does not name (a plain stack `IYX`, `QYX`, `QQYX`, or shaped metadata with a placeholder `QYX`; a plain 2D `YX` page and RGB `YXS` are named, decision D-d) takes the role the user confirmed: the image is read with it, with the **warning** `axes_not_named` recommending named copies before segmenting in other tools, and recorded as `axes_from: "confirmed"` (`AxisReview.axes_from`; `"header"` for every other image). A confirmation covers only the files it was given for, as they were then (`axis_check.ConfirmedAxes`, made by `fingerprint.confirm_axes` with each file's fingerprint; audit 2 B-1): a plain stack outside it, new or changed since (another fingerprint), is the **error** `plain_stack_not_confirmed` and must be confirmed again; it is never read with roles confirmed for other images. An `AxisMapping` given to `run_experiment` confirms every file the run finds. OME-Zarr images take their roles from their OME-NGFF axes and never have these issues: confirmed roles that contradict those axes are `ome_zarr_axes_mismatch`, whatever the arrangement ("OME-Zarr axes" below, issue #72). Since one mapping covers each TIFF arrangement, all files of one arrangement pass or fail together. The notebook shows these codes as one card; when the roles agree with every header, the notebook and `run` say so ("Axes checked"). Before v0.4.0 a swap was read transposed and a contradiction under the declaration, silently. |
| Unsupported axes | Position-like axes (tile `R`, mosaic `M`, row `K`, column `J`) cannot have a role. If they hold more than one plane, the file is an error. |
| Singleton axes | An axis of length 1 needs no role; its index is always 0. |
| Missing axes | A file lacking axes the mapping has is read with those axes as length 1 only when the user confirmed this and only one arrangement fits; its coordinates for them are 0. The confirmation is `missing_as_singleton` (`run_experiment`, the notebook's step 2), `--missing-as-singleton` for both `check` and `run` (one option, `cli_run.add_missing_as_singleton`, with one default: the earlier run's answer in the output folder, else no, `cli_run.missing_as_singleton`). `check` answers the metadata's `missing_axes` with the review of the suggested roles under that confirmation (`run.check_step.suggested_review(pre, missing_as_singleton=)`): the review's `missing_axes` or `ambiguous_missing_axes`, or nothing when every file fits, as `run` reports for the same roles (F10, matrix run of 3 November 2026; before, `check` always stopped on a mix of 2-D images and Z-stacks). |
| Notebook readiness | Confirming a valid singleton mapping resolves its missing-axis error for running; folder, exclusion and all other metadata errors still block processing. |
| Series | Only the first image series, at full-resolution level 0. A file with more than one series is an error. |
| OME-Zarr axes | Taken from the OME-NGFF metadata (0.4 and 0.5; 0.3 axis names): a time axis is T, a channel axis C, space axes named z, y, x are Z, Y, X; any other axis has no role. **Nothing is guessed** (issue #73): the version the metadata state is read before the axes. An OME-NGFF 0.1 or 0.2 store is unreadable (`unreadable_ome_zarr`) whatever it contains: its metadata do not name its axes, and the order its specification fixes (`t, c, z, y, x`) is never assumed; the message says to re-save it as OME-NGFF 0.4 or later (owner decision, 2 November 2026). A store of OME-NGFF 0.3, 0.4 or 0.5 without `axes` (which those versions require), and a store without `axes` that states no version or another one, is unreadable too (`unreadable_ome_zarr`, naming what is missing; there is no confirmation path); so are `axes` that are not a list. **Every OME-Zarr image is matched by the roles it declares** (issue #72), whatever its arrangement: with the experiment mapping's arrangement, a confirmed role that is not the axis's declared letter (`ZYX` confirmed `TYX` or `ZXY`; an axis of no known type, `QYX`, confirmed `CYX`) is the error `ome_zarr_axes_mismatch`, naming each image with its declared axes and confirmed roles, and the image is never read under them (`axes.ome_zarr_mapping`, also used by `file_mapping_for`). Images in another order are matched by role: a Z, C or T axis the experiment lacks is allowed only with length 1, an axis without a role only with length 1, and experiment roles the image lacks follow the missing-axes rule; otherwise `ome_zarr_axes_mismatch` is an error. An OME-Zarr image cannot be converted (§17), so these are not axis-check errors. |
| OME-Zarr images | Only level 0 (the first dataset of the first multiscales image) is read. A store with more than one image (several multiscales, a bioformats2raw folder with several series, a plate, a well with several fields) is an error (`multiple_series`). |
| OME-Zarr labels | The label images of an image's OME-NGFF `labels` group (0.4 and 0.5) are listed with `ngff_label_names` and opened, level 0, with `open_ngff_labels` (by name; the name may be left out when there is one). They are never analysed; later bricks such as patch extraction use them as masks. |
| Experiment mapping | Defined on the TIFF arrangement when the experiment has TIFF files; in an experiment of OME-Zarr images only, on their arrangement with the most axes. |
| Channel names, significant bits | Recorded in the run manifest when metadata provides them; not part of the CSV. Channel names that differ at the same channel index are the error `channel_names_differ` (issue #37): channels are matched by their number, so two stains at one number would be pooled as one channel. Only named channels are compared; a channel without a name in some images is not. Names are read without leading and trailing whitespace, for every image (TIFF, OME-Zarr and this brick's own copies), so `DAPI ` and `DAPI` are one name; the manifest records it (`channel_names_trimmed`, §9). Whitespace inside a name is compared exactly (`DA PI` and `DAPI` differ), with no override. An OME-Zarr channel label that is not text is malformed metadata: the image is unreadable (`unreadable_ome_zarr`), never read as an unnamed channel. |
| Physical sizes | Read with the header, not part of the CSV or the manifest, and compared between images in the folder check (§11, in micrometres with each axis's own unit; a size without a known unit cannot be compared): `PhysicalSizes` with the size of one step along X, Y, Z and T and each axis's unit (OME-NGFF unit names). TIFF: OME `PhysicalSize{X,Y,Z}` with their own units (micrometres when left out) and `TimeIncrement`, or ImageJ resolution, `spacing` and `finterval` with `unit`, `yunit`, `zunit`. OME-Zarr (0.4 and 0.5): the `scale` of level 0's `coordinateTransformations`, times the multiscales entry's own `scale` when given, with each axis's `unit` (common spellings such as `µm`, `um`, `micron` and `s` given as UDUNITS-2 names, others as written). Length units are the microscopy units of MorphoBricks decision D-a2 (§16): `pm`, `Å`/`angstrom`, `nm`, `µm` (`um`, `micron(s)`, `micrometer(s)`, `micrometre(s)`), `mm`, `cm`, `m`, and the names of each in US and British spelling (`nanometre`, ...); ImageJ's escaped micro sign (`\u00B5m`) is recognised. A size in any other unit (`inch`, `decimeter`, ...) is not converted to micrometres, so it cannot be compared: that axis is uncalibrated, and the unit as written is kept in `PhysicalSizes.unsupported_units` (axis letter and unit, information only, not part of equality) so that the folder check names it (`unsupported_pixel_size_unit`, §11). A plain TIFF's resolution tags (no OME or ImageJ metadata, for example 300 dpi with the unit inch) are never a pixel size (decision D-c): such an image has no calibration. Nothing is guessed: what the metadata does not give is None. Nothing is dropped silently either (issue #94): a calibration given but malformed (`coordinateTransformations` that are not a list, a transformation of a type other than `scale`, `translation` and `identity`, more than one scale, a scale in a separate file (`path`) or not one number per axis) gives no sizes, and a scale value of X, Y, Z or T that is not a positive finite number gives none on that axis; each is recorded with what is wrong (`PhysicalSizes.invalid`, `ngff_scale.read_scale`) and named by the warning `invalid_physical_calibration` (§11). The same holds for a size that is not a positive finite length in micrometres once converted (issue #93: `1e308 m` overflows to infinity, `1e-320 pm` underflows to zero; also an OME-Zarr level-0 scale times the image's scale that does so), from any TIFF or OME-Zarr metadata: the size is None, never `inf`, and `PhysicalSizes.invalid` gives the axis and the value; an OME-Zarr scale without a unit is kept with the unit None (in `PhysicalSizes`, with `""` in `unsupported_units`; the run records it as unknown, audit I3, and `unsupported_pixel_size_unit` names it), except on the axes a multiscales entry's `metadata` lists as `unknown_scale` (copies written by this brick up to v0.3, §10), whose sizes are None. |
| Depth slices (v0.4.0) | MorphoBricks v1 analyses 2-D planes (owner decision of 5 November 2026; 3-D feature extraction is a separate project). An image with more than one depth slice is the **error** `z_stack_not_supported`, naming every such image with its number of slices: from the Z axis its header names (`metadata.check_metadata`, before any role is confirmed: an ImageJ hyperstack, OME-TIFF, shaped TIFF or OME-Zarr image) and again under each image's confirmed roles (`review_axes`, which supersedes it; `size_checks.z_stack_issues`): a plain stack confirmed with a Z role is refused, and an image the mapping does not fit is measured by its header. An image whose header names Z but whose roles are confirmed otherwise (an ImageJ stack of time points that tifffile reads as Z, confirmed `T`) is the axis error `axes_contradict_header` instead, never read, so that its fix, named-axis copies (`convert`, §17), is not blocked. It is found before any pixel is read, so `check`, `run` and `convert` stop before writing anything but the report. A Z axis of one slice, named by the header or not, and an image without one are accepted as they are; nothing is squeezed, projected or guessed. The fix is an explicit, recorded step, never an implicit projection when an image is read: `convert --project max|mean|sum` writes 2-D projected copies (§17, plan §12 question 77) and the run is made on the copies. Time-lapses (T > 1) are accepted; their masks need the same T (§15). |
| Plane counts | Images may have different numbers of time points or planes along an unassigned dimension; this is a warning (§11). |
| Channel counts | Stage 3 S3.3 (owner decision of 11 November 2026; normalization N43 replaced by per-row presence, ADR 0003): imaging units may have different channels. Each unit lists its channels by canonical ID in the published description (experiment-v1 §4.4: entry *i* is C*i*); the same ID is the same stain; an image's channel count must match its unit (`R_CHANNEL_COUNT_MISMATCH`). Inside one unit (a leaf folder) every image has the same number of channels: along the C axis, 1 without one (also when a missing C axis is confirmed as length 1). A different number there is the error `different_channel_counts` naming the folder, found from the headers before any pixel is read: in the folder check (the `C` dimension, or the RGB samples `S` when there is no `C`), and again with the confirmed axis roles, which decide (§11); a run names it `R_UNIT_NOT_HOMOGENEOUS`. Images of different folders are never compared. Units of different image types (RGB beside channel units, Spec `v29`) stay refused with `R_HETEROGENEOUS_LAYOUT_NOT_ENABLED`. |

## 3. Pixel types

Defined by the [normalization brick's contract][normalization contract] §1.2.1: integers
int8–uint64 and float32/float64, everything else rejected, with their default and allowed
bin counts. This brick adds: one dtype per experiment (`mixed_dtypes` error), and all
planes of an experiment use the same bin count. In a described run (a published
`morphobricks.toml`) the pixel type is compared among the units that declare the same
`image_type`, and the notebook's folder check compares images inside each unit; units of
different image types (RGB and fluorescence) are refused as `R_HETEROGENEOUS_LAYOUT_NOT_ENABLED`
(kept at Stage 3: the owner enabled different channel lists, not mixed image types), not as
`mixed_dtypes`. It is decided from the description alone, after preflight has read and bound
every header but before any header check and before any pixel read, and is then the run's only
issue, error or warning (issues #321, #334): no header check runs after it, and nothing preflight
found is reported beside it (discovery such as `unreadable_tiff`, the masks such as
`masks_folder_not_found`, the exclusions such as the warning `excluded_path_not_found`, the
conversion record, the lab-tool links' repeat codes). Only what stops a run before it holds the
output folder (no published description, an output folder inside the experiment or the masks, a
locked output folder) comes first. Every run is described (§0). The
invariant is that a previously blocking input stays blocked; the diagnostic codes may change
(the per-type checks and the Spec's `R_*` codes replace the global `mixed_dtypes` and
`inconsistent_dimensions` of a described run).

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

## 5. CSV schema (`plane_histograms.csv`, schema version 5)

**Schema version 5 (Stage 3 S3.3, Spec ADR 0003 HV1-HV4, normalization v0.5.0).** One row per
XYC plane; the presence column `channels` (the plane's canonical channel IDs in its unit's
source order, `;`-separated) after the plane columns; the per-channel columns of **every channel
ID the description declares**, sorted, named `<Category>_<Feature>_<ID>`
(`Intensity_MinIntensity_DNA`, `Histogram_Count_DNA_00042`); a channel absent from a plane's
unit is blank, never a zero histogram. The underflow and overflow are centred moments
(`Histogram_{Underflow,Overflow}{Count,Mean,Std}_<ID>`). Written with
`write_plane_histograms(records, path, channel_ids=...)` (each image's staged rows with its own
IDs). A version 4 table is refused by the reader: the next run recomputes every row (manifest
version 5), `RunInputs` refuses it naming the fix. A failed channel measurement fails the whole
table (HV3): in this brick a plane that cannot be read stops the run (`unreadable_planes`) and no
table of this run is published. Its message names each failed plane:
`<image>, plane t=<T>, z=<Z>, c=<C> (channel <ID>): <cause>`, an axis the image does not have
left out, the channel ID from the description, `;`-separated, by image in sorted order and
then in the order the planes are read; once a channel fails the image's remaining planes are
still measured so that every failed one is named, and none of its rows is staged. An image that
cannot be read plane by plane at all (or, with content verification, hashed) is named with its
cause alone (`<image>: <cause>`). The message names at most 20 failed planes per image, in that
order, followed by `and <N> more in <image>` and "The run report lists every failed plane." (owner
decision of 17 November 2026); the run report lists every one under `unreadable_planes` (image:
every failed plane, the same for any number of workers). The issue's `paths` are the images. Histogram numbers of channels that are present are unchanged
from version 4. The rest of this section describes the version 4 layout it replaces, by C index.

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

This brick's optional group statistics (`group_summaries`, the notebook's step 4) pool
**one channel at a time** (issue #104): one summary per group and channel, with
`xyc_plane_count` (XYC planes) and `channel_histogram_count` (histograms pooled) reported
apart. Channels are pooled together only when asked (`pool_channels=True`), and the
summary then says so (`channel` None, shown as `all channels (pooled)`). A group whose
planes have different numbers of channels is refused (`ValueError`). These statistics are
shown only; no file is written, so no file schema depends on them.

## 7. Normalization parameters

Defined by the [normalization brick's contract][normalization contract] §1.2.4: every
method is an offset and a scale, `(pixels − offset) / scale`.

## 8. Validation severities

| Severity | Situations | Effect |
|---|---|---|
| Error | Experiment design not confirmed (`design_not_confirmed`) or a repeat group holding two folders of one condition (`repeat_group_conflict`), when a design is used (§12); image type RGB for images that are not three channels of an integer type (`rgb_not_possible`, §13); mixed dtypes; images with different numbers of channels (`different_channel_counts`, §2) or different channel names at the same channel number (`channel_names_differ`, §2); unreadable TIFF (header or pixel data) or OME-Zarr folder in a leaf folder that is not excluded; OME-Zarr images without the `zarr` extra; OME-Zarr images whose declared axes do not fit the experiment's roles or that the confirmed roles contradict (`ome_zarr_axes_mismatch`, §2); OME-Zarr images that do not name their axes, and every OME-NGFF 0.1 or 0.2 store (`unreadable_ome_zarr`, §2); every image excluded; more than one image series in a file; unsupported sample format or axis; an image with more than one depth slice (`z_stack_not_supported`, §2; MorphoBricks v1 analyses 2-D planes); missing/duplicate X or Y, duplicate roles, unassigned non-singleton axis, inconsistent mapping; an image whose confirmed roles swap Y and X, contradict its header or put X before Y (`axes_yx_swapped`, `axes_contradict_header`, `axes_x_before_y`, §2); plane that cannot be read within the memory limit, not even in blocks (for example a plane stored compressed in one piece); a plane over the memory limit stored with a TIFF compression whose decoding memory is not measured, which is never read in blocks (`compression_not_bounded`, §9.1 *Codecs*, #356); folder named `single` in another case, or beside other folders (§1); folders of one level differing only in case, anywhere in the experiment (`case_conflict`); folder or image names with outer whitespace or reading as a missing value (`invalid_name`, §1); images in one leaf folder with the same position identifier (§1); links that leave the experiment or masks folder, loop, or reach one file twice (`link_outside_folder`, `link_loop`, `linked_twice`, §1); a mount point below the experiment or masks folder (`mounted_folder`, §1); folder below the technical-repeat level; images in a folder that also holds subfolders (`tiff_outside_leaf`, §1); output folder inside the experiment or inside the masks folder (`output_inside_masks`, §15); experiment or named-axis copies inside the masks folder (`experiment_inside_masks`, `copies_inside_masks`, §15); images named as masks of other images (`image_named_as_mask`, §1); no usable planes; no axis roles given or recorded (`axes_not_confirmed`, §9.2); another run holding the output folder's lock (`output_locked`, §9.2); a copy tree whose conversion record cannot be read (`conversion_record_unreadable`, §17), or whose copies changed, went missing or were added since the conversion (`copy_changed_after_conversion`, §17; owner decision of 2 November 2026, PP-12: the bricks would refuse the run); an image that changed or went missing while the run checked or read it (`source_changed_during_run`, §9.4; no scientific outputs are published and sources are untouched; the discovery report is written); a mask that changed while its header was read (`mask_changed_during_check`) or after it, before the run's commit (`mask_changed_during_run`, §9.4, issue #160; the same); a recorded `experiment_design.csv` that cannot be read (`design_unreadable`); a `calibration.csv` that cannot be read (`calibration_unreadable`) or that states sizes for a target that is neither a folder nor an image of the experiment (`calibration_target_not_found`, §9.3); no experiment folder given or recorded (`experiment_not_recorded`, §9.2); when masks are given, a masks folder missing or inside the experiment, mask names differing only in case, two masks of one type for an image, a mask whose name could belong to two images or types, or a mask that is unreadable, has axes other than Y, X and named or stated Z/T (`mask_not_2d`), has stated axes that do not fit it or contradict the axes its metadata names (`invalid_mask_axes`, `mask_axes_contradict_header`), is stated `YX` (`shared_mask_not_supported`, §15), is stored with X before Y (its header's or its stated axes; refused by `check` and `run` too, never recorded with empty axes) or with its image's Y and X sizes swapped (`mask_layout`; never transposed), is of another size than its image (in `run`, its image's Y and X under the confirmed roles; not reported for an image with an axis error), is not of integers or booleans, has other depth slices or time points than the image's other masks or, in `run`, than the image (§15) | Processing is blocked |
| Warning | Images whose metadata does not name their axes, read with the confirmed roles (`axes_not_named`, §2); images or masks with other names, hard links (`hard_linked_file`, §1); in a tree of named-axis copies, sources changed or gone since the conversion (`conversion_source_changed`, §17); folders named like a masks folder holding images named as masks (`mask_folder_in_experiment`, §1); intensity checks of the written table (`image_intensity_outlier`, `repeat_intensity_shift`, `saturated_image`, `no_intensity_spread`, `channel_without_finite_pixels`, §14), reported apart from the run's issues; channels of a unit that do not look like the same channels of the other units (`channel_unlike_others`, `possible_channel_swap`, §14.1), reported the same way; files that are not images; empty folders; images excluded by the user, or excluded paths that match no image; non-finite pixels; different image widths or heights; different numbers of depth slices, time points or other planes; physical pixel sizes that differ or are known for only some images; a stated pixel size that differs from the header (`calibration_differs_from_header`, §9.3; the stated size is used); unbalanced numbers of images; biological repeats missing from some conditions (§11); planes without finite pixels while others remain; channels whose central intensity range covers too few histogram bins (`narrow_histogram_range`, §4); planes over the memory limit that are read in blocks; images without a mask of a type other images have, masks matching no image, masks of excluded images, files outside the mask-type folders (§15) | Processing continues; the item is reported |

Every message states what happened in plain language, lists the affected paths, and
suggests an action. Technical details go to the diagnostic log. The command line's log
(`--log FILE`, issue #99) is checked before it is opened, and opened only after the other
arguments: it is refused inside the experiment or masks folder (given, or recorded by the
earlier run in the output folder), inside `convert`'s folder of copies, as a folder or in a
folder that does not exist, and as or inside an output artefact (`plane_histograms.csv`,
`run_manifest.json`, the report files, `experiment_design.csv`, `calibration.csv`,
`mask_relationships.json`, the lock, `.morphobricks-conversion.json`, the publication and
staging folders `.publish` and `.staging` (§9.4); `log_target.PROTECTED_OUTPUTS`). Paths are compared resolved and, for the folders that exist, by
device and inode (another spelling of a folder on a file system that ignores case); output
names ignore case; an existing file with a second name (a hard link) is refused. Any other
name in the output folder is allowed.

**Write-target rule for other bricks (owner, 3 November 2026).** That rule is public as
`write_target_refusal(path, *, protected_folders, protected_files={}, output=None,
output_names=())` (`morphobricks_preprocessing.write_target`, exported by the package), so
another brick that writes a file its user names (MorphoFeature's `--to-segment`) applies
preprocessing's rule rather than a copy of it. It returns None when `path` may be written,
else the reason as a clause for the caller's own message (`"it is inside the masks folder
(/data/masks)"`; `--log` words it as above, byte for byte as before). It refuses, in this
order, and the first refusal is returned: a path that is one of `protected_files` (by
resolved path, or `os.path.samefile`: "it is the {name} ({file})"); a path inside, or equal
to, one of `protected_folders` (by resolved path, and for a folder that exists by device
and inode of each existing folder above the path: "it is inside the {name} ({folder})");
the folder `output` itself or a path that is, or is inside, one of its `output_names`
(ignoring case: "it would write over the run's {name}"); an existing folder ("it is a
folder"); a path whose folder does not exist ("its folder {folder} does not exist"); and an
existing file with more than one name ("it is a file that has another name too (a hard
link)"). Mapping keys name what is protected; None values are left out. It only reads the
file system (`stat`) and writes nothing; the caller writes right after, so what changes in
between is not seen: a file or hard link created at the path, the path or a folder above it
replaced by a link into a protected folder, or a protected folder created. A single string
as `output_names` is refused (`TypeError`), not compared letter by letter.

In notebook step 1, message details start collapsed with orange warning and red error
counts visible outside the dropdown. Collapsing details does not change severity or
whether processing is blocked. The consistency issues of §11 are shown in their own
**Dataset consistency** panel, next to the balance table, and not repeated under the
other messages.

## 9. Run manifest (`run_manifest.json`, manifest version 5)

UTF-8 JSON next to `plane_histograms.csv`, written only when the run succeeded. Its
replacement is the commit of the run's outputs: they are one **generation**, published
crash-consistently (v0.4.0, PP-12, owner decision of 2 November 2026; §9.4), and the
manifest names the table and the design by their SHA-256.

**File permissions (v0.4.0, plan §12 question 52).** Every file the brick writes (the
table, the manifest, the design, the report files, `calibration.csv` and
`mask_relationships.json`) gets the permissions a plain `open()` would give it: a new file
`0666` without the process's umask (`0644` with the usual umask `022`), so colleagues can
read the results in a shared folder; a file that already exists keeps its own mode, as the
user set it. Before v0.4.0 every output was `0600` (owner only) whatever the umask, because
the temporary file of the atomic write kept its private mode; such files keep `0600` until
the user changes them (`chmod a+r`) or deletes them. On Windows file modes do not apply.

Only one run writes to a given output folder at a time, enforced by a lock (§9.2). The
publication protocol (§9.4) does not change any file's schema or statistical
interpretation.

| Key | Meaning |
|---|---|
| `manifest_version`, `schema_version` | Manifest format (`"5"`) and CSV format (`"5"`), written as JSON **strings**, not numbers. Version 5 is the only manifest version written and read (v0.5.2, Stage 3 S3.3; version 4 was the only one in v0.5.1, owner ruling 16, ADR 0002 AR11): every manifest carries `experiment_description`, and holds a `content_sha256` in every file entry exactly when `settings.verify_content` is true (a content-verification run, which every run is unless it opts out, #159). The reader and the writer refuse a manifest without `experiment_description` or with content hashes that do not match `verify_content`. Any other version (`"1"` before v0.4.0; `"2"` and `"3"`, the undescribed runs of v0.4.0 and v0.5.0; `"4"`, the runs of v0.5.1 with their schema version 4 table; a later one) is refused with `ManifestFormatError: Manifest version '…' is not supported … Run preprocessing again …`: by `read_manifest`, `RunInputs.open` and `check_sources`; the next run in that output folder reads every image again and replaces it. Nothing is converted. Switching between a strict and a default run recomputes every row. A manifest whose `schema_version` is not the current one makes the next run recompute every row. |
| `written_at` | UTC time of writing, ISO 8601. `written_at` is the only field excluded from byte identity (owner decision of 16 November 2026): two runs of the same inputs and settings write byte-identical outputs except for this value (and the manifest SHA-256 the report names, which follows from it), whatever their number of workers; the tests compare the manifest's raw bytes under a fixed clock, never with the field deleted. A rerun whose manifest would differ from the earlier one only in `written_at` keeps the earlier time, so the manifest's bytes and SHA-256, which later bricks pin, stay the same (cross-brick test F10, 3 November 2026); the report still records the rerun. |
| `experiment_root` | Absolute path of the experiment folder, for information. |
| `dtype` | Pixel type of the experiment. |
| `settings` | `bin_count`, `memory_limit` (bytes), `missing_as_singleton`, `exclude` (sorted relative paths), `histogram` (`HistogramSettings.to_dict()` of the normalization brick: `library_version`, `histogram_range`, `robust_quantiles`, `min_central_bins`, §4; required), `verify_content` (JSON boolean, below: `true` when the run verified content, `false` when it opted out, so that an opt-out is on record (#159); required). Since v0.5.1 (SWEEP-PP) no key the writer always writes is defaulted when absent: such a manifest is refused. |
| `axis_mapping` | `axes` (file axis labels) and `roles` (`X`, `Y`, `Z`, `C`, `T` or null per axis). |
| `labels` | One entry per leaf folder: `folder` (relative path, `.` for the root) and its three labels. |
| `files` | One entry per analysed file, sorted by path: `relative_path`, `size` (bytes), `mtime_ns`, the three labels, `axes`, `singleton_roles`, `plane_count` (its rows in the table: XYC planes), `channel_names` (list, null for unnamed channels; empty if none in metadata), `significant_bits` (null if not in metadata), `read_in_blocks` (true when the image's planes are over the memory limit and are read in blocks; the histogram rows are the same either way, but the image QC rows are not, so with it a changed memory limit recomputes them, §19; required), `position_id` (the image's position identifier, §1: its name without the extension, never edited; required: an entry without it, with null, or with another value than `position_id(relative_path)` is refused naming the entry, by the reader and the writer, round-2 audit A-m5), `channel_names_trimmed` (true when leading or trailing whitespace was removed from the image's channel names as they were read, §2; `channel_names` holds the trimmed names; required), `channel_ids` (manifest version 5, Stage 3: the image's canonical channel IDs in source order, its unit's confirmed list; required; part of the row key, so rows recorded under other IDs are recomputed), `roles` (the role of each of the file's `axes`, `X`, `Y`, `Z`, `C`, `T` or null, from the file's own mapping: an OME-Zarr image in another order than `axis_mapping` has its own roles, so later bricks read these rather than rebuild them from `axis_mapping`; required). For an OME-Zarr image, `size` is the total size of its files, `mtime_ns` the time of its newest file, and `inventory_sha256` (v0.4.0, PP-12, issue #86; only for OME-Zarr images) the SHA-256 of its inventory: one line per file (its path relative to the image with `/`, a NUL, its size, a NUL, its `mtime_ns`, a newline), sorted by path, its root `labels` group left out; links to folders inside the image are followed, as the reader follows them, and links that reach one folder twice are refused. Adding, removing, renaming, resizing or touching any chunk or metadata file changes it; an OME-Zarr image recorded without it (v0.4.0rc3 and earlier) counts as changed (recomputed; `RunInputs` refuses it). Like a TIFF file's size and time it is metadata only (design v0.4 §1): a file rewritten with the same size and its time set back is not seen, only hashing the content would. **Version 2 (v0.4.0):** `roles` is required, with `axes_from` (required): `header` when the metadata names every axis and the roles agree, `confirmed` for a plain stack whose roles the user confirmed (§2); and `pixel_sizes`, one entry per role `X`, `Y`, `Z`, `T` with `value`, `unit` (OME-NGFF name) and `source`: `header` (the image's metadata) or `stated` (`calibration.csv`, §9.3) with a value, or `pixels` (stated as deliberately uncalibrated) with `value` and `unit` null; `value` and `source` null for an axis nothing gives a size (never guessed). A `header` or `stated` size without a value, or a value with no source or with `pixels`, is refused. The reader refuses an entry without `roles`, `axes_from` or `pixel_sizes`, and the writer refuses to write one (no older manifest is read leniently, SWEEP-PP). `content_sha256` (only in a `verify_content` run): the SHA-256 of the image's bytes, or for an OME-Zarr image of one line per file (its path relative to the image with `/`, a NUL, the file's SHA-256, a newline) over the files its inventory covers, in path order (`source_content.source_content_sha256`). |
| `csv_sha256` | SHA-256 of the table written with this manifest. |
| `experiment_design_sha256` | SHA-256 of the `experiment_design.csv` the run derived from its description and wrote with the table (§12), so later bricks can check that the design they read is the one the run wrote. |
| `masks` | The masks paired with the images (§15), when the run was given a masks folder or its OME-Zarr images have label images (always used): `folder` (absolute path of the masks folder, null when only OME-Zarr label images were used), `types` (sorted mask-type names) and `files` (one entry per image and mask type, sorted by image then type: `image`, `type`, `path`, `source` `folder` or `ome-zarr`, `axes` and `shape`: the mask's axes and lengths without its length-1 axes other than Y and X, `YX` for a 2D mask (`[1, 512]` for a mask one pixel high), with `Z` and/or `T` for a mask with depth slices or time points, e.g. `TYX` and `[50, 512, 512]`; §15). Absent when the run had no masks. Optional key: the table does not depend on the masks. `axes` and `shape` are absent in manifests written before them, whose masks are all 2D; a later brick must read `axes` before using a mask and never apply a 2D mask to several planes. **Version 2:** `axes` are the mask's axes as stored (its header's letters: `IYX` for a plain stack), and each mask entry also has `size` and `mtime_ns` (its fingerprint, the size and modification time `source_signature` gives: the mask file, or the label image's folder), `roles` (one per axis of `axes`, what each is read as: `TYX` for that plain stack stated `--mask-axes Cells=TYX`), `axes_from` (`header`, or `stated` when the axes were stated for its mask type). The release candidates up to v0.4.0rc6 also wrote `shared` (true for a 2-D mask stated `YX` for its type: one plane shared by every plane of its image); the option was removed by the owner decision of 5 November 2026, the key is no longer written, and `RunInputs.open` refuses a manifest that records `shared: true` (stated or not), naming the masks and asking for one mask plane per time point and a new run. A mask is read as stored, never reordered. |
| `image_type` | `fluorescence` or `rgb`, the image type the user confirmed (§13); null when the run was given none, or absent in manifests written before it. Optional key: the manifest stays version 1. Changing it recomputes nothing. |
| `channels_from_rgb_samples` | `true` when the C role is the RGB samples axis of images stored as RGB colour (§13), `false` otherwise; absent in manifests written before it. Optional key. |
| `intensity_checks` | The limits of the intensity checks run on the table (§14): `IntensityCheckSettings.to_dict()` of the normalization brick (`library_version`, `outlier_limit`, `saturation_limit`, `min_images`); absent in manifests written before it. Optional key. Changing the limits recomputes nothing. |
| `channel_identity` | The limits of the channel-identity check run on the table (§14.1): `ChannelIdentitySettings.to_dict()` (`outlier_limit`, `min_reference`, `mad_floor`, `min_log_difference`, `swap_limit`, `swap_ratio`, `partner_ratio`, all required when present); null or absent in manifests written before it. Optional key: the manifest version does not change. Changing the limits recomputes nothing. |
| `narrow_histogram_ranges` | The channel planes whose range check found the central range too narrow (§4), sorted by plane and channel: `relative_path`, `plane_id`, `channel` and the fields of the normalization brick's `RangeCheck` (`histogram_range`, `central_percentiles`, `central_bins`, `robust_quantiles`, `robust_limits`, `outside_robust_count`, `robust_central_bins`, `min_central_bins`). Empty when there are none; absent in manifests written before follow-up P6. Optional key: the manifest stays version 1. |
| `conversion_record_sha256` | SHA-256 of the conversion record (`.morphobricks-conversion.json`) in the experiment root when the experiment is a tree of named-axis copies (design v0.4 §5, §17); null otherwise. `RunInputs` checks it (owner decision of 2 November 2026, plan question 59): a record changed or gone since the run is refused, as for `calibration_sha256`. A changed record changes no row (the copies are checked by their fingerprints like any image). |
| `calibration_sha256` | SHA-256 of the output folder's `calibration.csv` as the run read it (§9.3); null when there was none (or absent in manifests written before PP-8). `RunInputs` checks it. |
| `experiment_description` | Always present (v0.5.1: every run is described, §0; experiment-v1 §4.5a, §4.6, §9.3). An object: `sha256` (of the exact bytes the run read: provenance, never a cache key), `schema_version`, `section_digests` (`conditions`, `channels`, `repeats`, `masks`), `units` (one object per declared unit: its repeat, condition and technical repeat IDs and `image_type_header_verified`, false for a format whose header states no colour interpretation and for a unit whose header was overridden; only for a unit declared `rgb` with `rgb_header_override = true` whose TIFF header does not say RGB and whose images all have three interleaved samples (Spec experiment-v1 §4.4b, amendment RH1), `image_type_header_overridden` (true) and `header_photometric` (the header's photometric interpretation, for example `minisblack`; distinct ones joined by `, `); for every unit declared `rgb`, `rgb_header_override`: the declared key (`true` or `false`, absent in the file is `false`; owner answers of 14 November 2026: declared and effective are recorded separately); for a unit that declares it `true`, `rgb_header_override_images`: a list sorted by `image` (relative path) of objects `image`, `override` (`used`: it lifted a header that does not say RGB; `unused`: it did not) and `header_photometric` (that image's own header colour interpretation, kept as evidence; null for a format that states none), an `unused` item adding `unused_reason` (`header_already_rgb`: header-verified; `no_colour_interpretation`, for example OME-Zarr: not header-verified). Homogeneity (`R_UNIT_NOT_HOMOGENEOUS`) compares the effective image type, so images of an overridden unit that differ only in colour interpretation are one layout; `R_RGB_OVERRIDE_NOT_THREE_SAMPLES` is reported before it. These keys are additive (optional on read, checked when present): the manifest version stays 5; and, only for a unit that has such images, `images_without_header_channel_names`: a list sorted by `image` (relative path) of objects `image`, `header_names` (`none` when the header names no channel, `partial` when only some) and `channel_positions` (the zero-based C positions without a header name). Such an image passes the header checks, needs no evidence and the confirmed layout is used (§4.3); it is listed, not warned about, and the key is omitted for a unit whose images are all fully named; RGB units list nothing), `masks` (per declared mask ID: `source_type`, `representation`, `status`, `images_with_masks`, `images` and each file with its fingerprint and its own `status`) and `records` (the lines of `morphobricks.records.jsonl`, copied unchanged). A mask file is `checked` only when the mask step recorded it in `mask_representation.json` with the same fingerprint, representation and source type; otherwise `not yet checked`. Each unit record also lists its `channels` in source order; in a fast-mode description (Spec ADR 0006, `channel_layout`) every unit record adds `channels_from` (`experiment` or `unit`, as published) and a unit with `channels_from = "experiment"` adds `fast_layout_images`: one object per checked image, sorted by `path`, with `path`, `fov_key` (an object: `condition`, `biological_repeat`, `technical_repeat`, `position_id`), `header_verified` (true exactly when every channel's evidence is `header`) and `channels`, one object per index of the list: `c`, `channel`, `header_name` (as read, never normalised: `""` for a stated empty name, null when the format states none) and `evidence` (`header`, or `statement`: no name, accepted under the user's statement); additive keys, checked when present (the manifest version stays 5), and `channel_coverage` (manifest version 5, Stage 3 S3.3) classifies every biological repeat for every declared channel ID: `status` `full` (every unit of the repeat lists it), `partial` (some do; also the warning `partial_channel_coverage`) or `unavailable`, with `units_with`, `units` and the units `lacking` it (`condition/technical repeat` IDs). A run refuses units of different image types (`R_HETEROGENEOUS_LAYOUT_NOT_ENABLED`); units with different channel lists are accepted. |
| `projection` | Only for a run on a tree of Z-projected copies (the key is left out otherwise, so a run without projection is byte for byte as before; issue #176, run side). An object with exactly `method` (`max`, `mean` or `sum`), `z_range` (`[start, stop]` requested for the experiment, or null for every slice), `conversion_record` (`{file, sha256}`: the record these facts come from, the same SHA-256 as `conversion_record_sha256`), `source_root`, `images` (one per analysed image, by path: `relative_path`, `source_relative_path`, `source_content_sha256`, `source_z_size`, `z_range` (the slices used), `source_dtype`, `output_dtype`, `dtype_rule`, `rounding`, `significant_bits_dropped`), `masks_paired` (the masks paired with projected images, sorted) and `warnings` (`["masks_on_projected_images"]` exactly when `masks_paired` is not empty). A reader that knows nothing of the key ignores it, but it also refuses the conversion record the run names (version 3 or later), so projected copies are never read by it as plain images; `manifest_version` is not changed. |
| `lab_links` | Only for a run given lab-tool links (v0.5.1, PP-2; Spec experiment-v1 §9.3; owner decision of 9 November 2026); the key is left out otherwise, so a run without links is byte for byte as before. An object with exactly the keys the user gave, recorded verbatim: `repository_url`, `commit` and `experiment_path` (each non-empty text with no outer whitespace or control character; `commit` a full lowercase hexadecimal SHA of 40 or 64 characters) and, when given, `repeat_codes` (each lab-tool repeat code to a canonical biological repeat ID, written sorted by code). Input: `run --lab-links FILE` (a UTF-8 JSON object; any other key, a value of another type, an empty value or a key given twice in one object is refused before anything is read, `lab_links_invalid`), or `run_experiment(mode=RunMode(lab_links=LabLinks(...)))` (`read_lab_links`, `LabLinks`, `LabLinksError`). Nothing is trimmed, guessed or filled in. A `repository_url` holding a credential (a password, `user:password@`, in any form; a user name before `@` in a URL whose scheme is not `ssh`, `git+ssh` or `ssh+git`, because `https://TOKEN@host/...` carries a token there), a query string or a fragment (`?` or `#`) is refused (`lab_links_invalid`), and the refusal does not repeat the URL: a credential is never written to the manifest. An SSH remote's login name (`git@host:lab/db.git`, `ssh://git@host/...`) is kept. `repeat_codes` are used only when the user confirmed them for this run (`--confirm-repeat-codes`, `LabLinks(repeat_codes_confirmed=True)`, `read_lab_links(confirmed=True)`; never read from the file): equal strings are not evidence. They must name every biological repeat of the published description exactly once and no other ID (compared exactly: a source folder or display name is not an ID). Each refusal is a run error reported together, before any pixel is read: `lab_links_repeat_codes_not_confirmed`, `lab_links_repeat_unknown`, `lab_links_repeat_twice`, `lab_links_repeat_missing`. **Provenance only:** the links are never written to `morphobricks.toml` (`labchronicle`, `provenance` and `source_database` stay `E_FORBIDDEN_FIELD`, `i99`), and they are in no section digest, description SHA-256, row key, staging key, mask-step check, design, table or report: a run with, without or with changed links publishes the same files apart from this key, and so the manifest's own SHA-256 (which the report records, and which later bricks that pin the manifest see change). Not a recorded input (§9.2): a run not given links records none. The reader checks the entry as the input is checked; a malformed one makes the manifest malformed. |
| `image_qc` | The image QC table written with the run (§19): an object with exactly `file` (`"image_qc.csv"`), `sha256` (of the table), `qc_version` (`"1"`) and `settings` (`qc_version`, `correlation_lags` `[1, 4, 16]`, `tile_grid` `[8, 8]`, `entropy_bins` `64`). Written by every run of this version; absent in manifests written before it (optional on read, checked when present: anything else makes the manifest malformed; the manifest version stays 5). The next run reuses earlier rows only when the table has this SHA-256, the same `qc_version` and settings, and a row for each channel of each histogram row; otherwise every row is recomputed, with the reason (§19). `RunInputs` does not read or check the table. |
| Older versions | Refused (v0.5.1, see `manifest_version`): no row of an older run is reused and nothing is taken from it; the next run reads every image again. |
| Row reuse | A file's rows are reused only when its row key is the earlier run's (`fingerprint.RowKey`, one rule for "the same file, read the same way", audit D5): its fingerprint (`fingerprint.Fingerprint`: size and modification time, and for an OME-Zarr image the inventory digest), labels, axes, roles, `singleton_roles`, `axes_from` and the path it is read from (so moving or converting the experiment recomputes it), and the settings that change rows (bins, histogram range, robust quantiles, range-check limit; and, for an image whose planes are read in blocks, the memory limit, which sets the blocks the image QC metrics depend on, §19). **The identity rule is the fingerprint, which is metadata** (design v0.4 §1): its known blind spot is a file rewritten with the same size whose modification time (for an OME-Zarr image, every changed file's) is set back to its earlier value; its earlier rows are then reused, its staged rows resumed, and `RunInputs` accepts it. |
| Content verification (on by default, owner 7 November 2026, #159; opt-in before) | Every run does it unless it opts out with `RunMode(name, verify_content=False)` (`run_experiment(mode=...)`, CLI `--no-verify-content`, the notebook's "Verify each image's content" box unticked); the opt-out is recorded as `settings.verify_content: false`. It closes that blind spot: each analysed image's `content_sha256` is taken after its fingerprint and recorded; it is part of the row key, so an earlier or staged row is reused only for the same content; an image read must still have it after its pixels are read (else `source_changed_during_run`); every recorded image (reused, resumed or read; an OME-Zarr image by the same store digest) is hashed once more as the last step before the commit, after the generation is staged, flushed and recorded and right before the manifest's rename (`staged_generation(before_commit=...)`, review of rc5), and a mismatch is `source_changed_during_run`: the publication folder is discarded and the earlier generation is intact, as for a fingerprint change; an image that cannot be hashed is `unreadable_planes`; and `RunInputs.open` of such a run checks every image's content SHA-256 once (reads after `open` check the fingerprint). It is a recorded input (`settings.verify_content`): a run given none keeps the recorded choice (true without one: a first run, or a version 1 manifest); turning it on or off recomputes every row ("content verification (verify_content) was turned on/off"), is reported as the change `verify_content`, and discards staged rows of the other mode with that reason. It reads every analysed image whole twice more per run (when its entry is made and before the commit), once more for each image read, and once for each `RunInputs.open`; the cost is in the pull request that made it the default (about 3 seconds per 1.5 GB read, measured at 1 GB/s of SHA-256). A run that verifies writes manifest version 3. Masks are hashed too (issue #160): each mask's content SHA-256 is taken once the header checks pass and again as the last step before the commit, and a mismatch (or a mask that cannot be hashed) is `mask_changed_during_run`; it is not recorded in the manifest (no mask has rows to reuse), so the manifest's mask entries are unchanged. Not covered: a change between the header read and the first hashing with its metadata kept (the rows are of the hashed content, the header may be of the earlier one); and a change in the moment between the last hashing and the rename, or after the commit (`RunInputs.open` hashes again). |
| Removed keys | `settings.convert_tiffs`, `settings.conversion` and `files[].ome_zarr_path` were written by v0.3 runs that converted TIFF images to OME-Zarr in the output folder (removed in v0.4.0, §10); a manifest that holds them is still read, and they are ignored. The copies such a run left in `<output>/ome_zarr` are not touched. |
| Flags | `missing_as_singleton`, `verify_content`, `read_in_blocks`, `channel_names_trimmed` and `channels_from_rgb_samples` are JSON booleans (`true`/`false`); any other value (`"false"`, `0`) makes the manifest malformed, never read as true or false, and `write_manifest` refuses to write one (`ValueError`), so a written manifest always reads back. |
| `versions` | Versions of `morphobricks-preprocessing`, `morphobricks-normalization` (which computes the histograms and writes the CSV; absent in manifests written before it), `numpy`, `tifffile` and Python. Information only: a different version never causes a rebuild. |

Channel names and significant bits come from OME metadata. Significant bits are for
information only; channel names must agree between images at each channel number (§2), and
later bricks that name channels should check their names against `channel_names` when it
is not empty.

### 9.1 Reading a clean run (`RunInputs`, v0.4.0)

Bricks read a run's files only through `RunInputs.open(output_folder, experiment_root=None,
masks_root=None)` (design v0.4 §6). It refuses (`RunInputsError`) a version 1 manifest, a
`plane_histograms.csv`, `experiment_design.csv` or `calibration.csv` whose SHA-256 is not the
manifest's (also a `calibration.csv` added since the run), the conversion record of a run
on named-axis copies whose SHA-256 is not the manifest's `conversion_record_sha256` (changed
or gone since the run; §17), a conversion record of another version than 4 (convert
again), a copy of a recorded image whose fingerprint or SHA-256 is not the record's (or that
the record does not list; PP-11c, issue #87), a manifest whose `projection` is not exactly what the record gives for the recorded images and masks (or that has none for projected copies, or a `projection` without a conversion record: projected copies are never read as plain images; issue #176), a run whose experiment (as recorded or
given), named-axis copies or output folder is inside its masks folder, or whose masks folder
is inside its experiment (§15's folder rules, audit B1: a run recorded before them is
refused as a new one would be), an image or mask changed or gone since the run, and a mask recorded without axes (a v0.4.0rc1 run wrote `axes: ""` for a
mask whose header puts X before Y; the message says to save the mask again with
`numpy.swapaxes` and run again, never "changed or missing"); every read checks the file's
fingerprint again (metadata: a rewrite of the same size with its time set back is not seen;
for a run with content verification, §9, `open` also checks every image's `content_sha256`).
The copies' SHA-256 is checked once, when the run is opened, for every
image the run recorded (`conversion_check`): each copy is read whole then, so opening a
run on named-axis copies costs one read of those copies (the excluded images' copies are
not read). Checking on every read instead would read a copy whole for each of its planes.
Reads after `open` check the fingerprint, as for any image. `RunInputs.projection` is the run's `RunProjection` (`method`, `z_range`, `conversion_record`, `source_root`, `images` and `image(relative_path)` with each image's slices used, types, rule and source SHA-256, `masks_paired`, `warnings`), or None for a run on images that are not projected copies, so a brick can carry the projection into its own records without reading the conversion record.

It also refuses an experiment holding files the run neither recorded nor excluded
(`UnrecordedFilesError`, a `RunInputsError`, PP-8b): images that preprocessing's own
discovery (`discover_experiment`) would analyse, and masks it would pair with a recorded
image (masks folder by `find_masks`'s pairing; OME-Zarr label images of recorded images),
less those the run recorded and the images it excluded. Its `files` (`UnrecordedFiles`:
`images`, `masks`) names them, and the message says to run preprocessing again on the
experiment as it is, or exclude the images. Files discovery ignores or refuses (non-image
and system files, images outside a leaf folder or below the technical repeat, masks that
pair with no image or belong to an excluded image) do not count. `open` always runs this
check: it lists folders and reads OME-Zarr label metadata, never a pixel or a TIFF header,
so a brick never leaves out an image added since the run and never discovers files itself.
Label images are never skipped (owner, 2 November 2026): a run holding OME-Zarr images is
refused (`RunInputsError`) without the `zarr` extra, naming `morphobricks-preprocessing[zarr]`,
and so is a recorded OME-Zarr image whose `labels` group cannot be read (the message names
it). A `masks_root` given for a run that recorded no masks folder makes every mask there
paired with a recorded image unrecorded; the message then says to run preprocessing again
with the masks folder (`--masks`).
`unrecorded_files(manifest, experiment_root, masks_root, output_folder=None)` gives the same
list without raising (it raises `ZarrUnavailableError` or `NgffError` for those two cases).
It gives:

- `fovs()`: `Fov` records with the FOV key, `plane_id`s, axes, roles, `axes_from`, channel
  names, dtype, `significant_bits`, and the Z, T and C counts, from the manifest and the
  table, never parsed;
- `pixel_sizes(fov)`: value, unit and source (`header`, `stated` or `pixels`) per role, or
  unknown (no value, no source). A header size is recorded only with a supported unit (a
  length unit of decision D-a2, or a time unit): one without a unit or in another unit
  (OME `pixel`, `inch`) is unknown, never a value of no unit (audit I3, plan §12 question
  72; named by `unsupported_pixel_size_unit`);
- `read_plane(fov, z, t)`, a `(C, Y, X)` array of the stored values, and `read_image(fov)`, a
  lazy `(T, Z, C, Y, X)` `LazyImage`. Both are in canonical order: an image whose own header
  names X before Y (for example `ZXY`) is returned Y before X, as the run read it; nothing
  else is reordered. The memory limit of `read_plane` (default 1 GiB) counts only reading
  the plane, not the histogram arrays a run needs (audit I2), and it counts reading **every
  channel** (issue #344): the `(C, Y, X)` result is allocated once and filled straight from
  the decoded pages, a page holding several channels (interleaved RGB, planar samples) is
  decoded once for all of them, and only one page is held at a time. Before any pixel is
  read, `read_plane` raises `PlaneMemoryError` when the bound of that whole read
  (`plane_channels.xyc_plane_memory`) is over the limit: for TIFF the result, the decoded
  page, the encoded bytes tifffile holds twice while it decodes the page (each page read
  sized from its own byte counts), per decoding
  thread one encoded and five decoded strips or tiles and the codec's state, and the parsed
  header; for OME-Zarr the result, every chunk the plane touches three times and the plane
  zarr returns. The bound depends on the machine: it counts tifffile's decoding threads,
  which tifffile sets from the CPU count (or `TIFFFILE_NUM_THREADS`), so one plane can be
  accepted on one computer and refused at the same limit on another. The bound is measured (tracemalloc, and peak RSS on Linux) for deflate,
  deflate with a predictor, LZMA and uncompressed TIFF and for OME-Zarr; a TIFF stored with any
  other compression is refused from its header before any pixel is read
  (`CompressionNotBoundedError`, *Codecs* below, #356). The values are
  bitwise those of reading each channel alone;
- `read_window(fov, *, t, z, bounds, channels, level=0, memory_limit=...)` (issue #179, owner
  decisions of 14 November 2026): an exact window of one plane as a `(C, h, w)` array of the
  stored values, in the stored pixel type, bitwise the same as that slice of `read_plane`.
  `bounds = (y0, y1, x0, x1)` are half-open `pixel_edge` bounds in the plane frame (Spec ADR
  0004), Y before X whatever the stored order; `channels` are canonical channel IDs, read in the
  order given, chosen before anything is decoded. Reading is aligned on the stored pieces (TIFF
  strips or tiles, OME-Zarr chunks) and touches only those the window intersects; only the
  window is returned. Channels that share stored pieces (TIFF samples stored interleaved, such
  as contiguous RGB strips or tiles, and OME-Zarr chunks longer than 1 along C) are read
  together: each piece the window intersects is decoded once for all of them (#294); channels
  stored apart (a page per channel, planar samples) are read each from its own pieces.
  `window_plan` gives `pieces` (intersected in one plane) and `reads` (pieces read in all). Refused, never padded, filled or clipped: bounds that are not four whole
  numbers, empty, or outside the plane (`WindowBoundsError`); a level other than the
  full-resolution level 0, the declared policy (`WindowLevelError`); no channel or a channel
  twice (`WindowChannelError`; an ID the image lacks is `ChannelNotFoundError`); TIFF pages the
  block reader cannot split (subsampled colour other than JPEG's, an NDPI JPEG header, a plane whose axes are not
  the page's rows and columns: `WindowLayoutError`, never read whole instead); a TIFF stored with
  a compression whose decoding memory is not measured (`WindowCompressionError`, a
  `WindowLayoutError` and a `CompressionNotBoundedError`, *Codecs* below, #356); a window whose
  working memory, estimated from the header (`windows.window_plan`), is over the limit
  (`WindowMemoryError`): the result, then for TIFF one strip or tile at a time, written straight
  into it, and for OME-Zarr every chunk the window intersects and zarr's copy of the channels
  read together. A TIFF stored compressed in one strip or tile is decoded whole, and the
  estimate counts it. The image's fingerprint is checked as for `read_plane`; no content digest
  is computed (a run with `verify_content` checks content once, at `open`). A caller records,
  with what it computes from a window, the image's recorded fingerprint (`size`, `mtime_ns`,
  and `inventory_sha256` for OME-Zarr), `level`, `bounds`, `channels`, `t` and `z`. It does not
  replace `read_plane` (yet);
- `window_reader(fov, *, cache_bytes, memory_limit=...)` (issue #304, opt-in): a
  `run_inputs_window.RunWindowReader` on one position whose `read_window(*, t, z, bounds,
  channels, level=0)` takes `read_window`'s arguments, checks and refusals and gives bitwise the
  same windows, but keeps the decoded compressed TIFF strips or tiles and OME-Zarr chunks in a
  bounded least-recently-used cache (`block_cache.BlockCache`) shared by every window it reads,
  so that a grid with margins decodes each piece about once per plane; `stats()` gives the
  decodes, hits, evictions and bytes held. The cache holds at most `cache_bytes` (a whole number
  above 0, below `memory_limit`; a piece larger is used once and not kept), counts in each
  window's memory estimate (a window whose own working memory and `cache_bytes` together are
  over `memory_limit` is `WindowMemoryError`), and is safe to share between threads (a piece
  asked by several at once is decoded once, and every thread that waited gets that decode, also
  when the piece is not kept; a kept piece is immutable, its bytes in a read-only buffer; the
  cache is per process, never shared). Pieces are kept under the image's path, the
  fingerprint the run recorded for it, the level and the page and piece (TIFF) or chunk
  (OME-Zarr). The fingerprint is checked before each window (`RunInputsError`, as for
  `read_window`) and again after it: an image changed during a read is refused
  (`windows.WindowSourceChangedError`) and the cache emptied, never served. Uncompressed TIFF
  pieces are read from the file as without a cache, never cached. There is no hidden or global
  cache: `read_window` keeps none. The same reader for a file outside a run is
  `window_reader.WindowReader(path, mapping, *, cache_bytes, memory_limit=..., fingerprint=None)`
  (its `read_window(*, bounds, channels, z=None, t=None, level=0)` and `window_plan(*, bounds,
  channels, z=None, t=None, level=0)`).
  `RunWindowReader.window_plan(*, t, z, bounds, channels, level=0)` (Unreleased) is the window's
  `windows.WindowPlan` from the header, with `read_window`'s checks, its `memory_estimate`
  counting `cache_bytes`. A plan is the plan of the plane read (#356): a compressed TIFF window's
  pieces are sized from the headers of the very pages and pieces the read decodes (its channels'
  pages at its T and Z), never from the first page's; `windows.window_plan` and
  `WindowReader.window_plan` given neither `z` nor `t` plan a window of any plane, sized from
  the series' largest piece (so does `mask_window_plan`, which takes no plane). A window's `memory_estimate` (Unreleased) is an upper bound of the
  read's peak, checked by measurement for the codecs of `codec_bounds.TIFF_BOUNDED` and the
  OME-Zarr codecs of `codec_bounds.ZARR_MEASURED` (a TIFF stored with any other compression is
  refused, *Codecs* below): the result, the pieces, the decode's buffers, the codec's state, the parsed header and,
  through a cache, the frozen copy of a kept piece. Both readers (Unreleased) have `close()`, `closed` and are context
  managers: closing drops the cache at once (also when the `with` block fails), a later window or
  plan is `window_reader.WindowReaderClosedError`, a window being read meanwhile finishes bitwise
  and keeps nothing; a reader holds no open file between windows. One reader per image is meant
  to be shared by every thread reading it (`docs/design/lazy-windows.md`).
  `window_cache_plan(fov, *, window, margin, channels)` (Unreleased; `window_reader.cache_plan`
  for a file) returns a `window_reader.CachePlan` from the header: `cache_bytes`, the cache a
  grid of `window` px windows with `margin` px margins read row by row needs to decode each
  piece once (`piece_rows` = `ceil((window + 2 × margin) / piece) + 1` rows of `per_row` pieces
  of `piece_nbytes`, for each of `groups` of channels stored apart; 0 for uncompressed TIFF
  pieces), a figure for the caller to cap with its own budget. Many windows of one reader in
  threads (Unreleased, `window_map`, design note §5): `map_plan(reader, windows, *, channels,
  workers, t=None, z=None, max_in_flight=None)` checks every window from the header (no pixel
  read; `t` and `z` required for a `RunWindowReader`) and returns a `MapPlan`: the windows'
  checked bounds, `workers`, `max_in_flight` (at least `workers`, default `2 × workers`),
  `cache_bytes`, `window_bytes` (the largest window working memory, cache excluded),
  `in_flight_bytes` (the `max_in_flight` largest added), `held_bytes` (the largest window as
  delivered, the one the caller holds while the next is read) and `memory_estimate`
  (`in_flight_bytes + held_bytes + cache_bytes`; `fn`'s own memory and the results the caller
  keeps beyond the last one are not counted).
  `map_windows(plan, fn, *, memory_budget)` refuses a plan whose `memory_estimate` is over
  `memory_budget` (`WindowMemoryError`) before any pixel is read, then returns a `WindowMap`
  iterator of `fn(bounds, window)` called in `workers` threads, in the plan's window order
  whatever finishes first, with at most `max_in_flight` windows submitted and not yet
  delivered, plus the one the caller holds; `iter_windows(plan, *, memory_budget)` yields `(bounds,
  window)` the same way. The first error in window order is re-raised, its type unchanged, with a
  note naming the window, after the results before it; an error, `close()`, leaving its `with`
  block or dropping it cancels the windows not started and waits for the running ones, leaving
  no thread; the reader is never closed by it. `summary()` gives a `MapSummary` (windows,
  delivered, finished, workers, max_in_flight) for the caller's record; workers never change a
  result and never enter an identity. Invalid `workers` or `max_in_flight` are refused, never
  adjusted; nothing falls back to processes or retries;
- `plane_shape(fov)`: the `(height, width)` of the position's planes, Y before X whatever the
  stored order (the frame of `read_window`'s bounds, the last two sizes of `read_plane`), from the
  header and the recorded axes only: no pixel is read and no content digest computed; the
  fingerprint is checked as for every read;
- *Codecs* (#356, owner decision of 18 November 2026): the readers that plan their memory from
  stated upper bounds before any pixel is read (window reads and their plans, `read_window`,
  `window_reader`, `window_cache_plan`, `map_plan` and the mask windows below; `read_plane`'s
  read of every channel; planes over the memory limit read in blocks, in `check`, `run`,
  `convert` and a reduced preview) decode only the TIFF codecs whose bound is measured, listed in
  one place, `codec_bounds.TIFF_BOUNDED` as `(compression, predictor)`: uncompressed (1, no
  predictor), deflate (8 `ADOBE_DEFLATE` and 32946 `DEFLATE`, the same decoder in tifffile;
  without a predictor or with the horizontal predictor 2), LZMA (34925, no predictor), and,
  decoded by imagecodecs (owner decision of 18 November 2026: measured, then read), LZW (5, without a predictor or with the horizontal predictor 2) and JPEG (7) in the
  measured form only: 8-bit samples, greyscale or interleaved RGB or YCbCr, subsampled at most
  2 × 2, no NDPI header (`codec_bounds.jpeg_unmeasured`; another form is refused naming it).
  Every compressed codec is decoded by imagecodecs, a required dependency (2024.12.30 or later;
  JPEG with libjpeg-turbo 3): `deflate_decode`, `lzma_decode` and `lzw_decode` in tifffile's
  table, never the standard library's `zlib` or `lzma`; a JPEG piece by imagecodecs' compiled
  libjpeg decoder called directly (`jpeg_decode` from imagecodecs 2026.11, `jpeg8_decode`
  before), never through tifffile or a decoder that falls back to LJPEG (`ljpeg_decode`) when
  libjpeg refuses a stream: what libjpeg refuses is refused (`jpeg_piece`, second review of
  #357, F1); any other decoder, an older imagecodecs or a missing one is refused before any
  pixel is read, naming it (`codec_bounds.decoder_unmeasured`, review of #357, B2).
  Their estimates count the C library's memory, which `tracemalloc` does not see (LZW's code
  table; libjpeg's state, rows and a progressive piece's coefficients), checked against the peak
  resident memory of the read (`tests/test_codec_memory.py`); a JPEG piece whose stream declares
  more pixels than its piece, or whose markers libjpeg could read otherwise, is refused before
  it is decoded, by every read (`jpeg_stream`; a JPEG page is decoded piece by piece). A TIFF
  stored with any other compression or predictor (old-style JPEG, PackBits,
  Zstandard, WebP, JPEG 2000, JPEG XL, PNG, LERC, the floating-point predictor, ...) is refused
  there from its header, before any pixel is read: `CompressionNotBoundedError` (code
  `compression_not_bounded`; for a window `WindowCompressionError`), naming the compression and
  the fix (save the image again as a TIFF that is uncompressed or compressed with deflate, LZW
  or LZMA, tiled for a large image, or as OME-Zarr; for planes over the limit, also raising the
  limit so they are read whole); `check_planes` reports such images over the limit as the error
  `compression_not_bounded` (no longer `planes_read_in_blocks`). No bound is invented for an
  unmeasured codec. The OME-Zarr codecs the measurements cover are recorded in
  `codec_bounds.ZARR_MEASURED` (Blosc with LZ4, the default of zarr format 2 arrays, OME-NGFF
  0.4; Zstandard, the default of zarr format 3 arrays, OME-NGFF 0.5); OME-Zarr reads are not
  refused by codec. Not covered: whole planes read within the memory limit
  (`planes.read_plane` of one plane, the run's histograms, the conversion), whose estimate is not
  stated as an upper bound for any codec: they read every codec tifffile decodes, as before;
- `read_mask_window(fov, mask_type, *, t, z, bounds, level=0, memory_limit=...)`: an exact window
  of one mask plane as a `(h, w)` array, bitwise the same as that slice of `read_mask`, in the
  mask's stored integer (or boolean) pixel type: labels are unchanged, never relabelled, cast or
  made binary, whatever the declared representation (`instances`, `binary`, `parent_labels`).
  Bounds, level and memory are refused exactly as `read_window` refuses them (`WindowBoundsError`,
  `WindowLevelError`, `WindowLayoutError`, `WindowMemoryError`), and only the TIFF strips or tiles
  and OME-Zarr chunks the window intersects are read. `z` and `t` are whole numbers (0 for an axis
  the image lacks), as for `read_window`. The mask is refused exactly as `read_mask` refuses it:
  no recorded mask of that type, `MaskUncheckedError` (`A_MASK_UNCHECKED`) for a mask the mask
  step has not checked, a mask changed since the run or stored with other axes or another shape
  than recorded; a mask whose header names X before Y is refused as `read_mask` refuses it, and a
  1-D array stored as one page (which `read_mask` reads as a row) is refused with
  `WindowLayoutError`, never read whole. `check_labels` is applied to the window's pixels. No
  content digest is computed. `mask_window_plan(fov, mask_type, *, bounds, level=0,
  memory_limit=...)` is the same check and the `windows.WindowPlan` from the header only: `shape`
  `(h, w)`, `dtype` the mask's stored type, `pieces` and `memory_estimate` (from the mask's own
  bytes per pixel), `channels` empty;
- `read_objects(selection, mask_id, t, z)` (Spec ADR 0005 BM2, BM6; contract version 3 below):
  the one helper that reads a resolved selection's `(mask_id, "objects")` use as a new `int32`
  array of the plane's shape, applying the recorded conversion, whole planes only (no window
  argument). An `instances` mask without a conversion gives its stored labels, checked by
  `check_labels`, never relabelled (an `instances` mask holding 0 and 1 is one object).
  `conversion = "connected_components"` gives `masks.binary.label_binary` of the stored plane
  (also `morphobricks_preprocessing.label_binary`): 8-connected (`CONNECTIVITY = 8`, the 3 × 3
  `STRUCTURE` of ones that the mask step's piece counter imports), numbered 1 to n with no gap
  in row-major order of each component's first pixel; a plane with no foreground gives zero
  objects, an all-foreground plane one object. `label_binary(mask, *, name="the mask")` takes a
  plain 2-D `numpy.ndarray` only (anything else, a `(1, Y, X)` stack included, is a
  `ValueError`; nothing is cast, thresholded or squeezed, the input is not changed) and first
  calls `mask_rules.check_binary_values` on it, the per-plane **read guard** of the mask step's
  file-wide rule: a float, complex or other non-integer dtype, a negative value or two distinct
  positive values is `MaskContradictsRepresentationError` (`R_MASK_CONTRADICTS_REPRESENTATION`,
  the mask step's message). More components than `LARGEST_LABEL` is a `ValueError` naming the
  count and the limit (counted before the `int32` output is filled; never wrapped, widened or
  truncated). A mask ID with no `objects` use in the selection (a mask used only as a region
  is read with `read_mask` or `read_mask_window` and never labelled), a `parent_labels` mask or
  a conversion that does not fit the declared representation is a `ValueError`; the mask is
  otherwise refused exactly as `read_mask` refuses it;
- `objects_read_plan(selection, mask_id)` (issue #316): what one `read_objects` plane costs, from
  the mask's header only (no pixel read, no content digest), refused exactly as `read_objects`
  refuses the selection and the mask. An `ObjectsReadPlan` (`masks.objects`, also
  `morphobricks_preprocessing.ObjectsReadPlan`): `shape` `(h, w)`, `stored_dtype`, `dtype`
  (`int32`), `conversion`, `stored_bytes` (the stored plane), `result_bytes` (the `int32` result,
  4 bytes per pixel whatever the stored type: 4 times `stored_bytes` for a `uint8` mask),
  `conversion_bytes` and `memory_estimate` = `stored_bytes + result_bytes + conversion_bytes`,
  the peak working memory of the read (a `uint8` `instances` mask: 5 times `stored_bytes`; an
  `int32` one 8 bytes per pixel, the stored plane and the copy returned), counting the decoded
  plane only: a compressed file's encoded pieces held while they are decoded (up to the plane's
  compressed size, up to `stored_bytes` for a mask that does not compress), an OME-Zarr chunk
  that also holds other planes and a few kilobytes of header are not counted. Without a
  conversion `conversion_bytes` is 0. With `connected_components` it is
  `masks.binary.labelling_bytes(shape, stored_dtype)`, an upper bound for every plane of that
  shape and type of what `label_binary` allocates beside its input and its result: the larger of
  its value check (`numpy.unique`'s sorted copy, the stored bytes per pixel plus 2) and its
  labelling (the boolean foreground, 1 byte per pixel; SciPy's label table, 8-byte entries for at
  most one provisional label per 2 × 2 block plus one line, counted three times for its
  doublings; its two line buffers; and the `int64` labels of a plane that could hold more
  components than the `int32` limit). A region read's estimate stays `mask_window_plan`'s, from the stored type;
- `mask_types()` and `read_mask(fov, type, z=None, t=None)`: the recorded pairing only, read
  as stored through the recorded `axes` and `roles` by preprocessing's reader with
  `check_labels`; a mask changed since the run is refused; a mask recorded `shared: true`
  (a 2-D mask shared by every plane, written by the release candidates up to v0.4.0rc6;
  removed by the owner decision of 5 November 2026) is refused at `open` with a re-run
  hint, never trusted;
- `z` and `t` are whole numbers (Python or NumPy integers; `read_mask` also takes None); any
  other value, such as a `plane_id`, is a `TypeError` naming the expected arguments
  (v0.4.0, plan §12 question 54). A `z` or `t` outside the image (`Fov.z_count`,
  `Fov.t_count`) is an `IndexError` for both reads (audit I5).
  There is no read by `plane_id`: a row gives its `z` and `t`;
- `histograms()` (the table as read when opened). There is no `design()` (removed in v0.5.1,
  SWEEP-PP; Spec ADR 0002 AR11 (3)): `experiment_design.csv` is an output only, its SHA-256
  checked at `open` against the manifest's `experiment_design_sha256` but never read back; a
  brick takes the repeat groups from the description (`Fov.repeat_group`,
  `resolve_request`);
- **semantics of a published description** (Stage 1 PR 12; every run has one since v0.5.1,
  §0: an output folder without a published `morphobricks.toml` is refused at `open` with
  `DescriptionRequiredError`, `A_DESCRIPTION_REQUIRED`, and an older manifest with
  `ManifestFormatError`, "run preprocessing again"). `Fov.repeat_group`, `Fov.condition_id`, `Fov.technical_repeat_id` (the canonical
  IDs, added beside the unchanged `Fov.key`, whose condition, repeat and technical repeat stay
  the source folder labels: owner decision Q8, experiment-v1 §3.4), `Fov.channel_ids` (the
  canonical channel ID of each C index, as the unit declares them; a unit whose channel count
  is not the image's, or folders that name no declared unit, is a `RunInputsError`: nothing is
  guessed) and `Fov.image_type` (v0.5.1, PP-1: the unit's declared type, `"rgb"` or
  `"channels"` for a unit that declares none, experiment-v1 §4.4b; from the published
  description only, never from a header, and never None on a `Fov` of `fovs()`). Each position
  carries its own unit's type, through `FovIdentity` (`fov_identity`), the one source of
  `Fov.image_type` and `FovSelection.image_type` alike. There is no run-wide image type:
  `RunInputs.image_type()` is removed (v0.5.1, PP-1; it returned the manifest's run-level
  `image_type`, null on every described run, which would hide per-unit types). The manifest's
  `image_type` (§9, §13) stays a record of the command line's review, not an input of a brick. `channel_index(fov, channel_id)` gives the C index, `ChannelNotFoundError` for a
  channel the image lacks (never a default). `references()` is the condition reference graph
  (condition ID to the ID it references, or `None`); `mask_semantics()` maps mask ID to
  `MaskSemantics(source_type, representation, source_channels)`; `mask_check_status(mask_id,
  fov)` is `checked` or `not yet checked`. `description()` reads the published
  `morphobricks.toml` again and returns it only if it is the file whose SHA-256 the run
  recorded (`RunInputsError` otherwise, also at `open`); a draft, a file not named
  `morphobricks.toml` (`description_consume.read_published_description`) or a published file
  with an unconfirmed repeat is refused with `DraftRefusedError` (`A_DRAFT_REFUSED`). `read_mask`
  of a mask file the mask step has not checked (§15; `mask_representation.json`) is refused with
  `MaskUncheckedError` (`A_MASK_UNCHECKED`); the error classes carry the Spec code as `code`. `ChannelNotFoundError` is `A_SCOPE_INCOMPLETE` (Stage 2, owner answer 2): one rule and one code for a channel that no image has and for one that some images lack, and its message names the image. `section_digests()` returns a copy of the manifest's `experiment_description.section_digests` (SHA-256 of the `conditions`, `channels`, `repeats` and `masks` sections; the published file is not read again).

- **The request resolver (resolver contract version 3, `RESOLVER_VERSION`; MorphoBricks
  architecture decision of 10 November 2026; version 2 since parent-label masks, Spec ADR 0002
  "Resolver contract version 2" and experiment-v1 §4.5b at MorphoBricks-Spec `69a58b3`; version
  3 since the conversion record, Spec ADR 0002 "Resolver contract version 3" and ADR 0005 at
  `bf68d30`).** `resolve_request(request)` is the one
  interpreter of a described run for an analysis, on every route (a brick's direct run and a run
  through MorphoFeature alike; `run_inputs_request`, `run_inputs_resolve`). A `Request` names
  canonical channel IDs, masks by ID with a role (`objects` or `region`) and, for a `binary`
  mask used as objects, the explicit `binary_mask_policy` (`connected_components`,
  experiment-v1 §4.5c), the planes (`plane`, one selection per (t, z), or `volume`, every Z of
  one t), the consumer's `Capabilities` (`rgb_support`, `volume_support`,
  `parent_labels_support`, all False by default) and the explicit `device` (`cpu` or `cuda`; recorded, never chosen here). It returns
  an immutable, picklable `ResolvedRequest`: one `FovSelection` per position (the unchanged key,
  `repeat_group`, `condition_id`, `technical_repeat_id`, the unit's declared `image_type`
  (`rgb` or `channels`, the position's `Fov.image_type`), the C
  index of every requested channel **in that image** (`channel_indices`, `index_of`), the masks
  as `MaskSelection(mask_id, source_type, representation, role, check_status, conversion)` and
  the `PlaneSelection`s to read), the `refusals`, the `coverage` of every biological repeat per
  channel and mask (`full`, `partial`, `unavailable`; experiment-v1 §8) and the `Provenance`
  (the resolver version, the description's SHA-256 and section digests as the manifest recorded
  them, the request, and `mask_conversions`). A refused request has no selections: no subset is
  taken. **Version 3 (Spec ADR 0005):** a `MaskRequest` is identified by `(mask_id, role)`, and
  a consumer sends one entry per use (each object set, each region or fraction use, a fraction
  being a `region`) without merging them; a mask ID may appear in any number of entries.
  Entries of one `(mask_id, role)` with the same conversion collapse into one `MaskSelection`,
  so `FovSelection.masks` holds one per `(mask_id, role)` in the order of its first entry (the
  `objects` one carries the conversion, the `region` one none); `A_SCOPE_INCOMPLETE`,
  `A_MASK_UNCHECKED` and the coverage are computed once per mask ID. The same mask ID requested
  as `objects` with two different conversions (a policy and none count as two) is the one
  `ValueError` `{mask_id!r} is requested as objects with two different conversions ({a!r} and
  {b!r}); one run converts a mask at most one way`, `a` and `b` in request order (version 2's
  "a mask ID is requested twice" is gone). `Provenance.mask_conversions` holds one
  `MaskConversion(mask_id, conversion, connectivity, labeller)` per mask ID whose selection has
  a conversion, in request order (none for a refused request): `connected_components` with
  `connectivity` 8 and `labeller` `morphobricks_preprocessing.RunInputs.read_objects`, or
  `parent_labels_as_area` with both None. `MaskConversion.as_record()` is the record
  `{"mask_id", "conversion", "connectivity", "labeller"}` in that key order, which every consumer
  copies as is under `mask_conversions` in its run record. A `binary_mask_policy` on a position
  with more than one Z plane is `A_VOLUME_NOT_SUPPORTED` (one refusal per such position, naming
  it), for a `plane` or a `volume` request, with or without `volume_support`; the labeller
  labels 2-D planes only. Consumers accept exactly the resolver versions they support (`{3}`),
  checked with normalization's `contracts.refuse_resolver_version` (Spec ADR 0005 BM10). Every
  applicable refusal is reported, each a `Refusal(code, subject, message, files)`:
  `A_SCOPE_INCOMPLETE` for a channel or mask some or all images lack ("present in k of n
  images", k may be 0; an undeclared mask ID is 0 of n), `A_MASK_UNCHECKED` ("k of n
  files"), `A_MASK_NOT_INSTANCES` (a `binary` mask as objects without the policy, and a
  `parent_labels` mask as objects always, with or without `parent_labels_support`: it is never
  split, relabelled or converted, and the message offers no policy; built by
  `mask_rules.refuse_not_instances`, the one constructor of the code, which
  `masks.check_object_set` also raises for an in-memory object set that is not a plain integer
  `numpy.ndarray`), `A_RGB_NOT_SUPPORTED` (`rgb` units without `rgb_support`) and
  `A_VOLUME_NOT_SUPPORTED` (`VolumeNotSupportedError`, the resolver contract's own code: a
  volume without `volume_support`, and a conversion of a Z-stack; built only by
  `run_inputs_request.volume_refusals`). `ResolvedRequest.require()` returns the selections or raises the first
  refusal's error class with every refusal in its text. The resolver reads the manifest, the
  published description and `mask_representation.json` only (both read on every call; the
  parse of the same bytes is reused, by content, so a file rewritten since is parsed again:
  PP-1): no pixel, no kernel, no brick,
  CellPatcher or provider import (a test scans its modules), and it writes nothing. It converts
  nothing: a recorded policy comes back as `MaskSelection.conversion` and in the record, and
  `read_objects` (a reading module, not the resolver) applies it on every route, so all routes
  give the same objects. A
  `parent_labels` mask in the role `region` comes back with `representation = "parent_labels"`
  and, for a consumer without `parent_labels_support`, `conversion = "parent_labels_as_area"`
  (`PARENT_LABELS_AS_AREA`): that consumer reads every non-zero pixel as inside and 0 as outside,
  records the marker with the mask ID in its run provenance or output and shows it to the user
  ("`Mito`: parent_labels used as an area; grouping not kept"); with the capability it comes back
  with no conversion and the consumer reads the groups. The
  `device` is checked for its spelling only and recorded in the provenance; the consumer refuses
  `cuda` without a GPU and never switches. A malformed request (a channel ID twice, one mask ID
  as objects with two conversions, an unknown role,
  policy, plane kind or device, a policy on a mask that is not `binary` or in a `region` role
  (on a `parent_labels` mask the message says it is never converted),
  channels or masks not a tuple of `str` IDs or of `MaskRequest`, a capability that is not a
  `bool`) is a `ValueError`, not a refusal. Every run the resolver sees is described: a run
  without a published description is refused by `open` with `A_DESCRIPTION_REQUIRED` (§0;
  `DescriptionRequiredError`, the resolver contract's other own code, MorphoBricks-Spec ADR 0002
  AR10, AR11 (1)), so there is no undescribed path.

A `RunInputs` is picklable, so worker processes can use it.
`morphobricks_preprocessing.testing.make_clean_run(folder, ...)` writes a tiny experiment of
named-axis OME-TIFFs (optionally with masks), publishes its description
(`testing.clean_description`: conditions `ctrl` and `drug`, one repeat `rep1`, channels `ch1`..,
one `instances` mask per mask type; `design=True` makes `ctrl` the reference of `drug`), and
runs preprocessing and, with masks, the mask step: a described run, for the bricks' tests
(v0.5.1; before, it was an undescribed run).
`make_clean_run(folder, description=<bytes, text or Description>)` (also `testing.make_described_run`)
writes instead the tree a published experiment description declares (`image_count` tiny images per
member, `rgb` units as RGB colour, one mask per image for each declared mask ID in `mask_types`;
`make_described_run(..., masks=IDS, mask_images=PATHS)` writes the masks for the named images only,
a declared subset as `testing.described_images(description)` lists them, for a run with partial
masks such as Spec `v09` on 2 of 6: v0.5.1, PP-1; nothing is chosen at random, and a path outside
the tree, an empty subset or a subset without masks is a `ValueError` before anything is written),
publishes the bytes as `morphobricks.toml`, runs preprocessing and the mask step, and returns
the same `CleanRun` with `description` set; `axes`, `shape`, `pixel_size`, `design` and `seed` then
raise `ValueError`. Tested for the Spec fixtures `v03 v08 v09 v13 v28 v30 v33 v34`.

**A stopped run (issue #322).** A run that preprocessing stops (for example Spec fixture `v29`,
`R_HETEROGENEOUS_LAYOUT_NOT_ENABLED`) publishes no `run_manifest.json`, and its refusal is only in
its report (§9.2), which bricks never read. `RunInputs.open` reads it for them
(`run_inputs_stopped.stopped_run_refusal`, the one reader of the report on the bricks' side), only
when the output folder holds no `run_manifest.json`, before anything else of the run is read and
before any image or mask is opened:

- the final report of a `run` step (`report_version` `"1"`, `step` `"run"`, `status`
  `"stopped"`, `provisional` false) raises `RunStoppedError`, a `RunInputsError` with
  `code = "RUN_FOLDER_STOPPED"` (the `RUN_FOLDER_*` family that Spec ADR 0007, proposed in
  MorphoBricks-Spec PR #48, gives the folder states `open` refuses; a runtime code outside
  experiment-v1 §8). Its public attribute `issues` is the stopped run's **error** issues
  (`ValidationIssue`), in the report's order, each with its `code`, `message`, `paths` (in the
  report's order) and `action` (the suggested action) exactly as the report records them:
  written back with the report's own serialiser, each is byte-equal to the report's entry.
  Nothing is sorted, trimmed, translated or filled in; warnings are not carried. The message
  starts with `RUN_FOLDER_STOPPED: ` and names the output folder, the errors' codes and the
  first error. A brick passes the refusal on unchanged (Spec ADR 0005 propagation rule) and
  never reads the report itself;
- a report that cannot be read (not UTF-8, not JSON, a key given twice, `NaN`), that is not of
  the shape report version 1 writes (`step`, `status`, `provisional`, and `issues` as objects with
  exactly `code`, `severity`, `message`, `paths`, `action`, all text but `paths`, a list of text,
  and `severity` `error` or `warning`), that is of another `report_version`, or that says the run
  stopped without recording an error raises `RunReportUnreadableError`, a `RunInputsError` with
  `code = "RUN_FOLDER_REPORT_UNREADABLE"` and the public attribute `path` (the report file, also
  named in the message). Nothing is guessed from such a file;
- no report, the report of `check` (always `stopped`), a provisional report (a run killed, or
  still reading, after its header checks) and a `written` report leave the refusal as it was:
  `RunInputsError` (`code` None), "holds no run_manifest.json: run preprocessing first".

A folder that holds a manifest is opened as before, whatever its report says: a stopped run keeps
the last good generation (§9.2), so `open` reads that generation and refuses it, if at all, with
the refusals above (stale files, `A_DRAFT_REFUSED`, ...), never with `RUN_FOLDER_STOPPED`. Both
errors are exported at the package's top level and are picklable with their attributes.

**The pinned surface (v0.5.1, PP-1).** `tests/test_brick_api_surface.py` pins, by name and type,
what the consuming bricks use: `RunInputs`'s public methods and their signatures (`open`, `fovs`,
`files`, `projection`, `pixel_sizes`, `read_plane`, `read_image`, `plane_shape`, `read_window`,
`window_reader`, `window_cache_plan`, `mask_types`, `read_mask`, `read_mask_window`, `mask_window_plan`,
`read_objects`, `objects_read_plan`,
`histograms`, `design`, `description`, `section_digests`, `images_without_header_channel_names`,
`channel_index`, `references`, `mask_semantics`, `mask_check_status`, `require_checked`,
`resolve_request`), the fields and types of `Fov`, `RunInputs`, the resolver's records (`Request`,
`MaskRequest`, `Capabilities`, `ResolvedRequest`, `FovSelection`, `MaskSelection`,
`PlaneSelection`, `Provenance`, `MaskConversion`, `Refusal`, `RepeatCoverage`), `ObjectsReadPlan` and `CleanRun`,
`RESOLVER_VERSION`, the signatures of `make_described_run`, `make_clean_run` and
`described_images`, and those of the Spec ADR 0005 helpers (`masks.binary.label_binary`,
`masks.check_object_set`, `mask_rules.refuse_not_instances`, `refuse_binary_values`,
`check_binary_values`, `run_inputs_request.volume_refusals`). A change to
any of them changes that test, this section and the CHANGELOG together.

### 9.2 Modes, recorded inputs, lock and report (v0.4.0, design v0.4 §1, §4)

| Topic | Rule |
|---|---|
| Modes | `run_experiment(mode=...)`, CLI `--mode`: `restart` (default) reuses the rows whose `RowKey` is unchanged (§9) and reads new or changed files; `full` recomputes every row and check and reuses no row. Both keep the user's confirmations (design, axis and mask-axis statements, stated calibration, image type) and publish only on success; a failed or stopped run leaves the last good run intact. `force_rebuild` and `--force-rebuild` were removed. |
| Files a run replaces or removes | Only `plane_histograms.csv`, `run_manifest.json`, `experiment_design.csv` (when the run has a design), the report files, `mask_relationships.json`, the publication folder `.publish` and the staging folder `.staging` (§9.4); never `calibration.csv` (§9.3). `mask_relationships.json` is removed by a `full` run and by a `restart` whose masks entry (§9) differs from the earlier run's (or when no v2 manifest records it). Source images, masks and anything else are never touched. |
| Recorded inputs | One output folder belongs to one experiment. An input not given is taken from the earlier v2 manifest: experiment root, axis mapping with the per-file plain-stack confirmations at their recorded fingerprints (a new or changed plain stack must be confirmed again), exclusions, masks folder and mask-axis statements (statements given are added per mask type), image type, design (`experiment_design.csv` in the output folder), bins (not across pixel types), histogram and intensity settings, `missing_as_singleton`, `verify_content`; the stated calibration is the output folder's `calibration.csv` (§9.3). A value given that differs is used and reported as a change with its effect (rows recomputed, or none); for the design, a design given whose answers (repeat group, confirmation, control) differ for folders in both, or `experiment_design.csv` edited since the earlier run wrote it; for the calibration, `calibration.csv` added, edited or removed since the earlier run read it (`calibration`, "no row is affected; the pixel sizes of N images change"). Masks are dropped only with `masks_folder=False` / `--no-masks`, which also drops the mask-axis statements recorded for the masks folder's masks (those of OME-Zarr label images are kept; before PP-10c the recorded statements stayed and the run was refused with `invalid_mask_axes`); exclusions are cleared with `exclude=[]` / `--no-exclude`. The lab-tool links (§9 `lab_links`, v0.5.1) are not a recorded input: they belong to the run they are given to, so a run not given them records none, and they are never listed as a change. A version 1 manifest gives nothing. |
| Experiment root | `run_experiment(experiment_root=None, output_folder=...)` and CLI `run --output FOLDER` use the recorded experiment root; without one, `ValueError` (API) or `experiment_not_recorded` (CLI). Another root than the recorded one is a change: every row is read again, and the recorded plain-stack confirmations are not applied there (each plain stack must be confirmed again). |
| `masks` command | Uses the recorded inputs the same way (plan §12 question 56): the experiment argument is optional when `--output` holds a v2 manifest recording it (`experiment_not_recorded` otherwise), and the masks folder, mask-axis statements and exclusions are the recorded ones unless given (`--no-exclude` clears the exclusions). A value given that differs is used for that scan and printed as a change; the manifest is not changed (the next run records only what it is given). The pairing with the recorded inputs, the scan and the write under the lock are `run.mask_step.prepare_mask_step(experiment_root, output_folder, exclude=, masks_folder=, mask_axes=)` (a `MaskStep`: the preflight, the output folder, the input changes, the blocking issues, or the `experiment_not_recorded` refusal) and `run.mask_step.describe_masks(step, thresholds=, progress=)` (a `MaskStepResult`: the report and the file written, or the `output_locked` refusal with no mask read and nothing written: the lock is taken before the first mask is read and held to the write; `ValueError` for masks whose pixels cannot be used), shared with the notebook (v0.4.0, PP-10d); the command only prints their results. |
| Axis roles | `mapping=None` uses the recorded roles; without any, the run stops with `axes_not_confirmed`. CLI `--axes` is needed for the first run of an experiment with plain stacks and for plain stacks that are new or changed; when every readable image names all its axes (`header_axes`) and the suggested mapping gives each a role, the first run uses those roles (design v0.4 §3; cross-brick test F9). `convert` always needs `--axes` (§17). |
| Lock | `.morphobricks-preprocessing.lock` in the output folder, created with `O_CREAT \| O_EXCL`, JSON `host`, `pid`, `started_at`; held by `run`, by `check` while it writes its report (and the design template), by `masks` from before its first mask read to the write of `mask_relationships.json` (a locked folder is refused before any mask is read), and by `design` and `calibrate` while they change `experiment_design.csv` and `calibration.csv` (CLI `run` also holds it to write the design template after a run stopped on the design), removed on every exit, and removed when the lock file cannot be written. A second run is refused with `output_locked`, naming the holder, and writes nothing; when the holder is a process on this computer that is no longer running (POSIX), the message says the lock looks stale and how to remove it, and the lock is kept (cross-brick test F11). `remove_stale_lock(output)` / `--remove-stale-lock` (every command) removes a lock only on that request, never one of a process still running on the same computer (checked on POSIX). Not written inside the experiment. The lock is not part of the manifest. |
| Report | `discovery_report.json` (report version 1) and `discovery_report.txt`, written on every run that holds the lock, also when it stops: `step` (`run` or `check`), `status` (`stopped` or `written`; `check` is always `stopped`), `provisional` (true for the report written after the header checks, before any pixel is read), `manifest_sha256` (the manifest published with it, `written` only), `experiment_root` and `experiment_root_changed`, `conversion` (for a tree of named-axis copies, §17: `record`, `sha256`, `source_root`; null otherwise), `output_folder`, `masks_folder`, `excluded`, `groups` (labels, position identifiers, image counts), `channels`, `channel_names`, `images` (per image `axes`, `roles`, `axes_from`, `pixel_sizes` by role with unit and source, `header`, `stated` or `pixels`, or `"uncalibrated"` without an X and a Y entry), `calibration` (`file`, `sha256` and the `statements`), `design`, `image_type`, `masks` (per type paired masks and images without one; unpaired and ambiguous masks), `mode`, `reused`, `recomputed` (file: reason), `removed`, `resumed` (files whose rows an interrupted run had staged, reused without reading their pixels, §9.4), `staged_discarded` (file: why its staged rows were not reused), `replaced` (file: what happened), `input_changes`, `unreadable_planes` (image: every failed plane of an image whose pixels could not be read, in sorted order of the images; the `unreadable_planes` error names at most 20 per image), `issues` (code, severity, message, paths, action). For people only: bricks never read it; a brick gets a stopped run's error issues from `RunInputs.open` (`RunStoppedError`, §9.1), the one reader of the report for the bricks. A published report is part of the run's generation (§9.4). |
| `check` | The same step stopped after the report; it writes the report only (`run.check_step.write_check_report`; v0.5.1: no design template, `experiment_design.csv` is derived by the run, §12). The command line's `check` refuses an output folder without a published description first (§0). The notebook's **Check folder** writes the same report with the same functions (`run.check_step.suggested_review`, `write_check_report`): it is the first step of describing the experiment, so it runs before *Check and save*; nothing when an embedding brick hides the output folder (`folder_step(show_output=False)`). |
| Exit status (v0.4.0; v0.5.1) | Every command (`check`, `run`, `masks`, `calibrate`, `describe`, `convert`) exits with status 1 when it prints an error and 0 otherwise; warnings never change it, and options argparse cannot read exit with 2. `check`, `run` and `masks` exit with 1 on `A_DESCRIPTION_REQUIRED` (§0). `check` exits with 1 on every error it reports: the preflight's, the axis check's and a refused write of its report (`output_locked`, `output_inside_masks`; found in the review of #130, it exited with 0). The `design` command and the design checks of `check` were removed in v0.5.1 (PP-C). |
| Notebook (v0.4.0, PP-10) | Step 3 offers the mode (`restart`, `full`) with its effect, and lists before the run the inputs that differ from the recorded ones with their effect (`run.planned.planned_changes(given, output_folder, calibration=)`, from `resolve_inputs` and `calibration_change`: the changes the run then reports). After a run it names the mode, the files replaced and the input changes, and shows the report; a stopped run says what it wrote (question 55). A refusal `output_locked` (check, run or a stated size) shows the holder (computer, process ID, start time) and a **Remove the lock of a stopped run** button: `remove_stale_lock` only on that click, never the lock of a process still running on the same computer; on Google Drive one session per output folder (question 58). |
| Notebook masks (v0.4.0, PP-10c) | Step 1 has an optional masks folder; **Check folder** pairs it with `preflight(masks_folder=, mask_axes=)`, the masks folder and statements being those the run would use (`run.planned.planned_masks`, from `resolve_inputs`: given, else recorded). The **Masks** card shows each mask type (`mask_review.mask_type_rows`), the images without one and the masks' issues (`masks.issues`, and `mask_checks.mask_issues` once the roles are confirmed, as `run`). Mask axes are asked per type only where needed (`mask_review.mask_axes_questions`: `mask_not_2d`, `invalid_mask_axes`, `mask_axes_contradict_header`, and `mask_planes_mismatch` of 2-D masks, answered `YX`), with only the statements `masks.stated.mask_roles` accepts for every mask of the type; nothing is preselected or applied until **Check the masks** (`run.preflight.remasked`, which keeps the other answers). The run is given the same `masks_folder` and `mask_axes`; a masks folder not given reuses the recorded one, and **Drop the recorded masks** gives `masks_folder=False`. Step 3 is gated on masks without errors and on a pairing that matches the masks folder and statements chosen. `folder_step(show_output=False)` shows neither and pairs no masks folder. The mask step (`mask_relationships.json`) is step 3's **Describe the masks** (next row). |
| Notebook mask step (v0.4.0, PP-10d) | Step 3's **Describe the masks (optional)** card. Its button is enabled when step 1's output folder holds a v2 manifest that recorded masks (`run.mask_step.recorded_masks(output_folder)`, a `RecordedMasks` from the manifest's `experiment_root`, `written_at` and `masks` keys): after a run in this session, or for an earlier run in a reopened notebook. The help line names the run it describes (output folder, when written, experiment folder, masks folder and mask types). The output folder is the one step 1 shows (default: next to the experiment folder); the experiment folder typed in step 1 only locates it, and the recorded one is read. A click runs the `masks` command's step with the run's recorded inputs (`prepare_mask_step(None, output_folder)`, the path the CLI's `masks --output` takes: experiment folder, masks folder, mask-axis statements, exclusions) and the default thresholds (the notebook offers no threshold; the command line's options change them), with a progress bar per image, then `describe_masks`, which reads the masks and writes `mask_relationships.json` holding the lock throughout. The card shows the mask types, the relationships with their counts (`mask_text.mask_type_rows`, `mask_relationship_rows`), masks of the same objects and the hierarchy (`mask_hierarchy_lines`), labelled as suggestions to check, and the path written. A refused step changes nothing: `experiment_not_recorded`, errors found since the run (an experiment or masks folder moved or removed, a mask changed; shown with their messages), no masks, masks that cannot be used (a red card), and `output_locked` (the lock card with **Remove the lock of a stopped run**). A new run clears the card. `folder_step(show_output=False)` hides the card; the pinned embedding API is unchanged (the click handler is private). |

### 9.3 Calibration (`calibration.csv`, v0.4.0, design v0.4 §3)

| Topic | Rule |
|---|---|
| Source per axis | For X, Y and Z, an image's size comes from its header (`header`) or from a statement in the output folder's `calibration.csv` (`stated`, or `pixels`: deliberately uncalibrated). The time step comes from the header only. An axis with neither is uncalibrated: no value, no source, never a default. A Z or T size is recorded only for an image whose roles include Z or T (v0.4.0, plan §12 question 53): a max projection's header may still give the z-step of its stack, and that is not a size of the projection (a singleton role of an axis the file lacks, `missing_as_singleton`, does not count). |
| File | UTF-8 CSV, columns `target,axis,size,unit`, one statement per line. `target`: `.` for the whole experiment, or a folder (a condition or a repeat) or an image, relative to the experiment folder with `/`, compared exactly (backslashes, a leading `./` and a trailing `/` are normalised; an absolute path, a drive, or an empty, `.` or `..` component elsewhere is refused). `axis`: `X`, `Y` or `Z`. `size`: a positive finite number in plain ASCII decimal notation (`0.65`, `1e-3`; not `0,65` or `1_0`); empty with the unit `pixels`. `unit`: a length unit of decision D-a2 in any spelling `space_unit` reads (written back as its OME-NGFF name), or `pixels`. The reader (`read_calibration`) is strict: other columns, a value missing or extra, outer whitespace, a target outside the experiment folder, an unknown axis or unit, a size that is not a positive number or not a positive finite length in micrometres once converted (`1e308 m`, issue #93), or two statements for one target and axis are refused naming the line (`CalibrationFormatError`). A UTF-8 byte-order mark and Windows line endings are read. |
| Precedence | The most specific statement covering an image applies: the image's own, then the deepest folder's, then the whole experiment's. Statements are per role: Y and X are stated and recorded on their own, and nothing exchanges them. |
| Writing | A run reads `calibration.csv` and never writes, replaces or removes it, so it survives `full`, stopped and failed runs. The user writes it by hand, with `write_calibration(calibration, output_folder)` (holding the output folder's lock; `OutputLockedError` while a run holds it) or with the CLI `calibrate` (`--size TARGET AXES SIZE UNIT`, `--pixels TARGET AXES`, `--remove TARGET AXES`; without them it prints the statements), which reads, changes and writes the file under the lock (`run.calibrate.update_calibration`), so two changes never overwrite each other. |
| Checks | `calibration_unreadable` (error: the file cannot be read; left as it is); `calibration_target_not_found` (error: a target that is neither a folder nor an image of the experiment, excluded images included); `calibration_differs_from_header` (warning, naming each image and axis with both sizes: a stated size not the header's by the D-a rule, or `pixels` where the header gives a size; z-steps only for images with more than one depth slice; the stated size is used); `calibration_axis_absent` (warning: a statement covers images without that axis, for example a Z size for the whole experiment with 2D images; the images are named, and the size is not recorded for them). The §11 pixel-size checks run on the resolved sizes; a header size in an unsupported unit is not reported on an axis that is stated. `check` and the notebook's folder check (`preflight`) use the same sizes. |
| Notebook (v0.4.0, PP-10) | Step 1's **Pixel sizes** card shows each image's X, Y, Z and T sizes with their source (`header`, `stated`, `pixels`, or uncalibrated) and states or removes statements per target, axes and unit with `calibration_entry` and `update_calibration` (under the lock; no second rule set), then reads the file again (`run.preflight.recalibrated`) without resetting the answers given. Before the axis roles are confirmed it shows the sizes the folder check found; after, those fitted to the confirmed roles (`fit_calibration`: no Z or T size for an image without that axis), as the run records them. The calibration messages are shown on the card. |
| Recording | Each manifest entry's `pixel_sizes` records the resolved sizes with their source (§9); the manifest records `calibration_sha256`; the report shows each image's sources and the statements, and its text counts the Z and T sizes of the images with that axis. Changing the calibration recomputes no row: the next `restart` records the new sizes, reuses every row and names the change (§9.2). `RunInputs` refuses a `calibration.csv` changed, removed or added since the run. |

### 9.4 Staging, resume and publication (v0.4.0, PP-12, design v0.4 §1)

| Topic | Rule |
|---|---|
| Staging | As soon as a run has read one image, that image's rows are staged in the output folder's `.staging` folder: a `<id>.csv` with its rows (the normalization brick's writer, the table's format), a `<id>.qc.csv` with its image QC rows (§19) and then `<id>.json` (staging version 2; a version 1 record is discarded and its image read again) naming the image, its staging key and the SHA-256 of both rows files. The staging key is the image's row key (§9: fingerprint, labels, axes, roles, `singleton_roles`, `axes_from`, read path, bins and histogram settings) with the pixel type, the table's schema version, the image QC `qc_version` and the versions of `morphobricks-preprocessing` and `morphobricks-normalization`. A rows file without its record (killed between the two) was never staged. |
| Resume | A run killed part-way (a crash, a kill, a lost Colab session) is resumed by the next run: each image that needs new rows whose staged key is the key it would have now (and whose fingerprint is still that; with content verification, whose content SHA-256, part of the key, too) takes its staged rows without its pixels being read (`RunResult.resumed`, report `resumed`). Any other staged rows are discarded and reported with the reason (report `staged_discarded`): the image changed since its rows were staged; its roles, labels, folder or row settings changed; the experiment's pixel type changed; another version staged them; they could not be read (reported under the image the record names, else the record's file name); the table already holds its rows; the image is no longer analysed; a `full` run (which reuses no staged row). Nothing is reused when identity is uncertain. A run that stops keeps `.staging`, so the next run resumes; a run that publishes removes it after the commit. Every check still runs over the whole experiment (design v0.4 §1). A resumed table is byte-identical to an uninterrupted run's. |
| Source changes during a run (issue #90) | Each image's header is read between two fingerprints that must agree (`preflight(bind_headers=True)`), and that fingerprint is the one its manifest entry records; the image must still have it just after its pixels are read (only then are its rows staged); and every analysed image (reused, resumed or read) is fingerprinted again just before the commit (in a `verify_content` run also hashed again, as the last step before the manifest's rename, above). An image that changed or went missing at any of these points is the error `source_changed_during_run` naming it: no scientific outputs are published and sources are untouched; the discovery report is written; the earlier generation is kept, and the rows staged so far stay staged (the changed image's own rows are not staged, or are discarded by the next run). Masks are bound the same way (issue #160): each mask's header is read between two fingerprints that must agree (`find_masks(bind=True)`, `MaskFile.bound`; else `mask_changed_during_check`), that fingerprint is the one its manifest entry records (before #160 the fingerprint was taken when the manifest was written), and every mask must still have it right before the commit; a mask that changed or went missing is the error `mask_changed_during_run` naming it, with what changed, and nothing is published (`mask_binding`). Source masks are checked as late as practicable (right before the manifest's `os.replace`, in `before_commit`), and ordinary concurrent edits are detected. The source files are not locked and not snapshotted (owner decision of 8 November 2026, issue #222), so a change in the instant between that final check and the manifest replace is not detected: the manifest binds the masks' bytes as they were at that final check. |
| Workers (issue #107) | `RunMode(workers=..., max_ram=...)` (`run_experiment(mode=...)`, CLI `run --workers N\|auto`, `--max-ram SIZE`): how many images are read at once, each whole (every plane and channel) in its own worker thread. A worker only reads its image with the run's reader and checks it after the read (fingerprint; with content verification, SHA-256), and returns its rows or `failed` / `changed`; the calling thread alone resumes staged rows (before any worker starts), stages each image's rows as soon as its worker returns, aggregates them in the sorted order of the images, runs the dataset-wide checks, publishes and calls `progress`. `1` (default): one image after the other in the calling thread, as before. A whole number `N`: `N` threads (at most one per image to read). `"auto"`: `min(images to read, CPUs this process may use)` and, with `max_ram` (bytes; only with `"auto"`), at most `(max_ram - 256 MiB) // largest`, where `largest` is the largest image's working-memory estimate from its header (`planes.plane_layout` under the run's memory limit: one plane, or one block when its planes are read in blocks, plus `8 × bins` per 2-D plane for its rows; never the size on disk; 256 MiB are kept for the run itself). Every image is counted as the largest (conservative); an image whose estimate alone is over that budget stops the run before any pixel is read: the error `image_over_max_ram` naming those images. The memory limit itself is never changed. The outputs are byte for byte those of the serial run with any value (every file, the manifest included: `written_at` is the only field excluded from byte identity, see §9); the number of workers is in no row key, staging key, manifest or table (`RunResult.workers` only: the size of the thread pool the images were read with, `min(workers planned, images to read)`, which is the most images read at once and the most distinct threads that read them, not a count of the threads used, since a pool starts a thread only when no idle one is waiting; 1 when they were read in the calling thread or none was read). Failures are the serial ones: `unreadable_planes`, `source_changed_during_run` (never staged), and an unexpected exception in a worker is raised by `run_experiment`, with a note naming the image, once the images not yet started are cancelled and those being read have finished; nothing is published, and what was staged is resumed by the next run. No serial fallback, no process backend, no plane- or channel-level parallelism. The notebook's **Images read at once** (step 3) defaults to `"auto"` with `max_ram` half of `run.workers.available_memory()` at its start (Linux `MemAvailable` lowered to the control group's remaining limit; Windows `GlobalMemoryStatusEx`; elsewhere the physical memory), rounded down to 0.1 GiB, and to 1 when that is unknown or below 0.5 GiB (owner decision of 16 November 2026); the CLI and `RunMode` keep 1. Once the axis roles give every analysed image's layout, and while the user has changed neither the workers nor the cap, the notebook starts at 1 when that default cap is below what the largest image needs (`run.workers.max_ram_needed`: its working-memory estimate, as above, plus the 256 MiB, so exactly when `"auto"` would refuse with `image_over_max_ram`) and says so ("Images read at once: 1, because the default memory cap (X GiB) is smaller than the largest image (Y GiB); outputs are identical for any number", Y rounded up to 0.1 GiB); values the user set are kept, with their refusal (D4b). Where the figure is the physical memory (`run.workers.memory_is_physical`: macOS), the cap's line says "physical memory". |
| Generation | The run's published files: `plane_histograms.csv`, `image_qc.csv` (§19), `experiment_design.csv` (with a design), `run_manifest.json` and the report files, and the files the generation removes (`mask_relationships.json`, §9.2). |
| Publication | (1) The whole new generation is written into `.publish` in the output folder and flushed to disk, then `.publish/publication.json` (publication version 1: each file's SHA-256 and the files removed). (2) **Commit, the one atomic step:** `os.replace` of the new `run_manifest.json` over the old one, then the output folder is flushed (`fsync` of the folder, POSIX), so the commit is on the disk when it returns and a power cut after it keeps the new generation. Nothing in the output folder changes before it. (3) The other files are moved into place (`os.replace` each), the removed files removed, `.publish` deleted, then `.staging`. |
| Interrupted publication | Whatever next takes the output folder's lock (`run`, `check`, `masks`, `design`, `calibrate`, the notebook's writes) first finishes a committed publication or deletes an uncommitted `.publish`, record first (the earlier generation is then untouched) (`run.generation.finish_publication`). **Committed** means the staged manifest was moved: the record is there, `.publish/run_manifest.json` is not, and the manifest in place is the record's; the remaining moves and removals are then done. Anything else is not committed, also when the manifest in place is byte-identical to the staged one (an identical rerun refused before its commit); a `.publish` whose record cannot be removed is left whole (its staged manifest keeps it uncommitted) for the next holder of the lock. A failure to move a file after the commit (for example a table held open on Windows) raises `PublicationIncompleteError`: the new generation is current, and the next holder of the lock finishes the moves. |
| What readers see | **Through the manifest** (`RunInputs`): exactly one generation, the old or the new one, never a mix: each file the manifest names is read by its SHA-256 from the output folder or, while a publication is unfinished, from `.publish` (`run.generation.published_path`), and the read is repeated if the manifest changed meanwhile (`read_stable`). **A plain reader of `plane_histograms.csv` alone** always reads one whole table of one generation (each move is one rename): the old one until it is moved. A reader that opens several output files without the manifest can pair files of two generations while a publication's step 3 runs or after a kill in it, until the next holder of the lock finishes it: read several files through `RunInputs`, or check `csv_sha256` and `experiment_design_sha256`. |
| Windows | The same protocol. `os.replace` replaces a file atomically on NTFS too, but fails while another program has the file open: at the commit nothing has changed then (the run raises, the earlier generation is kept); after it, `PublicationIncompleteError` as above. Folders are not flushed (Windows cannot open them; NTFS journals the rename itself, but a power cut right after the commit may bring back the earlier manifest, which is then a whole earlier generation); the kill tests (SIGKILL at every step) run on POSIX only. |

## 10. Removed in v0.4.0

Converting TIFF images to OME-Zarr in `<output>/ome_zarr` (`convert_tiffs`, `zarr_export`, CLI `--convert-tiff`, the notebook's "(Optional) Image conversion" card) was removed (spec revision 4, §8 PP-2): the bricks never read those copies, and earlier rows were the TIFF's own, so a later run keeps them. Named-axis copies are a separate step, `convert` (§17). The section number stays because other sections refer to it.

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
| `unsupported_pixel_size_unit` | Warning | An image's metadata give a pixel size or z-step (OME `PhysicalSize{X,Y,Z}`, ImageJ resolution or `spacing`, OME-Zarr level-0 scale) in a length unit that is not one of decision D-a2 (§16), for example OME `in` or ImageJ `inch`. The size is not converted and the axis is treated as uncalibrated (as before); the warning names each image, its axes and the unit as written, so the missing calibration is never silent. An OME-Zarr scale of X, Y or Z without a unit is named the same way ("X and Y without a unit"; review of #136): it is recorded as unknown (audit I3), never taken as micrometres, except on the axes listed as `unknown_scale` (§10). A unit without a size, and a plain TIFF's resolution tags (decision D-c), are not reported. The run is not refused. |
| `invalid_physical_calibration` | Warning | An image's metadata give a calibration that cannot be used (issue #94): OME-Zarr `coordinateTransformations` that are not a list or of an unsupported type, more than one scale, a scale stored in a separate file or not one number per axis, or a scale value of X, Y, Z or T that is not a positive finite number; or, from any TIFF or OME-Zarr metadata, a size that is not a positive finite length in micrometres once converted (issue #93: `1e308 m`, `1e-320 pm`); or OME-XML in a TIFF that cannot be parsed (audit m6: before, any error reading the sizes read as merely uncalibrated); or TIFF metadata whose bytes cannot be read (`OSError`, `struct.error`, `TiffFileError` while a tag is read), or an ImageJ `XResolution` or `YResolution` tag that is missing or unreadable while the other is given (tifffile drops a tag it cannot read with only a log message), with a unit (round-2 audit A-m6); or an ImageJ unit with neither resolution tag ("a unit without a resolution"; owner, 3 November 2026, v0.4.0rc5: before, uncalibrated with no reason); or, from `read_physical_sizes`, a TIFF file without pages ("no pages", v0.4.0rc5: tifffile opens it with only a log message; in the folder check such a file is `unreadable_tiff`, as before); `read_physical_sizes` also gives a file it cannot open as a TIFF no sizes with the reason in `invalid` (in the folder check such a file is `unreadable_tiff`), and raises any other exception as the defect it is; or a size of OME-TIFF (`PhysicalSizeX/Y/Z`, `TimeIncrement`) or ImageJ metadata (`spacing`, `finterval` and the resolution tags, with a unit) that is itself not a positive finite number (`inf`, `NaN`, zero, negative or not a number; review of #133, before silently unknown); or an ImageJ `XResolution` or `YResolution` tag, with a unit, that is not two whole numbers (a tag of another type; review of #136, before silently unknown). The message names each image and what is wrong, with the axis and value. Its sizes are unknown on those axes (never a substituted value) and recorded as uncalibrated; the warning is in the `check` and `run` issues and the report (`discovery_report.json`). An image is not named once every size the malformed calibration leaves unknown (X and Y, and Z or T with more than one plane) is stated in `calibration.csv`; a time step cannot be stated there, so a lost time step of a time series is always named. A calibration that is not given (no transformations, or no scale) is not reported. The run is not refused. |
| `partial_pixel_size` | Warning | An image's recorded pixel size (header or `calibration.csv`) is known in micrometres along only one of X and Y; the other is unknown or stated as `pixels` (audit m5, plan §12 question 72). The message names each image with both sizes. Bricks that measure lengths refuse such an image (calibration decision 8, 2 November 2026): a partial calibration is never completed or approximated. The run is not refused. |
| `non_square_pixels` | Warning | An image's recorded X and Y sizes are both known but not exactly equal in micrometres (calibration decision 1: no tolerance, so 0.5 and 0.5004 µm differ). The message names each image with both sizes. Bricks that measure lengths (perimeter, Feret diameter, axis lengths) refuse such an image; areas and intensities are exact. The run is not refused. |
| `different_channel_counts` | Error | The number of channels differs between images of one folder (one acquisition): the `C` dimension, or the RGB samples `S` when there is no `C`; once the axis roles are confirmed, the length of the C axis (1 without one), which replaces the header's count. The images not having the folder's most common number are listed, with each number's image count. Folders may have different channels (Stage 3 S3.3); the images of one folder must not: move the others into a folder of their own or exclude them. |
| `different_plane_counts` | Warning | The number of depth slices (`Z`), time points (`T`) or planes along any other dimension except X, Y, C and S differs between images. A missing dimension counts as 1. Channels are compared by `different_channel_counts`. Before the axis roles are confirmed (`check`, preflight) the axes each header names are compared, and a plain stack (its header names no axes, §2) is left out; once they are confirmed (`run`, the notebook's run, `AxisReview.combined_issues`) each image's depth slices and time points under its confirmed roles are compared instead, and the header version is dropped. An image whose confirmed roles contradict its header (an axis error, never read) is compared by its header. Before issue #74 a plain stack was compared by its header letters, so a plain `QYX` stack confirmed as `YXC` gave "planes along Q". |
| `different_sizes` | Warning | The image width or height differs between images (TIFF and OME-Zarr). The other dimensions are compared by `different_plane_counts`. As for `different_plane_counts`, the header's Y and X before the roles are confirmed (a plain stack left out) and the confirmed Y and X after (issue #74: before, a plain `QYX` stack of (Y, X, C) arrays confirmed as `YXC` was compared by its X and channels, and a real difference in height was missed). |

| Rule | Detail |
|---|---|
| Counts | Only images to analyse count: excluded images are left out, a folder whose images are all excluded is not a biological repeat, and a technical-repeat folder whose images are all excluded is not counted as a technical repeat (issue #82, plan §12 question 61). A biological repeat with images directly in its folder has no technical-repeat folders (count 0); so has one whose only technical-repeat folder is named `single` (§1, plan §12 question 23: the level's value `single`, like a missing level). When images are excluded, the balance table's text (`BalanceTable.not_counted()`: the CLI `check`, the notebook's consistency card and design card) says how many and names the folders not counted (`BalanceTable.excluded_images`, `excluded_folders`). |
| Balance table | Conditions as columns, repeat groups as rows (§12; by default the biological-repeat folder name, `single` when there are no biological-repeat folders); each cell holds the number of images and of technical-repeat folders. |
| Pixel sizes | TIFF: OME `PhysicalSizeX`, `PhysicalSizeY`, `PhysicalSizeZ` with their units (micrometres when not given), or ImageJ resolution tags and `spacing` with the ImageJ unit (unknown without a unit). OME-Zarr: the level-0 scale (`coordinateTransformations` of the first dataset, times the image's own scale when given) with the axis units; a scale without a unit is unknown. Sizes are never guessed. The checks run on the sizes each image is recorded with: its header's, or those stated in `calibration.csv` (§9.3); a size stated `pixels` counts as unknown, and `missing_pixel_size` says how many such images it lists. |
| Panel | The notebook shows the balance table with the issues `mixed_dtypes`, `inconsistent_dimensions`, `missing_axes`, `inconsistent_mapping`, `different_sizes`, `different_channel_counts`, `different_plane_counts`, `channel_names_differ`, `different_pixel_sizes`, `missing_pixel_size`, `unsupported_pixel_size_unit`, `invalid_physical_calibration`, `partial_pixel_size`, `non_square_pixels`, `unbalanced_images` and `repeat_missing_conditions`. The command line `check` prints the balance table and every issue; `run` reports the same warnings, and stops on `different_channel_counts` and `channel_names_differ`. |
| Outputs | Unchanged: the checks write nothing, and `plane_histograms.csv` and `run_manifest.json` do not change. |

## 12. Experiment design (`experiment_design.csv`, schema version 1): an output only

A **repeat group** is one biological repeat across all conditions (normalization plan
N30): later bricks pool it over the conditions. Before processing, the user pairs the
biological repeats, confirms that each repeat group is comparable (N35), and may mark
control conditions (N36). Since v0.5.1 (§0, ADR 0002 AR10/AR11) these answers are part of the
published description, and `experiment_design.csv` is **derived** from it (experiment-v1 §11,
`design_derive.derive_design`): written by the publication and by every run, never read back
by preprocessing or any other brick (owner answer 3, Spec ADR 0002 AR11 (3)). The rows marked
*(removed in v0.5.1)* below describe what is gone: the lenient reader `read_experiment_design`,
`load_design`, the strict `read_confirmed_design` with `ConfirmedDesign`/`DesignCheckError` and
`RunInputs.design` (SWEEP-PP), and the notebook's editor model (`ExperimentDesign`,
`default_design`, `apply_design_edit`/`DesignEdit`, owner answer 4). What stays:
`write_experiment_design`, `DESIGN_COLUMNS`, `design_derive`, the manifest's
`experiment_design_sha256`, the rows as `DesignTable` (read-only) with the run's check
`reconcile_design`/`design_issues`, and the strict `parse_design_bytes` (for tests and for checking a written file; the draft's
legacy migration, `draft.migrate_legacy_design`, was removed under Spec ADR 0002 AR10 (9)).

| Column | Meaning |
|---|---|
| `condition`, `biological_repeat` | Folder labels, as in `plane_histograms.csv` (`single` when absent) |
| `repeat_group` | Label of the repeat group the folder belongs to (default: `biological_repeat`) |
| `control` | `true` when the condition is marked as a control |
| `image_count`, `technical_repeat_count` | Images to analyse (technical repeats added up) and technical-repeat folders holding images to analyse (0 when the images are directly in the biological-repeat folder or in a technical-repeat folder named `single`; excluded images and folders whose images are all excluded are not counted, §11), at the time of confirmation; the current counts while unconfirmed |
| `confirmed` | `true` once the repeat group is confirmed |
| `confirmed_on` | Date of the confirmation (ISO 8601), empty while unconfirmed |
| `schema_version` | `1` |

| Rule | Detail |
|---|---|
| Rows | One per condition and biological-repeat folder with images to analyse, sorted by condition, then biological repeat. UTF-8, LF line endings, header row, the columns in the order above; booleans written `true`/`false`. The same design gives the same bytes. |
| Pairing | By default repeats with the same folder name in different conditions are one group, named after the folder. The user may move a folder into another group. A group holds at most one folder per condition (`repeat_group_conflict`, an error). A group not found in every condition is used as it is, with the warning `repeat_missing_conditions` (§11). |
| Confirmation | Per repeat group: "Were these images prepared and acquired under the same circumstances, so they are comparable with each other?". A group is confirmed when all its rows are. Without biological-repeat folders (every condition `single`), the whole experiment is one group and the question is asked once (N31). Missing technical-repeat folders need no question. |
| Controls | None, one or several conditions; every row of a condition has the same value. Recorded only. |
| Run's check | The run brings the derived rows up to date with the folders found (`reconcile_design`): folders in them keep their group, control mark and confirmation (added or removed images within a folder keep it too); a folder found but not described joins the group named after it, inherits its condition's control mark, and that group is refused as not confirmed (`design_not_confirmed`); folders no longer found are dropped. Nothing is read from the output folder. |
| Reading | *(removed in v0.5.1, SWEEP-PP)* No reader of the file for its answers remains: the lenient `read_experiment_design`, `load_design` and the strict `read_confirmed_design` are gone. `parse_design_bytes` parses the file exactly as `write_experiment_design` writes it (UTF-8 without a byte-order mark, the columns in order, `true`/`false`, whole counts, `YYYY-MM-DD`, identities as §16, a header naming a column twice or not at all refused naming it, issue #83), only to check a file written by this brick (no code path reads an `experiment_design.csv` to propose or prefill a draft: experiment-v1 §7, Spec ADR 0002 AR10 (9)); it raises `DesignFormatError` naming the line and column. `design_issues` gives `invalid_experiment_design` for an unusable repeat-group name or date in rows built in code, and `write_experiment_design` refuses to write them. `DesignTable.repeat_groups()` gives the `{(condition, biological_repeat): repeat_group}` mapping. |
| Notebook | The pairing is answered on the design card and kept in the draft (`morphobricks.draft.json`), confirmed with the rest on *Check and save*; the notebook never writes or reads `experiment_design.csv` (v0.5.1). The card is built on the draft (SWEEP-PP, owner answer 4): one row per folder of the draft's base with the repeat it is paired with (`PreprocessingNotebook.pairing`); a change is a draft answer at once, and a replicate whose folders it changes is asked "same way?" again. There are no comparability questions or control boxes on the card (they are on *Check and save* and the references). |
| Command line | *(removed in v0.5.1)* `run --design FILE` and the template `run` wrote: `run` refuses an output folder without a published description (§0). |
| Template from `check` | *(removed in v0.5.1)* `check` writes only its report. |
| `design` command | *(removed in v0.5.1)*: the pairing, confirmations and references are answered in the notebook's describe steps. `apply_design_edit` and `DesignEdit` were removed in SWEEP-PP. |
| `describe` command (Stage 1, PR 17) | `describe EXPERIMENT [--output FOLDER] [--exclude PATH] [--publish]` prints the draft experiment description (experiment-v1 §12) in plain language: the proposed ID of each condition, technical-repeat folder and biological repeat (no proposal, with the rule's code, for a folder that cannot be an ID: never repaired), each repeat with its folders and image counts and whether it is confirmed (and the date), the references, and every open question with what the user has to do. The draft is always the draft of the folders (`draft.draft_from_discovery`); an `experiment_design.csv` in the output folder is never read to propose or prefill it (experiment-v1 §7; the legacy migration was removed under Spec ADR 0002 AR10 (9)); when `morphobricks.toml` is published (`run.description_run.load_description`), the folders are compared with it (`description_reconcile.reconcile`: unchanged, new, gone, image counts, repeats to confirm again), reading the folder structure only, so the channels are not compared. It reads no image and writes nothing: no output folder, draft, description, design or records file. **It confirms nothing**: an ID, the comparability of a repeat (N35), a reference, a removal and every date are the user's answers (notebook), never given by the command or read from the clock. `--publish` publishes a draft only when no question is open and every decision is confirmed; otherwise it prints `Not published. What is missing:` with every item and exits with status 1. This version holds no answers outside the notebook (the draft file is not in the package), so `--publish` refuses every draft built from folders and publishes nothing; for a published description that matches the folders it says `Nothing to publish` (exit 0); a new, gone or changed folder is listed and the file is left as it is. An error (experiment folder not found, discovery errors, unreadable description or records) is exit status 1; the command takes no lock. |
| Run | The run derives the design from the published description and writes it with the table, published before the manifest, which records its SHA-256 (§9). `run_experiment` takes no design. `plane_histograms.csv` does not depend on the design. |

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
| Confirmation | Never applied without the user's confirmation. The notebook proposes it at the quick scan (display only, from the headers; nothing is recorded there) and asks on the Channels step once the axis roles are confirmed (owner, 12 November 2026), pre-filled only when every header agrees and recorded only by a click (**Use for all folders**, or each folder); folders of different types are refused (`R_HETEROGENEOUS_LAYOUT_NOT_ENABLED`). Changing the axis roles or the folder asks again. The run takes the type the saved description states. The command line takes `--image-type`, or uses the suggestion and prints it. |
| Run | `run_experiment(..., image_type=...)` records the type and `channels_from_rgb_samples` in the manifest (§9). `rgb` for images that cannot be RGB is the error `rgb_not_possible`; any other value raises `ValueError`. Without a type the manifest records null. |
| Outputs | `plane_histograms.csv` and `experiment_design.csv` do not depend on the type: a plane of an RGB image is one row with channels C0, C1 and C2, as for any image. Changing the type on a later run reads no image again. |

## 14. Intensity checks

After the table is written, the images' intensities are checked from its rows alone, with
the normalization brick's `image_summaries`, `intensity_checks` and `unchecked_channels` ([normalization
contract][normalization contract] §7, plan N38): no image is read and nothing is written
apart from the limits in the manifest.

| Rule | Detail |
|---|---|
| Summaries | One per image, all Z and T planes pooled per channel: median, 1st and 99.8th percentiles, saturated fraction (normalization contract §7.1). |
| Warnings | `image_intensity_outlier` (an image's median or 99.8th percentile in a channel more than `outlier_limit` robust deviations from the other images of its condition and biological repeat), `repeat_intensity_shift` (a biological repeat's median of image medians far from the other repeats' images of its condition), `saturated_image` (more than `saturation_limit` of the pixels at the type's maximum), `no_intensity_spread` (1st and 99.8th percentiles equal); then `channel_without_finite_pixels` (an image channel without finite pixels, whose blank summaries the four checks cannot see, from the normalization brick's `unchecked_channels`, normalization issue #57). One warning per image or repeat, channel and compared value, naming the channel `C<i>`, the image or `<condition>/<biological repeat>`, the value and the group value or limit. Comparisons stay within a condition, so treatment effects are never reported. |
| Limits | `IntensityCheckSettings`: `outlier_limit` 3.0, `saturation_limit` 0.01, `min_images` 5 (provisional in the normalization brick). `run_experiment(..., intensity_settings=...)` uses them and records them in the manifest (`intensity_checks`, §9); they change no row. |
| Stopped run | `RunResult.report_written` is True for every run that held the lock, also one that stops (it wrote its report); False for a run refused before (the lock, an output folder inside the experiment). A stopped run's summary (`report.run_summary`, the CLI, the notebook) says that no table or manifest was written and names what was: the report, and the design template the CLI `run` writes when it stops on the design (plan §12 question 55). |
| Run | `RunResult.intensity` holds the summaries and warnings (`IntensityReport`) of a written table, None otherwise. The warnings never block and are kept apart from `RunResult.issues`. |
| Notebook | Step 3 shows the checks of the table just written after every successful run, step 4 those of any chosen table: a status line with the limits, counts per kind, a table of the warnings (check, channel, image or repeat, what was found) and what to do about each kind, a collapsed **Check settings** panel (changing a limit updates the warnings at once; in step 3 the next run records it), and per channel (chosen with buttons) box plots of the image medians and high percentiles per condition and biological repeat, with images that have a warning in that channel (`Issue.channel`, N52) as red diamonds and shifted repeats as amber boxes, and the cumulative intensity distribution of each biological repeat, one panel per condition (shifted repeats dashed; at most 12 conditions). The plots are drawn in the notebook only; no file is written. |
| Command line | `run` prints the warnings after the run, grouped by kind with what to do once per kind, and draws no plots; `--outlier-limit`, `--saturation-limit` (a fraction) and `--min-images` set the limits. |

### 14.1 Channel identity (possible swaps)

Right after the intensity checks, `channel_identity.check_channel_identity` asks whether each
tagged channel of each imaging unit looks like the same channel of the other units, from the
same image summaries (design `channel-identity-check-2026-11-21`, owner answers of
21 November 2026): intensity only, no pixel read, warnings and a plot, never blocking.

| Rule | Detail |
|---|---|
| Signature | Per imaging unit (condition, biological repeat, technical repeat) and channel ID, the median over the unit's images of `level` = log(median + o), `bright` = log(high percentile + o) and `spread` = log(high − low percentile + o). The percentiles are the summaries' own, 1st and 99.8th (§14; the design text's 0.2nd is not used). The offset `o` is 0.003 × the median of the channel's own positive high percentiles among the images of that pixel type, and at least one count for integer pixel types (the smallest step measured, so a median of 0 or 1 count is not a large ratio). The log is therefore scale-free (16-bit and 0–1 float images with the same relative differences give the same findings while the integer floor is not in effect, that is when the channel's median high percentile is above about 333 counts), and a bright channel does not change a dim channel's offset or verdict. When a unit's channel is measured against another channel's reference, its values are summarised again with that channel's offset, so both are on one scale. **Limit:** float images converted from integer data whose medians are near zero get no floor (a float has no smallest step) and can give false `channel_unlike_others` warnings (a sparse 8-bit channel with medians of 0 or 1 count, stored as float/255, warned in 173 of 200 simulated runs; stored as 8-bit, in none). The saturated fraction is kept beside the signature, not compared. An image channel without finite pixels is left out; a unit channel with no image left or with a negative low percentile, a unit whose images have different pixel types, and a channel without any positive intensity in its pixel type have no signature and are listed as not checked (never filled in). A unit whose images have different pixel types does not enter the offsets either. A channel a unit has but cannot be checked (for example with negative intensities) still counts as one the unit has, so a look-alike of it is never worded as a channel the unit does not have. |
| Reference | For unit `u` and channel `c`: the per-component median and MAD × 1.4826 (at least `mad_floor`) of `c`'s signatures in the other units of `u`'s condition, never `u` itself. A treatment effect is therefore not reported when the condition has at least `min_reference` other units with the channel. With fewer, the other units of the experiment are the reference and the finding says "compared across conditions" (a treatment effect may then be reported); with fewer still, `c` of `u` is not checked (listed, not a warning). Units are compared only with units of the same pixel type; different types are named as not compared. **Limit:** a swap made in every unit of a condition is not found, since it is what that condition looks like. |
| Distance | `d(u, c -> c')`: root mean square of the robust z-scores of `u`'s `c` signature against the reference of `c'`. |
| Reported | A unit channel is reported when `d(u, c -> c) > outlier_limit` and at least one component is more than `outlier_limit` robust deviations and at least `min_log_difference` from the reference (the components named as off: level, brightness, contrast). Only unit channels compared with a reference count as checked; with none, the summary says that no channel could be checked. |
| `possible_channel_swap` | Either a mutual pair: `u`'s `c` is reported, its nearest other channel `c'` is one of `u`'s channels, `partner_ratio × d(u, c -> c') < d(u, c -> c)` and `partner_ratio × d(u, c' -> c) < d(u, c' -> c')`, both within `outlier_limit` (`u`'s `c'` reported or not): one warning, "`c` and `c'` look swapped", with both channels' distances, naming the unit once. Or a clear look-alike of another channel `c'` of the unit: `d(u, c -> c') <= swap_limit` and `swap_ratio × d(u, c -> c') < d(u, c -> c)`. |
| `channel_unlike_others` | Every other reported unit channel: names the unit (`condition/biological repeat/technical repeat`), the channel, the components off and the distances; "Nearest channel: `c'`" when another channel's reference is nearer than its own, or "It looks like `c'`, a channel this unit does not have" for a clear look-alike of a channel the unit lacks (never called a swap). |
| Limits | `ChannelIdentitySettings`: `outlier_limit` 3.5, `min_reference` 3, `mad_floor` 0.2, `min_log_difference` 0.6 (natural-log units), `swap_limit` 1.0, `swap_ratio` 3.0, `partner_ratio` 2.0, chosen with a simulation (2 conditions × 4 units × 3 channels, between-unit variation of 10 % and 20 %; PR #364). With very noisy data (units differing by about 30 % per channel) false warnings become frequent: in that simulation 171 of 300 runs without any swap give a warning and 59 a false swap; read the warnings as prompts to look, never as verdicts. The run uses the defaults and records them in the manifest (`channel_identity`, §9, beside `intensity_checks`); other limits are applied by calling `check_channel_identity(result.intensity.summaries, settings)`, which recomputes nothing. |
| Run | `RunResult.channel_identity` (`ChannelIdentityReport`: signatures, findings, warnings, not checked, the number compared, pixel types and offsets) for a written table, None otherwise; its warnings are in the run report beside the intensity warnings and kept apart from `RunResult.issues`. |
| Command line | `run` prints them after the intensity checks, grouped by kind with what to do, then what was not checked. |
| Notebook | Under the intensity checks' plots, **Do the channels look like themselves?**: a status line (an information status when no channel could be checked) with the limits, the findings with what to do, what was not checked, and one plot of brightness against contrast (log scale) with one point per unit and channel, coloured and shaped by channel tag; a flagged point is circled and labelled with its unit; a possible swap has a dashed arrow from the centre of the reference the check used for that unit and channel to the point. When nothing could be checked, the section stays and shows the status and why each unit channel was not checked, without a plot. Nothing is written. |

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
| Layout | Either one subfolder per mask type, each mirroring the experiment's folders (`<type>/<condition>/.../<image>.tif`), or the experiment's folders with the type after the image name (`<condition>/.../<image>_<type>.tif`, as segmentation tools often write it, e.g. MorphoRelate's JUMP-Target-2 conversion); the two may be mixed. Any number of mask types. The label images of an OME-Zarr image (its OME-NGFF `labels` group, with the `zarr` extra) are masks too, of the type named by the label image; since v0.4.0 they are always used when present, with or without a masks folder. Each label image has its own fingerprint in the manifest, and the `labels` group is left out of its image's fingerprint (`source_signature`), so adding or changing a label image does not recompute the image's rows. |
| Pairing | A mask belongs to the image in the same leaf folder with the same `position_id` (§1), ignoring upper/lower case; its file is a TIFF (`.tif`, `.tiff`, optionally `.ome`, any case). Excluded images have no masks: their masks are reported apart (`mask_of_excluded_image`, warning) and not used. |
| Names | A mask type is named by its folder, by the suffix after the image name, or by its label image. A file that could belong to two images (an image name that starts another's, `a_b_cells.tif` beside images `a` and `a_b`) or fit both layouts is the error `ambiguous_mask`. Two names differing only in case are the error `mask_names_differ_in_case`; one image with two masks of one type (two extensions, or a file and a label image) is the error `duplicate_mask`. |
| Header checks | From the headers only, no pixels read (`check`, `run`, `preflight`): once its length-1 axes other than Y and X are removed (Y and X are kept whatever their length, so a mask one pixel high or wide, or a single pixel, is a 2D `YX` mask; a TIFF whose series has no Y or X but is one 2D page, such as a 1D array `tifffile` saved as `{"shape": [N]}` and reads as `X`, is that page, `YX` of 1 row; plan §12 question 44), a mask has Y and X and at most a depth axis Z and a time axis T, each named so in its own metadata (TIFF axes as `tifffile` reads them, e.g. an ImageJ hyperstack or OME-TIFF; OME-NGFF axes for a label image); any other or unnamed axis, such as a plain stack that could be depth or time, is `mask_not_2d` and is never guessed, unless the axes are stated for its mask type (`--mask-axes Cells=TYX`, `mask_axes={"Cells": "TYX"}`: one role, Y, X, Z or T, per axis of the header with length-1 axes other than Y and X left out; recorded as `axes_from: "stated"`; a statement that does not fit is `invalid_mask_axes`, one that contradicts an axis the metadata names is `mask_axes_contradict_header`, one for a type not found is `invalid_mask_axes`; a statement `YX` is `shared_mask_not_supported`: it was the option of one 2-D mask shared by every plane of its image, removed by the owner decision of 5 November 2026, and a 2-D mask needs no statement). Masks are never converted or reordered: a mask whose X is stored before its Y, or whose Y and X are its image's swapped, is `mask_layout`, with a `numpy.swapaxes` hint; the message names each mask with its header axes (`Cells/a.tif: header axes XY: X is stored before Y`). Unlike an image whose own header names X before Y (read in canonical order), a mask is never reordered, so `run` refuses it too (before PP-8b `run` dropped this error with the header size comparison and recorded the mask with `axes: ""`); a manifest never records a mask without axes. A mask has the Y and X sizes of its image (`mask_size_mismatch`; `check` and `preflight` compare with the Y and X the image's header names, and not for a plain stack, whose Y and X are not named; `run` compares with the image's Y and X under the confirmed roles, `mask_size_issues`, and leaves out the masks of an image with an axis error (§2), which is never read — before v0.4.0 `run` compared with the header's letters, so a swapped image gave a misleading size error) and integer or boolean pixels (`mask_pixel_type`; a mask holding a negative label or a label above 2,147,483,647, the int32 limit every brick shares (§16, decision D-b), raises an error when its pixels are read); the masks of one image have the same depth slices and time points (`mask_planes_differ`); a mask that cannot be opened, or holds several series, is `unreadable_mask`, as is an OME-Zarr image's `labels` group that cannot be read or does not list its label images (path `<image>/labels`), so its mask types never disappear unreported. All five are errors. |
| Planes | A mask belongs to the image's planes (issue #36): its Z and T lengths (1 for an axis it lacks) must equal the image's under the confirmed axis roles, checked by `run` (`mask_planes_mismatch`, error; `mask_plane_issues`; not for an image with an axis error, §2). So a 2D mask belongs only to an image with one plane, and is refused for a time-lapse (Z-stacks are refused themselves, §2): the masks of an image with T > 1 must have the same T, one mask plane per time point, named in the mask's metadata (`TYX`) or stated for its type (`--mask-axes Cells=TYX`). A 2-D mask is never shared by several planes, whatever its metadata names (owner decision of 5 November 2026, plan §12 question 76: the stated shared 2-D mask of the release candidates, `--mask-axes Cells=YX`, **YX** under the notebook's Mask axes, `MaskFile.shared`, is removed; a statement `YX` is `shared_mask_not_supported`, and a `YX` statement recorded by an earlier run is not applied again). A mask's plane at index `i` along Z or T belongs to the image's rows with `z = i` or `t = i`. `read_mask(..., z=, t=)` reads one plane and requires `z` and `t` for a mask with those axes. `check` and the `masks` command have no confirmed roles and do not compare with the image. |
| Folder | The masks folder must exist (`masks_folder_not_found`) and be outside the experiment folder (`masks_inside_experiment`), where its folders would be read as labels. The experiment may not be inside the masks folder (`experiment_inside_masks`), where its images would be read as masks of a type named after its folder, and the masks folder may not hold named-axis copies (`copies_inside_masks`: a conversion record, §17, anywhere in its tree, OME-Zarr stores not entered). The output folder may not be inside it (`output_inside_masks`), where the run's files would be read as masks; `check`, `run`, `masks`, `design` and `calibrate` check this against the masks folder given or recorded by the run in the output folder before they create the output folder, take its lock or write a report (audit of 3 November 2026, I1; `design` and `calibrate` raise `OutputInsideMasksError`). Paths are compared resolved, every link followed (the rule of §1's links), and, for the folders that exist, by device and inode (another spelling of a folder on a file system that ignores case); the output folder against the experiment (`output_inside_experiment`) and the copy tree against the experiment (`copy_folder_overlaps_experiment`, §17) are compared the same way, with one helper, `links.relative_inside` (#135 review follow-up). `check` given no `--masks` exits with 1 and the error when the recorded masks folder holds the output folder. An output folder that holds both the experiment and the masks folder (their parent) is allowed: the run writes only its named files and folders at its top level, and neither the experiment nor the masks walk enters it (audit m8; plan §12 question 70). `RunInputs.open` refuses a recorded run that breaks these rules (§16). All errors. |
| Warnings | `mask_missing` (images without a mask of a type that other images have: their objects of that type cannot be measured), `mask_without_image` (files matching no image of the experiment), `mask_of_excluded_image` (masks of excluded images, not used), `mask_outside_type_folder` (files directly in the masks folder that are not TIFF masks). |
| Run | `run_experiment(..., masks_folder=..., mask_axes=...)` stops on a mask error and records the masks in the manifest's `masks` key (§9). Without masks, the run and its outputs are unchanged. |

### 15.1 The mask step (`mask_relationships.json`, report version 4)

The optional mask step reads every image's masks, one image at a time, and describes each
mask type and every ordered pair of types over the whole experiment. Pieces are counted
8-connected (a pixel joined to its 8 edge and corner neighbours: the 3 × 3 structure of ones
`masks.binary.STRUCTURE`, imported from the one labeller's module, not restated, Spec ADR 0005
BM1; owner decision "8-connected everywhere", 11 November 2026; report
version 4: before, 4-connected, so two pieces of one label touching only at a corner counted as
two and a mask's `median_pieces_per_label`, and so its kind, could differ); label 0 is background and every object has a positive label: a mask holding
a negative value (possible in a signed integer type, which the header check accepts) raises
an error when its pixels are read, and no report is written, so the background or a negative
value is never counted as an object. Every conclusion is a suggestion with its evidence:
the user names the masks and confirms or corrects the relationships. The step holds the
output folder's lock (§9.2) from before the first mask is read to the write of the report;
another run holding it refuses the step (`output_locked`) before any mask is read.

**Strict binary (Spec ADR 0005 BM11, experiment-v1 §4.5 at `bf68d30`).** With a published
description, the files of every mask declared `binary` are checked first, **file-wide**
(`mask_representation.binary_refusals`): each file is read between two fingerprints, its planes
as stored (no label check), and the distinct values of all its planes are united (reading stops
once the union holds a negative value or two positive ones; a float file is refused from its
pixel type without a pixel read); `mask_rules.check_binary_values` then checks the stored dtype
first (signed or unsigned integers or booleans; never float, complex or object, even with whole
values) and then the values: {0, v} for one v > 0. An all-zero file (zero objects) and an
all-foreground file {v} are accepted. A refused file is `R_MASK_CONTRADICTS_REPRESENTATION`,
recorded as a `ValidationIssue` with the error's code and message verbatim and no action
(`{path} is declared binary but holds the values [3, 7]; ...`, the non-zero values found,
ascending, at most ten, or `... has pixel type float32; ...`), never recorded as `checked` in
`mask_representation.json` (so a request naming the mask is `A_MASK_UNCHECKED` without a read),
and left out of the scan and of `mask_relationships.json`; the other masks are scanned as
before and the report is written. A file whose planes hold {0, 3} and {0, 7} is refused with
`[3, 7]`, though each plane alone would pass. At the mask step the header's `mask_pixel_type`
error is not raised for a file of a mask declared `binary` (the binary rule refuses it with its
own code); `check` and `run` still refuse every float mask with `mask_pixel_type`.
`instances` and `parent_labels` files are recorded as checked once read (pixels prove neither).

**The value rule in the record (owner, 16 November 2026; Spec ADR 0002 AR10).** The record of
every mask declared `binary` in `mask_representation.json` names the rule its files were checked
by: `"value_rule": "BM11"` (`mask_representation.BINARY_VALUE_RULE`), between `representation`
and `files`; the status file's `version` stays `"1"`. A `binary` record without it (written by a
mask step older than BM11, v0.5.6 and earlier, whose rule "at most two distinct values" passed
a file holding {3} at one time point and {7} at another, which the per-plane read guard
`label_binary` also passes) or naming another rule counts as checked for none of its files
(`mask_representation.predates_value_rule`): a request naming the mask is `A_MASK_UNCHECKED`
before any read, with the message `The mask '{id}' is declared binary and its check for k of n
files was recorded by a masks step older than the binary value rule (Spec ADR 0005 BM11: one
positive value over all the planes of a file), so it is not checked. Run the masks step again on
this output folder (A_MASK_UNCHECKED).` (`mask_rules.stale_check_message`, the only
place of that text; `read_mask`, `read_mask_window` and `read_objects` give the same sentence
for one file, `({path})` in place of `for k of n files`, and the conversion helpers give the
resolver's sentence for their one file, `for 1 of 1 files`, as §15.2 says), and
`mask_check_status` and the manifest's mask status say `not yet checked`. Running the mask step again applies BM11 and
writes the rule. `instances` and `parent_labels` records carry no rule and are unchanged.

The masks are bound to their fingerprints (issue #165, as a run's sources, §9.4): the step
fingerprints each mask before and after its header is read (`prepare_mask_step`;
`mask_changed_during_check` when they differ), and the scan fingerprints each image's masks
before their first plane is read and again after their last. A mask that no longer has the
fingerprint taken with its header, that went missing, or whose fingerprint changed during
its scan raises `MaskChangedError` (a `ValueError`; the error `mask_changed_during_scan`,
naming the masks and what changed): no report is written, nothing half-written is left (the
report is written whole, by one rename, only after every image is scanned), and an earlier
`mask_relationships.json` is left as it was. With `verify_content` (`prepare_mask_step(...,
verify_content=True)`, CLI `masks --verify-content`; by default the recorded run's setting)
each mask's content SHA-256 is also taken before and after its scan and must not change.
Unchanged masks give the same report as before.

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
Each child object's parent is the parent object sharing most of its pixels. When two or more
parents share exactly that many (a child lying half in each of two cells), the child has
**no parent**, so that no count depends on how the objects are numbered (owner, 3 November
2026; before, the parent with the lower label was taken): it is never `inside`, and when
the tied share would have made it inside it is counted as `ambiguous_parent`. Each child is
counted once in `inside`, `ambiguous_parent`, `split_between_parents` or `outside_parents`.

| Relation of child to parent | Rule (default thresholds; the values used are written in the report) |
|---|---|
| `same_objects` | At least 90% of the children and 90% of the parents are matched one to one at an intersection over union of at least 0.5: two segmentations of the same objects. The matches are a maximum matching of the pairs at or above the threshold, in each plane: each object is matched at most once, and as many objects as possible are matched. Two children that each cover half of one parent are one match, not two (issue #67; before report version 2 every object with any partner at the threshold counted as matched). |
| `grouped_by` | The child is `grouped`, at least 90% of its objects are inside as below, and at least 99% of its pixels carry the label of the parent object under them. |
| `inside` | At least 90% of the child objects have at least half of their pixels in one parent object, and that parent is larger than the child for at least 90% of them. |
| `none` | Otherwise. |

| Evidence (per pair) | Meaning |
|---|---|
| `children`, `parents` | Objects of each type. |
| `inside`, `inside_parent_larger` | Children with at least half of their pixels in one parent, which is their parent (no tie); of those, children whose parent is larger. |
| `ambiguous_parent` | Children with at least half of their pixels in a parent, for which two or more parents share that many pixels exactly: they have no parent and are not `inside` (report version 3). `masks`, the notebook's **Describe the masks** and the relationship table name them. |
| `split_between_parents`, `outside_parents` | Children touching parents but with less than half of their pixels in any one (tied or not); children touching no parent. |
| `parents_with_0_children`, `parents_with_1_child`, `parents_with_2_children`, `parents_with_3_or_more_children` | How many `inside` children each parent holds (two nuclei in one cell counts as 2); a child with no parent counts for none. |
| `child_pixels`, `child_pixels_with_parent_label` | Child pixels; those whose label equals the parent label under them. |
| `children_matched_one_to_one`, `parents_matched_one_to_one` | Objects matched one to one (the maximum matching above, at an intersection over union of at least 0.5); the two counts are equal. The number of matches does not depend on which of several equally large matchings is found, so it is deterministic. |
| `children_not_matched`, `parents_not_matched` | Objects of each type left without a match. |
| `children_over_iou_with_several_parents`, `parents_over_iou_with_several_children` | Objects with two or more partners at or above the threshold: a split or merged segmentation. Above 0.5 an object can overlap only one partner that much, so these are 0 at a threshold above 0.5. |

**Thresholds.** The numbers above are defaults (`DEFAULT_THRESHOLDS`), and each may be
changed: `inside_pixel_share` (0.5), `inside_object_share` (0.9), `parent_larger_share`
(0.9), `same_objects_iou` (0.5), `same_objects_share` (0.9), `grouped_label_share` (0.99)
and `grouped_median_pieces` (2). Shares and the intersection over union must be above 0
and at most 1, the median number of pieces at least 1; an unknown name or another value
raises `ValueError` before any mask is read (`mask_thresholds`). The command line takes
one option per threshold (`--inside-object-share 0.8`, ...); the notebook's **Describe the
masks** uses the defaults (§9.2).

**Hierarchy.** A relation `inside` or `grouped_by` is **direct** unless another type lies
between: nucleoli inside nuclei inside cells have the direct parent nuclei, and the
relation nucleoli inside cells is kept in the report but not in the hierarchy. A type may
have several direct parents (cells inside an organoid and inside a region of the slide), and
two types that are the same objects are both kept.

**Cycles (issue #105).** Relations come from pairwise evidence summed over the images with
both masks, so types seen together in different images can form a cycle (A inside B, B
inside C, C inside A). No parent-child hierarchy can then be inferred, and none is guessed:
`MaskReport.hierarchy_cycles` names each group of types in a cycle, and the hierarchy text
(`mask_report_summary`, `mask_hierarchy_lines`, the notebook) starts with the named warning
`mask_hierarchy_cycle` listing every type and link of the cycle, then shows the group as one
`[cycle: A, B, C]` entry; every type is listed. `mask_relationships.json` is unchanged
(report version 1): its `hierarchy` keeps every direct link, cycles included.

The report is UTF-8 JSON (two-space indent, LF), written atomically to the output folder:

| Key | Meaning |
|---|---|
| `report_version` | `4` (pieces are counted 8-connected, owner decision "8-connected everywhere", 11 November 2026: a label whose pieces touch only at a corner is one piece). `3` since v0.4.0rc5 (a tie between parents gives no parent, and `ambiguous_parent` was added; plan §12 question 74). `2` since issue #67: the one-to-one matches are a matching, and the four counts of unmatched and several-partner objects were added. |
| `thresholds` | The thresholds used, all seven. |
| `images` | Images scanned (every image with at least one mask). |
| `masks` | Per mask type: `kind`, `images`, `objects` (labels over all images) and `median_pieces_per_label`. |
| `hierarchy` | Per mask type, its direct parents (sorted; empty for none). |
| `same_objects` | Pairs of types that are the same objects, each pair sorted. |
| `relationships` | Every ordered pair with a relation other than `none`: `child`, `parent`, `relation`, `direct` and `evidence`. |
| `pairs` | Every ordered pair of types found together in at least one image, `none` included, in the same form. |
| `versions` | As in the manifest (§9); information only. |

The step is the command `masks` and the functions `find_masks` and `scan_masks`; it reads
each image's masks once and holds only one image's masks in memory. Masks of one image with
different depth slices or time points (`mask_planes_differ`, §15) are an error; no 2-D mask
is shared by several planes (owner decision of 5 November 2026). A mask that cannot be read
as the planes its header gave, or holds a label outside 0 to 2,147,483,647 (§16), when its pixels
are read raises an error.

### 15.2 Mask conversion helpers (Spec ADR 0005 BM12, step 2b)

A mask is converted only on purpose, into **new** mask files; the source is never changed.
`RunInputs.binary_to_instances(fov, mask_id, *, output_mask_id)` and
`RunInputs.classes_to_binary(fov, mask_id, *, output_prefix)` (module `mask_conversion`; the
command `convert-mask` and the notebook card **Convert a mask** call them) take the opened run,
a position and a declared mask ID, never paths, and return a `MaskConversionResult`
(`operation`, `image`, `mask_id`, `source_path`, `representation` to declare each output with,
`outputs`: `ConvertedMask(mask_id, path, class_value, line, published)`, and `note`).

| Helper | Converts | Writes |
| --- | --- | --- |
| `binary_to_instances` | a mask declared `binary` (its file passed the file-wide binary rule), one Z plane, any number of time points | one `int32` mask `output_mask_id`: each plane `label_binary` of the source plane (8-connected, 1 to n), equal to `read_objects` plane for plane; declare it `instances` |
| `classes_to_binary` | a 2-D `(Y, X)` class image of signed or unsigned integers declared `parent_labels` or `instances`, classes 0 (background) to 2,147,483,647 | one `uint8` mask `<output_prefix>_class<value>` per class value present, ascending: 1 where the image holds it, 0 elsewhere; none, with the note `no class present`, for an all-zero image; declare each `binary` |

**Refused before any pixel is read and anything written** (a `ValueError` naming the mask ID
and file unless a code is named): an undeclared mask ID (`'{id}' is not a mask of this run's
description; it declares 'A', 'B'`); a label image inside an OME-Zarr (`'{id}' for {image} is a
label image inside an OME-Zarr; the conversion helpers convert masks in the masks folder only`);
a representation the helper does not convert; a file the mask step has not checked or whose
fingerprint is not the one recorded when it was checked (`MaskUncheckedError`,
`A_MASK_UNCHECKED`, the resolver's message for that file); `binary_to_instances` on a file with
more than one Z plane; for `classes_to_binary`, from the header, a file that is not one 2-D plane
or not of an integer type. Then the pixels: `binary_to_instances` refuses a plane `label_binary`
refuses (`R_MASK_CONTRADICTS_REPRESENTATION`, verbatim); `classes_to_binary` applies
`mask_conversion.check_class_image(pixels, *, mask_id, path)` (no `check_labels`), whose
messages follow `'{id}' ({path}) `: `has {ndim} dimensions (shape {shape}); a class image is one
2-D plane of class values; a one-hot or multi-channel stack is not converted`, `has pixel type
{dtype}; a class image holds non-negative integer class values` (bool included), `holds
negative values (smallest {v}); class 0 is the background and classes are positive`, `holds
class values above 2147483647 (largest {v}); a class value must fit an int32 label`. A source
that changes while it is read is `MaskChangedError`.

**Outputs and collisions.** An output goes to `<output mask ID>/<the image's folders>/<the
image's position ID>.tif` in the run's masks folder (the type-folder layout discovery pairs with
that image), whatever the source's layout, never in the experiment folder. An output mask ID (and
a prefix) must pass the mask ID rules of experiment-v1 §3.2 (Label, Token, NFC, `single`, the
reserved list and the `in` part). Refused, never overwritten, before anything is written: an
output mask ID the description declares; one differing only in case from a declared mask ID or
mask source type (§3.2 `E_ID_CASE_COLLISION`: it could never be declared next to it); a mask type of that name (any case; in either layout,
or recorded by the run) holding a file without a published lineage line of the same operation;
an existing output file, unless it is the **same conversion already published**: a lineage line
whose `source` equals the current source in every key, whose `output` `root`, `mask_id` and
`path`, `operation`, `connectivity`, `class_value`, `helper` and `helper_version` equal this
call's, and whose `output.content_sha256` is the file's digest now. That call is a no-op
(`published` False). Another source with identical pixels is a collision.

**Lineage** (`mask_lineage.jsonl`, one line per output in its mask-type folder, UTF-8, LF). Keys
in this order: `source` and `output` (each `root` = `"masks"`, `mask_id`, `path` relative to the
masks folder with `/`, `axes` as recorded, `shape`, `dtype` in little-endian NumPy form,
`content_sha256`), `operation`, `connectivity` (`8` / null), `class_value` (null / the class),
`helper` (`morphobricks_preprocessing.RunInputs.<operation>`), `helper_version` (the package
version). `content_sha256` is the lower-case hex SHA-256 of the UTF-8 bytes of
`json.dumps({"axes", "dtype", "shape"}, sort_keys=True, separators=(",", ":"))`, one `0x0A`
byte, then every plane's pixels in stored axis order, C order, little-endian
(`mask_lineage.content_sha256(pixels, *, axes)`); never the file's bytes or its fingerprint. The
reader (`mask_lineage.read_lineage`) refuses a line that is not such a record, or whose `root`
is not `"masks"`, naming it.

**Publication** is reader-consistent with rollback, not atomic, per output: (1) the pixels go to a temporary file in
the target folder (named `.<file name>.<random>.conversion.tmp`), synced, and re-read to check
the digest; (2) `mask_lineage.jsonl` is replaced by a synced temporary file holding its previous
lines and the new one; (3) the temporary file is renamed to the output. A failure in (1) or (2)
leaves no output, no line, no temporary file and no folder created by the call; a failure in (3)
restores the previous lines. A line whose output does not exist is pending: readers ignore it,
and the next publication of that output removes it and leftover temporary files first.
`classes_to_binary` publishes class by class; a retry reuses the classes already published and
continues. Mask discovery skips `mask_lineage.jsonl` and the temporary files; `RunInputs.open`
does not count a published output of a mask type the run did not record as an unrecorded mask
(no description of the run can declare it), so a retry can open the run again. The user then
declares each output (source type = the output mask ID), runs preprocessing and the mask step
again, and reads it like any mask. There is no lock: run one conversion of a masks folder at a
time.

### 15.3 Derived region masks: the H&E tissue mask (Spec ADR 0005 BM15)

An opt-in helper, never run by default, computes a `binary` **region** mask from one declared
RGB image with a named, versioned, fixed rule whose colour maths is normalization's
(`docs/design/he-tissue-mask.md`). `RunInputs.he_tissue_mask(fov, *, output_mask_id,
rule="he_tissue_v1")` writes it and returns `TissueMaskResult` (`image`, `mask_id`, `path`,
`line`, `published`, `reusable`, `fractions`); `RunInputs.preview_he_tissue_mask(fov, *,
rule="he_tissue_v1")` computes it in memory and returns `TissueMaskPreview` (`E`, `H`, `od`,
`od_floored` exactly as normalization returns them, the components, `fractions`, the rule record
and the code identities); it writes nothing. The command `tissue-mask` and the notebook card
**Make an H&E tissue mask** call them. scikit-image (the `he-mask` extra:
`pip install "morphobricks-preprocessing[he-mask]"`, which installs
`morphobricks-normalization[histology]`), normalization's `stain` module and the helper's module
(`he_tissue_mask`) are imported only when a method is called, never by `import
morphobricks_preprocessing`, `RunInputs.open` or another command.

**The rule `he_tissue_v1` is provisional** (rule record `status` `"provisional"`) until the
acceptance report of the design is accepted; freezing gives a new record and so a new mask under
a new output mask ID, never a relabelled one. On one `uint8` RGB plane, with `E` and `H` from
`stain.to_scalar(rgb, axes="CYX", preset="he_eosin")` (`image`, `exclusion`) and `od, od_floored
= optical_density(rgb_yxc(rgb, "CYX"), check_white((255, 255, 255)))` (normalization contract
§3.6), it excludes, each comparison made in float64 as written: nuclei `H >= 0.05` (pieces under
15 px removed, then dilated by a disk of radius 2); red blood cells `E >= 0.06`, `H < 0.03`,
`OD_G - OD_R >= 0.40`, `OD_B - OD_R >= 0.10` (pieces under 15 px removed, then dilated by a disk of
radius 1); background `max(OD) < 0.08` (opened by a disk of radius 2, then pieces under 1000 px
removed). `keep = not (nuclei or red_cells or background)`. Pieces are 8-connected, "under N"
means fewer than N pixels, a disk is `x*x + y*y <= r*r`, the structuring element is clipped by
the image border (never padded or wrapped; the opening's erosion counts only pixels inside the
image), and every size is in pixels: no pixel size is read or used.

**Inputs, refused before any pixel is read** (a `ValueError` naming the position): the position
is resolved by its image path from `RunInputs.fovs()`, and a caller's `image_type`, axes, counts
or dtype are ignored; an image that is not the run's (`RunInputsError`); a unit not declared
`rgb`; more than one Z plane or time point; a pixel type other than `uint8` or a C count other
than 3; a run without a masks folder (not for the preview); an unknown rule; scikit-image missing
(`ImportError` naming the extra); an output mask ID refused as in §15.2 (experiment-v1 §3.2, a
declared ID, a case collision with a declared mask ID or source type, a mask type holding files
without a published `he_tissue_mask` line); an existing output whose line differs from this call
in anything known before the read. The plane is read with `RunInputs.read_plane` and its
fingerprint checked again after the read.

**Output.** One `uint8` `(Y, X)` file, `1` kept and `0` excluded, at `<output mask ID>/<the
image's folders>/<position ID>.tif` in the masks folder, published with its lineage line as in
§15.2 (`publish_output`, the pending-line rollback). An all-zero mask is written as computed.
Declare it `representation = "binary"`, run preprocessing and the mask step again, and request
it as a region; it is never an object set (as objects it is `A_MASK_NOT_INSTANCES`). In memory,
never pass it as cell-mode labels: a `uint8` 0/1 array is one object there.

**Lineage line** (BM15 schema, same `mask_lineage.jsonl`), keys in this order: `source`
(`root` `"experiment"`, `image`, `axes` `"CYX"`, `shape` `[3, Y, X]`, `dtype` `"|u1"`,
`content_sha256` of the pixels read), `output` (§15.2's file object, `root` `"masks"`),
`operation` `"he_tissue_mask"`, `rule` (the complete canonical rule record, with the SHA-256 of
`skimage.color.hed_from_rgb` as imported, float64 little-endian C order), `rule_sha256` (SHA-256 of
`json.dumps(rule, sort_keys=True, separators=(",", ":"))`), `code` (`preprocessing` and
`normalization`, each `distribution`, `version`, `verified`, `record_sha256`, `vcs_commit`,
`reason`), `reusable` (both identities verified), `numpy_version`, `scikit_image_version`,
`scipy_version`, `image_type` `"rgb"`, `rgb_header_override` (`declared` and that image's
recorded override item, or null), `pixel_size` (the recorded X and Y size, unit and source, or
null; information only), `fractions` (`kept`, `nuclei`, `red_cells`, `background` of all pixels,
and `od_floored`), `helper` `morphobricks_preprocessing.RunInputs.he_tissue_mask`,
`helper_version`. A code identity is verified only for a non-editable install whose `RECORD` is
readable (its exact bytes hashed), every hashed file unchanged, at least one import package
placed by its `RECORD`, every `.py` or extension module of its import packages listed with a
hash, and every imported module of them loaded from a hashed `RECORD` entry; otherwise `verified` is false and `reason` names the first failed check.

**Retry (BM15's own comparator).** An existing output is the same computation, and the call is a
no-op (`published` False), only when both lines are `reusable`, they are equal in every key
except `output.content_sha256` and `fractions`, and the file still has its digest. Anything else
is refused, never overwritten: another source (equal pixels included), code identity (the same
commit with another `RECORD` included), library version or rule record (provisional and frozen
included), and every line with `reusable` false. An unverified mask is written but never reused.

**Reading.** `mask_lineage.read_lineage` selects the schema by `operation`: BM12's two operations
select §15.2's keys, `he_tissue_mask` selects these; a line of neither operation is refused, as
are BM12 keys under a BM15 operation and the reverse, a `root` of `"experiment"` anywhere but a
BM15 `source`, and a `rule_sha256` that is not its rule's digest. A key repeated in one JSON
object at any depth is refused in both schemas (it was kept silently before).

### 15.4 The H&E tissue mask of an array, with no run (roadmap K3)

`he_tissue_mask_array(rgb, *, rule="he_tissue_v1") -> TissueMaskArray` (owner decision of
17 November 2026: in preprocessing) computes §15.3's rule on one image array in memory, for
users who have pixels but no MorphoBricks run (a script, another brick's folder command). **The
rule is the same provisional `he_tissue_v1`**, and the computation is the run route's own: the
array is moved to `(3, Y, X)` and given to the one function `RunInputs.he_tissue_mask` and
`preview_he_tissue_mask` call after their read (`he_tissue_mask.rule_on_plane`), never a copy,
so the mask is bitwise equal to the file the run route writes for the same pixels (tested on the
synthetic image of the design, four PI-style images, the §1 boundary colours, and a 100 x 220
image whose parts sit on every threshold of the rule: for each pixel comparison the 8-bit colours
nearest it on both sides, and pieces of 14/15 and 999/1000 px; its mask is also checked against
the mask its parts specify, so moving any threshold past the nearest 8-bit colour, or a size or
radius by one, fails a test (issue #345). No 8-bit colour has a maximum OD between 0.07816 and
0.08020, so the background threshold can move within that gap with no mask changed; the rule
record's values are pinned by the tests themselves).

**Inputs, refused before any computation**, nothing converted and no axis guessed: an input that
is not a plain `numpy.ndarray` (a subclass such as a masked array, a list, `None`: `TypeError`;
for a subclass the message suggests `np.asarray(rgb)`); a rule that is not one ID, a `str` (a
list, `None`: `TypeError`, in the run route too);
a pixel type other than `uint8`, or a shape other than `(Y, X, 3)` (R, G, B last) with `Y, X >= 1`
(`ValueError` naming what was found); an unknown rule (`ValueError`); scikit-image missing
(`ImportError` naming `pip install "morphobricks-preprocessing[he-mask]"`). The input checks
import nothing of the extra; scikit-image, normalization's `stain` module and the helper's
module are imported only past them, never by `import morphobricks_preprocessing`. Like the run
route it takes no pixel size (sizes are pixels), no white (fixed at 255) and no thresholds.

**Result.** `TissueMaskArray` (frozen): `mask`, boolean `(Y, X)`, `True` kept (a region, BM3;
being boolean, an in-memory consumer refuses it as an object set, BM4); and `record`, a frozen
`TissueMaskArrayRecord`: `rule_id` (`"he_tissue_v1"`), `operation` (`"he_tissue_mask"`),
`rule_text` (the complete rule record in its canonical JSON, keys sorted and no spaces: exactly
the text `rule_sha256` is the SHA-256 of, as UTF-8; `rule` returns it as a new dict, with
`rule_version`, `status` `"provisional"` and `provisional`), `rule_sha256`, `code` (BM15's code
identities of `preprocessing` and `normalization`, read-only, `verified` and `reason` as found),
`code_verified` (both verified: the run route's `reusable`), `numpy_version`,
`scikit_image_version`, `scipy_version`, `fractions` (`kept`, `nuclei`, `red_cells`,
`background`, `od_floored`), `helper` `morphobricks_preprocessing.he_tissue_mask_array` and
`helper_version`. These values equal the run route's lineage line in every key the two share
(`operation`, `rule`, `rule_sha256`, `code`, the three library versions, `fractions`).
`to_dict()` gives them as plain JSON values, keys in that order, for a caller to record.

**Not a run artifact (Spec ADR 0005 BM15).** BM15 governs a derived region mask written in a
run: a file in the masks folder with its lineage line, declared by the user and checked by the
masks step. This function reads no run, writes nothing and makes no lineage line, so nothing it
returns can be declared, checked or reused as a run's mask, and its record has no `source`,
`output`, `image_type`, `rgb_header_override` or `pixel_size`. An unverified code identity is
stated, not refused (nothing is reused in memory). **For MorphoBricks runs the run route
(§15.3, with lineage) remains the route.**

### 15.5 Segmentation hand-off (masks made by an outside tool, by channel ID)

`morphobricks_preprocessing.segmentation_handoff` and `.segmentation_record` (numpy and
tifffile only; no segmentation tool is imported or required) are what a segmentation notebook
needs from this brick. They make no decision for the tool and guess nothing.

| Function | Rule |
|---|---|
| `images_to_segment(inputs, mask_type, masks_root)` | The run's images with no mask of that type, decided by **preprocessing's own pairing**: the masks folder and the label images inside OME-Zarr images (the discovery the run and the mask step use), checked by the run's own rules (`mask_names_differ_in_case`, `duplicate_mask`), so the list and the next run cannot disagree: either layout of §15, any case in folders, name and type (`cells` is `Cells`), any TIFF extension; an OME-Zarr image with a label image of the type has its mask and is not listed. Excluded images are not in the run, so never listed. Refuses a masks folder that breaks the folder rules (§15 Folder) or that the run itself would refuse (an ambiguous mask, a link leaving the folder), and a mask type that is not one folder name. |
| `segmentation_input(inputs, channel_ids, fov, z=0, t=0)` | The channels **by canonical ID** as a `(C, Y, X)` array of the stored values, in the order of `channel_ids`. Resolved with `RunInputs.resolve_request(Request(channel_ids))` and `require()`: every refusal of the resolver passes through with its code and message (`A_SCOPE_INCOMPLETE` for a channel some images lack: choose channels every image has; `A_RGB_NOT_SUPPORTED`). Each channel is read at the C index the image stores it at (`FovSelection.index_of`), never at a position. An image with Z or T above 1 is refused: 2D only. `resolve_segmentation(inputs, channel_ids)` resolves once for the whole run and returns a `SegmentationPlan` whose `read(fov, z=0, t=0)` gives the same array; `segmentation_input` resolves on every call, so a batch uses the plan. |
| `mask_path(masks_root, mask_type, fov)` | `<masks>/<type>/<the image's folders>/<position ID>.tif` (the first layout of §15). |
| `write_mask(masks_root, mask_type, fov, labels, inputs, *, overwrite=False)` | Checks the labels first, then writes: 2D, the image's Y and X (header only), an integer or boolean type (floating point is refused even when whole), values from 0 to 2,147,483,647. Writes `int32` with zlib compression and the axes named `YX`, to a temporary file moved into place, so a reader never sees half a mask. A mask that the run's pairing finds for this image and type (any case, either layout, any extension, or a label image inside an OME-Zarr image) is refused, naming it, unless `overwrite=True`; then only the file at `mask_path` is replaced, and a twin elsewhere is still refused. Without `overwrite` the name is first claimed with an exclusive create (`O_CREAT | O_EXCL`, which fails if anything is there) and the finished file is then moved over that empty placeholder, so two writers never replace each other's mask and no hard links are needed (a Google Drive mount has none); a failed move removes the placeholder (only while empty), and a process killed in between leaves an empty file that the next write refuses as an existing mask. The masks folder may not be inside the experiment or the output folder, nor hold either (the folder rules of §15 and `masks_inside_output`). Images with Z or T above 1 are refused. |

`<masks>/<type>/segmentation_record.json` (`SegmentationRecord`, `SegmentationBatch`,
`write_record`, `read_record`, `mask_sha256`, `record_version` 1) is provenance only and an
**append-only history of batches**, so a second batch (another model, other parameters) never
erases how the first batch's masks were made. A batch holds `batch_id` (unique in the file),
`tool` and `tool_version`, `model` (`name`, `file` or null, `sha256`), `parameters` (a JSON
object), `channel_ids`, `device` (`cuda`, `cpu` or `mps`, always stated), `run_folder`,
`run_manifest_sha256`, and `images`: one entry per image (its path relative to the experiment,
once per batch), `status` `written`, `skipped` or `failed`, a `message` (required for
`failed`) and `mask_sha256`, the SHA-256 of the file written (required for `written`, null
otherwise). `new_batch_id()` makes a batch ID that is unique (the UTC start time and 8 random hex characters, so two batches started in the same second differ); an ID chosen by the caller must be unique too, since a repeated one is refused only when the record is written, after the masks. `write_record` holds an exclusive lock file (`.segmentation_record.lock`, beside the record, created with `O_CREAT | O_EXCL` and removed at the end only while it still names this writer; the run's own lock, `lock_file.exclusive_lock`) while it reads, changes and rewrites the record, and refuses at once, naming the holder, when another writer holds it (it never waits; delete the file only when no writer is running). `write_record` adds a batch; the same `batch_id` again (a batch written as it
progresses) is accepted only if everything but the images is equal and no earlier entry is
dropped or changed, otherwise it is refused and the file is left as it was.
`SegmentationRecord.latest(image)` gives the newest written entry of an image. The record is
checked on writing and on reading; an unknown version, an unknown or missing key, a value
outside its set, a batch ID twice or an image twice in a batch is refused, never repaired.
The mask type is checked with the rules the masks step applies to a discovered type
(`check_identity_text`: no surrounding whitespace, not `NA`, `nan`, `none`, `null`, `n/a` or
`<NA>`; Unicode NFC; one folder name) before anything is written. The masks step skips this
file silently when it is directly in a type folder (it is not a mask; the same name deeper
warns `mask_without_image`), and nothing reads it to decide anything.

The helper refuses what the run would refuse, before writing: an ambiguous mask or a link
leaving the folder, **any discovered mask type** that cannot be a `source_type` (the run's
`_description_name_issues`: `R_MASK_TYPE_NAME_INVALID`, a decomposed name; also for a type
other than the one asked for), a mask type spelled in two ways (`mask_names_differ_in_case`)
and an image with two masks of the type (`duplicate_mask`). A mask is never silently replaced:
without `overwrite` the name is claimed exclusively, so it works on a file system without hard
links (a Google Drive mount) too.

`write_mask` holds one exclusive write lock per mask type, `<masks>/<type>/.segmentation_write.lock`
(the run's lock file mechanism, `lock_file.exclusive_lock`), from the check for another mask of
the image to the published file, **in both modes**: an overwriting writer and a default writer
can never replace each other's mask. A held lock is refused at once, naming its holder, and
never waited on; a killed writer leaves it, and the message says to delete it. Like the
record's lock it is a dot-file, skipped by the masks step. The optional `on_published(path)` callback runs right after the publish, still under the lock (take a hash and write the provenance there; it is not called when the write is refused). If it raises (any exception, `KeyboardInterrupt` too), the write is **rolled back under the same lock** and the error propagates: with `overwrite=True` the earlier mask was first copied to a hidden file beside it (`.<name>.previous.<random>`, a dot-file the masks step skips, removed once the callback returns) and is moved back over the new one byte for byte, with its mode; a new mask is removed. A rollback that cannot restore or remove is a `SegmentationError` naming both files, and the copy is kept. The rollback is unconditional: it happens on **every** exception from the callback, even one raised after the callback had durably committed its provenance, so a callback that commits must reconcile its own post-commit failures (keep the mask and not raise, or undo its commit) before it raises; `write_mask` has no committed-state protocol. The provenance record is written atomically (a temporary file replaced into place) but **not fsynced**: that protects readers from a half-written record, not a power loss, after which a record or mask may be missing or stale. A lock holder carries a random
token, so a lock deleted and retaken is never removed by its earlier holder. Limits: the lock
protects the writers of one computer (not a Drive folder open in two Colab sessions); a
reader may briefly see a **zero-byte placeholder** at the mask's name while a mask is being
written (the claim before the move); and preprocessing must **not be run on the masks folder
during a batch** (a run could read that placeholder or a half-written batch). A placeholder
left by a killed writer is deleted by hand.

The shipped Cellpose notebook builds on this hand-off and adds no rule of its own to the masks
(`segmentation_cellpose`, `segmentation_cellpose_model`; Cellpose, `torch` and `dinov3` are
imported only inside functions, so importing the brick and discovery stay inert). Cellpose
4.2.1.1 is pinned and is not a dependency. Each of its silent behaviours is a refusal here: a
model is one of `cpsam_v2`, `cpsam`, `cpdino`, `cpdino-vitb` or an existing file, checked before
loading, and the weights file the loaded model reports must be the one asked for; a device
(`cuda`, `mps`, `cpu`) is always stated and an unavailable one is refused. After loading, **every parameter and every
buffer of the loaded network (`model.net`) must be on a device of the requested type** (`cuda:1`
counts as `cuda`; the index is not compared); a network that lists no tensors is refused.
Cellpose's own `model.device` is not evidence (it only repeats the device it was given), and
nothing beyond the network's tensors is verified. The device is the placement of the network's tensors; Cellpose's own post-processing is not
covered by it: Cellpose may move the flow-threshold quality step to the CPU when GPU memory is
short, and always computes the masks from the flows on the CPU on `mps`; that is Cellpose's
behaviour, **not refused**, and the batch records it as a note. Uncached weights are downloaded only on an explicit request; more than
three channels are refused (Cellpose reads the first three by position); a blank diameter is
the model's native scale, never an estimate and never 0, and a diameter that gives fewer than one
flow iteration (`int(200 / rescale)` is 0) is refused, never raised to 1. The batch's `model` holds the model
name, the weights file name (or the custom file's path) and its SHA-256, and its `parameters`
every effective setting (diameter in pixels or null with its meaning, thresholds, the **full**
normalisation dictionary given to Cellpose (its defaults read from its module at call time, with
the user's values applied) and the flow iterations `niter` (`int(200 / rescale)`, passed
explicitly), batch and tile size, `channel_axis` 0, device type, versions, `weights_downloaded`
and `weights_source` (the Hugging Face URL Cellpose uses; null for a model file), the device
`notes` and, for a DINOv3 model, the DINOv3 commit). The batch ID is the record's
`new_batch_id()`. The DINOv3 package is installed with `--no-deps` at a pinned commit, the head
of its default branch on 22 November 2026, which is **not verified against `cpdino`**; Cellpose
imports it only when first imported, so the runtime is restarted after installing it.
Two-dimensional images only. **A mask the batch publishes is never left without its record
entry**: each mask's SHA-256 and the record write (the batch so far, the same batch ID growing)
happen in `write_mask`'s `on_published`, **under the type's write lock**, before the next image
starts (lock order: the type's write lock, then the record's lock; never the reverse). So a
second batch cannot publish this type between a mask and its record entry, and the latest record
entry of an image always names the bytes on disk. An entry that would not be the image's newest
(a batch recorded after this one started already wrote the image) is refused. If hashing,
that check or the record write fails, even by an interrupt, `write_mask` rolls the write back under
the same lock (an overwritten mask restored byte for byte, a new one removed; a rollback that
cannot is a loud error naming the files), the image is `failed` with the error and `rolled back:
earlier mask restored` or `rolled back: the new mask was removed`, the images not yet started are
`failed` as `not started`, and the batch stops (`BatchResult.stopped`). A retry without
`overwrite` shows an existing mask as `skipped` only when its SHA-256 is that of its latest
`written` entry; otherwise (no entry: `exists without provenance (not made by a recorded batch)`,
another SHA-256, a twin elsewhere, or not readable) it is `needs_attention` in the table and the
counts (recorded as `skipped` with that reason); the model that made it is not guessed.

## 16. Shared rules for other bricks (MorphoBricks D86)

Rules every MorphoBricks brick applies in one way, so they live here as public functions
(cross-brick decision D86, owner decisions of 30 October 2026) and other bricks call them
instead of keeping copies.

| Rule | Function | Detail |
|---|---|---|
| Length units (D-a2) | `space_unit(text)`, `to_micrometers(value, unit)`, `SPACE_UNITS`, `UnsupportedUnitError` (`physical_sizes`) | Microscopy units only: `pm`; `Å`/`angstrom`; `nm`; `µm`/`um`/`micron(s)`/`micrometer(s)`/`micrometre(s)`; `mm`; `cm`; `m`; with each name in US and British spelling and in the plural (`nanometre`, `millimeters`, ...). Symbols of two letters or fewer are matched exactly (`Mm` is not `mm`), longer names in any case; ImageJ's `\u00B5` is decoded. `space_unit` returns the OME-NGFF name (`"micrometer"`), None when no unit is given, and refuses any other unit by name (`UnsupportedUnitError`); `to_micrometers` converts a positive finite length (any real number: Python or NumPy integers and floats such as `np.float32`; a bool, complex or text is a `ValueError`, audit m4), None when the length or its unit is not known, from the shortest decimal of the value's double (`repr`) with its point moved by the unit's power of ten and rounded once (no binary factor, no `decimal` context): a value parsed from a decimal of at most 15 significant digits is the decimal written, so the same length written in any of these units gives the double that length written in micrometres gives (`9` nm is `0.009` µm and `108.37` nm is `0.10837` µm; audit B-m7: they were `0.009000000000000001` and, dividing by 1000, `0.10837000000000001`); at 16 or 17 digits two decimals can give one double, and the one written is not known; the result is always positive and finite: a length that overflows or underflows in micrometres (`1e308 m`, `1e-320 pm`), or a whole number beyond the float range, is a `ValueError` naming it (issue #93; they live in `length_units` since then, re-exported by `physical_sizes`). `PhysicalSizes.micrometers` converts with it, and is None for such a size. This brick's readers (§2) use the same table: since D86, ImageJ and OME-NGFF sizes in British spellings (`micrometre`) are calibrated, where they were unknown before. |
| Plain-TIFF resolution (D-c) | `read_physical_sizes` | A TIFF's resolution tags give a pixel size only with OME or ImageJ metadata and a unit; a plain TIFF (scanner or image-editor dpi) is uncalibrated, never converted from inches or centimetres. |
| Labels (D-b) | `check_labels(pixels, name)`, `LARGEST_LABEL` (`masks`) | A mask holds whole-number labels from 0 (background) to `LARGEST_LABEL` = 2,147,483,647: MorphoBricks labels are int32 in every brick. A negative label, a label above the limit, or a pixel type that is not integer or boolean is refused with a `ValueError` naming the mask and the value. `read_mask`, `piece_counts`, `pair_counts` and `scan_masks` call it. Since D86 the limit is int32; before, this brick refused only labels above 2^63 − 1 (int64), so a `uint32` or wider mask with a label of 3,000,000,000 is now refused. |
| Header axes (D-d) | `header_axes(meta)`, `named_axes(meta)`, `series_axes_from_metadata(series)`, `PLACEHOLDER_AXES`, `OPEN_AXIS` (`metadata`) | The axes an image's own metadata names, as `tifffile` reads them with length-1 axes left out (`tifffile` keeps Y and X even of length 1, so an image one pixel high is `YX`): OME, ImageJ and `tifffile` "shaped" metadata (`{"axes": "CYX"}`, decision D-d), a plain 2D `YX` (or `YXS` RGB) TIFF, and an OME-Zarr image's OME-NGFF axes. None when an axis is a placeholder (`I`: a plain stack of pages; `Q`: shaped without axes, or an OME-NGFF axis of another type), or the image does not open: the file does not say which dimension is depth, time or channel. `named_axes` gives the rule position by position, with `?` (`OPEN_AXIS`) for a dimension the metadata does not name: a placeholder, and, in a plain stack of pages without axis metadata (`IYX`, `QYX`, `QQYX`; `TiffMetadata.axes_from_metadata` False, from `series_axes_from_metadata`; True, the strict reading, for a header built by hand), the `Y` and `X` too, which only say where each page's rows and columns are stored (a (Y, X, C) array written without metadata reads as `QYX`). `header_axes` gives axes exactly when nothing is open. The axis check (§2) compares confirmed roles with `named_axes`. |
| Declared axes (removed) | `check_declared_axes`, `declared_axes_transposed` | **Removed in v0.4.0** (spec revision 4): they accepted a declaration with Y and X swapped and read it transposed, which v0.4 forbids. Bricks take no axis declarations; preprocessing's axis check (§2) compares the confirmed roles with the header. |
| Mask files | `read_mask_file(path, z=, t=)` (`masks`) | Reads one plane of a TIFF mask or of an OME-Zarr label image (`<image>.zarr/labels/<name>`) given by its path, with `read_mask`'s rules (§15): its axes are its own metadata's, Y and X plus at most a Z and a T axis named so, length-1 axes other than Y and X left out (a mask one pixel high or wide is read as a 2D array, plan §12 question 44); any other or unnamed dimension (a plain stack, `IYX`; a `C` axis) is refused, the message giving the header's axes and shape and the dimension that is not named, never taken for depth or time; `z` and `t` are required for a mask with that axis; integer or boolean pixels; labels checked with `check_labels`. A `ValueError` names the file and the problem. |
| Identity (D-e) | `FOV_KEY`, `check_identity_text(value, field)`, `check_plane_index(value, axis)`, `check_identity_case(keys)`, `MISSING_TEXT`, `IdentityError` (`discovery`) | `FOV_KEY` = (`condition`, `biological_repeat`, `technical_repeat`, `position_id`), the position key of §1. `check_identity_text` accepts a label or `position_id` that is text without leading or trailing whitespace and not `""`, `NA`, `nan`, `none`, `null`, `n/a` or `<NA>` in any case (`MISSING_TEXT`); `check_plane_index` accepts None or an integer of at least 0, a Python `int` or a NumPy integer (returned as a plain `int`), and refuses everything else (bool, float, `"1"`) without converting it. Both raise `IdentityError` (a `ValueError`) naming the field and the value. Discovery applies the text rule to every folder and image name (`invalid_name`) and refuses names of one level that differ only in case anywhere in the experiment (`case_conflict`, §1). `position_id` keeps its own scope: unique within its technical repeat, ignoring case (`duplicate_position_id`). `check_identity_case` applies the same case rule to identities a brick reads from a table, one key per row in `FOV_KEY` order (or the three labels): a label of one level spelled two ways anywhere in the run, or two `position_id` values of one technical repeat equal ignoring case, raise `IdentityError` naming both spellings; the same key may repeat (several planes of one position). |
| Sources of a run | `check_sources(manifest, experiment_root, relative_paths, image_axes=, identities=)`, `source_signature` (`inventory`), `file_sha256` (`manifest`) | The checks a brick makes before using a run's table for some images (formerly CellPatcher `check_images` and MorphoFeature `histogram_fit`); from v0.4.0 file runs read through `RunInputs` (§9.1), which also checks masks. Only a current manifest is read at all (§9, version 4). Each image must be in the manifest (`images_not_in_manifest`), still exist (`images_missing`), and be unchanged by `source_signature`, its size and modification time, for an OME-Zarr folder its files' total size and newest time, so a rewritten chunk counts (`images_changed`). Its recorded `position_id` must be the one its name gives; with `identities` (`FOV_KEY` order), its labels and `position_id` must be the caller's (`identity_differs`, also for an entry without a `position_id`). With `image_axes`, the caller's axes must give each axis the role recorded for the file (`FileEntry.roles`; an entry without roles fits no axes, nothing is resolved from the header in their place); an axis without a role (length 1) fits any letter (`axis_mapping_mismatch`). All errors, naming their images; the table's own hash is checked with `file_sha256` against `csv_sha256`. |
| Masks named as images (D-i) | `mask_name_issues(leaves, mask_names=())`, `images_named_as_masks(leaves, suffixes=MASK_NAME_SUFFIXES)`, `mask_folder_part(path)`, `MASK_NAME_SUFFIXES`, `MASK_FOLDER_WORDS` (`discovery`) | The screen MorphoRelate's `init` ran (`project_image_names`), now discovery's warnings `image_named_as_mask` and `mask_folder_in_experiment` (§1) for every brick. Names are compared in lower case across the whole experiment; the longest `position_id` owns a file, an image of the same folder first. `mask_names` adds the caller's expected mask names (`cells` for `f1_cells.tif`) to `MASK_NAME_SUFFIXES`; `discover_experiment` gives none. A folder counts as a masks folder when a word of its name (`CellLabels` is `Cell` and `Labels`) is in `MASK_FOLDER_WORDS`. `images_named_as_masks` returns the `(image, owner)` pairs of that detection for the caller's own name endings (`["_cells"]`), for a brick that acts on the files itself (MorphoRelate leaves out the images named with a declared mask name). |
| Pixel sizes (D-a) | `same_pixel_size(a, b, roles=("X", "Y"))`, `pixel_size_groups(sizes, roles=)`, `PIXEL_SIZE_TOLERANCE` (`physical_sizes`) | Two pixel sizes are the same when `|a - b| <= 1e-3 * max(a, b)` on every compared axis, in micrometres; one rule for a declared size against a header and for two headers. `same_pixel_size` takes `PhysicalSizes` (converted with `micrometers`) or sizes already in micrometres, one per role, and returns None when either is unknown (no size, or a unit that cannot be converted), never True. The rule is not transitive (1.0 ≈ 1.0009 ≈ 1.0018, but 1.0 ≠ 1.0018), so there is no per-image grouping key: `pixel_size_groups` links images by single linkage, so images of two groups always differ while the extremes of one group may drift; unknown sizes are left out; the largest group first. A size that cannot be converted to a positive finite length in micrometres (`1e308 m`) is unknown, never infinite, and an infinite or NaN value is never the same as any other (issue #93; before, `inf` matched every size). The folder check's `different_pixel_sizes` uses both (§11). |
| Experiment design | *(removed in v0.5.1, SWEEP-PP)* `read_confirmed_design`, `ConfirmedDesign`, `DesignCheckError`, `load_design` | `experiment_design.csv` is an output only (Spec ADR 0002 AR11 (3)); no brick reads it for its repeat groups. A brick takes `repeat_group` from the published description through `RunInputs` (`Fov.repeat_group`, `resolve_request`). The codes `missing_experiment_design`, `unreadable_experiment_design` and `changed_experiment_design` are no longer raised by this package (`RunInputs.open` still refuses a design file whose SHA-256 is not the manifest's). |
| Binary masks (Spec ADR 0005) | `masks.binary.label_binary(mask, *, name)` (also `label_binary`), `CONNECTIVITY`, `STRUCTURE`, `labelling_bytes(shape, dtype)` (§9.1 `objects_read_plan`); `masks.check_object_set(value, *, name)` (also `check_object_set`); `mask_rules.refuse_not_instances`, `refuse_binary_values`, `check_binary_values`, `MaskContradictsRepresentationError` | The one labeller of a `binary` mask (8-connected, 1 to n in row-major order of first pixels, `int32`, §9.1 `read_objects`) and the one constructor of each shared refusal. `check_object_set` refuses an in-memory object set that is not a plain `numpy.ndarray` of a signed or unsigned integer dtype with `A_MASK_NOT_INSTANCES`: the array message (`'cells' is a bool array, not an object set. ...`) or the non-array one (`'cells' is a list (not a plain NumPy array), not an object set. ...`); a boolean array is a region only. `check_binary_values(values, *, dtype, path)` is the binary value rule (§15.1). No other code builds these messages (a test scans the source). |
| Mask conversion helpers (Spec ADR 0005 BM12) | `RunInputs.binary_to_instances(fov, mask_id, *, output_mask_id)`, `RunInputs.classes_to_binary(fov, mask_id, *, output_prefix)`, `MaskConversionResult`, `ConvertedMask`; `mask_conversion.check_class_image(pixels, *, mask_id, path)`; `mask_lineage.content_sha256(pixels, *, axes)`, `read_lineage`, `LINEAGE_FILENAME` | New mask files written on purpose from a checked mask, with their `mask_lineage.jsonl` lines (§15.2); the source is never changed and nothing is overwritten |
| Analysis refusals (Stage 2) | `ChannelNotFoundError` (`A_SCOPE_INCOMPLETE`), `MaskNotInstancesError` (`A_MASK_NOT_INSTANCES`), `RgbNotSupportedError` (`A_RGB_NOT_SUPPORTED`), `DraftRefusedError` (`A_DRAFT_REFUSED`), `MaskUncheckedError` (`A_MASK_UNCHECKED`), `RunInputsError.code` (`run_inputs_error`, exported at the package top level) | The Spec's analysis-layer codes (experiment-v1 §8) as one class each, all `RunInputsError` subclasses (a `ValueError`) with the code as the class attribute `code` (`None` on `RunInputsError` itself), so a brick matches the class or the code and keeps no copy. Preprocessing raises `A_DRAFT_REFUSED`, `A_MASK_UNCHECKED` and `A_SCOPE_INCOMPLETE` itself (§9.1); `A_MASK_NOT_INSTANCES` (a mask that is not `instances` used as an object set) and `A_RGB_NOT_SUPPORTED` (`rgb` units given to a brick that does not declare RGB support) are found by `resolve_request` (§9.1) against the consumer's declared capability, and a brick raises them through `ResolvedRequest.require()` (or reports its `refusals`). A brick writes the code in its message text, as preprocessing does. The classes pickle, so a worker process can raise them. `RunInputs.section_digests()` (§9.1) gives the description's section digests for cache keys. `Fov.image_type` (§9.1) is each position's declared `rgb` or `channels`; there is no run-wide image type. `RunInputs.resolve_request()` (§9.1) is the one interpreter of a described run for every route: a brick builds a `Request` and reads through the `FovSelection`s it returns, so channel-by-ID, scope, mask-representation and RGB rules have one implementation; `VolumeNotSupportedError` (`A_VOLUME_NOT_SUPPORTED`) and `DescriptionRequiredError` (`A_DESCRIPTION_REQUIRED`, raised by `RunInputs.open`) are the resolver contract's own codes (MorphoBricks-Spec ADR 0002), not experiment-v1 §8's. |
| Experiment description (Stage 1) | `read_description(path)`, `description_consume.read_published_description(path)` (the published `morphobricks.toml` only), `run.description_file.publish_description` (the only writer), `draft.draft_from_discovery`, `description_reconcile.reconcile` | A brick reads the **published** `morphobricks.toml` through `RunInputs` (§9.1) and never a draft: a draft is internal to preprocessing, is never named `morphobricks.toml` and is refused with `A_DRAFT_REFUSED` (experiment-v1 §12). The command line shows the draft (`describe`, §12) and confirms nothing: the user's answers are the only way an ID, a repeat or a removal becomes confirmed. The owner's answers Q1 to Q8 of the Stage 1 design are in plan §12, question 78. |

## 17. Named-axis copies (`convert`, v0.4.0, design v0.4 §5)

The exception path for images whose axes are wrong or not named: they are saved again as
named-axis OME-TIFF copies with the roles the user confirmed, and the user then runs
preprocessing on the copies. `convert_experiment(experiment_root, mapping, destination,
exclude=, missing_as_singleton=, memory_limit=, progress=)` (`conversion`), the CLI
`convert EXPERIMENT --to FOLDER --axes ROLES [--output FOLDER]`. Masks are never converted (§15). `--output` is the output folder of the runs on the originals (default: the experiment's default one), named in the printed next commands and where the recorded masks folder is looked up (cross-brick test F8). A copy keeps its source's stored order of dimensions, so `convert` does not solve `inconsistent_dimensions`, and the message says so (plan §12 question 73).

| Rule | Detail |
|---|---|
| What is converted | Every image with an axis error (`axes_yx_swapped`, `axes_contradict_header`, `axes_x_before_y`, §2) or unnamed axes (`axes_not_named`) under the mapping given; it is never taken from the headers. Every other image is copied byte for byte: its header names its axes as confirmed, it is an OME-Zarr image (with its label images), or it is excluded (`exclude`: never read). So the copy tree is the whole experiment. The command and the API are offered for any experiment; an experiment without these images is copied as it is. |
| Checks first | The experiment is checked with the mapping as a run would check it (folders, headers, `review_axes`), from the headers only. The axis errors are what the conversion corrects; any other error stops it before anything is written, and so do images of a type OME-TIFF cannot hold (`cannot_convert_pixel_type`: `int64`, `uint64`; pixel values are never changed to fit) and images to convert whose planes cannot be read within the memory limit, not even in blocks (`check_planes`: `plane_over_memory_limit`, `compression_not_bounded`, `unsupported_layout`; `planes_read_in_blocks` is a warning). `memory_limit` must be a whole number of bytes above 0 and `missing_as_singleton` True or False (`ValueError` before anything is read); the CLI's `--memory-limit` (`run`, `convert`) is parsed exactly (`cli_run.parse_size`, no floating point): a size that is not a whole number of bytes above 0 (`1.5`, `0.5B`) is refused while the command line is read, before anything is read (`1.5GiB` is 1610612736 bytes; issue #80). One metadata epoch (issue #96): each image is fingerprinted before and after its header is read (`preflight(bind_headers=True)`); an image whose two fingerprints differ (or that went missing) is `source_changed_during_check` and nothing is written. The copy's axes, sizes, channel names, significant bits and pixel type come from that header (the header is not read again for them), and the fingerprint taken with it is the one recorded as the source's and checked before and after the copy is written. A plane the reader gives in another pixel type than that header's is refused (`conversion_failed`), never cast. The axis errors and `axes_not_named` are not reported: each converted image's record entry gives its code. |
| Copy tree | A folder the user names (`--to`), outside the experiment and not holding it (`copy_folder_overlaps_experiment`; a file is `copy_folder_not_a_folder`), and neither inside the masks folder, where the copies would be read as masks, nor holding it (`copy_folder_overlaps_masks`; the masks folder given with `--masks`/`masks_folder=`, else the one recorded by the run in the default output folder, or in the notebook the one checked in step 1, else the one recorded in its output folder; without one known, the run refuses copies inside its masks folder, `copies_inside_masks`, §15): holding no link or mount point below it (else `copy_folder_has_links`, nothing written: a write through one would land outside it; a folder below it that cannot be listed, or disappears during the check, is `copy_folder_unreadable`, named, #467), new or empty (else `copy_folder_not_empty`, nothing overwritten), or the copy tree of an earlier conversion of the same experiment. The originals are only read. Each image keeps its relative path and file name (an OME-TIFF may end in `.tif`), so `source_relative_path`, `plane_id` and the FOV key are the originals'. |
| Copies | Uncompressed OME-TIFF, BigTIFF when the pixels and 1 KiB per page (for its directory) would take more than 4 GiB less 32 MiB. Axes: every axis with a role in the source's order, then Y and X (`ZCYX`); an axis without a role (length 1) or missing (`missing_as_singleton`) is not added. Each plane is the plane the run reads under the confirmed roles (`iter_planes`), bit for bit: same pixel type (in native byte order), no rescaling, NaN and infinities kept; a Y/X swap and X before Y are written rows (Y) then columns (X). Planes over the memory limit are read in blocks, as the run reads them, and each block is written to its place: no plane is held whole when the run would not hold it. The copy's metadata names every axis, so `check` and `run` read it with `axes_from: "header"`. |
| Pixel sizes | They follow the roles; a relabelled axis gets an unknown scale, never a guessed one (`copy_axes`). A size the source header gives is written only for the role the header names it for, and only when the user confirmed that role for that same axis; otherwise it is left out and the record says why. One exception (owner decision of 2 November 2026, plan §12 question 59): a Y or X relabelled as the header's other one of Y and X (a Y/X swap, X before Y) keeps the header's size when the header gives X and Y exactly equal, same value and unit, since nothing can be exchanged; otherwise both are unknown in the copy (state them in `calibration.csv`, §9.3, if known). A Y/X swap therefore never exchanges the sizes of Y and X. A z-step is never written as a time step. A plain stack's Y and X confirmed Y before X keep the header's sizes. Units are written as OME symbols (`µm`, `s`, ...). Channel names are written when the C axis keeps its role and every channel is named; significant bits as they are. |
| Safe | The copy tree's lock (`.morphobricks-preprocessing.lock`, §9.2's rule; `--remove-stale-lock`) is held throughout (`run.lock.folder_lock`: unlike a run's lock it never finishes or discards a `.publish` folder, which in a copy tree is not a run's and is refused as any other file, #467); a second conversion is `output_locked` (a lock left by a conversion on this computer that is no longer running is called stale and kept, as in §9.2; cross-brick test F11). Each copy is written in the conversion's work folder, `.morphobricks-conversion.partial/files/<relative path>` in the copy tree, the source's fingerprint checked before and after; the conversion makes that folder (it must not exist) and writes its owner file `owner.json` (exactly `kind` `morphobricks-preprocessing conversion work folder` and the lock `holder`: `host`, `pid`, `started_at`, `token`) into it before any copy. Once every copy is written they are moved into place, then the record is written (atomic, with the file-mode rule of §9), and the work folder is removed, only while its owner file still holds this conversion's token (a lock removed by mistake may have let another conversion take it over; it is then left, #482). `not_enough_space` is refused before writing. Any failure or interruption removes the work folder, the copies already moved into place (each where nothing was) and the folders it made, so the folder is left as it was; a read or write failure is `conversion_failed`, anything else is raised again. A conversion killed before its clean-up leaves its work folder: the next conversion removes it only when it holds a well-formed owner file (a `holder` with a host, a process ID, a start time and a 32-hex-digit token) and nothing else but the folder `files`, and that holder is not a process still running on this computer (the lock's liveness check, `run.lock.holder_running`, POSIX only; in the same process, only while its conversion runs). A work folder that is empty, or holds only an empty owner file or one cut short (it begins as a conversion writes it but is not whole JSON), was left by a kill between making the folder and writing that file: it is removed by unlinking that one file and an `rmdir`, never refused. A live holder is refused as `conversion_work_folder_in_use`; anything else under that name as `conversion_work_folder_unowned`; nothing is removed then (#448, #482). Nothing else is removed: a `<name>.partial` file or folder beside a copy is not the conversion's, so without a record it is `copy_folder_not_empty`, and with one it is left as it is. Right before each copy is written and before each is moved into place, the folders on its way are checked again for a link or mount point (one `lstat` per level; `copy_folder_has_links`, everything written removed), a file found where a copy goes is never overwritten (`conversion_failed`), and the clean-up after a failure removes nothing through a link or mount point (#467). A link swapped in between that check and the write or move is not caught: writes relative to an open folder that follow no link (`dir_fd`, `O_NOFOLLOW`, `renameat`) are not available on Windows nor to the OME-Zarr writer, which writes by path. New files get the mode a plain `open()` gives. |
| Repeat | A copy already in the tree is reused when its source (fingerprint and axes), roles, copy axes, pixel sizes, unknown sizes (roles and reasons), channel names, significant bits, the copy's fingerprint and the copy's SHA-256 (read whole) are as recorded; a new image is added; when every copy is reused the record is kept as it was (same SHA-256). Anything else (a source changed, other roles, a copy changed, an image no longer in the experiment, another experiment's copy tree, a file at a new image's place that the record does not list) is `copy_differs`: nothing is overwritten. A conversion record that cannot be read, or of another version than 4, is `conversion_record_unreadable`. |
| Conversion record | `.morphobricks-conversion.json` in the copy tree's root (a dot-file, so discovery skips it), record version 4: `source_root`, `mapping`, `projection` (null, or `{method, z_range}`: the method and slice range of a tree of Z-projected copies, see "Projected copies" below), `missing_as_singleton`, `written_at`, `versions`, and per image `relative_path`, `action` (`converted` or `copied`), `reason` (the axis code, `excluded`, or null), `source` and `copy` fingerprints (`size`, `mtime_ns`), `copy_sha256` (the copy's SHA-256, computed in one pass after it is written; an OME-Zarr copy's is the SHA-256 of its files' relative paths and SHA-256s in path order, its `labels` group left out as for fingerprints: `content_hash`), `source_axes`, `roles`, `copy_axes`, `pixel_sizes` (by role, as §9), `unknown_sizes` (role: why), `channel_names` (as the copy holds them; `[]` for none), `significant_bits` (or null), `projection` (null, or the projected copy's provenance, below) and, only for an image converted or projected into an OME-Zarr copy, `source_relative_path` (below). Sources keep the fingerprint rule (design v0.4 §1); the copies' SHA-256 is the owner's decision of 2 November 2026 (plan §12 question 59 (16)). Version 2 (PP-11c) added `copy_sha256`, `channel_names` and `significant_bits`; version 3 (issue #176) added the record-level `projection` and the entry's `projection`, and the action `projected`; version 4 (owner decision of 12 November 2026, before v0.5.2) added the entry's `source_relative_path` of an OME-Zarr copy, which a version 3 reader would ignore, looking for the source at the copy's path and warning `conversion_source_changed`. Only version 4 is read (SWEEP-PP: no backward compatibility, Spec ADR 0002 AR10): a record of any other version, 1 to 3 included, is refused everywhere it is read (`convert`, the run's `review_conversion`, `RunInputs`), never converted, with a message to convert the originals again into a new folder. `read_conversion_record(folder)` reads it with its SHA-256. |
| Projected copies (issue #176, record version 3 and later) | A tree made with a Z projection holds, for every image, a 2-D copy of its Z-stack made with the record-level `projection` `{method, z_range}` (`max`, `mean` or `sum`; there is no default method; `z_range` is `[start, stop]`, zero-based, stop excluded, the same for every image, or null for every slice). An entry has the action `projected` and a `projection` object with exactly: `source_relative_path`, `source_content_sha256` (the source's content as read while writing), `source_z_axis` (`{letter, index}` of the header's Z axis, or null for an image without one, which is one slice), `source_z_size`, `z_range` (the slices actually used), `method`, `source_dtype`, `output_dtype`, `dtype_rule`, `rounding` (`none`, `float64, one division` or `float64 addition, z ascending`), `nan_policy` (`propagate`), `dropped_sizes` (`{"Z": {value, unit, source}}`, or `{"Z": null}` when the source gave no Z size: the Z size is never written to the copy) and `significant_bits_dropped` (a reason, or null: kept for `max`, dropped for `sum` and `mean`). Excluded images stay plain copies (`copied`, reason `excluded`). The output type depends only on the input type, the method and the slice count (never on the data): `max` keeps the type; `sum` of `uint8`/`uint16` is `uint32`, of `int8`/`int16` is `int32`, of `uint32`/`int32` is `float64` holding the exact integer, of floats is `float64`; `mean` is `float64`; 64-bit integers are refused (`cannot_convert_pixel_type`). A sum or mean whose worst case (the type's extreme times the slices) could not be held exactly is refused (`projection_cannot_be_exact`), never wrapped or clipped. A NaN makes its pixel NaN, infinities are kept. The reader refuses an entry whose output type or rounding is not the table's for its method, a projected entry in a record without a projection (and the reverse), and a method or range that is not the record's. The reuse key holds the projection (everything of it except the content SHA-256, which the write computes), so another method or range into the same tree is `copy_differs`. Masks are never projected: `convert_experiment(..., projection=Projection(...), masks_folder=)` scans the masks folder from the headers only and refuses, before writing anything, masks of the images to project that have depth slices (`projection_masks_3d`) or more than Y and X with axes that are not named (`projection_masks_axes_unknown`); 2-D masks are left alone. The other refusals, all before anything is written: `nothing_to_project` (no image has a Z role), `projection_z_range_outside`, `projection_cannot_be_exact`, `cannot_convert_pixel_type`, `projection_over_memory_limit` (one input plane, the running plane and, when it is another type, the output plane must fit `memory_limit`) and `projection_zarr_not_supported` (OME-Zarr images are not projected yet). The copy's OME-XML holds an `Image/Description` with the method, slices, source, source SHA-256 and types, and a UUID derived from it, so one projection of one source is the same bytes; the source's content SHA-256 is taken before and after the projection and the source fingerprint before and after, and a source that changed is refused. A sum that overflows to infinity (`projection_overflow`) or adds an infinity to its opposite (`projection_undefined`) stops the conversion and is never written. Excluded images are copied byte for byte. |
| OME-Zarr copies (v0.5.2, record version 4) | `convert_experiment(..., store="ome-zarr")` (the notebook's **Also save a copy as OME-Zarr**; default `"ome-tiff"`) writes every image that is read as an OME-Zarr image instead of an OME-TIFF: also the images whose headers name their axes as confirmed, which are otherwise copied byte for byte. Excluded images and OME-Zarr images are copied byte for byte as before. With `projection` the projected copies are OME-Zarr. A copy is named after its image with `.ome.zarr` in place of the TIFF extension, in the same folder, so its position ID is the same; two images that would get one copy name are refused (`copy_names_collide`). In the record the entry's `relative_path` is the copy's path and `source_relative_path` (a key written only for such an entry: required there and refused on any other entry, so a source is never looked for at a copy's path; other entries are written as before) is the source's path relative to `source_root`; `significant_bits` is null (OME-NGFF 0.4 has no field for it). The store is OME-NGFF 0.4 (Zarr version 2), written directly: one array `0`, uncompressed, little-endian, one chunk per plane (the whole Y and X), metadata as sorted JSON, so one copy of one source is the same bytes. Each plane is the plane read under the confirmed roles, bit for bit; the axes are the roles (`t`, `c`, `z`, `y`, `x`) with the units of the copy's sizes, the level-0 `scale` holds the sizes and 1 for an unknown one, which is named under the multiscale's `metadata.unknown_scale` and so read as unknown; channel names are under `omero.channels[].label`, each channel with the `color` (`FFFFFF`) and `window` (the pixel type's whole range as `min`, `max`, `start`, `end`) that OME-NGFF 0.4 requires, display only and never read from the pixels; a projected copy's description is under `morphobricks.description`. Reading the copies needs the `zarr` extra, as for any OME-Zarr image. Plan, lock, `.partial` writes, reuse, the copy's SHA-256 and the checks of the run on the copies are those of the other copies. |
| The run on the copies | The user runs preprocessing on the copy tree with the same output folder (`--output`): the recorded experiment root becomes the copy tree and the report says so (`experiment_root_changed`, and `conversion` with the record, its SHA-256 and `source_root`; the text says "named-axis copies of ..."); every row is recomputed (the row key includes the read path, §9); the design, image type and calibration are kept (same relative paths). The manifest records `conversion_record_sha256`, and for projected copies the `projection` summary (§9); the report says "max projections of slices 1:4 (2 images) of <source>" (and `conversion.projection` in `discovery_report.json`). Masks are paired as usual (the image has no Z now, so 2-D masks fit; a mask with depth slices stays `mask_planes_mismatch` and stops the run), and the run warns `masks_on_projected_images`, naming the method, for masks paired with projections, in the report and the manifest's `projection.warnings`. The run and `check` warn when a source changed or went missing after the conversion (`conversion_source_changed`) and refuse a copy that changed (its fingerprint or its SHA-256: each copy is read whole, so `check` and `run` on a copy tree cost one more read of the copies), went missing or was added since (`copy_changed_after_conversion`: an error since PP-12, owner decision of 2 November 2026; the run stops after the header checks, before any pixel is read, writing only its report, since `RunInputs` would refuse the run; the message says to convert the originals again into a new folder); an unreadable record stops the run (`conversion_record_unreadable`). `check` and `run` also read the OME `Image/Description` header (first page's description tag only, no pixels) of every analysed TIFF the record does not list as `projected`: a description that begins "MorphoBricks Z projection:" in a tree with no record, or whose record does not list the image as projected, is refused as `projection_record_missing` (owner decision of 8 November 2026; plain images are unaffected). `RunInputs` reads the copies like any images, and refuses a run whose conversion record changed or went missing since (`conversion_record_sha256`), is of version 1, or whose recorded images' copies no longer have the recorded fingerprint and SHA-256 (§9.1). |
| Notebook (PP-11b) | Step 2's **Make named-axis copies (optional)** card, under the axis check, shown only when it reports images with `axes_yx_swapped`, `axes_contradict_header`, `axes_x_before_y` or `axes_not_named` for the roles being edited (design v0.4 §5: offered only for files with errors), and not with `folder_step(show_output=False)`. The roles are the user's: those confirmed in step 2, or the roles shown once the preview is checked and the card's own question is answered; any change of a role or of the missing-axes answer takes it back. A required folder for the copies (Browse…, or pasted) checked with the command's rule (`copy_folder_issues`); `preview_conversion` (`conversion_plan`, headers only, writes nothing) gives what will be converted and copied byte for byte (the command's `plan_conversion`), the space needed and the free space; then `convert_experiment` with a progress bar, under the copy tree's lock. Refusals use the existing cards (the lock card with **Remove the lock of a stopped run**, the rule of `--remove-stale-lock`). After the copies: the record's path, and **Check the copies in step 1**, which sets the experiment folder to the copy tree, keeps the output folder and checks again; the card says that the missing-axes answer must be given again and lists the copies whose relabelled sizes are unknown (`ConversionResult.unknown_sizes`), to state under **Pixel sizes**. |

## 18. The description draft (`morphobricks.draft.json`, Stage 1)

The answers a user has given while describing an experiment, before any `morphobricks.toml` is published (experiment-v1 §10, §12). It lives in the output folder, is never named `morphobricks.toml`, has no `schema` key, and no consumer reads it as a description (`RunInputs` refuses a draft: `A_DRAFT_REFUSED`). It holds **answers only**: the folders, counts and proposals are derived again from the folder (`draft_from_discovery`) on every resume, so a changed folder is never hidden by an old file. Code: `draft_answers.py` (the answers, `apply_answers`, `description_from_draft`), `draft_format.py` (strict bytes), `run/draft_file.py` (`new_answers`, `save_draft`, `load_draft`, `publish_draft`).

One JSON object, UTF-8, two-space indent, sorted keys, LF, a final newline, no duplicate key and no `NaN`. Every key below is required; a key of this list is the only key allowed at its level.

| Key | Value |
|---|---|
| `format` | `"morphobricks-description-draft"` |
| `version` | `4` (an integer; versions 1 to 3 are refused as unknown versions: version 2 had no tagging states, version 3 no fast-mode statement (Spec ADR 0006); any other value is refused as an unknown version, never read as a newer or older one) |
| `base_sha256` | The lower-case hex SHA-256 of the exact bytes of the published `morphobricks.toml` the draft was derived from, or `"absent"`. Missing or any other value: `R_DRAFT_BASE_MISSING` |
| `spec_commit` | The 40-digit MorphoBricks-Spec commit the rules are pinned to (`SPEC_COMMIT`, equal to `tests/spec_fixtures/PIN.toml`) |
| `conditions` | Condition **source folder** → object with only the keys that were answered, from `id`, `display_name`, `reference` (the source folder of the condition it is compared with, or `""` when the user answered that it is compared with nothing; absent means not answered), `target`, `treatment_key` |
| `technical_repeats` | Technical-repeat source folder (`single` for none) → its confirmed ID |
| `repeats` | The pairing label of the draft's repeat (the folder name) → object with only the answered keys, from `id`, `confirmed_on`, `display_name` |
| `pairing` | Condition source folder → source repeat folder → the pairing label of the repeat the user paired that folder with (only folders not in the repeat of their own name; applied to the draft first, so the `repeats` keys are the paired labels; refused with `pairing_after_confirmation` once a repeat is named or confirmed, `unknown_key` for a folder the draft lacks). A reference to the condition itself or one that closes a loop is refused when saved (`E_REFERENCE_SELF`, `E_REFERENCE_CYCLE`). |
| `units` | `condition folder` → `source repeat folder` → `technical-repeat folder` → `{channels, image_type?, display_name?}`; `channels` lists, for C0, C1, ... in order, a channel ID (every position needs a label the user declared, for example `unused`; an empty string is refused) or `null` (not tagged yet); `image_type` is `"rgb"` or `"channels"` (an ordinary channel image; absent means not answered), any other value is refused |
| `channels` | Channel ID → `{display_name?, header_names?}` (the label list of the notebook's *Your channels* screen; `header_names` are the raw header names of the cells the user tagged with that label: the tag is the alias confirmation, of the name the cell's header has when it is tagged. At each tag the names no tagged cell has any more are dropped, and only the names of the cells just tagged are added: a tag saved while the header had another name never confirms the name it has now, so `R_HEADER_CONTRADICTION` stays until that cell is tagged again) |
| `masks` | Mask ID → `{source_type, representation, source_channels?, display_name?}` (the notebook's *Your masks* screen: one answer per mask type, `representation` `instances`, `binary` or `parent_labels` (*grouped labels*, Spec experiment-v1 §4.5b) with no default and never the mask step's `grouped` kind; the ID is the mask type's folder name when it is a valid mask ID, else typed by the user; `source_channels` are declared channel IDs) |
| `channel_layout` | `null` for a strict per-unit draft, else the user's fast-mode statement (Spec ADR 0006, experiment-v1 §4.3a): `{channels, image_type, confirmed_on}`, the ordered channel IDs of the tagged unit, `channels` or `rgb`, and the day of the statement. A unit's object then also takes `channels_from` (`experiment`: published with this list materialised, or `unit`: tagged on its own); a unit with no answer in a fast-mode draft is drafted `experiment` (a new unit, P15), never `unit`. A marker in a strict draft is refused (`channels_from_without_layout`) |
| `switch_mode` | `true` only when the user switched a published file between fast and strict mode (the notebook's tick); publication refuses a candidate whose mode differs from the published one without it, and a recorded switch that changes nothing (`mode_switch_unconfirmed`, experiment-v1 §10: no reader or reconciliation switches the mode). A published file reopens in its own mode: its `channel_layout` and markers are the draft's answers |

Rules.

- **A draft is the user's answers and nothing else.** A proposed ID is not an answer: an ID, a channel, a mask representation or a confirmation is in the file only if the user gave it. A repeat is confirmed when its `confirmed_on` is present; that day is the calendar day of the user's answer, given by the caller (experiment-v1 §4.4a): library code never reads the clock.
- **Publishing** (`publish_draft`, the notebook's *Check and save*) publishes under the lock and then writes the derived `experiment_design.csv` (Spec experiment-v1 §11); in a described run nothing else writes that file, so the draft (which carries the pairing) and the CSV cannot disagree. Reopening a published file with no draft takes its answers from the file; removed folders are removed only on the user's *Yes, remove* (`removals`).
- **Saving** (`save_draft`) is atomic (temporary file, one replace) and under the output folder's lock, after each answer. The answers are applied to the draft and the exact bytes are read back with the strict reader first, so an answer that would be refused on resume (a `spec_commit` other than the pinned one, `spec_commit_mismatch`; an ID the reader would refuse, a folder the draft does not have, a date that is not a real day) is refused when saved and nothing is written. An incomplete draft can be saved. The file is replaced, not merged. A symbolic link at the draft's path is refused and never replaced.
- **Reading** is strict and never repairs: bytes that are not UTF-8 JSON, a byte-order mark, a duplicate key, `NaN`, another `format`, an unknown `version`, an unknown key at any level, a missing key, a value of the wrong type or a date that is not a real `YYYY-MM-DD` day are `DraftFileError` (`code`: `malformed`, `unknown_version`, `unknown_key`, `R_DRAFT_BASE_MISSING`, `R_FILE_SYMLINK`). Reading accepts any 40-digit `spec_commit`; whether it is the current one is decided at resume and save, below.
- **Resume** (`load_draft`): the file's `spec_commit` must equal the pinned `SPEC_COMMIT`, else `DraftFileError` with code `spec_commit_mismatch` (owner decision, 2026-11-08): the confirmations in the file were given under other rules, so none is carried forward. The message names both commits, says the published `morphobricks.toml` is untouched and says to start a fresh draft from the folders. Library code never rewrites the stored commit and never deletes or replaces the stale draft; the caller derives a fresh draft and saves it, which replaces the file. `publish_draft` resumes first, so it refuses the same way and writes nothing. Then the file's `base_sha256` must equal the SHA-256 of the file published now (or `absent` if there is none), else `R_PUBLISHED_FILE_CHANGED` (`PublicationRefused`); the draft is never merged with the new file, it is derived again from it. An answer keyed by a folder or repeat the draft no longer has is `DraftError` (`unknown_key`), never moved to another folder. In the notebook's described run, a repeat's `confirmed_on` read from the draft file is kept only when every unit the repeat has now has its whole saved channel list and image type and no unit with a saved answer has left it (experiment-v1 §10: a new or removed unit or member reopens the confirmation); otherwise it is dropped and the repeat is asked again, and the card names it. The same holds for an answer given in the draft with no published file: a change of a unit's declared channel list, image type or grouping after the confirmation drops it.
- **Draft to description** (`description_from_draft(draft, answers, *, channel_counts=None)`, pure): refuses with `DraftIncomplete`, listing **every** missing confirmation once (`id_pending`, `repeat_not_confirmed`, `confirmed_on_missing`, `unit_channels_missing` for a unit with no channel list, `unit_image_type_unanswered` for a unit whose image type is not answered `channels` or `rgb` (an old draft without one reopens with the question asked, never as `channels`), `unit_channel_untagged` for a position still `null`, `channel_undeclared` for a channel a unit names that is not declared; with `channel_counts`, the channel count of each unit's image now read through the confirmed axis roles, also `unit_channels_stale` for tags saved for another count and `unit_channel_count_unknown` for a unit without a count), otherwise returns the `Description`. A unit tagging one label at two positions is refused by `apply_answers` before anything is saved (`E_DUPLICATE_CHANNEL_IN_UNIT`). Nothing is confirmed for the user. `used_ids` is the draft's retired IDs plus every ID confirmed now. The description is then validated by the strict reader at publication; the function does not repeat the reader's rules.
- **Publication** (`publish_draft(..., channel_counts=...)`, the counts required): under one hold of the lock the draft is resumed, the description built with the counts, published with all the checks of `publish_description` (symbolic link, base, transition, strict reader on the exact bytes), and then the draft file is deleted. Any refusal writes nothing: the draft and the published file stay byte for byte as they were. If the draft cannot be deleted after a successful publication, the error is raised and the publication stands; the leftover draft records a base that no longer matches and is refused on resume.

## 19. Image QC metrics (`image_qc.csv`, `qc_version` 1)

Per-plane image QC metrics, measured in the histogram pass from the pixels it already reads (no
extra read; MorphoFeature design `docs/restart/designs/image-qc-metrics-2026-11-21.md`, branch
`claude/restart-doc`). They describe how a plane looks, not how bright it is: they are flags for
review (a rare phenotype can look like a QC outlier), never a reason to exclude an image, and
nothing in this brick acts on them. Nothing is normalised before measuring; the raw
intensities, the extrema and the saturation stay in `plane_histograms.csv`.

**The table.** UTF-8 CSV, LF line endings, one row for each channel of each row of
`plane_histograms.csv` (the channels the plane's unit lists), sorted by `source_relative_path`,
`z`, `t` and `channel`. Columns: `position_id`, `image_file`, `z`, `t`, `condition`,
`biological_repeat`, `technical_repeat`, `source_relative_path`, `plane_id` (the histogram
table's values), `channel` (the canonical channel ID), `read_in_blocks` (`true`/`false`),
`qc_status`, the metrics below, `blank_reasons`, `qc_version`. A metric that is undefined for a
plane is **blank, never zero**; floats are written with `repr`. `qc_status` is `measured`,
`non_finite_pixels` (a float plane with NaN or infinite pixels: the pixel metrics are blank, the
histogram metrics use its finite pixels) or `no_finite_pixels` (every metric blank).

**Why a metric is blank (`blank_reasons`).** One cell per row: `metric=reason` for every blank
metric, in column order, separated by `;` (empty when no metric is blank). The reasons:
`no_finite_pixels` and `non_finite_pixels` (the two statuses above), `constant_plane` (the Otsu
metrics of a plane of one value), `no_pairs` (a correlation whose lag is not shorter than the
plane's height or width, or than a block's: no pixel pair that far apart), `no_variance` (a
correlation whose pairs are all one value), `non_positive_mean` (a ratio metric of a plane whose
mean `m <= 0`) and `plane_too_small` (a side under 3 pixels for `tenengrad` and
`laplacian_variance`, under 16 for the tile metrics). The reader (`read_image_qc`) and the writer
are strict: another header, status, version, flag, reason, or a value that is not a finite number
refuses the table, and so does a combination no measurement makes: a status with metrics it
blanks (`no_finite_pixels` with a value, `non_finite_pixels` with a pixel metric, `measured`
without an `entropy`), a blank metric without a reason or a reason for a metric that has a value,
the three Otsu metrics not blank together, and a value outside its range (`foreground_fraction`
and `otsu_separation` in 0 to 1, `entropy` in 0 to 6, correlations in -1 to 1, the ratio metrics
and `tile_mean_cv` not negative). `blank_reasons` was added before the first release, so
`qc_version` stays 1; a table written without it is not read and its rows are recomputed.

**The metrics** (`m` is the plane's mean over all its pixels; every ratio metric is divided by
`m` or `m²`, so multiplying the intensities by a constant, or an 8-bit, 16-bit or float copy of
one image, gives the same value; the ratio metrics are blank when `m <= 0`). **Scale-free only
under pure multiplication.** A pedestal or background offset (`I -> a * I + b`, a camera offset,
an uneven background that was not subtracted) changes `m` to `a * m + b` and leaves the variance
and the differences at `a²` times theirs, so it changes `normalized_variance`, `tenengrad`,
`laplacian_variance`, `local_focus_median`, `local_focus_p10` and `tile_mean_cv` (all divided
by `m²` or by a mean): the same sample with more background reads as less sharp and less
uneven. The correlations, `otsu_fraction`-type shares (`foreground_fraction`, `otsu_separation`)
and `entropy` do not change under an offset. Nothing is subtracted before measuring (the raw
intensities are in `plane_histograms.csv`), so compare these metrics between images that
share an offset (same camera and settings), or read them next to the histogram table's minimum
and median. **Related metrics:** `tenengrad` is, up to the pixels at the plane's edge,
`normalized_variance * (1 - ρ₂)`, where `ρ₂` is the Pearson correlation of pixels two apart
(the mean of the two directions): the mean squared central difference is `σ²(1 - ρ₂)`
(tested), so Tenengrad is the variance seen at the finest scale and adds the structure scale
that `correlation_1` and `correlation_4` give; they are not independent evidence of focus.

| Column | Definition |
|---|---|
| `otsu_threshold` | Two-class Otsu threshold on the plane's histogram (each bin at its representative value, a robust-range tail at its mean; exact for integer histograms with one bin per value, the default for 8- and 16-bit images): the value of the highest bin of the lower class. Blank for a plane of one value. |
| `otsu_separation` | Otsu's separability: between-class variance at the threshold over the total variance (0 to 1). Two-class Otsu always splits. **Baseline:** on unimodal noise it splits the noise at its middle, giving `foreground_fraction` about 0.5 and a separation of `2/π ≈ 0.637` for Gaussian noise (0.635 to 0.66 for the Poisson and Gaussian noise tested). A sparse field of bright cells reads about 0.93, but a dense field with a unimodal histogram (smoothed noise, tested) reads 0.64 as well, so **`otsu_separation` does not tell an empty field from dense content** (it measures how two-class the histogram is, not how much is in the field); use `correlation_1` for that. |
| `foreground_fraction` | Share of the finite pixels above `otsu_threshold`. |
| `entropy` | Shannon entropy in bits of the histogram regrouped into 64 equal bins from the plane's minimum to its maximum (relative to the plane's own range; 0 for a plane of one value). |
| `normalized_variance` | `var / m²` (population variance; the squared coefficient of variation). CellProfiler's FocusScore is `var / m`, which changes with the intensity scale; divided by `m²` it is scale-free (design principle). |
| `tenengrad` | `(mean(gx²) + mean(gy²)) / m²` with central differences `gx = (I[y, x+1] - I[y, x-1]) / 2` over every pixel that has both neighbours in X (and `gy` likewise in Y). This is a central-difference Tenengrad, **not** the classical Tenengrad of the 3 x 3 Sobel operator (Krotkov): values are not comparable with Sobel-based ones. See the relation to `normalized_variance` above. |
| `laplacian_variance` | Variance of `I[y-1,x] + I[y+1,x] + I[y,x-1] + I[y,x+1] - 4 I[y,x]` over the pixels with all four neighbours, `/ m²`. |
| `correlation_1`, `correlation_4`, `correlation_16` | Pearson correlation of every pixel with its neighbour `k` pixels to the right, and (separately) `k` pixels below, over every such pair; the mean of the two. Blank when either direction has no pair (a plane not wider and taller than `k`, or read in blocks no more than `k` rows or columns across) or a constant side. These are Pearson correlations of pixel pairs at a distance, **not** CellProfiler's `MeasureTexture` "Correlation", which is a Haralick feature of a grey-level co-occurrence matrix of quantised intensities. **The emptiness marker:** `correlation_1` is about 0 (under 0.05 in absolute value, tested on Gaussian and Poisson noise) for an empty field of camera noise, where neighbouring pixels are independent, and 0.86 to 0.88 on the sparse small-cell test fields and at least 0.9 on fields with extended or blurred structure (tested); the Otsu metrics cannot show this. Optics and sensor binning can correlate noise, so read it as a flag, not a verdict. |
| `local_focus_median`, `local_focus_p10` | Each tile of a fixed 8 x 8 grid (tile edges at `floor(i * side / 8)`): its population variance `/ m²` (the plane's mean, so a dark tile is not inflated); the median and the 10th percentile over the 64 tiles (NumPy's default linear interpolation). Blank when a side is under 16 pixels. **`local_focus_p10` is content coverage, not focus:** when more than about 6 of the 64 tiles hold only background, the 10th percentile is the variance of that background (over `m²`), so it falls with the share of the field that is empty, not with blur (a sparse field of cells reads low at any sharpness). Read focus from `local_focus_median` on dense fields and from `tenengrad` and `laplacian_variance`. |
| `tile_mean_cv` | Population standard deviation of the 64 tile means over their mean: uneven content or illumination (never interpreted as illumination alone). |

**Planes read in blocks** (`read_in_blocks` true). Every metric is a combination of sums that add
up exactly across blocks (counts, means and centred sums of squares and products, combined
pairwise), and the histogram metrics come from the histogram, so the histogram, mean, variance
and tile metrics equal the whole plane's (to float64 rounding). The neighbour metrics
(`tenengrad`, `laplacian_variance`, the correlations) leave out the pairs that cross a block
edge: a small difference for full-width bands of many rows, larger for long lags in narrow
blocks (tested: within 5 % for Tenengrad and the Laplacian and 0.05 for the correlations on
cell-like test fields in blocks down to 17 x 33 pixels). **These tolerances are observations on
those fields, not guarantees.** An image whose strongest change lies on a block edge (a stitching
seam, a saturated band, a hard crop) can differ much more: a step exactly at a band edge drops
`tenengrad` and `laplacian_variance` to a fraction of the whole plane's (tested), and the blocks
are set by the memory limit and the file's strips or tiles, not by the image. A lag that no pair
spans (a correlation at lag `k` in blocks of at most `k` rows or columns) is blank with the reason
`no_pairs`, never zero (tested with blocks of 16 rows and lag 16). Compare these metrics only
between planes read the same way (`read_in_blocks`, and the same memory limit). Planes under the
memory limit are read whole.

**Numerics.** For an integer image every sum is a whole number held exactly in float64 on planes
of the usual sizes (below `2**53`), so the result does not depend on the summation order. For a
float image the sums are centred on the block's mean (an integer image on its rounded mean, which
keeps the sums whole) and taken in a fixed order: the same machine gives the same bytes; another
processor may change the last digits. Workers do not change the table. The sums use
`numpy.vecdot`, so the dependency floor is NumPy 2.0 (`pyproject.toml`; the other floors were
raised with it to releases that import with NumPy 2: SciPy 1.13, Matplotlib 3.8.4 and, for the
`he-mask` extra, scikit-image 0.24).

**Memory.** The working memory of the QC sums is part of the estimate of a whole plane and of a
block (`image_qc.qc_memory`, added by `plane_binning.Binning`, so a plane is read in blocks when
the histogram and the QC sums together do not fit the limit; it also enters the workers' per-image
estimate): the float64 band buffer of `min(64, block height) + 16` rows of the block's width, the
column sums, the thin strips of the window sums, NumPy's casting buffers and, for a float block,
the one-byte-per-pixel mask of the finite scan. It is an upper bound, checked against the measured
peak (`tracemalloc`) over blocks of different shapes and types.

**Reuse and resume.** The table is an output of the histogram step and follows its rules (§9,
§9.4): reused rows keep their QC rows; staged rows are staged with theirs; an earlier table that is
missing, changed (SHA-256), of another `qc_version` or settings, or not holding exactly one row for
each channel of each histogram row, with that row's full identity (position, image, Z, T, labels,
path, plane ID and channel, not only plane ID and channel), recomputes every row, with the reason
in the report. Changing the metric definitions changes `qc_version`. **The memory limit** changes
no histogram row but sets the blocks, and so the neighbour metrics of planes read in blocks: when
the earlier run or this one reads any plane in blocks, another `memory_limit` recomputes every row
(`rebuild_reason`: "the memory limit changed from X to Y ..."); with whole planes on both sides it
changes nothing. Staged rows record the limit in their key for an image read in blocks
(`RowKey.block_limit`) and are resumed only under it (reason in `staged_discarded`). The limit is
compared, not the resulting partition: a change that leaves the blocks as they were still
recomputes.

**Not in version 1:** the power-spectrum slope (PLLS: one FFT per plane, measured at 29-62 ms for
a 2048 x 2048 plane against a histogram step of about 74 ms per channel plane, over the design's
+50 % limit), background noise or SNR, stripes, bright artefacts, near-duplicates, time-lapse and
Z profiles. The channel-identity check does not use the metrics yet.

## 20. Header reading for other tools

Other tools (for example the OpticalFlow3D Profiler, on single-channel TZYX time-lapses) may read
image headers and run the header checks without a run. The stable entry points are:

| Entry point | What it gives |
|---|---|
| `inspect_tiff(path)`, `inspect_image(path)` (also `.zarr`) | A `TiffMetadata`: shape, dtype, axes the header names, channel names, series count, physical sizes; no pixels are read |
| `read_physical_sizes(path)` | A `PhysicalSizes`: pixel sizes X and Y, z-step, time step (`t`, with `time_unit`) and a unit per axis; ImageJ `finterval` and OME `TimeIncrement` are read |
| `check_metadata(files, allow_z=True)`, `inspect_images(root, paths, allow_z=True)` | The `ValidationIssue` list of the header checks (§8) |

Z is read: ImageJ and OME TZYX stacks give axes `TZYX` and the Z size. Only preprocessing's own
experiment checks refuse Z by default (`z_stack_not_supported`, §2): `check_metadata(files)` and
`inspect_experiment` keep that refusal, and `allow_z=True` switches off that one error; unreadable
files, several series, pixel types, arrangement, channel counts and names, sizes, plane counts,
pixel sizes and units are checked as before. `case_conflict` is not a header check: it comes from
folder discovery (`discover_experiment`, §1), so it is not reported from headers alone. Moving the
Z gate out of the header checks is issue #438.

The base install depends on `morphobricks-normalization` through a git URL (a release tag, not on
PyPI), so a tool that installs this package needs git access to that repository.
