# Changelog

All notable changes to `morphobricks-preprocessing`. Numbers are pull requests (PR) and
issues of [CellMigrationLab/morphobricks-preprocessing](https://github.com/CellMigrationLab/morphobricks-preprocessing).
The design is `docs/restart/preprocessing-design-v0.4.md` in MorphoFeature, on branch `claude/restart-doc` ([commit ff19de8](https://github.com/CellMigrationLab/MorphoFeature/blob/ff19de8938d5ed8c4143cff563715551ee874a05/docs/restart/preprocessing-design-v0.4.md); not on `main`). The decisions
are in [docs/plan.md](docs/plan.md) §12. Upgrading from v0.3: see the
[migration guide](docs/migration-0.3-to-0.4.md).

## Unreleased

- **`convert --project max|mean|sum [--z-range START:STOP]` (part of #176).** The command line of
  the recorded Z projection: no default method (`--project` alone or an unknown method is a
  parser error naming the three), `--z-range` zero-based and half-open for the whole
  experiment (`4:5` is a single slice; `--z-range` without `--project` is refused), the memory
  limit, `--exclude` and `--masks` as for any conversion (the recorded masks of the output
  folder are scanned for 3-D masks). It prints what was written and the next commands for a
  new output folder, `<--to>_MorphoBricks_Preprocessing` (the projected copies are a
  different source), with `--axes` of the copies. `z_stack_not_supported` now names the
  command ("Make 2-D projected copies first: convert EXPERIMENT --to FOLDER --axes ROLES
  --project max|mean|sum ...") instead of Fiji. `ConversionResult.projection` holds the
  record's method and range.
- **`convert_experiment(projection=Projection(...))` writes recorded 2-D copies of Z-stacks
  (part of #176).** Every image is projected with the method (`max`, `mean` or `sum`; no
  default) over all slices or one slice range for the experiment; the copy's axes are the
  confirmed roles without Z, each (t, c) is projected in ascending z into one running plane,
  the Z size goes to the record's `dropped_sizes`, and the copy's OME-XML says how it was
  made. Refused before anything is written: `nothing_to_project`, `projection_z_range_outside`,
  `projection_cannot_be_exact`, 64-bit integers (`cannot_convert_pixel_type`),
  `projection_over_memory_limit`, OME-Zarr images (`projection_zarr_not_supported`, for now),
  3-D masks (`projection_masks_3d`, `projection_masks_axes_unknown`, from the headers of the
  masks folder); a sum overflowing to infinity is `projection_overflow`. The source's content
  SHA-256 is taken before and after, and recorded. Another method or range into the same
  tree is `copy_differs`, naming both. A copy's OME-XML UUID is derived from its description,
  so one projection is the same bytes twice. `convert_experiment` takes its keyword options as
  a `ConversionOptions` group (same keywords; an unknown one is a `TypeError`) because it would
  have had nine parameters. `PlannedCopy` moved to `planned_copy.py`. No command or notebook
  card yet.
- **Conversion record version 3 holds a Z projection (part of #176).** `RECORD_VERSION` is `"3"`:
  a record-level `projection` (null, or `{method, z_range}`), the entry action `projected`
  and a strictly read `projection` object per projected entry (source content SHA-256, Z axis
  and size, slices used, method, types, `dtype_rule`, `rounding`, `nan_policy`,
  `dropped_sizes`, `significant_bits_dropped`; new pure module `projection_record.py`).
  An entry that contradicts itself, the record or the dtype table is refused, and the projection
  is part of the reuse key. Version 2 records are still read; version 1 is still refused. Nothing
  writes a projection yet (plan §12 question 77, contract §17 "Projected copies").
- **Z-projection arithmetic (part of #176).** New pure module `projection.py`: `Projection`
  (method `max`, `mean` or `sum`, no default; optional zero-based half-open slice range),
  `projection_dtype` (the output type from the input type, method and slice count only: max
  keeps the type; integer sums widen to `uint32`/`int32`, or to `float64` holding the exact
  integer for 32-bit inputs; means and float sums are `float64`; 64-bit integers are refused
  as `cannot_convert_pixel_type`; a sum or mean whose worst case could not be exact is refused
  as `projection_cannot_be_exact`, never wrapped or clipped), and `PlaneAccumulator` (slices
  added in ascending z into one running plane; NaN propagates, infinities are kept, a finite
  sum that overflows to infinity is `projection_overflow`, `inf + -inf` is
  `projection_undefined`). No I/O and no command yet: `convert --project` follows in later
  pull requests (design in MorphoFeature `docs/restart/designs/pp176-z-projection-design-2026-11-07.md`).
- **The records append is rolled back with the description (K4 of external review batch 26).**
  `_append_records` appended straight to the live `morphobricks.records.jsonl`; a `write`,
  `flush` or `fsync` that failed after bytes were written left a torn line, or records naming a
  hash of a description that was rolled back, because only the TOML was restored. The new file
  (old bytes plus new lines) is now written in full to a synced temporary file and replaced
  atomically, and a failed publication puts back both the description and the records bytes.
  `read_records` now also refuses any line that is not one of the three Spec record types
  (`reconfirmation`, `image_count_changed`, `removed`) with exactly that type's keys (`{}`
  was accepted before). A record's hashes are history: a reader never compares them with the
  published file.
- **The Spec test gate holds module-level skips and missing modules (#203).** A `test_spec_*.py`
  module skipped while it is collected (`pytest.skip(..., allow_module_level=True)`, a module-level
  `importorskip`) is now a failed collection, and a run over the whole tests folder fails when no
  test was collected from a module of `EXPECTED_MODULES` in `tests/spec_gate.py` (deleted or
  emptied module). Regression tests in `tests/test_spec_gate.py`.
- **A mask changed after the last check is no longer published (#198).** Every run, also one
  with `verify_content` off, fingerprints each mask again in the commit's `before_commit`
  callback, right before the manifest is replaced; a changed mask stops the run with
  `mask_changed_during_run` and the earlier generation is intact. Strict runs do this plus their
  existing SHA-256 check.
- **Derived `experiment_design.csv` and the cross-version parity suite (Stage 1, PR 10).** New
  module `design_derive`: `derive_design(description)` and `write_derived_design(description,
  path)` derive the legacy design file from a published description (Spec section 11): source
  folder labels as `condition` and `biological_repeat`, the repeat ID as `repeat_group`,
  `control` true for a condition another one references, `technical_repeat_count` 0 for the
  single unit, `confirmed` true and the repeat's `confirmed_on`. It writes with today's
  `write_experiment_design`, so the bytes equal the Spec's golden files for `v13`, `v33` and
  `v34` and the reference validator's derivation of every valid fixture; renaming a condition or
  technical-repeat ID never changes the file. `tests/test_parity_v0_4_0.py` runs the same
  confirm-run-rerun script under the `v0.4.0` tag and under this code (`tests/released_code.py`)
  on six legacy homogeneous datasets (one repeat, two conditions by two repeats, technical
  repeats, flat, OME names, RGB): `plane_histograms.csv` and `experiment_design.csv` are
  byte-identical, manifest file entries keep every v0.4.0 key and value (only the optional
  `raw_channel_names` is added), a rerun over a v0.4.0 output folder recomputes no row, and the
  derived design of each dataset's description equals the bytes v0.4.0 wrote. Not used by any
  run, notebook or CLI command yet. Stacked on the Spec re-pin.
- **Re-pin the vendored Spec to 7dbae0c (MorphoBricks-Spec PR 9).** New in the pin: the byte
  rules and golden files of the derived `experiment_design.csv` (section 11; `fixtures/expected/`
  for `v13`, `v33`, `v34`, not yet derived by any code: Stage 1 PR 10), the byte form of one
  section digest (9.3), `t13` (a changed `image_count` alone does not reopen a confirmation) and
  the encoding of `morphobricks.records.jsonl` (4.6). The reader, writer and publication agree
  with every fixture and the reference validator (0 differences, no exception for `image_count`
  any more; the oracle's section digests equal ours on every fixture). Fix: `read_records` split
  the records file with `str.splitlines`, which also cuts at U+2028 and U+0085, so a record
  holding such a character (written unescaped as the Spec requires) was refused as "not JSON";
  lines now end at LF only. Test helper: the catalogue no longer reads the golden-file rows as
  fixtures.
- **Safe display of planes (Stage 1, PR 13; part of #169).** New modules `preview` and
  `preview_render`, not used by the notebook yet. `preview.read_plane(path, mapping, max_side=...)`
  returns at most `max_side` pixels per side (`THUMBNAIL_SIDE` 256, `DETAIL_SIDE` 1024), every
  n-th source pixel, read in blocks under a 64 MiB budget whatever the run's memory limit: a
  20,000 x 20,000 uint16 plane peaks at about 64 MiB where `read_sample_plane` peaked at about
  1 GiB. A plane the file cannot be split for is read whole only within the run's limit, else
  refused (`plane_too_large_to_show`). `channel_strip` shows one panel per channel (middle Z,
  first T, stated) only through the roles the user confirmed (`AxisReview`): no review, a file the
  review does not cover or a file with an axis error is refused (`axis_roles_not_confirmed`), a
  file without a `C` axis is one channel whatever its shape, and a file that changed since its
  fingerprint is refused (`file_changed_while_shown`). `mask_thumbnail` uses the image's step and
  refuses a mask of another size. Rendering follows NucleiSky's order (reduce, float32, percentile
  contrast p0.5-p99.5 of the finite values, per channel): a flat image is mid-grey labelled
  `flat: every finite value is v`, an image without finite values is magenta, non-finite pixels
  are magenta and counted, never zeros. PNGs are written with the standard library (no
  matplotlib); `mask_overlay_png` draws only a representation the caller declares (`instances`
  outlines, `binary` tint) and never infers it. Not done here: `representative_fovs`, the caption
  text with unit and header name (`notebook_render`), and any widget.
- **Physical `R_*` checks of an experiment description (Stage 1, PR 8; stacked on the
  discovery and header additions, which provide the raw header names).** New module
  `physical_check`: `check_physical(description, discovery, metadata, ...)` reports
  `R_UNIT_NOT_FOUND`, `R_UNIT_NOT_DECLARED`, `R_UNIT_NOT_HOMOGENEOUS` (names the FOV, path and C
  index; it replaces the count and header checks of its unit), `R_CHANNEL_COUNT_MISMATCH`,
  `R_HEADER_CONTRADICTION` (raw names, NFC of both sides, no trimming, no case folding),
  `R_IMAGE_TYPE_CONTRADICTS_HEADER` (both directions, header only; OME-Zarr states no colour
  interpretation and is recorded as not header-verified) and `R_MASK_TYPE_NOT_DECLARED`. Pixels
  are never read and no mask representation is defaulted or inferred. Homogeneity compares the
  axes with `T` left out, so time-lapses of different lengths are one layout. An image whose
  channel count the header does not state (a plain stack without confirmed roles) or whose header
  cannot be read is listed in `unchecked`, not passed. `layout_groups` groups units by identical
  layout; only groups whose channels are all header-named are batch-confirmable. Not used by any
  run, notebook or CLI command yet.
- **Reconciliation of the published description with the folder (Stage 1, PR 9; conformance
  P07, P08, P15, P33).** New modules `description_reconcile`, `description_removal` and
  `reconcile_observe`. Each unit is `unchanged`, `new` or `removed`, matched only by its exact
  source folder labels. A unit that is new beside a confirmed member, a removed unit, channel
  evidence that no longer fits the declared list and a folder the user stated was renamed
  (`FolderRename`) reopen the N35 confirmation of that repeat only (`confirmed = false` in the
  draft, `confirmed_on` copied from the published file, a new date only from the user's
  `confirm_repeat`). A change of `image_count` alone reopens nothing (it is reported as
  `CountChange` and the member's count follows the folder). A renamed folder is a removed unit
  plus a new pending one, never paired by name; a new folder is proposed only for an existing
  repeat ID equal to its label. A removal needs the user's confirmation, asked once per
  condition (`removal_questions`: every lost repeat, technical repeat and channel in one
  question); a removed ID stays in `used_ids`, and a `reference` to a removed condition is never
  cleared silently. Fix in the merged transition check: `confirmed_on` may now move with the
  user's new confirmation when a condition ID holds another folder (conformance P33 b); the
  layout it compares (`repeat_layout`) includes the condition's source folder. Not used by any
  run, notebook or CLI command yet. Not done here: the pending new units are not in the draft
  (they need channels and IDs, which are the draft model's), and image-count changes are
  reported but not appended to `morphobricks.records.jsonl` (the file defines only
  re-confirmation records).
- **Discovery and header additions for the experiment description (Stage 1, PR 6).**
  Additive; a run without a description behaves as before. (1) `preflight` and `remasked`
  take `with_description=False`; with it, a condition, repeat or technical-repeat folder that
  is not Unicode NFC is the error `R_FOLDER_NOT_NFC`, a discovered mask type that breaks the
  Label rules (`NA`, ` cells`) is `R_MASK_TYPE_NAME_INVALID` and one that is not NFC is
  `R_FOLDER_NOT_NFC` (new module `name_issues`, also used by `find_masks(with_description=)`);
  nothing is renamed or normalized (owner decision Q1: without a description these names are
  accepted as in v0.4). (2) `TiffMetadata.raw_channel_names` and `FileEntry.raw_channel_names`
  hold the header channel names exactly as read, untrimmed; `channel_names` stays trimmed,
  `same_source` ignores them, and the manifest file entry gains the optional key
  `raw_channel_names`, written only when the header names a channel. The OME header reading
  moved from `metadata` to `ome_header`. (3) `preflight(progress=...)` calls
  `progress(phase, done, total, path)` for the phases `folders`, `headers` (one call per
  image), `masks` and `acquisitions`; `run.phases.progress_text` words them ("2/4 headers
  120/480"). Not used by the notebook or the command line yet. Tests:
  `tests/test_spec_discovery.py` (conformance P21, P26 recorded names, P29) and
  `tests/test_preflight_progress.py`.
- **Content hashing of the sources is on by default (#159; owner decision of 7 November 2026).**
  A run records each image's content SHA-256 and reuses a row only for the same content, so an
  image edited in place with its size and modification time kept is read again (manifest
  version 3). Opt out with `RunMode(verify_content=False)`, `--no-verify-content` or by
  unticking the notebook's box; the manifest then records `settings.verify_content: false`
  (version 2 and 3 manifests now always write the key; one written before has none and reads
  as not verified). An output folder keeps the choice of its earlier run, so an existing one
  is not recomputed. Cost on 60 OME-TIFF images of 25 MB (1.5 GB), cold cache: a fresh run
  15.9 s without and 19.8 s with hashing (+25 %); a rerun that reuses every row 5.4 s without
  and 8.3 s with (+3 s). The standalone `masks` command is unchanged.
- **Experiment-description publication (Stage 1, PR 5).** `description_file.read_description(path)`
  reads and validates a `morphobricks.toml` (a symbolic link is `R_FILE_SYMLINK`, never followed);
  `description_transition` compares a candidate with the published file and reports every `R_*`
  that applies (`R_CONFIRMED_ID_CHANGED`, `R_USED_ID_DROPPED`, `R_USED_ID_REUSED`,
  `R_CONFIRMED_ON_CHANGED`), accepting a changed ID only with the user's recorded
  `Reconfirmation`; `run.description_file.publish_description` does it under the output folder's
  lock: base SHA-256 or `absent` (`R_DRAFT_BASE_MISSING`, `R_PUBLISHED_FILE_CHANGED`), canonical
  bytes re-read by the strict reader, atomic write, and the re-confirmations appended to the
  append-only `morphobricks.records.jsonl` (old ID, new ID, date, SHA-256 before and after).
  All twelve Spec transitions and scenarios P09, P16-P20 and P22 pass. A new `confirmed_on` beside
  a changed image count alone is refused (new FOVs do not reopen a confirmation, section 10); the
  Spec's validator accepts it. Copying the records into the run manifest is a later pull request.
  Not used by any run, notebook or CLI command yet.
- **Draft description, proposed IDs and legacy design migration (Stage 1, PR 7).** New
  modules `draft_ids` and `draft`, and `design.parse_design_bytes` (the
  former private `_parse_strict`, now public: the strict parse, no confirmation required). A folder label is proposed as a condition,
  technical-repeat or biological-repeat ID only when the reader would accept it as written
  (Spec 4.6a; the same rule for repeat IDs): `control`, `files` or `z` get no proposal, nothing is
  repaired, derived or renamed. A raw header name is proposed as a channel ID only when it is a
  valid ID as it stands (never trimmed or repaired; `R`, `G`, `B` never). A draft keeps IDs as
  *proposed* until `confirm_id`, lists its open questions, and offers the added `condition_id`,
  `repeat_group` and `technical_repeat_id` beside the unchanged source labels.
  `migrate_legacy_design` reads `experiment_design.csv` strictly (`yes`/`no`/empty refused),
  carries `repeat_group` as the proposed repeat ID, takes the latest `confirmed_on` of a group
  with several dates and lists the original dates in a note (never the clock), maps 0, 1 or 2+
  marked controls to no reference, one reference per condition or an open question, and refuses a
  design that breaks a Spec rule with the reader's code. A legacy `repeat_group` that is not an
  allowed repeat ID (`z`, `files`) is not refused: it migrates with no proposed ID and an open
  question, so the user names the repeat (Spec 4.6a; owner decision of 7 November 2026). Where the Spec says "another allowed
  ID" for a forbidden folder there is no proposal: no rule for making one was given. Nothing is
  written and no run, notebook or CLI command uses it yet.
- **Experiment-description model, canonical writer and section digests (Stage 1, PR 4).** New
  modules `description` (the `Description` model; `description_from_bytes` reads with the strict
  reader and raises `DescriptionError` with every issue of the first failing phase) and
  `description_write` (`canonical_bytes`, `section_digests`): deterministic bytes of Spec
  experiment-v1 section 9.1, the round trip `parse(write(parse(F))) = parse(F)` on every valid and
  transition fixture, idempotent writing, and the SHA-256 of the `conditions`, `channels`,
  `repeats` and `masks` sections with `display_name`, `target` and `treatment_key` omitted
  (`v18` = `v01`; `v16` and `v21` have the digests of `v01`). Section 9.3 does not define the
  byte form of one section; here it is the section's table blocks as the file writes them. Not
  used by any run, notebook or CLI command yet.
- **Spec pin moved to MorphoBricks-Spec `745259b` (Stage 1, PR A).** The vendored fixtures,
  `experiment-v1.md` and the reference validator are the Spec's at that commit (new fixtures
  `i131`-`i134`; 36 valid, 134 invalid, 12 transition). The reader now applies the condition
  `single` rule (a condition ID is `single` if and only if its `source_folder` is `single`, and
  such a condition is the only one): `E_SINGLE_MISUSE`, phase 4. A key written twice inside an
  inline table was already `E_DUPLICATE_KEY`. Reader and reference validator agree on every
  fixture and mutation. Not used by any run, notebook or CLI command yet.
- **Experiment-description reader, phases 4-5 and warnings (Stage 1, PR 3).** New modules
  `description_identity`, `description_semantics`, `description_warnings` and
  `description_walk`; `description_read.read_description_bytes(data: bytes)` runs all five
  phases and the three warnings of Spec experiment-v1 and reports every error of the first
  failing phase. Every Spec `valid/` fixture passes with its exact warnings and every
  `invalid/` fixture fails with its exact code set; the Spec's reference validator is the test
  oracle. Not used by any run, notebook or CLI command yet.
- **Experiment-description reader, phases 1-3 (Stage 1, PR 2).** New modules
  `description_rules`, `description_report`, `description_read` and `description_structure`:
  `read_structure(data: bytes)` runs the strict phases 1 to 3 of Spec experiment-v1 (bytes and
  encoding, duplicate keys, header and version, structure, forbidden fields) and reports every
  error of the first failing phase with its code, table path and value. Not used by any run,
  notebook or CLI command yet.
- **Spec fixtures vendored (Stage 1, PR 1).** `tests/spec_fixtures/` holds the
  MorphoBricks-Spec conformance fixtures at a pinned commit, with a git tree-hash pin test,
  a gate that makes a skipped `tests/test_spec_*.py` test a failure, and the "no fallback
  rules" scan (conformance plan sections 1 and 2). Tests only: no change to the package,
  the CLI, the notebook or any output.
- **Group statistics per channel (#104).** `group_summaries` and the notebook's step 4 no
  longer pool distinct channels together: each group has one summary per channel, and the
  intensity curves are drawn in one panel per channel. Single-channel tables give the same
  numbers as before.
  - `GroupSummary` gains `channel` (the channel index, or `ALL_CHANNELS`/None for an
    aggregate), `xyc_plane_count` (XYC planes) and `channel_histogram_count` (histograms
    pooled). `plane_count`, which counted each channel of a plane as a plane, remains as a
    deprecated property equal to `channel_histogram_count` and warns
    (`DeprecationWarning`). The positional constructor changed: build summaries by
    keyword, or use `group_summaries`.
  - `group_table` adds the columns `channel`, `XYC planes` and `channel histograms` (in
    place of `planes`).
  - The earlier all-channel aggregate is available on request,
    `group_summaries(..., pool_channels=True)`, and is labelled `all channels (pooled)`.
  - A group whose planes have different numbers of channels is refused (`ValueError`).
  - **Migration:** no file changes. `plane_histograms.csv` (schema 4), the manifest and
    the other outputs are unchanged; step 4 writes no file. Code reading
    `GroupSummary.plane_count` should read `xyc_plane_count` or `channel_histogram_count`
    and select rows by `channel`; to reproduce a pre-#104 table, pass
    `pool_channels=True`. No other MorphoBricks repository uses these names.

- **Cyclic mask hierarchies are named (#105).** When the direct-parent relations of the mask
  step form a cycle (possible because pairs of types may be seen together in different
  images), the hierarchy text no longer prints an empty or truncated tree: it starts with
  the warning `mask_hierarchy_cycle`, naming every type and link of the cycle, and shows
  the group as one `[cycle: …]` entry; every type is listed, and no direction is guessed.
  New `MaskReport.hierarchy_cycles` and `mask_scan.hierarchy_cycles`. Acyclic hierarchies
  print as before; `mask_relationships.json` is unchanged (report version 1).

- **Masks are bound to their fingerprints for the whole run** (#160). Each mask's header is
  read between two fingerprints that must agree (`find_masks(bind=True)`,
  `MaskFile.bound`; else the new error `mask_changed_during_check`), the manifest records
  that fingerprint (before, it was taken when the manifest was written, so a mask rewritten
  during the run was recorded under headers checked on another version), and every mask
  must still have it right before the commit; otherwise the new error
  `mask_changed_during_run` names the mask and what changed, and nothing is published. A
  `verify_content` run also hashes every mask after the header checks and again as the last
  step before the commit. Content hashing stays opt-in (#159).
  - *Records and migration:* no schema change. The manifest (versions 2 and 3) and its mask
    entries keep their keys and values: for masks unchanged during the run the fingerprint
    recorded is the same, so an unchanged rerun keeps the manifest byte for byte. The masks'
    content SHA-256 is compared in memory and not recorded. Earlier manifests read as
    before.
- **No mask relationship report from masks that change while they are scanned** (#165). The
  mask step fingerprints each mask with its header (`prepare_mask_step`) and each image's
  masks before and after their planes are read; a mask that changed or went missing raises
  `MaskChangedError` (a `ValueError`; the new error `mask_changed_during_scan`, which the
  `masks` command and the notebook's **Describe the masks** show with what to do), no report is written and an earlier one is left as it was. With
  `verify_content` (`prepare_mask_step(verify_content=...)`, new `masks --verify-content`;
  by default the recorded run's setting) the masks' content SHA-256 is compared too.
  *Records:* `mask_relationships.json` is unchanged (report version 3) and unchanged masks
  give the same report.

## v0.4.0 (5 November 2026)

The final v0.4.0, with the small changes since v0.4.0rc5 (#147) and the release step: the
version is 0.4.0, the notebook installs `v0.4.0`, and **the normalization brick is pinned at
v0.3.4** (it was v0.3.3 in the release candidates).

- **Normalization v0.3.4** (its README, "Migrating to v0.3.4"). The pooled `mean` and `std`
  and the `zscore` offset and scale change **in their last bits** (normalization#67): they can
  differ from v0.3.3 by a few units in the last place (at most 3.7e-14 relative on its
  reference dump). A run, cached z-score parameters or features computed with v0.3.3 or a
  release candidate are not bitwise reproducible with v0.3.4; recompute them rather than
  mixing the two. Histograms, `plane_histograms.csv`, minimum, maximum, percentiles, median,
  MAD and the other normalization methods are bitwise unchanged. A `PlaneHistogram` of a
  plane with valid pixels but no `pixel_min` or `pixel_max` is now refused (no code of this
  package builds one). No preprocessing test or frozen fixture compares these values with
  v0.3.3 output bit for bit.

- **MorphoBricks v1 is 2-D per plane** (owner decision of 5 November 2026, tracker row 36;
  plan §12 question 76; contract §2, §9, §15). 3-D feature extraction will be a separate
  project.
  - **Z-stacks are refused:** an image with more than one depth slice is the new error
    `z_stack_not_supported`, naming every such image with its number of slices and saying
    to project the stack first (for example a maximum-intensity projection in Fiji) and use
    the projected image. It is found from the Z axis the header names (ImageJ, OME-TIFF,
    shaped TIFF, OME-Zarr; `check_metadata`) and again under the confirmed roles (a plain
    stack given a Z role; `review_axes`, which supersedes it), before any pixel is read, so
    `check`, `run` and `convert` stop before writing anything but the report. A header Z
    confirmed as another role (an ImageJ time-lapse tifffile reads as Z) stays the axis
    error `axes_contradict_header`, so `convert` can still fix it. A Z axis of
    one slice is accepted as it is. Nothing is projected or squeezed; a recorded projection
    step is planned after the release.
  - **The stated shared 2-D mask is removed:** `--mask-axes TYPE=YX` (and YX under the
    notebook's Mask axes), which made one 2-D mask stand for every plane of its image, is
    the new error `shared_mask_not_supported`; the notebook no longer offers it, and a
    `YX` statement recorded by an earlier run is not applied again. A 2-D mask of a
    time-lapse is `mask_planes_mismatch`, whose message now says that the masks must have
    the image's time points (T); `mask_planes_differ` no longer names a statement.
    `MaskFile.shared` and `mask_review.SHARED_CODE(S)` are removed, the manifest no longer
    writes a mask's `shared` key, and `RunInputs.open` refuses a manifest recording
    `shared: true` (`mask_record.shared_mask_text`, replacing `unstated_shared_text`).
    Plain-stack masks with stated axes (`--mask-axes Cells=TYX`) are unchanged.
  - The other bricks are not changed; their own Z-plane policies stay as a second defence.
- **`check --missing-as-singleton`** (matrix finding F10, owner decision of 4 November
  2026; plan §12 question 75): `check` takes `run`'s option (`--missing-as-singleton`,
  `--no-missing-as-singleton`), with the same default (the earlier run's answer in the
  output folder, else no) and the same review of images lacking some axes
  (`run.check_step.suggested_review(pre, missing_as_singleton=)`), so an experiment of 2-D
  images and Z-stacks passes `check` once confirmed (it always exited 1) and `check` and
  `run` report the same errors for the same roles. The suggested option then reads
  `--axes ZCYX --missing-as-singleton` when some image lacks an axis; an answer taken from
  the earlier run is printed by `check` and `run` ("Images lacking some axes: read with one
  plane along them, as confirmed for the earlier run"). The notebook's check report takes the answer step 2
  shows (the earlier run's), which step 2 still asks for before the run.
- **A 2-D mask is shared by a Z-stack or time-lapse only when stated** (superseded by
  the removal above; owner decision of
  4 November 2026, reversing the mask half of F10/#148; plan §12 question 75; contract §9,
  §15): a 2-D mask whose metadata names `YX` (TIFF, OME-TIFF, also OME metadata tifffile
  writes by itself, and OME-Zarr label images) is no longer shared by its image's planes
  without `--mask-axes TYPE=YX` (or YX under the notebook's Mask axes), recorded
  `shared: true`, `axes_from: "stated"`, as in rc5. The header-derived path
  (`MaskFile.named_shared`, `masks.stated.mask_names_its_axes`) is removed. Without the
  statement, `mask_planes_mismatch` and `mask_planes_differ` name it for each mask type with
  2-D masks among them (for example `--mask-axes Nuclei=YX`), and the notebook asks for YX
  in the `mask_planes_differ` case too. `RunInputs.open` refuses a manifest that records a
  mask `shared: true` without `axes_from: "stated"` (only main at #148 wrote it; no tag),
  naming the masks, the statement and "Run preprocessing again".
- **Shared write-target rule** (owner decision of 3 November 2026): `--log`'s rule is
  public as `write_target_refusal(path, *, protected_folders, protected_files={},
  output=None, output_names=())` (`morphobricks_preprocessing.write_target`, exported by
  the package; contract §8), so another brick that writes a file its user names
  (MorphoFeature's `--to-segment`) calls preprocessing's rule rather than a copy of it. It
  returns the reason as a clause, or None. `--log` uses it, with the same messages and
  behaviour (compared with rc5 over 238 cases).
- **Compatible with normalization's next release** (normalization#65, issue #47): the
  schema-version-2 equivalence test passes `pixel_min` and `pixel_max` to `PlaneHistogram`
  from the version-2 columns; it passes with normalization v0.3.3 and with v0.3.4 (the final pin). No package code builds a `PlaneHistogram`.
- **`mounted_folder` on overlayfs** (review of #146): the contract and `mounts` say that on
  an overlay file system whose layers are on different file systems, mounted with
  `xino=off`, files may report another `st_dev` and are then refused.
- **`source_changed_during_run` names a content change** (review of #146): the last
  content check of a strict run says "their content (SHA-256) is no longer the one the
  run checked"; an image read in a strict run "their size, time, files or content
  (SHA-256)"; a default run's message is unchanged.
- **An unknown manifest version is "not supported" again** (review of #146):
  `ManifestFormatError: Manifest version '4' is not supported (this version reads '1',
  '2', '3').`, not "The manifest is incomplete or malformed: ...".
- **A refused identical strict rerun discards its publication** (review of #146): when the
  last content check before the commit failed and the new manifest was byte-identical to
  the published one, the clean-up took the publication for a committed one and logged
  "Finishing the publication an earlier run left". The publication is now discarded as
  soon as the check fails ("Discarding the unfinished publication"); the outputs are the
  same as before.
- **Review of #147:** a refused publication that cannot be removed (a file held open) no
  longer turns the refusal into an `OSError`; its record is removed first, so the next run
  discards the leftover rather than finishing it. `write_target_refusal` refuses a single
  string as `output_names` (`TypeError`), and its docs name what can change between the
  check and the write.
- **A publication is committed only once its manifest has left `.publish`** (external
  audit of #147): when the refused publication's record could not be removed, the
  clean-up found the record beside a byte-identical published manifest and took the
  refused generation for a committed one, so a strict `full` rerun refused by its last
  content check removed `mask_relationships.json`. `finish_publication` now finishes a
  publication only when `.publish/run_manifest.json` is gone and the manifest in place is
  the record's; anything else is discarded, record first (a `.publish` whose record cannot
  be removed is left whole, still uncommitted). The refused run keeps every output.

## 0.4.0rc5 (since v0.4.0rc4)

What changed between v0.4.0rc4 and v0.4.0rc5: PRs #139 (cross-brick test of 3 November
2026), #140 (review of rc4), #142 (round-2 audit), #143 (this release candidate), #145
(normalization v0.3.3) and #146 (release review: strict manifests, the final content check).
Each item is described in full under 0.4.0 below. Decisions: [plan](docs/plan.md) §12
questions 66, 72, 73 and 74.

### Migration notes (from rc4)

- **Mount points are refused** (#140, #146): a mount point below the experiment or masks
  folder, as listed in Linux's `/proc/self/mountinfo`, or a folder, image or mask on
  another device than the selected folder, is the error `mounted_folder` in discovery,
  `check`, `run`, `convert` and `RunInputs`. Not seen: a mount made after the walk started,
  and, without a mount table (macOS, BSD, Windows), a mounted single file or a mount of the
  same device. Copy the files into the experiment, or select the mounted folder itself. A
  file with several hard links is the warning `hard_linked_file`.
- **`position_id` is required in a version 2 manifest** (#142, A-m5), equal to the image's
  name without its extension; anything else is a `ManifestFormatError`. Manifests written
  by rc1 to rc4 open unchanged (rc4's is tested); a hand-edited one may not.
- **Sizes in micrometres may change by up to 1 ULP** (#142, B-m7) when computed from a unit
  other than µm (`9 nm` is now `0.009`, not `0.009000000000000001`). Manifests are
  unchanged; exact comparisons of the same length in two units now agree.
- **`--verify-content`** (#140, I3), opt-in: rows are reused only for the same content
  SHA-256 (`RunMode(verify_content=True)`, the notebook's **Verify each image's content**).
  Turning it on or off recomputes every row; a default run's manifest has the rc4 keys.
- **A strict run's manifest is version 3** (#146, release review): a `--verify-content`
  run writes `manifest_version` `"3"`, which rc4 and earlier refuse (`ManifestFormatError:
  Manifest version '3' is not supported`, tested by running rc4's code on it) instead of
  opening it as a fingerprint-only run; with version 2 they ignored the new keys. Default
  runs still write version 2, byte for byte as before. This version reads 1, 2 and 3; a
  version 2 manifest holding `verify_content` or `content_sha256` (unreleased rc5 builds
  only) is refused as malformed, so the next run recomputes it.
- **A strict run hashes every source again right before its commit** (#146): after the
  generation is staged and right before the manifest's rename, so a rewrite that restores
  size and time after the earlier checks is `source_changed_during_run`; no scientific
  outputs are published and sources are untouched; the discovery report is written, and
  the earlier generation is intact. It costs one more whole read of every image per strict
  run; the default run is unchanged.
- **A child tied between parents has no parent** (#143, plan Q74): a child lying exactly as
  much in two or more parents is no longer given the one with the lower label. When that
  share reaches `inside_pixel_share` (at the default, exactly half in each of two parents)
  it is counted as `ambiguous_parent`, not `inside`; a tie below it stays
  `split_between_parents`, as before. `mask_relationships.json` is report version
  3; `inside` and `parents_with_N_children` may be lower than with rc4 when masks have such
  ties. CellPatcher and MorphoFeature read neither count.

- **The normalization brick is pinned to v0.3.3** (#145). It refuses corrupt or ambiguous
  statistics and parameters instead of using them (its README, "Migrating to v0.3.3"):
  among others a `plane_histograms.csv` with the header but no rows or with two rows of one
  `plane_id`, and `z` or `t` that are not whole numbers 0 or above. Valid inputs give
  bit-identical results, so tables written by rc4 read unchanged; the full test suite
  needed no change for the new refusals. A hand-edited table may now be refused.
- **A new intensity warning, `channel_without_finite_pixels`** (#145, normalization #57):
  an image channel whose pixels are all NaN or infinite has blank summaries, which the four
  intensity checks cannot see. It is now named, after the other intensity warnings, in
  `check_intensities`, `check_table`, `run` (its report and the command line) and the
  notebook (its own tile, **channels not checked**). `INTENSITY_CODES` has five codes;
  code that expects four should list the new one.

### Changed and fixed

- Normalization brick v0.3.3; `channel_without_finite_pixels` from its `unchecked_channels`
  is an intensity warning (#145, normalization #57).
- `pair_counts`: an exact tie between parents gives no parent; the new evidence
  `ambiguous_parent` is shown by `masks`, the notebook's **Describe the masks** card and its
  relationship table; report version 3 (#143, plan Q74).
- An ImageJ TIFF with a unit but neither resolution tag is `invalid_physical_calibration`
  with the reason; before, uncalibrated with no reason. `read_physical_sizes` gives a TIFF
  without pages no sizes with the reason in `invalid`; in `check` and `run` such a file is
  the error `unreadable_tiff`, as before (#143).
- A TIFF calibration that cannot be read is invalid, and defects are raised (#142, A-m6);
  `position_id` required in v2 (#142, A-m5); one length in any unit is one double (#142,
  B-m7).
- Mount points refused, hard links named (#140, B1/I1); the output folder flushed after the
  commit (#140, I2); the identity rule stated and `--verify-content` added (#140, I3).
- Strict manifests are version 3; strict runs hash every source again right before the
  commit; an image or mask file on another device is `mounted_folder` too; refused runs
  are worded as what they do (no scientific output published, the report written) (#146).
- `masks` accepts per-plane masks with a stated shared 2-D mask (F1); `run` needs no
  `--axes` when every image names its axes (F9); a rerun that changes nothing keeps the
  manifest byte-identical (F10); stale locks and resumed runs are worded (F11); `convert
  --output` and the order of copies are explained (F8) (#139, plan Q73).
- Plan Q66 confirmed by the owner; Q73's follow-up (a stated page reshape or reordered
  copies) is not in v0.4 (issue #144).
- Version 0.4.0rc5; the notebook installs `v0.4.0rc5`.

## 0.4.0 (unreleased; release candidates rc1 to rc5)

Preprocessing becomes the one step that discovers, validates, records and serves an
experiment. Every later brick reads the run through `RunInputs`. Nothing is guessed: axes,
calibration, masks and design come from the headers or from the user's recorded answers.

Release candidates: **v0.4.0rc1** (after PP-6, #112), **v0.4.0rc2** (after PP-8d, #118),
**v0.4.0rc3** (#125, after PP-10d), **v0.4.0rc4** (PP-13, for the round-2 data-flow audit
and the bricks' migration) and **v0.4.0rc5** (#143, after the round-2 audit fixes).

### Breaking

- **Manifest version 2** (PP-4, #110). It records per file `roles`, `axes_from` and
  `pixel_sizes` by role with unit and source, and per mask `size`, `mtime_ns`, `roles`,
  `axes_from` and `shared`. A v1 manifest gives no reusable row and no recorded input:
  **every v0.3 run must be run again.** A row is reused only when its `RowKey` matches.
- **The axis check** (PP-1, #64). Roles that swap the header's Y and X, contradict the
  header, or put X before Y in a plain stack are errors (`axes_yx_swapped`,
  `axes_contradict_header`, `axes_x_before_y`). Nothing is read transposed. A plain stack's
  confirmation covers only the files it was given for (`plain_stack_not_confirmed`, PP-4).
- **Modes replace `force_rebuild`** (PP-7, #113): `mode="restart"` (default) and
  `mode="full"`, CLI `--mode`. A later run reuses every recorded input. A value given that
  differs is reported with its effect. `--no-masks` and `--no-exclude` drop the recorded
  masks and exclusions.
- **Lock** (PP-7, #113): an `O_EXCL` lock file in the output folder. A second run is refused
  with `output_locked`. `--remove-stale-lock` / `remove_stale_lock()` removes a stale lock
  only on request.
- **`image_named_as_mask` is an error** (PP-5, #111). Masks are checked as stored and never
  converted. OME-Zarr `labels/` are always used. Plain-stack masks need `--mask-axes` per
  type. A 2-D mask of a multi-plane image needs `--mask-axes TYPE=YX` (shared). A mask whose
  header names X before Y is refused (`mask_layout`, PP-8b, #115).
- **Bricks read runs through `RunInputs`** (PP-6, #112), which refuses v1 manifests,
  changed files and checksums, and files the run did not record (`UnrecordedFilesError`,
  PP-8b, #115).
- **OME-Zarr**: OME-NGFF 0.1 and 0.2 stores, and stores without `axes`, are
  `unreadable_ome_zarr`. Every OME-Zarr image is matched by its declared roles
  (`ome_zarr_axes_mismatch`) (#129; issues #72, #73). An OME-Zarr image's fingerprint
  includes its file inventory: images recorded by v0.4.0rc3 or earlier count as changed, and
  `RunInputs` refuses such runs (PP-12, #132; issue #86).
- **Links** in the experiment and masks folders are followed only inside them
  (`link_outside_folder`, `linked_twice`, `link_loop`) (#128; issue #81).
- **Exit status**: every command exits with 1 when it prints an error. `check` and `design`
  exited with 0 on some errors (#133; issue #93).
- **Experiment design**: every design reader refuses a header with a duplicate or unnamed
  column. `RunInputs.design()` uses the strict reader (#130; issue #83). `load_design`
  returns `(design, changes)` and raises `DesignCheckError` for an unreadable file (#62).
- **Conversion records** are version 2, with each copy's SHA-256. Version 1 records
  (v0.4.0rc3 or earlier) are refused (PP-11c, #126; issue #87).
- `copy_changed_after_conversion` is an error that stops the run (PP-12, #132).
- `--memory-limit` must be a whole number of bytes above 0 (PP-11c, #126; issue #80).

### Added

- The intensity warning `channel_without_finite_pixels`, from the normalization brick's
  `unchecked_channels` (v0.3.3, normalization #57): an image channel without finite pixels,
  which the four intensity checks cannot see (#145).
- `RunInputs.open(output, experiment_root=None, masks_root=None)`, with `fovs()`,
  `pixel_sizes()`, `read_plane()`, `read_image()` (lazy), `mask_types()`, `read_mask()`
  (labels checked), `histograms()`, `design()` and `image_type()`. It is picklable.
  `testing.make_clean_run` writes a clean run for the bricks' tests (PP-6, #112).
  `unrecorded_files()` (PP-8b, #115).
- `discovery_report.json` and `.txt` on every run, also when it stops, with what was
  reused, recomputed, resumed and replaced (PP-7, #113).
- Calibration: `calibration.csv` in the output folder (a size or `pixels` per experiment,
  folder or image), recorded per axis with its source. CLI `calibrate`, `write_calibration`,
  `read_calibration`, `review_calibration` (PP-8, #114). No Z or T size is recorded for an
  image without that axis (`calibration_axis_absent`, PP-8c, #117).
- CLI `design` (`--repeat-group`, `--control`, `--no-control`, `--confirm`,
  `--confirm-all`) and `edit_design` / `DesignEdit`. `check` writes a prefilled design
  template (PP-8c, #117).
- Named-axis copies: CLI `convert EXPERIMENT --to FOLDER --axes ROLES` and
  `convert_experiment`. Images with axis errors are written as OME-TIFF with the confirmed
  roles; other images are copied byte for byte; the conversion record
  `.morphobricks-conversion.json` is written last (PP-11, #124). Copy SHA-256 and a single
  metadata epoch (PP-11c, #126; issues #87, #88, #92, #96). `preview_conversion` (PP-11b,
  #127).
- Staging and resume: each image's rows are staged in `.staging` and reused by the next run
  when their key and fingerprint still match. Crash-consistent publication through
  `.publish`, committed by the manifest's `os.replace` (PP-12, #132). Images that change
  during the run are `source_changed_during_run` (issue #90).
- Notebook: Google Colab and JupyterLab support, pinned to `RELEASE_TAG` (PP-8d, #118). The
  **Pixel sizes** and **Experiment design** cards, the **Mode** choice, the report, and
  **Remove the lock of a stopped run** (PP-10a, #120). The **Masks folder** field with mask
  axes per type (PP-10c, #122). **Describe the masks** (PP-10d, #123). **Make named-axis
  copies** (PP-11b, #127).
- `_cp_masks` and `_masks` mask suffixes (PP-5, #111).
- Code standards (design v0.4 §9) enforced by `tools/check.py --rules` with a shrink-only
  baseline (PP-0, #65). The pinned embedding API for MorphoRelate (PP-9, #119).
- Docs: the [migration guide](docs/migration-0.3-to-0.4.md), this changelog, and the
  [Colab hand check](docs/colab-release-check.md) for the major release (PP-13). The roadmap
  (#116).
- Warnings `partial_pixel_size` (a pixel size along only one of X and Y) and
  `non_square_pixels` (X and Y not exactly equal in micrometres): bricks that measure
  lengths refuse such images (audit m5, plan §12 question 72).

### Fixed

- **A TIFF calibration that cannot be read is invalid, not absent** (round-2 audit, A-m6):
  `read_physical_sizes` caught every exception and logged it at debug level, so a file
  it could not read, or a programming error, read as an uncalibrated image. Now a file that
  cannot be opened as a TIFF (`TIFF_OPEN_ERRORS`: `OSError`, `ValueError` including
  `TiffFileError`, and the `TypeError` and `struct.error` tifffile raises on some damaged
  headers) and metadata whose bytes cannot be read (`METADATA_READ_ERRORS`: `OSError`,
  `struct.error`, `TiffFileError`; module `tiff_size_values`) give no sizes with the reason
  in `PhysicalSizes.invalid`, named by `invalid_physical_calibration`; no metadata is still
  no calibration; any other exception is raised, and a value that is not a path is a
  `TypeError`. An ImageJ resolution tag that tifffile dropped because it could not read it
  (it only logs), next to the other one, is named instead of leaving that axis silently
  uncalibrated. An ImageJ unit with neither resolution tag ("a unit without a resolution")
  is `invalid_physical_calibration` too, no longer uncalibrated with no reason (owner, 3
  November 2026; #143). `read_physical_sizes` gives a TIFF file without pages ("no pages")
  no sizes with the reason in `invalid`; the folder check reports such a file as the error
  `unreadable_tiff`, as before.

- **A child lying equally in two or more parents has no parent** (owner, 3 November 2026;
  plan §12 question 74, #143): `pair_counts` gave it the parent with the lower label index,
  so `inside` and `parents_with_N_children` depended on the label numbering. An exact equal
  largest overlap now gives no parent; when the share would make the child inside it is
  counted as `ambiguous_parent` (new evidence), shown by `masks`, the notebook and the
  relationship table. `mask_relationships.json` is report version 3.

- **A version 2 manifest names every image's position by its name** (round-2 audit, A-m5):
  the reader accepted a file entry without `position_id`, or with null, and `RunInputs`
  then keyed that position by `""`; an edited `position_id` was read as given. Version 2
  now requires it, equal to `position_id(relative_path)` (the image's name without its
  extension, D85), and refuses anything else naming the entry and the identifier its name
  gives (`ManifestFormatError`); the writer refuses the same, so a written manifest always
  opens. Every v0.4.0 writer recorded it this way, rc4 included (a committed rc4 manifest
  is read in the tests). Version 1 manifests are read for reporting as before.

- **One length written in any unit is one double in micrometres** (round-2 audit, B-m7):
  `to_micrometers` multiplied by a binary approximation of the factor, so `9` nm became
  `0.009000000000000001` µm while `0.009` µm stayed `0.009`, and a header in nm next to a
  `calibration.csv` in µm for the same size was not the same number (an exact comparison
  called the pixels non-square). Dividing by the integer factor would still leave about a
  fifth of such decimals 1 ULP apart (`108.37 / 1000` is `0.10837000000000001`). A length is
  now converted from the shortest decimal of its double (`repr`), its point moved by the
  unit's power of ten and rounded once by `float` (no binary factor, no `decimal` context):
  for a value parsed from a decimal of at most 15 significant digits that is the decimal
  written, so the same length in pm, Å, nm, µm, mm, cm or m gives the double `float` gives
  for it written in µm (`108.37` nm is `0.10837`). At 16 or 17 digits two decimals can give
  one double and the one written is not known. The result is still always positive and
  finite, and an overflow or underflow is still refused. **Sizes in micrometres computed
  from a unit other than µm may change by 1 ULP** (the last bit of the double); manifests
  are unchanged, as they record each size in its own unit; the 0.1 % rule
  (`same_pixel_size`) is not affected.

- **The identity rule is stated, and strict reuse is offered** (review of rc4, I3; owner, 3
  November 2026): reuse, resume and `RunInputs` identify a source by its metadata
  fingerprint (size and `mtime_ns`; for OME-Zarr the inventory of paths, sizes and times),
  whose known blind spot is a same-size rewrite with the time set back; the contract, the
  user guide and the docstrings now say so. The opt-in `RunMode(verify_content=True)` (CLI
  `--verify-content`, the notebook's "Verify each image's content" box) records each
  source's content SHA-256 (`files[].content_sha256`, `settings.verify_content`) and
  reuses earlier or staged rows only for the same content; `RunInputs` of such a run checks
  it at `open`, and every source is hashed again right before the commit. Its manifest is
  version 3, which v0.4.0rc4 and earlier refuse (#146). It is a recorded input; turning it
  on or off recomputes every row and is named. The default run is unchanged: its manifest
  is version 2 and has neither key (`verify_content` is written only when true).

- **Mount points below the experiment or masks folder are refused** (owner, 3 November
  2026): a bind mount is an ordinary folder to the walk, so its files from outside were
  read. A folder (or file) listed in Linux's `/proc/self/mountinfo`, or a folder, image or
  mask on another device than the selected folder, is the error `mounted_folder` naming
  it, in discovery, OME-Zarr stores, the masks folder, `check`, `run`, `convert` and
  `RunInputs` (`mounts.MountCheck`). A mount made after the walk started is not seen.
  Where no mount table can be read (macOS, BSD, Windows), only a mount of another device
  is seen: not a mounted single file of the same device, nor a same-device mount. The
  device is read with `os.stat` of the path: `os.DirEntry.stat()` reports `st_dev` 0 on
  Windows, which refused every folder there (review of #140).
- **The commit is flushed to disk** (review of rc4, I2): the output folder is flushed
  (`fsync`, POSIX) right after the manifest's rename, so a power cut after a run returned
  keeps the new generation. A run that finishes an earlier publication flushes it first.
- **Hard-linked images and masks are named** (owner, 3 November 2026): a file whose link
  count is above 1 is the warning `hard_linked_file`; it is read as before.

- Cross-brick test of 3 November 2026 (plan §12 question 73): `masks` accepts per-plane masks
  with a stated shared 2-D mask (`--mask-axes Nuclei=YX`) as `run` does, instead of
  `mask_planes_differ` (F1); `run` needs no `--axes` when every image names its axes (F9);
  a rerun that changes nothing leaves `run_manifest.json` byte-identical, keeping its
  `written_at`, so pinned manifest SHA-256s stay valid (F10); `output_locked` says a lock
  left by a process no longer running on this computer looks stale and how to remove it
  (also `convert`'s, for the folder of the copies; it is never removed on its own), `run`
  without `--axes` names the images' differing arrangements instead of `--axes None`, and a resumed run no longer says all rows were computed (F11); `convert --output` names
  the runs' output folder in the printed next commands, and `inconsistent_dimensions` says
  that named-axis copies keep each image's order (F8).
- Preprocessing audit of 3 November 2026 (plan §12 question 72): `read_plane` and
  `RunInputs.read_plane` no longer count histogram memory, so a 5000² float32 plane is read
  within the 1 GiB limit (I2); a header size in an unsupported unit (OME `pixel`) or without
  a unit is unknown in `pixel_sizes`, never a value without a unit (I3); `RunInputs.read_mask`
  and `read_plane` refuse a `z` or `t` outside the image with `IndexError`, a shared 2-D
  mask included (I5); `to_micrometers` accepts NumPy real scalars (m4); OME-XML in a TIFF
  that cannot be parsed is `invalid_physical_calibration`, no longer read as uncalibrated,
  and other errors reading TIFF sizes are no longer hidden (m6). An ImageJ resolution tag
  of another type is `invalid_physical_calibration`, and an OME-Zarr scale without a unit
  is named by `unsupported_pixel_size_unit` (review of #136).
- Output files get the permissions of a plain `open()` (your umask) instead of `0600`
  (PP-8c, #117).
- A stopped run says what it wrote (PP-8c, #117).
- `read_plane` and `read_mask` raise `TypeError` for a `z` or `t` that is not an integer
  (PP-8c, #117).
- The `masks` command uses the recorded inputs (PP-8c, #117).
- `--no-masks` after a run with stated mask axes was refused (PP-10c, #122).
- Malformed OME-Zarr calibrations, and header sizes that overflow or are not positive
  finite, give unknown sizes with `invalid_physical_calibration` (#129, #133; issues #94,
  #93). Every conversion to micrometres is positive and finite (`to_micrometers`).
- Technical-repeat folders whose images are all excluded are not counted (#130; issue #82).
- Changing the notebook's output folder reloads that folder's design (#130; issue #98).
- `--log` is checked before it is opened, and refused inside the inputs or on a protected
  output (#128; issue #99).
- `same_objects` is a one-to-one maximum matching; `mask_relationships.json` is report
  version 2 (#131; issue #67).
- `different_sizes` and `different_plane_counts` follow the confirmed roles (#131; issue
  #74).
- The conversion checks memory and free space before writing and refuses 64-bit integer
  images (PP-11, #124; issues #75, #76). The run reads the copies themselves (issue #79).
- An experiment or named-axis copies inside the masks folder are refused
  (`experiment_inside_masks`, `copies_inside_masks`), and so is a copy folder overlapping it
  (`copy_folder_overlaps_masks`; `convert --masks`, `masks_folder=`); `RunInputs.open`
  refuses a recorded run that breaks these rules (audit B1, 3 November 2026).
- `check`, `run`, `masks`, `design` and `calibrate` check the output folder against the
  masks folder, given or recorded, before writing anything (audit I1).
- `output_inside_experiment` and `copy_folder_overlaps_experiment` compare folders by
  device and inode too, as the masks-folder rules do, so `Exp/out` is inside `exp/` on a
  file system that ignores case (macOS, Windows), and discovery still skips that output
  folder (#135 review follow-up).
- Masks one pixel high or wide are read as 2-D `YX` masks (#63).
- Step 1 keeps an unreadable `experiment_design.csv` untouched (#61).
- The notebook starts from the inputs the earlier run in the output folder recorded: the
  exclusions, bins, histogram and intensity settings, image type and missing-axes answer,
  from the run's own rule (`run.planned.recorded_inputs`). An untouched notebook kept
  neither a CLI run's `--bins` nor its `--exclude` (audit I4; plan §12 question 71).

### Removed

- `convert_tiffs` and `zarr_export` (OME-Zarr copies in `<output>/ome_zarr`):
  `ConversionSettings`, `ConversionEstimate`, `estimate_conversion`, `OmeZarrWriter`,
  `ome_zarr_relative_path`. Also the CLI `--convert-tiff`, `--chunk-size`, `--compression`,
  `--compression-level` and `--pyramid-levels`, and the notebook's conversion card (PP-2,
  #66).
- The deprecated stand-ins of the code moved to `morphobricks_normalization` (`_moved`,
  `csv_io`, `dtypes`, `histogram`, `normalization`, `pooling`, `records` and their
  top-level names) (PP-2, #66).
- `check_declared_axes` and `declared_axes_transposed` (PP-2, #66).
- `find_masks(ome_zarr_labels=)` (PP-5, #111).
- `run_experiment(force_rebuild=)` and `--force-rebuild` (PP-7, #113).
- `notebook_options.PYRAMID_OPTIONS` (PP-10b, #121).
- The backup-and-restore publication protocol (PP-12, #132).
- Compatibility of `PhysicalSizes` pickles across versions is not guaranteed (owner
  decision, design v0.4 change log).

## 0.3.4 and earlier

See the release notes of the tags and [docs/plan.md](docs/plan.md) §12, questions 33 to 41.
