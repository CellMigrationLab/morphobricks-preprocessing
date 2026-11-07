> Historical record, not current. Milestones PP-0 to PP-13 of v0.1 to v0.4, moved unchanged from
> [docs/plan.md](../plan.md) §10. The current plan and decision ledger are in `docs/plan.md`.

# Milestones and architecture notes (v0.1 to v0.4)

The core is importable and independent of widgets: one module per job (folder discovery,
TIFF metadata, axes, histograms, CSV I/O, manifest, validation, pooling, normalization,
API, CLI). The notebook and CLI only call the API.

- **Milestone 0 — Contract:** folder rules, axis rules, histogram semantics, CSV schema,
  validation severities ([contract.md](../contract.md)). *Done.*
- **Milestone 1 — Scanner/validator:** folder tree, TIFF headers, inferred hierarchy,
  dtype, axes, actionable validation results. *Folder discovery, header inspection and
  dtype/dimension checks implemented; axis-role checks follow with the axis review.*
- **Milestone 2 — Axis review UI:** metadata-prefilled selectors, plane preview, confirmed
  mapping saved to the manifest. *Implemented: axis mapping, prefill and validation
  (`axes`), plane-by-plane reading with a memory limit and the preview plane (`planes`),
  and the axis review step of the notebook.*
  *Review fix: confirmed singleton axes clear the corresponding readiness error while
  folder, exclusion and other metadata errors continue to block the run.*
- **Milestone 3 — Histogram engine:** per-plane histograms, extrema, non-finite and
  constant planes, pooling, normalization library. *Core implemented and tested.*
- **Milestone 4 — CSV and reruns:** write/update the CSV and manifest, detect
  new/changed/deleted planes, force rebuild. *Implemented: run manifest (`manifest`),
  preflight and incremental runs with exclusions (`run`).*
  *Review fix: stage both files, preserve earlier output on write/publication failures,
  and keep backup files with a recovery message if restoration fails.*
  *v0.4.0 (PP-7, question 48): `restart`/`full` modes replace `force_rebuild`; later runs
  reuse every recorded input; an `O_EXCL` lock guards the output folder; every run writes
  `discovery_report.json` and `.txt` (contract §9.2).*
  *v0.4.0 (PP-8, question 49): stated calibration in the output folder's `calibration.csv`
  (experiment, folder or image; a size or `pixels`), recorded per axis with its source;
  the pixel-size checks run on the resolved sizes; CLI `calibrate` (contract §9.3).*
  *v0.4.0 (PP-8b, questions 50 and 51): a mask whose header names X before Y is refused by
  the run (`mask_layout`); `RunInputs.open` refuses an experiment holding images or masks
  the run did not record (`UnrecordedFilesError`, contract §9.1).*
- **Milestone 5 — Integration and docs:** public API, notebook, CLI, user documentation.
  *Implemented: summaries (`report`), notebook widgets (`notebook_ui`,
  `notebooks/MorphoBricks_Preprocessing.ipynb`), `check`/`run` command line (`cli`) and the
  [user guide](../user_guide.md).*
  *Step-4 feedback: show an animated spinner through table loading, statistics and plotting;
  disable the calculation button while busy and restore it on success or failure.*
  *Setup feedback: standalone Step 0 detects the environment, prefers a nearby checkout,
  reuses an installed package, or installs missing requirements into the active kernel.*
  *Step-1 feedback: centre the folder-label table's headers and contents consistently.*
  *v0.4.0 (PP-8d, question 58): the notebook runs on Google Colab and in JupyterLab following
  LabConstrictor's portability guide; its GitHub installation is pinned to a release tag.*
  *Notebook guidance feedback: separate "What next?" boxes show the next action in larger
  text, following the current confirmations. Step-1 message details are collapsed with
  persistent orange warning and red error counts. Step-3 conversion settings use an
  expandable panel with separate storage, compression and preview-resolution cards.*
- **OME-Zarr (optional `zarr` extra):** OME-Zarr images (OME-NGFF 0.4 and 0.5) are read
  as input next to TIFF files, plane by plane from level 0, with their declared axes
  (`zarr_input`). *TIFF to OME-Zarr conversion, described next, was removed in v0.4.0
  (question 46); TIFF images are read directly.* For TIFF images the user chose once per experiment: read them
  directly, or convert each to `<output>/ome_zarr/<relative path>.ome.zarr` (OME-NGFF 0.4)
  and compute its histograms from the copy, like any OME-Zarr image (`zarr_export`). The
  copies keep the source values at level 0; smaller levels (2 x 2 means) are for viewers.
  They follow the incremental rules of §7 and are published with the table and manifest,
  so a failed run leaves the earlier copies in place. In the notebook the choice and the
  conversion settings (chunk size, compression, pyramid levels) are in step 3's
  "(Optional) Image conversion" card. *Implemented, see questions 12 and 13.*
- **Large planes in blocks:** planes over the memory limit are read in blocks of their
  stored strips, tiles or chunks, sized by the memory limit (`planes`), and their
  histograms are computed in two passes over the blocks with results identical to whole
  planes (`histogram.compute_block_histogram`). Up to v0.3, OME-Zarr copies of such planes were written
  block by block, with their smaller levels built from the stored level above
  (`zarr_export`). The axis preview shows them reduced. Step 3's "What will happen" box
  names these images before the run. *Implemented, see questions 15 and 16.*
- **Normalization brick (follow-up P1):** the histogram, bin-count, record, CSV, pooling
  and normalization modules come from `morphobricks-normalization` v0.1.0, a required
  dependency; the old names stay importable here with a `DeprecationWarning` for one
  version. Outputs are unchanged. *Implemented, see question 19.*
- **Position identity (cross-brick decision D02):** every row and every manifest file
  entry carries the image's `position_id`, so `condition`, `biological_repeat`,
  `technical_repeat` and `position_id` identify a position in every brick; images in one
  leaf folder with the same identifier are refused. *Implemented, see question 20.*
- **Dataset consistency (follow-up P2 of the normalization plan):** the folder check counts
  images per condition and biological repeat, warns about unbalanced counts and repeats
  missing from some conditions (`dataset`), and compares the headers' plane counts and
  physical pixel sizes (`metadata`, `physical_sizes`); the notebook shows them, with the
  earlier consistency issues, in step 1's "Dataset consistency" panel. *Implemented, see
  question 21.*
- **Experiment design (follow-up P3):** after the labels, step 1 pairs biological repeats
  into repeat groups across conditions, asks for each group whether it is comparable, and
  records control conditions, in `experiment_design.csv` (`design`); reruns with the same
  folders skip the questions, the command line uses the file or writes a template, and
  the manifest records its hash. *Implemented, see question 22.*
- **Input support for MorphoRelate milestone M3 (released in v0.1.0):** a folder named `single`
  read as a one-member level, physical sizes with a unit per axis in the image header
  (TIFF and OME-Zarr, from the OME-NGFF scale), and steps 1 and 2 of the notebook
  embeddable by another brick. Outputs are unchanged. *Implemented, see questions 23 to
  26.* In v0.2.0, step 1 also embeds without the experiment design
  (`show_design=False`). *Implemented, see question 31.*
- **Channels across columns (follow-up P3b of the normalization plan):**
  `plane_histograms.csv` schema version 3, written with the normalization brick's
  Milestone 2b (now pinned to its v0.2.0, question 28): one row per XYC plane with each channel in its own
  columns; its channels read one at a time (`planes.iter_xyc_planes`); images with
  different numbers of channels are an error before any image is read; earlier tables are
  recomputed on the next run. *Implemented, see question 27.*
- **Image type (follow-up P4 of the normalization plan):** after the axis roles, step 2
  asks whether the images are fluorescence or RGB, suggesting RGB for TIFF images stored as
  RGB colour on the channel axis (`image_type`, `metadata.TiffMetadata.rgb_samples`); RGB
  needs three channels of an integer type. The manifest records the type; the command line
  takes `--image-type` or prints the suggestion. Outputs are otherwise unchanged.
  *Implemented, see question 29.*
- **Intensity checks (follow-up P5 of the normalization plan):** after every successful
  run, and in step 4 for any table, the normalization brick's `image_summaries` and
  `intensity_checks` give the four intensity warnings from the table alone (`intensity`);
  the notebook shows them with adjustable limits and per-channel box plots and
  distributions, the command line prints them, and the manifest records the limits.
  *Implemented, see question 30.*
- **Masks (MorphoFeature decision D78):** every image paired with its masks from a masks
  folder outside the experiment (or the label images of OME-Zarr images), checked from the
  headers (`masks`); an optional mask step reads them and suggests each mask type's kind
  and the relationships between types in `mask_relationships.json` (`mask_scan`); the
  manifest records the masks. Library, command line and documentation; the notebook step
  follows. *Implemented, see question 32.*
- **Release v0.2.0:** P4, P5, step 1 without the experiment design and the masks.
  *Released, see question 33.*
- **Histogram range (follow-up P6 of the normalization plan):** step 3 offers the
  normalization brick's robust histogram range besides each plane's minimum to maximum,
  every plane is binned with its range check, and a `narrow_histogram_range` warning names
  the channels whose central range covers too few bins; `plane_histograms.csv` schema
  version 4. *Implemented, see question 34.*
- **Release v0.3.0:** P6, with the normalization brick's v0.3.1. *Released, see question
  35.*
- **Release v0.3.1:** `position_id` names an image with the other image extensions bricks
  read (MorphoBricks D85, PR #33). *To be tagged, see question 36.*
- **PP-10 (notebook cards):** split into PP-10a (the cards) and PP-10b (the PP-9 debt below),
  two sequential PRs.
  **PP-10a** *(implemented, PR #120)*: the notebook catches up with PP-7, PP-8, PP-8b and
  PP-8c, with the functions the command line uses (contract §9.2, §9.3, §12):
  - **Check folder** writes the report and the design template like `check`
    (`run.check_step.suggested_review`, now shared with the CLI, and `write_check_outputs`),
    and shows the report; when an embedding brick hides the output folder
    (`show_output=False`) the check writes nothing, and without the design card it writes
    the report only.
  - **Pixel sizes** card: each image's sizes with their source; statements per target, axes
    and unit through `calibration_entry`/`update_calibration` under the lock, the file read
    again with `run.preflight.recalibrated` (no new check, answers kept); the calibration
    messages on the card. Z and T are shown as found until the roles are confirmed, then
    fitted to them (`fit_calibration`): "no such axis" for an axis an image lacks.
  - **Experiment design** answers through `apply_design_edit` (the "Undo" through the new
    `DesignEdit.unconfirm`), saved with `run.design_file.write_design` under the lock; a
    refused answer is shown and changes nothing; the template note says nothing is
    confirmed. The "Cannot save the design" warning is logged inside `log.attached()`, so
    it reaches "Technical details".
  - Step 3: a **Mode** choice (restart/full, with what both keep) replaces "Recompute every
    row" (no longer reset by "Use the recommended settings"); "What will happen" lists the
    input changes the run would report (`run.planned.planned_changes`; the run's design
    lookup moved to `run.design_file.design_for_run` for it); after the run the status
    names the mode, the files replaced and the changes, and the report is shown; a stopped
    run says it wrote its report (question 55), a refused one "Nothing was written."
  - The `output_locked` card names the holder (computer, process ID, start time) with a
    **Remove the lock of a stopped run** button (owner, 2 November 2026, PP-8d review): same
    rule as `--remove-stale-lock`, only on that click, never the lock of a process still
    running on the same computer; it explains the Google Drive limit (one session per
    output folder). The "Common problems" code cell is replaced by the button.
  - The preview drawn under roles still being edited is labelled "Preview of the roles
    above, not confirmed yet" (nothing is written); after confirmation "Preview with the
    confirmed axis roles".
  - The PP-8b refusals (`UnrecordedFilesError`, the zarr refusals of `RunInputs.open`) do not
    surface in the notebook, which never opens `RunInputs`; `zarr_not_installed` is a
    folder-check error with its install command, shown with the other messages.
  - Touched functions end within §9: `_build_run_step` and `run` are split, and the plan
    moved to `notebook_run_plan`.

  **PP-10b** *(implemented, the PR after #120)*: the debt PP-9 left (a move-only split):
  - UI modules no longer compute (§9 layers; audit finding D6):
    `notebook_plots.distribution_png` calls the owner `intensity.cumulative_share` instead of
    its copy; `notebook_render.preview_caption` takes the value range from
    `notebook_values.value_range` and `plane_summary_html` the plane counts from
    `notebook_values.plane_counts` (pure); the plan's layouts come from
    `run.planned.plane_layouts` and `memory_plan` (orchestration), the UI keeping only the
    cache and the words (`_plan_layouts`/`_memory_plan` moved to `notebook_run_plan` first,
    move only).
  - Every notebook function is within §9: `_build_folder_step` (140 executable lines),
    `IntensityPanel.__init__` (110), `_build_results_step` (92), `_refresh_axis_step` (90)
    and `_build_axis_step` (82) are split into builders of one card each (PP-10a did
    `_build_run_step` and `run`), and every private notebook function over 10 lines has a
    docstring: the baseline holds no notebook entry any more.
  - `PYRAMID_OPTIONS`, public but unused since `convert_tiffs` was removed (v0.4.0, PP-2),
    is removed from `notebook_options` and `notebook_ui.__all__`; migration-guide note for
    PP-13: nothing replaces it.
- **PP-10c (the notebook's masks field)** *(implemented, PR #122; owner decision of 2 November
  2026: before PP-11)*: design v0.4 §2 "the notebook has a masks field, with mask-axis
  statements per mask type" (contract §9.2 "Notebook masks", §15):
  - Step 1 has an optional **Masks folder** (Browse… as the experiment field, with the Colab
    "Copy path" message); **Check folder** pairs it with `preflight(masks_folder=,
    mask_axes=)`, as `check --masks`. The masks folder and statements are those the run
    would use (`run.planned.planned_masks`: given, else recorded); the **Masks** card shows
    each type (`mask_review.mask_type_rows`), the images without one and the masks' issues
    (by the confirmed roles once there are any: `mask_checks.mask_issues`, as `run`).
  - **Mask axes** per type, asked only where needed (`mask_review.mask_axes_questions`:
    unnamed axes or a statement that does not fit; 2-D masks of multi-plane images once the
    roles are confirmed, answered `YX`), offering only the statements `mask_roles` accepts
    for every mask of the type; nothing preselected, nothing applied until **Check the
    masks** (`run.preflight.remasked`, other answers kept). `MaskFile.named_axes` is new,
    and a mask whose roles are refused keeps its header axes and shape, so the choices can
    be worked out.
  - The run is given the same masks folder and mask axes; **What will happen** names them
    and the changes (`planned_changes` stays equal to the run's `input_changes`, tested);
    **Drop the recorded masks** is `--no-masks`. Step 3 is gated on masks without errors and
    a pairing matching the choices.
  - Fix found on the way: `--no-masks` (`masks_folder=False`) after a run with stated mask
    axes was refused with `invalid_mask_axes`; it now drops the statements recorded for the
    masks folder's masks (label-image statements are kept).
  - Embedding: `folder_step(show_output=False)` shows neither card and pairs no masks
    folder (MorphoRelate has its own masks card); the embedding API is unchanged.
  - Not in this PR: a notebook button for the `masks` step (`mask_relationships.json`); it is
    PP-10d.
- **PP-10d (the notebook's masks-step button)** *(implemented, PR #123; owner decision of 2
  November 2026: right after PP-10c, before PP-11)*: a notebook button for the `masks` step
  (describe the masks: `mask_relationships.json`; contract §9.2 "Notebook mask step", §15):
  - Move only, first: the CLI's pairing with the recorded inputs, scan and write under the
    lock leave `cli_masks.masks_command` for `run.mask_step` (orchestration):
    `prepare_mask_step` (a `MaskStep`) and `describe_masks` (a `MaskStepResult`, the lock
    refusal returned rather than raised). Review (coordinator decision): the lock is held
    from before the first mask is read to the write, so a locked folder is refused before
    any mask is read, in the command and the notebook. The command only prints; its output is unchanged
    (same text and exit status over 13 scenarios, the HPMEC data included).
  - Step 3's **Describe the masks (optional)** card (`notebook_mask_step`, ui): enabled when
    step 1's output folder holds a v2 manifest that recorded masks (`recorded_masks`), after
    a run in this session or for an earlier run in a reopened notebook (review, coordinator
    decision); the help line names the run it describes; runs the step with the run's
    recorded inputs and the default thresholds (the notebook offers no threshold option;
    the command line does), with a progress bar; shows the mask types, the relationships
    with their counts, masks of the same objects and the hierarchy as suggestions to check,
    and the path of `mask_relationships.json`. Refusals use the existing cards (the lock
    card with **Remove the lock of a stopped run**, red cards for mask errors and masks that
    cannot be used). Hidden with `folder_step(show_output=False)`; the pinned embedding API
    is unchanged.
  - The masks' texts and the new table rows moved from `report` to `mask_text` (pure), so
    `report.py` stays within §9's 600 lines.
- **PP-11 (named-axis OME-TIFF copies, the exception path)** *(implemented, this PR; design
  v0.4 §5; contract §17; question 59)*:
  - `convert_experiment` (`conversion`, orchestration) and the CLI `convert EXPERIMENT --to
    FOLDER --axes ROLES`: images with an axis error or unnamed axes are written as
    named-axis OME-TIFF with the confirmed roles (`copy_axes`, pure; `ome_tiff_copy`, io:
    planes from `iter_planes`, in blocks over the memory limit, each block written in
    place; BigTIFF when large); every other image copied byte for byte; masks never
    converted. Any error other than the axis errors stops it before anything is written.
  - Pixel sizes follow the roles; a relabelled axis gets an unknown scale, except that a Y
    or X relabelled as the other keeps the header's size when X and Y are exactly equal
    (owner, question 59 (1)); never exchanged; channel names kept when C keeps its role.
  - Safe: the copy tree's lock; the planes checked against the memory limit and the free
    space before anything is written; `.partial` files moved into place together once all
    are written (any failure or interruption leaves the folder as it was); unchanged copies
    (source, roles, axes and pixel sizes as recorded) reused, anything else refused
    (`copy_differs`, `copy_folder_not_empty`); the conversion record (`conversion_record`,
    `.morphobricks-conversion.json`) written last, and kept as it was when every copy is
    reused. The messages are in `conversion_issues` (pure).
  - The run on the copy tree: `conversion_record_sha256` in the manifest, `conversion` in
    the report, the warnings `conversion_source_changed` and `copy_changed_after_conversion`
    (`review_conversion`, from `preflight`), every row recomputed (the read path changes);
    `RunInputs.open` refuses a record changed or gone since the run (question 59 (14)).
  - The axis messages (`axes_yx_swapped`, `axes_contradict_header`, `axes_x_before_y`,
    `axes_not_named`) and the notebook's axis marks name the `convert` command.
  - The notebook card for the conversion followed in PP-11b.
  - Review (2 November 2026): the obsolete issues of `convert_tiffs`/`zarr_export` had
    acceptance criteria kept here: a memory budget that fails fast (#76), strict settings
    (#80), no 64-bit precision loss (refused, #75), the copy's fingerprint and scales
    checked before reuse (#87, #88, #92), no mixing of metadata epochs (#96); the run reads
    the copies themselves, so the table describes the persisted pixels (#79).
- **PP-11b (notebook card for the conversion)** *(implemented, this PR; owner decision of
  2 November 2026, plan §12 question 59 (15))*: design v0.4 §5 "the notebook offers
  conversion only for files with errors" (contract §17 "Notebook"):
  - Step 2's **Make named-axis copies (optional)** card (`notebook_convert`, ui), right
    under the axis check, shown when it reports `axes_yx_swapped`, `axes_contradict_header`,
    `axes_x_before_y` or `axes_not_named` for the roles being edited; hidden with
    `folder_step(show_output=False)`; the pinned embedding API is unchanged.
  - The roles: those confirmed with **Confirm axis roles**, or, while the axis errors keep
    that button disabled, the roles shown once the preview is checked and the card's
    **Yes, write the copies with these roles** is pressed; changing a role or the
    missing-axes answer takes it back. Nothing is taken from the headers.
  - A required folder for the copies (Browse…, the Colab **Copy path** card); the plan from
    `preview_conversion` (in `conversion_plan`, beside the command's `plan_conversion`
    (PP-11c), with the folder rule `copy_folder_issues` moved there from `conversion`,
    plus the space needed and free; writes nothing): images converted (with their code) and
    copied byte for byte, the space; errors keep the button disabled.
  - **Make named-axis copies** calls `convert_experiment` with a progress bar; refusals on
    the existing cards (the lock card, now naming the folder for the copies, with **Remove
    the lock of a stopped run**).
  - After the copies: the record's path and **Check the copies in step 1** (the experiment
    folder becomes the copy tree, the output folder is kept and filled in, and the folder is
    checked again); the card says the missing-axes answer must be given again and lists the
    copies whose relabelled Y/X sizes are unknown (`ConversionResult.unknown_sizes`, new), to
    state under **Pixel sizes**.
- **PP-11c (conversion integrity)** *(implemented, PR #126; issues #80, #87, #88, #92,
  #96; question 59 (16)-(18))*: `conversion.py` split into `conversion_plan`,
  `conversion_reuse` and `conversion_write` (move only); the copies' SHA-256 in the conversion record (version 2,
  `content_hash`) verified at reuse, by `review_conversion` and by `RunInputs.open`
  (`conversion_check`); channel names, significant bits and the unknown sizes' reasons
  compared at reuse; each header bound to its image's fingerprint
  (`preflight(bind_headers=True)`, `source_changed_during_check`); the copy's pixel type
  from that header; `parse_size` exact.
- **Before the audit: experiment design identity and counts (issues #82, #83 and #98)**
  *(this PR; questions 61, 62 and 63)*: technical-repeat folders whose images are all
  excluded are not counted, and the balance text names them; every design reader refuses a
  header naming a column twice or a column without a name, and `RunInputs.design()` reads
  with `read_confirmed_design`; changing the notebook's output folder reloads the design
  from the new folder and asks again.
- **Before the audit: links and the log (issues #81 and #99)** *(done; questions 64 and
  65)*: `links.LinkGuard` follows a link only inside the folder walked (discovery, OME-Zarr
  stores, the masks folder; `RunInputs` and `convert` refuse the same links), and the CLI
  checks `--log` (`log_target`) before opening it, after the other arguments.
- **Before the audit: micrometre conversion and exit status (issue #93, #130 review)**
  *(this PR; question 66)*: every conversion to micrometres is a positive finite length
  (`length_units.to_micrometers`, moved from `physical_sizes` with the unit table, which
  re-exports them); a header size that overflows or underflows is unknown and named by
  `invalid_physical_calibration`, a stated one is refused, and an infinite size is never
  the same as another; `check` and `design` exit with status 1 whenever they print an
  error.
- **PP-12 (staging and resume)** *(implemented, PR #132; design v0.4 §1, §8; issues #86,
  #90; question 69; contract §9.4)*:
  - Move only, first: `source_fingerprint` and `LABELS_GROUP` from `manifest` to
    `inventory` (io; `manifest` re-exports `source_fingerprint`).
  - #86: an OME-Zarr image's fingerprint adds the SHA-256 of its inventory (each file's
    relative path, size and `mtime_ns`, sorted by path; still metadata only):
    `Fingerprint.inventory`, `FileEntry.inventory_sha256` (OME-Zarr only), kept for label
    images and copies too; `check_sources` compares it; a record without it counts as
    changed. A folder inside the image that cannot be listed raises instead of being
    skipped.
  - Staging (`run.staging`) and resume (`run.resume`): each image's rows staged in
    `.staging` as soon as it is read; the next run reuses them only when the staging key
    (row key, pixel type, schema version, the versions of preprocessing and normalization)
    and the fingerprint still match, and reports `resumed` and `staged_discarded` (reason
    per file); a stopped run keeps `.staging`, a published run removes it; `full` reuses
    none.
  - Crash-consistent publication (`run.generation`, owner decision of 2 November 2026,
    question 69): the generation in `.publish` with a publication record, committed by
    the manifest's `os.replace`, then moved in; the lock finishes or discards an
    interrupted publication; `RunInputs` reads by SHA-256 (`published_path`) and again if
    the manifest changed (`read_stable`). The backup-and-restore protocol is gone.
  - #90: headers bound to fingerprints in the run (`bind_headers=True`), the entry records
    that fingerprint, the image is fingerprinted after its pixels are read (before its rows
    are staged) and every image before the commit: `source_changed_during_run` (error).
  - Owner decision: `copy_changed_after_conversion` is an error that stops the run before
    it writes (§17).
  - Not in this PR: a v0.4.0rc3 run on OME-Zarr images must be run again (its entries
    have no inventory: recomputed, and `RunInputs` refuses them).
  - **Follow-ups (review of PR #132):** (a) masks are fingerprinted when the manifest is
    written, as before, not bound to the fingerprint taken with their headers during the
    run (owner and coordinator: a follow-up, not a blocker); (b) a run killed while an
    atomic write's temporary file is open leaves that hidden `.<name>.<random>.tmp` file in
    the output folder (on main too, for the provisional report); the lock could remove
    them.
- **PP-13 (docs and the migration guide)** *(done, this PR; design v0.4 §8)*:
  - [docs/migration-0.3-to-0.4.md](../migration-0.3-to-0.4.md): every breaking change from
    v0.3.4 for users and for bricks (what changed, why, what to do), linked from the README
    and the user guide §7;
  - [CHANGELOG.md](../../CHANGELOG.md) for 0.4.0 (breaking, added, fixed, removed);
  - docs pass: README (masks, `RunInputs`, resume), user guide §7 (upgrading), contract
    (§9.3 before §9.4; the removed declared-axes text dropped), the notebook's step 3 note on
    resuming;
  - [docs/colab-release-check.md](../colab-release-check.md): the Colab hand checks of PRs
    #118, #123 and #127 in one list, for the major release;
  - version 0.4.0rc4 (pyproject and the notebook pin), for the data-flow audit and the bricks'
    migration; the final 0.4.0 bump comes after the audit.
  - **Follow-ups recorded for the audit (coordinator, 2 November 2026; not changed here):**
    (a) `pair_counts` breaks an exact-half tie by the lower parent index, so `inside` and
    `parents_with_N_children` can depend on label numbering when a child lies exactly half in
    each of two parents (the `same_objects` counts do not, question 67) (*done in v0.4.0rc5:
    a tie gives no parent, `ambiguous_parent`, question 74*); (b) masks are not
    bound to a fingerprint during a run: they are fingerprinted when the manifest is written
    (PP-12 follow-up (a)); (c) a run killed while an atomic write's temporary file is open
    leaves a hidden `.<name>.<random>.tmp` in the output folder, for example after the
    provisional report (PP-12 follow-up (b)); (d) `length_units.to_micrometers` refuses NumPy
    scalars other than `float64` (for example `np.float32`, `np.int64`) instead of
    converting them; (e) the notebook's axis card does not show the size and plane-count
    warnings recomputed under the confirmed roles (question 68): they appear only in the
    run's result.
- **Release v0.4.0 (PP-13) checklist**, for every tag of v0.4.0 including release
  candidates, in the release PR before the tag is pushed:
  - set `version` in `pyproject.toml`;
  - set the notebook's pin (question 58): `RELEASE_TAG` in Step 0 to the new tag,
    `current_version` to the tag without "v", the same version in
    `notebooks/notebook_latest_versions.yaml`, and the tag named in the Step 0 Colab callout
    (`tests/test_notebook_file.py` checks that the four agree);
  - add the tag's changes to [CHANGELOG.md](../../CHANGELOG.md), and any breaking change to the
    [migration guide](../migration-0.3-to-0.4.md);
  - run the full gate (ruff check and format, pytest with `.[dev,notebook,zarr]`,
    `tools/check.py --rules`) and wait for CI on every platform;
  - **for the major release v0.4.0 only** (owner, 2 November 2026): after the round-2
    data-flow audit and the matrix run, run the notebook by hand on Google Colab with
    [docs/colab-release-check.md](../colab-release-check.md) (PRs #118, #123 and #127). Release
    candidates are not checked on Colab: until then the notebook is verified headless only
    (the tests run it in a local kernel).
  - v0.4.0rc4 (PP-13): version and pin set; the owner tags it. The data-flow audit and
    MorphoFeature's migration run against it.
  - v0.4.0rc5: version and pin set, the changelog's rc5 section (PRs #139, #140, #142 and
    the rc5 PR: questions 66, 73 and 74, the round-2 audit fixes); the owner tags it.
