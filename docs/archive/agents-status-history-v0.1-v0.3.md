> Historical record, not current. The per-release status log that was `AGENTS.md` §11 up to the
> v0.4.0 cleanup, moved unchanged. The current state is `AGENTS.md` §11; releases are in
> [CHANGELOG.md](../../CHANGELOG.md).

# Status log, October 2026 (v0.1 to v0.3.4 and PP-10 to PP-11b)

- **Done:** Milestones 0–5 (contract, scanner and validator, axis review, histogram engine,
  CSV and reruns, notebook + CLI + user guide), the notebook redesign plus feedback
  round (PRs #6 and #7), OME-Zarr input with optional TIFF -> OME-Zarr conversion
  (PR #5), planes over the memory limit read in blocks (PR #12, plan §12 question 15),
  naming those images before the run (PR #13, question 16), plane coordinates always
  written (PR #14, question 17), OME-NGFF label readers (PR #15, question 18), and
  follow-up P1 of the normalization plan: the dependency on `morphobricks-normalization`
  v0.1.0 with deprecated re-exports, its version in the manifest, Step 0 checking for it,
  and contract §3–§7 moved to the normalization contract (PRs #16 and #17, question 19),
  and `position_id` filled per cross-brick decision D02 (question 20), and follow-ups
  P2 (dataset consistency warnings, question 21) and P3 (experiment design, question 22),
  and P3b (`plane_histograms.csv` schema version 3 with the channels in columns, question
  27).
- **Input support for MorphoRelate milestone M3:** folders named `single` read as a
  one-member level (question 23), a unit per axis in `PhysicalSizes` and physical sizes in
  `inspect_image` (question 24), OME-Zarr physical sizes from the OME-NGFF scale (question
  25), and notebook steps 1 and 2 embeddable with their own words (question 26).
- **Released as v0.1.0** (27 October 2026), with everything above; the normalization brick
  is pinned to its v0.2.0 (question 28).
- **Released as v0.2.0** (27 October 2026, question 33): follow-ups P4 (the image type,
  question 29) and P5 (intensity checks and plots, question 30) of the normalization plan
  (PRs #28 and #29), step 1 embeddable without the experiment design,
  `folder_step(show_design=False)` (question 31, PR #27), and masks: pairing every image
  with its masks, header checks, the manifest's `masks` key, and the mask step with
  `mask_relationships.json`, as library, command line and documentation (question 32,
  PR #26). The normalization brick stays pinned to its v0.2.0.
- **Released as v0.3.0** (28 October 2026, question 35): follow-up P6 of the
  normalization plan (question 34, PR #31): the histogram range in step 3 and the
  `narrow_histogram_range` warning, `plane_histograms.csv` schema version 4, the
  normalization brick pinned to its v0.3.1.
- **v0.3.1** (to be tagged, question 36): `discovery.position_id`, the one function every
  MorphoBricks brick names positions with (D85), also removes `.png`, `.jpg`, `.jpeg`,
  `.bmp`, `.gif` and `.npy` (naming only) and refuses a name that is only an extension
  (PR #33). Outputs unchanged. Released 29 October 2026.
- **v0.3.2** (to be tagged, question 37): the fixes from the data-flow audit of
  29 October 2026 (#35–#39): negative mask labels refused (PR #40); a malformed OME-Zarr
  labels group is `unreadable_mask` (PR #41); differing channel names are an error,
  compared exactly after trimming leading and trailing whitespace, recorded as
  `channel_names_trimmed` (PRs #42, #45); masks get Z/T plane identity and a 2D mask for a
  multi-plane image is refused (PR #43); strict manifest flags, per-file roles and uint64
  labels (PR #44); a folder holding images and subfolders is an error (PR #46); the
  normalization brick pinned to v0.3.2 (PR #47). Released 30 October 2026.
- **v0.3.3** (question 39): the shared rules other bricks call instead of
  copying them (MorphoBricks D86, question 38, contract §16), PRs #49–#55: length units,
  int32 labels, header axes, identity text and case, source checks against a run's
  manifest, masks named as images, pixel sizes within 0.1 %; and the
  `unsupported_pixel_size_unit` warning (PR #56). Released 31 October 2026.
- **v0.3.4** (to be tagged, question 41): core fixes for the other bricks. Declared axes
  (PR #58): a plain stack's positional Y and X are left open, so a plain `QYX` stack
  declared `YXC` is accepted; the named Y and X declared swapped are accepted and read
  transposed (`declared_axes_transposed` reports it); any other disagreement is refused;
  new `named_axes`, `series_axes_from_metadata`, `TiffMetadata.axes_from_metadata`. One
  strict experiment-design reader for every brick (PR #59, question 40):
  `read_confirmed_design`; edits to `experiment_design.csv` with an empty or NA-like
  `repeat_group`, repeat groups differing only in case, or a `confirmed_on` that is not
  `YYYY-MM-DD` are refused, naming the line and column.
- **Notebook masks field (PP-10c):** step 1 takes the masks folder and the mask axes per
  mask type (design v0.4 §2), see plan §10.
- **Notebook masks step (PP-10d):** step 3's **Describe the masks** runs the `masks` step
  (`mask_relationships.json`) with the run's recorded inputs, through `run.mask_step`, which
  the CLI calls too; see plan §10.
- **Named-axis copies (PP-11):** `convert` / `convert_experiment` writes OME-TIFF copies of
  the images with axis errors or unnamed axes with the confirmed roles into a new folder
  (contract §17, plan §10 and question 59).
- **Notebook conversion card (PP-11b):** step 2's **Make named-axis copies (optional)** card,
  under the axis check when it reports these images: the folder for the copies, the
  confirmed roles, the plan (`preview_conversion`), the copies (`convert_experiment`) with a
  progress bar, the lock card, and **Check the copies in step 1**; see plan §10.
- **Next (not started):** the notebook step for the masks (name each mask, confirm or
  correct the relationships) and a file saving those answers for the other bricks
  (question 32); 3D and time-lapse masks later.
