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

You need Python 3.11 or newer and JupyterLab (or Google Colab). Download
[`notebooks/MorphoBricks_Preprocessing.ipynb`](../notebooks/MorphoBricks_Preprocessing.ipynb),
open it and run its three code cells in order:

1. **Install MorphoBricks preprocessing** prepares the environment and prints the versions.
   Running it again installs nothing that is already there; no restart is needed.
2. **Mount Google Drive** connects Google Drive on Google Colab. In JupyterLab or
   LabConstrictor it prints a one-line note and does nothing.
3. **Run preprocessing** displays the complete preprocessing widget app. Run before cell 1,
   it stops with one line saying to run cell 1. On Colab without Drive mounted it opens the
   app after one line: "Google Drive is not mounted: only files on this Colab runtime are
   visible, and they are deleted when the runtime stops." It never runs an earlier cell for
   you.

Cell 1 chooses one source:

1. **Nearby checkout:** when the kernel is running in this repository or one of its subfolders,
   use that checkout's source code. The checkout must be the commit the notebook pins
   (`RELEASE_REF`) with no uncommitted changes, and setup prints its commit and tree state. Any
   other commit or edited tree is refused; developers set `DEVELOPMENT_OVERRIDE = True` at the
   top of cell 1 to use it, and the printed header then says so. An editable install
   (`pip install -e`) of a checkout elsewhere follows the same rule: used when it is the clean
   `RELEASE_REF` commit, otherwise refused (never replaced, since a developer set it up).
   `DEVELOPMENT_OVERRIDE` covers local checkouts only: an installed package of another commit
   is still replaced.
2. **Matching installed package:** otherwise, reuse an installed preprocessing package only
   when pip recorded (its `direct_url.json`, PEP 610) that it installed `RELEASE_REF` from the
   notebook's GitHub repository. The version number alone is never enough: the same version
   built from another commit, another repository or no record is replaced by `RELEASE_REF`
   (#371). pip would keep such a package (same version), so cell 1 reinstalls the package
   alone at `RELEASE_REF` (`--force-reinstall --no-deps`) before installing what is missing.
3. **Pinned GitHub source:** otherwise install the notebook's immutable `RELEASE_REF`, never
   moving `main`.

Missing notebook dependencies are installed into the same Python environment as the kernel.
A local checkout is installed in editable mode when it needs dependencies. Setup prints the
preprocessing version and commit, the normalization and ipywidgets versions, the notebook
version and the selected source; after an installation it reads the commit again and stops if
pip did not install `RELEASE_REF`, naming the `pip install --force-reinstall --no-deps` command
that replaces it (a restart would not help there). Installation needs internet access, and the GitHub option needs Git. If another copy
of the package is already loaded, restart the kernel before switching source checkouts. When
pip replaces a preprocessing the kernel had already imported, cell 1 stops and asks for a
restart (the loaded module stays the old copy until then); run it again after the restart: it
installs nothing more. The Cellpose notebook's cell 1 does the same. Both also stop when the
package the kernel imports is not the copy pip installed (for example when the working folder
is a checkout's `src` folder): pip's record would then name a commit that is not the code
running.

To install everything manually instead, run these commands in the environment used by
your notebook kernel:

```bash
pip install "morphobricks-preprocessing[notebook] @ git+https://github.com/CellMigrationLab/morphobricks-preprocessing.git"
pip install jupyterlab
```

To read OME-Zarr images, install the `zarr` extra too:
`pip install "morphobricks-preprocessing[notebook,zarr] @ git+https://github.com/CellMigrationLab/morphobricks-preprocessing.git"`.

Start JupyterLab with `jupyter lab` and open the notebook. **Browse…** opens your computer's
folder dialog; where no dialog can open (Google Colab, a remote server), it says so and you
paste the path instead.

### Google Colab

Open the notebook in Google Colab (**File › Open notebook › GitHub**, or upload the file) and
run the notebook's three code cells in order. On Colab:

- cell 1 installs the notebook's pinned preprocessing revision, with its notebook
  dependencies and the `zarr` extra when needed;
- cell 2 connects your Google Drive: allow access in the window that opens. Your Drive is then
  the folder `/content/drive/MyDrive`. Cell 3 refuses to open the app until Drive is
  connected, so the data and the output are never silently put on the runtime's own disk;
- uses Colab's native core ipywidgets support. The notebook accepts ipywidgets 7.7 or 8 and
  does not force a widget upgrade, runtime restart or custom-widget manager.

**Where to keep the data.** A Colab runtime is a temporary computer: everything on it except
Google Drive is wiped when it disconnects (after some idle time, or after several hours).

- Put the experiment folder on Google Drive and paste its path in step 1, for example
  `/content/drive/MyDrive/experiment_2026_10`. **Browse…** cannot open a dialog on Colab: open
  the **Files** panel in the left bar, right-click the folder, choose **Copy path** and paste.
- Keep the output folder on Google Drive too, outside the experiment folder. The default,
  next to the experiment folder, is right when the experiment is on Drive.
- Keep both there for every session. The output records where the images were read (their
  full path) and each image's size and modification time; a later run reuses the rows only
  when the images are read from the same place. Do not copy the experiment to the runtime's
  own disk to read it faster: the copy is lost when the runtime disconnects, and reading it
  from another place recomputes every row.

**Limits of Google Drive** (the run behaves the same; these are things to know):

- **One session per output folder.** On Google Drive the lock cannot guard an output folder
  across machines or sessions. It only stops a second run in the same Colab runtime. Google
  Drive copies files between computers with a delay, so a run started at the same time from
  another Colab session, or from a computer that syncs the same Drive folder, is not
  stopped. Use one session at a time per output folder on Drive.
- **A lock left by a disconnected runtime.** If the runtime disconnects during a check or a
  run, its lock stays in the output folder and the next check shows **The output folder is
  locked**, naming the run that holds it (computer, process ID, start time). When you are
  sure no other session uses the folder, press **Remove the lock of a stopped run**, then
  check the folder again. The button never removes the lock of a process still running on
  the same computer; outside the notebook, `remove_stale_lock(output_folder)` or
  `--remove-stale-lock` do the same.

- **Unchanged images read again.** An image counts as unchanged when its size and
  modification time are the same as at the last run. If Google Drive reports a file's
  time differently later (for example after the file was synced again from another
  computer), the image is read again. That costs time but never reuses wrong rows. The
  other way round is not seen: a file rewritten with the same size and its modification
  time set back (by a tool that restores times) counts as unchanged, and its earlier rows
  are kept. Every run therefore compares each image's SHA-256 too (**Verify each image's
  content**, ticked by default; CLI `--verify-content`), and every image is read once more per
  run. Untick it (CLI `--no-verify-content`) to compare size and time only; the manifest then
  records `verify_content: false`. An output folder keeps the choice of its earlier run.

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
  holding such files, are reported: an image named as a mask is an error (`image_named_as_mask`:
  rename it or exclude it), a masks folder inside the experiment a warning
  (`mask_folder_in_experiment`).
- Each image is one position (field of view), named after its file without the extension:
  `img_01.tif` and `img_01.ome.zarr` are both position `img_01`. The same name may be used
  in different folders (`tech_rep1/img_01.tif` and `tech_rep2/img_01.tif` are two
  positions), but two images in one folder must not share a name apart from the extension
  or upper/lower case. That is an error; rename one or move it out of the experiment.
- A fourth level of folders (below the technical repeat) is an error, so that no layer of
  images is dropped silently.
- Other files (`.txt`, `.csv`, ...) are reported and skipped. System files such as
  `.DS_Store`, `Thumbs.db`, `desktop.ini` and `__MACOSX` are ignored without a message.
- Links (symbolic links, and junctions on Windows) are followed only when they stay inside
  the experiment folder; the same holds for the masks folder. A link to a file or folder
  outside it is an error (`link_outside_folder`) naming the link and its target: nothing
  outside the folder you selected is read, so copy the data in or select a folder that
  holds it. A link back to a folder above it is an error (`link_loop`), and so is one file
  reached by two paths, a link and its target or two hard links (`linked_twice`): it would
  count twice.
- A mounted folder inside the experiment or masks folder (another disk, a network share, or
  a Linux bind mount of another folder) is an error (`mounted_folder`) naming it: its files
  come from outside the folder you selected. Copy the files in, or select the mounted folder
  itself. The selected folder may itself be on any disk or share.
- An image or mask that has other names (a hard link, perhaps outside the folder) is a
  warning (`hard_linked_file`): it can change through a name the run does not see.

To change a label, rename the folder and check again.

### Masks (optional)

If you already have masks (segmentations of cells, nuclei, nucleoli, ...), keep them in a
**masks folder** next to the experiment folder, not inside it, and keep the experiment, the
named-axis copies (`convert`) and the output folder out of the masks folder: everything in it
is read as masks, so these are refused (`experiment_inside_masks`, `copies_inside_masks`,
`output_inside_masks`). Make one folder per kind of
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
- A 2D mask belongs to an image with one plane. For a time-lapse, the mask needs the same
  time points as its image, one plane per time point, with its axes named in its metadata
  (an ImageJ hyperstack or OME-TIFF with axes `TYX`), or stated once for its kind of mask
  when its file does not name them (`--mask-axes Cells=TYX`, §5, or **Mask axes** in the
  notebook's step 1, §4). A plain stack whose axis could be depth or time is refused until
  its axes are stated.
- A 2D mask is never used for every plane of a time-lapse, whatever its metadata says:
  `run` stops with `mask_planes_mismatch`. Save one mask plane per time point. (The release
  candidates of v0.4.0 offered `--mask-axes Nuclei=YX` for one 2D mask shared by every
  plane; that option was removed and is now the error `shared_mask_not_supported`.)
- You may have any number of kinds of mask. Their folder names (or the part after the image
  name) are their names: they must differ by more than upper/lower case. If one image's name
  starts another's (`a.tif` and `a_b.tif`), a mask named `a_b_cells.tif` could belong to
  either and is refused: rename the masks or the images.
- The label images stored inside an OME-Zarr image (its `labels` group) are used as masks
  too, named after the label image.
- An image without a mask of one kind is reported; its objects of that kind cannot be
  measured.

Masks do not change `plane_histograms.csv`. They are checked and recorded for the bricks
that measure objects; the optional `masks` command (§5), or **Describe the masks** in the
notebook's step 3 (§4), also tells how they relate.

### Making masks with Cellpose (optional)

[`notebooks/MorphoBricks_Cellpose.ipynb`](../notebooks/MorphoBricks_Cellpose.ipynb) makes masks
for a run you already have (run preprocessing first and describe the experiment). Its cells:
install (Cellpose 4.2.1.1 and the preprocessing commit it is made for), Google Drive, open the
run's output folder and a masks folder, choose the output and the **channels by channel ID**,
choose the model, device and settings and load the model, preview one example result (a
dropdown of the run's images and Cellpose's own result panels, as in the ZeroCostDL4Mic
Cellpose notebook; nothing is written), and segment the whole experiment. From cell 3 on, each cell shows a form of widgets, the same in Google
Colab, JupyterLab and LabConstrictor: fill it in and press its button (**Open the run**,
**Choose the channels**, **Load the model**, **Preview**, **Segment the whole experiment**). You never edit
code. Folders are typed or pasted, or chosen with **Browse…** where a dialog can open (not on
Colab: copy the path from the **Files** panel). A refusal is shown under the button in a red card,
with the same message as before; change the form and press the button again. Its
`DEVELOPMENT_OVERRIDE = True` (top of cell 1, for developers) differs from the Preprocessing
notebook's: it keeps whatever preprocessing is installed, of any commit or none recorded (the
banner then says "commit unknown" and that the override is on). Then:

- **Output and channels.** You choose the output, a **cell mask** or a **nuclei mask** (the
  mask type follows it, `Cells` or `Nuclei`, and stays editable), and channels by ID (`DNA`,
  `ACTIN`), never by position: a **cell channel** (cytoplasm or cell border) and a **nuclear
  channel**, both optional but at least one. A cell mask reads the cell channel, then the
  nuclear one if chosen (a cell channel is required); a nuclei mask reads the nuclear channel
  only (required). The record names each channel's role and the output.
- **Both.** The output **Both** segments each image twice: the cells from the cell channel and
  the nuclear channel (stacked as cell, nuclear), the nuclei from the nuclear channel alone. It
  needs both channels. The two are separate mask types (`Cells` and `Nuclei` by default, both
  editable and different), each with its own `segmentation_record.json`, its own skip, overwrite
  and rollback and its own table; a batch that stops does not stop the other. One model and
  one set of settings serve both unless, under **Advanced Cellpose settings**, you tick a
  different model or settings for the nuclei. The preview shows both results. The notebook
  never pairs a cell with a nucleus by label number: relate them downstream from the geometry
  (MorphoRelate, containment or overlap).
- **Channels by number.** Tags (channel IDs) are the default. You may instead choose **By
  number** and type a channel's number (its C position, 1 for the first), only when every
  image of the run has the same channel IDs at the same positions: the number is translated
  once to that ID, resolved like a tag, and the record keeps the ID with the number typed
  (`channel_number` in `channel_roles`). When the layouts differ (3 channels in some repeats
  and 4 in others) the number is refused with the images of each layout named: choose the
  channel by its tag. A number is never resolved image by image.
- **Partial channels.** When some images
  have a channel that others lack (a fourth stain in one biological repeat), only the channels
  every image has can be used: the dropdown says which channel is partial, and choosing it
  shows preprocessing's refusal (`A_SCOPE_INCOMPLETE`) unchanged. Cellpose reads at most three
  channels, so more are refused.
- **Model.** `cpsam_v2` (default), `cpsam`, `cpdino`, `cpdino-vitb` or a model file of your
  own. A name or file that does not exist is refused: Cellpose itself would silently use
  `cpsam_v2`. The weights of a built-in model are downloaded from Hugging Face on first use,
  only if you tick the download box. `cpdino` models also need DINOv3, installed at a pinned
  commit (the head of its default branch on 22 November 2026, not verified against `cpdino`; installed without its dependencies) only if you tick its box. Restart the runtime after installing it, then run the cells again.
- **Device.** The model form says, before anything loads, whether a GPU was found and which.
  You choose `cuda`, `mps` or `cpu`. A GPU that is not available is refused, never
  replaced by the CPU, and after loading every parameter and buffer of the network must be on
  the device you chose. Cellpose may still move its flow-threshold quality step to the CPU when
  GPU memory is short, and always computes the masks on the CPU on `mps`; that is Cellpose's
  behaviour, it is not refused, and the record notes it.
- **Advanced Cellpose settings** (a closed section of the model form): the diameter, the flow
  and cell probability thresholds (0.4 and 0) and the batch size (8).
- **Diameter** is in pixels. Blank means the model's own native scale (Cellpose 4.2.1.1 does
  not estimate a diameter); 0 is refused.
- **Normalisation** is always Cellpose's own, per image (each channel to its 1st to 99th
  percentile); it is not a choice, and images are never sent to Cellpose unnormalised. The full
  normalisation is written to the record.
- **2D only.** An image with more than one Z plane or time point fails with a message that
  says so.
- **Result.** One mask per image at `<masks>/<type>/<the image's folders>/<image>.tif`
  (2D, `int32`, 0 is background, the image's width and height), never over an existing mask
  unless you tick overwrite, and `<masks>/<type>/segmentation_record.json` with the model's
  file name and SHA-256, every setting (the full normalisation and the flow iterations included), whether the weights were downloaded now and from where, the channels, the device and each image as written,
  skipped or failed. The final table lists every image, with the error of a failed one.
- **A mask type is its exact name.** A mask type whose name differs from a folder already in
  the masks folder only in upper/lower case (`Cells` when `cells/` is there, even empty) is
  refused when you choose it, on every computer, naming both (`mask_type_differs_in_case`): on
  Windows and macOS the two are one folder, so the masks of two types would be mixed. Type the
  existing folder's exact name (`cells`) to add to its masks, or choose another mask type.
  Nothing is renamed or merged.

Afterwards declare the mask type (both mask types for **Both**) in the experiment description (for example `[masks.Cells]`
with `source_type = "Cells"` and `representation = "instances"`), save it, run preprocessing
again with the masks folder, **Describe the masks**, then continue with MorphoFeature.

### Preprocessing, Cellpose and the masks in one notebook (optional)

[`notebooks/MorphoBricks_Preprocessing_Cellpose.ipynb`](../notebooks/MorphoBricks_Preprocessing_Cellpose.ipynb)
does the whole flow. Its cells 1 to 3 are exactly those of the Preprocessing notebook (cell 1
pins its own preprocessing commit, the one holding cells 4 and 5; it never installs Cellpose):
run the experiment in the app of cell 3. Then:

- **4. Segment with Cellpose** segments the run the app just finished, in the masks folder
  that run uses (the masks folder the app checked, else the one the run recorded; type one
  under **Open the run** when it has none). With no finished run in the app (none yet, or a
  run that stopped) it says so and names cell 3; you may also type the output folder of an
  earlier finished run. A run whose images changed since it ran is refused. The first time,
  Cellpose 4.2.1.1 and PyTorch are installed, only when you tick **Install Cellpose 4.2.1.1
  (with PyTorch) now** and press the button; if the installation changes `numpy` or `opencv`
  while they are loaded, restart the runtime, run cells 1 to 3 again (run preprocessing again
  in cell 3: the histograms are reused), then cell 4. Then the same steps as the Cellpose
  notebook above, one under the other; their messages name the step (for example *Press Load
  the model first*), and **What to do next** points to cell 5.
- **5. Register the masks** shows, for each mask type cell 4 wrote, the declaration it would
  add (`[masks.Cells]` with `source_type = "Cells"` and `representation = "instances"`). Tick
  each one and press **Register the masks**: the app of cell 3 then checks the folders again
  with the masks folder, answers the mask on **Masks**, saves the description, runs again
  (the histograms of unchanged images are reused) and describes the masks. Masks written into
  the masks folder the run already used are fine. If its folder check asks you to confirm the
  axes again, do it in cell 3 (step **Axes**) and press the button again (or run cell 5
  again). It refuses, changing nothing, a mask type that collides with a mask already declared,
  a mask type cell 4 wrote beside a folder of the masks folder whose name differs from it only
  in case (`mask_type_differs_in_case`),
  a masks folder inside the experiment, masks of another run or masks folder than the app's,
  images changed since the run, a run cell 3 published again after cell 4 opened it (open the
  run again in cell 4 and segment again), and, after that stop, axes other than those the
  segmented run recorded or another experiment in cell 3.

## 3. Supported images

- **TIFF files:** `.tif` and `.tiff` (any case), including OME-TIFF and ImageJ hyperstacks.
  The axis roles must agree with the axes their metadata names (§4 step 2); a file whose
  axes you know better than its header is saved as a named-axis copy first. A plain stack
  of pages without axis metadata is read with the roles you confirm (a warning).
- **OME-Zarr images:** OME-NGFF 0.4 and 0.5 (needs the `zarr` extra; 0.5 needs Python 3.11
  or newer). Their axes (time, channel, z, y, x) come from their metadata, and they are
  read only with those roles: roles that give an axis another role (its depth as time,
  say) are an error, and an image whose metadata do not name its axes is not read (the
  axes are never guessed). Only the
  full-resolution level of a single image is read; a folder holding several images (a
  plate, or a bioformats2raw export with several series) is an error: export each image
  separately or exclude it.
- **Pixel types:** 8-, 16-, 32- and 64-bit signed or unsigned integers, 32- and 64-bit
  floats. All images of an experiment must have the same pixel type.
- **2-D planes only:** MorphoBricks v1 analyses 2-D planes. An image with more than one
  depth slice (a Z-stack) is refused (`z_stack_not_supported`), every such image named:
  make recorded 2-D projected copies first with `convert --project max|mean|sum` (below),
  and use the copies. A Z axis of one slice is fine. Time-lapses are supported.
- **Dimensions:** every image is split into planes along T (time); a plane holds every
  channel (C) of the image at that time point, one row of the table. All TIFF
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

Run the notebook's three cells from top to bottom (install, mount Google Drive, run
preprocessing). Cell 3 opens the app: a strip of seven steps, one on screen at a time, with
**Back** and **Next** under it. **Next** is never blocked, and a chip of the strip opens its
step; a green chip with a check mark is done.

The page is one column and scrolls as a whole. Each step shows what you must answer;
explanations are under **About this step** and details are in collapsed panels. Every wait
shows a bar with what is done of the total, the file being read and the time. Every greyed-out
button has a line beside it saying what it waits for. Nothing is decided for you: what the app
proposes is recorded only when you click.

**Looking at images.** Every step that shows images has the same viewer: four lists that
narrow each other (condition, biological repeat, technical repeat, image; a level with one
value is not shown), a **t (time point)** slider and a **z (depth slice)** slider when the
image has those axes (it opens on the first time point and the middle slice), and every
channel of that plane side by side (`c=0`, `c=1`, …, each with its own contrast; at most six,
and then it says *6 of N channels shown*; a colour image is one colour picture). **Larger
pictures** draws them larger. The line under the pictures names the image, the plane, its size
and which axis roles it was read with: the file header's (not confirmed yet), the ones you are
editing, or the confirmed ones. An image that cannot be drawn gets a sentence saying why.

1. **Images.** Give the folders (images; masks and output are optional) and click **Scan the
   folders**. The scan lists the conditions and folders without reading any image header and
   shows one image chosen at random in the viewer, for looking only; any other image can be
   chosen there. Under the pictures, **Axes of this image** proposes a role for each of its
   dimensions from its header. If a picture looks wrong (a strip, stripes, channels that are
   really slices), change a role right there: the pictures are redrawn, and a header that
   names no axes gets its picture once you say what its dimensions are. What you leave there
   is the proposal for every image; nothing is confirmed yet. If this is your experiment,
   click **Looks right: read all image headers**: every header is read and the proposal is
   checked against every image (one line says whether it fits them all). The image type
   (separate channels or colour RGB) is shown too, from the headers, for information only:
   nothing is recorded there, it is confirmed on Channels. The viewer then
   shows the images as the check read them; the folders found, the check's
   report (formatted: a summary, then tables with the details folded) and **Exclude images**
   are collapsed below.
   If the check itself fails or is interrupted (for example Google Drive stopped answering),
   nothing of an earlier check stays shown as valid on any step: its labels, roles, offers,
   pictures, recommended bins and confirmations are cleared, and a red message names the
   folder and the error (the full report is in **Technical details**).
   Make sure Google Drive is mounted and the folder is there, then click the button again.

   **Coming back to earlier work.** As soon as the output folder is set (typed, chosen, or the
   default next to a new experiment folder) and no check is on screen, the app looks at what
   the folder holds and restores it with a message at the top: *Found earlier work from
   &lt;date&gt;: restored &lt;what&gt;*, the output folder, what was reused and what was read
   again, and the step it opened on. When the experiment folder box is empty it is filled
   from the earlier work; a different experiment folder restores nothing. What it restores:
   - **The folder check.** Each check keeps the headers it read in `check_headers.json`. An
     image whose size and modification time (its fingerprint) are unchanged keeps its recorded
     header and is not opened again; every new or changed image is read, and the message names
     them. A record of another experiment folder, of another version of the software, or that
     cannot be read is not used: every header is read again, and the message says why. Mask
     headers, and the first page of each TIFF (which says whether it is a projected copy), are
     always read again.
   - **Your answers.** The saved answers (`morphobricks.draft.json`), the saved description
     (`morphobricks.toml`) and the earlier run's settings are read as **Check folder** reads
     them, and the steps open on the first step still to do. Nothing is confirmed for you: the
     axis roles, for one, are offered for one click.
   - **A finished run.** When the run is of this experiment and of the saved description as it
     is, with every image unchanged and its table as written, **Run** opens with its report,
     its intensity checks and what to do next (in the Preprocessing and Cellpose notebook, cell
     4 segments this run). Otherwise the message says why it is not current.

   **Check folder** (after **Scan the folders** on the stepper) reads every header again.
   **Start fresh** forgets what was restored and starts a new session: no file is changed or
   deleted, and Check folder still reads the answers saved in the output folder (choose
   another output folder to start without them). If the earlier work cannot be read, a red
   message says so and nothing of it is used.
2. **Axes.** What the files hold, in two parts. The roles start from the proposal of step
   1 (the header's, or what you chose on the example) and can be changed here at any time;
   once confirmed, **Change axis roles** takes the confirmation back. The instant one of these
   buttons is clicked it is greyed out and says *Working…*, with a progress bar of the parts
   of the confirmation under it; if something fails part-way, the error is said there in red.
   - **Axis roles:** one row per image dimension with its role, and under them the viewer
     with one image as these roles read it, redrawn whenever you change a role (a wrong role
     looks wrong: a strip, stripes); choose any other image, time point or slice there. Click **Confirm these axes** (offered when the headers name
     every axis), or **Yes, the example looks right** and then **Confirm axis roles**.
   - **Pixel size:** one line, for example *Pixel size: 0.108 × 0.108 µm (from the headers,
     same in all 270 images)*. When the headers give none, type it once under **Pixel size X**
     (**Y** follows while **same for X and Y** is ticked; untick it to type another Y) and
     click **Use for all images**, or leave the images uncalibrated; nothing is guessed. When the headers
     disagree, the line says how many images have which size; with three or more sizes it
     gives their range instead (*Pixel sizes differ slightly: 0.10047–0.10054 µm (spread
     0.07 %) across 138 images, 25 combinations*), with the sizes in a folded table. Nothing
     is chosen for you: until you choose, each image is recorded with the size of its own
     header. Either state one size for all images (it is shown what it replaces before it is
     written, and it is recorded as stated by you), or state sizes per folder under **Set per
     imaging unit…**.
3. **Channels.** First the **image type**: what the channel dimension holds, *separate
   stains or markers* or *colour (RGB)*. It is proposed from the image headers (only when
   they all agree) and recorded when you click **Use for all folders**. **Set per folder…**
   holds one row per folder, with its picture; it opens when the headers differ. An
   experiment that mixes colour and separate-channel folders is refused in this version.
   When a folder's images store three values per pixel side by side but their header does
   not say colour (some scanners save colour this way), the card explains it and offers
   **Read these folders as colour (RGB) although their header does not say colour**. It is
   never ticked for you and can be ticked once at least one of those folders is answered colour
   (RGB); ticking it records `rgb_header_override = true` for each of them answered colour,
   exactly as writing it by hand.
   If only some of them have it (for example a folder answered colour after the tick), the box
   stays unticked and the card names the folders that have it.
   Colour folders have the channels R, G and B. Otherwise name what you stained and confirm the list; then tag each folder's channels
   with those names, looking at any image in the viewer (each channel shows its header name
   and the tag chosen for it). **Fast mode** is the default: **Every imaging unit has the same
   channels in the same order** is ticked, your declared assumption (never a guess). A folder
   whose image header names every channel with one of your labels, exactly as written (or with
   a header name already saved for that label), is tagged from its header with no click. Tag the
   row *Tags of every imaging unit* once, looking at the first imaging unit (the first tagged
   unit): the tags fill every unit without header names, one row per unit (grouped by condition
   / biological repeat, then technical repeat) and one column per C index. Then go on, or change
   any cell of any unit: that unit is marked *edited*, the others *same as the first tagged
   unit*. What is saved is exactly what tagging each unit by hand saves. A contradiction is
   refused, never filled: a unit with another number of channels, or whose header names are in
   another order or are other names, stops the step, which lists every such unit with the fix,
   **Tag this unit on its own** (or all refused units at once); nothing is reordered or guessed.
   **Add another layout** tags several units with one other number of channels with a row of
   their own. Colour (RGB) folders keep their own path. On a reopen, a new unit without header
   names is shown filled and saved only by **Tag the new units like the first tagged unit**.
   **Tag this repeat folder (all conditions)**: one row of tags per biological-repeat folder
   name (and channel count). Set the row once (for example the four channels of `rep2`, whose
   acquisitions have an extra channel) and click the button: every condition's acquisitions in
   that folder, technical repeats included, take these tags, each keeping its own list. An
   acquisition with another number of channels is in its own row; one whose header names
   contradict the row is listed and not filled, and the description is not saved while it is
   listed (tag it on its own). An acquisition whose own images disagree (other header names or
   another number of channels) is never filled either: it is listed, and its images must be
   corrected or separated. The rows go by folder name, since the channels are tagged
   before the pairing: if you pair the `rep2` folders of different conditions with different
   biological repeats, every row of that folder (whatever its channel count) says so and
   fills nothing; tag those conditions on their own.
   From a listed acquisition, **Override this unit (start from the row's tags)** opens that
   acquisition's tags with the row's tags filled in. Under each channel the header name is
   shown, and an orange box marks where it disagrees, so you change only what is different.
   Nothing is saved while you edit; the acquisition counts as tagged only after **Confirm these
   tags** (every channel tagged, each label once). If the acquisition's own header or its
   tags change later, it is listed again.
   A listed acquisition is counted only when you confirm *that* acquisition (**Confirm these
   tags**, **Confirm the same tags**, or its own folder tagged on the grid). Confirming one
   acquisition never counts another, and fast mode does not tag a listed acquisition from the
   header names saved by confirming another: it stays listed with the same contradiction and
   the same orange boxes. When a listed acquisition has the same header names, at every
   channel, as one you confirmed, it says "same headers as *that one*, which you confirmed as
   *tags*" and offers **Confirm the same tags**; when several listed acquisitions have them it
   also offers **Confirm all N like it**. Nothing is counted until you click; the buttons save
   exactly as **Confirm these tags** does (all N or none).
   Tagging a listed acquisition by hand on the grid (every channel, fitting its header) is
   saved as your confirmation of it, so it counts and can be offered as the source of **Confirm
   the same tags**. With the statement unticked, a tag chosen for a block of folders is your
   confirmation of every listed acquisition in that block: the block names them under its
   dropdowns ("A tag chosen here also confirms the refused unit(s) ..."), also when its folder
   list is folded; take an acquisition out of the block first to leave it listed. Tags that fit
   only because you confirmed *another* acquisition with the same header names, or because the
   header changed, never count it. If you rename or remove a label after an acquisition was listed, its
   override opens with that channel left blank: choose a label that still exists. What is
   listed and what you confirmed is saved with your answers: after you close and reopen the
   notebook, a listed acquisition is still listed, and still not counted, until you confirm it.
   This holds also when you stop the kernel (Interrupt) during a tag save: what is listed and
   confirmed is then read again from the saved answers, in the same session too, so an
   interrupted save never drops a listed acquisition or a confirmation.
   Two notebooks open on the same output folder never write over each other's answers: each
   notebook saves only over the draft it read or last saved. When the other one saved since,
   the change is refused with `R_DRAFT_CHANGED` ("Not saved: the draft changed"), nothing is
   written and the other notebook's listed acquisitions, confirmations and tags stay as they
   are; press **Check folder** in step 1 to read them, then give your change again.
   In fast mode this holds also when you correct the acquisition's image header so that it fits
   its row: it is not tagged from its header but stays listed ("a refused unit is never tagged
   from its header, even one that fits now; confirm its tags") until you confirm it. In fast
   mode a listed acquisition you have not confirmed also keeps its tags when you edit a
   layout's row of tags, fill new acquisitions like the first, tag several acquisitions with one
   dropdown, or use **Tag like the saved group** or a header pre-tag: only an action on it alone
   (its override, a one-click confirmation, or its own dropdowns) changes them. An
   acquisition you set to colour (RGB) keeps its listing for later: it is not listed while it is
   colour, and is listed again if you set it back to channels.
   In fast mode a row offers only the acquisitions that do not fit the layout, and those it
   fills are tagged on their own (checked as in the strict mode); unticked, a row is offered
   for every repeat folder. On a reopen, a row shows the tags every acquisition of its folder
   already holds (only a convenience: what is saved is per acquisition).
   Untick the statement for the strict per-unit page, exactly as before (the units fast mode
   tagged from their headers with no click are untagged again: their header is a proposal to
   confirm, as before; your own tags stay): folders with identical
   headers are tagged together; the list of those folders, where one can be tagged on its own,
   is one click away. On a saved experiment whose images were
   re-exported with their channels in another order, tag that folder again in the new order:
   each saved channel that moved is listed (*DNA: ctrl/rep1 channel 1 → channel 2*) with
   **Yes, these are the new positions**. Only that click records the change (experiment-v1
   §4.6, one re-confirmation per channel, written when you save); without it the save is
   refused with `R_CONFIRMED_ID_CHANGED`. That replicate's *same way?* is asked again on
   **Check and save**; every other replicate keeps its date. When a tag leaves a saved channel
   without a header name it had (the images now give another name or none, or the folder that
   gave it is gone), that channel is listed with its names saved and now (*DNA: header names
   “DAPI”, “Hoechst” → “DAPI”*) and **Yes, this header name changed**. Only that click records
   it (one re-confirmation per channel, written when you save), also when a folder is removed
   or renamed in the same save: the saved file keeps no header names per folder, so **Yes,
   remove** cannot show that a name went with its folder. A change after the click is asked
   again. **Yes, remove** and a stated folder rename confirm only the folders they show: a
   saved ID replaced by another ID in the same save (a channel `DNA` now `DAPI`) is never
   confirmed by them, and the save is refused with `R_CONFIRMED_ID_CHANGED` (#402), naming
   the change (*DNA -> DAPI*): undo the ID edit. An ID changed in the same save as a folder
   change of the same entity (a folder renamed or removed) is never paired with it: the save
   is refused with `folder_and_id_changed` (#475); undo the ID edit and the folder change
   saves on its own. A saved ID cannot be changed from the notebook: only the publication API
   takes a `Reconfirmation` that names a new ID. A whole biological repeat or condition can
   be removed (every one of its folders gone from disk, or a condition folder renamed on disk,
   which is a removed folder plus a new one): answer **Yes, remove** for each condition listed
   and save. Its ID stays in `used_ids` as retired (experiment-v1 §4.9): it is never given to
   another repeat or condition, and a folder of that name that comes back gets a new ID.
4. **Masks.** The masks the check paired with the images (and, for masks whose files do not
   name their axes, the axes to state), then for each mask type: *separate objects*,
   *regions* or *grouped labels (one value per parent, e.g. mitochondria by cell)*. Nothing is
   chosen for you, not even when **Describe the masks** finds a mask `grouped`: that is
   evidence beside your answer. **Describe the masks** reads every mask and reports how the types relate. It
   reads the masks a run recorded, so it is greyed out before the run and says so; come back
   to it after **Run**. The histogram table does not need it, but bricks that use masks accept
   only masks it has read. Without masks, the step says so. Under it, **Convert a mask
   (optional)** writes new mask files from a declared, checked mask: a binary mask's objects as
   an objects mask, or one binary mask per class of a class image (see `convert-mask`, §5).
   Choose what to write, the mask and the new mask ID (or prefix), then **Convert the mask**;
   the card lists the files written and says what to declare next. While the button is
   disabled, the line beside it says what is missing.
5. **Design.** One grid: conditions down, biological repeats across, each cell the folder of
   that repeat, and what each condition is compared against in the last column. The names are
   the folder names, folders with the same name are the same repeat, and nothing is compared.
   If that is right, click **Looks right: confirm the design**; otherwise change a comparison
   in the grid, or a name or the pairing under **Edit names or pairing…**, first.
6. **Check and save.** The viewer, your experiment in sentences and one question per replicate (*Were
   these prepared and imaged the same way?*), then **Yes, save the experiment description**.
   The line beside the button says what is still missing. Warnings of the description (for
   example a label declared that no folder uses, `W_UNUSED_CHANNEL`) are listed above the
   button; they do not stop the save.
7. **Run.** **Compute histograms**; what is still to do is listed only while something is.
   **Images read at once** (in Settings) is **Auto** by default: as many images at once as
   the CPUs allow and the **Memory cap (GiB)** fits (the command line's `--max-ram`), each in
   its own thread. The cap starts at half of the memory the computer had available when the
   notebook started (on Linux, also within the limit of its container), rounded down to
   0.1 GiB, and the line under it says the figure; change it if other programs need more or
   less, or set 0 for no cap. Choose 1 to read one image after another (the command line's
   default, `--workers 1`), or 2, 4 or 8. When the available memory cannot be read, or half of
   it is below 0.5 GiB, the notebook starts with 1. The outputs are byte-identical whatever the
   number; the speed-up is modest, because most of the histogram work holds Python's global
   interpreter lock (GIL). On macOS the figure is the computer's physical memory (no
   available figure is offered there), and the line says so. Once the axis roles are
   confirmed, if you have changed neither the number nor the cap and the default cap cannot
   hold this dataset's largest image (its estimated working memory and the 256 MiB the run
   keeps), the notebook starts at 1 and says so beside the choice: "Images read at once: 1,
   because the default memory cap (X GiB) is smaller than the largest image (Y GiB); outputs
   are identical for any number." Values you set yourself are kept: an image that alone needs
   more than your cap stops the run before any pixel is read (`image_over_max_ram`): raise
   the cap or choose 1. The result says how many images were read at once.
   After the run: its result, and collapsed panels for the **intensity checks and plots**,
   **Compare groups** (pooled statistics and a distribution plot per condition, repeat or
   image, for the table this run wrote; it writes nothing) and the report (formatted, with the
   plain text of `discovery_report.txt` folded last).

**Time-lapses and Z-stacks.** A time-lapse goes through the steps as it is: each time point
is a plane of its own, and the viewer's **t** slider shows any of them. A Z-stack is refused
(`z_stack_not_supported`: MorphoBricks analyses 2-D planes), and the Axes step then shows
**Z-stacks: save the dataset as projections**. Look at the stack with the viewer's **z**
slider, choose the **Projection** (maximum, mean or sum; there is no default) and, if not all,
the slices used; **Show this projection of the image above** draws it for looking. After
**Yes, the example looks right**, **Write the projected copies and continue with them** writes
a 2-D copy of every image, with the conversion record, into the output folder
(`<output>/projected_max`, or for example `projected_mean_z1-4`), exactly as `convert
--project` does (§5), and opens the copies as the experiment: they are scanned and checked
again, a line says what they are copies of, and you confirm their axes and go on. Masks are
never projected. The original stacks are only read, never changed or deleted, and the record
lists each one (folder, path, fingerprint, SHA-256, slices used). **Keep the 3-D stacks for
3-D-capable tools (fiberfeatures)** is shown greyed out: 3-D analysis is not enabled yet.
When you open the stacks again with the same output folder, the copies written earlier are
offered (**Continue with the copies written earlier**). Copies whose conversion record cannot
be used (one written before v0.5.2, of version 3) are not offered, and a line says so and why:
writing them again needs another output folder.

**A copy as OME-Zarr.** The Folders card of step 1 has **Also save a copy as OME-Zarr (in the
output folder)**. Ticking it writes nothing. Once the axis roles are confirmed, the Axes step
shows **OME-Zarr copy** with **Write the OME-Zarr copy and continue with it**: every image is
written plane for plane, with the confirmed roles, to `<output>/ome_zarr` (one
`<image name>.ome.zarr` per image, OME-NGFF 0.4, uncompressed), with a conversion record, and
the copies are opened as the experiment (scanned and checked again; you confirm their axes and
go on). Your images are only read. With Z-stacks the projected copies are written as OME-Zarr
instead (`<output>/projected_max_zarr`). Pixel sizes you stated for the whole experiment or a
folder keep applying; a size stated for a single image names the image file, so state it again
for the copy if needed (the check says so: `calibration_target_not_found`).

Folders may have different numbers of channels (for example three stains everywhere and a
fourth only in `Biological_repeat_2`): on **Channels** you give each label once and tag each
folder's channels; the same label means the same stain in every folder. A stain a folder does
not have is left blank in the histogram table, never zero. Inside one folder every image must
have the same channels (`different_channel_counts`, naming the folder). In **Compare groups**,
a grouping in which a stain is in only some planes of a group (for example a condition whose
second repeat has the extra stain) is not calculated: the panel asks *Summarise only the planes
that have the channel?* and calculates only after you answer **Yes**; the table then marks
those rows and the choice is written to Technical details.

When you open the notebook again on an experiment that is already saved and whose folders
did not change, it opens on the first step still to do (usually **Run**).

Other MorphoBricks notebooks embed the first cards of this app as stacked pages
(`folder_step`, `axis_step`, `run_step`): the cards and their messages are the same, laid out
one page under the other, with a picture per folder and the example of the axes step instead
of the viewer.

On Google Colab, cell 3 asks Colab not to put its output in a frame that scrolls on its own
(`google.colab.output.no_vertical_scroll()`), so the page scrolls as a whole there too.

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
`T`, or `-` for a length-1 dimension without a role. It is needed only when some images are
plain stacks, whose metadata do not name their axes: when every image names all its axes,
`run` uses those roles without `--axes` (it prints **Axis roles (as every image names
them)**; design v0.4 §3), and they are still checked as below. A later `run` in the same output folder needs neither the roles nor
the experiment folder again, unless plain stacks were added or changed:

```bash
morphobricks-preprocessing run --output /data/exp1_MorphoBricks_Preprocessing
```

uses the experiment folder and everything else the earlier run recorded there. Without an
experiment folder and without an output folder of a v0.4.0 run, `run` stops with
`experiment_not_recorded`, naming what is missing. Giving an experiment folder other than
the recorded one (for example after moving it) is reported as a change: every row is read
again from the new place, and images that do not name their axes (plain stacks) must be
confirmed again with `--axes`.

Both commands check the axes (§4, step 2): `check` with the suggested roles, which are the
headers' own, so only the `axes_not_named` warning of plain stacks can appear there; `run`
with the roles given in `--axes`. They print **Axes checked: every image names its axes in
its metadata, and the roles agree with them** when all is well (and name the plain stacks
whose roles are recorded as confirmed), or the errors naming the images, the likely cause
and the two fixes, and `run` stops (exit status 1).

`--image-type fluorescence` or `--image-type rgb` gives the image type (§4, step 3).
Without it, `run` uses the type suggested from the image headers and prints it, with why it
was suggested and how to choose the other one; `check` prints the suggestion too. `rgb`
for images that are not three channels of a whole-number pixel type stops the run
(`rgb_not_possible`).

`check`, `run` and `masks` need the experiment description published in the output folder
(`morphobricks.toml`, v0.5.1): the conditions, which biological repeats pair across conditions
and that they are comparable, the channels and the masks, answered in the notebook's describe
steps and saved with *Check and save*. Without it they print `A_DESCRIPTION_REQUIRED` with what
to do, write nothing and exit with status 1. `describe` shows the draft description of the
folders and every question still open. The run writes `experiment_design.csv`, derived from the
description (an output for later bricks; nothing reads it back). `check` and `run` write the
report, `discovery_report.json` and `discovery_report.txt`, to the output folder; `run` writes
it on every run, also one that stops (`status` `stopped` or `written`, with the SHA-256 of the
manifest it describes). A run that stops says so, says that no table or manifest was written,
and names what it did write: the report. Only a run refused before it starts (another run
holds the lock, or no description is published) writes nothing.

A later `run` in the same output folder reuses everything the earlier run recorded: the
axis roles (`--axes` is needed again only for plain stacks that are new or changed), the
masks folder and mask axes, the exclusions, the image type, the bins and the histogram and
intensity settings. An option given that differs from the recorded value is used, and the
report names the change and its effect. A rerun that changes nothing leaves
`run_manifest.json` exactly as it was, its `written_at` included, so bricks that recorded its
SHA-256 still accept it; the report records the rerun. `written_at` is the only field of a
run's outputs excluded from byte identity: two runs of the same inputs and settings, with any number of workers, write
the same bytes in every file except that time (and the manifest SHA-256 the report names). Other options:

| Option | Meaning |
|---|---|
| `--output FOLDER` | Output folder (default: next to the experiment folder) |
| `--exclude PATH` | Leave out an image, relative to the experiment folder; repeat for several |
| `--bins N` | Bins per plane |
| `--histogram-range minmax\|robust`, `--robust-quantiles LOW HIGH` | Histogram range of histograms that are not exact (default minmax; robust quantiles 0.01 99.99) |
| `--memory-limit SIZE` | Working-memory limit per plane, e.g. `2GiB` or `1.5GiB`: a whole number of bytes above 0 (`1.5` or `0.5B` are refused before anything is read) |
| `--workers N\|auto` | How many images are read at once, each in its own thread (default 1: one after another). `auto`: at most the number of CPUs, and with `--max-ram` as many as the largest image's estimated working memory fits. The results are exactly the same with any value; it is not recorded and changes nothing on the next run |
| `--max-ram SIZE` | With `--workers auto`: the memory the run may use, e.g. `8GiB` (256 MiB of it kept for the run itself). An image that alone needs more stops the run before any pixel is read (`image_over_max_ram`) |
| `--missing-as-singleton`, `--no-missing-as-singleton` | Confirm that images lacking some dimensions have one plane along them (`check` and `run`; default: the earlier run's answer, else no). `check` then reports what `run` will: an experiment of 2D images and Z-stacks passes `check` once confirmed, and the suggested option says `--axes ZCYX --missing-as-singleton` |
| `--image-type fluorescence\|rgb` | The image type (default: the earlier run's, else the suggested one, printed) |
| `--mode restart\|full` | `restart` (default) reuses the rows of unchanged images; `full` recomputes every row and check. Both keep your confirmations and replace the results only when the run succeeds |
| `--verify-content`, `--no-verify-content` | Reuse rows (earlier or staged) only when each image's content SHA-256 is the one recorded with them, not only its size and modification time; every image is read once more. Default: the earlier run's choice, else on; `--no-verify-content` opts out and the manifest records it. Turning it on or off recomputes every row. With `masks`: also compare each mask's content SHA-256 before and after it is read (same default) |
| `--lab-links FILE` | A JSON file of lab-tool links (for example LabChronicle), recorded in `run_manifest.json` only (`lab_links`): exactly the keys `repository_url`, `commit` (the full 40- or 64-character lowercase SHA) and `experiment_path`, as non-empty text, and optionally `repeat_codes`, which maps each lab-tool repeat code to a biological repeat ID of the description (for example `{"R001": "rep1"}`). Any other key, a value of another type, an empty value, spaces around a value, or a key given twice is refused before anything is read (`lab_links_invalid`). A `repository_url` holding a credential (a password, `user:password@`, in any form; a user name before `@` in a URL whose scheme is not `ssh`, `git+ssh` or `ssh+git`, because `https://TOKEN@host/...` carries a token there), a query string or a fragment (`?` or `#`) is refused (`lab_links_invalid`), and the refusal does not repeat the URL: a credential is never written to the manifest. An SSH remote's login name (`git@host:lab/db.git`, `ssh://git@host/...`) is kept. The links change no result: no row, digest, cache or other file. They are not taken over by the next run: give the file to each run it belongs to. Not offered by the notebook |
| `--confirm-repeat-codes` | Confirm the `repeat_codes` of `--lab-links` for this run. The run prints each pair; without this option a file with repeat codes is refused (`lab_links_repeat_codes_not_confirmed`), also when a code is spelled like the ID: the lab tool's codes and the description's IDs are separate names |
| `--no-masks` | Drop the masks folder an earlier run recorded, with the mask axes stated for its masks (those of OME-Zarr label images are kept) |
| `--no-exclude` | Clear the exclusions an earlier run recorded, so every image is analysed again (not together with `--exclude`) |
| `--remove-stale-lock` | Remove the lock left by a run that was stopped or whose computer restarted (`check`, `run`, `masks` and `calibrate`); give it only when no other run uses the output folder, on any computer (only Linux and macOS can tell whether a process on this computer still runs) |
| `--outlier-limit N`, `--saturation-limit FRACTION`, `--min-images N` | Limits of the intensity checks printed after the run (default 3, 0.01 and 5) |
| `--log FILE` | Write technical details to a file (`run`, `masks`, `convert`). It is checked before anything is written and opened only once the other options are checked: a file inside the experiment or masks folder (given or recorded by the earlier run), inside the folder of the copies (`convert`), or named as one of the run's files in the output folder (`plane_histograms.csv`, `run_manifest.json`, the reports, `experiment_design.csv`, `calibration.csv`, `mask_relationships.json`, the lock) is refused and nothing is written; so is a file that is a hard link (it has another name). Any other name in the output folder is fine, for example `run.log` |
| `--masks FOLDER` | The masks folder (§2). `check` lists the mask types found and checks every mask from its header; `run` stops on a mask error and records the masks in `run_manifest.json`. A masks folder other than the one an earlier run recorded does not reuse the mask axes stated for that folder's masks (`--mask-axes`): state them again for the new folder (those of OME-Zarr label images are kept) |
| `--mask-axes TYPE=ROLES` | The axes of a mask type, one role (Y, X, Z or T) per axis as stored, stated once per type: `Cells=TYX` for plain stacks of time points whose files do not name their axes; a 2D mask needs none, and `Cells=YX` (the removed shared 2D mask) is the error `shared_mask_not_supported` (repeatable; `check`, `run`, `masks`). Masks are never reordered. |

After writing, `run` prints the intensity checks of the table, grouped by kind, each with
the channel, the image or biological repeat and the values compared, and what to do once
per kind; it draws no plots (the notebook does). Then it prints the channel check: each
imaging unit's channels compared with the same channels of the other units of its condition
(a treatment effect is not reported when the condition has at least 3 other units), with
`channel_unlike_others` and `possible_channel_swap` warnings (for example "channels DAPI and
GFP look swapped") and the channels it could not check; the notebook draws them under
**Do the channels look like themselves?**. A swap made in every unit of a condition is not
found. Warnings only: nothing is changed or blocked.

Read the warnings as prompts to look, not as verdicts. With very noisy data (units that differ
by about 30 % per channel) false warnings are frequent: in a simulation, 171 of 300 runs
without any swap gave a warning and 59 a false swap. Images stored as floating-point numbers
but converted from whole-number data, with medians near zero (for example a sparse 8-bit
channel stored as 0-1 floats), can give false "unlike others" warnings that the same images
stored as 8-bit do not. The same experiment in 16 bits and as 0-1 floats gives the same
findings only while the channel's high percentile is above about 333 counts; below that, the
whole-number files have a floor that floats do not.

**Exit status.** Every command (`check`, `run`, `masks`, `calibrate`, `describe` and
`convert`) exits with status 1 when it prints an error, and with 0 otherwise: warnings,
intensity warnings included, never change it. So `check` exits with 1 on every error it
reports, `A_DESCRIPTION_REQUIRED` included. Options that cannot be read exit with 2. Scripts can therefore stop on any
non-zero status.

The optional `masks` command reads the masks and tells how they relate:

```bash
morphobricks-preprocessing masks /data/exp1 --masks /data/exp1_masks
```

It reads every image's masks, one image at a time, and prints for each kind of mask
whether its labels are separate objects (`instances`), labels spread over many pieces
(`grouped`, such as mitochondria labelled with their cell's number; pieces are counted
8-connected, so two pixels touching only at a corner are one piece), a single value per
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
mask that segment the same objects are reported as such: at least 90% of the objects of
both are matched one to one, each object matched at most once (two halves of one cell are
one match, not two). An object that lies exactly as much in two or more objects of the
other kind is given **no parent**, whichever has the lower number. When that share would
make it inside (a nucleus exactly half in each of two cells), it is not counted as inside
and the command lists it under "Objects given no parent" (`ambiguous_parent` in the
report, since v0.4.0rc5); a smaller tied share is counted as split, as before. These are suggestions from the
pixels, not decisions: you name each kind of mask and check the relationships in the bricks
that use them. The report is written to `mask_relationships.json` in the output folder
(§6). The command takes `--output`, `--exclude` and `--log` like the others, and exits with
status 1 when an error blocks it or when no masks are found. Like `run`, it uses what the
run in the output folder recorded: after a run,

```bash
morphobricks-preprocessing masks --output /data/exp1_MorphoBricks_Preprocessing
```

scans the recorded experiment folder with the recorded masks folder, mask axes and
exclusions (`--no-exclude` clears the exclusions for this scan). Without an experiment
folder and without a run recording one, it stops with `experiment_not_recorded`. A value
you give that differs from the recorded one is used for this scan and printed as a change;
the run keeps its recorded value until you give it the new one. Label 0 is the background and
objects have positive labels: a mask with a negative value stops the command with a message
naming the mask, and no report is written.
The output folder's lock is held from before the first mask is read until the report is
written: when another run holds it, the command stops with `output_locked` before reading
any mask. A mask that changes after the command read its header or while its pixels are read
(a segmentation still writing it) stops the command with `mask_changed_during_scan`, naming
the mask: no report is written, and an earlier `mask_relationships.json` is left as it was.
`--verify-content` also compares each mask's content (SHA-256) before and after it is read
(each mask is read whole twice more); given neither it nor `--no-verify-content`, the run's
own setting is used.

The rules behind each suggestion use thresholds you can change, for example
`--inside-object-share 0.8` to call a kind of mask "inside" another when 80% of its objects
(not 90%) lie inside, or `--same-objects-iou 0.7` to ask for a closer match before two
kinds of mask count as the same objects. `morphobricks-preprocessing masks --help` lists
all seven with their defaults; the values used are printed and written in the report.
`python -m morphobricks_preprocessing` works the same way.

### Describe your experiment (`describe`)

```bash
morphobricks-preprocessing describe /data/exp1
```

prints, in plain language, the draft description of the experiment that the folders (and,
when there is one, the output folder) allow: the ID proposed for each condition, technical
repeat and biological repeat (a folder such as `control` or `z`, whose name cannot be an ID,
gets no proposal and is never repaired), each repeat with its folders and image counts, and
every question still open, each with what you have to do: choose or accept an ID, confirm
that the folders of a repeat were prepared and acquired under the same circumstances, say
what each of two controls is compared against.

- It reads the folder structure (and, to compare a published description, the image headers;
  no pixel is read) and **writes nothing**: no output
  folder, no draft, no description. `--output FOLDER` names the output folder to read (default:
  next to the experiment), `--exclude PATH` leaves an image out, as in `check`.
- The draft is always built from the folders. An `experiment_design.csv` in the output folder
  (from an earlier version, say) is not read and proposes or prefills nothing: describe the
  experiment in the notebook.
- If a `morphobricks.toml` is published, `describe` compares it with the folders instead: how
  many acquisitions are as published, which are new or gone, which image counts changed and
  which units' image headers no longer fit their published channels and which repeats must
  be confirmed again. The headers of the images are read (no pixel) and compared as the
  notebook compares them on reopening, so a header rewritten under the same files is a
  change in both. A header change alone reopens no repeat (experiment-v1 §10: only a change of
  a unit's ordered channel list, its folders or the pairing does): the repeat keeps its date,
  and the run stops until the description fits the headers again (give the channel the new
  header name, or tag the channels again, in the notebook; a saved header name that a new
  tag drops is confirmed there with **Yes, this header name changed**).
- **Nothing is confirmed by the command.** An ID, the comparability of a repeat, a reference
  and the removal of a folder are your answers, given in the notebook; the command never
  answers for you and never fills in a date.

```bash
morphobricks-preprocessing describe /data/exp1 --publish
```

publishes the description only when no question is open and every decision is confirmed;
otherwise it prints `Not published. What is missing:` with every item and exits with status 1.
Without the fast-mode options below the command line holds none of your answers (they are
given in the notebook), so `--publish` publishes nothing from a draft made of folders: it
lists what is missing. For
a published description that matches the folders it says `Nothing to publish` and exits with
status 0; for one whose only change is in the image headers, it says that no answer is
missing and that the run stops until the headers fit, and exits with status 0; for one that
no longer matches otherwise, it lists the new, gone or changed folders and leaves the file as
it is. Like the other commands, `describe` exits with status 1 when it reports an
error (an experiment folder that is not found, an unreadable description) and 0 otherwise.

**Fast mode, the default for a new description (Spec ADR 0006).** When every imaging unit has
the same channels in the same order, state it once and publish in one command:

```bash
morphobricks-preprocessing describe /data/exp1 --channels DNA,ACTIN --image-type channels \
    --accept-ids --comparable --confirmed-on 2026-11-20 --publish
```

`--channels` are the channel IDs of the *tagged unit*, the first acquisition in folder order
(the output names it and its header names), in source order C0, C1, ...; its header names are
confirmed as those channels' header names. `--image-type` is `channels` or `rgb` (then
`--channels R,G,B`). `--accept-ids` accepts every proposed ID, `--comparable` is your answer
that every biological repeat's folders were prepared and acquired the same way, and
`--confirmed-on` is the day of your statements (never read from the clock). Every image's
header is checked against the list first: an image with other channels, names in another
order, other names or another image type is listed (`R_FAST_LAYOUT_CONTRADICTION`) and nothing
is written. Experiments where some acquisitions differ are described in the notebook, tagging
those acquisitions on their own. `--strict` keeps the per-acquisition mode, answered in the
notebook. A published description is never switched between modes by `describe`. While the
output folder holds answers saved in the notebook (`morphobricks.draft.json`), the command
publishes nothing (`R_DRAFT_EXISTS`): they are finished and published in the notebook.

### Stating pixel sizes (`calibrate`)

Each image's pixel size (X and Y) and z-step (Z) come from its metadata. When the
metadata give none, or the wrong one, state them in `calibration.csv` in the output
folder. Nothing is ever guessed: an image with neither is *uncalibrated*, and the report
says so.

```bash
# 0.65 um pixels for the whole experiment; a 2 um z-step for one condition
morphobricks-preprocessing calibrate /data/exp1 --size . XY 0.65 um --size treated Z 2 um
# one image measured in pixels on purpose (deliberately uncalibrated)
morphobricks-preprocessing calibrate /data/exp1 --pixels treated/rep1/img3.tif XY
# show the statements, or remove one (the metadata are used again)
morphobricks-preprocessing calibrate /data/exp1
morphobricks-preprocessing calibrate /data/exp1 --remove treated Z
```

The target is `.` (the whole experiment), a folder (a condition or repeat) or an image,
relative to the experiment folder with `/`, spelled exactly as on disk. Only `.` and `./`
are the whole experiment: a target of only slashes, backslashes and `.` steps (`/`, `//`,
`\`, `.\`, `./.`, `.//`, `././`) names no folder and is refused (reason
`calibration_target_root`), never read as the whole experiment; `.\` and `./.` were read as
the whole experiment before and are no longer accepted. `calibrate` and the notebook change
nothing; a line of `calibration.csv` edited by hand to such a target (for example `/`, read
as the whole experiment before) now stops the run with `calibration_unreadable`, naming the
line (`calibrate` and the notebook always write `.`). The most specific statement applies:
an image's own over its folder's, a folder's over the whole experiment's. Each axis is
stated on its own (`XY` states both with the same size), so Y and X never trade sizes. Units
are pm, Å, nm, µm (um, micron), mm, cm or m. You may also edit the file in a spreadsheet;
its columns are `target,axis,size,unit`, with an empty size and the unit `pixels` for a
pixel statement:

```text
target,axis,size,unit
.,X,0.65,um
.,Y,0.65,um
treated,Z,2,um
treated/rep1/img3.tif,X,,pixels
treated/rep1/img3.tif,Y,,pixels
```

A run reads the file and never changes or removes it (also with `--mode full`, or when it
stops). The next run records each image's sizes with their source (`header`, `stated` or
`pixels`), names the change in its report, and recomputes no row. A stated size that
differs from the metadata is a warning (`calibration_differs_from_header`); the stated size
is used. A Z (or time) size is recorded only for images that have that axis: a max
projection whose metadata still give the z-step of its stack gets no Z size, and a
statement for an axis some images lack is a warning naming them
(`calibration_axis_absent`). In Python, `write_calibration(calibration, output_folder)`
writes the file (see §9).

`calibrate` never writes inside the experiment folder: an output folder that is the experiment
folder or inside it (also through a link) is refused with `output_inside_experiment` before
anything is created or written, against the experiment folder you give, or, with only
`--output`, against the experiment the run in that output folder recorded. With only `--output`
and no run there yet, no experiment is known: give the experiment folder too
(`calibrate /data/exp1 --output /data/exp1_out ...`) so the check can be made. The command still
runs in that case, but says so on the error output (stderr): "Note: no experiment was given and
none is recorded in <output folder>, so it was not checked that the output folder is outside the
experiment (give the experiment folder to check it)." It is not an error and does not change the
exit status; it is not printed when the experiment is given or recorded.

### Named-axis copies (`convert`)

When the metadata of some images name their axes wrongly (the errors `axes_yx_swapped`,
`axes_contradict_header`, `axes_x_before_y`) or do not name them at all (the warning
`axes_not_named`), and you are sure of the roles, save the images again as named-axis
copies with your roles, then run preprocessing on the copies:

```bash
morphobricks-preprocessing convert /data/exp1 --to /data/exp1_named_axes --axes ZXY --output /data/exp1_MorphoBricks_Preprocessing
morphobricks-preprocessing check /data/exp1_named_axes --output /data/exp1_MorphoBricks_Preprocessing
morphobricks-preprocessing run /data/exp1_named_axes --output /data/exp1_MorphoBricks_Preprocessing --axes ZYX
```

`--axes` gives your roles for the original images, exactly as for `run`; they are never
taken from the metadata. `convert` writes a new folder (`--to`, outside the experiment)
with the experiment's folders and file names: the images with axis problems as OME-TIFF
whose metadata name your roles (Y and X are written rows then columns), every other image
copied as it is. The originals are only read. Each plane of a copy holds exactly the pixel
values preprocessing reads from the original with your roles: same pixel type, nothing
rescaled. Large planes are read and written in blocks within `--memory-limit`. Masks are
never converted: keep using your masks folder, and keep the copies out of it
(`copy_folder_overlaps_masks`; give `--masks` if your runs' masks folder is not the one
recorded in the default output folder).

Pixel sizes follow the roles. An axis you give another role than its metadata gets no size
in the copy: after a Y/X swap the copy has no X or Y pixel size (the metadata's X size
belongs to the axis you call Y), unless the metadata give X and Y exactly the same size,
which is then kept; a depth step is never written as a time step. State the missing sizes
in `calibration.csv` if you know them (`calibrate`). Images whose planes cannot be read
within the memory limit, and a folder without the space for the copies, are refused
before anything is written. The command prints what it
wrote and the next commands, with the suggested `--axes` for the copies and the output
folder of your runs on the originals: give it with `--output` (without it the default
output folder is named, which only works if your runs used it). `--output` is also where
the masks folder is looked up when `--masks` is not given.

`convert` keeps each image's stored order of dimensions (`ZXY` stays three dimensions in
that order, with your roles): it does not reorder images, so it cannot solve
`inconsistent_dimensions` (images arranged differently, for example `CZYX` among `ZCYX`);
export those images again in the same order, or process them as a separate experiment
(plan §12 question 73). A plain stack that holds several dimensions in one sequence of pages
(for example channels and depth slices interleaved, read as `IYX`) cannot be converted
either: one role per stored dimension cannot say how the pages split, and the brick never
guesses it. Save it again with its dimensions (for example as a hyperstack in Fiji, which
writes ImageJ metadata naming them), then check it again.

Give `run` the output folder of your runs on the originals: the design, image type and
stated pixel sizes are kept (the copies have the same names), the report says the
experiment is now the folder of copies, and every row is computed again. The conversion
record, **.morphobricks-conversion.json** in the folder of copies, lists every image with
its original's and its copy's size and modification time, the copy's SHA-256 (a checksum of
its content), the roles, the pixel sizes, the channel names and the significant bits; the
run records the record's SHA-256 and warns when an original changes after the conversion
(`conversion_source_changed`). A copy that changed (also a change that keeps the copy's size
and time), went missing or was added since the conversion stops the run before anything but
the report is written (`copy_changed_after_conversion`, an error): later bricks would refuse
such a run, so convert the originals again into a new folder and run on that one. Checking the copies reads each of them whole,
so `check` and `run` on a folder of copies, and opening their run, take about one read of
the copies longer. An original that changes while `convert` reads its metadata is refused
(`source_changed_during_check`); convert again once nothing writes into the experiment.

A folder of copies made before v0.5.2 has a conversion record of version 1, 2 or 3: `convert`,
`run` and the bricks read version 4 only and refuse it (`conversion_record_unreadable`, naming
its version); nothing is converted. Convert the originals again into a new folder.

Running `convert` again with the same images and roles reuses the copies and only adds new
images; when nothing is added the record is left as it was. Bricks reading the run on the
copies (`RunInputs`) refuse it if the conversion record is changed or removed afterwards,
or if a copy no longer has the SHA-256 the record gives.
`convert` never overwrites anything else: a folder holding other files, copies of other
images or made with other roles is refused (`copy_folder_not_empty`, `copy_differs`), and so
is a folder holding a link or mount point anywhere below it (`copy_folder_has_links`), or a
folder below it that cannot be read (`copy_folder_unreadable`);
convert into a new folder then. If it fails (`conversion_failed`), the folder is left as it
was. The copies are written in a hidden work folder, `.mbconv` ("MorphoBricks conversion";
`.morphobricks-conversion.partial` in earlier versions, still recognised), and
moved into place together. If a conversion is killed (the computer stopped, the Colab
session ended) while it writes the copies, the next one removes that folder, since a
conversion marks it as its own when it makes it, and converts as if nothing had happened.
If it is killed while it moves the copies into place, the copies already moved stay where
they are, without a conversion record: the next conversion cannot check them, so it refuses
the folder (`copy_folder_not_empty`), names them and says they may be such copies; remove
those files (or convert into a new folder), then convert again. A file or folder named like a copy with `.partial` added is never removed:
it is not the conversion's. Images of 64-bit integers cannot be saved as OME-TIFF (`cannot_convert_pixel_type`).

**Z-stacks: recorded 2-D projections.**
A Z-stack is still refused by `check` and `run` (`z_stack_not_supported`). To analyse one,
make a projected copy first:
`convert EXPERIMENT --to FOLDER --axes ZCYX --project max` (or `mean` or `sum`; there is no
default method; `--z-range 2:10` uses only those slices of every image, zero-based with the
end excluded, `4:5` is the single slice 4, and every image must have them), or in Python
`convert_experiment(EXP, mapping, FOLDER, projection=Projection("max", (2, 10)))`.
Every image of the experiment is written as a 2-D OME-TIFF of the same folders and names:
its axes are your roles without Z (`ZCYX` becomes `CYX`), each (t, c) is projected on its own,
and the Z size is not written to the copy (it is recorded in the conversion record as
dropped). Your originals are only read. The pixel type is decided by the input type and the
method, never by the data: a maximum keeps it; a sum of 8-bit or 16-bit images is 32-bit and
exact (`int32` for signed ones; 32-bit images give 64-bit floats holding the exact integer);
a mean is a 64-bit float; a sum or mean that could not be exact is refused
(`projection_cannot_be_exact`), never wrapped or clipped. A NaN stays NaN; a sum that overflows
to infinity is refused (`projection_overflow`). The copy's OME-XML says how it was made, and
the conversion record (version 4) holds the method, the slices used, the source's SHA-256,
the types, the rounding and the dropped Z size for every image. Masks are never projected:
masks with depth slices stop the projection (`projection_masks_3d`); 2-D masks stay where
they are. OME-Zarr images are not projected yet (`projection_zarr_not_supported`; exclude
them). One folder holds one method and one slice range (`copy_differs` otherwise). The command prints the next commands with a
new output folder (by default the copy folder's name with `_MorphoBricks_Preprocessing`):
the projected copies are a different source, so the description starts again there (the
design and the stated pixel sizes are entered again). A run on the copies records the projection in its manifest (`projection`: the method, the slices used, the source SHA-256 of every image) and the report says "max projections of slices 1:4". Masks next to projected images are used as they are, with the warning `masks_on_projected_images`: check that they were made on, or for, the projection. A run on projected copies reads the conversion record strictly: a record that is missing or does not match the run is refused, never read as a plain image. The notebook card follows.

**In the notebook.** When step 2's axis check reports these images, a **Make named-axis
copies (optional)** card appears under it, calling the same functions as the command:

1. **The roles.** They are yours: the roles confirmed with **Confirm axis roles** (possible
   with only `axes_not_named`), or, while the axis errors keep that button disabled, the
   roles shown once you pressed **Yes, the preview looks right** and then **Yes, write the
   copies with these roles** on the card. Changing a role, or the answer about missing
   axes, takes that answer back. Nothing is taken from the header.
2. **The folder for the copies** (required, no default): paste it or use **Browse…** (on
   Google Colab, **Copy path** in the **Files** panel). It is checked with the command's rule
   (`copy_folder_overlaps_experiment`, `copy_folder_overlaps_masks` against the masks
   folder of step 1 or of the run in the output folder, `copy_folder_not_a_folder`).
3. **What will happen**, from the command's planning, reading headers only: the images
   written as OME-TIFF (with their axis code) and those copied as they are (with `excluded`),
   the space needed and the free space. Any error that would stop the conversion is shown
   here and keeps **Make named-axis copies** disabled, as does a drive without the space.
4. **Make named-axis copies** writes the copies with a progress bar, holding the folder's
   lock. Refusals are shown on the card with what to do; a folder locked by a stopped
   conversion shows its holder and **Remove the lock of a stopped run** (the rule of
   `--remove-stale-lock`).
5. **After the copies:** the card shows the folder, what was written, copied and reused, the
   path of the conversion record and what to do next. **Check the copies in step 1** sets
   the experiment folder to the copies, keeps the output folder (an empty box is filled with
   the originals' default) and checks again. Confirm the labels, the axis roles (now named in
   the copies' metadata) and the image type again; the design and stated pixel sizes are
   kept. If you pressed **Yes, they have a single plane there**, press it again (the copies
   do not add those axes; `--missing-as-singleton` on the command line). The card lists the
   copies whose relabelled Y or X has no pixel size (unless the header gave X and Y the same
   size): state them under **Pixel sizes** in step 1.

The card is not shown when another brick embeds step 1 without the output folder
(`folder_step(show_output=False)`).

### Converting a mask (`convert-mask`)

Some bricks need objects where your segmentation gave a binary mask, or one mask per tissue class
where it gave a class image. `convert-mask` writes such masks as **new files** in your masks
folder; your mask is only read, and nothing is overwritten. The mask must be declared and
checked by the masks step first.

```bash
# the connected objects (8-connected) of the binary mask TumorRegion, as the objects mask TumorObjects
morphobricks-preprocessing convert-mask --output /data/exp1_MorphoBricks_Preprocessing \
    --mask TumorRegion --to-instances TumorObjects
# one binary mask per class value of the class image Tissue: Tissue_class1, Tissue_class2, ...
morphobricks-preprocessing convert-mask --output /data/exp1_MorphoBricks_Preprocessing \
    --mask Tissue --to-classes Tissue
```

- `--to-instances NEW_ID` takes a mask declared **binary**, with one depth slice (each time point
  is labelled on its own). `--to-classes PREFIX` takes a class image (one 2-D plane of whole
  numbers, 0 the background) declared **parent_labels** or **instances**; each class value v
  present gives `PREFIX_classv`, and an image holding only 0 gives none ("no class present").
- `--image PATH` (repeatable) converts only those images; by default every image with a file of
  the mask.
- The new files go to `<NEW_ID>/<the image's folders>/<image name>.tif` in the masks folder, next
  to a `mask_lineage.jsonl` that records, for each file, the mask it was made from, how, and a
  digest of both. Running the same command again changes nothing.
- It stops at the first refusal and says why: a mask that is not declared, not checked yet or
  changed since the masks step (`A_MASK_UNCHECKED`: run `masks` again), a mask of the wrong kind,
  a label image inside an OME-Zarr image (only masks in the masks folder are converted), or a new
  mask ID that is declared already or whose files exist and were not made by this conversion.

Then, as the command prints: declare each new mask (its source type is its ID; `instances` for
`--to-instances`, `binary` for `--to-classes`), run preprocessing and the masks step again, and
use it like any mask. In the notebook, the card **Convert a mask (optional)** under **Describe the
masks** does the same.

### An H&E tissue mask (optional)

For H&E images (a unit declared `image_type = "rgb"`), `tissue-mask` writes a **new** region mask
that leaves out nuclei, red blood cells and empty space, so that a brick such as fiberfeatures
measures the tissue between them. It needs the `he-mask` extra:

```bash
pip install "morphobricks-preprocessing[he-mask]"
morphobricks-preprocessing tissue-mask --output /data/exp1_MorphoBricks_Preprocessing --to HETissue
```

- It is a **fixed rule**, `he_tissue_v1`, and it is **provisional**: its numbers are not frozen
  until an acceptance report is accepted. It is not a trained segmentation. Its colour maths is
  the normalization brick's (the eosin and hematoxylin maps of the `he_eosin` preset and the
  optical density per channel), and all its sizes are **in pixels**: the same rule on images of
  another magnification leaves out pieces of another physical size. Look at the preview (in the
  notebook) or the printed "kept" share before you use it.
- `--image PATH` (repeatable) writes only those images; by default every image of a unit
  declared rgb. Images with depth slices or time points, not 8-bit or not three channels are
  refused.
- The files go to `<NEW_ID>/<the image's folders>/<image name>.tif` (1 = kept, 0 = left out) next
  to `mask_lineage.jsonl`, which records the image, the complete rule and its digest, the exact
  installed code that computed it and the library versions. An image where nothing is kept gives
  an all-zero mask: a valid, empty region.
- Running the same command again changes nothing. If the printed line says **this mask will not
  be reused on retry**, the installed code could not be verified (for example a development
  install, `pip install -e`): the mask is written, but a second run with the same ID is refused;
  use a new ID.
- Then declare it, run preprocessing and the masks step again, and use it **as a region**:

  ```toml
  [masks.HETissue]
  source_type = "HETissue"
  representation = "binary"
  source_channels = ["R", "G", "B"]
  display_name = "H&E tissue (he_tissue_v1)"
  ```

  For example fiberfeatures `MaskRequest("HETissue", "region")`. It is never a set of cells: do
  not pass it as cell-mode labels.

In the notebook, the card **Make an H&E tissue mask (optional)** beside **Convert a mask** does
the same: choose the RGB positions (or **Choose all of this unit**), **Preview** one image (the
nuclei tinted blue, red cells orange, empty space grey, with the kept share; nothing is written),
type the new mask ID, then **Write the tissue mask**.

**Without a run** (Python, for an image you have read yourself): `he_tissue_mask_array` computes
the same provisional rule on one 8-bit `(Y, X, 3)` R, G, B array and writes nothing.

```python
from morphobricks_preprocessing import he_tissue_mask_array

found = he_tissue_mask_array(rgb)  # rgb: numpy uint8, shape (Y, X, 3), colour last
found.mask  # boolean (Y, X), True = kept; use it as a region, never as cell labels
found.record.status  # "provisional"
found.record.to_dict()  # rule, rule_sha256, code identities, versions, fractions: keep it
```

Any other pixel type or shape is refused; nothing is converted, and a `(3, Y, X)` array must be
moved to `(Y, X, 3)` by you. The mask is the one the run route writes for the same pixels, but
it has no lineage file and cannot be declared as a mask of a run: for a MorphoBricks run, use
`tissue-mask` (or the notebook card) above.

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
  §6.2 (schema version 5: the columns are named by channel ID, for example
  `Histogram_Count_DNA_00042`; a `channels` column lists the stains of each plane, and a stain
  the image does not have is blank; each channel also has its underflow and overflow, zero
  unless the robust range is used). A table written by an earlier version is recomputed on the next
  run; later bricks ask you to run this brick again on it.
- **`image_qc.csv`:** one row per plane and channel with image QC metrics measured while the
  histograms are computed (no extra read): the share of the plane above its Otsu threshold
  (`foreground_fraction`; `otsu_separation` is about 0.64 for noise and for any dense field
  with one broad histogram peak, so it does not tell an empty field from content), the
  histogram's `entropy`, focus (`normalized_variance`, `tenengrad`, `laplacian_variance`,
  `local_focus_median`; `local_focus_p10` shows how much of the field holds content, not focus),
  the correlation of each pixel with its neighbours 1, 4 and 16 pixels away (structure size;
  `correlation_1` is about 0 for an empty field of camera noise and high when there is
  structure) and how uneven the field is (`tile_mean_cv`). They do not change when all
  intensities are multiplied by a constant (an 8-bit and a 16-bit copy give the same values),
  but a background offset does change the focus and unevenness values, so compare images that
  share their camera settings. They are for review only: nothing is excluded because of them. A
  blank value means the metric is not defined for that plane, never zero; the `blank_reasons`
  column says why for each (for example a plane of one value, or no pixel pair that far apart).
  Planes read in blocks are marked `read_in_blocks`; their neighbour metrics leave out the pixel
  pairs across block edges, which can matter for an image with a seam on a block edge, and
  another memory limit recomputes them. Definitions in [contract.md](contract.md) §19.
- **`experiment_design.csv`:** one row per biological-repeat folder of each condition: its
  repeat group, whether its condition is a control, its image and technical-repeat counts,
  and whether and when the repeat group was confirmed. Derived from the saved description and
  written by every run (and by *Check and save*); a record for people, never read back: later
  bricks take the pairing from the description. Format in [contract.md](contract.md) §12.
- **`run_manifest.json`:** what the run read and with which settings: labels, axis roles,
  the image type (and whether the channels are RGB colour samples), bin count, histogram
  range, memory limit, excluded images, the limits of the intensity checks, the planes with a
  narrow histogram range, package versions, the
  fingerprint of the experiment design (and of `calibration.csv`), and each image's position, size, modification time and pixel sizes with their source. Channel names and significant bits are recorded when the metadata
  provides them, and whether the image's planes were too large to read whole and are read
  in blocks. Format in [contract.md](contract.md) §9.
- **`discovery_report.json`** and **`discovery_report.txt`:** the report of the last run (or
  `check`, or the notebook's **Check folder**), written also when the run stops: its `status` (`stopped` or `written`), the
  SHA-256 of the manifest it describes, the folders, conditions, repeats and positions, the
  channels, each image's axes (and whether they come from the header or your
  confirmation) and pixel sizes with their source (the metadata, `calibration.csv`, or
  pixels as stated; or `uncalibrated`), the stated calibration, the design and image type, the masks,
  every message with its action, and what the run reused, recomputed and replaced, and
  why. It is for you to read; later bricks never use it.
- **`check_headers.json`** (notebook only): the image headers the last **Check folder** read,
  each with the fingerprint it was read at, so that reopening the output folder restores the
  check without reading unchanged images again (§4, *Coming back to earlier work*). It is a
  cache: delete it and the next restore reads every header; later bricks never read it.
- **`calibration.csv`** (only when you state pixel sizes): your statements (§5, Stating
  pixel sizes). Runs read it and never change it.
- **`ome_zarr/`**: written only by v0.3 runs that converted TIFF images to OME-Zarr. That
  option was removed in v0.4.0; such a folder is left as it is, and may be deleted.

- **`mask_representation.json`** (only from the `masks` command or the notebook's **Describe
  the masks**, and only when the output folder holds a published `morphobricks.toml`): which
  mask files the mask step checked against the representation the description declares, each
  with the fingerprint it had when read. A new or changed mask file is `not yet checked` until
  the step reads it again. A file of a `binary` mask must be stored as integers or true/false
  (never floats, even with whole values) and hold, over all its planes together, 0 and at most
  one other positive value; an all-zero file and a file with no 0 at all are accepted. A
  float file, a negative value, or two different positive values (also when each time point
  alone holds one) is reported as `R_MASK_CONTRADICTS_REPRESENTATION` (the command exits 1);
  such a file is not recorded as checked and is left out of `mask_relationships.json`.
  An `instances` or `parent_labels` (grouped labels) file is recorded as checked once the step
  has read it: pixels cannot prove either.
  A `binary` mask checked by the masks step of v0.5.6 or earlier (which allowed two different
  values over the whole file) is `not yet checked` in this version: an analysis that reads it is
  refused with `A_MASK_UNCHECKED`, whose message says the check predates the binary value rule;
  run `masks` again on the output folder. `instances` and `parent_labels` masks keep their check.

- **`mask_relationships.json`** (only from the `masks` command or the notebook's
  **Describe the masks**): each kind of mask, the
  relationship between every two kinds with its counts, and the hierarchy. The rules and
  thresholds are in [contract.md](contract.md) §15.

When masks are given, `run_manifest.json` also lists every image's masks (`masks`), with
each mask's axes and size (for example `TYX` and 50 x 512 x 512 for a mask per time point).

**Interrupted runs resume.** As soon as an image is read, its rows are kept in the
hidden folder `.staging` in the output folder. If the run is interrupted (a crash, a
stopped kernel, a lost Colab session, a computer switched off), run again with the same
settings: the images already read are not read again, as long as they are unchanged (same
size, time and, for OME-Zarr, the same files; unless the run opted out with
`--no-verify-content`, also the same SHA-256) and the settings that change rows are the same. The report lists them as `resumed`, and lists any rows it did not reuse with the
reason (`staged_discarded`). A `full` run reuses none. After a killed run, remove its lock
first (`--remove-stale-lock`). Once a run has written its results, `.staging` is deleted.

**Results are switched in one step.** The new table, design, manifest and report are first
written into the hidden folder `.publish`; replacing `run_manifest.json` then makes them
the current results in one step, and the other files are moved into place. A run stopped
before that step leaves the earlier results exactly as they were; one stopped after it is
finished by the next run (or `check`, `masks`, `calibrate`). Later bricks always
read one whole set of results, old or new. If a file cannot be moved into place (for
example because another program has the table open on Windows), the run says so: close
that program and run again. This needs temporary disk space for the new outputs.

**Images that change during the run.** If an image changes or disappears while the run
checks or reads it (an acquisition or a copy still writing into the experiment), the run
stops with `source_changed_during_run` and publishes nothing. Wait until nothing writes to
the experiment, then run again: the images already read resume. Masks are checked the same
way: a mask that changes while the run checks its header, or at any time before the run
publishes its results (a segmentation still writing it), stops the run with
`mask_changed_during_check` or `mask_changed_during_run`, naming the mask and what changed,
and nothing is published. With `--verify-content` the masks' content is compared too.

Every output file gets the permissions a file you save yourself would get (your umask;
usually readable by everyone, writable by you), so colleagues can open the results in a
shared folder. A file that already exists keeps the permissions you gave it. Outputs
written by versions before v0.4.0 are readable by their owner only: make them readable
with `chmod a+r <output folder>/*`, or delete them before running again.

Only one run at a time can use an output folder: a run holds a lock file
(`.morphobricks-preprocessing.lock`) and a second run is refused (`output_locked`). If the
process is forcibly stopped, its lock stays; remove it with `--remove-stale-lock` once you
are sure no run is going on (every command takes it). When the lock was left by a process on
this computer that is no longer running, the message says the lock looks stale; it is still
never removed without your request. `check` holds the lock
while it writes its report, `masks` (and **Describe the masks**) from before the first
mask is read until `mask_relationships.json` is written (a locked folder is refused before
any mask is read), and `calibrate` while it changes its file,
and are refused the same way. A stopped run leaves the last good results in place;
rerunning checks the table against the manifest and rebuilds a mismatched pair.
`mask_relationships.json` is removed by a `full` run and when the masks changed since it
was written; run the `masks` command (or **Describe the masks**) again.

## 7. Running again, and excluding images

Run the notebook or `run` again after adding, changing or removing images:

- only new and changed images are read; rows of unchanged images are kept;
- rows of deleted or renamed images are removed (a renamed image counts as removed and
  added);
- everything is recomputed when the bin count, the histogram range, the axis roles or the
  pixel type changed,
  when the table was edited by hand or written by an earlier version of the brick, or when
  you ask for it (`--mode full`, `mode="full"`);
- changing only the image type reads nothing again: the histograms are the same for both
  types, and only the manifest records the new type.

**After upgrading from an earlier version (v0.5.1 or before):** its run manifest is not read
(only version 5 is, since v0.5.2), so the first run recomputes every row and reuses no recorded
input: give the axis roles, masks and exclusions again. Later bricks refuse such older runs until they are run again.

Run the brick again after adding images or masks before using a later brick: later bricks
read the images and masks this brick's run recorded, and refuse an experiment holding images
or masks the run did not record (they name them).

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
| `R_HETEROGENEOUS_LAYOUT_NOT_ENABLED` | Error | The published experiment description declares colour (RGB) acquisitions next to acquisitions of separate channels. This version processes one image type per experiment and nothing was processed: the message names the layouts and units. It is the run's only message then, error or warning: it is decided from the description, no header check follows it, and nothing else found in the folders (an unreadable image, a missing masks folder, an excluded path that does not exist, ...) is reported until the description declares one image type. Process them as separate experiments. Different channel lists or orders of one image type are accepted. |
| `partial_channel_coverage` | Warning | A biological repeat has a stain in only some of its imaging units (for example one technical repeat imaged an extra stain); the message counts imaging units (`rep1: PHASE in 1 of 2 imaging units`). The images are measured as acquired (the stain is blank where it was not acquired) and the coverage is in the manifest. Later analyses that pool the stain over the whole repeat refuse it (`A_SCOPE_INCOMPLETE`) unless you choose the imaging units that have it; that refusal counts images ("present in 2 of 4 images"). |
| `R_MASK_CONTRADICTS_REPRESENTATION` | Error | The masks step found a file of a mask declared `binary` that is stored as floats, or holds, over all its planes together, a negative value or two or more different positive values (the message names the file and the values found, or its pixel type). A binary mask holds 0 and at most one positive value (an all-zero or all-foreground file is accepted), stored as integers or true/false. Declare the mask as `instances` in the description if every value is an object, or as `parent_labels` (grouped labels) if every value is one group, such as the mitochondria of one cell; or fix the file (save a float mask as integers). The file stays `not yet checked`, so an analysis that names the mask refuses it (`A_MASK_UNCHECKED`) without reading it. |
| `R_UNIT_NOT_FOUND`, `R_UNIT_NOT_DECLARED`, `R_UNIT_NOT_HOMOGENEOUS`, `R_CHANNEL_COUNT_MISMATCH`, `R_HEADER_CONTRADICTION`, `R_IMAGE_TYPE_CONTRADICTS_HEADER`, `R_MASK_TYPE_NOT_DECLARED` | Error | The published description and the folder or the image headers disagree (the message says where). Correct the description, publish it again and run again. |
| `R_IMAGE_TYPE_CONTRADICTS_HEADER` for colour scanner files, `R_RGB_OVERRIDE_NOT_THREE_SAMPLES`, `E_RGB_OVERRIDE_WITHOUT_RGB` | Error | Some scanners store colour (RGB) images with a header that does not say RGB (for example TIFF photometric *minisblack* with three samples per pixel). The acquisition declared `image_type = "rgb"` is then refused, and the message says so and names `rgb_header_override`. If the three samples are red, green and blue in this order, add `rgb_header_override = true` to that acquisition in `morphobricks.toml` (beside `image_type = "rgb"`; in the notebook, tick **Read these folders as colour (RGB) although their header does not say colour** on the Image type card of the Channels step) and publish again: the run then accepts the header and records the acquisition in the manifest as declared RGB with the header overridden (`image_type_header_overridden`, `header_photometric`). Every image whose header does not say RGB must have exactly three interleaved samples per pixel, else `R_RGB_OVERRIDE_NOT_THREE_SAMPLES` names the image (reported before `R_UNIT_NOT_HOMOGENEOUS`); the key on an acquisition that is not declared `rgb` is `E_RGB_OVERRIDE_WITHOUT_RGB`. Images of the acquisition that differ only in colour interpretation (some *minisblack*, some RGB) are then one layout. Images whose header already says RGB, or whose format states no colour interpretation (OME-Zarr), do not use the key; the manifest records, per image, whether it was used (`rgb_header_override_images`). Adding, removing or changing the key reopens the "same way" confirmation of that replicate. Only headers are read, never pixels. |
| `R_FAST_LAYOUT_CONTRADICTION` | Error | The published description is in **fast mode** (it has a `channel_layout` table: "every imaging unit has the same channels in the same order", Spec ADR 0006) and some images of the acquisitions that follow it (`channels_from = "experiment"`) contradict that list. One message lists **every** contradicting image, with its FOV key (condition, repeat and acquisition folders, then the image name) and path, and every contradiction found in it: `image_type` (the header says colour where the list is not, or the reverse), `channel_count` (another number of channels), `header_order` (header names of the list in another order) or `header_names` (a header name that is no confirmed name of any channel of the list), each with its C index and what was expected and found. It is decided from the image headers only, before any pixel is read; nothing is reordered, dropped or guessed, and nothing was processed. Tag the acquisitions of these images on their own (`channels_from = "unit"`, confirmed per acquisition), publish again and run again. An image whose header gives no channel names (or an empty name) needs no evidence and follows the list; the manifest records each image of a fast acquisition in `fast_layout_images` (`header_verified` true only when every channel was confirmed by its header name). In such acquisitions this one code replaces `R_CHANNEL_COUNT_MISMATCH`, `R_HEADER_CONTRADICTION`, `R_IMAGE_TYPE_CONTRADICTS_HEADER` and `R_UNIT_NOT_HOMOGENEOUS` (which stays for the other axes); `R_RGB_OVERRIDE_NOT_THREE_SAMPLES` keeps its priority. An acquisition tagged on its own is checked as in a description without fast mode. Experiments with extra channels in some acquisitions are refused here: tag those acquisitions on their own. |
| `folder_and_id_changed` | Error | *Check and save* (and publication) found an ID changed in the same save as a folder change of the same entity: for example a repeat ID `Day1 -> DayX` while one of its folders was renamed or removed, a condition ID changed while its folder was renamed, or a removed folder's ID typed for the new folder. Neither change is guessed from the other, so the message names the entity, its saved ID and its folders. Undo the ID edit: put the saved ID back (the folder change then saves on its own). A removed folder's ID stays used and is not given to another folder (experiment-v1 4.9). Nothing was published. |
| `R_DRAFT_CHANGED` | Error | Notebook: `morphobricks.draft.json` was changed since this notebook read or saved it (another notebook, or a program, saved its own answers in the same output folder). Nothing was saved or published, and the answers saved there are kept. Press **Check folder** in step 1 to read them, then give your change again. |
| `R_DRAFT_EXISTS` | Error | Command line (`describe --publish`): the output folder holds `morphobricks.draft.json`, answers saved in the notebook that the command does not read. Nothing was published or written. Finish those answers and publish them in the notebook (step 1, **Check folder**, then **Check and save**), or, if they are not wanted, remove or archive that file yourself and run the command again. |
| `removal_mismatch` | Error | A saved folder is no longer in the description (neither under its folder nor under its IDs) and no **Yes, remove** confirms it, or a removal names a folder the description still holds. Confirm the removal of a folder that is gone, or undo the change; withdraw the removal of a folder that is still there, or check the folder again. Nothing was published. |
| `mode_switch_unconfirmed` | Error | The description would change between fast mode (one channel list for the experiment) and the strict per-acquisition mode, but you did not switch it: a published description keeps its mode when it is reopened. Tick or untick **Every imaging unit has the same channels in the same order** on *Tag channels* yourself, then save again. Nothing was published. |
| `E_FAST_LAYOUT_MISMATCH`, `E_FAST_MARKER_WITHOUT_LAYOUT` | Error | The description is refused by the reader: an acquisition marked `channels_from = "experiment"` lists other channels or another image type than `channel_layout` (`E_FAST_LAYOUT_MISMATCH`), or an acquisition has `channels_from` in a description without `channel_layout` (`E_FAST_MARKER_WITHOUT_LAYOUT`). In a description with `channel_layout` every acquisition must say `channels_from` (`E_MISSING_FIELD`). |
| `description_unreadable` | Error | `morphobricks.toml` or `morphobricks.records.jsonl` cannot be read. A description the reader refuses is reported with its own code (`E_*`, `R_FILE_SYMLINK`) and table path. |
| `unreadable_folder` | Error | A folder could not be opened. Check permissions. |
| `link_outside_folder` | Error | A link in the experiment or masks folder leads outside it (the message names each link and its target), and nothing outside the selected folder is read. Copy or move the files in, or select a folder that holds them, then remove the link. |
| `link_loop` | Error | A link leads back to a folder that holds it. Remove it. |
| `mounted_folder` | Error | A folder (or file) in the experiment or masks folder is a mount point: another disk or share, or a bind mount of another folder, so its files come from outside the selected folder. It is named and not entered. Copy the files in, or select the mounted folder itself. Where the system's mount table cannot be read (macOS, Windows), only a mount of another device is seen. |
| `hard_linked_file` | Warning | An image or mask has other names (hard links; the message gives the number), perhaps outside the folder, so it can change through a name the run does not see. It is read as usual. Replace the hard link with a copy if it may change. |
| `linked_twice` | Error | One file is reached by two paths (a link and its target, two links, or two hard links), so it would count twice under two names; both are named. Keep one path to each file. |
| `reserved_folder_name` | Error | A folder is named `single` in another case (`Single`), or a folder named `single` has other folders beside it. Rename it to `single` in lower case, or give it a name of its own. |
| `case_conflict` | Error | Folders of one level have names that differ only in case, next to each other or anywhere in the experiment (`control/Rep1` and `treated/rep1`). Spell each name one way, or rename or merge them. |
| `invalid_name` | Error | A folder or image name starts or ends with a space, or is `NA`, `nan`, `none`, `null`, `n/a` or `<NA>` (any case), which tables read as a missing value. Rename it. |
| `duplicate_position_id` | Error | Two images in one folder have the same name apart from the extension or upper/lower case (`img_01.tif` and `img_01.ome.zarr`), so their positions cannot be told apart. Rename one, or move it out of the experiment. |
| `folder_too_deep` | Error | Folders below the technical repeat. Move the images up, or select a lower folder as the experiment. |
| `output_inside_experiment` | Error | The output folder is inside the experiment. Choose another. `calibrate` refuses it too, before it creates the folder or writes `calibration.csv` (`OutputInsideExperimentError` from `write_calibration` and `update_calibration`). Without an experiment given or recorded in the output folder it cannot be checked: `calibrate` then prints a one-line note on stderr and goes on. |
| `no_images` | Error | No images to analyse (or all excluded). Check the folder. |
| `unreadable_tiff` | Error | A TIFF file cannot be opened. Export it again, or exclude it. |
| `unreadable_ome_zarr` | Error | A `.zarr` folder is not an OME-Zarr image that can be read; the message says why. That includes an image whose metadata do not name its axes (OME-NGFF 0.3 to 0.5 require them), and every OME-NGFF 0.1 or 0.2 store (their metadata never name the axes): the axes are never guessed. Re-save an 0.1 or 0.2 store as OME-NGFF 0.4 or later. Export it again as OME-Zarr (OME-NGFF 0.4 or 0.5) with its axes, or exclude it. |
| `ome_zarr_axes_mismatch` | Error | An OME-Zarr image declares an axis with several planes that the experiment has no role for (or has no X/Y), or the confirmed roles give one of its axes another role than its metadata declares (for example its depth `z` confirmed as time `T`); the message names each image with its declared axes and the confirmed roles. An OME-Zarr image is read only with the roles it declares. Check the axis roles, or process it separately. |
| `multiple_series` | Error | A file or OME-Zarr folder holds several images (positions, scenes, series, wells or fields). Export them separately, or exclude it. |
| `unsupported_dtype` | Error | Unsupported pixel type (e.g. float16). Convert, or exclude it. |
| `mixed_dtypes` | Error | Images have different pixel types. Process them separately or convert. With a saved experiment description the images are compared within each unit (and, in the run, among units of the same image type): colour (RGB) and fluorescence units in one description are not reported here but stop the run with `R_HETEROGENEOUS_LAYOUT_NOT_ENABLED`. |
| `different_channel_counts` | Error | Images of one folder have different numbers of channels (an image without a channel dimension has one). Folders may differ; the images of one folder (one acquisition) must not: move the others into a folder of their own, or exclude them. If a dimension got the channel role by mistake, correct the axis roles. |
| `inconsistent_dimensions`, `inconsistent_mapping` | Error | Images have their dimensions in a different order. Export them the same way or process separately; named-axis copies (`convert`) keep each image's order, so they do not solve this. |
| `missing_axes` | Error | Some images lack a dimension. Confirm they have one plane along it (step 2, or `--missing-as-singleton` for `check` and `run`), or export them again. |
| `ambiguous_missing_axes` | Error | It is unclear which dimension an image lacks. Export it again. |
| `axes_not_named` | Warning | The metadata of some images does not name their axes (a plain stack of pages, `IYX` or `QYX`), so which dimension is depth, time or channel comes only from the roles you confirm. The images are read with them, and the roles are recorded as confirmed by you. Check the roles with the preview. Before segmenting these images in other tools, consider saving named-axis copies (OME-TIFF) with the `convert` command (§5), so every tool reads the same axes. |
| `axes_yx_swapped` | Error | The roles you confirmed give the Y and X the image header names the other way round, so the images are not read (never transposed). The message names the likely cause. Two fixes: correct the axes (if the header is right, choose its roles), or convert (save named-axis copies with your roles with the `convert` command, §5, then run preprocessing on the copies). |
| `axes_contradict_header` | Error | The roles you confirmed contradict the axes the image header names (for example the header says channels, C, and you chose time, T). The images are not read under a contradicted declaration. The message names the likely cause, often the tool that saved the files: tifffile labels a 3-D stack saved with `imagej=True` or `ome=True` as `CYX`, and an ImageJ stack of slices reads as depth even for a time-lapse. The same two fixes as `axes_yx_swapped`. |
| `axes_x_before_y` | Error | The roles you confirmed put X before Y (for example a plain stack confirmed `TXY`). Reading them would transpose every plane, and nothing is ever read transposed. Save the images again with Y before X, or as OME-TIFF copies with Y before X (the `convert` command, §5); if the roles are wrong, correct them. |
| `plain_stack_not_confirmed` | Error | Some images that do not name their axes (plain stacks) are new or changed since you confirmed the axis roles, so the confirmation does not cover them: a plain stack's roles are your statement about the images you checked, and are never applied to others. Check their roles with the preview and confirm them again (on the command line: give `--axes`). |
| `missing_xy_axis`, `duplicate_axis_role` | Error | X/Y missing or a role used twice. Correct the axis roles. |
| `unassigned_axis` | Error | A dimension with several planes has no role. Choose Z, C or T. |
| `unsupported_axis` | Error | A position or tile dimension. Export each position separately. |
| `z_stack_not_supported` | Error | Images have more than one depth slice (Z-stacks); every such image is named with its number of slices. MorphoBricks v1 analyses 2-D planes. Make 2-D projected copies first (`convert EXPERIMENT --to FOLDER --axes ROLES --project max|mean|sum`; the originals are only read) and run on the copies; or exclude the images. A Z axis of one slice is accepted. Found from the header's Z axis, and again from the axis roles you confirm (a plain stack given a Z role). |
| `plane_over_memory_limit` | Error | A plane cannot be read within the memory limit, not even in blocks (for example, it is stored compressed in one piece). Raise the limit, or save the images again as tiled or uncompressed TIFF files or as OME-Zarr with smaller chunks. |
| `compression_not_bounded` | Error | Planes too large to read whole within the memory limit are stored with a TIFF compression whose decoding memory has not been measured (old-style JPEG, PackBits, Zstandard, WebP, JPEG 2000, ..., or JPEG in another form than 8-bit greyscale, RGB or YCbCr), so they are not read in blocks: only uncompressed, deflate (zlib), LZW, LZMA and 8-bit greyscale, RGB or YCbCr JPEG TIFF images are (decoded by imagecodecs, a required dependency: an image is also refused when the installed imagecodecs is missing, older than 2024.12.30 or, for JPEG, not built on libjpeg-turbo 3, and the message says so). The message names the compression. Save the images again as uncompressed, deflate, LZW or LZMA TIFF files (tiled, for large images) or as OME-Zarr, or raise the memory limit so that their planes are read whole. The same refusal, from the header and before any pixel is read, stops window reads and `RunInputs.read_plane` of such an image whatever its size. |
| `unsupported_layout` | Error | The image cannot be read one plane at a time. Check the axis roles or export again. |
| `unreadable_planes` | Error | Pixel data cannot be decoded. The message names each plane that failed (its T, Z and C index and the channel ID) and why, at most 20 per image followed by "and N more"; the run report lists every one (`unreadable_planes`; in the notebook's run report, under *Unreadable planes*, one folded list per image); no `plane_histograms.csv` is written, since one channel that cannot be measured fails the whole table. Export again, or exclude the image. |
| `image_over_max_ram` | Error | `--workers auto --max-ram`: an image needs more working memory to read than the budget leaves (estimated from its header). Nothing was read. Raise `--max-ram`, or lower `--memory-limit` so its planes are read in smaller blocks. |
| `invalid_bin_count` | Error | Bin count outside the allowed range. Use the default or a value in range. |
| `experiment_not_recorded` | Error | `run` was given no experiment folder, and the output folder holds no v0.4.0 run that records one (or no output folder was given). Give the experiment folder. |
| `axes_not_confirmed` | Error | No axis roles were given and the output folder holds no earlier run that records them. Confirm the roles (`--axes`). |
| `output_locked` | Error | Another run is using the output folder; it is named with its computer, process and start time (a process on this computer that is no longer running is named as a lock that looks stale). Wait for it to finish; if it was stopped or the computer restarted, remove its lock (`--remove-stale-lock`). Nothing was written. |
| `copy_folder_overlaps_experiment` | Error | `convert`: the folder for the copies is the experiment folder, inside it, or holds it. Choose a new folder outside the experiment. |
| `copy_folder_overlaps_masks` | Error | `convert`: the folder for the copies is the masks folder, inside it, or holds it; copies there would be read as masks. Choose a folder outside the masks folder. |
| `copy_folder_not_a_folder` | Error | `convert`: the path given with `--to` is a file. Choose a folder. |
| `copy_path_too_long` | Error | `convert` on Windows: a copy would have a file whose path, while it is written in the conversion's work folder, is longer than 259 characters, or a folder whose path is longer than 247, which Windows cannot handle. Nothing is written. Choose a folder for the copies with a shorter path, or shorten the names of the images or folders named. Not checked when Windows' long paths are enabled (the `LongPathsEnabled` setting). |
| `copy_names_collide` | Error | An OME-Zarr copy: two images of one folder differ only by their extension, so their copies would have the same name. Nothing is written. Rename or exclude one of them. |
| `copy_folder_has_links` | Error | `convert`: the folder for the copies holds symbolic links (directory junctions on Windows) or mount points, named `link -> target`, through which the copies would be written elsewhere, perhaps over the originals. Nothing is written; remove them or choose a new, empty folder. |
| `copy_folder_unreadable` | Error | `convert`: a folder below the folder for the copies cannot be read, or disappeared while it was checked, so it cannot be checked for links or mount points; it is named. Nothing is written. Make it readable or remove it, or choose a new, empty folder. |
| `conversion_work_folder_in_use` | Error | `convert`: the folder for the copies holds the work folder of a conversion whose process still runs on this computer (its lock was probably removed by mistake). Nothing is removed or written. Wait for it to finish; if that process is not a conversion, move or remove the work folder yourself. Only Linux and macOS can tell whether a process runs, and only on this computer: on Windows, or for a conversion on another computer, the work folder is taken for one left by a stopped conversion and removed. The folder's lock is what keeps two conversions apart, so remove a lock (`--remove-stale-lock`) only once you are sure no conversion into that folder is running on any computer. |
| `conversion_work_folder_unowned` | Error | `convert`: the folder for the copies holds `.mbconv` (or `.morphobricks-conversion.partial`, its earlier name), the name of the folder a conversion writes its copies in, but not as a conversion leaves it: its owner file is missing or not well formed, or it holds something else than that file and the folder `files`. An owner file of NUL bytes only is named as unreadable, possibly left by a power loss, so ownership cannot be proven. Nothing is removed or written. Move or remove it yourself, or choose a new, empty folder. |
| `conversion_work_folder_not_removed` | Error | `convert`: the folder for the copies holds the work folder of a conversion that was stopped, and it cannot be removed: a file in it may be open in another program (an antivirus, or a sync client such as OneDrive). Nothing is written, and the work folder is named. Close those programs or wait, then convert again; or remove the work folder yourself. |
| `copy_folder_not_empty` | Error | `convert`: the folder for the copies holds files and no conversion record. Nothing is overwritten; choose a new or empty folder. When the folder also holds the work folder of a stopped conversion, the message says the files named may be copies it had moved into place when it was interrupted: remove them, then convert again. |
| `copy_differs` | Error | `convert`: images in the folder of copies differ from the copies this conversion would write (an original changed, other roles, a copy changed, an image no longer in the experiment), or the folder holds copies of another experiment. Nothing is overwritten; convert into a new folder, or remove those copies first. |
| `cannot_convert_pixel_type` | Error | `convert`: images of 64-bit integers cannot be saved as OME-TIFF, and pixel values are never changed to fit. Save them again with named axes in a 32-bit type if their values fit, or correct the roles. |
| `nothing_to_project` | Error | `convert` with a projection: no image has a depth axis (Z) under the roles you gave. Check the roles; nothing was written. |
| `projection_z_range_outside` | Error | `convert` with a projection: the slice range does not fit every image; the shorter images are named. Give a range every image has, or leave it out. |
| `projection_cannot_be_exact` | Error | `convert` with a projection: a sum or mean of that many slices of that pixel type could not be held exactly. Use max or mean, or a shorter slice range. |
| `projection_overflow` | Error | `convert` with a projection: a sum overflows to infinity (float64) at the pixels named. Use max, or mean if the values allow it. |
| `projection_undefined` | Error | `convert` with a projection: a sum adds an infinity to its opposite at the pixels named, which is undefined. Use max. |
| `projection_over_memory_limit` | Error | `convert` with a projection: one input plane and its running total do not fit the memory limit. Raise `--memory-limit`. |
| `projection_masks_3d` | Error | `convert` with a projection: masks of the images have depth slices (Z), and masks are never projected. Make 2-D masks on the projected images, or move these masks out of the masks folder. |
| `projection_masks_axes_unknown` | Error | `convert` with a projection: masks have more than Y and X and their axes are not named, so it is not known whether they have depth slices. Name the axes in the masks' metadata, or move them out. |
| `projection_zarr_not_supported` | Error | `convert` with a projection: OME-Zarr images are not projected yet. Exclude them. |
| `source_changed_during_check` | Error | `convert`: an original changed while its metadata was read, so the metadata may not describe the file that would be copied. Nothing was written; convert again when nothing writes into the experiment. |
| `conversion_failed` | Error | `convert`: an image could not be copied (unreadable, changed while copied, or the folder cannot be written). The copies written by this conversion were removed; fix the cause and convert again, or exclude the image. |
| `not_enough_space` | Error | `convert`: the copies would not fit on the drive. Free space or choose another drive. |
| `conversion_record_unreadable` | Error | The conversion record of a folder of named-axis copies (**.morphobricks-conversion.json**) cannot be read, or is not of version 4 (versions 1 to 3 were made before v0.5.2), so `run` stops and `convert` refuses the folder. Convert again into a new folder. |
| `conversion_source_changed` | Warning | An original changed or went missing after its named-axis copy was made; the copies hold the images as converted. If the originals were corrected, convert again into a new folder. |
| `copy_changed_after_conversion` | Error | An image of the folder of named-axis copies changed (its size, time or SHA-256), went missing or was added after the conversion, so the conversion record does not describe it, and the bricks (`RunInputs`) would refuse a run on it. The run stops before it writes anything but the report: convert the originals again into a new folder, and run preprocessing on that one (same output folder). |
| `source_changed_during_run` | Error | An image changed or went missing while the run was checking or reading it, so its rows would not describe it. No scientific outputs are published and sources are untouched; the discovery report is written. Wait until nothing writes to the experiment (an acquisition or a copy still running), then run again: the images already read resume from their staged rows. |
| `mask_changed_during_check` | Error | A mask changed while its header was read, so the check cannot tell which version it describes. Wait until nothing writes to the masks (a segmentation or a copy still running), then check or run again. |
| `mask_changed_during_run` | Error | A mask changed or went missing after the run checked its header and before it published its results (its size or time, or for an OME-Zarr label image its files; with `--verify-content` also its content). Nothing is published; the discovery report is written. Wait until nothing writes to the masks, then run again: the images already read resume from their staged rows. |
| `mask_header_unbound` | Error | `scan_masks` called directly with masks found without their headers bound to the files' fingerprints (`find_masks(..., bind=True)` or `preflight(..., bind_headers=True)`), so their headers might describe other versions of the files. No mask is read and no report is made. Find the masks with their headers bound and scan again. The `masks` command and the notebook always bind them. |
| `mask_changed_during_scan` | Error | The `masks` command (or **Describe the masks**): a mask changed or went missing after its header was read or while its pixels were read. No report is written; an earlier `mask_relationships.json` is left as it was. Wait until nothing writes to the masks, then describe them again. |
| `design_unreadable` | Error | The `experiment_design.csv` an earlier run wrote cannot be read. Correct it, or delete it to confirm the design again. |
| `no_usable_planes` | Error | No plane has finite pixel values. Check the images. |
| `tiff_outside_leaf` | Error | Images sit in a folder that also holds subfolders, so they have no condition or repeat of their own. A folder holds either images or subfolders, never both. Move the images into a folder of their own, or out of the experiment folder. |
| `non_tiff_file`, `empty_folder` | Warning | Files or folders that are skipped. |
| `image_named_as_mask` | Error | An image is named like a mask of another image (`f1_mask.tif` beside `f1.tif`; the endings `_mask`, `_masks`, `_cp_masks`, `_labels`, `_label`, `_seg`, `_segmentation`), and a mask is never analysed as an image. If it is a mask, move it into a masks folder outside the experiment, or exclude it; if it is an image, rename it. |
| `mask_folder_in_experiment` | Warning | A folder inside the experiment is named like a masks folder (`masks`, `ctrl_masks`, `CellLabels`) and holds files named like masks of the images; they would be analysed as images. Move it out of the experiment and give it as the masks folder, or rename it if these are images. |
| `planes_read_in_blocks` | Warning | Planes too large for the memory limit are read in blocks. The results are the same; nothing to do. Raise the limit to read them whole, which is faster. |
| `different_sizes` | Warning | Images have different widths or heights. Usually expected; otherwise check the camera settings. For a plain stack (no axes in its file), the sizes are compared once you confirm its axis roles, with those roles. |
| `different_plane_counts` | Warning | Images have different numbers of depth slices, time points or other planes. Images with more planes weigh more in pooled statistics. Check the acquisition if this is not expected. For a plain stack, compared with its confirmed axis roles, as `different_sizes`. |
| `different_pixel_sizes` | Warning | The physical pixel size or z-step (from the metadata or `calibration.csv`) differs between images by more than 0.1 % (for example different objectives). Images outside the largest group of matching sizes are listed; when sizes only change by small steps from image to image, none is listed. Check the microscope settings if this is not intended. |
| `missing_pixel_size` | Warning | Some images have a pixel size (from the metadata or `calibration.csv`) and others do not, so it cannot be checked; images stated as measured in pixels count as without one, and the message says how many. Export them again with metadata, or state their sizes with `calibrate`, if needed. |
| `unsupported_pixel_size_unit` | Warning | An image gives its pixel size or z-step in a unit that is not converted (for example inches), or an OME-Zarr scale without a unit; only pm, Å, nm, µm, mm, cm and m are. The message names each image, axis and unit. Those sizes are not used: the image is treated as uncalibrated on those axes. Export it again with the pixel size in a supported unit, or state it with `calibrate`, if the physical size matters. Not reported for an axis whose size is stated. |
| `invalid_physical_calibration` | Warning | An image's metadata give a pixel size, z-step or time step that cannot be used (OME-XML in a TIFF that cannot be read; an ImageJ TIFF with a unit but no resolution tags; for OME-Zarr: `coordinateTransformations` that are malformed, of an unsupported type, stored in a separate file, or a scale that is zero, negative or not a number; for any image: a size too large or too small to be written in micrometres, such as `1e308 m`); the message names each image and what is wrong, with the value. The image is treated as uncalibrated on those axes; no size is made up. Correct the metadata, or state the sizes with the `calibrate` command: the warning goes once every lost pixel size and z-step is stated. A lost time step cannot be stated, so its warning stays until the metadata are corrected. |
| `partial_pixel_size` | Warning | An image has a pixel size along only one of X and Y (the other unknown or stated as pixels). Bricks that measure lengths will refuse it: nothing is completed or approximated. State both sizes with `calibrate`, or both as pixels. |
| `non_square_pixels` | Warning | An image's X and Y pixel sizes are not exactly equal (for example 0.5 and 0.5004 µm). Bricks that measure lengths (perimeter, Feret diameter, axis lengths) will refuse it. If the pixels are square, state both sizes, equal, with `calibrate`; otherwise no action is needed if no lengths are measured. |
| `calibration_axis_absent` | Warning | `calibration.csv` states a size for an axis some images do not have (for example a Z size for 2D images or projections); the message names them. No size is recorded for an axis an image does not have. Give the statement a narrower target, or no action if it is meant for the other images. |
| `calibration_differs_from_header` | Warning | `calibration.csv` states a pixel size (or `pixels`) that differs from an image's metadata; the message names each image and axis with both sizes. The stated size is used. Correct or remove the statement if the metadata are right. |
| `calibration_target_not_found` | Error | `calibration.csv` names a folder or image that is not in the experiment (names are compared exactly, case included). Correct the target, or remove the line. |
| `calibration_target_root` | Error (reason) | Not a message of its own: the reason given inside the refusal of a pixel-size target of only slashes, backslashes and `.` steps (`/`, `//`, `\`, `.\`, `./.`, `.//`, `././`), which names no folder and is never read as the whole experiment. In `calibration.csv` the message is `calibration_unreadable` (below), naming the line and this reason, and the run stops, the file kept; `calibrate` and the notebook's calibration card refuse it and change nothing. For the whole experiment give the target `.` (in the notebook, **Use for all images**); otherwise give the folder or image. |
| `calibration_unreadable` | Error | `calibration.csv` cannot be read; the message names the line and what is wrong. The file is left as it is: correct it, or remove it to use the sizes in the metadata. |
| `unbalanced_images` | Warning | A condition and biological repeat has fewer than half or more than twice the median number of images. Check for missing, misplaced or duplicated images. |
| `repeat_missing_conditions` | Warning | A biological repeat is not found in every condition. It is still used. If it has another folder name in some condition, pair it in the experiment design (step 1). |
| `design_not_confirmed` | Error | Some repeat groups are not confirmed as comparable. Confirm them in the notebook's describe steps and save the description again. |
| `lab_links_invalid` | Error | The `--lab-links` file cannot be read, is not one JSON object with exactly `repository_url`, `commit`, `experiment_path` and optionally `repeat_codes`, or a value is of another type, empty, has spaces around it, or (`commit`) is not a full lowercase SHA, or (`repository_url`) holds a credential, a query string or a fragment; or `--confirm-repeat-codes` was given with nothing to confirm. Nothing is read or written. Correct the file. |
| `lab_links_repeat_codes_not_confirmed` | Error | The lab-tool links map repeat codes to biological repeats, and the mapping was not confirmed. Check each pair the run prints, then give `--confirm-repeat-codes`. |
| `lab_links_repeat_unknown` | Error | A lab-tool repeat code names a biological repeat the description does not declare (IDs are compared exactly; a folder name or display name is not an ID). Correct the file. |
| `lab_links_repeat_twice` | Error | Two lab-tool repeat codes name the same biological repeat; each repeat has exactly one code. Correct the file. |
| `lab_links_repeat_missing` | Error | The lab-tool repeat codes leave out a biological repeat of the description; a mapping names every repeat. Add its code, or leave `repeat_codes` out. |
| `rgb_not_possible` | Error | The image type RGB was chosen, but the images are not three channels of a whole-number pixel type. Choose fluorescence, or check the axis roles: the RGB samples must have the role C. |
| `repeat_group_conflict` | Error | A repeat group holds two folders of the same condition. Move one into another replicate on the *Replicates* page and save the description again (*Check and save*). |
| `invalid_experiment_design` | Error | A repeat group has no usable name (empty, `NA`, `nan`, `none`, `null`, `n/a`, `<NA>`, or spaces around it), two repeat groups are spelled the same apart from upper and lower case, or a confirmation date is not written `YYYY-MM-DD`. It comes from a description edited by hand or rows built in code; correct the description (the message names the folder). |
| `image_intensity_outlier` | Warning (intensity check) | An image's median or high percentile in a channel is far from the other images of its condition and biological repeat. Look at it: it may be out of focus, contain debris or bubbles, or have been acquired differently. Exclude it if it is faulty. |
| `repeat_intensity_shift` | Warning (intensity check) | A biological repeat is brighter or darker than the other repeats of its condition. Check whether it was prepared or acquired differently; normalising per biological repeat in later bricks removes such differences. |
| `saturated_image` | Warning (intensity check) | More than the limit (default 1 %) of an image's pixels in a channel are at the largest value of its pixel type, so their true intensity is lost. Check the exposure, gain or laser power. |
| `no_intensity_spread` | Warning (intensity check) | An image's channel has the same 1st and 99.8th percentile (empty or blank), so it cannot be normalised on its own. Check the channel, or exclude the image. |
| `channel_unlike_others` | Warning (channel check) | One imaging unit's channel does not look like the same channel in the other units of its condition: its level, brightness or contrast differ by about 1.8 times or more and by much more than the units usually differ. The message names the nearest other channel, or says it looks like a channel this unit does not have (for example a fourth channel only other repeats have). Look at that unit's images in that channel: check its channel tag and how the images were exported, and whether the stain, exposure or acquisition differed. Nothing is changed or blocked. With very noisy data (units differing by about 30 % per channel) false warnings are frequent (171 of 300 simulated runs without a swap warned); floats converted from whole numbers with medians near zero can warn falsely, and 16-bit and 0-1 float images agree only above the integer floor (a high percentile above about 333 counts). |
| `possible_channel_swap` | Warning (channel check) | Two channels of one imaging unit look like each other's channel in the other units (for example "DAPI and GFP look swapped"), or one channel clearly looks like another channel of the same unit: its channel tags may be in the wrong order. Check the unit's channels against how its images were acquired; if they are swapped, correct them in the experiment description and run again. Nothing is changed or blocked. A swap made in every unit of a condition is not found: the check compares each unit with the other units of its condition. |
| `channel_without_finite_pixels` | Warning (intensity check) | An image's channel has no finite pixels (every value NaN or infinite), so its intensities were not checked and it cannot be normalised from its own pixels. Exclude the image, or fix the channel in the source data and run again. |
| `channel_names_differ` | Error | Channel names differ between images at the same channel number, so different stains would be pooled as one channel. Spaces at the start or end of a name are ignored (and noted in the manifest); otherwise names must match exactly, and names that differ only in inner spaces are quoted and reported as such. Check the channel order; export the images again with the same channel order and names, or exclude the images whose channels differ. |
| `non_finite_pixels` | Warning | NaN or infinite pixels are left out of the histograms and counted. |
| `mask_hierarchy_cycle` | Warning (mask step) | The direct parents of some mask types form a cycle (for example A inside B in some images, B inside C in others and C inside A), so no parent-child hierarchy can be inferred for them. The hierarchy lists them together as `[cycle: …]` with every link. Check those masks; nothing is guessed. |
| `no_finite_pixels` | Warning | Some planes have a channel without finite pixels; those channels are left out of group statistics. |
| `excluded_images`, `excluded_path_not_found` | Warning | Images excluded as requested, or an excluded path that matches no image. |
| `manifest_section_digests_malformed` | Error (other bricks) | The run manifest's `experiment_description.section_digests` does not hold exactly the sections `conditions`, `channels`, `repeats` and `masks`, each a lowercase SHA-256 (`ManifestSectionDigestsError`, when the manifest is read). The manifest was changed after the run; the message names the field. Nothing is filled in: run preprocessing again. |
| `manifest_section_digest_mismatch` | Error (other bricks) | `RunInputs.open` found a section digest in the run manifest that is not the digest of that section of the published `morphobricks.toml` the run read (`SectionDigestMismatchError`); the message names each such section. The manifest was changed after the run, and later bricks key their caches on these digests, so the run is refused, never corrected. Run preprocessing again. |
| `manifest_channel_coverage_mismatch` | Error (other bricks) | `RunInputs.open` found a `channel_coverage` in the run manifest that is not the channel coverage of the published `morphobricks.toml` the run read (`ChannelCoverageMismatchError`); the message names `experiment_description.channel_coverage` and each biological repeat that differs. The manifest was changed after the run, so the run is refused, never corrected. Run preprocessing again. |
| `manifest_unit_record_malformed` | Error (other bricks) | A record of the run manifest's `experiment_description.units` is not an object with `repeat`, `condition`, `technical_repeat`, `channels` and `image_type_header_verified` of their types, or holds a field the run does not write (`ManifestUnitRecordError`, when the manifest is read). The manifest was changed after the run, or was written by a newer release that records a unit field this one does not know (refused, never ignored); the message names the unit record and the field. Nothing is filled in: run preprocessing again. A manifest written before v0.5.2 never reaches this check: its version is refused first. |
| `manifest_unit_record_mismatch` | Error (other bricks) | `RunInputs.open` found a unit record in the run manifest that is not a unit of the published `morphobricks.toml` the run read: an unknown repeat, condition or technical repeat, a unit recorded twice or not at all, other channels, or a declared `rgb_header_override` that the description does not declare (`UnitRecordMismatchError`); the message names the unit record and the field. Run preprocessing again. |
| `masks_folder_not_found` | Error | The masks folder does not exist. Select the folder that holds one folder per kind of mask. Later bricks opening a run (`RunInputs.open(..., masks_root=...)`) refuse a masks folder given that does not exist or is a file with the same code (`MasksFolderNotFoundError`); it is never read as a folder without masks. Give the folder that holds the masks, or leave `masks_root` out to use the one the run recorded. |
| `masks_folder_unreadable` | Error | A masks folder given to a later brick (`RunInputs.open(..., masks_root=...)`) cannot be listed, for example for lack of permission, so its masks cannot be checked against the run (`MasksFolderUnreadableError`); the message names the folder and the reason. Check its permissions, or give another folder. |
| `output_inside_masks` | Error | The output folder is inside the masks folder, where the run's files would be read as masks. Choose another output folder. |
| `experiment_inside_masks` | Error | The experiment folder is inside the masks folder, where its images would be read as masks. Select the folder that holds only the masks, next to the experiment folder. |
| `copies_inside_masks` | Error | The masks folder holds named-axis copies (a conversion record), which would be read as masks. Move the copies out of the masks folder. |
| `masks_inside_experiment` | Error | The masks folder is inside the experiment folder, where its folders would be read as conditions and repeats. Move it next to the experiment folder. |
| `mask_names_differ_in_case` | Error | Two kinds of mask have names that differ only in upper/lower case. Rename one. |
| `duplicate_mask` | Error | An image has two masks of one kind (two extensions, or a file and an OME-Zarr label image of the same name). Keep one. |
| `unreadable_mask` | Error | A mask cannot be opened, or holds several images, or the `labels` group of an OME-Zarr image cannot be read (it must list its label images). Save the mask again as a TIFF label image, or repair the OME-Zarr labels. |
| `mask_not_2d` | Error | A mask has dimensions other than Y and X that its metadata does not name as depth (Z) or time (T), for example a plain stack. (A mask one pixel high or wide is a 2D mask, not this error.) State the axes of its mask type once (`--mask-axes Cells=TYX`), or save it as a 2D label image, or with its axes named (an ImageJ hyperstack or OME-TIFF with axes TYX or ZYX). |
| `shared_mask_not_supported` | Error | A mask type is stated `YX` (`--mask-axes Nuclei=YX`): one 2D mask shared by every plane of its image. That option was removed: MorphoBricks v1 analyses 2-D planes, and a mask has the time points of its image. Remove the statement (a 2D mask needs none); for a time-lapse, save one mask plane per time point (`TYX`, named or stated). |
| `invalid_mask_axes`, `mask_axes_contradict_header` | Error | Axes stated for a mask type do not fit its masks (one role, Y, X, Z or T, per axis; Y and X once each), name a type that is not found, or contradict the axes a mask's metadata names. Correct the statement. |
| `mask_layout` | Error | A mask is stored with its Y and X the other way round from its image as read (its header names X before Y, for example `XY`, named in the message with the mask; or the image's height and width swapped). Masks are never transposed: save it again with `numpy.swapaxes(mask, -1, -2)`. |
| `mask_planes_differ` | Error | The masks of one image have different numbers of depth slices or time points. Make every mask of an image with the image's time points. A 2-D mask is never shared by several planes. |
| `mask_planes_mismatch` | Error (`run`) | A mask does not have the time points and depth slices of its image under the confirmed axis roles: the masks of a time-lapse must have the same T as their image, one plane per time point, and a 2D mask fits only an image with one plane, whatever its metadata names (it is never shared by several planes). Save one mask plane per time point, with the axes named (`TYX`) or stated (`--mask-axes Cells=TYX`), or check the axis roles. |
| `mask_size_mismatch` | Error | A mask does not have the width and height of its image. Check that it was made from that image at full resolution. |
| `mask_pixel_type` | Error | A mask's pixels are not whole numbers (for example decimals). Save it as an integer label image. |
| `mask_missing` | Warning | Some images have no mask of a kind that other images have; their objects of that kind cannot be measured. Add the masks, or exclude the images. |
| `ambiguous_mask` | Error | A mask's name could belong to two images (one image name starts another's) or fit both layouts. Rename the masks or the images so each names one image and one kind. |
| `mask_without_image`, `mask_outside_type_folder`, `mask_of_excluded_image` | Warning | Files in the masks folder that match no image of the experiment (check the folder and file names), other files directly in the masks folder, or masks of excluded images. They are not used. |
| `images_not_in_manifest`, `images_missing`, `images_changed`, `identity_differs`, `axis_mapping_mismatch` | Error (other bricks) | A later brick checked its images against this brick's run (`check_sources`): an image the run did not analyse, no longer there, changed since, recorded with other labels or position (or with none), or read with axes the run gave other roles. Run this brick again on the experiment as it is, or use the run's labels and axis roles. |
| `UnrecordedFilesError` | Error (other bricks) | The experiment holds images or masks that the preprocessing run neither recorded nor excluded, for example added after the run. Later bricks never look for images themselves, so they refuse rather than leave them out: run this brick again on the experiment as it is (with its masks folder, `--masks`, for masks), or exclude the images. Files the run ignores (non-image files, system files, masks matching no image) do not count. A run holding OME-Zarr images also needs the `zarr` extra here (`pip install "morphobricks-preprocessing[zarr]"`), so that their label images can be checked; an OME-Zarr image whose `labels` group cannot be read is refused too. |
| `zarr_not_installed` | Error | OME-Zarr images need the `zarr` extra. Install it, or exclude the OME-Zarr images. |

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

The output folder must hold the published experiment description (`morphobricks.toml`,
written by the notebook's *Check and save*); without it `run_experiment` returns
`written=False` with the one error `A_DESCRIPTION_REQUIRED` and writes nothing. The run writes
the `experiment_design.csv` derived from the description; `run_experiment` takes no design
(v0.5.1). Later bricks read the pairing from the description (`RunInputs.fovs()`:
`repeat_group`, `condition_id`, `technical_repeat_id`).

Lab-tool links (v0.5.1) are given as `run_experiment(..., mode=RunMode(lab_links=links))`, with
`links = read_lab_links("links.json", confirmed=True)` (or `LabLinks(...)`); `confirmed=True`
is your confirmation of its repeat codes. They are recorded in the manifest's `lab_links` only.

Several images can be read at once, each in its own thread (issue #107):
`run_experiment(..., mode=RunMode(workers=4))`, or `RunMode(workers="auto", max_ram=8 * 2**30)`
for at most the CPUs and what the largest image's estimated working memory allows in 8 GiB. The
outputs are byte for byte those of `workers=1` (the default); `result.workers` says how many
threads read. With more than one worker, `progress` is still called in the calling thread, as
each image is done.

Stated pixel sizes (§5) are built with `calibration_entry` and written with
`write_calibration`; the next run records them, and later bricks read every image's sizes
with their source through `RunInputs.pixel_sizes`:

```python
from morphobricks_preprocessing import Calibration, calibration_entry, write_calibration

calibration = Calibration(tuple(calibration_entry(".", axis, 0.65, "um") for axis in ("X", "Y")))
write_calibration(calibration, result.output_folder)
result = run_experiment(output_folder=result.output_folder)  # no row is recomputed
```

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
report.hierarchy_cycles  # (): types whose direct parents form a cycle, if any
write_mask_report(report, "/data/exp1_MorphoBricks_Preprocessing/mask_relationships.json")
```

`scan_masks(..., thresholds={"inside_object_share": 0.8})` changes a threshold (the others
keep their defaults, `DEFAULT_THRESHOLDS`). The label images of OME-Zarr images are always
used, with or without a masks folder; `preflight(..., mask_axes={"Cells": "TYX"})` states
the axes of a mask type whose files do not name them; `read_mask` reads one mask's pixels
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
