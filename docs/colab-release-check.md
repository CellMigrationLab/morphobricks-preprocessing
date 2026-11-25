# Colab and JupyterLab release check

The tests run the notebook in a real kernel, with `google.colab` faked, but they cannot show
that a browser draws the widgets and reacts to clicks. So before a release is tagged, the owner
opens the release PR's notebook (its Colab link and its raw download, both pinned to a commit
whose notebook installs `RELEASE_REF`) and ticks this list once in a fresh Google Colab runtime
and once in JupyterLab or LabConstrictor. A failure here blocks the tag, even when every test
passes.

Write the date, the commit and any failure (as an issue number) next to each item.

**Data.** Three experiments on Google Drive (Colab) or on disk (JupyterLab):

- **Main:** two conditions, at least two biological repeats, a masks folder (one mask type of
  separate objects and, if at hand, one of grouped labels such as mitochondria by cell), and
  one folder whose images have an extra stain (for example three channels in most folders and
  four in one).
- **Z-stacks:** a small experiment of Z-stacks (a few images are enough).
- **Time-lapse:** a small experiment of time-lapse images (optional; the tests cover it).

**Before opening it.** `RELEASE_REF` in cell 1 is the release preparation commit, and cell 3's
`PreprocessingNotebook(...)` call is that commit's API (v0.8.0: `PreprocessingNotebook()`). The
release PR moves `RELEASE_REF` and records the pair in `RELEASE_CALLS` of
`tests/test_notebook_file.py`, which fails for a `RELEASE_REF` without one.

**The pins move only in the release PR (owner, 24 November 2026).** Pull requests no longer
re-pin a notebook. The release PR moves the `RELEASE_REF` of all three notebooks
(`MorphoBricks_Preprocessing`, `MorphoBricks_Preprocessing_Cellpose` and `MorphoBricks_Cellpose`;
the last is to be retired after the owner's Colab test and is then dropped from the list in
`PINNED_NOTEBOOKS` of `tests/test_notebook_cellpose.py`) with two commits:
commit A, the release preparation (the code), then commit B, which only points `RELEASE_REF`
at A (and records A's call in `RELEASE_CALLS`). Label the release PR `release`: its CI is then
a release run (the full matrix, `MORPHOBRICKS_RELEASE_RUN=1`), where the freshness test
`test_release_only_the_preprocessing_pin_is_a_commit_with_this_code` runs and must pass, and a
missing Git history fails it. Locally, `python tools/check.py --release` runs the same. The CI
workflow can also be run by hand (Actions, CI, *Run workflow*), and pushing the `v*` tag runs it
again on the tagged commit.

## A. The three cells (Colab)

1. - [ ] The code of the three cells is hidden: each shows its title (a Colab form).
2. Fresh runtime, run **cell 3** first.
   - [ ] One line: "… run cell 1 (Install MorphoBricks preprocessing) first …", no traceback;
     nothing is installed.
3. Run **cell 1**.
   - [ ] It prints **Ready** with the preprocessing (0.8.0), normalization (0.8.0) and
     ipywidgets versions and the pinned source, then names cells 2 and 3. No restart or
     ipywidgets upgrade is asked for; Drive is not mounted.
   - [ ] Run it again: nothing is installed again, the same versions.
4. Run **cell 3** before cell 2.
   - [ ] One line, "Google Drive is not mounted: only files on this Colab runtime are visible,
     and they are deleted when the runtime stops.", then the app opens.
5. Run **cell 2**, allow access.
   - [ ] "Google Drive is connected: your files are in /content/drive/MyDrive."
6. Run **cell 3** again.
   - [ ] No Drive line; the app renders inline (not the Code snippets pane) and the output is
     not in a frame with its own scroll bar: the one-line welcome, the strip of seven steps
     (Images → Axes → Channels → Masks → Design → Check and save → Run) and the first step.

## B. The seven steps (Colab, the main experiment on Drive)

7. **The image viewer** (every step that shows images uses the same one).
   - [ ] Dropdowns for condition, biological repeat, technical repeat and image narrow each
     other (a level with one value is not shown); every channel of the plane is side by side
     (at most six, then "6 of N channels shown"); **Larger pictures** works.
   - [ ] The viewer says which roles the image is read with: the header's on Images, the ones
     being edited and then the confirmed ones on Axes, the confirmed ones on Channels and
     *Check and save*.
8. **Images** (step 1).
   - [ ] Paste the Drive paths (experiment, masks, output); **Browse…** says it cannot open
     here and points to the Files panel's **Copy path**. **Also save a copy as OME-Zarr (in the output folder)** is
     unticked; leave it unticked for this pass.
   - [ ] **Scan the folders** shows the folders and one image in the viewer, with **Axes of
     this image** under it: one list per dimension, the roles proposed from that image's
     header (a dimension the header does not name has no role). Changing a role redraws the
     pictures at once; **Show another image** picks another one.
   - [ ] **Looks right: read all image headers** shows its progress (done of total, time
     left), then the result and one line saying whether the proposed axes fit all images.
     Nothing is confirmed yet.
   - [ ] The image type (separate channels or colour RGB) is shown from the headers, display
     only: "nothing is recorded here". It is confirmed on Channels.
9. **Axes** (step 2).
   - [ ] It starts from the roles left at the scan and says that they can be changed; one click
     confirms them when every header agrees, else the tick and **Confirm axis roles**. The
     roles are confirmed only by the click.
   - [ ] The instant **Confirm these axes** (or **Confirm axis roles**, or **Change axis
     roles**) is clicked, the button is greyed out and says **Working…**, and a progress bar
     under it counts the parts done (n of 5, what it does now); both clear when it is done.
   - [ ] Pixel size: two inputs **X** and **Y** with **same for X and Y** (ticked: Y follows X).
     With three or more different header sizes, one line ("Pixel sizes differ slightly: …
     (spread … %) across … images, … combinations"), the sizes in a folded table and the two
     choices (one size for all images, or sizes per imaging unit); nothing is chosen for you.
   - [ ] No image type on this step. The folder with the extra stain is accepted (no
     `different_channel_counts` across folders).
10. **Channels** (step 3).
    - [ ] First the image type in one line, pre-filled only when every header agrees and
      recorded only by **Validate** (formerly *Use for all folders*; or each folder under
      **Set per folder…**, whose pictures appear once it is opened; folders of different types
      are refused, `R_HETEROGENEOUS_LAYOUT_NOT_ENABLED`).
    - [ ] v0.6.1 (#313, Spec RH1): with a folder whose images have three interleaved samples
      under a header that does not say colour, answered colour (RGB), the Image type card shows
      one unticked box for the RGB header override; ticking it records
      `rgb_header_override = true` for those folders, and it is never pre-ticked.
    - [ ] Then name the stains, then tag each folder's channels; the extra-stain folder gets
      its own list. The viewer shows each channel with its header name and its folder's tag.
    - [ ] v0.9.0 (guided step, owner design of 25 November 2026). After the labels, one
      question: **Is the channel order the same for the whole dataset?** No row of dropdowns
      before it is answered, and never one row per folder unless asked. Choosing in a dropdown
      saves nothing (the draft file's time in the Files panel does not change) and nothing
      jumps or lags; only **Submit** saves. Check these on small trees (the acceptance tests
      `tests/test_notebook_channel_step_acceptance.py` click the same):
      1. *Owner's layout*: 3 channels everywhere, a 4th in some biological repeats. The
         question says "3 and 4 channels". **Yes**: two rows, *3 channels* and *4 channels*.
         Tag both, **Submit**: "Saved". Finish and **Check and save** publishes; each folder
         has its own list (3 or 4 labels), no `channel_layout`.
      2. *One count, same order*: **Yes**: one row. Tag it, **Submit**, publish: one
         `channel_layout` and every unit `channels_from = "experiment"`.
      3. *One count, order per repeat*: **No**, then **Same order within each biological
         repeat folder**: one row per repeat folder. Give `rep2` another order, **Submit**, publish:
         each folder of a repeat has that repeat's order.
      4. *One count, every unit*: **No**, **Tag every imaging unit**: one row per folder;
         **Submit**, publish: each folder has its own list.
      5. *Mixed counts, per repeat*: **No**, **Same order within each biological repeat
         folder**: one row per repeat folder and channel count. **Submit**, publish.
      6. *A swapped header*: one folder's header names in another order. **Yes**, tag the row
         in the order of the others, **Submit**: "Saved; 1 imaging unit(s) not tagged", the
         folder listed with "C0: header … is …, the row says …", untagged. **Check and save**
         refuses. **Override this unit**, set its order, **Confirm these tags**: the list is
         empty; publish (that folder `channels_from = "unit"`).
      7. *Colour*: every folder colour: after **Validate**, "Every folder is colour
         (RGB): nothing to tag", no question; publish with R, G, B. Colour and separate
         channels mixed: Axes does not confirm and Channels waits for the axis roles.
      8. *Changing your mind*: pick, change, change back; **Yes** → **No** → **Yes** before
         Submit: the same choices come back, a row per repeat starts from the *Yes* choice of
         its count, no stale row is left, and nothing was saved.
      9. *One image type for all folders* (before the rows; the button is **Validate**): (a) on
         the owner's layout (3 and 4 channels) choose *separate stains or markers, one per
         channel* in the box, click **Validate** once: every folder is answered and the guided
         question appears. (b) With folders holding different numbers of images: the same one
         click. (c) Colour images whose headers differ (one says colour, the others are a
         scanner's three samples without the colour header): nothing is pre-filled, **Set per
         folder…** stays folded, and the line says to choose one type for all folders. Choose
         *separate stains*, **Validate**: every folder is answered and *Another step
         for 1 folder* names the one whose header says colour
         (`R_IMAGE_TYPE_CONTRADICTS_HEADER`); the others go on. Or choose *colour (RGB)*:
         *Another step for 5 folders* names the scanner folders; tick the RGB header
         override and **Validate**: it clears and the description publishes with R, G, B.
      10. *The image type is a form*: open **Set per folder…**, change the box, change one
         folder to colour, back, and again: nothing jumps, the rows are not redrawn, the
         draft file's time does not change, and each folder's note says "Not validated yet".
         **Validate** with a folder left without a type is refused and names it; with every
         folder answered it saves once and the labels and the channel question follow.
      11. *Reopening* (review of #546): save a per-repeat answer (3., without publishing),
         close and run the notebook again on the same output folder, open Channels: the
         question shows **No** and **Same order within each biological repeat folder**, the
         rows show your orders and "Pre-answered from your saved tags"; the draft file's time
         does not change on opening; **Check and save** publishes each repeat's own order (no
         `channel_layout`). The same for a per-unit answer and for *Yes* with 3 and 4 channels.
         With a folder still listed: "Saved per-unit tags are kept; answer the question to
         change them". A row pre-filled from a broader choice says so ("check it before
         Submit"); per unit, a row left blank keeps that folder as it is. After **Validate**
         the button reads **Validated ✓** until a choice changes. A folder whose own images
         name their channels differently is in no row: **Submit** lists it untagged ("its
         images disagree") and records no other folder's tags for it; once its images agree
         and the folder is checked again, **Override this unit** opens with every dropdown
         blank ("no row's tags were recorded for it").
11. **Masks** (step 4).
    - [ ] **Check the masks** runs with progress. *Your masks* offers separate objects,
      regions and *grouped labels (one value per parent, e.g. mitochondria by cell)*, none
      pre-selected. **Describe the masks** is greyed out, with its reason, until a run exists.
12. **Design** (step 5).
    - [ ] The grid of conditions by biological repeats; **Looks right: confirm the design**.
13. **Check and save** (step 6).
    - [ ] The experiment in words, the viewer with the confirmed roles, and the report
      formatted (a summary table, then folded parts). **Yes, save the experiment description**
      writes `morphobricks.toml` in the output folder.
14. **Run** (step 7).
    - [ ] v0.7.0 (#317, #320): **Images read at once** (in Settings) is **Auto**, with a
      **Memory cap (GiB)** at half of the available memory rounded down to 0.1 GiB and a line
      naming the figure (on a runtime with little memory: 1, with the one-line reason). The
      run's result says how many images were read at once.
    - [ ] **Compute histograms** finishes with progress; run messages are short cards with the
      whole message one click away. The intensity checks, **Compare groups** and the report are
      collapsed panels and open.
    - [ ] **Compare groups** on the extra stain asks *Summarise only the planes that have it?*
      (No by default) and calculates only after **Yes**.
    - [ ] The output folder holds `plane_histograms.csv`, `run_manifest.json`,
      `experiment_design.csv` and the report, with no "(1)" copies, no `.publish`, `.staging` or
      `.tmp` leftovers and no lock file.
15. - [ ] Back in **Masks**, **Describe the masks** is enabled and writes
    `mask_relationships.json`.
    - [ ] v0.6.0 (Spec ADR 0005): with a mask declared `binary` whose file holds two positive
      values (or is saved as floats), **Describe the masks** refuses that file with
      `R_MASK_CONTRADICTS_REPRESENTATION` and goes on with the other masks; the file stays
      *not yet checked* and is not in `mask_relationships.json`.
    - [ ] v0.6.0 (#311): on an output folder whose `binary` mask was described by v0.5.6,
      **Convert a mask** (*A binary mask's connected objects*) is refused with
      `A_MASK_UNCHECKED`, saying the check predates the binary value rule and to run the masks
      step again; after **Describe the masks** the same conversion works.
    - [ ] The optional card **Convert a mask** under **Describe the masks**: greyed out, with
      its reason, until a run declares masks and a new mask ID is typed. *A binary mask's
      connected objects* on a checked `binary` mask writes `<new ID>/…/<position>.tif` in the
      masks folder and a `mask_lineage.jsonl` line, shows the files and the next steps (declare
      the new mask, run, describe the masks); a second click writes nothing new ("already
      written before").
    - [ ] v0.7.0 (BM15), optional, with an H&E experiment of RGB units at hand: the card **Make
      an H&E tissue mask** beside **Convert a mask** is greyed out with its reasons (each one
      in order: no unit declared rgb, choose a position, type the new mask ID, and
      scikit-image missing with `pip install "morphobricks-preprocessing[he-mask]"` when it is
      not installed). Unusable positions are listed greyed with their reason. **Preview**
      writes nothing and shows the image with nuclei blue, red cells orange and background
      grey, the kept share and "provisional"; **Write** writes the masks and a
      `mask_lineage.jsonl` line and lists the next steps; a second click is listed as a retry
      that changed nothing (unless the preview said the mask will not be reused on retry).
    - [ ] v0.7.0 (#334), optional, with an image whose planes cannot be decoded: the run stops
      with `unreadable_planes` (at most 20 planes per image in the message), and the report
      panel has a part *Unreadable planes* before *Checks*: one closed fold per image listing
      every failed plane in reading order.
16. - [ ] Throughout: one column, the page scrolls as a whole (no box with its own scroll bar,
    no horizontal page scroll bar); every greyed-out button says beside it what it waits for;
    no flood of debug messages under the cell.

## C. Z-stacks, time-lapse and the OME-Zarr copy (Colab)

17. **Z-stacks** (a new output folder).
    - [ ] Scan and read the headers; the viewer has a **z** slider. On Axes the stacks are
      still refused (`z_stack_not_supported`) and the card **Z-stacks: save the dataset as
      projections** appears: the method list starts on "— choose a method —" (maximum, mean,
      sum; no default) and the slices are all unless changed.
    - [ ] **Show this projection of the image above** shows the projection of the example.
      **Write the projected copies and continue with them** is greyed, with its reason, until
      a method is chosen and the example is checked; then it writes
      `<output>/projected_<method>[_z<start>-<stop>]` with progress and opens the copies as the
      experiment (scanned and checked again; you confirm their axes).
    - [ ] **Keep the 3-D stacks for 3-D-capable tools (fiberfeatures)** is greyed with "3-D
      analysis is not enabled yet". The original stacks are unchanged.
    - [ ] Go on to save and run on the copies. Open the stacks again with the same output
      folder: **Continue with the copies written earlier: …** is offered (nothing is rewritten);
      check another folder of stacks: the method and the slices are asked again.
    - [ ] Optional: with an output folder holding projected copies written by v0.5.1
      (conversion record version 3), they are not offered, and one warning line names the
      folder, "record of version 3" and "choose another output folder".
18. **Time-lapse** (optional).
    - [ ] The viewer has a **t** slider on every step; the steps go through to the run as for
      the main experiment.
19. **OME-Zarr copy** (the main experiment, a new output folder).
    - [ ] Tick **Also save a copy as OME-Zarr (in the output folder)** on Images: nothing is
      written. On Axes, the card shows where the copy goes, and **Write the OME-Zarr copy and
      continue with it** is greyed, with its reason, until the axis roles are confirmed.
    - [ ] The click writes `<output>/ome_zarr` (one `<position>.ome.zarr` per image) with
      progress and opens the copies as the experiment; go on to save and run on them.
    - [ ] With the Z-stacks and the option ticked, the projected copies are written as OME-Zarr
      (`<output>/projected_<method>…_zarr`).

## D. Again later (Colab)

20. - [ ] Disconnect and delete the runtime, open a new one, run cells 1-3 on the same Drive
    folders: the steps open answered, and **Compute histograms** reuses every row.
21. - [ ] Optional: an output folder written by v0.5.1. The run reads every image again and
    replaces the v0.5.1 output (manifest version 4 is not reused). A folder of copies written
    by v0.5.1 opened as the experiment stops with `conversion_record_unreadable` naming version
    3 and the fix (convert the originals again into a new folder).
22. - [ ] Optional: stop a run part-way. The next check names the lock; **Remove the lock of a
    stopped run** clears it, and the next run resumes.

## E. JupyterLab or LabConstrictor

23. - [ ] Download the raw notebook; run cell 3 first (the one line naming cell 1), then cells
    1, 2 and 3. Cell 2 prints only "Not Google Colab: there is no Google Drive to mount here,
    so nothing was done."; cell 3 prints no Drive line.
24. - [ ] Repeat B (7-16) with local paths, and C (17-19) if time allows; **Browse…** opens a
    folder chooser on a local kernel with a display (otherwise it says to paste the path).
25. - [ ] On a Python 3.10 kernel, if one is at hand: cell 1 says in one line to select Python
    3.11 or newer.

## After the check

Write the result (date, commit, items passed, failures with their issue numbers) in the release
PR. Then tag.
