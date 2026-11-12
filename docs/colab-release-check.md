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
`PreprocessingNotebook(...)` call is that commit's API (v0.5.2: `PreprocessingNotebook()`). The
release PR moves `RELEASE_REF` and records the pair in `RELEASE_CALLS` of
`tests/test_notebook_file.py`, which fails for a `RELEASE_REF` without one.

## A. The three cells (Colab)

1. - [ ] The code of the three cells is hidden: each shows its title (a Colab form).
2. Fresh runtime, run **cell 3** first.
   - [ ] One line: "… run cell 1 (Install MorphoBricks preprocessing) first …", no traceback;
     nothing is installed.
3. Run **cell 1**.
   - [ ] It prints **Ready** with the preprocessing (0.5.2), normalization (0.5.0) and
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
      recorded only by **Use for all folders** (or each folder under **Set per folder…**, whose
      pictures appear once it is opened; folders of different types are refused,
      `R_HETEROGENEOUS_LAYOUT_NOT_ENABLED`).
    - [ ] Then name the stains, then tag each folder's channels; the extra-stain folder gets
      its own list. The viewer shows each channel with its header name and its folder's tag.
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
