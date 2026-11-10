# MorphoBricks Preprocessing

[![CI](https://github.com/CellMigrationLab/morphobricks-preprocessing/actions/workflows/ci.yml/badge.svg)](https://github.com/CellMigrationLab/morphobricks-preprocessing/actions/workflows/ci.yml)

The preprocessing brick of MorphoBricks. It reads an experiment folder of
microscopy TIFFs, computes one intensity histogram per channel of every plane, and writes
them, with each plane's experimental labels, to `plane_histograms.csv`: one row per plane
(an image at one time point), each channel in its own columns. MorphoBricks v1 analyses 2-D
planes: a Z-stack (more than one depth slice) is refused (`z_stack_not_supported`); make
recorded 2-D projected copies first with `convert --project max|mean|sum`, and use the copies. Later bricks
(for example patch extraction) use that table to calculate normalization
parameters for any group of images without rereading the pixels.

The brick never modifies source images and never applies a normalization itself.
The histograms, the table format, pooling and the normalization methods come from the
[normalization brick](https://github.com/CellMigrationLab/morphobricks-normalization)
(`morphobricks_normalization`), installed with this package; other bricks import them
from there.

## Status

Pre-alpha; the current release is v0.5.0 (what is planned next is in
[docs/roadmap.md](docs/roadmap.md)). Implemented and tested:

- Folder discovery and validation, TIFF and OME-Zarr header inspection without reading pixels
- Dataset consistency warnings, calibration (never guessed) and axis mapping, with named-axis
  OME-TIFF copies for images whose axes are wrong or unnamed (command `convert`)
- Reading images one 2D plane at a time within a memory limit
- The experiment design, the image type and intensity checks of the written table
- Optional masks: pairing, header checks, and a mask step that writes `mask_relationships.json`
- Running an experiment end to end, updating earlier output incrementally, resuming
  interrupted runs and publishing results in one crash-consistent step
- Reading a run from another brick: `RunInputs.open(output_folder)`
- A step-by-step notebook ([notebooks/MorphoBricks_Preprocessing.ipynb](notebooks/MorphoBricks_Preprocessing.ipynb))
  and a command line (`morphobricks-preprocessing check` / `run` / `masks` / `calibrate` /
  `describe` / `convert`)
- **Every run is described** (v0.5.1): the output folder must hold a published experiment
  description, `morphobricks.toml`, written by the notebook's describe steps (*Check and
  save*). A run without one is refused with `A_DESCRIPTION_REQUIRED` on every route.

Every feature, message code and output file is described in the [user guide](docs/user_guide.md).

From the normalization brick (`morphobricks_normalization`, a required dependency, pinned
to its release v0.4.0): per-plane histograms with the robust range and the range check
(`histogram`, `ranges`), the histogram table (`csv_io`, schema version 4), pooled group statistics
(`pooling`) and normalization parameters (`normalization`); import them from
`morphobricks_normalization` (the deprecated paths through `morphobricks_preprocessing` were
removed in v0.4.0).

Optional (the `zarr` extra): OME-Zarr images as input (`zarr_input`). **New users: start with the
[user guide](docs/user_guide.md).**

**Upgrading:** there is no backward compatibility before the first pipeline release (Spec
ADR 0002 AR10). Every run needs a published experiment description, and a run written before
v0.5.1 is refused until it is run again; nothing is converted. See the [CHANGELOG](CHANGELOG.md).

## Installation

```bash
pip install "git+https://github.com/CellMigrationLab/morphobricks-preprocessing.git@v0.5.0"
```

For development, from a clone:

```bash
pip install -e ".[dev]"
```

The optional `notebook` extra installs the widget dependencies for the notebook
(`pip install "morphobricks-preprocessing[notebook] @ git+https://github.com/CellMigrationLab/morphobricks-preprocessing.git@v0.5.0"`).
The notebook's single runnable cell can set this up automatically: it uses a compatible nearby
checkout or installed package, and installs missing requirements into the active kernel. A standalone
download installs from GitHub if needed (internet access and Git required).

### Notebook: Google Colab and JupyterLab

[![Open in Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/CellMigrationLab/morphobricks-preprocessing/blob/main/notebooks/MorphoBricks_Preprocessing.ipynb)

[`notebooks/MorphoBricks_Preprocessing.ipynb`](notebooks/MorphoBricks_Preprocessing.ipynb)
is one portable widget app for Google Colab, JupyterLab and LabConstrictor. It follows
LabConstrictor's
[notebook portability guide](https://github.com/CellMigrationLab/LabConstrictor/blob/main/.tools/docs/notebook_portability.md):

1. The notebook is intentionally small: one short Markdown introduction and one `# @title …`
   runnable cell. That cell prepares the environment and explicitly displays the complete
   preprocessing UI; users never edit code, so LabConstrictor may hide it.
2. Colab-only setup stays inside `if "google.colab" in sys.modules:` and mounts Google Drive.
   The UI uses only core ipywidgets supported by both ipywidgets 7.7 and 8; it does not force an
   ipywidgets upgrade/restart or enable Colab's custom-widget manager. Locally the same notebook
   uses the Jupyter kernel and normal local paths. No shell or line magics are used.
3. A downloaded/Colab notebook installs one immutable preprocessing commit, recorded as
   `RELEASE_REF`, rather than moving `main`. A nearby source checkout is preferred for local
   development, but only when it is exactly the `RELEASE_REF` commit with a clean tree; the setup
   shows its commit and tree state, and `DEVELOPMENT_OVERRIDE = True` is the explicit, printed
   way to use another commit or local edits. A pre-existing remote installation is reused only when its package version
   matches the notebook.
4. `current_version` and `notebook_name` are the variables LabConstrictor reads;
   [`notebooks/notebook_latest_versions.yaml`](notebooks/notebook_latest_versions.yaml) tracks
   the published notebook version. The setup output also reports preprocessing, normalization
   and ipywidgets versions and the selected source.
5. On Colab the setup installs the `notebook` and `zarr` extras; locally it installs the
   `notebook` extra when needed. The scientific processing remains in the package:
   the widget app ultimately calls the same `run_experiment()` pipeline as the CLI.
6. Notebook/bootstrap changes must be smoke-tested in both a fresh Google Colab runtime and
   JupyterLab/LabConstrictor before their release candidate is tagged; see
   [docs/colab-release-check.md](docs/colab-release-check.md).

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

Rerunning (`mode="restart"`, the default) only reads images that are new or changed, and
reuses every input the earlier run recorded in the output folder (axis roles, masks,
exclusions, design, image type, settings); `mode="full"` recomputes everything, and
`exclude=[...]` leaves out damaged files. One run at a time holds the output folder's lock,
and every run, also one that stops, writes `discovery_report.json` and a text version. The notebook and the
notebook asks for the experiment description (conditions, which biological repeats pair
across conditions and that they are comparable, channels, masks) and publishes
`morphobricks.toml`; every run needs it, and writes the `experiment_design.csv` derived from
it (an output only). They also ask for the image type, `fluorescence` or `rgb`
(suggested by `review_image_type`), and record it in the manifest; in Python, pass it with
`image_type=`. After writing, the run checks the images' intensities from the table
(`result.intensity`); `check_table(path)` checks any table.

## Command line

```bash
morphobricks-preprocessing describe /data/exp1       # the draft description, what is open
morphobricks-preprocessing check /data/exp1          # report, suggested --axes
morphobricks-preprocessing run /data/exp1 --axes ZCYX
morphobricks-preprocessing run --output /data/exp1_MorphoBricks_Preprocessing   # later runs
morphobricks-preprocessing masks --output /data/exp1_MorphoBricks_Preprocessing # mask step
morphobricks-preprocessing calibrate --output /data/exp1_MorphoBricks_Preprocessing
morphobricks-preprocessing convert /data/exp1 --to /data/exp1_named_axes --axes ZXY  # axis errors
```

`check`, `run` and `masks` need the experiment description published in the output folder
(the notebook's describe steps); without it they stop with `A_DESCRIPTION_REQUIRED` and write
nothing. `describe` shows the draft and what is still to be answered. `check` writes the
report. `run` and `masks` reuse every input the run in the
output folder recorded. A run that stops writes its report and says so. Every output file
gets the permissions a file you save yourself would (your umask). `convert` saves the
images whose metadata name their axes wrongly or not at all as named-axis OME-TIFF copies
with your roles in a new folder (each plane bit for bit the plane read with those roles;
every other image copied as it is; a conversion record written last), then you run
preprocessing on that folder with the same output folder. The notebook offers the same
conversion in step 2, under the axis check, when it finds such images. Details in the
[user guide](docs/user_guide.md) §5.

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
    position_id="img_01",  # required: the XY position within its technical repeat
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

Coding conventions are described in [docs/development.md](docs/development.md). Planned work after v0.5.0 is in [docs/roadmap.md](docs/roadmap.md).

## License

MIT, see [LICENSE](LICENSE).
