# MorphoBricks Preprocessing

[![CI](https://github.com/CellMigrationLab/morphobricks-preprocessing/actions/workflows/ci.yml/badge.svg)](https://github.com/CellMigrationLab/morphobricks-preprocessing/actions/workflows/ci.yml)

The preprocessing brick of MorphoBricks. It reads an experiment folder of
microscopy TIFFs, computes one intensity histogram per 2D plane, and writes them,
with each plane's experimental labels, to `plane_histograms.csv`. Later bricks
(for example patch extraction) use that table to calculate normalization
parameters for any group of images without rereading the pixels.

The brick never modifies source images and never applies a normalization itself.
The normalization methods live in this package so other bricks can import them.

## Status

Pre-alpha. Implemented and tested:

- Per-plane histograms for all supported pixel types (`histogram`)
- Reading and writing the histogram table (`csv_io`)
- Group statistics pooled from histograms (`pooling`)
- Normalization parameters and their application (`normalization`)

Planned (see [docs/plan.md](docs/plan.md)): folder discovery and validation, TIFF
metadata and axis review, the run manifest and incremental updates, the
notebook interface and the command-line interface.

## Installation

```bash
pip install "git+https://github.com/CellMigrationLab/morphobricks-preprocessing.git"
```

For development, from a clone:

```bash
pip install -e ".[dev]"
```

The optional `notebook` extra installs the widget dependencies.

## Example

```python
import numpy as np
from morphobricks_preprocessing import (
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
    histogram=compute_plane_histogram(plane),
)
write_plane_histograms([record], "plane_histograms.csv")

# In a later brick: pool any group and derive normalization parameters
records = read_plane_histograms("plane_histograms.csv")
for key, members in group_records(records, "condition").items():
    pooled = PooledHistogram(members)
    params = percentile_range(pooled, low=1, high=99.8)
    normalized = params.apply(plane, clip=(0, 1))
```

## How the statistics work

- Every plane is binned over **its own minimum to maximum**, with the same number
  of bins for every plane. Adding images never changes existing rows.
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
