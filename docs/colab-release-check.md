# Colab hand check for the major release

**Until this check is done, the notebook is verified headless only.** The tests
(`tests/test_notebook_file.py`, `tests/test_notebook_*.py`) run the whole notebook in a local
kernel and drive its widgets without a browser, and they fake `google.colab`. No release
candidate of v0.4.0 has been run by hand on Google Colab.

The owner runs this check by hand on Google Colab **once, for the major release (v0.4.0)**,
after the round-2 data-flow audit and the matrix run (owner, 2 November 2026). Release
candidates are not checked on Colab. The list collects the hand-check lists of PR #118
(PP-8d, Colab readiness), PR #123 (PP-10d, Describe the masks) and PR #127 (PP-11b, Make
named-axis copies). Items were updated for later changes: PP-10a replaced the stale-lock code
cell with a button, and PP-12 replaced the `.run-*` backup folders with `.publish` and
`.staging`.

Use a dataset on Google Drive with a masks folder, and a second small experiment of
Y/X-swapped OME-TIFFs for section F. Note the tag, the date and any failure next to each item.
Report a failure as an issue before the final tag.

## A. Step 0 on a fresh runtime (PR #118)

1. Open the notebook from the release tag on a fresh runtime and run Step 0.
   - [ ] The Drive authorisation window appears and the mount succeeds.
   - [ ] Step 0 prints "Using release v0.4.0 from GitHub" and installs
     `morphobricks-preprocessing[notebook,zarr]`.
   - [ ] The green ready card shows the expected package version.
   - [ ] No "Restart session" request appears. If one does, restart, run Step 0 again and
     note it.
2. - [ ] Run Steps 1–4. Every widget renders and reacts: buttons, dropdowns, accordions, the
     preview image, sliders, progress bars and matplotlib plots. If anything is blank, try
     without `enable_custom_widget_manager()` and report it.

## B. Step 1 (PR #118)

3. - [ ] **Browse…** shows the "could not be opened … Copy path" message.
   - [ ] A pasted **/content/drive/MyDrive/…** path checks fine.
   - [ ] The default output folder lands on Drive next to the experiment.

## C. Step 3 on the full dataset (PR #118)

4. - [ ] The run finishes.
   - [ ] On Drive, the output folder has `plane_histograms.csv`, `run_manifest.json`,
     `experiment_design.csv` and the report (`discovery_report.txt` and `.json`).
   - [ ] There are no "(1)" duplicate files, and no leftover `.publish`, `.staging` or
     `.<name>.<random>.tmp` files.
   - [ ] There is no lock file (`.morphobricks-preprocessing.lock`) after the run.
5. - [ ] Run Step 3 again in the same session. Everything is unchanged and nothing is read.
6. - [ ] Disconnect the runtime (Runtime › Disconnect and delete runtime) and open a new one.
     Run Steps 0–3 again on the same Drive folders. The rows are reused (unchanged). This
     shows the Drive times are stable across mounts.
7. Optional: stop a run part-way (interrupt, or disconnect the runtime).
   - [ ] The next check names the lock holder (`output_locked`), and **Remove the lock of a
     stopped run** clears it.
   - [ ] The next run resumes: the report lists the images read before the stop as
     `resumed`.
8. - [ ] Optional: an experiment with OME-Zarr images on Drive checks and runs (the `zarr`
     extra is installed).

## D. Describe the masks (PR #123)

9. - [ ] In Steps 1–3 with an experiment and a masks folder on Drive, before **Compute
     histograms**, the card shows a greyed button: "Available when the output folder holds a
     run that recorded masks".
10. - [ ] After the run, the help line says "Describes the run in … written …, of …: N mask
    types from …", and the button is enabled.
11. - [ ] Click it: a spinner, a progress bar per image, then a green bar. The tables are
    labelled as suggestions, and `mask_relationships.json` appears in the output folder.
12. - [ ] Run Step 3 again: the card clears.
13. - [ ] **Reopen** the notebook (Runtime › Restart session) and choose the same folders in
    Step 1 without running. The button is enabled, names the earlier run, and works.
14. - [ ] Move the masks folder on Drive and click: a red card says the masks folder does not
    exist, and nothing is written.
15. - [ ] The tables scroll inside the card, with no horizontal page scrollbar.

## E. Make named-axis copies (PR #127)

16. - [ ] Run Step 0, then Step 1 on an experiment of Y/X-swapped OME-TIFFs on Drive, and
    press **Check folder**.
17. - [ ] In Step 2, swap the Y and X roles. The red axis check appears, with the **Make
    named-axis copies (optional)** card under it. Its button is disabled.
18. - [ ] Press **Show preview** and **Yes, the preview looks right**, then **Yes, write the
    copies with these roles**.
19. - [ ] Press **Browse…**: the amber card names **Copy path**. Paste a Drive folder inside
    the experiment and check that it is refused. Then paste one next to the experiment.
20. - [ ] Check the plan lists and the space, then press **Make named-axis copies**. The bar
    moves, the green card shows the record path, and the pixel-size note lists the copies.
21. - [ ] Press **Check the copies in step 1**. Step 1 shows the copy folder, the same output
    folder and a clean check. The roles are suggested from the copies.
22. - [ ] Confirm the labels, preview, roles and image type, then run Step 3. The report says
    "named-axis copies of …".
23. - [ ] Optional: disconnect the runtime during a conversion, reconnect, and convert into
    the same folder. The lock card appears. Press **Remove the lock of a stopped run**, then
    convert again.

## After the check

- Record the result (tag, date, items passed, failures with their issue numbers) in the
  release PR of v0.4.0.
- Then update this file's first paragraph, the README's "Notebook" section and plan §10
  (release checklist) to say which release was checked on Colab.
