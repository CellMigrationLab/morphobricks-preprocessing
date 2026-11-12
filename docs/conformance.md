# Conformance run records

The conformance plan of MorphoBricks-Spec (`docs/conformance-plan.md`, section 5) asks for the
test run to be recorded but names no place for the record, so it is kept here. Every row is a
real run; a skip is never counted as a pass. Owner rulings of 8 November 2026 (issue #235):
"no skips" applies to the Spec conformance modules (`tests/test_spec_*.py`); a skip elsewhere
in the suite is listed as **not run**, with its reason.

## Release record: v0.5.2 (12 November 2026)

A G1-style record of the v0.5.2 release candidate (as for v0.5.1 below). It does not decide a
gate: G2 and G3 also need the bricks' items, and deciding a gate is the owner's.

| | |
|---|---|
| Release candidate | Measured on the tree of the v0.5.2 preparation commit (commit A of branch `claude/release-0.5.2`, from `main` at `682e14e`: #278, #280, #282, #288, #289, #284-#287) before this record was added; the record changes only this file. Commit B then points the notebook's `RELEASE_REF` at A and records the pair in `RELEASE_CALLS` (the notebook and two test files only), so `git diff A B -- src pyproject.toml` is empty: the installed package is the one `RELEASE_REF` names. The measurement on B is in the release PR |
| Spec pin | `tests/spec_fixtures/PIN.toml`: MorphoBricks-Spec `69a58b3e2f89b318364c4c1252c54caf676fb6bb` (fixtures tree `3705f2b`), checked by `test_spec_pin` (16 passed) |
| Environment | Linux, Python 3.13.18, `pip install -e '.[dev,notebook,zarr]'` in a fresh venv (normalization 0.5.0 from its tag `v0.5.0` = `1291b09`, numpy 2.5.4, tifffile 2026.11.8, ipywidgets 8.1.9, zarr 3.4.2, pytest 9.2.0); run as root on a case-sensitive file system |
| Commands | `python tools/check.py` (Ruff, Ruff format, pytest over `tests/`, code rules); `python -m pytest -q -p no:cacheprovider -rsxX --junitxml=… tests/test_spec_*.py` |
| `tools/check.py` | Ruff clean, Ruff format 457 files already formatted, pytest **5460 passed, 4 skipped** (not run, below), 0 failed, code rules passed (exit status 0) |
| Spec modules (`tests/test_spec_*.py`) | **1799 passed, 0 skipped, 0 xfailed, 0 failed**, from all 25 modules of `tests/spec_gate.py` `EXPECTED_MODULES` (JUnit: no `skipped`, `failure` or `error` element in any case) |
| Stage 3 (G3), preprocessing's parts | The conformance plan §4 "Histogram table v5" row on `v13`, `v04`, `v05`, `v06`, `v17` and an ID with `_` (`PAX_488`), the `v17` channel case (a PHASE request: `R002` partial, `R001` unavailable; `ChannelScopeIncompleteError` for its statistics), the owner's extra-stain case, and `v29` still refused: `test_stage3_channels` (17 passed), `test_channel_counts_per_folder` (1), `test_notebook_extra_stain` (3); `v04`, `v05`, `v06`, `v17` processed (`test_spec_run_description`). Not shown here: the `v05` per-repeat classification of a PHASE request (`rep1` partial; `v05` is processed and its table checked, but no test asserts its coverage); HV3's per-channel measurement failure (a plane that cannot be read stops the run, `unreadable_planes`; left for S3.4), parallel shards (preprocessing has none), and the readers of CellPatcher and MorphoFeature |
| Parent labels (§4), preprocessing's parts | `test_spec_parent_labels` (25 passed), as in the re-pin record below. The consumers' parts (manifest key, area measurement, recorded marker) are the bricks' |

Test cases per Spec module: catalogue 386, derived design 84, discovery 17, encoding 60, Fov
image type 14, gate 9, lab links 11, no fallback 15, parent labels 25, pin 16, publication 20,
reader 338, reader phases 1-3 295, reader refusal paths 8, reconcile 26, records 22, records
goldens 76, refval cross-check 210, resolve request 43, rules table 14, run aliases 15, run
description 9, RunInputs semantic 8, transitions 75, vendored missing 3. New since v0.5.1:
`test_spec_parent_labels` (#289).

**Not run (4 environment skips, outside the Spec modules):**

| Test | Reason |
|---|---|
| `tests/test_cli_log.py:166` | Case-sensitive file system: the case-only collision cannot be built |
| `tests/test_folder_rules.py:199` | Same |
| `tests/test_folder_rules.py:230` | Same |
| `tests/test_discovery.py:665` | Run as root: file modes are ignored, so an unreadable folder cannot be made |

These four behaviours are **not verified by this run**.

**Still the owner's:** the Colab and JupyterLab browser check (`docs/colab-release-check.md`),
before tagging; the decision on mixed RGB and channel units (still refused, #288); whether the
conversion record's new optional `source_relative_path` needs record version 4 (#287).

## Re-pin to Spec `69a58b3` (11 November 2026, parent-label masks, PL1)

Not a release record. Measured on branch `claude/parent-label-masks` (from `main` at `a775ebf`),
Linux, Python 3.13.18, fresh venv with `.[dev,notebook,zarr]`.

| | |
|---|---|
| Spec pin | MorphoBricks-Spec `69a58b3e2f89b318364c4c1252c54caf676fb6bb` (PR #28), vendored with the Spec's `tools/vendor.py`; `vendor.py --check` reports the folder identical |
| `tools/check.py` | Ruff clean, Ruff format 443 files, pytest **5396 passed, 4 skipped** (the four environment skips below: case-sensitive file system three times, running as root once), 0 failed, code rules passed |
| Spec modules (`tests/test_spec_*.py`) | **1796 passed, 0 skipped**, 25 modules (new: `test_spec_parent_labels`) |
| Mask fields (section 2) | `v38`, `v39` accepted and cross-checked against the oracle (and mutated, `test_spec_refval_crosscheck`); `i136`, `i137` `E_ENUM`; `i138`, `i139` `E_UNKNOWN_FIELD`; `i112` `E_FORBIDDEN_FIELD`; `i111` retired (absent, its number not reused) |
| Parent labels (section 4) | Preprocessing's parts on a described `v38` run (`test_spec_parent_labels`): `Mito` as a region is selected with `representation = "parent_labels"` and `conversion = "parent_labels_as_area"` without the capability, no conversion with it; as objects `A_MASK_NOT_INSTANCES` with and without it; a `binary_mask_policy` on it is a `ValueError`; a non-bool capability is a `ValueError`; the mask step records its files `checked`; the notebook offers *grouped labels* and pre-fills nothing from the `grouped` kind. The consumers' parts (manifest key, area measurement, recorded marker) are the bricks' |

## Release record: v0.5.1 (10 November 2026)

A G1-style record of the v0.5.1 release candidate (as for v0.5.0 below). It does not decide a
gate: the Stage 2 gate (G2) also needs the bricks' items, and deciding a gate is the owner's.

| | |
|---|---|
| Release candidate | Measured on `9f61cb9` (branch `claude/release-0.5.1`: `main` at `9a5da03`, the v0.5.1 preparation `d023ff9` (commit A), `a4154ad` (commit B), which points the notebook's `RELEASE_REF` at `d023ff91289a2d2204e1ae56f7dee114d1a665e4`, the first record `7607617`, then the review fixes `7beac12` (a cell's one-line stop names the error that caused it; notebook, tests and docs only) and `9f61cb9` (CHANGELOG)). The commit that updates this record changes only this file. `git diff d023ff9 9f61cb9 -- src pyproject.toml` is empty: the installed package is the one `RELEASE_REF` names. Earlier measurement, on `a4154ad` before the review: 5270 passed, 4 skipped; 1732 in the Spec modules, 0 skipped (reproduced by the review) |
| Spec pin | `tests/spec_fixtures/PIN.toml`: MorphoBricks-Spec `cc9820f5b84f01bbbb5c8e3f54e70ee13cb5e9eb` (fixtures tree `40fe48c`), checked by `test_spec_pin` (16 passed) |
| Environment | Linux, Python 3.13.18, `pip install -e '.[dev,notebook,zarr]'` in a fresh venv (package version 0.5.1, normalization 0.4.0, numpy 2.5.4, tifffile 2026.11.8, ipywidgets 8.1.9, zarr 3.4.2, pytest 9.2.0); run as root on a case-sensitive file system |
| Commands | `python tools/check.py` (Ruff, Ruff format, pytest over `tests/`, code rules); `python -m pytest -q -p no:cacheprovider -rsxX --junitxml=… tests/test_spec_*.py` |
| `tools/check.py` | Ruff clean, Ruff format 432 files already formatted, pytest **5274 passed, 4 skipped** (not run, below), 0 failed, code rules passed (exit status 0) |
| Spec modules (`tests/test_spec_*.py`) | **1732 passed, 0 skipped, 0 xfailed, 0 failed**, from all 24 modules of `tests/spec_gate.py` `EXPECTED_MODULES` (JUnit: no `skipped`, `failure` or `error` element in any case) |
| Install | `pip install "morphobricks-preprocessing[notebook] @ git+…@d023ff9…"` in a fresh venv reports 0.5.1 (normalization 0.4.0), its package files equal this branch's `src/morphobricks_preprocessing` (`diff -r`), and `PreprocessingNotebook()` builds `welcome()`, `steps()` and `results_step()` |
| Shipped notebook | Its three code cells, unmodified, in a real kernel (`nbclient`) in a fresh venv: cell 3 first stops with one line naming cell 1; cell 1 installs `RELEASE_REF` from GitHub and prints **Ready** (0.5.1, normalization 0.4.0); cell 2 prints its one-line note; cell 3 displays the app. With `google.colab` faked: cell 3 before cell 2 prints the one Drive line and opens the app; after cell 2 it opens without it. Cell 1 on a Python 3.10 kernel stops with the one "Python 3.11 or newer" line (on `a4154ad`). Re-run by the review on `9f61cb9` in a fresh venv (cell 3 alone, cell 1 twice, cell 2 outside Colab, cell 3; faked Colab: cell 1 adds the `zarr` extra, cell 3 without Drive prints the one line and opens the app), plus an interface that cannot load (one line naming the `ModuleNotFoundError`), a failed Drive mount (one line naming the `ValueError`) and an unexpected error in cell 3 (its full traceback) |

Test cases per Spec module: catalogue 376, derived design 80, discovery 17, encoding 58, Fov
image type 14, gate 9, lab links 11, no fallback 15, pin 16, publication 20, reader 330, reader
phases 1-3 287, reader refusal paths 8, reconcile 25, records 22, records goldens 76, refval
cross-check 203, resolve request 43, rules table 14, run aliases 15, run description 9,
RunInputs semantic 8, transitions 73, vendored missing 3. New since v0.5.0:
`test_spec_resolve_request` (PP-0), `test_spec_fov_image_type` (PP-1) and
`test_spec_lab_links` (PP-2, the lab-tool-links part of the section 2 "Provenance" row, which
ADR 0001 GS1 deferred from G1 to the Stage 2 gate). The cases removed since v0.5.0 are the
undescribed and legacy ones (PP-C, SWEEP-PP, PP-03; the P30 legacy case per Spec `cc9820f`).

**Not run (4 environment skips, outside the Spec modules):**

| Test | Reason |
|---|---|
| `tests/test_cli_log.py:166` | Case-sensitive file system: the case-only collision cannot be built |
| `tests/test_folder_rules.py:199` | Same |
| `tests/test_folder_rules.py:230` | Same |
| `tests/test_discovery.py:665` | Run as root: file modes are ignored, so an unreadable folder cannot be made |

These four behaviours are **not verified by this run**.

**Still the owner's:** the Colab and JupyterLab browser check (`docs/colab-release-check.md`),
before tagging. P24, P27 and P31 stay brick outcomes for the Stage 2 gate.

## Gate G1 record: release candidate v0.5.0 (10 November 2026)

| | |
|---|---|
| Release candidate | Measured on `1e5390c` (branch `claude/release-0.5.0`: `main` at `bf733ee`, the v0.5.0 preparation `3b616a0`, `c74d717`, the first record `4437067`, the G1 gap tests `a6a0343`, the record `c675f13`, the P21/P29 fix `0a2e25b`, `5cc3b07`, the record `76e09c3`, then `0ea3254`, the merge of `main` at `8c573d6` (#254, #255, #257: the audit fixes listed below), `ba415de`, the record `43d1257`, then the final-review fix `fd7afd3` (below), and `1e5390c`, which points the notebook's `RELEASE_REF` at `fd7afd3`). The commit that updates this record changes only this file, so the code and tests are those of `1e5390c`. `git diff fd7afd3 1e5390c -- src pyproject.toml` is empty: the installed package is the one `RELEASE_REF` names. Earlier measurements: on `ba415de`, before the final review, 5105 passed and 1671 in the Spec modules; on `a6a0343` (5028 passed, 1660 in the Spec modules) P21 and P29 were not met, and `0a2e25b` fixes that source defect; on `5cc3b07`, before the merge of `main`, 5045 passed and 1671 in the Spec modules |
| Spec pin | `tests/spec_fixtures/PIN.toml`: MorphoBricks-Spec `1bed024e278d104ba4b8f63ae76c020fb2e3470d`. The G1 scope ruling (ADR 0001 GS1, conformance plan sections 4 and 5) was merged later as `cdc1841`, which changes only `docs/adr/0001-experiment-description-v1.md` and `docs/conformance-plan.md`, so the pinned files are unchanged. Fixtures tree `f61b1c2`, `experiment-v1.md` blob `b12678c`, `refval.py` blob `0ea8309`, `refrecords.py` blob `ef06a1e`, each also compared by hand with `git rev-parse 1bed024:<path>` in a fresh clone of the Spec: all four equal |
| Environment | Linux, Python 3.13.18, `pip install -e '.[dev,notebook,zarr]'` in a fresh venv (package version 0.5.0, numpy 2.5.4, tifffile 2026.11.8, ipywidgets 8.1.9, zarr 3.4.2, pytest 9.2.0, normalization 0.3.4); run as root on a case-sensitive file system |
| Commands | `python -m pytest -q -p no:cacheprovider -rsxX --junitxml=… tests/` (the whole suite, with a per-test JUnit report), and `python tools/check.py` (Ruff, Ruff format, pytest over `tests/`, code rules) |
| Whole suite | 5119 collected: 5115 passed, 4 skipped (not run, below), 0 failed, 0 xfailed (the 10 more than on `ba415de` are `tests/test_notebook_final_review.py`; the 60 more on `ba415de` than on `5cc3b07` are the tests of #254, #255 and #257) |
| Spec modules (`tests/test_spec_*.py`) | 1671 passed, **0 skipped, 0 xfailed, 0 failed**, from all 21 modules of `tests/spec_gate.py` `EXPECTED_MODULES` |
| `tools/check.py` (on `1e5390c`) | Ruff clean, Ruff format 416 files already formatted, pytest 5115 passed and 4 skipped, code rules passed (exit status 0) |

**How "no skips" was checked for the Spec modules.** (1) The JUnit report has 1671 test cases
from `test_spec_*` modules, every one passed: none has a `skipped` (or xfail) element. (2) The
`-rsxX` summary of the run lists exactly the four skips below, none in a Spec module, and no
xfail or xpass. (3) All 21 modules of `EXPECTED_MODULES` have test cases in the report
(catalogue 376, derived design 81, discovery 22, encoding 58, gate 9, no fallback 15, pin 16,
publication 20, reader 330, reader phases 1-3 287, reader refusal paths 8, reconcile 25,
records 22, records goldens 76, refval cross-check 203, rules table 14, run aliases 16, run
description 9, RunInputs semantic 8, transitions 73, vendored missing 3), and the gate's
collection check (a deleted, emptied or skipped Spec module stops the run) did not fire.
(4) The gate itself (`tests/spec_gate.py`) turns a skip or xfail in those modules into a
failure, and its own tests (`test_spec_gate.py`, 9) passed.

**Reference validator.** `test_spec_refval_crosscheck.py` (203 passed) compares the reader with
the vendored `refval.py` on every fixture; `test_spec_records_goldens.py` (76 passed) compares
`read_records` with the vendored `refrecords.py` on every records golden; the transitions oracle
(`test_spec_transitions.py::test_the_oracle_agrees_on_every_ordered_pair`) and the derived-CSV
oracle (`test_spec_derived_design.py`) passed.

### Not run (4 environment skips, outside the Spec modules)

| Test | Reason |
|---|---|
| `tests/test_cli_log.py:165` (`test_the_experiment_folder_in_another_case_is_refused`) | The file system tells names apart by case, so the case-only collision cannot be built |
| `tests/test_folder_rules.py:201` (`test_the_masks_folder_spelled_in_another_case_is_the_same_folder`) | Same: case-sensitive file system |
| `tests/test_folder_rules.py:229` (`test_output_folder_in_the_experiment_spelled_in_another_case`) | Same: case-sensitive file system |
| `tests/test_discovery.py:665` (`test_unreadable_folder_is_an_error`) | Run as root: file modes are ignored, so an unreadable folder cannot be made |

These four behaviours are **not verified by this run**.

### Final-review fixes (`fd7afd3`), since the measurement on `ba415de`

Found by the final adversarial review of the candidate, by driving the notebook headless; each
has regression tests in `tests/test_notebook_final_review.py` (10 tests, 8 of which fail on
`ba415de`), all passed in this run. No Spec module and no Spec pin changes.

- **A header refusal lifted by an answer about another cell.** Tagging any channel of any folder
  recomputed every label's `header_names` from every saved tag, so after images were rewritten
  with other header names the `R_HEADER_CONTRADICTION` of folders the user had not tagged again
  vanished, the save published it and the run wrote the table. Now a tag confirms the header
  name of the cell tagged and nothing else (`channel_answers.synced`); the refusal stays until
  the channel it names is tagged again.
- **A "same way?" kept for other folders or another layout in a draft.** With nothing published,
  the date saved in `morphobricks.draft.json` was kept when a folder joined or left the
  replicate, and when a tag or an image type changed after it, and was then published as the
  user's confirmation. Now it is asked again (`published_answers.resumed`, and `reasked` with or
  without a published file); a replicate brought back to the layout it was answered for keeps
  its answer.

### Fixes merged from `main` in `0ea3254` (measured first on `ba415de`)

Each with its own tests, all passed in this run; none changes a Spec module or the Spec pin.

- **Audit batches 33 and 34a (#254):** what is published is what was answered, of the image
  checked. A block's saved tags are exactly those shown; a unit tagging one label twice is
  refused (`E_DUPLICATE_CHANNEL_IN_UNIT`); an unanswered image type is an open question
  (`unit_image_type_unanswered`); `publish_draft` requires each unit's channel count
  (`unit_channels_stale`, `unit_channel_count_unknown`); the notebook's folder check binds each
  header to its file's fingerprint (`bind_headers=True`, beside the P21/P29 `with_description`),
  and pictures, mask evidence and the save are of that revision; records that the records reader
  would refuse are never written (`R_RECORDS_FILE_INVALID`).
- **Audit batch 34b (#255):** *Confirm these axes* is offered only while a preview of exactly
  the offered roles is shown; the reopen reconciles with the folder check's header evidence
  (channel count, raw header names, the RGB flag), so a header rewritten under the same files is
  a channel change, not "nothing changed"; the axis preview is bound with the same header
  fingerprints; non-finite pixels are no longer counted as crop detail.
- **Pre-release follow-ups (#257):** "prepared and imaged the same way?" is asked again only for
  a replicate whose declared layout changes against the published one (experiment-v1 §10),
  every other `confirmed_on` kept byte for byte (§4.4a); header contradictions are the run's
  `R_HEADER_CONTRADICTION`, shown at once; *Check and save* runs publication's transition checks
  (`R_CONFIRMED_ON_CHANGED`, `R_CONFIRMED_ID_CHANGED`, `R_USED_ID_*`); `describe` reads the
  headers (no pixel) when it compares a published description; a unit's channel count comes from
  every image of the unit.

### Physical scenarios P01-P35 (conformance plan section 3)

"Passed" means every test named passed in this run, on the fixtures the plan names (ADR 0001
GS1: a scenario that names fixtures is shown on those fixtures; further fixtures are listed as
"also"). Where the required outcome is not shown on the named fixture, the row says so.

| # | Result | Tests (module: test functions) |
|---|---|---|
| P01 | Passed | `test_physical_check`: `test_p01_an_image_with_another_channel_order_is_not_homogeneous` (`v01`) |
| P02 | Passed | `test_physical_check`: `test_p02_an_image_with_one_channel_is_not_homogeneous_not_a_count_mismatch` (`v01`) |
| P03 | Passed | `test_physical_check`: `test_p03_no_case_folding_and_no_similarity` (`v11`, 2 cases) |
| P04 | Lifted at Stage 3 (Spec roadmap S3.3; owner decision of 11 November 2026) | `test_spec_run_description`: `test_p04_lifted_at_stage_3_different_channel_lists_are_processed` (`v04`, `v05`, `v06`, `v17`: processed, schema version 5), `test_p04_units_of_different_image_types_are_still_refused_naming_the_units` (`v29`, kept refused); `test_heterogeneous_refusal_first` (9, `v29`); `test_stage3_channels` (the conformance plan §4 "Histogram table v5" row on `v13`, `v04`, `v05`, `v06`, `v17` and an ID with `_`; the owner's extra-stain case) |
| P05 | Passed | `test_spec_run_description`: `test_p05_binary_mask_with_values_0_to_5_is_refused_by_the_mask_step_not_the_run`, `test_p05_an_instances_mask_holding_only_0_and_1_is_accepted` (`v08`) |
| P06 | Passed | `test_physical_check`: `test_p06_swapped_fluorophores_in_unnamed_units_pass_and_each_unit_needs_its_own_look` (`v12` with a second unnamed unit, DrugA/rep1, of the same shape; DrugA's two planes stored the other way round): no issue, the physical report equals that of the unswapped folder, the two units share one layout group that is **not** batch-confirmable (Q3), and `channel_strip` shows each unit's own planes (labels `C0`, `C1`, no name made up); `test_spec_run_aliases`: `test_p06_two_unnamed_units_with_swapped_fluorophores_run_unrefused_on_the_confirmation`: the run is written with no issue, its `experiment_description` entry equals the unswapped run's, and every image of both units is listed under `images_without_header_channel_names` (`none`, positions 0, 1). Also `test_p26_images_without_header_names_need_no_evidence_v12`, `test_an_image_with_names_when_the_channel_has_none_contradicts` (`v12`) |
| P07 | Passed | `test_spec_reconcile`: `test_p07_a_new_technical_repeat_is_pending_and_reopens_only_its_repeat` (`v13`), `test_p07_after_the_user_assigns_the_unit_and_confirms_the_new_date_is_accepted` (`v06`) |
| P08 | Passed | `test_spec_reconcile`: `test_p08_a_renamed_folder_is_removed_plus_new_and_never_paired`, `test_p08_the_removal_is_one_grouped_question_and_publishes_only_when_answered` (`v03`) |
| P09 | Passed | `test_description_file`: `test_p09_a_symlinked_description_is_refused_by_the_reader`, `test_p09_a_dangling_symlink_is_refused_not_missing`, `test_p09_publication_refuses_a_symlink_and_neither_follows_nor_replaces_it` (`v01`) |
| P10 | Passed | `test_physical_check`: `test_p10_an_undeclared_mask_type_is_refused_and_a_declared_empty_one_is_valid` (`v07`) |
| P11 | Passed | `test_spec_run_description`: `test_p11_masks_for_none_some_then_all_images_are_valid_and_completeness_is_recorded` (`v09`) |
| P12 | Passed | `test_physical_check`: `test_p12_a_declared_unit_with_every_image_excluded_is_not_found` (`v01`) |
| P13 | Passed | `test_physical_check`: `test_p13_rgb_units_declared_rgb_match_the_header_and_are_header_verified` (`v28`), `test_a_format_without_a_colour_interpretation_is_recorded_as_not_header_verified` |
| P14 | Passed | `test_spec_run_inputs_semantic`: `test_p14_a_draft_is_never_read`, `test_p14_a_published_file_with_an_unconfirmed_repeat_is_refused`, `test_p14_a_description_changed_since_the_run_is_refused` (`v01`) |
| P15 | Passed | `test_spec_reconcile`: `test_p15_new_fovs_in_an_existing_unit_keep_the_confirmation_and_its_date` (`v13`), `test_p15_a_changed_channel_list_reopens_with_the_date_kept` (`v01`) |
| P16 | Passed | `test_spec_publication`: `test_p16_a_changed_id_is_refused_and_t02_also_drops_a_used_id`, `test_p16_t01_is_accepted_only_with_a_recorded_reconfirmation`, `test_p16_a_display_name_change_is_accepted_and_invalidates_nothing` (`v01`, `t01`, `t02`, `v16`) |
| P17 | Passed | `test_spec_publication`: `test_p17_a_second_draft_from_the_same_base_is_refused_and_not_merged` (`v01`, `v21`, `v16`; includes re-deriving B from A's file) |
| P18 | Passed | `test_spec_publication`: `test_p18_publishing_over_nothing_with_the_absent_base_is_accepted`, `test_p18_another_file_appears_before_publication`, `test_p18_a_draft_without_a_usable_base_is_refused_and_nothing_is_published` (8 cases) |
| P19 | Passed | `test_spec_publication`: `test_p19_a_retired_id_declared_again_is_refused`, `test_p19_also_when_the_returning_folder_is_the_one_the_retired_id_had` (`v25`, `t03`) |
| P20 | Passed | `test_spec_publication`: `test_p20_a_dropped_used_id_is_refused` (`v25`, `t04`), `test_p20_removing_the_last_member_of_a_condition_needs_the_recorded_confirmation` (on `v01`); `test_spec_records`: `test_p35_v25_is_what_removing_the_last_members_of_drug_a_from_v01_publishes` (the accepted removal publishes `v25` byte for byte, DrugA kept in `used_ids`) |
| P21 | Passed | `test_spec_discovery` on `v01` published with a decomposed folder `CTRL/Cafe\u0301` added: `test_p21_a_run_with_published_v01_refuses_a_decomposed_folder` (`run_experiment` refuses with `R_FOLDER_NOT_NFC` naming `CTRL/Cafe\u0301`, beside `R_UNIT_NOT_DECLARED`; nothing written, nothing renamed, no composed twin), `test_p21_the_mask_step_refuses_a_decomposed_experiment_folder`, `test_p21_the_command_line_check_and_run_refuse_a_decomposed_folder`, `test_p21_the_command_line_describe_refuses_a_decomposed_folder`; `test_notebook_name_refusals`: `test_p21_the_described_folder_check_refuses_a_decomposed_folder`, `test_p21_the_v0_4_folder_check_with_a_published_description_refuses_too`, `test_p21_p29_a_refused_name_blocks_check_and_save`. Without a description: `test_p21_p29_an_undescribed_run_of_the_same_trees_is_as_in_v0_4`, `test_p21_p29_the_mask_step_without_a_description_is_as_in_v0_4`, `test_p21_p29_the_command_line_check_without_a_description_is_as_in_v0_4`, `test_p21_p29_without_a_description_the_folder_check_is_as_in_v0_4`. Also the 4 synthetic-tree `test_p21_*` of `preflight(..., with_description=True)`. Fixed in `0a2e25b` (on `a6a0343` the run gave only `R_UNIT_NOT_DECLARED`): each of these fails on `c675f13` except the four without a description |
| P22 | Passed | `test_spec_publication`: `test_p22_confirmed_on_stays_byte_for_byte_through_an_unrelated_publication`, `test_p22_a_new_date_on_an_unchanged_repeat_is_refused` (`t05`), `test_p22_after_a_reopening_and_a_new_confirmation_the_new_date_is_accepted` (`v01`) |
| P23 | Passed | `test_physical_check`: `test_p23_a_unit_declared_rgb_whose_images_are_two_channel_grayscale` (`v28`), `test_p23_an_rgb_tiff_in_a_unit_that_does_not_declare_it` (`v01`), `test_p23_pixels_that_look_like_rgb_are_never_examined` |
| P24 | Deferred to the Stage 2 gate per ADR 0001 GS1 (MorphoBricks-Spec `cdc1841`) | The outcome is a brick's (provider manifest `rgb_support`). Preprocessing's own parts passed: the `A_RGB_NOT_SUPPORTED` class (`test_a_error_classes`) and the section 2 "RGB" row (below) |
| P25 | Passed | `test_spec_run_inputs_semantic`: `test_p25_each_fov_carries_the_source_label_and_the_added_repeat_group` (`v03`, `v30`), `test_p25_in_v30_grouping_by_the_source_label_would_merge_different_repeats` |
| P26 | Passed | `test_spec_run_aliases` (9 `test_p26_*`), `test_physical_check` (`test_p26_*`, 2 functions), `test_spec_discovery` (`test_p26_the_header_names_are_recorded_as_read_untrimmed`, `test_p26_the_manifest_records_the_header_name_as_read`): 19 cases (`v11`, `v31`, `v12`) |
| P27 | Deferred to the Stage 2 gate per ADR 0001 GS1 (MorphoBricks-Spec `cdc1841`) | The outcome is a brick's (per-request `binary_mask_policy`). Preprocessing's own parts passed: `A_MASK_NOT_INSTANCES` (`test_a_error_classes`) and the section 2 "Mask fields" row (`i111`, `i112`, below) |
| P28 | Passed | `test_spec_run_description`: `test_p28_each_new_batch_is_not_yet_checked_until_the_mask_step_has_read_it` (`v08`, with the 0-5 binary batch); `test_spec_run_inputs_semantic`: `test_p28_a_mask_file_not_yet_checked_is_refused_when_read_until_the_step_checked_it` (`v08`), `test_p28_on_v09_each_batch_of_masks_is_unchecked_until_the_mask_step_reads_it` (`v09`: three batches, each `not yet checked` and `A_MASK_UNCHECKED` until the mask step reads it, earlier batches stay `checked`) |
| P29 | Passed | `test_spec_discovery` on `v07` published with mask folders `NA`, ` cells` and `Cafe\u0301` beside `nuclei` and `cells`: `test_p29_a_run_with_published_v07_refuses_the_mask_type_names` (`run_experiment` refuses with `R_MASK_TYPE_NAME_INVALID` naming ` cells`, `NA` and `R_FOLDER_NOT_NFC` naming `Cafe\u0301`, beside `R_MASK_TYPE_NOT_DECLARED`; nothing renamed), `test_p29_the_mask_step_refuses_the_names_with_a_published_description` (the step is not ready), `test_p29_the_command_line_check_and_masks_refuse_the_mask_type_names` (no `mask_relationships.json`); `test_notebook_name_refusals`: `test_p29_the_described_folder_check_refuses_the_mask_type_names`, `test_p29_pairing_the_masks_again_keeps_the_refusal`, `test_p21_p29_a_refused_name_blocks_check_and_save` (no `morphobricks.toml` is published, so the names never reach `E_ID_TEXT`). Without a description: the four `test_p21_p29_*_as_in_v0_4` above. Also the 5 synthetic-tree `test_p29_*`. Fixed in `0a2e25b` (on `a6a0343` the run gave only `R_MASK_TYPE_NOT_DECLARED`); the new tests fail on `c675f13` except those without a description |
| P30 | Passed | `test_spec_derived_design`: `test_p30_the_derived_file_of_migrated_legacy_answers_differs_only_in_the_earlier_date` (`v13` tree, 2 cases): the `v13` legacy file migrated, answered with the proposed IDs and derived again is the legacy file byte for byte (and the golden); with `rep1` rows dated 2026-10-01 and 2026-10-15, the repeat takes 2026-10-15, the note lists both dates, and the derived file differs from the legacy file in one line only, the CTRL/rep1 date (the one-date groups `rep2`, `rep3` are byte-identical); under a frozen clock no clock is read. Also `test_draft`: `test_p30_a_group_with_two_dates_takes_the_latest_and_a_note_lists_both`, `test_a_confirmed_group_without_any_date_stays_open_and_takes_no_clock_date`. The answers state each unit's image type ("channels"): since audit batch 33 an unanswered image type is an open question (`unit_image_type_unanswered`), so the test answers it, as the merge `0ea3254` records **Amended after this run** (10 November 2026, re-pin to Spec `cc9820f`, ADR 0002 AR10 (9)): the legacy-CSV case was removed from P30; the test is now `test_p30_v13_derived_twice_with_a_clock_moved_by_a_day_equals_the_golden` and the migration tests named here are deleted. This row records the run on `1bed024`. |
| P31 | Deferred to the Stage 2 gate per ADR 0001 GS1 (MorphoBricks-Spec `cdc1841`) | The outcome is MorphoCoverage's (prefill, confirmation). Preprocessing's own parts passed: `v27` valid with no warning, and editing `target` or `treatment_key` of `v27` changes no digest and no derived row (section 2 rows below) |
| P32 | Passed | `test_draft`: `test_p32_forbidden_folders_get_no_proposal_and_nothing_is_adopted`, `test_p32_ordinary_folders_are_proposed_as_their_labels`; `test_spec_derived_design`: golden `v33`, `v34` and `test_the_file_names_folders_never_ids` |
| P33 | Passed | `test_spec_reconcile`: `test_p33_a_a_renamed_condition_folder_is_not_attached_to_the_old_id`, `test_p33_b_the_statement_keeps_the_id_moves_the_folder_and_reopens_the_repeats`, `test_p33_c_the_old_folder_removal_needs_its_confirmation_and_the_id_stays_used` (`v33`; (a) asserts that the hand-made attachment is `t12` and gives exactly `R_CONFIRMED_ID_CHANGED`); `t10`, `t11`, `t12` against their predecessors in `test_spec_transitions`; `test_condition_folder_reopens` (2) |
| P34 | Passed | On `v01` (`test_spec_records`): `test_p34_v01_one_changed_count_appends_one_line_and_keeps_every_confirmed_on` (one `image_count_changed` line with the section 4.6 keys, old and new counts and `recorded_on`; the published file differs from `v01` in that one `image_count` line, so no repeat reopens and every `confirmed_on` is byte-identical), `test_p34_v01_a_removal_and_count_changes_write_removed_then_one_line_per_member` (RR3: the `removed` line, then one count line per remaining member, `rep1` keeps its date). Also on `v06`, `v13`: `test_a_changed_image_count_writes_the_old_and_new_counts_with_recorded_on`, `test_several_changed_members_are_written_in_condition_then_repeat_order`, `test_an_image_count_change_needs_a_date_and_writes_nothing_without_one`, `test_a_removal_and_a_count_change_in_one_publication_write_both_in_order` |
| P35 | Passed | On `v25` and `t07` (`test_spec_records`): `test_p35_t07_is_what_a_confirmed_removal_from_v01_publishes` ((a): the confirmed removal of DrugA/rep2 on 2026-11-07 publishes `t07` byte for byte, with the `reconfirmation` lines and one `unit` line), `test_p35_t07_without_the_recorded_removal_is_refused_and_writes_nothing` (`R_CONFIRMED_ID_CHANGED`, nothing written), `test_p35_v25_is_what_removing_the_last_members_of_drug_a_from_v01_publishes` ((b), (c) condition: the `condition` line then each `unit` line, the file is `v25` byte for byte), `test_p35_on_the_v25_base_a_removed_repeat_keeps_every_retired_id` ((c) repeat on the `v25` base: the `repeat` line then its `unit` line; `used_ids` unchanged, DrugA and `rep2` kept), `test_p35_on_the_v25_base_an_unconfirmed_removal_writes_nothing`. (c) technical repeat on `v06` (`v25` has only `single`): `test_a_removed_technical_repeat_names_its_id_and_folder_then_its_unit`. Also `test_a_removed_unit_writes_one_unit_record_naming_all_six_keys`, `test_a_removed_repeat_writes_the_repeat_then_each_of_its_units`, `test_a_removed_condition_writes_the_condition_then_each_of_its_units`, `test_a_dropped_unit_without_a_removal_is_not_published`, `test_nothing_is_written_when_publication_is_refused` |

### Golden CSV comparisons (conformance plan section 2, "Derived CSV")

`tests/test_spec_derived_design.py`, 81 passed (the two P30 cases are in the P30 row):

- `test_the_golden_files_are_the_three_the_catalogue_lists`: `fixtures/expected/` holds exactly
  `v13`, `v33`, `v34`;
- `test_the_derived_file_equals_the_golden_file_byte_for_byte`: `v13`, `v33`, `v34`, byte for byte;
- `test_the_derived_file_equals_the_oracles_on_every_valid_fixture` and
  `test_the_derived_file_is_read_back_by_the_strict_parser`: every valid fixture, against
  `refval.derive_csv` and through `parse_design_bytes`;
- `test_the_file_names_folders_never_ids`: `v33` carries the folder label `control`, never `ctrl`.

Legacy parity with the `v0.4.0` tag (`tests/test_parity_v0_4_0.py`, 24 passed, the tag's `src`
run in a child Python): `plane_histograms.csv` and `experiment_design.csv` byte-identical,
identities and manifest entries equal, a v0.4.0 output folder reused with zero rows recomputed,
and the derived design equal to the bytes v0.4.0 wrote. The goldens for the other valid
fixtures are "to be added" in the Spec (conformance plan section 2); the oracle comparison covers
them meanwhile.

### Notebook items (roadmap Gate G1; Stage 1 design section 5, items a, b, d)

All headless (ipywidgets driven by `click()` and `.value`) and in a real kernel; all passed.

| Item | Tests |
|---|---|
| (a) The notebook starts from raw folders | `test_notebook_check_save` (14): from a folder tree only, every answer, one click publishes `morphobricks.toml` and only then writes `experiment_design.csv` (`test_the_csv_is_written_only_at_save`, `test_save_says_saved_and_enables_run`); `test_notebook_stepper` (13); `test_notebook_description` (25); `test_notebook_channels` (30); `test_notebook_masks_screen` (9); `test_preview` (22); `test_notebook_file` (6, including `test_notebook_runs_in_a_kernel`) |
| (b) The confirmed file reloads and reconciles | `test_notebook_check_save`: `test_reopening_with_nothing_changed_folds_every_screen`, `test_a_folder_that_joins_a_replicate_asks_same_way_again_for_it_only`, `test_a_removed_folder_needs_yes_remove_before_saving` (writes the `removed` record), `test_the_draft_carries_the_pairing_across_a_reopen`; `test_notebook_row_fold` (6); `test_notebook_tag_like` (6); `test_notebook_stepper`: `test_a_reopened_experiment_opens_on_the_axes_its_only_open_step`, `test_a_changed_reopen_turns_the_affected_steps_orange` |
| (c) Legacy exports, identities and caches preserved | `test_parity_v0_4_0` (24, above); `test_notebook_check_save::test_without_describe_the_v04_cards_stay` |
| (d) Heterogeneous refusal explicit and tested | Stage 3: only units of different image types are refused (`v29`): `test_heterogeneous_refusal_first` (9, including `test_the_check_and_save_list_leads_with_the_layout_refusal`); different channel lists are processed: P04 above |
| Shipped notebook install | `test_notebook_file::test_remote_install_is_pinned_to_one_exact_commit`, `test_version_metadata_matches_labconstrictor_index`; `test_notebook_setup` (18, including `test_setup_installs_missing_package_from_the_exact_commit`) |

**Colab and JupyterLab browser check: owner, before tagging.** The browser smoke check of
`docs/colab-release-check.md` (a fresh Google Colab runtime and JupyterLab/LabConstrictor) was not
run for this record: headless tests cannot show that a browser renders the widgets.

### Section 2 rows (conformance plan section 2)

The `confirmed_on` row, and the rows that ADR 0001 GS1 keeps in G1 on the subjects of the
deferred items, each from this run. The other section 2 rows run in the Spec modules above (all
passed, none skipped); they were not re-audited row by row for this update.

| Row | Result | Tests |
|---|---|---|
| `confirmed_on` | Passed | `i98` is `E_MISSING_FIELD` (`test_spec_reader`); `test_description_write`: `test_a_frozen_clock_moved_by_a_day_writes_identical_bytes_for_an_unchanged_model`: with `date` and `datetime` frozen in the `datetime` module and every package module that imports them (`tests/frozen_clock.py`), on 2026-11-09 and then 2026-11-10, the canonical bytes of every valid fixture are identical, the canonical writer reads the clock 0 times, and republishing `v01` the next day leaves `morphobricks.toml` byte-identical with no record written |
| RGB | Passed | `test_spec_reader`: `v28`, `v29` accepted; `i100`-`i106`, `i119` give exactly their codes, each with a table path and the value; `test_spec_refval_crosscheck` agrees on all of them; `test_description_write`: round trip, idempotence and section 9.1 layout of `v28`, `v29` |
| Mask fields | Passed | `test_spec_reader` and `test_spec_refval_crosscheck`: `i111` (`E_ENUM`), `i112` (`E_FORBIDDEN_FIELD`) |
| Descriptive keys, Section digests | Passed | `v27` accepted with no warning (`test_spec_reader`); `test_description_write`: `test_editing_only_target_or_treatment_key_of_v27_changes_no_digest_and_no_grouping` (new: the edited `v27` is valid, all four digests equal, the derived design rows equal), `test_a_display_name_target_or_treatment_key_changes_no_digest` (`v16`, `v21` against `v01`) |
| Provenance | `i99` passed; lab-tool links deferred to the Stage 2 gate per ADR 0001 GS1 (MorphoBricks-Spec `cdc1841`) | `i99` is `E_FORBIDDEN_FIELD` (`test_spec_reader`, `test_spec_refval_crosscheck`). Preprocessing has no lab-tool input yet |

### Against the conformance plan

Gate G1 scope, ADR 0001 GS1 (MorphoBricks-Spec `cdc1841`): P24, P27, P31 and the lab-tool-links
part of the section 2 "Provenance" row are deferred to the Stage 2 gate per ADR 0001 GS1
(MorphoBricks-Spec `cdc1841`); nothing else is deferred. Against that scope this run shows:

- P01-P23, P25, P26, P28-P30, P33-P35 passed on the fixtures the plan names. P32
  passed on trees with the `v33` and `v34` folders and on the `v33`, `v34` goldens. The section 2
  rows in the table above passed (Provenance: its `i99` part).
- P21 and P29, not met when measured on `a6a0343` (their codes came only from
  `preflight(..., with_description=True)`, which no production caller passed), are met on `v01`
  and `v07` since `0a2e25b`: the run, the mask step, the command line (`check`, `run`, `masks`,
  `describe`) and the notebook's folder check refuse the names whenever a description is in use
  (owner decision Q1), and *Check and save* does not publish while one is refused. Without a
  published description the run, mask step and check are unchanged: the same trees run without
  a description give byte-identical `plane_histograms.csv`, `mask_relationships.json`,
  `experiment_design.csv` and reports on `c675f13` and `5cc3b07`, the manifests differing only in
  `written_at` (and so the reports in the manifest's SHA-256); `test_parity_v0_4_0` (24) passed.
  The command line's `describe` now refuses a decomposed folder (exit status 1) where it drafted it
  before: it drafts a description, so section 3.3 applies.
- The Colab and JupyterLab browser check is the owner's, before tagging.

So this record does not by itself declare any reader conforming; deciding Gate G1 is the owner's.

## Run of 8 November 2026 (Stage 1, PR 17)

Kept as it was run. Issue #235: this section had been relabelled to later Spec pins (`2975e0e`,
`c8f6852`, `1bed024`) without a new run. The record was written in commit `7418da1`, whose
`tests/spec_fixtures/PIN.toml` and the record's own first text both name `b197ac1`, so that is
the pin restored here. The run on the current pin is the G1 record above.

| | |
|---|---|
| Base commit | `68b2a4b` (`origin/main`, merge of PR 211); the run included the changes of PR 17 |
| Spec pin | MorphoBricks-Spec `b197ac1` (`tests/spec_fixtures/PIN.toml` of that commit) |
| Command | `python tools/check.py` (Ruff, Ruff format, then `pytest -q` over the whole `tests/` folder), then `python tools/check.py --rules` |
| Whole suite | 4324 passed, 4 skipped, 0 failed (301 s) |
| Spec modules (`tests/test_spec_*.py`) | 1535 passed, 0 skipped, 0 failed (the Spec gate fails a skip or xfail there) |
| Code rules | passed, no baseline entry grew |

The 4 skips are outside the Spec modules, each an environment limit of the machine that ran:

- `tests/test_cli_log.py:165`, `tests/test_folder_rules.py:201`, `tests/test_folder_rules.py:229`:
  the file system tells names apart by case, so the case-only collision cannot be built;
- `tests/test_discovery.py:665`: the run had permissions that ignore file modes (root).

These four behaviours were therefore **not verified by that run**. Not covered by it: the Spec's
golden CSVs and the notebook items (PRs 13 to 16), and an end-to-end publication through the
command line (`describe --publish` publishes nothing from a draft of folders).
