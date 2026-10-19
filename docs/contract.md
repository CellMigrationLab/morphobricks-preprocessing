# Preprocessing Brick Contract (Milestone 0)

This document fixes the rules other bricks can rely on. Changing any of them requires a
new `schema_version`.

## 1. Folder hierarchy and labels

```
<experiment root>/<condition>/<biological repeat>/<technical repeat>/<image>.tif
```

| Rule | Detail |
|---|---|
| Leaf folder | A folder that contains images and no subfolders. Only TIFFs directly in leaf folders are analysed. |
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
| Error | Mixed dtypes; unreadable TIFF (header or pixel data) in a leaf folder that is not excluded; every image excluded; more than one image series in a file; unsupported sample format or axis; missing/duplicate X or Y, duplicate roles, unassigned non-singleton axis, inconsistent mapping; plane over the memory limit; folder named `single`; sibling folders differing only in case; folder below the technical-repeat level; output folder inside the experiment; no usable planes | Processing is blocked |
| Warning | Non-TIFF files; empty folders; TIFFs outside leaf folders; images excluded by the user, or excluded paths that match no image; channel names differing between files; non-finite pixels; different image sizes with a valid mapping; planes without finite pixels while others remain | Processing continues; the item is reported |

Every message states what happened in plain language, lists the affected paths, and
suggests an action. Technical details go to the diagnostic log.

## 9. Run manifest (`run_manifest.json`, manifest version 1)

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
| `manifest_version`, `schema_version` | Manifest format (`1`) and CSV format (`1`). |
| `written_at` | UTC time of writing, ISO 8601. |
| `experiment_root` | Absolute path of the experiment folder, for information. |
| `dtype` | Pixel type of the experiment. |
| `settings` | `bin_count`, `memory_limit` (bytes), `missing_as_singleton`, `exclude` (sorted relative paths). |
| `axis_mapping` | `axes` (file axis labels) and `roles` (`X`, `Y`, `Z`, `C`, `T` or null per axis). |
| `labels` | One entry per leaf folder: `folder` (relative path, `.` for the root) and its three labels. |
| `files` | One entry per analysed file, sorted by path: `relative_path`, `size` (bytes), `mtime_ns`, the three labels, `axes`, `singleton_roles`, `plane_count`, `channel_names` (list, null for unnamed channels; empty if none in metadata), `significant_bits` (null if not in metadata). |
| `csv_sha256` | SHA-256 of the table written with this manifest. |
| `versions` | Versions of `morphobricks-preprocessing`, `numpy`, `tifffile` and Python. |

Channel names and significant bits come from OME metadata and are for information only.
