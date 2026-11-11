# AGENTS.md — MorphoBricks preprocessing brick

Guidance for AI coding agents (and humans) working on
[CellMigrationLab/morphobricks-preprocessing](https://github.com/CellMigrationLab/morphobricks-preprocessing).
It summarises the agreed plan, the decisions taken so far, the programming style, and what
was learned while building the notebooks. Read it before changing anything.

The authoritative documents are in `docs/`. This file points to them and adds what they
do not say: how we work, why things are the way they are, and the traps we already hit.

| File | What it fixes |
|---|---|
| `docs/plan.md` | Goals, processing passes, milestones, open questions and their decisions (§12) |
| `docs/contract.md` | Rules other bricks rely on: folders, axes, dtypes, binning, CSV schema, statistics, normalization, validation severities, manifest format. Changing it may need a new `schema_version` |
| `docs/development.md` | Coding conventions |
| `docs/user_guide.md` | Biologist-facing guide: notebook, CLI, outputs, every message code |
| [morphobricks-normalization](https://github.com/CellMigrationLab/morphobricks-normalization) `docs/contract.md` | Pixel types, binning, group statistics and normalization parameters (§1.2) and the `plane_histograms.csv` columns (§6.2); this repo's contract §3–§7 only point there |

---

## 1. What the brick does

MorphoBricks is a modular microscopy pipeline. This repository is its **preprocessing
brick**.

- **Input:** one experiment folder of TIFF images and/or OME-Zarr images (OME-NGFF 0.4/0.5,
  `zarr` extra).
- **Output:** `plane_histograms.csv` (schema version 4): one row per XYC plane (an image at
  one Z and T), with one intensity histogram per channel in its own columns, plus
  `experiment_design.csv` (repeat groups, confirmation, controls) and `run_manifest.json`,
  all in an output folder outside the experiment.
- **Statistics code (from the normalization brick):** the per-plane histogram, the CSV
  reader and writer, pooling (`PooledHistogram`) and normalization parameters (`min_max`,
  `percentile_range`, `z_score`, `robust_z_score`) live in the required dependency
  `morphobricks_normalization` (pinned to its release v0.4.0; first pinned at v0.2.0, plan §12 questions 27 and
  28). This brick runs them over
  the experiment. Later bricks such as patch extraction use them to normalise any group of
  planes without rereading pixels. Import them from `morphobricks_normalization`; the
  deprecated paths through this package were removed in v0.4.0.
- **Never:** modifies source images, writes inside the experiment folder, or applies a
  normalization itself.
- **Optional (`zarr` extra, plan §12 question 12):** OME-Zarr input. (Converting TIFF images
  to OME-Zarr in the output folder, `convert_tiffs`, was removed in v0.4.0.)
- **Large planes (plan §12 question 15):** planes over the memory limit are read in blocks,
  automatically and announced, with identical rows.
- **Out of scope:** spatial corrections (flat-field, BaSiC, CLAHE).

Pipeline:

1. **Pass 1 (preflight):** folder discovery, then TIFF header inspection without reading
   pixels, then a suggested axis mapping.
2. **The user confirms** the labels, the experiment design (repeat groups comparable) and
   the axis roles.
3. **Pass 2:** read one 2D plane at a time (in blocks when it is over the memory limit),
   compute exact extrema and the histogram, write the CSV and the manifest. Reruns only
   read new or changed files.

---

## 2. Repository map (one module per job)

```
src/morphobricks_preprocessing/
  discovery.py      folder walk, leaf folders, labels (condition / biological_repeat / technical_repeat),
                    position_id per image and the duplicate check; FOV_KEY, check_identity_text,
                    check_plane_index, check_identity_case (the shared identity rules, contract §16)
  links.py          LinkGuard: links followed only inside the folder walked (experiment, OME-Zarr
                    stores, masks folder); link_outside_folder, link_loop, linked_twice (#81);
                    mounted_folder (error), hard_linked_file (warning)
  mounts.py         MountCheck: mount points below a folder (/proc/self/mountinfo, st_dev)
  folder_rules.py   which of the experiment, masks, copies and output folders may hold which:
                    experiment_inside_masks, copies_inside_masks, masks_inside_experiment,
                    output_inside_masks (audit B1/I1, plan §12 question 70)
  metadata.py       TIFF header inspection (tifffile, no pixel reads), dtype/dimension/series checks,
                    OME channel names + significant bits, physical sizes (TiffMetadata.physical_sizes);
                    header consistency warnings (width/height, plane counts, pixel sizes);
                    header_axes and named_axes (the axes the metadata names, contract §16)
  physical_sizes.py PhysicalSizes (pixel sizes, z-step, time step, a unit per axis, micrometers()
                    for comparing), re-exports length_units' space_unit/to_micrometers,
                    read_physical_sizes (OME / ImageJ TIFF metadata; also importable
                    ) and ngff_physical_sizes (OME-NGFF scale and axis units)
  tiff_size_values.py one size or unit as metadata write it; TIFF_OPEN_ERRORS and
                    METADATA_READ_ERRORS, the read failures that are an invalid calibration (A-m6)
  length_units.py   space_unit/to_micrometers, SPACE_UNITS (the shared unit rule, contract
                    §16): a conversion is always a positive finite length, else ValueError (#93)
  ngff_scale.py     read_scale: an OME-NGFF coordinateTransformations scale, checked; what
                    is wrong with a malformed one (PhysicalSizes.invalid, issue #94)
  dataset.py        dataset consistency from the folders: biological repeats per condition, balance
                    table, unbalanced_images, repeat_missing_conditions, CONSISTENCY_CODES (P2)
  design.py         the rows of experiment_design.csv (an output only since v0.5.1): DesignTable/
                    DesignRow (read-only), the run's reconcile_design and design_issues against
                    the folders, write_experiment_design, parse_design_bytes (the strict parser
                    of a written file; nothing reads the file to prefill a draft); no reader for other bricks (SWEEP-PP)
  design_derive.py  derive_design/write_derived_design: experiment_design.csv from a published
                    description (Spec experiment-v1 11), written by design.write_experiment_design
  design_values.py  the design file's header check (no column twice or unnamed, #83), booleans,
                    counts, dates and DesignFormatError (pure; re-exported by design.py)
  description_rules.py  the one table of the experiment-description rules (Stage 1, Spec
                    experiment-v1): codes, field tables, forbidden names, enumerations; compared
                    with the vendored text by tests/test_spec_rules_table.py
  description_report.py DescriptionIssue (code, table path, value), DescriptionReport
  description_read.py   the reference reader's bytes -> phases: decode (1), header (2), the
                    driver (read_structure: phases 1-3)
  description_structure.py phase 3: closed fields, forbidden names, exact types, ranges, enums
  description_identity.py phase 4: Label (check_identity_text), Token, reserved, NFC, single,
                    case-collision scopes; description_semantics.py phase 5: references,
                    RGB rules, display names, used_ids, one folder one ID, reference graph;
                    description_warnings.py the three warnings; description_walk.py members/units
                    (read_description_bytes in description_read.py runs phases 1-5 and warnings)
  description.py        the model (Description, Condition, Channel, Repeat, Member, Unit, Mask)
                    built from a reader-accepted document; description_from_bytes -> DescriptionError
  description_write.py  the canonical writer (Spec experiment-v1 9.1) and section digests (9.3)
  physical_check.py the physical R_* checks of a description against the folder and image headers
                    (units found/declared, homogeneity, header evidence, image type, mask types
                    declared); layout_groups (header-named layouts are batch-confirmable, Q3)
  description_reconcile.py reconcile(published, observed): units unchanged/new/removed by exact folder
                    labels, N35 reopening (confirmed_on kept), stated renames (FolderRename),
                    confirm_repeat; description_removal.py candidate (the draft), removal_questions
                    (one grouped question per condition) and reconfirmations; reconcile_observe.py
                    observe_folders (image counts of a folder walk)
  description_transition.py the R_* comparison of a candidate with the published file (4.4a, 4.6, 4.9),
                    Reconfirmation (the user's recorded acceptance of a changed ID)
  description_file.py   read_description(path): symlink refusal (R_FILE_SYMLINK), bytes -> Description
  run/description_file.py publish_description: lock, base SHA-256, strict re-read, transitions,
                    atomic write, append-only morphobricks.records.jsonl (Spec experiment-v1 4.6, 10)
  records_file.py   pure checker of morphobricks.records.jsonl (Spec experiment-v1 4.6): check_records
                    (bytes -> records + E_RECORDS_* issues), encode_record, RECORD_KEYS; read_records
                    in run/description_file.py refuses with R_RECORDS_FILE_INVALID
  draft_ids.py         proposed IDs: the folder label or raw header name only when the reader would
                    accept it as written, else none (never repaired); draft.py the draft
                    (IdSlot proposed/confirmed, open questions, unit_rows with condition_id,
                    repeat_group, technical_repeat_id)
  image_type.py     image type (P4): FLUORESCENCE/RGB, review_image_type (suggestion from
                    TiffMetadata.rgb_samples and the confirmed roles, whether RGB is possible via the
                    brick's check_rgb), image_type_issues (rgb_not_possible)
  intensity.py      intensity checks (P5): check_intensities/check_table (the brick's image_summaries
                    and intensity_checks, IntensityReport), repeat_boxes and repeat_distributions for
                    the notebook's plots, intensity_text for the CLI
  validation.py     ValidationIssue (severity, code, message, paths, action), error()/warning() builders
  axes.py           AxisMapping (role per dimension), prefill, check_mapping, review_axes (one mapping
                    applied to every file, missing axes as singletons only when confirmed)
  axis_review.py    review_axes and check_mapping (moved from axes.py in PP-4)
  inventory.py      source_signature: a file's size and mtime, an OME-Zarr
                    folder's total size, newest mtime and inventory digest (issue #86; PP-12);
                    store_files (the files an OME-Zarr fingerprint covers)
  source_content.py verify_content: a source's content SHA-256 (TIFF bytes; OME-Zarr per-file
                    SHA-256s), with_content, content_changed (RunInputs of a strict run)
  manifest_versions.py manifest versions 1/2/3: a strict run writes 3, which rc4 refuses
  lab_links.py      LabLinks: the lab-tool links (Spec experiment-v1 9.3, PP-2), the fixed keys,
                    repeat_code_issues (confirmed, every described repeat once), the manifest's
                    lab_links entry; provenance only, in no digest or key; lab_links_file.py reads
                    the --lab-links file (read_lab_links)
  versions.py       package_versions (moved from manifest.py, which re-exports it)
  fingerprint.py    Fingerprint (size, mtime, inventory) and RowKey: when a file's rows are reused (D5);
                    confirm_axes/unchanged_confirmation (a confirmation of the files as they are)
  recorded_sizes.py RecordedSize: pixel sizes by role with unit and source (manifest v2):
                    header, stated or pixels (no value)
  calibration.py    stated calibration (PP-8, contract §9.3): CalibrationEntry/Calibration
                    (target `.`, folder or image; most specific applies), resolve_sizes,
                    review_calibration (calibration_target_not_found,
                    calibration_differs_from_header); calibration_io.py reads/writes
                    calibration.csv strictly; run/calibrate.py write_calibration (locked)
  pixel_size_checks.py the §11 pixel-size checks on resolved sizes (moved from metadata.py)
  image_consistency.py dtype_issues (mixed_dtypes) and arrangement_issues (missing_axes,
                    inconsistent_dimensions), moved from metadata.py; unit_consistency.py the same
                    checks per unit of a described run (#239): unit_checked (notebook folder
                    check, inside each unit) and declared_type_issues (the run, per image_type)
  narrow_ranges.py  NarrowRange (moved from manifest.py)
  sources.py        check_sources (moved from manifest.py in PP-4)
  manifest_fields.py a file entry's position_id (required in v2, the name's: A-m5) and roles,
                    checked as written and as read (pure)
  unrecorded.py     unrecorded_files: images and masks the run neither recorded nor excluded, by
                    preprocessing's discovery and pairing; RunInputs.open raises
                    UnrecordedFilesError for them (PP-8b, contract §9.1)
  run_inputs_text.py the texts RunInputs refuses a run with (moved from run_inputs.py in PP-12)
  required_description.py every run needs a published morphobricks.toml (v0.5.1, ADR 0002
                    AR10): is_published, the A_DESCRIPTION_REQUIRED issue and its fix, shared by
                    the run, the mask step, the CLI and RunInputs
  mask_record.py    the manifest's masks entry with fingerprints and roles (manifest v2); the
                    fingerprint is the one bound to the mask's header (issue #160)
  mask_binding.py   masks bound to their fingerprints (issues #160, #165): changed_masks,
                    mask_contents/content_changed_masks (verify_content),
                    mask_changed_issue (mask_changed_during_run, mask_changed_during_scan),
                    scan_fingerprints/check_scanned and MaskChangedError (scan_masks)
  axis_check.py     the axis check (contract §2): header_axis_issue, axis_disagreements,
                    likely_axis_cause, check_header_axes, ConfirmedAxes (a confirmation covers
                    only its files as fingerprinted, plain_stack_not_confirmed); AXIS_ERROR_CODES, AXIS_CHECK_CODES
  preview.py        safe display (Stage 1, PR 13): read_plane(max_side) (reduced, read in blocks under a
                    64 MiB budget, 256 px thumbnail / 1024 px detail), channel_strip (only through
                    the confirmed AxisReview; no channel axis guessed), mask_thumbnail, PreviewCache;
                    preview_render.py display_panel (float32 after the reduction, percentile contrast
                    of the finite values; flat / no-finite / non-finite shown as such), stdlib PNG,
                    strip_png, mask_overlay_png (declared representation only),
                    colour_composite (an RGB image as one colour picture, display only)
  planes.py         plane-by-plane reader (iter_planes, read_plane, read_sample_plane), table rows as
                    XYC planes whose channels are read one at a time (iter_xyc_planes, XycPlane),
                    memory estimate,
                    check_planes (preflight memory / layout errors); planes over the limit read in
                    blocks of TIFF strips/tiles or zarr chunks (Plane.blocks, BlockReader), reduced
                    previews
  _io.py            write_text_atomic (every file the brick writes) and free_space
  manifest.py       RunManifest / RunSettings / FileEntry, JSON read/write, SHA-256 of the CSV,
                    check_sources (images against a run, for other bricks, contract §16)
  run/              the run (a package since PP-3; `morphobricks_preprocessing.run` re-exports
                    its public names): preflight.py (Preflight, preflight, sample_planes),
                    pipeline.py (run_experiment, incremental), steps.py (header checks,
                    rows, outcome), modes.py (restart/full, RunMode with verify_content), recorded.py (inputs reused from
                    the earlier run, InputChange), lock.py (the output folder's O_EXCL lock,
                    remove_stale_lock), output_guard.py (the output folder against the
                    masks folder, given or recorded, before anything is written),
                    check_step.py (write_check_report), mask_step.py
                    (the masks step shared by CLI `masks` and the notebook: prepare_mask_step,
                    describe_masks, recorded_masks; PP-10d), result.py (RunResult),
                    entries.py (manifest entries, new/changed/unchanged by RowKey),
                    reuse.py (which earlier rows are reused), rows.py (a file's rows and their
                    pixel checks), publish.py (write_report), generation.py (the outputs
                    as one generation: .publish, the manifest's replacement as the commit,
                    finish_publication, published_path/read_stable for readers; PP-12),
                    staging.py (each file's rows staged in .staging), resume.py (resume or
                    read each file between fingerprints, source_changed_during_run)
  report.py         plain-language summaries shared by CLI and notebook, group statistics tables
  mask_text.py      the masks' texts (mask_summary, mask_report_summary) and the notebook's
                    tables of the mask step (mask_type_rows, mask_relationship_rows,
                    mask_hierarchy_lines, with the mask_hierarchy_cycle warning, #105);
                    moved from report.py in PP-10d
  discovery_report.py the run report (discovery_report.json and .txt, design v0.4 §4): status,
                    manifest SHA-256, what was found, reused, recomputed and replaced
  cli.py, __main__  `morphobricks-preprocessing check|run|masks|calibrate|describe|convert` (also `python -m morphobricks_preprocessing`);
                    cli_run.py holds `run`'s options and output; cli_calibrate.py `calibrate`;
                    cli_convert.py `convert`; log_target.py where `--log` may write (#99),
                    by write_target.py's write_target_refusal (the shared rule other bricks
                    call for files their users name; contract §8)
  masks/           masks (Q32; a package since PP-5: model, files, header, layout, read,
                    checks, labels): find_masks pairs every image with its masks (masks folder with one
                    folder per type mirroring the experiment, or <image>_<type>.tif in the
                    experiment's folders, mixed; OME-Zarr label images), header-only
                    checks (2D, image YX, integer/bool), MaskInventory (to_manifest), read_mask,
                    read_mask_file (a mask by its path), check_labels (int32 labels, contract §16)
  mask_checks.py    mask checks with the confirmed roles: mask_size_issues (Y/X by role),
                    mask_issues (a run's mask issues; images with axis errors skipped)
  mask_review.py    what the notebook shows about the masks: mask_type_rows, and
                    mask_axes_questions (the types whose axes must be stated, with the
                    statements mask_roles accepts)
  mask_scan.py      the optional mask step (Q32): piece_counts (scipy.ndimage), pair_counts,
                    scan_masks -> MaskReport (kinds, relations, direct-parent hierarchy, evidence;
                    each image's masks fingerprinted before and after they are read, #165),
                    write_mask_report (mask_relationships.json); DEFAULT_THRESHOLDS,
                    mask_thresholds (checked changes)
  mask_pairs.py     pair_counts' pure helpers: best_parents (a tie between parents gives no
                    parent, ambiguous_parent; Q74), one_to_one_matches and match_counts
  copy_axes.py      convert (PP-11, contract §17): the axes, shape, pixel sizes (by role; a
                    relabelled axis gets an unknown scale; equal X and Y kept through a Y/X
                    swap, question 59) and OME metadata of a named-axis copy
  projection.py     Z projections (issue #176), pure: Projection (max/mean/sum, optional slice range),
                    projection_dtype (output type and exactness bound from input type, method and
                    slice count; refused rather than wrapped), PlaneAccumulator (running plane,
                    z ascending)
  projection_layout.py projected_layout: a converted copy's layout without Z, the dropped Z size
  projection_plan.py plan_projection: every refusal before a write, PlannedCopy per image;
                    projection_issues.py its messages; projection_masks.py the 3-D mask scan
  planned_copy.py   PlannedCopy (moved from conversion_plan.py)
  projection_record.py ProjectionEntry: a projected copy's provenance in the conversion record
                    (version 3), read strictly against the dtype table (pure)
  ome_tiff_copy.py  write_projected_copy (a Z projection, page by page); write_named_copy: one image as uncompressed OME-TIFF, planes from iter_planes
                    under the confirmed roles, blocks written in place (BigTIFF when large)
  conversion_record.py the copy tree's .morphobricks-conversion.json (version 3, 2 still read: fingerprints,
                    copy SHA-256, roles, sizes, channel names, bits), review_conversion (the
                    run's conversion_source_changed warning and copy_changed_after_conversion
                    error, PP-12), changed_copies
  content_hash.py   content_sha256: a copy's SHA-256 (file, or OME-Zarr folder without labels)
  conversion_check.py conversion_problem: RunInputs.open's check of a copy tree (record SHA-256,
                    version, each recorded copy's SHA-256, read once at open)
  conversion.py     convert_experiment: the copy tree under its lock, record last;
                    conversion_issues.py its messages (pure)
  conversion_plan.py plan_conversion: headers checked with the confirmed roles, each bound to
                    its image's fingerprint (source_changed_during_check), planes checked
                    against the memory limit first, PlannedCopy per image;
                    copy_folder_issues (the folder rule) and preview_conversion (the plan,
                    space needed and free, for the notebook card; writes nothing, PP-11b)
  conversion_reuse.py compare: unchanged copies reused, anything else there refused
  conversion_write.py write_all: .partial files moved into place together (any failure
                    removes what it wrote)
  zarr_input.py     OME-Zarr input: open_ngff (OME-NGFF 0.4/0.5 metadata -> level-0 array, axis
                    letters and physical sizes from the scale and axis units),
                    ngff_label_names/open_ngff_labels (label images, for later bricks),
                    zarr 2/3 helpers, canonical_dtype; needs the `zarr` extra
  notebook_style.py notebook look and feel: cards, status cards, tiles, tables, ConfirmToggle, Browse dialogs
  notebook_axes.py  the notebook's axis-check card and the marks under the role dropdowns
  notebook_masks.py step 1's masks folder, Masks card and mask-axis statements (PP-10c)
  notebook_mask_step.py step 3's Describe the masks card: the masks step after a run that
                    recorded masks (PP-10d); it names the masks by their IDs from Your masks
  notebook_masks_screen.py the Your masks screen (MasksCard, self-contained) and
                    notebook_mask_question.py one mask type's question (objects or regions, no
                    default; mask ID = folder name when valid; optional made-from channels);
                    mask_answers.py (pure) the mask answers of the draft; mask_evidence.py the
                    evidence beside the question (images with the mask, dtype, values in one
                    image, the mask on that image)
  notebook_described.py the described run (every PreprocessingNotebook; there is no
                    undescribed notebook since v0.5.1): the overrides that retire the labels toggle, the design card's
                    comparable questions and CSV writes, the IDs toggle and the Image type card,
                    and reopen from the published file
  notebook_check_save.py the Check and save screen (sentences, one "same way?" per replicate,
                    Yes, remove, the save click, the folded reopen); notebook_check_view.py its
                    HTML; description_review.py (pure) the sentences and the CSV row diff;
                    description_candidate.py the checks on the exact bytes; description_reopen.py
                    the published file against the folders; published_answers.py (pure) the
                    published file as draft answers, retired IDs, reopened replicates
  notebook_convert.py step 2's Make named-axis copies card under the axis check (PP-11b):
                    preview_conversion, then convert_experiment under the copy folder's lock,
                    then Check the copies in step 1 (same output folder)
  notebook_ui.py    PreprocessingNotebook: the four notebook steps (needs the `notebook` extra);
                    folder_step/axis_step take title, purpose, intro and show_next (and
                    show_output and show_design for step 1) so other bricks can embed them
                    (plan §12 Q26, Q31). Since PP-9 it holds the page methods, __init__ and
                    the shared helpers; the step methods live on mixin classes in the modules
                    below, and it re-exports every public name it had (`__all__`)
  notebook_steps.py         steps(): the shipped notebook's 12 steps, one at a time (PR 17a; pure
                            composition of the cards below); notebook_stepper.py the strip, Back/Next,
                            the overview, the compact page (one screen per step: the cards scroll in
                            their own box) and about()/field_rows(); notebook_step_states.py each
                            step's done/one-line state
  notebook_compact.py       the Images and Run steps arranged for one screen: one Folders card, the
                            collapsed panels, and Compare groups (the former statistics page) in the
                            run step (redesign of 10 November 2026)
  notebook_progress.py      Wait: one line per wait (done of total, item, elapsed, time left once
                            measurable); progress_html, time_left (pure parts)
  notebook_viewer.py        ImageViewer: the one image viewer of the shipped notebook (narrowing
                            dropdowns from image_tree.py, t and z sliders, every channel of one
                            plane, at most 6); image_view.py read_view, the one reader behind it;
                            notebook_viewers.py which roles each step reads an image with
  notebook_scan_axes.py     the quick scan's "Axes of this image": roles proposed from the header,
                            changed right there, the proposal the Axes step starts from
  notebook_projection.py    the Axes step's card for Z-stacks: choose the projection, write the
                            projected copies into the output folder (convert_experiment), open
                            them as the experiment; the greyed 3-D option
  report_view.py            report_html: the check's or run's report as headings and tables (pure)
  notebook_quick_scan.py    the first step's quick scan: the folder walk only, one random image for
                            looking only, then the explicit click that reads every header
  axis_example.py           which planes the axes step's example shows (pure): one tile per channel,
                            a few time points or slices; notebook_plots.example_png draws them
  pixel_size_summary.py     the pixel size in one line, which images differ and where (pure)
  notebook_names.py         the names card's rows (conditions on step 4, replicates on step 5)
  notebook_axes_quick.py    the stepper's one-click Confirm these axes (Q3, PR 17b)
  notebook_row_fold.py      reopen: unchanged, answered rows fold; new or changed rows orange
  notebook_channels_rows.py the tag grid's folder rows: pictures (colour for RGB), Enlarge
  notebook_channel_moves.py the Channels step's questions about saved channels at new positions
                            (#258) and saved header names lost (#281); channel_moves.py (pure)
                            lists them (channel_moves, name_changes) and turns the user's click
                            into the 4.6 re-confirmations (move_confirmations)
  notebook_folder_previews.py Images found: a header picture per folder (header_preview.py,
                            display only, Y/X named, capped reads); notebook_thumb_tile.py the
                            shared thumbnail with its 1:1 crop (preview_crop.py, pure) and Enlarge
  notebook_folder.py        step 1: folder, check, issues, labels (_FolderStep)
  notebook_design.py        step 1: the experiment design card (_DesignCard)
  notebook_axis_step.py     step 2: role dropdowns, axis check, missing-axes card (_AxisStep)
  notebook_axis_preview.py  step 2: preview, confirmation, image type (_AxisPreview)
  notebook_run.py           step 3: settings, what will happen, the run (_RunStep)
  notebook_results.py       step 4: pooled statistics per group and channel (_ResultsStep)
  notebook_plots.py         the PNG plots and IntensityPanel (the intensity checks card)
  run_labels.py             RunLabels: channel and replicate IDs of a described run's plots, from the
                    published description when it is the file the manifest recorded (else C<n>, folder labels)
  notebook_render.py        HTML helpers: issues, balance, labels, role effect, captions, tiles
  notebook_options.py       the steps' choices and fixed settings (ROLE_OPTIONS, ...)
  notebook_values.py        display_range (pure: no widgets, no files)
notebooks/MorphoBricks_Preprocessing.ipynb   thin notebook: every code cell calls notebook_ui only
notebooks/notebook_latest_versions.yaml      the notebook's latest version, for LabConstrictor's version check
tests/              pytest with simulated TIFFs (tests/tiff_tree.py, tests/simulation.py); the
                    histogram science goldens (tests/histogram_goldens.py, tests/data);
                    described test runs of a tree (tests/described_tree.py)
experiments/binning_study/      the study behind the binning decisions (not part of the package)
```

Moved to [morphobricks-normalization](https://github.com/CellMigrationLab/morphobricks-normalization) (P1), imported from
`morphobricks_normalization.<module>` by the code here:

```
  dtypes.py         supported dtypes, default and allowed bin counts
  histogram.py      per-plane histogram (PlaneHistogram, compute_plane_histogram, and
                    compute_block_histogram: two passes over blocks, identical result), memory estimate
  records.py        PlaneRecord (one CSV row: an XYC plane, one histogram per channel),
                    make_plane_id, group_records
  csv_io.py         write/read plane_histograms.csv (atomic write, labels always text)
  pooling.py        PooledHistogram: pooled counts, never mean-of-means
  normalization.py  NormalizationParameters (offset, scale) and the four methods
```

Their tests (`test_histogram.py`, `test_csv_io.py`, `test_pooling.py`,
`test_normalization.py`) moved with them. The deprecated names here were removed in v0.4.0.

The notebook and the CLI contain **no processing logic**. They call the same public
functions the tests use.

---

## 3. How we work

1. **Read first.** Before coding a milestone, read `docs/plan.md`, `docs/contract.md`,
   `docs/development.md` and the modules you will touch.
2. **Ask before deciding.** Open questions in plan §12, and any choice that is genuinely
   the user's (product behaviour, UX, file formats), are asked *before* coding. Offer 2–4
   options with a recommended one first and explain the trade-off in one line each.
3. **Record every decision in the docs** using the established pattern in plan §12:
   `*Decided (October 2026): ...*`. Also update the affected rules in `contract.md`
   (tables in §1, §2, §8, §9) and the milestone status in plan §10.
4. **Local review first (user request, October 2026).** Keep new changes local for the
   user to review. Do not commit, push or open a PR until the user requests it. Once
   requested, use **one milestone or feedback round per branch and PR**, against `main`.
   PR description:
   *Summary* (by module), *Decisions*, *Notes for review* (limitations, what was not
   verified), *Tests* (exact counts, e.g. "455 passed, 2 skipped").
5. **Done means:** `python tools/check.py` passes (`ruff check .`, `ruff format --check .`,
   `pytest` and the code rules of `docs/development.md` against their baseline), (CI runs
   lint on Ubuntu and tests on Ubuntu, Windows and macOS with Python 3.11 and 3.13), docs
   updated, and local changes ready for review. Open a PR only when the user requests it.
6. **Be honest in reports.** Say what was verified and what was not (for example: "widgets
   tested headless; not looked at in a real JupyterLab").
7. **Scope discipline.** Do the task asked; list unrelated problems as follow-ups instead
   of fixing them silently.
8. **Keep generated or local files out of commits.** That includes test images, outputs
   (`*_MorphoBricks_Preprocessing/`), editor/agent config and launch files.

---

## 4. Rules of the brick (summary of the contract)

### Folders and labels (contract §1)

- **Images:** TIFF files and OME-Zarr folders (name ends in `.zarr`). A store is one image,
  never a folder level, and discovery never enters it.
- **Hierarchy:** `<experiment>/<condition>/<biological repeat>/<technical repeat>/<image>.tif`.
- **Leaf folders:** a leaf folder has images and no subfolders. Only TIFFs directly in
  leaf folders are analysed.
- **Missing levels:** a branch may stop early; missing labels are the value `single`. A
  folder *named* `single` (lower case) that is the only folder in its place is that level's
  value `single` (a level with one member, plan §12 question 23); spelled in another case,
  or beside other folders, it is the error `reserved_folder_name`.
- **Uniqueness:** labels are unique only among siblings, compared case-insensitively.
  Names of one level differing only in case are an error anywhere in the experiment
  (`case_conflict`, decision D-e; siblings only before D86). Groups are identified by the
  full label path.
- **Names (D-e):** labels and `position_id` pass `discovery.check_identity_text`: no
  leading/trailing whitespace, not `""`/`NA`/`nan`/`none`/`null`/`n/a`/`<NA>` in any case
  (`invalid_name`). Bricks that read identities from tables apply the case rule with
  `discovery.check_identity_case` (labels run-wide per level, `position_id` per technical repeat).
- **Depth:** a folder below the technical-repeat level is an **error** (so a layer of images
  is never dropped silently).
- **Positions (cross-brick decision D02):** one image is one position. Its `position_id`
  (`discovery.position_id`) is the name without `.tif`/`.tiff`/`.zarr` and a preceding
  `.ome`. The position key is `condition` + `biological_repeat` + `technical_repeat` +
  `position_id`. Two images in one leaf folder with the same identifier, ignoring case,
  are the error `duplicate_position_id`: never guess.
- **System files:** dot-files and dot-folders, `Thumbs.db`, `desktop.ini` and `__MACOSX` are
  ignored **without** a warning.
- **Mixed folders:** images in a folder that also holds subfolders are the error
  `tiff_outside_leaf` (owner decision, October 2026; every brick refuses such a folder).
- **Warnings:** non-TIFF files, empty folders; images named as masks of other images and
  masks-like folders holding them (`image_named_as_mask`, `mask_folder_in_experiment`,
  decision D-i).
- **Paths:** paths in outputs are relative to the experiment root, with `/`.

### Axes (contract §2)

- **Roles:** X, Y, Z, C, T. Exactly one X and one Y; each role at most once; every axis
  longer than 1 needs a role. One confirmed mapping per experiment.
- **2-D planes only (owner, 5 November 2026; plan §12 question 76):** an image with more
  than one depth slice is the error `z_stack_not_supported` (`size_checks.z_stack_issues`),
  from the header's named Z in `check_metadata` and again under the confirmed roles in
  `review_axes` (superseding it); Z = 1 is accepted as it is, nothing is projected or
  squeezed. A mask of a time-lapse has the image's T; the stated shared 2-D mask
  (`--mask-axes TYPE=YX`, `MaskFile.shared`) is removed (`shared_mask_not_supported`).
- **Prefill from tifffile axis labels:**
  - X/Y/Z/C/T keep their role;
  - `S` (RGB samples) becomes C when there is no other C;
  - everything else (`I`, `Q`, …) is left unassigned: **never guess** depth vs time.
- **Axis check (v0.4.0, plan §12 question 45):** every TIFF's confirmed roles must agree
  with the axes its header names (`axes.header_axis_issue`, position by position, on
  `metadata.named_axes`). Otherwise one of three errors naming the files with their header
  and confirmed axes: the errors `axes_yx_swapped` and `axes_contradict_header` (with the
  likely cause; such an image is **never read**, not transposed, not under a contradicted
  declaration; fix: correct the axes, or named-axis copies) and the warning
  `axes_not_named` (a plain stack, read with the confirmed roles, `axes_from:
  "confirmed"`).
  `AxisReview.axis_error_files` lists them; mask sizes and planes are checked by the
  confirmed roles and skip those images (`run.mask_issues`). OME-Zarr images never have
  these errors: their roles are always those their OME-NGFF axes declare (next bullets).
- **Position-like axes** (`R` tile, `M` mosaic, `J` column, `K` row) cannot get a role. If
  they hold more than one plane: `unsupported_axis` error.
- **Missing axes:** a file lacking some of the mapping's axes (e.g. `ZYX` next to `ZCYX`) is
  read with them as length 1 **only** if the user confirmed it (`missing_as_singleton`) and
  exactly one arrangement fits. The coordinate is then 0, not blank.
- **Series and pyramids:** only the first image series, at full-resolution level 0. A file
  with more than one series is an error (`multiple_series`). OME pyramids are one series;
  non-OME SubIFD pyramids appear as two series in tifffile and are therefore blocked.
- **Coordinates:** a row is an XYC plane; its coordinates `z, t` are zero-based and always
  written: 0 when the image has no such axis (question 17). Channels are columns,
  numbered from 0 (question 27). `PlaneRecord` stores None as 0; `Plane` (2D, with
  `z, c, t`) and `XycPlane` coordinates stay None for axes the file lacks.
- **Channel counts (N43, question 27):** every image has the same number of channels (the
  C axis, 1 without one, also for a confirmed missing C), else the error
  `different_channel_counts`: from the headers in `metadata.check_metadata` (C, or S
  without C) and from the confirmed roles in `review_axes`, which supersedes it
  (`axes._SUPERSEDED_METADATA_CODES`). Shared builder `size_checks.channel_count_issues`.
- **OME-Zarr axes:** translated from OME-NGFF metadata (`zarr_input.ngff_axes`: time -> T,
  channel -> C, space z/y/x -> Z/Y/X, anything else -> Q). The experiment mapping is
  defined on the TIFF arrangement if there are TIFFs; every OME-Zarr image, also one with
  the mapping's letters, is matched by role (`axes.ome_zarr_mapping`, `declared_mapping`,
  `file_mapping_for`; issue #72): a misfit, or confirmed roles that contradict the declared
  axes (`ZYX` confirmed `TYX`), is `ome_zarr_axes_mismatch`. Use `file_mapping_for`, never
  `mapping.for_axes`, to get an image's mapping. Missing axes are never guessed (issue
  #73): the version is read first, and OME-NGFF 0.1/0.2 stores are refused whatever they
  contain (re-save as 0.4 or later; owner, 2 November 2026); any other store without axes
  is `unreadable_ome_zarr` too, with no confirmation path.
- **OME-Zarr calibration (issue #94):** a malformed `coordinateTransformations`
  (`ngff_scale.read_scale`) gives unknown sizes with the reason in
  `PhysicalSizes.invalid`, named by the warning `invalid_physical_calibration`; never a
  silent fallback, never a substituted size.

### Pixel types and binning (normalization contract §1.2.1–§1.2.2)

- **Supported dtypes:** int8/16/32/64, uint8/16/32/64, float32, float64. Everything else
  (bool, complex, float16, …) is rejected. One dtype per experiment (`mixed_dtypes` error).
  Source values are never converted.
- **Every plane is binned over its own min–max range**, with the same bin count for all
  planes of an experiment. The binning study showed per-plane ranges cut percentile errors
  5–30× compared with binning over the whole dtype range, and adding images never changes
  existing rows. Optionally (follow-up P6, question 34) histograms that are not exact use
  the brick's robust range instead: bins between the plane's 0.01 % and 99.99 % quantiles,
  the pixels outside counted as underflow and overflow. Every plane is binned with the
  brick's `histogram_with_range_check`; channels whose central range (1st to 99.8th
  percentile) covers fewer than 100 bins give the warning `narrow_histogram_range`.
- **Integer planes:** whole-number bin limits whose widths differ by at most one value.
  Exact when the range fits the bin count.
  - Default bins: 256 for 8-bit and 65,536 for 16-bit (one per value, so exact), 65,536 for
    wider types.
  - Allowed: 1 to min(65,536, 2^bits).
- **Float planes:** equal-width bins over the finite range. NaN and ±inf are ignored and
  counted.
  - Default 10,000 bins, allowed 2 to 65,536.
- **Special planes:**
  - A constant plane puts every pixel in bin 0.
  - A plane with no finite pixels has blank extrema and zero counts; it is a warning
    unless no plane is usable (error).
- **Representative value** of a bin is its midpoint; no pixel is further than half a bin
  from it.

### Statistics and normalization (normalization contract §1.2.3–§1.2.4)

- **Groups pool their planes' (representative, count) pairs. Never average per-plane
  statistics.** In the study, mean-of-means underestimated std by up to 22% and p99.9 by up
  to 36%.
- **Accuracy:** min and max are exact from stored extrema. Percentiles use numpy
  `inverted_cdf` on pooled counts.
- **Normalization:** each method is an offset and a scale: `(pixels − offset) / scale`.
  Percentile range defaults to 1–99.8. Robust z-score uses MAD × 1.4826. A zero or
  non-finite scale is an error.

### CSV (normalization contract §6.2, schema version 4, follow-ups P3b and P6)

- **Rows:** one per XYC plane (image, Z, T), written with the brick's writer from
  `PlaneRecord(path, *labels, histograms=…, z=, t=, position_id=)`.
- **Columns:**
  `position_id, image_file, z, t, condition, biological_repeat, technical_repeat,
  source_relative_path, plane_id, dtype, bit_depth, channel_count, total_pixel_count,
  bin_count, schema_version`, then per channel `i`: `Intensity_MinIntensity_C<i>`,
  `Intensity_MaxIntensity_C<i>`, `Histogram_ValidPixelCount_C<i>`,
  `Histogram_BinningMin_C<i>`, `Histogram_BinningMax_C<i>`, `Histogram_Count_C<i>_00000…`.
- **`plane_id`** = relative path plus `::z=…,t=…`, always both (`img.tif::z=0,t=0` for a
  2D image). It is the row key.
- **Rows** are sorted by path, then z, t.
- **Earlier versions:** the reader refuses schema 1 and 2 (`CsvFormatError`); a run
  recomputes them completely (manifest `schema_version` differs). Each channel's
  histogram equals the reference row's, whole and in blocks (science goldens:
  `tests/test_histogram_goldens.py` with the committed reference in `tests/data`, written by
  `tests/data/make_histogram_goldens.py` with an earlier, independent implementation).
- **Step 4** summarises each channel of a group on its own (issue #104): one
  `GroupSummary` per group and channel (`channel`, `xyc_plane_count`,
  `channel_histogram_count`), a `channel` column in the table and one plot panel per
  channel. Pooling every channel together (as with version 2) only with
  `group_summaries(..., pool_channels=True)`, labelled `all channels (pooled)`. (The
  deprecated alias `GroupSummary.plane_count` was removed in v0.5.1, SWEEP-PP.)
- **`position_id`** is filled in every row (D02, plan §12 question 20). Earlier tables with
  it blank are filled on the next run without reading pixels.
- **Reading:** labels are always read back as text (`NA`, `001`, `TRUE` stay as typed).
  Floats are written with `repr`.

### Run manifest and reruns (contract §9, plan §7)

- **Manifest contents:**
  - settings, the confirmed labels per leaf, and the axis mapping;
  - per-file size, `mtime_ns`, axes, `singleton_roles`, plane count, `position_id` and
    `roles` (the file's own axis roles; optional keys, null when absent; manifest version
    stays 1); flags are JSON booleans only and the versions are strings (`"1"`, `"4"`);
  - channel names and significant bits (OME only, information only);
  - the CSV's SHA-256 and package versions (including `morphobricks-normalization`).
- **A file is unchanged** when its size, mtime (an OME-Zarr image: also its inventory digest,
  `inventory_sha256`, issue #86), labels and axis layout match and the table
  still holds all its rows. Unchanged rows are reused; new and changed files are read;
  deleted, renamed or excluded files lose their rows. The fingerprint is metadata: a
  same-size rewrite with its mtime set back is not seen (the stated blind spot). The opt-in
  `RunMode(verify_content=True)` / `--verify-content` adds each source's content SHA-256 to
  the entry and the row key (`source_content`); a recorded input, and changing it rebuilds.
- **Full rebuild** happens in `mode="full"` (PP-7; `force_rebuild` was removed), when there
  is no readable manifest, when
  the dtype, bin count, histogram settings (`RunSettings.histogram`), `verify_content` or
  axis mapping changed, or when the CSV's hash no longer matches.
  Changing only the memory limit or the exclusions recomputes nothing.
- **Writes (PP-12, contract §9.4):** each image's rows are staged in `.staging` as soon
  as it is read (an interrupted run resumes from the staged rows whose key still matches;
  anything else is discarded and reported). The outputs are one generation, written into
  `.publish` and committed by the manifest's `os.replace` (the one atomic step), then moved
  into place (the output folder is fsynced right after the commit); the next holder of the
  lock finishes or discards an interrupted publication.
  Every image is fingerprinted when its header is read, before and after its pixels are
  read, and before the commit (`source_changed_during_run`, issue #90). Every mask's header
  is read between two fingerprints (`mask_changed_during_check`), that fingerprint is the one
  recorded, and the mask must still have it before the commit (with `verify_content` also its
  content SHA-256; `mask_changed_during_run`, issue #160). Nothing is
  published if any error is found.
- **Exclusions:** the user may exclude images explicitly (`exclude=[...]`, e.g. damaged
  files). Excluded images are not read, are reported (`excluded_images` warning), are
  recorded in the manifest, and lose their rows.

### Memory (plan §5)

- **Unit of reading:** read one TIFF page at a time and yield the planes inside it. Never
  load a full Z/C/T volume unless the file stores it as a single page.
- **Channels of a row (question 27, the owner's choice):** `iter_xyc_planes` reads and bins
  them one at a time (`XycPlane.histograms`), keeping only the finished histograms; the
  last decoded page is kept, so a page holding several channels (RGB) is decoded once.
  When C is a page axis and pages hold several Z/T planes (volumetric), pages are decoded
  again per XYC plane: time, never memory.
- **Estimate per plane, from the header:** decoded page + encoded bytes + plane copy +
  histogram temporaries (`histogram.estimate_histogram_memory`) + the finished bin counts
  of every channel of the XYC plane (`8 × bins × channel_count`). It is deliberately an
  upper estimate, and one whole/blocks decision covers every channel.
- **Limit:** default 1 GiB. A plane over the limit is read in blocks (question 15):
  - Blocks are whole TIFF strips or tiles, or OME-Zarr chunks, in full-width bands first,
    as many as fit the limit (`planes._choose_block`, estimate `planes._block_memory`).
    Uncompressed TIFF pieces (read straight from the file) and zarr chunks may also be read
    a few rows at a time. No block setting: the limit decides.
  - Announced, never silent: warning `planes_read_in_blocks` in `check_planes`, and
    `read_in_blocks` per file in the manifest (information only, not part of
    `same_source`).
  - Histograms: `compute_block_histogram` reads the blocks twice (extrema, then counts);
    8- and 16-bit integers once (a count per possible value). Identical to the whole plane,
    and `compute_plane_histogram` uses the same code with one block.
  - A plane read in blocks is a `Plane` with `pixels=None`; `Plane.blocks()` reads from
    the open file, so use it before asking `iter_planes` for the next plane.
  - Previews (`read_plane(preview=True)`, `read_sample_plane`) are reduced to every n-th
    pixel (`Plane.step`, at most 4,096 px per side).
  - A compressed TIFF storing a plane in one strip/tile cannot be split: still the
    `plane_over_memory_limit` error, like a plane whose smallest block is over the limit.
    The readers raise `PlaneMemoryError` before reading.
  - Wide integers (32/64-bit) reserve up to 512 MiB for the per-value count table in
    `estimate_histogram_memory`, so they need a limit above about 520 MiB even in blocks.

### Dataset consistency (contract §11, follow-up P2)

- **Warnings only**, from folder names and headers; never block.
- **Balance:** images per condition (columns) and biological repeat (rows, matched by
  folder name), technical repeats added up; below half or above twice the median of the
  non-empty cells is `unbalanced_images`. Excluded images are not counted.
- **`repeat_missing_conditions`:** a biological repeat not in every condition (only with
  more than one condition).
- **Pixel sizes:** `different_pixel_sizes` (X by Y; z-step only for images with Z > 1;
  micrometres, the shared 0.1 % rule `same_pixel_size` with single-linkage
  `pixel_size_groups`, D-a), `missing_pixel_size` (known for some only) and
  `unsupported_pixel_size_unit` (a size in a unit outside D-a2: not converted, the image
  uncalibrated on that axis, the image, axis and unit named). Since PP-8 they run on the
  resolved sizes (header or `calibration.csv`, contract §9.3); `preflight` replaces the
  header-based ones in `metadata.issues`.
  OME-Zarr sizes come from the level-0 scale with axis units; no unit means unknown.
- **`different_plane_counts`:** Z, T or any other dimension except X, Y, C and S;
  channels are the error `different_channel_counts` (question 27), also in this panel;
  `different_sizes` is width/height only, so no dimension is reported twice.
- **Notebook:** the consistency codes (`dataset.CONSISTENCY_CODES`) appear only in step 1's
  "Dataset consistency" panel with the balance table; the Messages card keeps the rest.
  The CLI `check` prints the balance table; `run` reports the same warnings.

### Experiment design (contract §12, follow-up P3)

- **Repeat group** = one biological repeat across conditions; same folder name pairs by
  default; one folder per condition per group (`repeat_group_conflict` otherwise).
- **Confirmation** per group (`design_not_confirmed` until then); all-`single` experiments
  get one whole-experiment question. Controls are recorded only.
- **`experiment_design.csv`** (schema 1): one row per condition x biological-repeat folder,
  sorted; counts at confirmation; `confirmed_on` ISO date. Written by the run only. No brick
  reads it back (v0.5.1, SWEEP-PP; Spec ADR 0002 AR11 (3)): the lenient reader,
  `load_design`, the strict `read_confirmed_design`/`ConfirmedDesign`/`DesignCheckError` and
  `RunInputs.design()` are removed; bricks take `repeat_group` from the description
  (`Fov.repeat_group`). `parse_design_bytes` (strict, `DesignFormatError` naming line and
  column, also for a header naming a column twice or not at all, #83) remains to check a
  written file; the draft's legacy migration was removed (Spec ADR 0002 AR10 (9)), and no code
  path reads an `experiment_design.csv` to propose or prefill a draft.
- **The run's check:** `reconcile_design` keeps the derived rows of known folders, reopens only
  groups that gain a folder (`design_not_confirmed`), drops removed folders.
- **v0.5.1: an output only.** Every run is described (`A_DESCRIPTION_REQUIRED` without a
  published `morphobricks.toml`, contract §0); the run derives `experiment_design.csv` from the
  description and writes it with the table (`experiment_design_sha256` in the manifest). The
  notebook keeps the pairing in the draft; there is no `design=`, `--design`, `design`
  command or `check` template any more.

### Image type (contract §13, follow-up P4)

- **Types:** `fluorescence` and `rgb`; RGB needs three channels of an integer type
  (the normalization brick's `check_rgb`), else `rgb_not_possible` in a run.
- **Suggestion:** `rgb` only when every analysed image is a TIFF stored as RGB colour
  (`TiffMetadata.rgb_samples`: photometric RGB, three samples per pixel, axis `S`), the
  confirmed C role is on `S`, and RGB is possible. Never applied without confirmation.
- **Notebook:** step 2's "Image type" card appears once the axis roles are confirmed
  (also when embedded); radio buttons with the suggestion preselected, disabled at
  fluorescence when RGB is impossible; `ConfirmToggle` **Yes, this is the right image
  type**; changing roles or folder resets it; step 3 gated on `confirmed_image_type`.
- **CLI:** `--image-type`, else the suggestion, printed with its reason.
- **Manifest:** optional top-level `image_type` (null without one) and
  `channels_from_rgb_samples`; changing the type recomputes nothing.

### Intensity checks (contract §14, follow-up P5)

- **From the table only:** `run_experiment` checks its rows in memory (`RunResult.intensity`,
  apart from `RunResult.issues`, never blocking); `check_table` reads any table. The four
  codes are the normalization brick's; comparisons stay within a condition.
- **Limits:** `IntensityCheckSettings` (3.0, 0.01, 5), recorded in the manifest
  (`intensity_checks`, optional); changing them recomputes nothing.
- **Notebook:** `IntensityPanel` in step 3 (after a successful run, `run_checks`) and step 4
  (`table_checks`): status, tiles, warnings table with actions, collapsed **Check
  settings**, channel buttons, box plots (`Issue.channel` marks images) and cumulative
  distributions per biological repeat. Nothing written. CLI prints them, no plots.

### Validation messages (contract §8)

Every expected problem is a `ValidationIssue` with:

- a stable snake_case `code` (e.g. `missing_axes`, `unreadable_tiff`), listed in user guide §8;
- a severity: errors block processing, warnings do not;
- a plain-language `message` saying what happened (no jargon, no stack traces);
- `paths`: sorted relative paths of the affected files;
- an `action`: what the user can do.

Technical details go to `logging` (shown in the notebook's "Technical details" panel, or
`--log FILE` in the CLI). Custom exceptions are only for genuine failures.

---

## 5. Decisions log

| Topic | Decision |
|---|---|
| Binning | Per-plane min–max, same bin count per experiment, pooled counts for groups (binning study) |
| Core dependencies | `numpy`, `tifffile`, `scipy` (the mask step's piece counts, Q32) and `morphobricks-normalization` (git dependency pinned to its release tag v0.4.0, whose CSV reader checks each row's stored `plane_id`, `image_file` and `bit_depth` (since v0.3.2), refuses a blank `position_id` and tables of schema versions 1 to 3 (v0.4.0), and whose `PlaneRecord` requires a keyword-only `position_id` and which refuses corrupt or ambiguous statistics and parameters (v0.3.3); `[tool.hatch.metadata] allow-direct-references`). `ipywidgets` and `matplotlib` in the `notebook` extra; `zarr` in the `zarr` extra; `pytest` and `ruff` in `dev` |
| OME-Zarr (Q12) | Input may be TIFF and/or OME-Zarr (folders ending in `.zarr`), mixed. OME-Zarr axes come from their metadata and are matched by role; level 0 of a single image only, several images per store is `multiple_series`. *Removed in v0.4.0 (plan Q46): TIFF images are always read directly; the following describes v0.3.* TIFF choice once per experiment (`convert_tiffs`): read directly (default) or convert to `<output>/ome_zarr` (OME-NGFF 0.4) and compute rows from the copies; switching recomputes TIFF rows and removes unused copies. Reads OME-NGFF 0.4 and 0.5 (0.5 needs zarr 3). In the notebook: its own step-3 card "(Optional) Image conversion" after Settings (Q13): radio buttons (read directly = recommended), a disk-space warning (amber, red when the estimate exceeds free space, never blocking), and conversion settings (chunk size, compression + level, pyramid levels) shown only when converting. CLI `--convert-tiff` with `--chunk-size`, `--compression`, `--compression-level`, `--pyramid-levels`. No overlap setting: chunks never overlap |
| System files (Q5) | Ignored silently; `single` check and sibling uniqueness are case-insensitive |
| Too-deep folders (Q7) | Error |
| Folders named `single` (Q23) | Accepted as the level's value `single` when lower case and the only folder in its place (MorphoBricks one-member level, MorphoRelate M3); another case or other folders beside it stay `reserved_folder_name` |
| Channel names (Q1) | Manifest only (from OME); `channel_names_differ` was a warning, an error since issue #37 (channels are matched by number, no override); names trimmed of leading/trailing whitespace at read time for every image (manifest `channel_names_trimmed`), inner whitespace compared exactly; copies leave unnamed channels unlabelled; CSV stays at schema 1 |
| Plane coordinates (Q17) | `z`, `c`, `t` always written (0 for absent axes), `plane_id` always `::z=…,c=…,t=…`; CSV schema 2, version-1 tables recomputed on the next run. Superseded for the CSV by Q27: rows have `z`, `t` and channels in columns |
| Channels across columns (Q27, P3b) | CSV schema 3 with the brick's writer: one row per XYC plane, per-channel CellProfiler-style columns, `plane_id` `::z=…,t=…`. Channels read one at a time (owner's choice); memory estimate adds the plane's finished bin counts. `different_channel_counts` error before any read (headers in step 1, confirmed roles in step 2); `different_plane_counts` without channels. Schema 1/2 tables recomputed; manifest version 1 records schema 3; step 4 output unchanged. Brick first pinned by commit `0e53e67`, now by its tag v0.2.0 (Q28) |
| OME-NGFF labels (Q18) | Read-only helpers `ngff_label_names`/`open_ngff_labels` for later bricks (CellPatcher masks); label images are never analysed and the outputs do not change |
| Multi-series / pyramids (Q2) | Level 0 only; more than one series is an error |
| RGB `S` axis (Q3) | Prefilled as C, confirmed like any role |
| Significant bits (Q4) | Manifest only, information only; `bit_depth` and binning unchanged |
| Corrupt files (Q6) | Explicit exclusion list, recorded in manifest, rows removed |
| Notebook target | Google Colab and JupyterLab/LabConstrictor from one file, following LabConstrictor's notebook portability guide: three `# @title` code cells in order (1. Install, 2. Mount Google Drive, 3. Run preprocessing; owner, 10 November 2026), explicit `display()` of the existing notebook pages, Colab-only Drive mount inside `if "google.colab" in sys.modules:`, no magics, core ipywidgets 7.7/8 compatibility, no forced widget restart/custom manager, remote install pinned to immutable `RELEASE_REF` |
| CLI | `check <folder>` (summary, label tree, issues, suggested `--axes`) and `run <folder> --axes ROLES` with `--output`, `--exclude`, `--bins`, `--histogram-range`, `--robust-quantiles`, `--memory-limit 2GiB`, `--missing-as-singleton` (also on `check`, F10), `--mode restart|full`, `--no-masks`, `--no-exclude`, `--remove-stale-lock` and `--log`; also `masks`, `calibrate`, `design` and `convert` (the TIFF conversion options of v0.3, `--convert-tiff` and `--chunk-size`, were removed in v0.4.0). Axis roles are always stated explicitly, never taken silently from metadata (amended 3 November 2026, plan §12 question 73: when every image names all its axes, `run` uses those roles without `--axes` and prints them as the images name them; plain stacks still need `--axes`). Exit 1 on errors |
| Large planes (Q15) | Planes over the memory limit are read in blocks automatically, announced by the warning `planes_read_in_blocks` and `read_in_blocks` in the manifest. Block size derived from the limit (whole strips/tiles/chunks, uncompressed pieces and chunks also by rows). Compressed single-strip/tile planes keep the `plane_over_memory_limit` error. Two-pass histograms identical to whole planes. (Up to v0.3, OME-Zarr copies were written block by block; conversion was removed in v0.4.0.) Axis preview reduced (every n-th pixel) with a caption saying so |
| Normalization brick (P1) | The modules `dtypes`, `histogram`, `records`, `csv_io`, `pooling` and `normalization` moved unchanged to `morphobricks_normalization` (normalization plan N1, N2, N6) and are imported from there. Up to v0.3 their public names stayed importable from `morphobricks_preprocessing` and the old submodules, with a `DeprecationWarning`; those stand-ins (`_moved.py`) were removed in v0.4.0 (plan Q46). The package's own code never uses the old paths. Outputs unchanged (CSV schema 2); the manifest's `versions` adds `morphobricks-normalization`; Step 0 checks for it; contract §3–§7 text moved to the normalization contract (§1.2, now §6.2 for the CSV) |
| Dataset consistency (Q21, P2) | Step 1 "Dataset consistency" panel: balance table (images and technical-repeat folders per condition x biological repeat), `unbalanced_images` (< 0.5 or > 2 x median), `repeat_missing_conditions`, `different_pixel_sizes`, `missing_pixel_size` (OME-Zarr scale included), `different_plane_counts`; `different_sizes` narrowed to width/height; consistency issues shown only in that panel, not in Messages. Warnings only; outputs unchanged |
| Experiment design (Q22, P3) | Step 1 after the labels: pairing dropdowns, one yellow "Yes, they are comparable" question per repeat group (single whole-experiment question without biological-repeat folders), control checkboxes; `experiment_design.csv` written after every answer; reruns load it and fold the questions behind a Change button; step 3 gated. CLI `run` reads it or `--design`, else writes a template and exits 1. `run_experiment(design=...)` optional; manifest `experiment_design_sha256` (optional key, version 1). Two folders of one condition in a group block that group |
| Image type (Q29, P4) | Step 2 card after the axis roles: Fluorescence or RGB, suggestion preselected, confirmed with **Yes, this is the right image type**; RGB suggested for TIFF photometric RGB with three samples on the C axis; RGB needs three integer channels (`check_rgb`), else disabled with the reason and `rgb_not_possible` in a run. Manifest keys `image_type`, `channels_from_rgb_samples` (optional, version 1); type changes recompute nothing. CLI `--image-type`, default the printed suggestion |
| Intensity checks (Q30, P5) | Warnings from the table alone with the brick's `image_summaries`/`intensity_checks`, shown automatically after step 3 and in step 4 for any table; limits under a collapsed **Check settings** panel, recorded in the manifest (`intensity_checks`); per-channel box plots with flagged images and cumulative distributions per biological repeat, notebook only; the CLI prints the warnings (`--outlier-limit`, `--saturation-limit`, `--min-images`) |
| Histogram range (Q34, P6) | Step 3 **Settings**: the brick's `HistogramSettings` (`minmax` default, or `robust` with adjustable quantiles); recorded in the manifest's `settings.histogram`, any change recomputes every row. Every plane binned with `histogram_with_range_check` (memory estimate with `range_check=True`, through `planes._Binning`); `narrow_histogram_range` warnings from `range_check_issues`, one per channel, with the narrow planes recorded in the manifest's `narrow_histogram_ranges` so kept rows still report them. CSV schema 4 (underflow and overflow columns, zero with `minmax`); schema 3 tables recomputed. CLI `--histogram-range`, `--robust-quantiles`. Normalization brick pinned to v0.3.1 |
| Position identity (Q20, D02) | `position_id` = image name without extension, in every CSV row and manifest file entry; FOV key = labels + `position_id`; duplicates in one leaf folder are the error `duplicate_position_id`. CSV columns and schema 2 unchanged; manifest version 1 with an optional key |
| Masks (Q32) | Optional masks folder outside the experiment, one subfolder per mask type mirroring the experiment's folders, or the experiment's folders with `<image>_<type>.tif` (both may be mixed; `ambiguous_mask` when a name could fit two images or both layouts), paired by leaf folder + `position_id`; OME-Zarr label images are masks too. Header-only errors (`masks_folder_not_found`, `masks_inside_experiment`, `mask_names_differ_in_case`, `duplicate_mask`, `ambiguous_mask`, `unreadable_mask`, `mask_not_2d`, `mask_size_mismatch`, `mask_pixel_type`) and warnings (`mask_missing`, `mask_without_image`, `mask_outside_type_folder`) in `check`/`run`; manifest key `masks` (optional, version 1). Optional mask step (`masks` command, `scan_masks`): kinds, relations with evidence, direct-parent hierarchy, `mask_relationships.json` (report version 1); default thresholds the user may change (`thresholds=` keyword, one CLI option each, checked by `mask_thresholds`, written in the report); suggestions only, the user names and confirms. SciPy core dependency. Confirmed by the owner: 2D masks at the image's YX, integer/bool pixels, masks folder outside the experiment. Next: notebook step and a file of the user's answers; tag v0.2.0 proposed after merge. Plane identity (issue #36): masks may have Z and/or T named in their own metadata (never guessed: unnamed axes are `mask_not_2d`); the masks of an image share their planes (`mask_planes_differ`) and `run` checks them against the image's confirmed roles (`mask_planes_mismatch`, `mask_plane_issues`), so a 2D mask is refused for a Z-stack or time-lapse; manifest mask entries gain `axes` and `shape` (optional keys, version 1); `read_mask(z=, t=)` reads one plane; `scan_masks` scans plane by plane |
| Notebook style | Follow the lab's NucleiSky and LabChronicle notebooks (see §7) |
| Widget widths | **99%**, never 100% |
| Waits | Every wait over about a second shows `notebook_progress.Wait`: done of total, the item, elapsed time, time left once measurable. Readers take a `progress` callback; no bare spinner (owner, 10 November 2026) |
| Disabled actions | Every disabled button has a visible `notebook_render.blocked_line` beside it saying what blocks it; a tooltip alone is a hidden refusal (owner, 10 November 2026) |
| One screen per step | The shipped notebook's steps fit 1366 x 768: one row of chips, explanations under *About this step*, details in collapsed panels, long lists scrolling in their own box (owner, 10 November 2026) |
| Paths and filenames in widget HTML and notebook text | **`<b>…</b>` / `**…**`, never `<code>` or inline code** |
| Things the user must confirm | **Large buttons** in a highlighted question box (`ConfirmToggle`), never small checkboxes |
| Statistics step | **Shipped notebook since v0.5.1: the collapsed *Compare groups* panel of the run step, for that run's table; no table chooser, no second intensity check (owner, 10 November 2026). The page below remains for `results_step()` without `steps()`.** "Step 4 · Calculate the statistics (optional)": clearly optional, writes nothing. A CSV chooser (Browse…) is prefilled with the table just computed, otherwise empty. Not calculated automatically |

---

## 6. Programming style

- **Python 3.11+** (MorphoBricks decision D01). Put `from __future__ import annotations` in every module, and type
  hints on all public functions.
- **Ruff is the authority.** Line length 100. Rules E, W, F, I, B, UP, NPY and D, with
  numpy docstring convention; D105 and D107 ignored; tests are exempt from D.
  - D401 needs an **imperative first line** in docstrings of functions and methods
    ("Return…", "Build…", "Show…"). Properties may say "True if…".
  - B023: bind loop variables in lambdas (`lambda f, i=index: …`).
- **NumPy-style docstrings** (Parameters / Returns / Raises) on public modules, classes and
  functions. Module docstrings explain the module's job and point to the contract section.
- **Comments explain *why*,** not *what*. Keep the density of the surrounding code.
- **Data and state:**
  - Small **frozen dataclasses** for settings and results; no global state.
  - Mappings exposed from results are read-only (`MappingProxyType`).
  - Functions take explicit inputs and return results.
- **Determinism:**
  - Sort folders, files and outputs.
  - The same inputs give byte-identical CSVs; tests check this.
  - Write files as UTF-8 with LF line endings (`newline="\n"`), atomically (temp file,
    then `os.replace`).
- **Messages are for biologists:** short sentences, plain words, a suggested action, the
  affected paths. Use `validation.plural()` for counts ("1 image", "3 images"). User-facing
  text uses British spelling ("analysed", "normalise", "colour"); identifiers keep existing
  names (`normalization`).
- **Console output (CLI) must be ASCII-safe.** Windows consoles (cp1252) turn "→" into
  "?", so use "->" in texts the CLI prints. `cli.py` also sets `errors="replace"`.
- **No new core dependencies** without asking. tkinter (for folder dialogs) is optional
  and imported lazily with a fallback.
- **Public API** is re-exported in `__init__.py`. `__all__` is sorted: constants, then
  classes, then functions.
- **Code from the normalization brick:** import it as
  `from morphobricks_normalization.<module> import ...`.

### Tests

- **Simulated data only**, generated in `tmp_path`: `tests/tiff_tree.py` (`write_data`,
  `write_tiff`, `make_tree`, `write_file`) and `tests/simulation.py`. Do not rely on
  example files.
- **Every acceptance check in plan §11 has a test.** A bug fix comes with a test that fails
  without it.
- **Proving "no pixels are read":** monkeypatch `asarray` on `TiffFile`, `TiffPage`,
  `TiffFrame` and `TiffPageSeries` to raise.
- **OME-Zarr test data:** `tests/zarr_tree.py` (`write_ngff`, `write_bioformats2raw`,
  `write_plate`) writes stores with zarr directly; 0.5 tests
  skip with zarr 2.18.
- **Proving "one page at a time":** spy on `planes._read_page`. To prove "only changed
  files are read", spy on `run.rows.iter_xyc_planes` (patch the submodule that uses a
  name: `run.pipeline.file_sha256`, `run.publish` for `os`/`shutil`). For planes in blocks,
  spy on
  `planes._read_segment` and make `asarray` of every tifffile class fail.
- **Planes in blocks with small test images:** the memory limit
  `plane_layout(path, mapping, memory_limit=1).block_memory_estimate` gives the smallest
  blocks a file can be read in. Consume each lazy plane (and each `XycPlane`'s channels)
  before the next one (the file closes when `iter_planes`/`iter_xyc_planes` ends). The histogram tests, with an independent copy of the
  original whole-plane formulas (`reference_histogram`), are in the normalization brick.
- **Speed:** rerun tests use 8-bit images (256 bins). Wide 65,536-bin CSVs make every run
  about 1 s.
- **Platform-specific tests** (case-sensitive file system, POSIX permissions) skip with a
  reason on Windows.
- **Notebook widgets are tested headless:** build the pages, drive them with
  `button.click()` and `.value = …`, and assert on `.value` of HTML widgets. Tests skip
  if ipywidgets/matplotlib are missing; CI installs `.[dev,notebook,zarr]`. OME-Zarr
  tests skip without zarr; CI's Python 3.11 jobs pin zarr 2.18 and 3.13 jobs get zarr 3.
- **The notebook file is tested too:**
  - valid nbformat 4, with no outputs;
  - code cells hidden as Colab forms (`"cellView": "form"`) and titled `# @title <N>. …` (1. Install, 2. Mount Google Drive, 3. Run preprocessing);
  - Colab-only code and `/content` paths only inside the Colab guard; no magics;
  - the GitHub install pinned to `RELEASE_TAG`, equal to `current_version` and the
    version index;
  - exactly three code cells, in order: 1 installs and reports versions, 2 mounts Google Drive
    on Colab (a one-line note elsewhere), 3 displays only widgets built by `notebook_ui` and,
    run before cell 1, names cell 1 (`tests/test_notebook_setup.py`); every stop of a cell is
    one line without a traceback (the cell's `CellStop`, tested in a real kernel);
  - the buttons it names exist;
  - its cells execute, by `exec` and in a real kernel (`nbclient`, `tests/test_notebook_file.py`;
    cell 1's alternatives and the cells out of order in `tests/test_notebook_setup.py`).

---

## 7. Notebook UI guidelines

The lab's standard is the notebooks in
[CellMigrationLab/nucleisky](https://github.com/CellMigrationLab/nucleisky) and
[CellMigrationLab/LabChronicle](https://github.com/CellMigrationLab/LabChronicle). They use
plain core ipywidgets with injected CSS and copy their helpers into each notebook; here the
helpers live in `notebook_style.py` so all steps stay consistent. The notebook UI must remain
compatible with both ipywidgets 7.7 (Colab) and 8 (modern JupyterLab). The user found the first,
plainer version "not interactive enough". Descriptive, guided and forgiving is the bar.

### Notebook file (`notebooks/MorphoBricks_Preprocessing.ipynb`)

- **Title cell:** `# 🔬 **MorphoBricks | Preprocessing**`, a purpose paragraph, "What the
  notebook does", "Quick workflow" (numbered, naming every button), a "Before you start"
  checklist, a 🛡️ safety blockquote, and an example folder tree in a `text` block with
  📂/📄.
- **Per step:** `___`, then `## <emoji> **Step N: <Verb phrase>**`, then numbered
  instructions naming each button in bold. Tips are blockquotes (`> 💡 Tip:`, `> 🩹`,
  `> ℹ️`). Mark optional steps "(optional)" in the heading and say why.
- **Code cells:**
  - Hidden, with metadata
    `{"cellView": "form", "collapsed": false, "jupyter": {"source_hidden": true}, "jupyterlabHideCode": {"locked": true}}`.
  - The first line is a Colab title: `# @title <N>. …`, numbered as the headings (LabConstrictor
    portability guide; every code cell, all hidden, plan Q58). Longer explanations go in Markdown cells.
  - Outputs are stripped.
  - Three code cells (owner, 10 November 2026, as ZeroCostDL4Mic and NucleiSky): **1. Install**,
    **2. Mount Google Drive**, **3. Run preprocessing**, each under a Markdown heading that
    explains it. Cell 3 checks that cell 1 ran (`SETUP_RECORD`), and otherwise stops naming
    cell 1; on Colab without Drive mounted it opens the app after one line saying that only
    files on the runtime are visible (owner, v0.5.1). No cell runs another.
  - A cell that cannot go on raises its own `CellStop` (a `RuntimeError` whose
    `_render_traceback_` returns the message), so Colab and JupyterLab show one line and no
    traceback (owner, v0.5.1). A stop caused by another error (the interface import, the Drive
    mount) names that error on the same line; an error that is not a stop keeps its traceback. Cell 1 checks the Python version before `import tomllib`.
  - Cell 1 is a standalone standard-library bootstrap: nearby checkout, installed package,
    or GitHub installation of the release `RELEASE_TAG`, with missing requirements
    installed using the kernel's Python (`subprocess`, never `!pip`); on Colab with the
    `zarr` extra too. Colab-only code
    (cell 2's Drive mount) stays inside `if "google.colab" in sys.modules:`; no `/content` path
    outside it, and no custom widget manager is enabled. `current_version`
    and `notebook_name` are LabConstrictor's names; at each release the pin moves
    (plan §10, release checklist).
  - Later cells are one call each, e.g. `notebook.folder_step()`.
- **Step 0** starts the notebook and shows a green "ready" card with the package version.
- **Runtime callouts:** Steps 0 and 1 carry "**Colab users:**" / "**JupyterLab users:**"
  blockquotes (Drive, output folder outside the experiment, one session per output folder
  on Drive, runtime wiped on disconnect, Browse… only locally).
- **Closing cell:** "✅ After running" checklist and "Common problems" that quote the real
  message texts (check they still match the code).
- **Text:** use bold, not inline code, in the markdown (fenced text blocks such as the
  folder tree are fine).

### Page layout (`notebook_style`)

- **Embedding steps 1 and 2 (Q26, Q31):** `folder_step` and `axis_step` keep this notebook's
  words by default; another brick (MorphoRelate) passes its own `title`, `purpose` and
  `intro` (HTML; `""` leaves the box out), `show_output=False` to hide the output folder
  card (shown again when the check reports `output_inside_experiment`), and `show_next=False` to hide the "What next?" box naming this notebook's
  steps. The widgets and their behaviour are the same either way. `show_design=False`
  leaves out the "Experiment design" card: confirming the labels then writes nothing
  (no `experiment_design.csv`, no output folder), the step's texts name neither the design
  nor step 3, and step 3 runs without a design.
- **Page and cards:** `ui.page(title, subtitle, children)` wraps a step in a grey page
  (`mb-root`) with a title and a one-sentence purpose. `ui.card(header, children)` gives a
  white card with an upper-case header. `ui.intro(html)` adds a blue "what happens here"
  box at the top of each step.
- **Help text:** `ui.help_text(html)` adds a **grey help line under every control**: what
  it is, what the default does, what happens if changed.
- **Status cards:** `ui.status(kind, title, lines)` is the single status-card component.
  - Green "ok": done, next step named.
  - Blue "info": waiting or neutral.
  - Amber "warn": please check.
  - Red "error": action required.
  - Errors are phrased as fixes ("Read the messages below; each one says what to do").
    Successes end with what to do next.
- **Other building blocks:**
  - `ui.working(text)`: spinner status while something runs.
  - `ui.tiles([(emoji, value, label)])`: headline numbers.
  - `ui.table(rows, numeric_from)`: scrollable, sticky header, right-aligned numbers.
  - `ui.checklist([(done, text)])`: ✅/⭕ lists.
  - `ui.path_list(paths)`: long lists folded behind "Show N more".
  - `ui.button(…, main=True)`: full-width (99%), 42 px tall main action at the bottom of
    a step, with a Font Awesome icon and a tooltip. Button style follows meaning: success
    = do it, info = look or preview, warning = change or undo or exclude.
  - `ui.ConfirmToggle(question, explanation, confirm_label, confirmed_text, undo_label)`:
    a yellow question box with a big green "Yes, …" button. Once confirmed the box turns
    green and shows an amber "Undo: …" button. Use it for **every** "the user checked
    this" moment (labels correct, preview looks right, missing axes are single planes).
- **Paths:**
  - `ui.ask_directory()` and `ui.ask_open_file()` open native Tk dialogs (topmost). They
    return None when no dialog is possible, so show "paste the path instead".
  - `ui.clean_path()` strips quotes and smart quotes and expands `~` and environment
    variables.
- **CSS:**
  - Forced light colour scheme and a system sans-serif font.
  - `overflow-x: hidden` on boxes inside pages, so ipywidgets boxes never show stray
    horizontal scrollbars.
  - Escape all dynamic text with `html.escape`.

### Interaction rules (learned)

1. **Gate, don't surprise.** Later steps are locked until earlier ones are confirmed. Step
   3 shows a "Before you run" checklist naming the first thing still to do, and Run stays
   disabled until it is empty.
2. **Inputs reset what depends on them.** Changing the experiment folder resets the
   preflight, the confirmations and the axis roles. Changing the output folder reads the
   experiment design from the new folder, or asks it again (#98); never carried over.
   Changing an axis role resets the preview confirmation. Choosing another CSV clears the
   old statistics.
3. **Explain every choice live.** Each axis card shows its length, the suggested role and a
   dropdown with descriptive options ("Z - depth (focal planes)"). Under it, a sentence
   updates with the choice ("3 depth slices, numbered z = 0 to 2; each becomes its own
   plane"), and a summary says how many planes of which size each image gives.
4. **Show what will happen before it happens.** "What will happen" lists the output folder,
   the files to be written, whether an earlier run exists and what will be reread, and
   (once the axis roles are confirmed, from the headers only, cached) which images will
   be read in blocks or cannot be read within the memory limit. A
   live line under the output folder shows where results go and warns if that is inside
   the experiment.
5. **Recommended settings first.** A ticked "Use the recommended settings" hides the
   advanced controls (bins with dtype-specific bounds and help, memory limit, recompute
   everything). Unticking reveals them, and re-ticking restores the defaults.
6. **Busy discipline.** Disable the button, change its label ("Computing…", spinner
   icon), and restore it in `finally`. The progress bar names the current image. Turn the
   bar green or red at the end.
7. **Previews must be checkable.** A file picker plus z/c/t sliders (hidden for axes of
   length 1 and until the preview is shown; the middle Z slice first). The image is
   stretched for display only (0.5–99.5 percentiles), and the caption gives the file,
   coordinates, size, dtype and value range. Problems reading a plane show as a status
   card, not a traceback.
8. **Results:** tiles (planes, read, kept, new, changed, removed, excluded, checks). The
   statistics table explains its columns. A cumulative intensity plot per group (up to
   12). Unexpected exceptions become a red card with the type and message; the traceback
   goes to the collapsed "Technical details (log)" accordion, which is fed by a
   `logging.Handler` attached during each action.
9. **Optional means optional.** Say so in the title and intro, write nothing, and do not
   compute it automatically.
10. **Visual check.** Before calling UI work done, render pages with
    `ipywidgets.embed.embed_minimal_html(...)`, serve the folder with
    `python -m http.server`, and look at them in a browser. This catches overflow and
    scrollbars, awkward order, and hidden-vs-shown states. Standalone renders lack
    JupyterLab styles and icons; say so when reporting.

---

## 8. tifffile knowledge (version 2026.x)

- **Axis codes** (`TIFF.AXES_NAMES`): X width, Y height, Z depth, S sample, I sequence, T
  time, C channel, A angle, P phase, R tile, H lifetime, E lambda, L exposure, V event,
  M mosaic, Q other, J column, K row.
- **How tifffile labels a plain stack:** it depends on how the file was written. Without
  metadata, `imwrite(..., metadata=None)` gives `IYX`; with `metadata={}` (a "shaped" series)
  it gives `QYX`. Both must be assigned by the user.
- **When writing test files:**
  - Pass `photometric="minisblack"`, or 3-sample stacks become RGB.
  - RGB contiguous is `YXS`; planar is `SYX` (`planarconfig="separate"`).
  - `volumetric=True` with `tile=(z, y, x)` stores a whole Z stack in one page.
- **Series:** `tif.series[0]` is level 0; OME pyramids have `series[0].levels`. Non-OME
  SubIFD writes create a second series.
- **Planes as pages:** `series.pages[i]` may be a `TiffFrame` (OME, compressed) and may be
  None for missing OME IFDs. `page.asarray()` decodes one page.
- **Splitting a series into pages:** the shape is page-selecting leading axes followed by
  the page shape. Find the split `k` where `prod(shape[:k]) == len(series.pages)` and
  `prod(shape[k:]) == keyframe.size`, with X and Y at or after `k`. Otherwise the layout
  is unsupported (planes would span pages).
- **Encoded size:** use `keyframe.databytecounts`.
- **Byte order:** dtypes may be big-endian; normalise them with `.newbyteorder("=")`.
- **OME details:** `tif.is_ome` and `tif.ome_metadata` hold the XML.
  - Channel names: `Pixels/Channel@Name`. Significant bits: `Pixels@SignificantBits`.
  - Write them in tests with
    `metadata={"axes": ..., "Channel": {"Name": [...]}, "SignificantBits": 12}`.
  - ImageJ "Labels" are slice labels, not channel names.

---

## 9. Environment gotchas (seen on Windows)

- **Line endings:** files must stay LF. Python's `Path.write_text` on Windows writes CRLF:
  write bytes, or pass `newline="\n"`.
- **Worktrees and the editable install:** an editable install in the main checkout's venv
  imports the *main* checkout's `src`. In a git worktree, run tests with `PYTHONPATH=src`
  so the worktree code is used.
- **Encoding:** set `PYTHONIOENCODING=utf-8` when printing widget HTML (emojis) to a
  Windows console.
- **Long inline scripts:** shell heredocs with non-ASCII characters ("…", emojis) can
  break; write the script to a file and run it instead.
- **What ruff touches:** ruff also lints and formats `.ipynb` code cells and Python code
  blocks inside Markdown files (it reformatted README/user-guide examples). Run
  `ruff format .` before `ruff format --check .`.

---

## 10. Commands

```bash
python -m venv .venv
pip install -e ".[dev,notebook,zarr]"
ruff check .
ruff format --check .
pytest
morphobricks-preprocessing check /data/exp1
morphobricks-preprocessing run /data/exp1 --axes ZCYX
morphobricks-preprocessing masks /data/exp1 --masks /data/exp1_masks
morphobricks-preprocessing convert /data/exp1 --to /data/exp1_named_axes --axes ZXY
jupyter lab notebooks/MorphoBricks_Preprocessing.ipynb
```

---

## 11. Status (current state, v0.5.1)

- **Released:** v0.5.1 (10 November 2026), with the normalization brick pinned at v0.4.0. The
  per-release history is in [CHANGELOG.md](CHANGELOG.md); the milestones PP-0 to PP-13 are in
  [docs/archive/plan-milestones-v0.1-v0.4.md](docs/archive/plan-milestones-v0.1-v0.4.md); the older
  status log of this file is in
  [docs/archive/agents-status-history-v0.1-v0.3.md](docs/archive/agents-status-history-v0.1-v0.3.md).
- **Unreleased:** none yet; later changes go in a new "Unreleased" section of the changelog.
- **Scope:** 2-D per plane (a Z-stack is refused as `z_stack_not_supported`); TIFF and OME-Zarr
  input read directly (TIFF to OME-Zarr conversion and the `_moved.py` re-exports were removed);
  optional masks, the mask step and the notebook's **Describe the masks**; named-axis copies
  (`convert`, notebook card); calibration; resumable, crash-consistent runs.
- **Command line:** `check`, `run`, `masks`, `calibrate`, `describe`, `convert` (`check`,
  `run` and `masks` need a published description, v0.5.1).
- **Next:** [docs/roadmap.md](docs/roadmap.md).
- **Not yet verified:**
  - the exact look of the notebook in a real JupyterLab;
  - the optional `jl-hidecode` JupyterLab extension, which keeps code cells collapsed. It
    is mentioned in the user guide but is not a dependency.
