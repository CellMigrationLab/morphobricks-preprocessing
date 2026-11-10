# Changelog

All notable changes to `morphobricks-preprocessing`. Numbers are pull requests (PR) and
issues of [CellMigrationLab/morphobricks-preprocessing](https://github.com/CellMigrationLab/morphobricks-preprocessing).
The design is `docs/restart/preprocessing-design-v0.4.md` in MorphoFeature, on branch `claude/restart-doc` ([commit ff19de8](https://github.com/CellMigrationLab/MorphoFeature/blob/ff19de8938d5ed8c4143cff563715551ee874a05/docs/restart/preprocessing-design-v0.4.md); not on `main`). The decisions
are in [docs/plan.md](docs/plan.md) §12. (The v0.3 to v0.4 migration guide was removed in
v0.5.1: no run before v0.5.1 is read.)

## Unreleased (v0.5.1)

### Notebook redesign 3: the axes step shows the data as the roles read it; pixel size in one line

Owner test of v0.5.0 (10 November 2026), items (e) and (f). The shipped notebook has 12 steps:
*Pixel sizes* is part of *Axes*.

- **An example of one image as the chosen roles read it**, drawn by the folder check itself and
  redrawn whenever a role changes (`axis_example.example_rows`, `notebook_plots.example_png`):
  one tile per channel labelled `c=…` (six evenly spread beyond six), a few time points
  `t=…` or depth slices `z=…`, or the single plane, each with its height and width and its
  true proportions, so a wrong role looks wrong. The image and the other planes are under
  **Another image, or other planes**. **Show preview** is gone (its button is hidden and still
  works for the bricks that click it). The planes are read as before: with the roles being
  edited, only the image as the folder check read it, labelled *not confirmed yet*.
- **The confirmation is unchanged and explicit.** The roles are confirmed by **Yes, the example
  looks right** and **Confirm axis roles**, or by the one-click **Confirm these axes** under the
  same conditions as before (owner decision Q3); nothing is confirmed without the click. The
  role boxes and the example are always shown; the *Look closer, or change the roles* fold and
  the separate *Suggested axes* card are gone.
- **Pixel size in one line, with the axes** (`pixel_size_summary`): "Pixel size: 0.108 × 0.108
  µm (from the headers, same in all 270 images)". When nothing gives a size, one input states
  it for the whole experiment (**Use for all images**: the user's explicit statement, written
  to `calibration.csv` for `.` as the `calibrate` command does, replacing other X and Y
  statements; Z statements are kept). When the images do not agree the line says so with the
  counts and folders ("Headers disagree: 0.108 × 0.108 µm in 180 images, 0.216 × 0.216 µm in
  90 (Condition_2/Biological_repeat_2)") and **Set per imaging unit…** opens: the size of each
  folder and the form to state one for a folder or an image. Nothing is chosen silently: each
  image is recorded with its header's size until the user states another, and a stated size
  that differs from a header is still warned about (`calibration_differs_from_header`). The
  rules of `calibration.csv` and what the manifest records per image are unchanged; the
  experiment description holds no pixel size (Spec experiment-v1).
- Texts of the notebook no longer carry step numbers after step 3 ("the run can start", "on
  Image type"), so they stay right when steps merge.

### Notebook redesign 2: progress for every wait, and a quick scan before the headers

Owner test of v0.5.0 (10 November 2026), items (c) and (d).

- **Every wait shows what is done of the total, the item being read, the time elapsed and,
  once it can be measured, the time left** (`notebook_progress.Wait`, `progress_html`,
  `time_left`): the folder walk ("Listing the folders: 14 so far"), the image headers
  ("Reading image headers: 34 of 120, CTRL/rep1/a.tif"), the mask headers, the check report,
  the pictures per folder, **Check the masks**, the run, the mask step, the named-axis copies
  and the statistics (their stage of four). "Checking the folders and image headers…" is gone.
  The time left is said only after one item and one second; uncounted work shows the elapsed
  time alone.
- **Progress callbacks from the readers.** `discover_experiment(..., progress=)` is called with
  each folder before it is listed; `masks.find_masks(..., progress=)` and
  `run.preflight.remasked(..., progress)` before each mask header; `preflight`'s `progress`
  now also reports `("folders", 0, 1, folder)` and `("masks", done, total, mask_path)`. The
  first and last call of each phase, and every result, are unchanged.
- **Quick scan first (the shipped notebook's first step).** **Scan the folders** walks the
  folders only (the folder check's own walk; no image header is read), shows every folder with
  its labels and image count, and opens one image chosen at random, with its path and seed, for
  looking only (drawn from that file's header by the rules of the folder pictures). Nothing of
  the scan is kept. The folder check is then the explicit click **Looks right: read all image
  headers**; it is disabled, with a line saying why, until the folders are scanned as they are
  typed now, so changing a folder, the output or masks folder or the exclusions asks for a new
  scan. `folder_step()` for other bricks is unchanged: **Check folder**, one click.
- `is_tiff_name`, `is_system_name`, `TIFF_SUFFIXES` and `SYSTEM_NAMES` moved from `discovery`
  to `image_names` (the functions are still importable from `discovery`), keeping
  `discovery.py` under the 600-line rule (its baseline entry is gone).

### Notebook redesign 1: fixes from the owner's test of v0.5.0 (10 November 2026)

- **Lists of files in a message draw no empty bullets.** `notebook_style.path_list` returns
  plain lines in a box (scrolling above about ten lines) instead of a nested list, and a status
  card joins a list of paths to the line before it. The same paths are shown, folded behind
  "Show N more" as before.
- **"Is compared against" keeps its dropdown.** Choosing an option rebuilt every dropdown of the
  card (twice), so the control vanished while the answer was saved. The rows are now built once
  per set of conditions and updated in place; the answer, its refusal and the saved draft are
  unchanged. Measured on 28 images of 2048 x 2048: 0.58 s to 0.35 s per choice (the rest is the
  refresh of the other pages).
- **Every disabled action says what blocks it, in a visible line beside it**
  (`notebook_render.blocked_line`): *Confirm axis roles* (the axis error by name, the preview
  not drawn, the preview not ticked), *Yes, the preview looks right*, *Compute histograms*,
  *Yes, save the experiment description*, *Make named-axis copies* and its roles question, and
  the stains confirmation. Nothing about when an action is allowed changed. Roles that are not
  a mapping at all (two X) now also disable *Confirm axis roles* at once.
### Normalization pinned at v0.4.0 (#262)

- **`morphobricks-normalization` moves from v0.3.4 to v0.4.0** (tag `c952263`). It is
  breaking for the table: `PlaneRecord.position_id` is required and keyword-only, a blank
  `position_id` in a CSV row is refused with `CsvFormatError`, and tables of schema versions
  1 to 3 are refused. On valid inputs its output is bit-identical, so `plane_histograms.csv` is
  unchanged byte for byte.
- A table without position identifiers is no longer filled in without reading: the reader
  refuses it and the run rebuilds every row ("the earlier histogram table could not be read").
  The dead fill-in in `run/steps.py` is removed, and `run/rows.py` takes `position: str`.
- Tests build every `PlaneRecord` with `position_id=`; the README example does too. The
  `RELEASE_REF` install still pins v0.3.4 and is unchanged (the v0.5.1 release moves it).

### The notebook has three code cells: install, mount Google Drive, run

Owner request of 10 November 2026, as in the lab's ZeroCostDL4Mic and NucleiSky notebooks. The
single code cell of `notebooks/MorphoBricks_Preprocessing.ipynb` is split into three, each under
a Markdown heading that explains it; what the cells do together is unchanged (`RELEASE_REF`,
the install target and `PreprocessingNotebook(describe=True)` stay as they were).

- **1. Install MorphoBricks preprocessing**: the pinned install with its extras, the version
  report and `SETUP_RECORD`, as before; it no longer mounts Drive. Running it again installs
  nothing that is already there; no restart is requested.
- **2. Mount Google Drive**: mounts Drive on Colab (the same refusal when the mount fails); in
  JupyterLab or LabConstrictor it prints a one-line note and does nothing.
- **3. Run preprocessing**: shows the app. Run before cell 1 it stops with "run cell 1 (Install
  MorphoBricks preprocessing) first", not an `ImportError`; on Colab without Drive at
  `/content/drive/MyDrive` it stops naming cell 2. It never runs an earlier cell.
- Tests: exactly these three cells in order and each one's part (`test_notebook_file.py`), the
  cells out of order and cell 1 run twice (`test_notebook_setup.py`), and the notebook in a
  real kernel with and without a faked `google.colab`, and cell 3 alone.

### PP-C: every run is described; undescribed runs and their compatibility code removed

No backward compatibility before the first pipeline release (owner policy of 10 November 2026,
MorphoBricks-Spec ADR 0002 AR10 and AR11, Stage 2 plan §0.1 and §0.2). **Breaking.**

- **Every run needs a published experiment description.** `run_experiment`,
  `prepare_mask_step`, the command line's `check`, `run` and `masks`, and `RunInputs.open` refuse
  an output folder without `morphobricks.toml` with the one code `A_DESCRIPTION_REQUIRED`
  (owner ruling 1), before anything is read or written, naming the fix: the notebook's describe
  steps (*Check and save*), or the command line's `describe` to see what is open. The refusal
  comes before the recorded run or any image header is read (also for an output folder inside
  the experiment or the masks, which is said beside it) and, on the command line, before
  `--log` is opened or `--remove-stale-lock` applied. New:
  `required_description` (`is_published`, `description_required_issue`, `undescribed_issues`) and
  `DescriptionRequiredError` (a `RunInputsError`, exported). Contract §0.
- **Removed: the undescribed ("v0.4") run.** Its `NO_DESCRIPTION` warning; the
  `with_description` argument of `preflight`, `remasked` and `masks.find_masks` and
  `run.preflight.description_in_use` (the name refusals `R_FOLDER_NOT_NFC` and
  `R_MASK_TYPE_NAME_INVALID` always apply); the `None` answers of `RunInputs.description()`,
  `section_digests()`, `images_without_header_channel_names()`, `references()` and
  `mask_semantics()`; `make_clean_run`'s undescribed run (below).
- **Removed: `experiment_design.csv` as an input to grouping.** `run_experiment(design=...)`,
  `run --design FILE`, the `design` command (`cli_design.py`), `run.design_file`
  (`write_design_template`, `write_design`, `edit_design`, `design_for_run`), `check`'s design
  template (`run.write_check_outputs`, `CheckOutputs`; `write_check_report` stays),
  `recorded.design_change` and `planned_changes(design=...)`, and the notebook's writes and reads
  of the file. The run now derives the design from the published description
  (`design_derive.derive_design`) and writes it with the table (`experiment_design_sha256` in
  the manifest): an output only, for every run. Stays until SWEEP-PP: the lenient reader
  `read_experiment_design` (only the one-way draft migration `migrate_legacy_design` uses it),
  `ExperimentDesign`/`default_design`/`apply_design_edit` behind the notebook's pairing card
  (owner answer 4), and the strict `read_confirmed_design`/`RunInputs.design` until CellPatcher
  and MorphoFeature read `repeat_group` from the description (owner answer 3).
- **Identities.** No identity is taken from a folder name in place of the description: the
  run's grouping comes from the description's repeats. `position_id` stays the image file stem,
  always together with the canonical condition, repeat and technical-repeat IDs (owner ruling 2;
  no change).
- **Run manifest: version `"4"` only** (owner ruling 16). Every run writes version 4 (with
  `experiment_description`; content hashes exactly when `verify_content`), and only version 4
  is read: `"1"`, `"2"`, `"3"` (and any later version) are refused with "Run preprocessing
  again", never converted; the next run reads every image again. Removed:
  `MANIFEST_VERSION = "2"`, `STRICT_MANIFEST_VERSION`, `DESCRIBED_MANIFEST_VERSION`,
  `LEGACY_MANIFEST_VERSION`, `version_for`, the `manifest_before_v040` source issue and the
  version-1 branches of row reuse, recorded inputs and `RunInputs`. The tolerant reading of
  version-1 file fields (`manifest_fields`' `strict=False`) goes in SWEEP-PP.
- **Notebook: always described.** `PreprocessingNotebook` has no `describe` argument and every
  `_describe` branch is gone: the labels toggle and the global *Image type* card are never
  shown (the type comes from the saved description), and the design card no longer reads or
  writes `experiment_design.csv` (its undescribed `_open_design`, `_save_design`,
  `_design_load_note`, `_refresh_design_status`, `_design_pending` and the check's design
  template are removed). The widgets of the retired cards stay until SWEEP-PP. The embedding
  API keeps `folder_step`, `axis_step`, `run_step` and `show_design`; an embedding brick must
  now let the user describe and save before the run (below). **The shipped notebook is
  unchanged** until the v0.5.1 release: it installs `RELEASE_REF` (v0.5.0, `fd7afd3`) and calls
  that code's `PreprocessingNotebook(describe=True)`; the release PR moves `RELEASE_REF` and the
  call to `PreprocessingNotebook()` together (`test_notebook_file.RELEASE_CALLS`, the release
  check list).
- **Refusal texts.** `design_not_confirmed`, `repeat_group_conflict` and
  `repeat_missing_conditions` name the notebook's describe steps / *Replicates* page and
  *Check and save*, not the removed `design` command or an edit of `experiment_design.csv`.
- **`testing.make_clean_run` makes a described run.** Same tree and FOV keys as before
  (`ctrl`/`drug`, `rep1`, `a`/`b`); it publishes `testing.clean_description(...)` (channels
  `ch1`..`chN`, one `instances` mask per mask type, `design=True` makes `drug` reference
  `ctrl`), writes the derived `experiment_design.csv` every time, and runs the mask step when
  there are masks, so every mask is `checked`. New: `testing_described.publish_and_run`.
- **Tests.** The undescribed-run tests now run described trees (`tests/described_tree.py`:
  `run_described`, `main_described`, `describe_tree`, the description the user would confirm
  for a tree the test wrote, every proposal accepted; never part of the package). Deleted as
  undescribed- or legacy-only: `test_parity_v0_4_0.py` and `parity_datasets.py`,
  `test_removed_in_v040.py`, `test_design_command.py`, `test_strict_manifest.py` and
  `test_described_manifest_version.py` (replaced by `test_manifest_version.py`),
  `released_code.py`, `tests/data/rc4_run_manifest.json` and `make_rc4_manifest.py`, and the
  single tests of other files that checked only undescribed or legacy behaviour (listed in the
  pull request). Kept as science goldens and renamed (owner ruling 6):
  `test_schema_v2_equivalence.py` is `test_histogram_goldens.py` (`histogram_goldens.py`,
  `tests/data/golden_*`, `make_histogram_goldens.py`), its version-2 manifest test deleted. CI
  no longer fetches the whole history (`fetch-depth: 0` was only for the old-code runs).

**Downstream** (each brick is unaffected until its pin moves past this change): every brick's
test runs must be described (`make_clean_run` does it; a direct `run_experiment` call needs a
published `morphobricks.toml` first), `run_experiment(design=...)` and the `describe` argument
of `PreprocessingNotebook` are gone, and a run written by v0.5.0 or earlier is refused until it
is run again.

### PP-0: `RunInputs.resolve_request`, the shared request resolver (#256)

Resolver contract version 1 (`RESOLVER_VERSION = 1`; MorphoBricks-Spec ADR 0002 AR1-AR11,
contract §9.1 and §16). **API additions only.**

- **One interpreter of a described run for every analysis route** (a brick's direct run and a
  run through MorphoFeature alike). A `Request` (canonical channel IDs; masks by ID with a role,
  `objects` or `region`, and, for a `binary` mask used as objects, the explicit
  `binary_mask_policy`; `plane` or `volume`; the consumer's `Capabilities`, `rgb_support` and
  `volume_support`, both False by default; the explicit `device`, `cpu` or `cuda`) resolves to
  an immutable, picklable `ResolvedRequest`: one `FovSelection` per position with the C index of
  every requested channel **in that image**, the masks as `MaskSelection`s, the planes to read
  and the canonical IDs beside the unchanged key; every applicable refusal at once
  (`A_SCOPE_INCOMPLETE` "present in k of n acquisitions", `A_MASK_UNCHECKED` "k of n files",
  `A_MASK_NOT_INSTANCES`, `A_RGB_NOT_SUPPORTED`, and the contract's own
  `A_VOLUME_NOT_SUPPORTED`); the coverage of every biological repeat per channel and mask
  (`full`, `partial`, `unavailable`), refused or not; and the `Provenance` (the resolver
  version, the description's SHA-256 and section digests as the manifest recorded them, and the
  request with its device). A refused request selects nothing; `require()` raises the first
  refusal's error class with every refusal in its text.
- **It only classifies.** It reads the manifest, the published description (checked against
  the run's SHA-256) and `mask_representation.json`; it reads no pixel, runs no kernel, writes
  nothing, converts no mask (a recorded policy comes back as `MaskSelection.conversion` for the
  route's shared labeller), checks only the device's spelling (the consumer refuses `cuda`
  without a GPU) and imports no brick, CellPatcher or provider module, directly or through the
  package (tested in a fresh interpreter). A malformed request is a `ValueError`: an ID twice;
  an unknown role, policy, plane kind or device; a policy on a mask that is not `binary` or in a
  `region` role; channels or masks not a tuple (of `str` IDs, of `MaskRequest`), or a capability
  that is not a `bool` (the request is recorded as given, so it must be immutable).
- **Every run it sees is described** (AR10): a run without `morphobricks.toml` is refused by
  `RunInputs.open` with `A_DESCRIPTION_REQUIRED` (PP-C), so the resolver has no undescribed
  branch.
- `FovIdentity` gains the unit's `image_type`; `Fov` is unchanged. New exports:
  `RESOLVER_VERSION`, `Request`, `MaskRequest`, `Capabilities`, `ResolvedRequest`,
  `FovSelection`, `MaskSelection`, `PlaneSelection`, `Refusal`, `RepeatCoverage`, `Provenance`,
  `VolumeNotSupportedError`. Suite: `tests/test_spec_resolve_request.py`, a Spec module
  (`spec_gate.EXPECTED_MODULES`).

### PP-1: `Fov.image_type` per position, the partial-mask test helper, the pinned surface

Stage 2 plan row PP-1 (MorphoBricks-Spec ADR 0002 AR8, contract §9.1 and §16). **Breaking** for a
reader of `RunInputs.image_type()`.

- **`Fov.image_type`: each position's declared type.** `"rgb"` or `"channels"` (a unit that
  declares no `image_type`, experiment-v1 §4.4b), from the published description only, never
  from a header, and never None on a `Fov` of `fovs()`. One source: `FovIdentity.image_type`
  (`fov_identity.declared_image_type`), which `Fov` and the resolver's `FovSelection` both carry,
  so the two always agree; `resolve_request` reads the position's `Fov.image_type` instead of
  looking the unit up again. `FovIdentity.image_type` and `FovSelection.image_type` are now `str`
  (`"channels"` where PP-0 gave None; resolver contract still version 1, unreleased before
  v0.5.1); `run_inputs_request.CHANNELS` and `IMAGE_TYPES` name the two values.
- **Removed: `RunInputs.image_type()`.** It returned the manifest's run-level `image_type` (the
  command line's review, null on every described run), a run-wide value that would hide a
  unit's own type. Read `Fov.image_type` (or `FovSelection.image_type`). The manifest field
  stays as a record. Downstream: CellPatcher reads `inputs.image_type()` today
  (`run_open.py`, `normalization.py`, `notebook_session.py`); its v0.5.1 pin (CP-4) moves to
  `Fov.image_type`.
- **`make_described_run(..., masks=IDS, mask_images=PATHS)`** writes the masks of `IDS` for the
  named images only (relative paths, as the new `testing.described_images(description)` lists
  them), so a run with partial masks, such as Spec `v09` with `Nuclei` on 2 of 6 images, is
  built as a real run; `None` (the default) is every image, as before. Explicit only: nothing
  is chosen at random; a path outside the tree, an empty subset, a single text or a subset
  without masks is a `ValueError` before anything is written. `write_described_tree` takes the
  same `mask_images`.
- **The mask step's status file is parsed once per content.** `read_checked` still reads
  `mask_representation.json` on every call but reuses the parse of the same bytes
  (`lru_cache` keyed by the bytes, as `description_consume` already does for the description),
  so the result is the one an uncached read gives and a rewritten file is parsed again;
  `RunInputs` holds no memo and pickles as before. Measured on a 10,000-position `v09` run with
  `Nuclei` masks (one machine, best of runs): `mask_check_status` for 1,000 positions 6.6 s
  before, 0.85 s after, the same 1,000 answers and the same `resolve_request` result. The
  description itself was already parsed once per content (`description_consume`), so it needed
  no memo. Left as found (a later item): `resolve_request` takes about 3 s on that run and
  `mask_check_status` for all 10,000 positions about 12 s, both from linear scans per file
  (`file_status` over the status file's list, `_mask_entry` over the manifest's), not from
  reading the description.
- **The pinned surface.** `tests/test_brick_api_surface.py` pins the names, fields, types and
  signatures the bricks use (`RunInputs`, `Fov`, the resolver records, `resolve_request`,
  `make_described_run`, `make_clean_run`, `described_images`, `RESOLVER_VERSION`); contract
  §9.1 lists them. New Spec module `tests/test_spec_fov_image_type.py` (`v28` rgb on every
  position; `v13`, `v01` channels; `v29`'s two units through the identity; `v09` on 2 of 6
  through the helper equal to PP-0's hand-built tree), in `spec_gate.EXPECTED_MODULES`; PP-0's
  `v09` test now uses the helper.

### PP-2: lab-tool links, recorded in the run manifest only

Stage 2 plan row PP-2 (owner decision of 9 November 2026; MorphoBricks-Spec experiment-v1 §9.3,
conformance plan §2 "Provenance" row, its lab-tool-links part checked at the Stage 2 gate per
ADR 0001 GS1; contract §9). **API and CLI additions only.**

- **`run --lab-links FILE`**: a UTF-8 JSON object with exactly `repository_url`, `commit` and
  `experiment_path` (non-empty text, nothing trimmed; `commit` a full lowercase SHA of 40 or 64
  hexadecimal characters) and optionally `repeat_codes` (each lab-tool repeat code, such as
  `R001`, to a canonical biological repeat ID). An unknown key, a missing one, a value of
  another type, an empty value, outer whitespace or a control character, or a key given twice
  in one object is refused before anything is read (`lab_links_invalid`). So is a
  `repository_url` holding a credential (`user:password@` in any form, or a user name before
  `@` outside an SSH remote: `https://TOKEN@host/...`), a query string or a fragment; the
  refusal does not repeat the URL, and a credential is never written to the manifest
  (coordinator decision on PR #264; `git@host:lab/db.git` is kept). API:
  `run_experiment(mode=RunMode(lab_links=...))` with `LabLinks` or `read_lab_links` (new
  exports `LabLinks`, `LabLinksError`, `read_lab_links`; `RunMode` gains `lab_links`, because
  `run_experiment` is at its parameter limit).
- **Repeat codes need the user's confirmation** (`--confirm-repeat-codes`;
  `LabLinks(repeat_codes_confirmed=True)`, `read_lab_links(confirmed=True)`), never read from
  the file: equal strings are not evidence (§9.3). The run prints each pair. A confirmed
  mapping must name every biological repeat of the published description exactly once and no
  other ID, compared exactly. Run errors, reported together before any pixel is read:
  `lab_links_repeat_codes_not_confirmed`, `lab_links_repeat_unknown`, `lab_links_repeat_twice`,
  `lab_links_repeat_missing`.
- **Recorded verbatim in `run_manifest.json` under `lab_links` only** (left out without links,
  so such a manifest is byte for byte as before; the manifest version stays 4). Never in
  `morphobricks.toml` (`i99` stays `E_FORBIDDEN_FIELD`), and in no section digest, description
  SHA-256, row or staging key, mask-step check, design, table or report: the manifest's own
  SHA-256 is the only other value that changes (the report records it). Not a recorded input:
  a later run not given links records none.
- **No notebook input**: neither the Spec nor the plan asks for one; the CLI and the API only.
- New Spec module `tests/test_spec_lab_links.py` (`v03`, `v08`, `i99`; in
  `spec_gate.EXPECTED_MODULES`): the same run with, without and with changed links, in
  `restart` and `full` mode, publishes the same files apart from the links block, reuses every
  row, and keeps the four section digests, the description SHA-256, every row key, the
  resolver's provenance and the mask step's check; unconfirmed, unknown, missing and
  wrong-case repeat codes are refused; `i99` is still refused with links given. The input's
  own checks and the CLI: `tests/test_lab_links.py`.

### SWEEP-PP: the remaining compatibility code removed

The obsolete-code sweep of this repository (MorphoFeature `docs/restart/designs/obsolete-code-inventory-2026-11-10.md`
§1 and §12, Stage 2 plan §3.7; owner policy of 10 November 2026, Spec ADR 0002 AR10). What PP-C
already removed is not repeated here. **Breaking** where it says so.

- **Tests: the frozen clock without the `notebook` extra** (#262). `tests/frozen_clock.py`
  skips a module whose import fails only because ipywidgets or matplotlib is missing (any
  other import error is raised), so a plain `pip install -e '.[dev]'` passes the frozen-clock
  tests (`tests/test_frozen_clock_extras.py`).

- **Removed: `GroupSummary.plane_count`** (inventory PP-07), the deprecated alias of
  `channel_histogram_count` from before #104; use `xyc_plane_count` (XYC planes) or
  `channel_histogram_count` (channel histograms pooled). No caller in the nine repositories.

- **Removed: `source_fingerprint`** (inventory PP-08, owner answer 7) from `inventory`, its
  re-export from `manifest` and the package's top level. It was kept "for callers" that compared
  size and modification time only; no source caller in the nine repositories. Use
  `inventory.source_signature` (size, modification time and the OME-Zarr inventory digest).

- **Conversion record: version 3 only** (inventory PP-06). **Breaking.** A
  `.morphobricks-conversion.json` of any other version (2 was still read as a tree without a
  projection; 1 had its own message) is refused by `convert`, the run's `review_conversion` and
  `RunInputs` with `conversion_record_unreadable`, naming its version and the fix: convert the
  originals again into a new folder. The record's and each entry's `projection` key is required
  (version 3 always writes it). Removed: `_READABLE_VERSIONS`, `_OLD_VERSIONS` and the "version 2
  cannot hold a projection" check.

- **Run manifest: nothing read leniently** (inventory PP-05, owner ruling 16). **Breaking** for a
  hand-edited or truncated manifest only (version 4 always writes these keys). Removed: the
  `strict` argument of `manifest_fields.recorded_position_id` (version-1 entries kept any value,
  or None), the `strict` branches of the file-entry reader and writer, and the defaults for
  absent keys: a file entry needs `position_id`, `roles`, `axes_from`, `pixel_sizes`,
  `singleton_roles`, `channel_names`, `significant_bits`, `read_in_blocks` and
  `channel_names_trimmed`, the settings `histogram` and `verify_content`; a manifest without
  one is refused (`ManifestFormatError`). `recorded_sizes.sizes_from_data` refuses an absent
  list (also in a conversion record). `check_sources` no longer has the
  `position_id_not_recorded` issue (an entry without a position is `identity_differs`) and no
  longer resolves roles from the image header for an entry without them (it fits no axes,
  `axis_mapping_mismatch`).

- **Docs: `docs/migration-0.3-to-0.4.md` removed** (inventory PP-10): no run of v0.3 or v0.4 is
  read any more. README, user guide and this file no longer point to it. (The rest of PP-10, the
  v0.4.0 parity tests, `test_removed_in_v040.py`, `released_code.py` and the CI's full-history
  fetch, went in PP-C; `test_schema_v2_equivalence.py` was renamed `test_histogram_goldens.py`
  there.)

- **`experiment_design.csv`: an output only, nothing reads it back; the notebook's pairing card
  rebuilt on the draft** (inventory PP-02, owner answers 3 and 4; Spec ADR 0002 AR11 (3)).
  **Breaking.** Removed: `read_confirmed_design`, `ConfirmedDesign`, `DesignCheckError` (its
  codes `missing_experiment_design`, `unreadable_experiment_design`,
  `changed_experiment_design`), `RunInputs.design()`, the lenient `read_experiment_design`,
  `load_design`, `DesignChanges`, `default_design`, the editor model `ExperimentDesign` (its
  `with_group`, `with_confirmed`, `with_control`, `dated`), `design_edit.py`
  (`DesignEdit`, `DesignEditError`, `apply_design_edit`, `parse_repeat_group`), and the unused
  `report.design_summary`/`design_table`. The derived rows are now a read-only `DesignTable`
  (`design.py`): the run's check against the folders (`reconcile_design`, which returns the
  rows only; `design_issues`), `write_experiment_design`, `DESIGN_COLUMNS`, `design_derive`
  and the manifest's `experiment_design_sha256` stay, and `RunInputs.open` still checks the
  file's SHA-256. `parse_design_bytes` (strict) stays for the draft's experiment-v1 §7
  migration of a legacy file. A brick takes the repeat groups from the description
  (`Fov.repeat_group`, `resolve_request`). *Check and save* compares the candidate's derived
  rows with those of the published description, not with the file in the output folder
  (`check_candidate` lost its `output` argument). **Notebook:** the pairing card shows one row
  per folder of the draft's base, with the repeat it is paired with
  (`PreprocessingNotebook.pairing`, replacing `.design`); a change is a draft answer at once.
  Removed with the retired card parts: the hidden comparability questions
  (`group_confirmations`, `groups_box`), the hidden control boxes (`controls_box`,
  `control_checks`, `_sync_controls`), the *Change the experiment design* button, and the dead
  `_design_pending`/`_design_items`/`_design_confirmed` gates. The shipped notebook file is
  unchanged (it installs its `RELEASE_REF`). **Downstream** (pins hold until moved):
  CellPatcher (`normalization.py`, `config.py`, `setting_checks.py`, `file_runs.py`, CP-1) and
  MorphoFeature (`experiment_design.py`, `fits.py`, MF-4) must read `repeat_group` from the
  description before their pin passes this change.

## 0.5.0 - 2026-11-09

Stage 1 of the experiment description (MorphoBricks-Spec experiment-v1, pinned at `1bed024`):
the reader, the canonical writer, publication, reconciliation, the described run, semantic
`RunInputs` and the notebook that writes `morphobricks.toml`. Runs without a description keep
the v0.4.0 behaviour (with a warning). The release step: the version is 0.5.0, the notebook
installs `v0.5.0` (its `RELEASE_REF` is the release-preparation commit), and the Gate G1 record
of this release candidate is in `docs/conformance.md`. The normalization brick stays pinned at
v0.3.4. It also carries the audit batches 33, 34a and 34b (#254, #255) and their
pre-release follow-ups (#257).

- **Fixed (final review of the release candidate): two confirmations the user did not give.**
  (1) *A header refusal lifted by an answer about another cell.* Tagging any channel of any
  folder recomputed every label's `header_names` from every saved tag, so after images were
  rewritten with other header names (for example two channels swapped) the
  `R_HEADER_CONTRADICTION` of folders the user had not tagged again vanished, *Check and save*
  said the images agree and the run wrote the table. Now a tag confirms the header name of the
  cell tagged, and nothing else: names no tagged cell has any more are dropped, and a name is
  added only for the cells of that answer. The refusal stays until the channel it names is
  tagged again. (2) *A "same way?" kept for other folders or another layout in a draft.* With
  nothing published yet, the answer saved in `morphobricks.draft.json` was kept when a folder
  joined or left the replicate before the next folder check, and when a tag or an image type
  changed after it, and it was published as the user's confirmation. Now it is asked again in
  both cases, as it already was against a published file (experiment-v1 §10); the names card
  says which replicate is asked again, and a replicate tagged back to the layout it was
  answered for keeps its answer.
- **Fixed: `R_FOLDER_NOT_NFC` and `R_MASK_TYPE_NAME_INVALID` on the production paths
  (conformance P21, P29).** They were raised only by `preflight(..., with_description=True)`,
  which no production caller passed, so a run with a published `morphobricks.toml` refused a
  decomposed folder only as `R_UNIT_NOT_DECLARED`, and the mask types `NA`, ` cells` and a
  decomposed name only as `R_MASK_TYPE_NOT_DECLARED`. Now `run_experiment`, the mask step
  (`prepare_mask_step`, the command line's `masks`) and the command line's `check` and `run`
  pass `with_description` whenever the output folder holds a published description
  (`run.preflight.description_in_use`); the notebook's folder check and its re-pairing of the
  masks also pass it while the user describes the experiment, and *Check and save* is blocked
  while either refusal stands; the command line's `describe` refuses a folder label that is not
  NFC instead of drafting it. Nothing is renamed or normalized. A run, mask step or check
  without a published description is unchanged (owner decision Q1).
- **Pre-release follow-ups to #254 and #255.** A replicate's "prepared and imaged the same
  way?" is asked again when the candidate description changes its layout against the published
  one, as experiment-v1 §10 says ("a unit's ordered channel list", a new or removed unit or
  member, the pairing): at the reopen, and as soon as an answer changes a unit's declared
  channel order, image type or grouping. Only that replicate's `confirmed_on` is taken back;
  every other one is kept byte for byte (§4.4a). Image headers that no longer fit the declared
  channels (`channels_changed`) are the run's refusal (`R_HEADER_CONTRADICTION`), shown at once,
  not a reopening: a header rename fixed by tagging the channel again (its new header name joins
  the channel's `header_names`) keeps the date and saves. Ticking "same way?" for a replicate
  whose layout is as published takes the published date back (§10: "the draft copies it from
  the published file, never from the clock"). *Check and save* runs publication's own
  transition checks (`R_CONFIRMED_ON_CHANGED`, `R_CONFIRMED_ID_CHANGED`, `R_USED_ID_*`) with the
  same re-confirmations, so Save is disabled, with the reason, for a candidate that publication
  would refuse. `describe` (command
  line) reads the image headers (no pixel) when it compares a published description and
  reports a header-only change and the replicate to confirm again, as the notebook does. Save
  stays allowed when the run's checks would refuse (owner decision, 2026-11-10); a line beside
  the button says that the run will stop, with each refusal and its code. The step-by-step
  "Yes, the preview looks right" is offered only while a preview of the current roles is shown
  (a preview that fails takes an earlier tick back), as the one-click *Confirm these axes* is.
  The tag pictures (`channel_strip`), their 1:1 crop and the *Images found* picture check the
  bound fingerprint after the read too: a file rewritten during the read is not shown. A unit's
  channel count comes from every image of the unit: images that disagree (or one that cannot be
  read) leave it unknown (`unit_channel_count_unknown`), never the first image's count.
  "The run (step 12) can start" is said only once the run's checks have run and passed; before
  the axis roles are confirmed the screen says to confirm them first. A refusal is not hidden
  behind a missing answer: while answers are missing, *Check and save* says at once that the
  run is blocked by both, the answers and the run's refusal of the saved description (its codes
  and folders), or, before the axis roles are confirmed, the header change the folder check
  found. A plane read across a rewrite is not kept in the preview cache. **API changes:**
  `published_answers.reopened(draft, answers, published)` compares the declared layouts (units
  with their ordered channels and image type); `published_answers.layouts`,
  `published_layouts`, `reasked` and `kept_date`, `run.description_file.transition_refusals`,
  `description_candidate.transition_issue`, and `check_candidate(..., published=,
  confirmations=)` are new; `UnitLayout.counts_agree` and `channel_units.unit_channel_counts`
  are new.

- **Audit batches 33 and 34a: what is published is what was answered, of the image checked.**
  *Yes, these are right* saves exactly the tags a block shows (a hidden proposal is never
  saved), and a unit tagging one label twice is refused before it is saved
  (`E_DUPLICATE_CHANNEL_IN_UNIT`). An unanswered image type is an open question
  (`unit_image_type_unanswered`; "channels" is still published as an absent `image_type`).
  `publish_draft` now requires `channel_counts`, each unit's channel count read through the
  confirmed axis roles: tags saved for another count (`unit_channels_stale`) or a unit without
  a count (`unit_channel_count_unknown`) are refused. The notebook's folder check binds every
  header to its file (`bind_headers=True`); the tag pictures, the Step 1 pictures and the mask
  evidence are drawn only of that revision (masks paired again without a new check are bound
  too, and evidence already read is not shown once its image or mask changed), and the save is
  refused when an image changed since the check. The axis step's preview is not bound yet.
  *Check and save* shows tags saved for another channel count as stale, not as the folder's
  channels. An answer the draft file cannot take (lock, write error) is
  refused and not shown as answered. `make_described_run` refuses a member with no technical
  repeat or fewer images than technical repeats before writing anything. A new record the
  records reader would refuse is never written (`R_RECORDS_FILE_INVALID`), and a run refused
  for its records file reports that code with its `E_RECORDS_*` reasons instead of
  `description_unreadable`. `all_colour` counts a unit only when its tags are exactly R, G, B.
  **API changes:** `publish_draft(..., channel_counts=...)` is a new required keyword;
  `missing_confirmations` and `description_from_draft` take an optional `channel_counts`;
  `header_preview(..., bound=...)` is a new required keyword; `mask_evidence` takes
  `options: StripOptions, bound` in place of `cache, memory_limit`; `Thumbnails` takes the
  check's `header_fingerprints`. A draft saved before this change with no image type reopens
  with the image type asked, never as "channels".
- **Notebook: folder pictures with a 1:1 crop, "Tag like the saved group", colour mask evidence
  (PR 17c).** *Images found* adds a picture of each folder's first image to the labels table,
  from the file header only and display only (`header_preview`, `notebook_folder_previews`):
  drawn only when the header names Y and X ("preview after Axes (step 2)" otherwise), every
  other axis max-projected and named, colour for an RGB header, at most 8 planes and
  4096 x 4096 pixels read per picture (a larger image says so), a read failure written in its
  cell; the check draws the first 12 folders and a button draws the others. A thumbnail
  downscaled more than 2x gets a crop of 128 native pixels where the thumbnail varies most
  (deterministic, topmost then leftmost, with its x/y range; `preview_crop`), in one shared
  tile with **Enlarge** (`notebook_thumb_tile`), also on *Replicates* and *Check and save*.
  On a reopen, a block of new folders with exactly a saved block's header offers
  **Tag like the saved group (…)**: the click writes the same draft bytes as tagging by hand.
  The mask evidence of an RGB folder is drawn on its colour picture.
- **Notebook: unchanged rows fold on reopen (PR 17b-2).** When a saved experiment is reopened, a
  row whose folders are found exactly as saved and whose question is answered folds behind one line
  ("3 unchanged folders, answered as saved") with an **open** toggle: the pairing, the condition,
  replicate and technical-repeat names, the image types, the tag blocks and the replicate blocks of
  *Check and save*. A row with a new folder, a folder whose image count changed, or one whose
  header channels no longer fit the saved channels, stays open and is marked orange ("new since
  saved", "changed since saved"); a row with a warning never folds. Display only
  (`notebook_row_fold`): the answers, the draft, the save and the run are unchanged.
- **Notebook: tags per header block, colour pictures, one-click axes (PR 17b-1).** *Tag channels*
  shows folders whose image headers are identical, exactly (channel count, every raw header name
  present and equal, colour flag), as one block tagged once for all its folders (owner decision Q5),
  with every folder's pictures below for checking; a header lacking a channel name, other names or
  other saved tags keep folders apart, and **Tag this folder on its own** takes one out. A block
  answer writes the same per-folder answers into the draft as tagging one folder at a time (tested on
  the draft bytes). The colour pre-tick is confirmed only on *Image type*; *Tag channels* restates
  it. A colour (RGB) folder is drawn as one colour picture (channel 1 red, 2 green, 3 blue, each with
  its own percentile contrast; display only) on *Tag channels*, *Image type* (which now has a picture
  per folder), the pairing and *Check and save*: `preview_render.colour_composite`,
  `Thumbnails.colour`. The axes step offers complete, unambiguous roles (no axis message, every image
  naming its axes, nothing missing to state) with a preview and one **Confirm these axes** click,
  which records what the two clicks of the role boxes record (owner decision Q3); an unchanged reopen
  offers the earlier run's roles and then opens its next open step. Pixel sizes say *uncalibrated*
  on the chip and in the summary when no physical size is found. Replicate IDs are shown in natural
  order (`rep2` before `rep10`, the folder breaking a tie) in the run plots and legends, the
  replicate blocks, the names rows and the short names: display only. Texts no longer name the old
  steps 1-4 (the results page, the design card, the mask step, the design messages).
- **Described runs with several layouts report `R_HETEROGENEOUS_LAYOUT_NOT_ENABLED` first (#245).**
  The run lists it before the axis review's issues, and the arrangement issues
  (`inconsistent_mapping`, `missing_axes`, `ambiguous_missing_axes`) no longer advise exporting the
  images the same way: they stay errors and say they follow from the layouts. The notebook's axis
  step says that no roles are suggested because the units declare different layouts (fluorescence
  and RGB). A previously blocking input stays blocked; diagnostic codes may change (a described
  run reports the per-type checks and the Spec's codes in place of the global `mixed_dtypes` and
  `inconsistent_dimensions`).
- **Notebook: one step at a time (UX proposal 2026-11-09, PR 17a).** `PreprocessingNotebook.steps()`
  shows the shipped notebook's 13 steps one at a time, with a strip of chips (done green with a tick,
  current blue, to do grey, changed by a reopen orange; a chip opens its step) and Back/Next (Next is
  never blocked: the save and the run still refuse what is missing). The order is the owner's: images
  found, axes, pixel sizes, conditions, replicates (pairing with the replicate and technical-repeat
  names, then the consistency card only when it warns), compared against, image type (its own page;
  the tag grid has no type column there), stains, tag channels, masks, check and save (starting with a
  one-line overview of steps 1-10, each with *change*), run, describe the masks. Pure composition: the
  cards, answers, draft, save and run are unchanged; a reopened experiment opens on its first step
  still to do (the axes, confirmed once per session). The notebook file shows `notebook.steps()` and
  `results_step()`; `folder_step()` and `axis_step()` render as before for embedders. Text: no RGB
  sentence on a described run's axes page, no broken picture before the preview, "same way?" *above*,
  the reader and CSV lines of *Check and save* behind *Details*, "folder" instead of "unit", and the
  steps named by their new numbers. The run page's box plots and curves order the replicates of a
  condition by replicate ID (`rep1` before `rep2`), not by folder name.
- **Re-pin the vendored Spec to 1bed024 (MorphoBricks-Spec PR 16, owner decisions RF1 to RF6);
  `read_records` follows section 4.6.** The Spec now fixes the bytes and values of
  `morphobricks.records.jsonl` and names one refusal, `R_RECORDS_FILE_INVALID`, with the reasons
  `E_RECORDS_ENCODING`, `_NO_FINAL_LF`, `_NOT_JSON_OBJECT`, `_UNKNOWN_TYPE`, `_KEY_SET`, `_KEY_ORDER`,
  `_NON_ASCII_ESCAPED`, `_VALUE` and `_FORM`. `read_records` checked only the key set; it now refuses
  every file the Spec's reference validator refuses (new pure module `records_file`): key order at every
  level, escaped non-ASCII, duplicate keys, impossible dates and counts, upper-case or newline-ended
  SHA-256, an unchanged or empty count record, and any byte that is not the canonical encoding
  (`json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"`, what the writer appends, so every
  file the writer produces is still accepted). `RecordsFileError` now carries `code` and `reasons`.
  The vendored files add `fixtures/records/` (37 goldens) and the reference module `refrecords.py`
  (imported by `refval.py`, also pinned). `tests/test_spec_records_goldens.py` runs every golden
  through `read_records` against the catalogue and the reference validator. A draft saved under the
  previous pin is refused at resume with `spec_commit_mismatch`, as before.
- **Notebook: Step 3 run-page labels (UX proposal 2026-11-09, Q4).** In a described run the intensity
  plots name the channel tabs, titles and tab findings by channel ID (`DNA`, `ACTIN`, `PHASE`) instead
  of `C0`/`C1`/`C2`, and the box-plot axis and curve legends use the replicate ID (`rep2`) instead of
  the folder name (`day2`). The IDs come from the published `morphobricks.toml` only when its SHA-256
  is the one the run's manifest recorded (`run_labels.recorded_labels`); an undescribed run, an edited
  file, or a channel position whose units do not all declare one ID keeps `C<n>` and the folder label.
  The run tile says "no problems found" with a tick, not the warning sign. "What will happen" is hidden
  after a successful run (it planned that run) and returns when a setting changes. *Describe the masks*
  shows the declared kind (objects or regions) beside the suggested kind and has one *Mask* column
  instead of *Mask type* and *Mask ID* holding the same text.
- **Notebook: check and save, reopen, described runs (Stage 1 PR 16b).** `PreprocessingNotebook(describe=True)`
  (the shipped notebook) saves the experiment as `morphobricks.toml` before the run. *Check and save*
  restates it in sentences (what each condition is compared against, "compared against nothing" in
  words), shows each replicate with its folders, image counts, a safe picture and the channel chips
  (a label some acquisitions lack in orange, `A_SCOPE_INCOMPLETE`), and asks "Were these prepared and
  imaged the same way…?" once per replicate (the N35 answer, moved here from Step 1). The short names
  are listed ("never change once saved"); the exact canonical bytes go through the strict reader and
  the run's checks (heterogeneous layouts and the physical `R_*` checks said in words), and the
  derived `experiment_design.csv` change is listed by row. One click adopts every short name,
  publishes under the lock and writes `experiment_design.csv` — only then (`run.draft_file.publish_draft`
  now writes the derived CSV and takes `removals` and `recorded_on`). Run waits for it. Reopening:
  the published file is the answers (`published_answers`, retired IDs only in `used_ids`); nothing
  changed folds every screen behind "Saved on …, nothing changed. [Run] [Change…]"; new, removed and
  recounted folders are listed in orange; a removed folder needs *Yes, remove*; a replicate whose
  folders changed is asked "same way?" again. The draft carries the pairing. In a described run the
  labels toggle, the design card's "comparable" questions, its CSV writes, the check's design template,
  the names card's "Yes, use these IDs" and the global *Image type* card are retired; the pairing card
  shows a picture of each folder once the axis roles are confirmed. Without `describe` the v0.4 cards
  are unchanged.
- **Notebook: your masks (Stage 1 PR 16a).** A *Your masks* screen after *Your channels*, shown only
  when the folder check paired masks: one question per mask type, *separate objects, each with its
  own number* (`instances`) or *regions: inside or outside* (`binary`), with nothing preselected
  (experiment-v1 §4.5), and optional *made from* channels (declared labels only). Beside it: how
  many images have the mask, its pixel type, the number of different values in one image (read with
  the mask step's reader) and the mask on that image (PR 13 safe preview; drawn only as the user
  declared it, the mask alone in grey before an answer). The mask ID is the folder name when it is a
  valid mask ID (Token rule, reserved names, the object-set part `in`); otherwise the user types one.
  A declared mask whose files are gone is listed with *Remove*. Step 3's *Describe the masks* shows
  these IDs in a *Mask ID* column and no longer asks to name the masks. New: `mask_answers`,
  `mask_evidence`, `preview.labels_thumbnail`, `draft_ids` kind `masks`.
- **Notebook: your channels, labels first then tag per unit (Stage 1 PR 15, draft).** After the axis
  roles are confirmed, *Which labels did you image?* lists what was stained (prefilled only from
  header names that are valid IDs as they are and not generic such as `Ch1`, `C0`, `AF488`; near
  spellings are grouped for the user to decide, never merged; a case collision is refused at once),
  and *Tag each channel* shows one row per unit with a safe thumbnail, the quoted header name and a
  dropdown of the labels per channel, plus a per-unit image type with nothing preselected. Labels
  used in only some units say so (`A_SCOPE_INCOMPLETE`). Every answer is saved to the draft, which is
  now version 3 (a unit's `channels` may hold `null` for not tagged, never `""`: every position needs a declared label, e.g. `unused`;
  `image_type` may be `channels`; versions 1 and 2 are refused); contract §18.
- **Contract: the final mask check is as late as practicable, not a lock (#222, docs only).** Source masks are checked right before the manifest replace and ordinary concurrent edits are detected; the files are not locked, so a change between that check and the replace is not detected, and the manifest binds the bytes as they were at the final check (owner decision of 8 November 2026: documented, not snapshotted). `docs/contract.md` §9.4.
- **Notebook Step 1 cards: names, IDs, pairing and references (Stage 1 PR 14, draft).** After the
  labels are confirmed, *Names for your experiment* proposes the folder names as IDs (only where
  valid as they are), takes an optional shown-as name, and adopts the IDs only when the user clicks
  *Yes, use these IDs* (a refused ID is listed with the reader's code and message; nothing is
  confirmed). *What is each condition compared against?* takes the references (a loop is refused at
  once) and marks the referenced conditions as controls in `experiment_design.csv`. Every answer,
  the pairing of differently named folders and the date of each comparable-repeat click are saved to
  `morphobricks.draft.json`, which is resumed on reopening. The draft file is now version 2 with a
  `pairing` table (version 1 is refused); contract §18.
  On reopening, saved answers are kept for what still exists in the folders (a removed folder or
  condition is listed, never silently lost) and an unusable draft is copied to
  `morphobricks.draft.unused-N.json` before it is replaced (`run.draft_file.read_answers`,
  `draft_answers.carry_over`). References have an explicit "no comparison" answer.
- **Re-pin the vendored Spec to c8f6852 (MorphoBricks-Spec PR 15, owner decisions RC1 and SI1).**
  Text only: RC1 adds the optional boolean `rgb_support` key of a brick's provider manifest
  (absent or `false` is unsupported; a non-boolean is a manifest error), and SI1 makes a channel or
  mask that no image has the same `A_SCOPE_INCOMPLETE` refusal as one that some images lack.
  Both are behaviour of the bricks, not of the description reader, so no code here changes
  except `SPEC_COMMIT`. Fixtures and the reference validator are unchanged (the fixtures README
  alone differs). A draft saved under the previous pin is refused at resume with
  `spec_commit_mismatch` (see the draft file contract) and must be started again from the folders.
  Every fixture is exact and the cross-check against the reference validator has 0 differences.
- **The run side of the Z projection (part of #176).** A run on projected copies records a
  `projection` summary in its manifest (method, slices requested, and for every image the
  slices used, types, dtype rule, rounding and the source's content SHA-256, with the
  conversion record it comes from), `RunInputs.projection` returns it (None for an unprojected
  run, whose manifest is byte for byte as before), and the report says "max projections of
  slices 1:4". 2-D masks next to projected images are paired as usual and the run warns
  `masks_on_projected_images` (report and manifest); 3-D masks still stop the run
  (`mask_planes_mismatch`). `RunInputs.open` refuses a manifest whose `projection` is not
  what the conversion record gives, projected copies recorded without it, and a `projection`
  without a record: nothing is read as a plain image.
- **`projection_record_missing` (owner decision, 8 November 2026).** `check` and `run` read the
  OME `Image/Description` header (the first page's description tag, no pixels) of every
  analysed TIFF the conversion record does not list as `projected`. A description that begins
  "MorphoBricks Z projection:" in a tree with no record, or whose record does not list the
  image as projected, is an error naming the images and saying to rerun `convert --project`
  or restore the record. Plain images are unaffected.
- **`A_*` error classes and `RunInputs.section_digests()` (Stage 2 design PR 2, owner answer 2).**
  `ChannelNotFoundError` now has `code = "A_SCOPE_INCOMPLETE"`: one rule and one code for a channel
  no image has and for one some images lack; the message names the image and ends with the code.
  New `MaskNotInstancesError` (`A_MASK_NOT_INSTANCES`) and `RgbNotSupportedError`
  (`A_RGB_NOT_SUPPORTED`), both `RunInputsError` subclasses for the bricks to raise (preprocessing
  checks neither). The five analysis codes' classes (also `DraftRefusedError` and
  `MaskUncheckedError`) are exported at the package top level. `RunInputs.section_digests()` returns
  a copy of the manifest's `section_digests` of the description (None without one), read from the
  manifest only. Contract §9.1 and §16.
- **`make_clean_run(description=...)` builds a described run for the bricks' Stage 2 tests (Stage 2
  design PR 1).** `description` is a published experiment description (bytes, text or a
  `Description`). The helper writes the declared tree (`image_count` images per member, RGB colour
  for an `rgb` unit), writes the declared masks named in `mask_types` (now mask IDs in that case),
  publishes the bytes as `morphobricks.toml`, runs preprocessing and the mask step, and returns a
  `CleanRun` whose new `description` field holds the model (None otherwise).
  `testing.make_described_run(folder, description, masks=..., mask_step=...)` is the same with
  the mask step optional (masks left `not yet checked`). `axes`, `shape`, `pixel_size`, `design`
  and `seed` raise `ValueError` with a description. Tested on the Spec fixtures `v03 v08 v13 v28
  v30 v33 v34`: each builds, opens with `RunInputs`, and `fovs()` gives its condition IDs, repeat
  groups, technical repeat IDs, channel IDs and image count. Without `description`, behaviour is
  unchanged.
- **A described run writes manifest version `"4"` (Stage 2 design question 9, owner answer 9).**
  When the run manifest carries `experiment_description`, `manifest_version` is `"4"`; an
  undescribed run keeps `"2"` or `"3"`, byte for byte (the v0.4.0 parity suite is unchanged).
  A v0.4.0 reader skips keys it does not know, so it opened a described run and silently ignored
  the description (channels by header name, pairing by folder name, no mask checks); it now
  refuses version `"4"` (`ManifestFormatError`, tested with the code of the `v0.4.0` tag).
  `read_manifest` and `RunInputs.open` read version 4. They refuse a version 4 manifest without
  `experiment_description` and a version 1, 2 or 3 manifest with one; the writer applies the
  same rule. Version 4 keeps the content rule of 2 and 3: a `content_sha256` in every entry
  exactly when `settings.verify_content` is true. Contract §9 (`manifest_version`).
- **Images without header channel names are listed in a described run (follow-up of #218, owner
  decision 2026-11-08).** An image whose header names no channel, or not every one, still passes
  the per-unit header checks (§4.3, fixture v12: no evidence needed, the confirmed layout is
  used), with no warning, but is now recorded: the unit's record in the manifest's
  `experiment_description` entry gets `images_without_header_channel_names` (image, `none` or
  `partial`, and the zero-based channel positions without a name; omitted for a unit whose images
  are all named), `read_manifest` reads it strictly, `RunInputs.images_without_header_channel_names()`
  returns it read-only (None without a description), and the report text and JSON list the images.
  A run without a description is unchanged: no new manifest or report key.
- **A draft written under another Spec commit is refused at resume (#223, owner decision of
  2026-11-08).** `load_draft` and `publish_draft` raise `DraftFileError` with the new code
  `spec_commit_mismatch` when the draft's `spec_commit` is not the pinned `SPEC_COMMIT`;
  `save_draft` refuses answers carrying another commit. Before, any valid 40-digit commit was
  accepted and the stored confirmations were published as current. The message names both
  commits, says `morphobricks.toml` is untouched and says to start a fresh draft from the folders.
  The stored commit is never rewritten and the stale draft is neither deleted nor repaired.
- **Re-pin the vendored Spec to 2975e0e (MorphoBricks-Spec PR 13, owner decision RR3).** Text only: the
  `image_count_changed` record is written for every confirmed member whose count changed, also
  beside other changes, and the order of the `removed` lines is stated; fixtures and the reference
  validator are unchanged (the fixtures README alone differs). Every fixture is exact and the
  cross-check against the reference validator has 0 differences.
- **Publication writes the `removed` and `image_count_changed` records (owner decisions RR2 and
  RR1).** `publish_description` takes the user's `removals` (each with its confirmation date) and
  `recorded_on`. Every unit the candidate drops must be named by a removal (else `ValueError`,
  nothing written) and is appended to `morphobricks.records.jsonl` as a `removed` line with all
  six keys; a condition, repeat or technical repeat whose ID the removal leaves held nowhere gets
  its own line first (keys it does not name are `null`). A change of a confirmed member's
  `image_count` writes `image_count_changed` with the old and new counts and `recorded_on`
  (required then, never read from the clock), also when the same publication changes other
  things (RR3). Entity lines come first, each unit line once after them; the date of an entity
  is the first removal answer that touches it; with an ID correction the lines carry the
  published IDs. All lines go in the same single atomic append as
  the re-confirmations, so the publication is whole or absent.
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
- **Description draft file and draft to description (Stage 1).** New `morphobricks.draft.json`
  in the output folder: a versioned, strictly read JSON file with the user's answers so far
  (IDs, the date of each repeat's confirmation, the channel of every C index of every unit,
  masks, references, display names), `base_sha256` (the published bytes, or `absent`) and the
  pinned Spec commit. `run/draft_file.py`: `save_draft` (atomic, under the output lock, an answer
  the rules refuse is never saved), `load_draft` (`R_PUBLISHED_FILE_CHANGED` when the published
  file is no longer the base; a malformed, unknown-version or extra-key file is refused, never
  repaired), `publish_draft` (builds the description, publishes it and deletes the draft under
  one hold of the lock; any refusal writes nothing). `draft_answers.description_from_draft` is
  pure and refuses with every missing confirmation listed; nothing is confirmed on the user's
  behalf and the clock is never read. `run/description_file.publish_locked` is
  `publish_description` for a caller that holds the lock. Contract §18.

- **`describe` command, docs and a recorded conformance run (Stage 1, PR 17).**
  `morphobricks-preprocessing describe EXPERIMENT [--output FOLDER] [--exclude PATH]
  [--publish]` prints the draft experiment description in plain language: the proposed IDs
  (none, never repaired, for a folder that cannot be an ID), the repeats with their folders and
  image counts, the references and every open question with what the user has to do. The draft
  is the legacy `experiment_design.csv` of the output folder migrated, or the folders alone;
  against a published `morphobricks.toml` it compares the folders (new, gone, image counts,
  repeats to confirm again; no header is read). It reads no image and writes nothing, and
  **confirms nothing**: confirmations are the user's (owner rule). `--publish` publishes only
  a draft with no open question and every decision confirmed, else it lists what is missing and
  exits with status 1; since no answers live outside the notebook yet, it publishes nothing
  from a draft of folders (open question to the owner), and says `Nothing to publish` for a
  published description that matches the folders. Docs: contract §12 and §16, user guide
  "Describe your experiment", plan §12 question 78 (the owner's answers Q1 to Q8),
  `docs/conformance.md` (the recorded run of the suite with no Spec skip).
- **Reader owns its rule vocabulary; five refusals pinned by path (#204).**
  `description_identity` no longer imports `check_identity_text` from `discovery` or `SINGLE`
  from normalization (Spec §2.7): `description_rules` holds `MISSING_TEXT`, `SINGLE` and
  `label_refused`, and `tests/test_description_rules_copy.py` keeps them equal to the shared
  values. Behaviour is unchanged. `tests/test_spec_reader_refusal_paths.py` pins the path and
  value of `E_SOURCE_FOLDER_REUSED`, `E_SOURCE_FOLDER_CONFLICT`, `E_SOURCE_REPEAT_REUSED`,
  `E_MASK_SOURCE_TYPE_REUSED` and `E_REFERENCE_CYCLE`, which the oracle compares by code only.
  The reader's paths were already correct for all five.
- **Per-unit header evidence for a described run (Stage 1, PR 13; conformance P26).** When the
  output folder holds a published `morphobricks.toml`, `run` no longer applies the
  experiment-wide v0.4 checks `channel_names_differ` and `different_channel_counts`: units may
  spell a channel's header differently through the aliases the description confirms. The
  per-unit checks decide instead: a header name must be, after NFC of both sides and with no
  trimming or case folding, a confirmed name of the unit's channel at that C index
  (`R_HEADER_CONTRADICTION`), the count must match the unit's list
  (`R_CHANNEL_COUNT_MISMATCH`), and units with different lists are refused as before
  (`R_HETEROGENEOUS_LAYOUT_NOT_ENABLED`). The refusal names the unit folders, the image, the
  image's header names and the channel's confirmed names. An image without header names needs
  no evidence (experiment-v1 section 4.3). A run without a description is unchanged (the global
  checks still apply). Tests: `tests/test_spec_run_aliases.py`.
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
- **Semantic `RunInputs` (Stage 1, PR 12; stacked on PR 11).** For a run with a published
  description, each `Fov` carries `repeat_group`, `condition_id`, `technical_repeat_id` and
  `channel_ids` beside the unchanged key (owner decision Q8: the key keeps the source folder
  labels); `RunInputs.channel_index(fov, id)` finds a channel by canonical ID and refuses an
  absent one; `references()` gives the condition reference graph, `mask_semantics()` and
  `mask_check_status()` the masks' meaning and check status. `RunInputs.open` and
  `description()` read only the published `morphobricks.toml`, check it is the file the run
  recorded, and refuse a draft or an unconfirmed file with `A_DRAFT_REFUSED`; `read_mask` of a
  file the mask step has not checked is `A_MASK_UNCHECKED`. A run without a description is
  unchanged. New modules `fov_identity`, `description_consume`, `run_inputs_error`,
  `run_inputs_semantic`; `LazyImage` moved to `run_inputs_image` (still exported from
  `run_inputs`). Conformance P14, P25 and the `A_MASK_UNCHECKED` part of P28 in
  `tests/test_spec_run_inputs_semantic.py`.
- **A run with a published experiment description (Stage 1, PR 11).** When the output folder
  holds a published `morphobricks.toml`, `run` reads it with the strict reader (a refusal is
  reported with its Spec code, table path and a plain message), runs the physical checks
  against the folder and headers, and records `experiment_description` in `run_manifest.json`:
  the SHA-256 of the exact bytes read, the section digests, which units' image type was checked
  against a header, the status of every declared mask file (`checked` or `not yet checked`,
  with how many images have one) and the publication records of `morphobricks.records.jsonl`
  copied unchanged. Units that declare more than one channel layout are refused with
  `R_HETEROGENEOUS_LAYOUT_NOT_ENABLED`, naming the layouts and units; nothing is processed. The
  mask step (`masks` command, `describe_masks`) checks each declared mask's representation:
  a `binary` mask holding more than two values is `R_MASK_CONTRADICTS_REPRESENTATION` (the
  command exits 1; the file stays `not yet checked`), and what it checked is written to
  `mask_representation.json`, each file bound to the fingerprint it had when read, so a new
  batch of files is `not yet checked` again. A run without a description is the v0.4 run, byte
  for byte, with no new manifest key, and issues a `UserWarning` (owner decision Q1). New
  modules `description_layouts`, `description_record`, `description_run_text`,
  `mask_representation` and `run/description_run`; `file_sha256` moved to `_io` (re-exported by
  `manifest`). Conformance P04, P05, P11, P28 in `tests/test_spec_run_description.py`.
- **The notebook bootstrap shows and checks the Git identity of a local checkout ([#103](https://github.com/CellMigrationLab/morphobricks-preprocessing/issues/103)).** A nearby checkout was accepted when its `pyproject.toml` version equalled `current_version`, and the header showed only the version and path, so an edited checkout that kept the version number passed the `RELEASE_REF` pin. In local-checkout mode the cell now reads the checkout's commit and whether its tree has changes, prints both (also in the final header) and records them in `SETUP_RECORD`. A checkout whose commit differs from `RELEASE_REF`, whose tree has changes, or whose state cannot be read (not a Git work tree rooted there, or no Git) is refused, unless `DEVELOPMENT_OVERRIDE = True` is set in the cell; the header then says "DEVELOPMENT OVERRIDE". Installed-package and pinned-install modes are unchanged. Tests: `tests/test_notebook_setup.py`, `tests/test_notebook_file.py`.
- **Re-pin the vendored Spec to b197ac1 (MorphoBricks-Spec PR 12, owner decision UR1).** The
  reserved names of section 3.2 now also apply to `used_ids.channels`: an entry whose case-folded
  form is on the list is `E_ID_RESERVED`, as for a declared channel ID (a listed ID reserves it).
  The other three `used_ids` kinds are unchanged. New fixtures `i135` (invalid) and `v37`
  (valid); the reference validator is updated. Every fixture is exact and the cross-check against
  the reference validator has 0 differences. Before this change the reader accepted `i135`.
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
