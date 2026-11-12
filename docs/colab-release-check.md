# Colab and JupyterLab release check

The tests run the notebook in a real kernel, with `google.colab` faked, but they cannot show
that a browser draws the widgets and reacts to clicks. So before a release is tagged, the owner
opens the release PR's notebook (its Colab link and its raw download, both pinned to the
`RELEASE_REF` commit) and ticks this list once in a fresh Google Colab runtime and once in
JupyterLab or LabConstrictor. A failure here blocks the tag, even when every test passes.

Use an experiment on Google Drive (Colab) or on disk (JupyterLab) with two conditions, at least
two biological repeats and a masks folder. Write the date, the commit and any failure (as an
issue number) next to each item.

**Before opening it.** `RELEASE_REF` in cell 1 is the release preparation commit, and cell 3's
`PreprocessingNotebook(...)` call is that commit's API (v0.5.1: `PreprocessingNotebook()`). The
release PR moves both together and records the pair in `RELEASE_CALLS` of
`tests/test_notebook_file.py`, which fails for a `RELEASE_REF` without one.

## A. The three cells (Colab)

1. - [ ] The code of the three cells is hidden: each shows its title (a Colab form).
2. Fresh runtime, run **cell 3** first.
   - [ ] One line: "… run cell 1 (Install MorphoBricks preprocessing) first …", no traceback;
     nothing is installed.
3. Run **cell 1**.
   - [ ] It prints **Ready** with the preprocessing (0.5.1), normalization (0.4.0) and
     ipywidgets versions and the pinned source, then names cells 2 and 3. No restart or
     ipywidgets upgrade is asked for; Drive is not mounted.
   - [ ] Run it again: nothing is installed again, the same versions.
4. Run **cell 3** before cell 2.
   - [ ] One line, "Google Drive is not mounted: only files on this Colab runtime are visible,
     and they are deleted when the runtime stops.", then the app opens.
5. Run **cell 2**, allow access.
   - [ ] "Google Drive is connected: your files are in /content/drive/MyDrive."
6. Run **cell 3** again.
   - [ ] No Drive line; the app renders inline (not the Code snippets pane): the one-line
     welcome, the strip of seven steps and the first step.

## B. The seven steps (Colab, the experiment on Drive)

7. - [ ] **Images:** paste the Drive paths (experiment, masks, output); **Browse…** says it
   cannot open here and offers **Copy path**. **Scan the folders** shows the folders and an
   example picture with a progress line; **Looks right: read all image headers** shows its
   progress (done of total, time left) and the result. The image type (separate channels or
   colour RGB) is shown from the headers, display only: "nothing is recorded here".
8. - [ ] **Axes:** one row per dimension with its role, the example redrawn when a role changes,
   the pixel size in one line; no image type. The roles are confirmed only by the click. The
   instant **Confirm these axes** (or **Confirm axis roles**) is clicked, the button is greyed
   out and says **Working…**, and a progress bar under it counts the parts done (n of 5, what
   it does now); both clear when the roles are confirmed.
9. - [ ] **Channels:** first the image type in one line, pre-filled only when every header
   agrees and recorded only by **Use for all folders** (or each folder under **Set per
   folder…**; folders of different types are refused, `R_HETEROGENEOUS_LAYOUT_NOT_ENABLED`);
   then name the stains (none for colour RGB: its channels are R, G and B) and tag each
   folder's channels.
10. - [ ] **Masks:** **Check the masks** runs with progress; each mask type can be described.
    **Describe the masks** is greyed out, with its reason, until a run exists.
11. - [ ] **Design:** the grid of conditions by biological repeats; **Looks right: confirm the
    design**.
12. - [ ] **Check and save:** the experiment in words; **Yes, save the experiment description**
    writes `morphobricks.toml` in the output folder.
13. - [ ] **Run:** **Compute histograms** finishes with progress; the intensity checks,
    **Compare groups** and the report are collapsed panels and open. The output folder holds
    `plane_histograms.csv`, `run_manifest.json`, `experiment_design.csv` and the report, with
    no "(1)" copies, no `.publish`, `.staging` or `.tmp` leftovers and no lock file.
14. - [ ] Back in **Masks**, **Describe the masks** is enabled and writes
    `mask_relationships.json`.
15. - [ ] Throughout: one column, the page scrolls as a whole (no box with its own scroll bar,
    no horizontal page scroll bar); every greyed-out button says beside it what it waits for;
    no flood of debug messages under the cell.

## C. Again later (Colab)

16. - [ ] Disconnect and delete the runtime, open a new one, run cells 1-3 on the same Drive
    folders: the steps open answered, and **Compute histograms** reuses every row.
17. - [ ] Optional: stop a run part-way. The next check names the lock; **Remove the lock of a
    stopped run** clears it, and the next run resumes.

## D. JupyterLab or LabConstrictor

18. - [ ] Download the raw notebook; run cell 3 first (the one line naming cell 1), then cells
    1, 2 and 3. Cell 2 prints only "Not Google Colab: there is no Google Drive to mount here,
    so nothing was done."; cell 3 prints no Drive line.
19. - [ ] Repeat B (7-15) with local paths; **Browse…** opens a folder chooser on a local
    kernel with a display (otherwise it says to paste the path).
20. - [ ] On a Python 3.10 kernel, if one is at hand: cell 1 says in one line to select Python
    3.11 or newer.

## After the check

Write the result (date, commit, items passed, failures with their issue numbers) in the release
PR. Then tag.
