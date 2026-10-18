# User Guide

The preprocessing brick reads an experiment folder of microscopy TIFF images and computes
one intensity histogram for every 2D plane. The histograms, with each plane's labels, are
saved in `plane_histograms.csv`. Later MorphoBricks bricks (for example patch extraction)
use this table to normalise images per condition, repeat or image without reading the
pixels again.

The brick **never changes your images** and never normalises them itself. It writes only to
its own output folder, which is never inside the experiment folder.

## 1. Installation

You need Python 3.10 or newer and JupyterLab (or Jupyter Notebook). In a terminal:

```bash
pip install "morphobricks-preprocessing[notebook] @ git+https://github.com/CellMigrationLab/morphobricks-preprocessing.git"
pip install jupyterlab
```

To also save OME-Zarr copies of the images (§6), install the `zarr` extra too:
`pip install "morphobricks-preprocessing[notebook,zarr] @ git+https://github.com/CellMigrationLab/morphobricks-preprocessing.git"`.

Download [`notebooks/preprocessing.ipynb`](../notebooks/preprocessing.ipynb), start
JupyterLab with `jupyter lab`, and open the notebook.

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

Run the cells from top to bottom. Each step explains what it found; errors say what
happened, which files are affected and what to do.

1. **Check the experiment folder.** Paste the folder path and press *Check folder*. The
   folders and image headers are inspected without reading pixels. Look at the tree of
   labels and tick *The folders and labels above are correct*. If some images cannot be
   read, *Exclude these images and check again* leaves them out (see §7).
2. **Review the axis roles.** Each dimension of the images gets a role, suggested from the
   image metadata. Dimensions of length 1 need no role. Press *Show preview*: each picture
   should be a normal 2D image (if it looks like stripes or a smear, X or Y is on the wrong
   dimension). Press *Confirm axis roles*. If some images lack a dimension the others have
   (often a single channel), tick the box that confirms they have one plane along it.
3. **Compute the histograms.** Press *Run*. The defaults suit most experiments:
   - *Bins per plane:* one bin per possible value for 8- and 16-bit images (exact
     histograms); 65,536 bins for wider integers and 10,000 for floats.
   - *Memory limit:* the most working memory one plane may need (default 1 GiB). Raise it
     only if the computer has enough memory.
   - *Recompute every row:* start from scratch instead of updating the earlier table.
   - *Also save OME-Zarr copies:* write a copy of every image in OME-Zarr format to the
     output folder (needs the `zarr` extra; see §6).

   Nothing is written unless the whole run succeeds. *Technical details* holds the log for
   troubleshooting.
4. **Look at the results.** Pooled statistics (minimum, maximum, mean, standard deviation,
   median and chosen percentiles) for each condition, biological repeat, technical repeat
   or image.

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
