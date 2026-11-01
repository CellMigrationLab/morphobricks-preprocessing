# User Guide

The preprocessing brick reads an experiment folder of microscopy images (TIFF or
OME-Zarr) and computes
one intensity histogram for every 2D plane. The histograms, with each plane's labels, are
saved in `plane_histograms.csv`. Later MorphoBricks bricks (for example patch extraction)
use this table to normalise images per condition, repeat or image without reading the
pixels again.

The brick **never changes your images** and never normalises them itself. It writes only to
its own output folder, which is never inside the experiment folder.

## 1. Installation

You need Python 3.11 or newer and JupyterLab (or Jupyter Notebook). Download
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

To read OME-Zarr images, or to convert TIFF images to OME-Zarr (§6), install the `zarr`
extra too:
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
- Images are TIFF files or OME-Zarr images. An OME-Zarr image is a folder whose name ends
  in `.zarr` (usually `.ome.zarr`); it counts as one image, not as a folder level, so put
  it where you would put a TIFF file. TIFF and OME-Zarr images can be mixed.
- Only images in folders **without subfolders** are analysed. Images next to subfolders
  are an error (`tiff_outside_leaf`): a folder holds either images or subfolders, never
  both, so move them into a folder of their own.
- A folder may be named `single` (in lower case) when it is the only folder in its place:
  like a missing level, it means the level has one member, so `treated/single/img_01.tif`
  has the biological repeat `single`. A folder named `Single` or `SINGLE`, or a `single`
  folder next to other folders, is an error.
- Spell each name one way everywhere: two folder names of one level that differ only in
  upper/lower case are an error, next to each other (`Control` and `control`) or anywhere
  in the experiment (`control/Rep1` and `treated/rep1`).
- Folder and image names must not start or end with a space, and must not be `NA`, `nan`,
  `none`, `null`, `n/a` or `<NA>` (in any case): tables read those as a missing value. This is
  an error (`invalid_name`).
- Keep masks out of the experiment folder (see Masks below). Images named like a mask of
  another image (`f1_mask.tif` beside `f1.tif`), or a folder named like a masks folder
  holding such files, give a warning (`image_named_as_mask`, `mask_folder_in_experiment`).
- Each image is one position (field of view), named after its file without the extension:
  `img_01.tif` and `img_01.ome.zarr` are both position `img_01`. The same name may be used
  in different folders (`tech_rep1/img_01.tif` and `tech_rep2/img_01.tif` are two
  positions), but two images in one folder must not share a name apart from the extension
  or upper/lower case. That is an error; rename one or move it out of the experiment.
- A fourth level of folders (below the technical repeat) is an error, so that no layer of
  images is dropped silently.
- Other files (`.txt`, `.csv`, ...) are reported and skipped. System files such as
  `.DS_Store`, `Thumbs.db`, `desktop.ini` and `__MACOSX` are ignored without a message.

To change a label, rename the folder and check again.

### Masks (optional)

If you already have masks (segmentations of cells, nuclei, nucleoli, ...), keep them in a
**masks folder** next to the experiment folder, not inside it. Make one folder per kind of
mask, named as you like (`Cells`, `Nuclei`, ...), and inside each the same folders as the
experiment, with one mask per image under the image's name:

```
masks/
    Cells/
        control/bio_rep1/tech_rep1/img_01.tif
        control/bio_rep1/tech_rep1/img_02.tif
    Nuclei/
        control/bio_rep1/tech_rep1/img_01.tif
        control/bio_rep1/tech_rep1/img_02.tif
```

- Or keep the masks in the same folders as the experiment, each named after its image
  followed by `_` and its kind (`img_01_cells.tif`, `img_01_nuclei.tif`), as many
  segmentation tools write them; the kind is the part after the image name. Both ways may be
  mixed in one masks folder.
- A mask is a TIFF label image with the width and height of its image: background 0,
  each object its own whole number (or a true/false mask). The extension may differ from
  the image's (`img_01.tiff` for `img_01.ome.tif`).
- A 2D mask belongs to an image with one plane. For a Z-stack or a time-lapse, the mask needs
  one plane per depth slice or time point, with its axes named in its metadata (an ImageJ
  hyperstack or OME-TIFF with axes `ZYX` or `TYX`); a 2D mask for such an image, or a plain
  stack whose axis could be depth or time, is refused by `run`/`check`.
- You may have any number of kinds of mask. Their folder names (or the part after the image
  name) are their names: they must differ by more than upper/lower case. If one image's name
  starts another's (`a.tif` and `a_b.tif`), a mask named `a_b_cells.tif` could belong to
  either and is refused: rename the masks or the images.
- The label images stored inside an OME-Zarr image (its `labels` group) are used as masks
  too, named after the label image.
- An image without a mask of one kind is reported; its objects of that kind cannot be
  measured.

Masks do not change `plane_histograms.csv`. They are checked and recorded for the bricks
that measure objects; the optional `masks` command (§5) also tells how they relate.

## 3. Supported images

- **TIFF files:** `.tif` and `.tiff` (any case), including OME-TIFF and ImageJ hyperstacks.
  The axis roles must agree with the axes their metadata names (§4 step 2); a file whose
  axes you know better than its header is saved as a named-axis copy first. A plain stack
  of pages without axis metadata is read with the roles you confirm (a warning).
- **OME-Zarr images:** OME-NGFF 0.4 and 0.5 (needs the `zarr` extra; 0.5 needs Python 3.11
  or newer). Their axes (time, channel, z, y, x) come from their metadata. Only the
  full-resolution level of a single image is read; a folder holding several images (a
  plate, or a bioformats2raw export with several series) is an error: export each image
  separately or exclude it.
- **Pixel types:** 8-, 16-, 32- and 64-bit signed or unsigned integers, 32- and 64-bit
  floats. All images of an experiment must have the same pixel type.
- **Dimensions:** every image is split into planes along Z (depth) and T (time); a plane
  holds every channel (C) of the image at that Z and T, one row of the table. All TIFF
  images must have their dimensions in the same order; the number of slices, time points
  and the size may differ. OME-Zarr images may store them in another order: they are
  matched by role, so z = 2, c = 1 means the same slice and channel in every image.
- **Channels:** all images of an experiment must have the same number of channels (an
  image without a channel dimension has one). Split images with other channels into a
  separate experiment, or exclude them.
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

After a successful action, a separate **What next?** box gives the next action in larger
text. In step 1 it first asks you to check the labels, then points to step 2 once they are
confirmed. Step 3 offers the optional statistics step, and step 4 suggests reviewing the
results or comparing another grouping.

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
   - Once the labels are confirmed, **Experiment design** asks about the biological
     repeats. Later bricks normalise each biological repeat over all conditions together,
     so they need to know which folders are the same repeat:
     - **Pair the biological repeats across conditions.** Folders with the same name in
       different conditions are one *repeat group*. If a repeat has another folder name in
       some condition (acquired a day later, for example), choose its group in the
       **Repeat group** dropdown. A group can hold only one folder of each condition.
     - **Confirm that they are comparable.** For each repeat group, a yellow box asks
       whether its images were prepared and acquired under the same circumstances. Press
       **Yes, they are comparable** if they were. Without biological-repeat folders, one
       question covers the whole experiment. Step 3 runs only once every group is
       confirmed; if images are not comparable, process them as separate experiments.
     - **Controls (optional).** Tick the control conditions. This is recorded for later
       bricks; nothing uses it yet.
     - The answers are saved in **experiment_design.csv** in the output folder (§6) after
       every answer. Next time, if the folders are the same, they are loaded and the
       questions are folded away; **Change the experiment design** shows them again. A new
       biological-repeat folder asks again only for the group it joins.
     - If the saved **experiment_design.csv** cannot be read (for example a spreadsheet
       program rewrote a date such as 2026-10-26 as 26/10/2026), a red box shows why,
       naming the line and column, and the file is left as it is. Correct the file and
       undo and confirm the labels again to read it, or answer the questions again: the
       first answer replaces the file.
   - **Dataset consistency** shows how many images each condition (columns) and
     biological repeat (rows; the repeat groups once paired) has, with the number of technical-repeat folders. A cell is
     amber when it has fewer than half or more than twice the median number of images, or
     when a condition does not have that biological repeat. Expand **Show consistency
     warnings** to see the checks that compare the images with each other: pixel type,
     dimensions, width and height, numbers of channels, slices and time points, channel
     names and physical pixel sizes (see §8). Warnings never stop you; they say what to
     check. Images with different numbers of channels, pixel types or dimension orders are
     errors: fix them before going on.
   - **Messages** lists the other problems (folders, files that are not images, unreadable
     images), with the warning count visible in orange and the error count in red.
     Expand **Show warnings and errors** to see each problem, the affected files and what
     to do. The details start collapsed after each check. If some images cannot be read,
     **Exclude these images and check again** inside the dropdown leaves them out (see §7).
2. **Review the axis roles.**
   - Each dimension of the images has its own box. It shows the dimension's length and the
     role suggested from the metadata, with a dropdown to change it.
   - A line under each dropdown says what the choice means, for example *3 depth slices,
     numbered z = 0 to 2*. A blue line says how many planes each image will be split into.
   - Click **Show preview** and move the sliders to look at different slices, channels and
     time points of any image. If the roles you choose give the images different numbers of
     channels, a message says so and step 3 stays locked. Each picture should be a normal image of your sample;
     stripes or a smear mean X or Y is on the wrong dimension. Brightness is stretched for
     display only.
   - The roles are checked against the axes each image's metadata names, in one **Axis
     check** card. A role that disagrees with the header is marked in amber under its
     dropdown; the card gives the error (`axes_yx_swapped` or `axes_contradict_header`,
     §8) naming the images with their header axes, your roles and the likely cause. Such
     images are never read: correct the axes, or save named-axis copies with your roles and
     run preprocessing on the copies. An axis the metadata does not name (a plain stack of
     pages) takes the role you confirm: the card warns (`axes_not_named`) and the run
     records the roles as confirmed by you.
   - If the preview looks right, press **Yes, the preview looks right**, then click
     **Confirm axis roles**. When the roles agree with every header, a green card says
     **Axes checked: they are correct for every image**. Changing a role afterwards asks
     you to check the preview again.
     The confirmed roles are locked until you click **Change axis roles**.
   - If some images lack a dimension the others have (often a single channel), a yellow box
     lists them; press **Yes, they have a single plane there** if that is the case.
     Once you confirm the axis roles, this resolves the missing-axis error for step 3.
     Any other errors still need to be fixed before running.
   - Once the axis roles are confirmed, **Image type** asks whether the images are
     **Fluorescence** (each channel is a separate stain or marker) or **RGB** (colour
     images, such as brightfield or histology stains). Later bricks normalise the two types
     differently: fluorescence channel by channel, RGB with the red, green and blue channels
     together (StarDist's method), so the colours are kept. The histograms are the same for
     both types.
     - RGB is suggested when the images are stored as RGB colour (three colour samples per
       pixel) and those samples are the channels; fluorescence otherwise. The suggestion is
       only preselected: press **Yes, this is the right image type** to confirm it, or choose
       the other type first. An amber line reminds you when you choose against the
       suggestion. Step 3 runs only once the type is confirmed.
     - RGB needs exactly three channels of a whole-number pixel type (8- or 16-bit colour).
       For other images the choice is greyed out at Fluorescence, with the reason.
     - Changing the axis roles asks for the image type again. The type is recorded in
       **run_manifest.json** (§6); changing it on a later run reads no image again.
3. **Compute the histograms.**
   - **Before you run** lists anything still to do.
   - **What will happen** names the output folder and files and the image type, and says
     whether an earlier run was found and what will be read. Once the axis roles are confirmed it also names
     the images whose planes are too large for the memory limit and will be read in
     blocks, and, in a red box, any image that cannot be read within the limit at all
     (the run would stop; raise the limit or exclude the image).
   - The recommended settings suit most experiments. Untick **Use the recommended
     settings** to change them:
     - *Bins per plane:* one bin per possible value for 8- and 16-bit images (exact
       histograms); 65,536 bins for wider integers and 10,000 for floats.
     - *Histogram range:* **Minimum to maximum of each plane** (recommended) or
       **Robust**, which bins between two quantiles of each plane (0.01 % and 99.99 % by
       default) and counts the pixels outside separately. It matters only for histograms
       that are not exact (float images, and integer images with more values than bins).
       A few extreme pixels, such as hot pixels, can squeeze every other pixel into a few
       bins of the minimum-to-maximum range; the run then warns
       (`narrow_histogram_range`), names the planes, and says how many bins the robust
       range would give. Run again with the robust range to fix it. Changing the range
       reads every image again.
     - *Memory limit:* the most working memory one plane may need (default 1 GiB). A
       plane that needs more (roughly above 5,000 x 5,000 pixels) is read in blocks
       instead, and the run reports this as a warning: the histograms are exactly the
       same, only reading takes longer. Raise the limit only if the computer has enough
       memory.
     - *Recompute every row:* start from scratch instead of updating the earlier table.
   - **(Optional) Image conversion:**
     - **Read the images directly** (the default and recommended choice) reads TIFF and
       OME-Zarr images as they are and saves nothing extra.
     - **Convert the TIFF images to OME-Zarr, then process the converted copies** writes
       every TIFF image in OME-Zarr format to the **ome_zarr** folder of the output (see
       §6) and computes its histograms from that copy, like for any OME-Zarr image; the
       results are the same. OME-Zarr images are never converted.
     - When converting, an amber box warns that the images are stored again, so the space
       they occupy is duplicated. It shows the size of the TIFF images, the most the copies
       may need (before compression) and the free space on the output drive. It turns red
       if the space may not be enough, but you can still run.
     - **OME-Zarr conversion settings** opens when you choose conversion. Click its header
       to collapse or expand three cards, each with its own controls and explanation:
       **Storage block size** (square chunks, default 1,024 pixels per side), **Lossless
       compression** (LZ4 by default, Zstandard, or none, with a level from 1 to 9), and
       **Preview resolutions** (smaller copies for fast viewing: automatic, full resolution
       only, or a fixed number). Histograms use full resolution. Collapsing the panel keeps
       your choices and leaves the disk-space warning visible. Changing the settings later
       writes the copies again; the histograms do not change.
     - The choice is greyed out, with the install command below it, when the `zarr` extra
       is not installed. **What will happen** says which images are converted and with
       which settings, and a tile counts the conversions.
   - Click **Compute histograms**. A progress bar shows which image is being read. Tiles
     then summarise planes written, images read, kept, new, changed, removed and excluded.
     Nothing is written unless the whole run succeeds. **Technical details (log)** holds
     the log for troubleshooting.
   - **Intensity checks** then compares the images, using only the table just written (no
     image is read again, and nothing else is written). Each image is compared with the
     other images of its condition and biological repeat, and each biological repeat with
     the other repeats of its condition, so differences between conditions (treatment
     effects) are never reported:
     - Tiles count the images checked and each kind of warning; a table lists every
       warning with the check, the channel (**C0**, **C1**, …), the image or biological
       repeat, and what was found (the values compared and the limit), followed by what to
       do about each kind. The four kinds are images far from the others of their
       biological repeat, biological repeats shifted from the others of their condition,
       saturated images, and images without intensity spread (see §8).
     - **Check settings** (collapsed) holds the limits: how many robust deviations count as
       far (default 3), the share of saturated pixels (default 1 %), and the fewest images a
       comparison needs (default 5). Changing them updates the warnings and plots at once;
       the run records the limits in **run_manifest.json**, so the next run records the
       new ones. **Restore the defaults** sets them back.
     - Plots, one channel at a time (choose it above the plots): box plots of the image
       medians and high percentiles for each condition and biological repeat, one point per
       image, with images that have a warning in that channel as red diamonds and shifted
       repeats as amber boxes; and one panel per condition with the intensity distribution
       of each biological repeat (comparable repeats lie close together; a shifted one is
       dashed). The plots are only shown here, never saved.
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
     deviation, median and percentiles. Every channel of a group is pooled together, as
     before; its **planes** column counts each channel of a plane as one plane.
   - The plot compares the groups' intensity distributions: curves further right are
     brighter.
   - **Intensity checks** shows the same warnings, settings and plots as step 3 for the
     chosen table, so tables of other experiments can be checked too. It needs only the
     table, not the images.

In JupyterLab, the optional `jl-hidecode` extension keeps the code cells collapsed and adds
a run button to each; without it, click the "…" of a cell to see its code.

## 5. Command line

The same checks and computations are available from a terminal:

```bash
morphobricks-preprocessing check /data/exp1
```

prints what will be analysed, the label tree, the number of images per condition and
biological repeat (with `!` marking unbalanced ones), the problems found and the
suggested axis roles, for example `--axes ZCYX`. Then:

```bash
morphobricks-preprocessing run /data/exp1 --axes ZCYX
```

`--axes` gives one role per dimension of the images, in their order: `X`, `Y`, `Z`, `C`,
`T`, or `-` for a length-1 dimension without a role. Roles are never taken from metadata
without being stated.

Both commands check the axes (§4, step 2): `check` with the suggested roles, which are the
headers' own, so only the `axes_not_named` warning of plain stacks can appear there; `run`
with the roles given in `--axes`. They print **Axes checked: every image names its axes in
its metadata, and the roles agree with them** when all is well (and name the plain stacks
whose roles are recorded as confirmed), or the errors naming the images, the likely cause
and the two fixes, and `run` stops (exit status 1).

`--image-type fluorescence` or `--image-type rgb` gives the image type (§4, step 2).
Without it, `run` uses the type suggested from the image headers and prints it, with why it
was suggested and how to choose the other one; `check` prints the suggestion too. `rgb`
for images that are not three channels of a whole-number pixel type stops the run
(`rgb_not_possible`).

`run` also needs the experiment design (§4, step 1). It reads `experiment_design.csv` from
the output folder (or the file given with `--design`). The first time, or when folders were
added, it writes that file and stops, so you can fill it in with a spreadsheet program or a
text editor: give rows the same `repeat_group` to pair biological repeats named differently,
set `confirmed` to `true` in every row of a repeat group whose images are comparable, and
optionally `control` to `true` for control conditions. Then run again. `check` says whether
the design is confirmed, or why a saved one cannot be read (naming the line and column); it
never writes the file. Other options:

| Option | Meaning |
|---|---|
| `--output FOLDER` | Output folder (default: next to the experiment folder) |
| `--exclude PATH` | Leave out an image, relative to the experiment folder; repeat for several |
| `--bins N` | Bins per plane |
| `--histogram-range minmax\|robust`, `--robust-quantiles LOW HIGH` | Histogram range of histograms that are not exact (default minmax; robust quantiles 0.01 99.99) |
| `--memory-limit SIZE` | Working-memory limit per plane, e.g. `2GiB` |
| `--missing-as-singleton` | Confirm that images lacking some dimensions have one plane along them |
| `--image-type fluorescence\|rgb` | The image type (default: the suggested one, printed) |
| `--design FILE` | The confirmed experiment design (default: `experiment_design.csv` in the output folder); it is copied to the output folder |
| `--force-rebuild` | Recompute every row |
| `--convert-tiff` | Convert the TIFF images to OME-Zarr first and process the copies (needs the `zarr` extra); prints the disk space needed |
| `--chunk-size PX` | With `--convert-tiff`: tile size of the copies (16 to 8192, default 1024) |
| `--compression lz4\|zstd\|none`, `--compression-level N` | With `--convert-tiff`: lossless compression (default lz4, level 5) |
| `--pyramid-levels auto\|none\|N` | With `--convert-tiff`: smaller levels for viewing (default auto) |
| `--outlier-limit N`, `--saturation-limit FRACTION`, `--min-images N` | Limits of the intensity checks printed after the run (default 3, 0.01 and 5) |
| `--log FILE` | Write technical details to a file |
| `--masks FOLDER` | The masks folder (§2). `check` lists the mask types found and checks every mask from its header; `run` stops on a mask error and records the masks in `run_manifest.json` |

After writing, `run` prints the intensity checks of the table, grouped by kind, each with
the channel, the image or biological repeat and the values compared, and what to do once
per kind; it draws no plots (the notebook does). Both commands exit with status 1 when an
error blocks processing; intensity warnings never do.

The optional `masks` command reads the masks and tells how they relate:

```bash
morphobricks-preprocessing masks /data/exp1 --masks /data/exp1_masks
```

It reads every image's masks, one image at a time, and prints for each kind of mask
whether its labels are separate objects (`instances`), labels spread over many pieces
(`grouped`, such as mitochondria labelled with their cell's number), a single value per
image (a true/false mask or one object) or empty; then which kinds lie inside which, with
the counts behind it, for example how many cells hold 0, 1, 2 or 3 or more nuclei; and the
hierarchy of direct parents, for example:

```
Hierarchy (direct parents only):
    Organoid
        Cells
            Nuclei
                Nucleoli
            Vesicles
```

Nucleoli inside nuclei inside cells have the nuclei as their direct parent; two kinds of
mask that segment the same objects are reported as such. These are suggestions from the
pixels, not decisions: you name each kind of mask and check the relationships in the bricks
that use them. The report is written to `mask_relationships.json` in the output folder
(§6). The command takes `--output`, `--exclude` and `--log` like the others, and exits with
status 1 when an error blocks it or when no masks are found. Label 0 is the background and
objects have positive labels: a mask with a negative value stops the command with a message
naming the mask, and no report is written.

The rules behind each suggestion use thresholds you can change, for example
`--inside-object-share 0.8` to call a kind of mask "inside" another when 80% of its objects
(not 90%) lie inside, or `--same-objects-iou 0.7` to ask for a closer match before two
kinds of mask count as the same objects. `morphobricks-preprocessing masks --help` lists
all seven with their defaults; the values used are printed and written in the report.
`python -m morphobricks_preprocessing` works the same way.

## 6. Output files

The default output folder is next to the experiment folder:
`/data/exp1` → `/data/exp1_MorphoBricks_Preprocessing`.

- **`plane_histograms.csv`:** one row per plane (every channel of an image at one depth
  slice and time point) with the position (`position_id`, the file name without its
  extension), the file name, the zero-based plane coordinates `z` and `t` (0 when the
  image has no such dimension), the three labels, the pixel type and the number of
  channels; then, for each channel, its exact minimum and maximum, pixel counts and
  histogram, in columns ending in the channel number (`Intensity_MinIntensity_C0`,
  `Histogram_Count_C1_00042`, …). The table is very wide (one column per bin and channel),
  so spreadsheet programs may not open it completely; read it in Python with
  `read_plane_histograms()`. The columns are defined in the [normalization brick's
  contract](https://github.com/CellMigrationLab/morphobricks-normalization/blob/main/docs/contract.md)
  §6.2 (schema version 4: each channel also has its underflow and overflow, zero unless the
  robust range is used). A table written by an earlier version is recomputed on the next
  run; later bricks ask you to run this brick again on it.
- **`experiment_design.csv`:** one row per biological-repeat folder of each condition: its
  repeat group, whether its condition is a control, its image and technical-repeat counts,
  and whether and when the repeat group was confirmed. Written in step 1 and by every run.
  Later bricks read the pairing from it (`read_experiment_design`). Format in
  [contract.md](contract.md) §12.
- **`run_manifest.json`:** what the run read and with which settings: labels, axis roles,
  the image type (and whether the channels are RGB colour samples), bin count, histogram
  range, memory limit, excluded images, the limits of the intensity checks, the planes with a
  narrow histogram range, package versions, the
  fingerprint of the experiment design, and each image's position, size and modification time. Channel names and significant bits are recorded when the metadata
  provides them, and whether the image's planes were too large to read whole and are read
  in blocks. Format in [contract.md](contract.md) §9.
- **`ome_zarr/`** (only when TIFF images are converted): one `.ome.zarr` folder per TIFF
  image, in the same folder structure as the experiment, e.g.
  `ome_zarr/control/bio_rep1/tech_rep1/img_01.ome.zarr`. The full-resolution level holds
  exactly the source pixel values; smaller levels are averages for fast viewing (for
  example in napari with the `napari-ome-zarr` plugin). Pixel sizes and channel names are
  copied from the metadata when present (an unnamed channel stays unnamed), each axis with its own unit. Copies of unchanged
  images are kept on later runs, copies of deleted or excluded images are removed. Copies
  written by an earlier version with the X unit on every axis are converted again once,
  with their own units; the table does not change. Switching back to reading the
  TIFF images directly removes the copies and recomputes their rows. Details in
  [contract.md](contract.md) §10.

- **`mask_relationships.json`** (only from the `masks` command): each kind of mask, the
  relationship between every two kinds with its counts, and the hierarchy. The rules and
  thresholds are in [contract.md](contract.md) §15.

When masks are given, `run_manifest.json` also lists every image's masks (`masks`), with
each mask's axes and size (for example `TYX` and 50 x 512 x 512 for a mask per time point).

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
- everything is recomputed when the bin count, the histogram range, the axis roles or the
  pixel type changed,
  when the table was edited by hand or written by an earlier version of the brick, or when
  you ask for it;
- changing only the image type reads nothing again: the histograms are the same for both
  types, and only the manifest records the new type.

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
| `reserved_folder_name` | Error | A folder is named `single` in another case (`Single`), or a folder named `single` has other folders beside it. Rename it to `single` in lower case, or give it a name of its own. |
| `case_conflict` | Error | Folders of one level have names that differ only in case, next to each other or anywhere in the experiment (`control/Rep1` and `treated/rep1`). Spell each name one way, or rename or merge them. |
| `invalid_name` | Error | A folder or image name starts or ends with a space, or is `NA`, `nan`, `none`, `null`, `n/a` or `<NA>` (any case), which tables read as a missing value. Rename it. |
| `duplicate_position_id` | Error | Two images in one folder have the same name apart from the extension or upper/lower case (`img_01.tif` and `img_01.ome.zarr`), so their positions cannot be told apart. Rename one, or move it out of the experiment. |
| `folder_too_deep` | Error | Folders below the technical repeat. Move the images up, or select a lower folder as the experiment. |
| `output_inside_experiment` | Error | The output folder is inside the experiment. Choose another. |
| `no_images` | Error | No images to analyse (or all excluded). Check the folder. |
| `unreadable_tiff` | Error | A TIFF file cannot be opened. Export it again, or exclude it. |
| `unreadable_ome_zarr` | Error | A `.zarr` folder is not an OME-Zarr image that can be read. Export it again as OME-Zarr (OME-NGFF 0.4 or 0.5), or exclude it. |
| `ome_zarr_axes_mismatch` | Error | An OME-Zarr image declares an axis with several planes that the experiment has no role for (or has no X/Y). Check the axis roles, or process it separately. |
| `multiple_series` | Error | A file or OME-Zarr folder holds several images (positions, scenes, series, wells or fields). Export them separately, or exclude it. |
| `unsupported_dtype` | Error | Unsupported pixel type (e.g. float16). Convert, or exclude it. |
| `mixed_dtypes` | Error | Images have different pixel types. Process them separately or convert. |
| `different_channel_counts` | Error | Images have different numbers of channels (an image without a channel dimension has one). Every image of an experiment must have the same channels: split the others into a separate experiment, or exclude them. If a dimension got the channel role by mistake, correct the axis roles. |
| `inconsistent_dimensions`, `inconsistent_mapping` | Error | Images have their dimensions in a different order. Export them the same way or process separately. |
| `missing_axes` | Error | Some images lack a dimension. Confirm they have one plane along it (step 2), or export them again. |
| `ambiguous_missing_axes` | Error | It is unclear which dimension an image lacks. Export it again. |
| `axes_not_named` | Warning | The metadata of some images does not name their axes (a plain stack of pages, `IYX` or `QYX`), so which dimension is depth, time or channel comes only from the roles you confirm. The images are read with them, and the run manifest records them as confirmed by you. Check the roles with the preview. Before segmenting these images in other tools, consider saving named-axis copies (OME-TIFF), so every tool reads the same axes. |
| `axes_yx_swapped` | Error | The roles you confirmed give the Y and X the image header names the other way round, so the images are not read (never transposed). The message names the likely cause. Two fixes: correct the axes (if the header is right, choose its roles), or convert (save named-axis copies with your roles with `morphobricks-preprocessing convert`, then run preprocessing on the copies). |
| `axes_contradict_header` | Error | The roles you confirmed contradict the axes the image header names (for example the header says channels, C, and you chose time, T). The images are not read under a contradicted declaration. The message names the likely cause, often the tool that saved the files: tifffile labels a 3-D stack saved with `imagej=True` or `ome=True` as `CYX`, and an ImageJ stack of slices reads as depth even for a time-lapse. The same two fixes as `axes_yx_swapped`. |
| `declared_axes_mismatch` | Error (other bricks; deprecated, removed in v0.5.0) | The axes a brick was told for an image or mask contradict the axes in its metadata (for example a depth stack declared as channels). Declare the axes the metadata gives, or export the image again with the right axes. |
| `missing_xy_axis`, `duplicate_axis_role` | Error | X/Y missing or a role used twice. Correct the axis roles. |
| `unassigned_axis` | Error | A dimension with several planes has no role. Choose Z, C or T. |
| `unsupported_axis` | Error | A position or tile dimension. Export each position separately. |
| `plane_over_memory_limit` | Error | A plane cannot be read within the memory limit, not even in blocks (for example, it is stored compressed in one piece). Raise the limit, or save the images again as tiled or uncompressed TIFF files or as OME-Zarr with smaller chunks. |
| `unsupported_layout` | Error | The image cannot be read one plane at a time. Check the axis roles or export again. |
| `unreadable_planes` | Error | Pixel data cannot be decoded. Export again, or exclude the image. |
| `invalid_bin_count` | Error | Bin count outside the allowed range. Use the default or a value in range. |
| `no_usable_planes` | Error | No plane has finite pixel values. Check the images. |
| `tiff_outside_leaf` | Error | Images sit in a folder that also holds subfolders, so they have no condition or repeat of their own. A folder holds either images or subfolders, never both. Move the images into a folder of their own, or out of the experiment folder. |
| `non_tiff_file`, `empty_folder` | Warning | Files or folders that are skipped. |
| `image_named_as_mask` | Warning | An image is named like a mask of another image (`f1_mask.tif` beside `f1.tif`; the endings `_mask`, `_labels`, `_label`, `_seg`, `_segmentation`), so a mask would be analysed as an image. If it is a mask, move it into a masks folder outside the experiment; if it is an image, nothing to do. |
| `mask_folder_in_experiment` | Warning | A folder inside the experiment is named like a masks folder (`masks`, `ctrl_masks`, `CellLabels`) and holds files named like masks of the images; they would be analysed as images. Move it out of the experiment and give it as the masks folder, or rename it if these are images. |
| `planes_read_in_blocks` | Warning | Planes too large for the memory limit are read in blocks. The results are the same; nothing to do. Raise the limit to read them whole, which is faster. |
| `different_sizes` | Warning | Images have different widths or heights. Usually expected; otherwise check the camera settings. |
| `different_plane_counts` | Warning | Images have different numbers of depth slices, time points or other planes. Images with more planes weigh more in pooled statistics. Check the acquisition if this is not expected. |
| `different_pixel_sizes` | Warning | The physical pixel size or z-step in the metadata differs between images by more than 0.1 % (for example different objectives). Images outside the largest group of matching sizes are listed; when sizes only change by small steps from image to image, none is listed. Check the microscope settings if this is not intended. |
| `missing_pixel_size` | Warning | Some images give their pixel size in the metadata and others do not, so it cannot be checked. Export them again with metadata if needed. |
| `unsupported_pixel_size_unit` | Warning | An image gives its pixel size or z-step in a unit that is not converted (for example inches); only pm, Å, nm, µm, mm, cm and m are. The message names each image, axis and unit. Those sizes are not used: the image is treated as uncalibrated on those axes. Export it again with the pixel size in a supported unit if the physical size matters. |
| `unbalanced_images` | Warning | A condition and biological repeat has fewer than half or more than twice the median number of images. Check for missing, misplaced or duplicated images. |
| `repeat_missing_conditions` | Warning | A biological repeat is not found in every condition. It is still used. If it has another folder name in some condition, pair it in the experiment design (step 1). |
| `design_not_confirmed` | Error | Some repeat groups are not confirmed as comparable. Confirm them in step 1, or set `confirmed` to `true` in `experiment_design.csv`. |
| `rgb_not_possible` | Error | The image type RGB was chosen, but the images are not three channels of a whole-number pixel type. Choose fluorescence, or check the axis roles: the RGB samples must have the role C. |
| `repeat_group_conflict` | Error | A repeat group holds two folders of the same condition. Move one into another group. |
| `invalid_experiment_design` | Error | A repeat group has no usable name (empty, `NA`, `nan`, `none`, `null`, `n/a`, `<NA>`, or spaces around it), two repeat groups are spelled the same apart from upper and lower case, or a confirmation date is not written `YYYY-MM-DD`. Correct `experiment_design.csv` (the message names the line and column). |
| `missing_experiment_design`, `unreadable_experiment_design` | Error (other bricks) | A brick that pools biological repeats as `experiment_design.csv` pairs them found no such file, or one it cannot use as written by the preprocessing brick (another column order, a value with spaces around it, `TRUE` or `yes` for `true`, an empty repeat group, a byte-order mark from a spreadsheet program, ...). Run the preprocessing brick on the experiment again: its run writes the file as other bricks read it. |
| `unreadable_experiment_design` | Error (`load_design`, notebook step 1, `check`) | The saved `experiment_design.csv` in the output folder cannot be read even leniently (for example a date a spreadsheet program rewrote as 26/10/2026, or a repeat group `NA`); the message names the line and column. The file is left as it is. Correct it, or answer the questions again in step 1, which replaces it. |
| `changed_experiment_design` | Error (other bricks) | `experiment_design.csv` is not the file the preprocessing run that wrote the statistics used (its SHA-256 differs from the one in the run manifest). Give that run's design, or run the preprocessing brick again. |
| `image_intensity_outlier` | Warning (intensity check) | An image's median or high percentile in a channel is far from the other images of its condition and biological repeat. Look at it: it may be out of focus, contain debris or bubbles, or have been acquired differently. Exclude it if it is faulty. |
| `repeat_intensity_shift` | Warning (intensity check) | A biological repeat is brighter or darker than the other repeats of its condition. Check whether it was prepared or acquired differently; normalising per biological repeat in later bricks removes such differences. |
| `saturated_image` | Warning (intensity check) | More than the limit (default 1 %) of an image's pixels in a channel are at the largest value of its pixel type, so their true intensity is lost. Check the exposure, gain or laser power. |
| `no_intensity_spread` | Warning (intensity check) | An image's channel has the same 1st and 99.8th percentile (empty or blank), so it cannot be normalised on its own. Check the channel, or exclude the image. |
| `channel_names_differ` | Error | Channel names differ between images at the same channel number, so different stains would be pooled as one channel. Spaces at the start or end of a name are ignored (and noted in the manifest); otherwise names must match exactly, and names that differ only in inner spaces are quoted and reported as such. Check the channel order; export the images again with the same channel order and names, or exclude the images whose channels differ. |
| `non_finite_pixels` | Warning | NaN or infinite pixels are left out of the histograms and counted. |
| `no_finite_pixels` | Warning | Some planes have a channel without finite pixels; those channels are left out of group statistics. |
| `excluded_images`, `excluded_path_not_found` | Warning | Images excluded as requested, or an excluded path that matches no image. |
| `masks_folder_not_found` | Error | The masks folder does not exist. Select the folder that holds one folder per kind of mask. |
| `masks_inside_experiment` | Error | The masks folder is inside the experiment folder, where its folders would be read as conditions and repeats. Move it next to the experiment folder. |
| `mask_names_differ_in_case` | Error | Two kinds of mask have names that differ only in upper/lower case. Rename one. |
| `duplicate_mask` | Error | An image has two masks of one kind (two extensions, or a file and an OME-Zarr label image of the same name). Keep one. |
| `unreadable_mask` | Error | A mask cannot be opened, or holds several images, or the `labels` group of an OME-Zarr image cannot be read (it must list its label images). Save the mask again as a TIFF label image, or repair the OME-Zarr labels. |
| `mask_not_2d` | Error | A mask has dimensions other than Y and X that its metadata does not name as depth (Z) or time (T), for example a plain stack. (A mask one pixel high or wide is a 2D mask, not this error.) Save it as a 2D label image, or with its axes named (an ImageJ hyperstack or OME-TIFF with axes TYX or ZYX). |
| `mask_planes_differ` | Error | The masks of one image have different numbers of depth slices or time points. Make every mask of an image with the image's planes. |
| `mask_planes_mismatch` | Error (`run`) | A mask does not have the depth slices and time points of its image under the confirmed axis roles: a 2D mask fits only an image with one plane (not a Z-stack or time-lapse), and a mask with Z or T needs as many slices or time points as its image. Save one mask plane per image plane, with the axes named, or check the axis roles. |
| `mask_size_mismatch` | Error | A mask does not have the width and height of its image. Check that it was made from that image at full resolution. |
| `mask_pixel_type` | Error | A mask's pixels are not whole numbers (for example decimals). Save it as an integer label image. |
| `mask_missing` | Warning | Some images have no mask of a kind that other images have; their objects of that kind cannot be measured. Add the masks, or exclude the images. |
| `ambiguous_mask` | Error | A mask's name could belong to two images (one image name starts another's) or fit both layouts. Rename the masks or the images so each names one image and one kind. |
| `mask_without_image`, `mask_outside_type_folder` | Warning | Files in the masks folder that match no analysed image (check the folder and file names), or other files directly in the masks folder. They are not used. |
| `images_not_in_manifest`, `images_missing`, `images_changed`, `position_id_not_recorded`, `identity_differs`, `axis_mapping_mismatch` | Error (other bricks) | A later brick checked its images against this brick's run (`check_sources`): an image the run did not analyse, no longer there, changed since, recorded with other labels or position (or by a version that did not record positions), or read with axes the run gave other roles. Run this brick again on the experiment as it is, or use the run's labels and axis roles. |
| `zarr_not_installed` | Error | OME-Zarr images, or converting TIFF images, need the `zarr` extra. Install it, exclude the OME-Zarr images, or read the TIFF images directly. |

## 9. Python interface

Everything the notebook does is available as functions:

```python
from morphobricks_normalization import read_plane_histograms
from morphobricks_preprocessing import preflight, run_experiment

check = preflight("/data/exp1")  # pass 1: folders and headers
print(check.issues)
mapping = check.suggested_mapping  # AxisMapping; edit with mapping.with_role(...)
result = run_experiment("/data/exp1", mapping)
records = read_plane_histograms(result.csv_path)
```

The experiment design is optional in Python: without `design=`, the run neither asks for
nor writes one. To record it as the notebook does (`load_design` returns the saved design
brought up to date with the folders, or the default design when there is no file yet; a
saved file that cannot be read raises `DesignCheckError` instead, never a default design;
breaking change for the next release, plan §12 question 43: until v0.3.4 it returned a
third value, `problem`, with the default design):

```python
from morphobricks_preprocessing import load_design

# An existing file that cannot be read raises DesignCheckError (unreadable_experiment_design,
# naming the line and column) and is left as it is: correct it, do not overwrite it.
design, changes = load_design(result.output_folder, check.dataset.repeats)
design = design.with_group(("treated", "rep2_day2"), "rep2")  # pair a repeat
for group in design.groups:
    design = design.with_confirmed(group, True, "2026-10-26")
result = run_experiment("/data/exp1", mapping, design=design)
pairing = design.repeat_groups()  # {(condition, biological_repeat): repeat_group}
```

Later bricks read the file with `read_experiment_design(path)`.

The intensity checks of a written table are in `result.intensity` (summaries and the
normalization brick's warnings, each with its channel); `check_table(path)` checks any
table:

```python
from morphobricks_normalization import IntensityCheckSettings
from morphobricks_preprocessing import check_table, repeat_boxes

limits = IntensityCheckSettings(outlier_limit=4)
result = run_experiment("/data/exp1", mapping, intensity_settings=limits)
for issue in result.intensity.issues:
    print(issue.code, issue.channel, issue.items, issue.message)
records, report = check_table(result.csv_path, limits)
boxes = repeat_boxes(report, channel=0)  # what the notebook's box plots show
```

The image type is optional too: `run_experiment(..., image_type="rgb")` records it in the
manifest, and without it the manifest records none. `review_image_type` gives the
suggestion and whether RGB is possible, from the headers and the axis roles:

```python
from morphobricks_preprocessing import review_axes, review_image_type

suggestion = review_image_type(check.metadata, review_axes(check.metadata, mapping))
print(suggestion.suggested, suggestion.reason, suggestion.rgb_problem)
result = run_experiment("/data/exp1", mapping, image_type=suggestion.suggested)
```

Masks are found from their headers by `preflight` and read by `scan_masks`:

```python
from morphobricks_preprocessing import preflight, scan_masks, write_mask_report

check = preflight("/data/exp1", masks_folder="/data/exp1_masks")
masks = check.masks  # MaskInventory: types, files, issues
cells = masks.for_image("control/bio_rep1/tech_rep1/img_01.tif")["Cells"]  # MaskFile
report = scan_masks(masks, "/data/exp1")  # reads the pixels, one image at a time
report.hierarchy  # {"Nuclei": ("Cells",), "Cells": (), ...}: direct parents
write_mask_report(report, "/data/exp1_MorphoBricks_Preprocessing/mask_relationships.json")
```

`scan_masks(..., thresholds={"inside_object_share": 0.8})` changes a threshold (the others
keep their defaults, `DEFAULT_THRESHOLDS`). `preflight(..., ome_zarr_labels=True)` uses the
label images of OME-Zarr images without a masks folder; `read_mask` reads one mask's pixels
(a `ValueError` for a negative label or a label above 2,147,483,647, the int32 limit of
every MorphoBricks brick, as in `piece_counts`, `pair_counts` and `scan_masks`; `check_labels`
is that check alone);
`pair_counts` and `piece_counts` give the counts behind the report for one pair of masks or
one mask.

Reading the table, group statistics and normalisation parameters come from the
normalization brick (`morphobricks_normalization`, installed with this package):
`read_plane_histograms`, `PooledHistogram`, `group_records`, `min_max`,
`percentile_range`, `z_score` and `robust_z_score`. The same names imported from
`morphobricks_preprocessing` still work for one version, with a warning. The rules
behind them are in [contract.md](contract.md); the design in [plan.md](plan.md).
