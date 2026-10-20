# Preprocessing Brick Contract (Milestone 0)

This document fixes the rules other bricks can rely on. Changing any of them requires a
new `schema_version`.

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
| Reserved name | A folder named `single` (in any upper/lower case) is an error. |
| Uniqueness | Folder names need only be unique among siblings, ignoring case: siblings whose names differ only in case are an error. |
| Depth | At most three folder levels below the root. A folder below the technical-repeat level is an error. |
| System files | Dot-files and dot-folders (`.DS_Store`, `._*`, `.ipynb_checkpoints`), `Thumbs.db`, `desktop.ini` and `__MACOSX` are ignored without a warning. |
| Scope | A group is identified by its full label path, e.g. `(control, bio_rep1)`. |
| Paths | `source_relative_path` is relative to the experiment root, with `/` separators. |

## 2. Axes

Roles X, Y, Z, C, T. Exactly one X and one Y; each role at most once. Non-singleton axes
must have a supported role. One confirmed mapping per experiment. Plane coordinates `z`,
`c`, `t` are zero-based and blank when the axis is absent.

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
| Experiment mapping | Defined on the TIFF arrangement when the experiment has TIFF files; in an experiment of OME-Zarr images only, on their arrangement with the most axes. |
| Channel names, significant bits | Recorded in the run manifest when metadata provides them; not part of the CSV. Channel names that differ at the same channel index are a warning. |

## 3. Pixel types

| Kind | Types | Default bins | Allowed bins |
|---|---|---|---|
| Integer | int8/16/32/64, uint8/16/32/64 | min(65,536, 2^bits) | 1 to min(65,536, 2^bits) |
| Float | float32, float64 | 10,000 | 2 to 65,536 |

Everything else (bool, complex, float16, ...) is rejected. One dtype per experiment. All
planes of an experiment use the same bin count.

## 4. Histogram semantics

Notation: `n` = `bin_count`, `lo` = `binning_min`, `hi` = `binning_max`.

### Integer planes

`lo` and `hi` are the plane's exact minimum and maximum. `W = hi − lo + 1` values.

- If `W ≤ n`: bin `i` holds exactly the value `lo + i` for `i < W`; bins `W .. n−1` are
  empty. The histogram is exact.
- If `W > n`: bin `i` holds the values `lo + ceil(i·W/n)` to `lo + ceil((i+1)·W/n) − 1`.
  Limits are whole numbers, consecutive bins touch without gaps or overlaps, and widths
  are `floor(W/n)` or `ceil(W/n)` values.
- Representative value of a bin: `(first value + last value) / 2`.
- Bound: every pixel is within `(widest bin − 1) / 2` of its representative.

### Float planes

`lo` and `hi` are the plane's finite minimum and maximum; NaN and ±infinity are ignored.

- Bin width `(hi − lo) / n`. Bin `i` is `[lo + i·width, lo + (i+1)·width)`; the last bin
  also includes `hi`. A pixel `x` goes to bin `floor((x − lo) / width)`, clipped to
  `n − 1` (edge assignment is subject to floating-point rounding).
- Constant plane (`lo == hi`): all pixels in bin 0; representative `lo`.
- No finite pixels: `lo`, `hi` blank; all counts 0.
- Representative value: the bin midpoint `lo + (i + 0.5)·width`.
- Bound: every pixel is within `width / 2` of its representative.

## 5. CSV schema (`plane_histograms.csv`, schema version 1)

UTF-8, comma-separated, one header row, one row per plane, rows in a deterministic order.

| Column | Type | Meaning |
|---|---|---|
| `position_id` | text or blank | From reliable metadata only; never inferred from file names. |
| `image_file` | text | File name. |
| `z`, `c`, `t` | integer or blank | Zero-based plane coordinates; blank when the axis is absent. |
| `condition`, `biological_repeat`, `technical_repeat` | text | Folder labels; `single` when absent. Always read as text. |
| `source_relative_path` | text | Path relative to the experiment root, `/`-separated. |
| `plane_id` | text | `source_relative_path`, plus `::z=…,c=…,t=…` for the coordinates present. Row key. |
| `dtype` | text | NumPy dtype name, e.g. `uint16`. |
| `bit_depth` | integer | Storage bits (8, 16, 32, 64). |
| `pixel_min`, `pixel_max` | number or blank | Exact finite extrema. Floats written at full precision. |
| `total_pixel_count` | integer | All pixels in the plane. |
| `valid_pixel_count` | integer | Finite pixels; equals the sum of the bin columns. |
| `binning_min`, `binning_max` | number or blank | Range the bins cover (equal to the extrema). |
| `bin_count` | integer | Number of bin columns. |
| `schema_version` | text | `1`. |
| `histogram_bin_00000` … | integer | Pixel count per bin. |

## 6. Group statistics

Groups pool their planes' (representative value, count) pairs; per-plane statistics are
never averaged.

| Statistic | Definition | Accuracy |
|---|---|---|
| min, max | Smallest `pixel_min`, largest `pixel_max` | Exact |
| mean, std | Count-weighted mean and population std (÷N) of representatives | Exact for exact histograms; otherwise very close (errors cancel) |
| percentile q | Smallest representative with cumulative count ≥ ceil(q/100 · N) (numpy `inverted_cdf`) | Within the group's largest per-plane bound |
| median | 50th percentile | As percentiles |
| MAD | Median of \|representative − median\| | Within twice the bound |

Planes without finite pixels are excluded and reported; a group with no finite pixels is
an error.

## 7. Normalization parameters

Every method is an offset and a scale; applying it computes `(pixels − offset) / scale`.

| Method | Offset | Scale |
|---|---|---|
| min-max | min | max − min |
| percentile range (default 1–99.8) | low percentile | high − low percentile |
| z-score | mean | std |
| robust z-score | median | MAD × 1.4826 |

A zero or non-finite scale is an error with a plain-language message.

## 8. Validation severities

| Severity | Situations | Effect |
|---|---|---|
| Error | Mixed dtypes; unreadable TIFF (header or pixel data) or OME-Zarr folder in a leaf folder that is not excluded; OME-Zarr images, or converting TIFF images to OME-Zarr, without the `zarr` extra; OME-Zarr images whose declared axes do not fit the experiment's roles; every image excluded; more than one image series in a file; unsupported sample format or axis; missing/duplicate X or Y, duplicate roles, unassigned non-singleton axis, inconsistent mapping; plane over the memory limit; folder named `single`; sibling folders differing only in case; folder below the technical-repeat level; output folder inside the experiment; no usable planes | Processing is blocked |
| Warning | Files that are not images; empty folders; images outside leaf folders; images excluded by the user, or excluded paths that match no image; channel names differing between files; non-finite pixels; different image sizes with a valid mapping; planes without finite pixels while others remain | Processing continues; the item is reported |

Every message states what happened in plain language, lists the affected paths, and
suggests an action. Technical details go to the diagnostic log.

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
| `manifest_version`, `schema_version` | Manifest format (`1`) and CSV format (`1`). |
| `written_at` | UTC time of writing, ISO 8601. |
| `experiment_root` | Absolute path of the experiment folder, for information. |
| `dtype` | Pixel type of the experiment. |
| `settings` | `bin_count`, `memory_limit` (bytes), `missing_as_singleton`, `exclude` (sorted relative paths), `convert_tiffs` (whether TIFF images are converted to OME-Zarr and processed from the copies; false when absent). |
| `axis_mapping` | `axes` (file axis labels) and `roles` (`X`, `Y`, `Z`, `C`, `T` or null per axis). |
| `labels` | One entry per leaf folder: `folder` (relative path, `.` for the root) and its three labels. |
| `files` | One entry per analysed file, sorted by path: `relative_path`, `size` (bytes), `mtime_ns`, the three labels, `axes`, `singleton_roles`, `plane_count`, `channel_names` (list, null for unnamed channels; empty if none in metadata), `significant_bits` (null if not in metadata), `ome_zarr_path` (for a TIFF converted to OME-Zarr, the copy relative to the output folder; null otherwise or when the key is absent). For an OME-Zarr image, `size` is the total size of its files and `mtime_ns` the time of its newest file. |
| `csv_sha256` | SHA-256 of the table written with this manifest. |
| `versions` | Versions of `morphobricks-preprocessing`, `numpy`, `tifffile` and Python. |

Channel names and significant bits come from OME metadata and are for information only.

## 10. Converting TIFF images to OME-Zarr (optional)

When `convert_tiffs` is on, every TIFF image is converted to OME-Zarr in the output folder
and its rows are computed from the converted copy, like for any OME-Zarr image. The copy
holds the source values, so the rows equal those of reading the TIFF directly; the
`source_relative_path` of the rows stays the TIFF's path. OME-Zarr input images are never
converted.

| Rule | Detail |
|---|---|
| Location | `<output>/ome_zarr/<source_relative_path without .tif/.tiff/.ome.tif>.ome.zarr` |
| Format | OME-NGFF 0.4, Zarr format 2, `/` chunk separator, Blosc compression |
| Axes | `t`, `c`, `z`, `y`, `x` in that order, only the roles of the confirmed mapping (missing axes resolved as singletons have length 1; axes without a role are dropped) |
| Level 0 | The source values, unchanged, in the source pixel type; chunks of one plane, at most 1024 x 1024 pixels |
| Further levels | 2 x 2 block means rounded to the pixel type, until a plane fits in 512 x 512 (at most 8 levels); for viewing only |
| Scale | Physical pixel sizes, z-step and time step from OME or ImageJ metadata, with OME-NGFF units; 1 when unknown |
| Channels | `omero` block with channel names from metadata (or `Channel <i>`) and each channel's observed minimum and maximum as display window |
| Updates | Copies of unchanged images are kept, of new or changed images rewritten, and of images no longer analysed removed; a failed run leaves the earlier copies as they were (see §9 for publication) |
| Switching | Turning conversion on or off recomputes every TIFF row; turning it off removes the copies the manifest recorded |
| Missing copy | A converted TIFF whose copy was deleted counts as changed and is converted again |
