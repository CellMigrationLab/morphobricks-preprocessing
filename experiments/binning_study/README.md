# Binning Study (October 2026)

Simulations behind the histogram design in [docs/plan.md](../../docs/plan.md). The
scripts are kept as they were run; the package in `src/` is the maintained
implementation.

## Data

`simulate_experiment.py` creates 80 fluorescence-like 512 × 512 images: 2 conditions ×
4 biological repeats × 2 technical repeats × 5 images, with brightness differences at
every level, cell-like blobs, shot and read noise. Options: `--dtype float64`
(background-subtracted values, about 43% negative), `--variable-size` and larger batch
effects (`--bio-sigma`, `--tech-sigma`). The same seed always gives the same images.

## Studies and results

**1. Pooled pixels vs averaging per-image statistics** (`compare_statistics.py`)
Averaging is exact for the mean only when every image has the same pixel count. It always
underestimates the standard deviation, and upper percentiles diverge most. At condition
level, with heterogeneous data: std −14% on average (worst −22%), p99.9 −21% (worst −36%).

**2. Histogram bin counts for uint16, full dtype range** (`histogram_experiment.py`)
65,536 bins reproduce pooled statistics exactly. With fewer bins the worst percentile
error equals (values per bin − 1) / 2: 0.5 at 32,768 bins, 3.5 at 8,192, 31.5 at 1,024
(p1 up to 23% off). Equal-width and nearly-equal integer bins behave the same; the widest
bin sets the error.

**3. uint16, each image's own range** (same script, `observed` domain)
Exact at 32,768 bins; at 8,192 bins the worst percentile error falls from 3.5 to 0.5 grey
levels, at 1,024 bins from 31.5 to 6.0 (image level) and 1.0 (condition level).

**4. float64, each image's own range** (`float_histogram_experiment.py`)
No percentile was ever off by more than half a bin width. Worst percentile error at
8,192 bins: 0.69 (image) to 0.27 (condition); mean and std effectively exact. Errors
shrink at higher levels because each image's bin edges differ, so rounding errors cancel.

## Running

From this folder, with numpy and tifffile installed:

```bash
python simulate_experiment.py
python compare_statistics.py
python histogram_experiment.py --bins 65536 32768 8192 1024
python simulate_experiment.py --root simulated_experiment_float64 --dtype float64
python float_histogram_experiment.py
```

Generated images and results are ignored by git.
