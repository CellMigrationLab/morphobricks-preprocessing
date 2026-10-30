# MorphoBricks Preprocessing

[![CI](https://github.com/CellMigrationLab/morphobricks-preprocessing/actions/workflows/ci.yml/badge.svg)](https://github.com/CellMigrationLab/morphobricks-preprocessing/actions/workflows/ci.yml)

The preprocessing brick of MorphoBricks. It reads an experiment folder of
microscopy TIFFs, computes one intensity histogram per channel of every plane, and writes
them, with each plane's experimental labels, to `plane_histograms.csv`: one row per plane
(an image at one depth slice and time point), each channel in its own columns. Later bricks
(for example patch extraction) use that table to calculate normalization
parameters for any group of images without rereading the pixels.

The brick never modifies source images and never applies a normalization itself.
The histograms, the table format, pooling and the normalization methods come from the
[normalization brick](https://github.com/CellMigrationLab/morphobricks-normalization)
(`morphobricks_normalization`), installed with this package; other bricks import them
from there.

## Status

Pre-alpha. Implemented and tested:

- Folder discovery and validation (`discovery`, `validation`)
- TIFF header inspection without reading pixels (`metadata`)
- Dataset consistency warnings in the folder check: images per condition and biological
  repeat, repeats missing from some conditions, differing plane counts and pixel sizes,
  and an error for differing numbers of channels (`dataset`, `metadata`,
  `physical_sizes`)
- Axis mapping: prefill from metadata, validation, applying one mapping to every
  file (`axes`)
- Reading TIFFs one 2D plane at a time, with a memory limit and a preview plane, and the
  channels of a table row one after another; planes too large for the limit are read in
  blocks with identical results (`planes`)
- The experiment design: pairing biological repeats across conditions, confirming they
  are comparable, and marking controls, in `experiment_design.csv` (`design`)
- The image type, fluorescence or RGB: suggested from the metadata (RGB colour samples),
  confirmed by the user and recorded in the run manifest (`image_type`)
- Running an experiment end to end and updating earlier output incrementally, with a
  run manifest (`run`, `manifest`)
- Intensity checks of the written table: images far from the others of their biological
  repeat, shifted biological repeats, saturated images and images without spread, with
  plots per channel in the notebook (`intensity`)
- Masks (optional): pairing every image with its masks of any number of kinds (a masks
  folder with one folder per kind, and OME-Zarr label images), checking them from their
  headers and recording them in the run manifest (`masks`); and a mask step that reads them
  and tells how the kinds relate (inside, grouped by, same objects, with the counts behind
  it, such as cells with two nuclei) and their hierarchy, in `mask_relationships.json`
  (`mask_scan`, command `masks`). A mask is 2D, or has depth (Z) and/or time (T) planes
  named in its own metadata, one per image plane; a 2D mask of a Z-stack or time-lapse is
  refused, projections included (`mask_planes_mismatch`), and the manifest records each
  mask's `axes` and `shape`
- A step-by-step notebook ([notebooks/MorphoBricks_Preprocessing.ipynb](notebooks/MorphoBricks_Preprocessing.ipynb))
  and a command line (`morphobricks-preprocessing check` / `run` / `masks`)

From the normalization brick (`morphobricks_normalization`, a required dependency, pinned
to its release v0.3.2): per-plane histograms with the robust range and the range check
(`histogram`, `ranges`), the histogram table (`csv_io`, schema version 4), pooled group statistics
(`pooling`) and normalization parameters (`normalization`). Importing these
names from `morphobricks_preprocessing` still works for one version, with a
`DeprecationWarning`.

Optional (the `zarr` extra): OME-Zarr images as input (`zarr_input`), and converting TIFF
images to OME-Zarr before processing them (`zarr_export`). **New users: start with the
[user guide](docs/user_guide.md).**

## Installation

```bash
pip install "git+https://github.com/CellMigrationLab/morphobricks-preprocessing.git"
```

For development, from a clone:

```bash
pip install -e ".[dev]"
```

The optional `notebook` extra installs the widget dependencies for the notebook
(`pip install "morphobricks-preprocessing[notebook] @ git+https://github.com/CellMigrationLab/morphobricks-preprocessing.git"`).
The notebook's Step 0 can set this up automatically: it uses a nearby checkout or an
installed package, and installs missing requirements into the active kernel. A standalone
download installs from GitHub if needed (internet access and Git required).

From a terminal:

```bash
morphobricks-preprocessing check /data/exp1
morphobricks-preprocessing run /data/exp1 --axes ZCYX --image-type fluorescence
```

## Running an experiment

```python
from morphobricks_preprocessing import preflight, run_experiment

check = preflight("/data/exp1")  # folder and headers only, no pixels read
for issue in check.issues:
    print(issue)
mapping = check.suggested_mapping  # review and edit, e.g. mapping.with_role(0, "T")

result = run_experiment("/data/exp1", mapping)  # writes to /data/exp1_MorphoBricks_Preprocessing
print(result.written, result.read, result.unchanged)
```

Rerunning only reads images that are new or changed; `force_rebuild=True`
recomputes everything, and `exclude=[...]` leaves out damaged files. The notebook and the
command line also ask for the experiment design (which biological repeats pair across
conditions, and that they are comparable) and write `experiment_design.csv`; in Python,
pass it with `design=` (see the [user guide](docs/user_guide.md) §9). Later bricks read it
with `read_experiment_design`. They also ask for the image type, `fluorescence` or `rgb`
(suggested by `review_image_type`), and record it in the manifest; in Python, pass it with
`image_type=`. After writing, the run checks the images' intensities from the table
(`result.intensity`); `check_table(path)` checks any table.

## Example

```python
import numpy as np
from morphobricks_normalization import (
    PlaneRecord,
    PooledHistogram,
    compute_plane_histogram,
    group_records,
    percentile_range,
    read_plane_histograms,
    write_plane_histograms,
)

plane = np.random.default_rng(0).integers(100, 4000, (512, 512), dtype=np.uint16)
record = PlaneRecord(
    source_relative_path="control/bio_rep1/tech_rep1/img_01.tif",
    condition="control",
    biological_repeat="bio_rep1",
    technical_repeat="tech_rep1",
    histograms=[compute_plane_histogram(plane)],  # one per channel
)
write_plane_histograms([record], "plane_histograms.csv")

# In a later brick: pool one channel of any group and derive normalization parameters
records = read_plane_histograms("plane_histograms.csv")
for key, members in group_records(records, "condition").items():
    pooled = PooledHistogram(members, channel=0)
    params = percentile_range(pooled, low=1, high=99.8)
    normalized = params.apply(plane, clip=(0, 1))
```

## How the statistics work

- Every channel of every plane is binned over **its own minimum to maximum**, with
  the same number of bins for every plane. Adding images never changes existing rows.
- Integer bins have **whole-number limits**. When a plane's range fits within
  the bin count (always the case for 8- and 16-bit images with the default
  65,536 bins), the histogram is exact.
- Groups are combined by **pooling pixel counts**, never by averaging per-image
  statistics. Exact minimum and maximum come from the stored extrema.
- No percentile can be off by more than half the widest bin in the group.

The full rules are in [docs/contract.md](docs/contract.md). The simulation study
behind these choices is in [experiments/binning_study](experiments/binning_study).

## Development

```bash
ruff check .
ruff format .
pytest
```

Coding conventions are described in [docs/development.md](docs/development.md).

## License

MIT, see [LICENSE](LICENSE).
