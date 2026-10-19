# User Guide

The preprocessing brick reads an experiment folder of microscopy TIFF images and computes
one intensity histogram for every 2D plane. The histograms, with each plane's labels, are
saved in `plane_histograms.csv`. Later MorphoBricks bricks (for example patch extraction)
use this table to normalise images per condition, repeat or image without reading the
pixels again.

The brick **never changes your images** and never normalises them itself. It writes only to
its own output folder, which is never inside the experiment folder.

## 1. Installation

You need Python 3.10 or newer and JupyterLab (or Jupyter Notebook). Download
[`notebooks/MorphoBricks_Preprocessing.ipynb`](../notebooks/MorphoBricks_Preprocessing.ipynb), open it in Jupyter and
run **Step 0**. It detects local Jupyter or Google Colab and checks these options:

1. **Nearby checkout:** if the kernel is running in this repository or one of its
   subfolders, use that checkout's source code, including local edits.
2. **Installed package:** otherwise, use the package already available to this kernel.
3. **GitHub installation:** if the package is missing, install it with the notebook
   dependencies from the project's GitHub repository.

Missing notebook dependencies are installed into the same Python environment as the
kernel. A local checkout is installed in editable mode when it needs dependencies;
an installed package's notebook extra is used when its UI dependencies are missing.
Setup prints the selected source and Python executable. It does not upgrade an already
usable installation. Installation needs internet access, and the GitHub option needs Git.
If another copy of the package is already loaded, restart the kernel to switch checkouts.

To install everything manually instead, run these commands in the environment used by
your notebook kernel:

```bash
pip install "morphobricks-preprocessing[notebook] @ git+https://github.com/CellMigrationLab/morphobricks-preprocessing.git"
pip install jupyterlab
```

To also save OME-Zarr copies of the images (§6), install the `zarr` extra too:
`pip install "morphobricks-preprocessing[notebook,zarr] @ git+https://github.com/CellMigrationLab/morphobricks-preprocessing.git"`.

Start JupyterLab with `jupyter lab` and open the notebook. Google Colab is detected by the
setup cell, but native **Browse…** dialogs are only available with a local desktop: paste
paths on the runtime filesystem when using a hosted notebook.

## 2. Organising the experiment folder

The folder you select is the **experiment folder**. Below it, up to three levels of folders
give each image its labels:

```
experiment/
    control/                  condition
        bio_rep1/             biological repeat
            tech_rep1/        technical repeat
                img_01.tif
                img_02.tif
            tech_rep2/
                img_01.tif
    treated/
        bio_rep1/
            img_01.tif        technical repeat: single
```

- Folder names are your labels. They only need to differ from their neighbours:
  `bio_rep1` under `control` and under `treated` are two different samples.
- A branch may stop early. The missing labels are `single`, so `treated/bio_rep1/img_01.tif`
  has the technical repeat `single`. If the images are directly in the experiment folder,
  every label is `single`.
- Only images in folders **without subfolders** are analysed. TIFF files next to subfolders
  are reported and skipped.
- Do not name a folder `single`, and do not use two neighbouring folder names that differ
  only in upper/lower case (`Control` and `control`). Both are errors.
- A fourth level of folders (below the technical repeat) is an error, so that no layer of
  images is dropped silently.
- Other files (`.txt`, `.csv`, ...) are reported and skipped. System files such as
  `.DS_Store`, `Thumbs.db`, `desktop.ini` and `__MACOSX` are ignored without a message.

To change a label, rename the folder and check again.

## 3. Supported images

- **Files:** `.tif` and `.tiff` (any case), including OME-TIFF and ImageJ hyperstacks.
- **Pixel types:** 8-, 16-, 32- and 64-bit signed or unsigned integers, 32- and 64-bit
  floats. All images of an experiment must have the same pixel type.
- **Dimensions:** every image is split into 2D planes along Z (depth), C (channel) and T
  (time). All images must have their dimensions in the same order; the lengths (number of
  slices, time points, size) may differ.
- **RGB images:** the three colour samples are treated as channels (C).
- **Pyramidal images:** only the full-resolution level is read.
- **Not supported yet:** files with several positions or scenes (several image series),
  and position or tile axes. Export each position as its own file.

## 4. The notebook, step by step

The notebook's code cells are hidden: run them from top to bottom (click a cell and press
**Shift + Enter**). Each step is a page of boxes. A blue box at the top says what the step
is for, every control has a grey help line, and one coloured message says what happened
and what to do next. Green means done, amber means "please check", and red means
something must be fixed first. Where you need to confirm something, a yellow box asks the
question and you answer with a large green **Yes, …** button. The box turns green, and an
**Undo** button takes the answer back. Later steps stay locked until the earlier ones are
confirmed. If you change the experiment folder, everything after it is reset.

0. **Start the notebook.** The setup cell checks the environment and installs missing
   requirements if needed. A green box then confirms it is ready and shows the package
   version.
1. **Check the experiment folder.**
   - Paste the folder path, or click **Browse…** to choose it in a dialog. Below the output
     folder, a line shows where the results will go.
   - Click **Check folder**. Only folder names and image headers are read.
   - Tiles summarise the result: images to analyse, image folders, conditions, pixel type,
     skipped files, and errors and warnings.
   - The **Labels from the folder names** table shows every image folder with its condition,
     biological repeat and technical repeat (*single* where a level is missing). If they
     are right, press **Yes, the labels are correct** in the yellow box under the table.
   - **Messages** list each problem with the affected files and what to do. If some images
     cannot be read, **Exclude these images and check again** leaves them out (see §7).
2. **Review the axis roles.**
   - Each dimension of the images has its own box. It shows the dimension's length and the
     role suggested from the metadata, with a dropdown to change it.
   - A line under each dropdown says what the choice means, for example *3 depth slices,
     numbered z = 0 to 2*. A blue line says how many planes each image will be split into.
   - Click **Show preview** and move the sliders to look at different slices, channels and
     time points of any image. Each picture should be a normal image of your sample;
     stripes or a smear mean X or Y is on the wrong dimension. Brightness is stretched for
     display only.
   - If the preview looks right, press **Yes, the preview looks right**, then click
     **Confirm axis roles**. Changing a role afterwards asks you to check the preview again.
     The confirmed roles are locked until you click **Change axis roles**.
   - If some images lack a dimension the others have (often a single channel), a yellow box
     lists them; press **Yes, they have a single plane there** if that is the case.
     Once you confirm the axis roles, this resolves the missing-axis error for step 3.
     Any other errors still need to be fixed before running.
3. **Compute the histograms.**
   - **Before you run** lists anything still to do.
   - **What will happen** names the output folder and files, and says whether an earlier
     run was found and what will be read.
   - The recommended settings suit most experiments. Untick **Use the recommended
     settings** to change them:
     - *Bins per plane:* one bin per possible value for 8- and 16-bit images (exact
       histograms); 65,536 bins for wider integers and 10,000 for floats.
     - *Memory limit:* the most working memory one plane may need (default 1 GiB). Raise it
       only if the computer has enough memory.
     - *Recompute every row:* start from scratch instead of updating the earlier table.
   - **Also save OME-Zarr copies of the images** (off by default) also writes every image
     in OME-Zarr format to the **ome_zarr** folder of the output (see §6). It is greyed out,
     with the install command below it, when the `zarr` extra is not installed. When it is
     ticked, **What will happen** names the copies' folder and a tile counts the copies
     written.
   - Click **Compute histograms**. A progress bar shows which image is being read. Tiles
     then summarise planes written, images read, kept, new, changed, removed and excluded.
     Nothing is written unless the whole run succeeds. **Technical details (log)** holds
     the log for troubleshooting.
4. **Calculate the statistics (optional).** The table from step 3 is all that later bricks
   need; this step only reads it to compare groups and writes no files.
   - **Histogram table** is filled in with the table from step 3. To look at another
     experiment, paste the path of its `plane_histograms.csv` or click **Browse…**.
   - Choose **Condition**, **Biological repeat**, **Technical repeat** or **Image**, and the
     percentiles to report, then click **Calculate statistics**.
   - A spinning wheel shows that work is in progress while the table is read, statistics
     are calculated and the plot is drawn. The button says **Calculating…** and stays
     disabled until the calculation finishes or an error is shown; you can then try again.
   - The table lists pooled statistics per group: minimum, maximum, mean, standard
     deviation, median and percentiles.
   - The plot compares the groups' intensity distributions: curves further right are
     brighter.

In JupyterLab, the optional `jl-hidecode` extension keeps the code cells collapsed and adds
a run button to each; without it, click the "…" of a cell to see its code.

## 5. Command line

The same checks and computations are available from a terminal:

```bash
morphobricks-preprocessing check /data/exp1
```

prints what will be analysed, the problems found, the label tree and the suggested axis
roles, for example `--axes ZCYX`. Then:

```bash
morphobricks-preprocessing run /data/exp1 --axes ZCYX
```

`--axes` gives one role per dimension of the images, in their order: `X`, `Y`, `Z`, `C`,
`T`, or `-` for a length-1 dimension without a role. Roles are never taken from metadata
without being stated. Other options:

| Option | Meaning |
|---|---|
| `--output FOLDER` | Output folder (default: next to the experiment folder) |
| `--exclude PATH` | Leave out an image, relative to the experiment folder; repeat for several |
| `--bins N` | Bins per plane |
| `--memory-limit SIZE` | Working-memory limit per plane, e.g. `2GiB` |
| `--missing-as-singleton` | Confirm that images lacking some dimensions have one plane along them |
| `--force-rebuild` | Recompute every row |
| `--ome-zarr` | Also write OME-Zarr copies of the images (needs the `zarr` extra) |
| `--log FILE` | Write technical details to a file |

Both commands exit with status 1 when an error blocks processing.
`python -m morphobricks_preprocessing` works the same way.

## 6. Output files

The default output folder is next to the experiment folder:
`/data/exp1` → `/data/exp1_MorphoBricks_Preprocessing`.

- **`plane_histograms.csv`:** one row per plane with the file name, the zero-based plane
  coordinates `z`, `c`, `t` (blank when the image has no such dimension), the three
  labels, the pixel type, the exact minimum and maximum, pixel counts and the histogram.
  The table is very wide (one column per bin), so spreadsheet programs may not open it
  completely; read it in Python with `read_plane_histograms()`. The columns are defined in
  [contract.md](contract.md) §5.
- **`run_manifest.json`:** what the run read and with which settings: labels, axis roles,
  bin count, memory limit, excluded images, package versions, and each image's size and
  modification time. Channel names and significant bits are recorded when the metadata
  provides them. Format in [contract.md](contract.md) §9.
- **`ome_zarr/`** (only when OME-Zarr copies are switched on): one `.ome.zarr` folder per
  image, in the same folder structure as the experiment, e.g.
  `ome_zarr/control/bio_rep1/tech_rep1/img_01.ome.zarr`. The full-resolution level holds
  exactly the source pixel values; smaller levels are averages for fast viewing (for
  example in napari with the `napari-ome-zarr` plugin). Pixel sizes and channel names are
  copied from the metadata when present. Copies of unchanged images are kept on later
  runs, copies of deleted or excluded images are removed. Switching the option off keeps
  the existing copies but no longer updates them. Details in
  [contract.md](contract.md) §10.

The table and manifest are both prepared before the earlier files are replaced. If a
replacement fails, the earlier files are restored. This needs temporary disk space for
the new outputs and copies of the earlier pair. If restoration also fails (for example,
another program locks an output file), the error names a backup folder: close programs
using the output files and restore those backups before running again.

Run only one writer per output folder. If the process is forcibly stopped during output
replacement, rerunning checks the table against the manifest and rebuilds a mismatched pair.

## 7. Running again, and excluding images

Run the notebook or `run` again after adding, changing or removing images:

- only new and changed images are read; rows of unchanged images are kept;
- rows of deleted or renamed images are removed (a renamed image counts as removed and
  added);
- everything is recomputed when the bin count, the axis roles or the pixel type changed,
  when the table was edited by hand, or when you ask for it.

To leave out images, for example a damaged file, list them under *Exclude images* (one per
line) or pass `--exclude`. Excluded images are not read, are listed in the messages and the
manifest, and their earlier rows are removed. Remove them from the list to analyse them
again.

## 8. Messages

Errors stop processing until they are resolved; warnings are reported and processing
continues.

| Message code | Severity | Meaning and what to do |
|---|---|---|
| `experiment_not_found` | Error | The folder does not exist. Select an existing folder. |
| `unreadable_folder` | Error | A folder could not be opened. Check permissions. |
| `reserved_folder_name` | Error | A folder is named `single`. Rename it. |
| `case_conflict` | Error | Neighbouring folders differ only in case. Rename or merge them. |
| `folder_too_deep` | Error | Folders below the technical repeat. Move the images up, or select a lower folder as the experiment. |
| `output_inside_experiment` | Error | The output folder is inside the experiment. Choose another. |
| `no_images` | Error | No images to analyse (or all excluded). Check the folder. |
| `unreadable_tiff` | Error | An image cannot be opened. Export it again, or exclude it. |
| `multiple_series` | Error | A file holds several positions or scenes. Export them separately, or exclude it. |
| `unsupported_dtype` | Error | Unsupported pixel type (e.g. float16). Convert, or exclude it. |
| `mixed_dtypes` | Error | Images have different pixel types. Process them separately or convert. |
| `inconsistent_dimensions`, `inconsistent_mapping` | Error | Images have their dimensions in a different order. Export them the same way or process separately. |
| `missing_axes` | Error | Some images lack a dimension. Confirm they have one plane along it (step 2), or export them again. |
| `ambiguous_missing_axes` | Error | It is unclear which dimension an image lacks. Export it again. |
| `missing_xy_axis`, `duplicate_axis_role` | Error | X/Y missing or a role used twice. Correct the axis roles. |
| `unassigned_axis` | Error | A dimension with several planes has no role. Choose Z, C or T. |
| `unsupported_axis` | Error | A position or tile dimension. Export each position separately. |
| `plane_over_memory_limit` | Error | A plane needs more memory than the limit. Raise the limit or crop the images. |
| `unsupported_layout` | Error | The image cannot be read one plane at a time. Check the axis roles or export again. |
| `unreadable_planes` | Error | Pixel data cannot be decoded. Export again, or exclude the image. |
| `invalid_bin_count` | Error | Bin count outside the allowed range. Use the default or a value in range. |
| `no_usable_planes` | Error | No plane has finite pixel values. Check the images. |
| `non_tiff_file`, `empty_folder`, `tiff_outside_leaf` | Warning | Files or folders that are skipped. |
| `different_sizes` | Warning | Images have different dimension lengths. Usually expected. |
| `channel_names_differ` | Warning | Channel names differ between images at the same position. Check the channel order. |
| `non_finite_pixels` | Warning | NaN or infinite pixels are left out of the histograms and counted. |
| `no_finite_pixels` | Warning | Some planes have no finite pixels; they are left out of group statistics. |
| `excluded_images`, `excluded_path_not_found` | Warning | Images excluded as requested, or an excluded path that matches no image. |
| `zarr_not_installed` | Error | OME-Zarr copies were requested without the `zarr` extra. Install it, or run without copies. |

## 9. Python interface

Everything the notebook does is available as functions:

```python
from morphobricks_preprocessing import preflight, run_experiment, read_plane_histograms

check = preflight("/data/exp1")  # pass 1: folders and headers
print(check.issues)
mapping = check.suggested_mapping  # AxisMapping; edit with mapping.with_role(...)
result = run_experiment("/data/exp1", mapping)
records = read_plane_histograms(result.csv_path)
```

Group statistics and normalisation parameters for later bricks: `PooledHistogram`,
`group_records`, `min_max`, `percentile_range`, `z_score` and `robust_z_score`. The rules
behind them are in [contract.md](contract.md); the design in [plan.md](plan.md).
