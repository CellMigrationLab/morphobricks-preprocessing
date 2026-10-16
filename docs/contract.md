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
| Reserved name | A folder named `single` is an error. |
| Uniqueness | Folder names need only be unique among siblings. |
| Scope | A group is identified by its full label path, e.g. `(control, bio_rep1)`. |
| Paths | `source_relative_path` is relative to the experiment root, with `/` separators. |

## 2. Axes

Roles X, Y, Z, C, T. Exactly one X and one Y; each role at most once. Non-singleton axes
must have a supported role. One confirmed mapping per experiment. Plane coordinates `z`,
`c`, `t` are zero-based and blank when the axis is absent.

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
| Error | Mixed dtypes; unreadable TIFF in a leaf folder; unsupported sample format or axis; missing/duplicate X or Y, duplicate roles, inconsistent mapping; plane over the memory limit; folder named `single`; output folder inside the experiment; no usable planes | Processing is blocked |
| Warning | Non-TIFF files; empty folders; TIFFs outside leaf folders; non-finite pixels; different image sizes with a valid mapping; planes without finite pixels while others remain | Processing continues; the item is reported |

Every message states what happened in plain language, lists the affected paths, and
suggests an action. Technical details go to the diagnostic log.
