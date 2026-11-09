# Conformance run records

The conformance plan of MorphoBricks-Spec (`docs/conformance-plan.md`, section 5) asks for the
test run to be recorded but names no place for the record, so it is kept here. Every row is a
real run; a skip is never counted as a pass. Owner rulings of 8 November 2026 (issue #235):
"no skips" applies to the Spec conformance modules (`tests/test_spec_*.py`); a skip elsewhere
in the suite is listed as **not run**, with its reason.

## Gate G1 record: release candidate v0.5.0 (9 November 2026)

| | |
|---|---|
| Release candidate | `c74d717e4f4f0a72be8b991435413adf4a2a1d37` (branch `claude/release-0.5.0`: `main` at `bf733ee` plus the v0.5.0 preparation `3b616a0` and the notebook's `RELEASE_REF` commit). The commit that adds this record changes only this file, so the code and tests are those of `c74d717` |
| Spec pin | `tests/spec_fixtures/PIN.toml`: MorphoBricks-Spec `1bed024e278d104ba4b8f63ae76c020fb2e3470d` (Spec `main`). Fixtures tree `f61b1c2`, `experiment-v1.md` blob `b12678c`, `refval.py` blob `0ea8309`, `refrecords.py` blob `ef06a1e`, each also compared by hand with `git rev-parse 1bed024:<path>` in a fresh clone of the Spec: all four equal |
| Environment | Linux, Python 3.13.18, `pip install -e '.[dev,notebook,zarr]'` in a fresh venv (package version 0.5.0, numpy 2.5.4, tifffile 2026.11.8, ipywidgets 8.1.9, zarr 3.4.2, pytest 9.2.0, normalization 0.3.4); run as root on a case-sensitive file system |
| Commands | `python -m pytest -q -p no:cacheprovider -rsxX --junitxml=… tests/` (the whole suite, with a per-test JUnit report), and `python tools/check.py` (Ruff, Ruff format, pytest over `tests/`, code rules) |
| Whole suite | 5018 collected: 5014 passed, 4 skipped (not run, below), 0 failed, 0 xfailed |
| Spec modules (`tests/test_spec_*.py`) | 1649 passed, **0 skipped, 0 xfailed, 0 failed**, from all 21 modules of `tests/spec_gate.py` `EXPECTED_MODULES` |
| `tools/check.py` | Ruff and format clean, pytest 5014 passed and 4 skipped, code rules passed (exit status 0) |

**How "no skips" was checked for the Spec modules.** (1) The JUnit report has 1649 test cases
from `test_spec_*` modules, every one passed: none has a `skipped` (or xfail) element. (2) The
`-rsxX` summary of the run lists exactly the four skips below, none in a Spec module, and no
xfail or xpass. (3) All 21 modules of `EXPECTED_MODULES` have test cases in the report
(catalogue 376, derived design 79, discovery 11, encoding 58, gate 9, no fallback 15, pin 16,
publication 20, reader 330, reader phases 1-3 287, reader refusal paths 8, reconcile 25,
records 15, records goldens 76, refval cross-check 203, rules table 14, run aliases 15, run
description 9, RunInputs semantic 7, transitions 73, vendored missing 3), and the gate's
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

### Physical scenarios P01-P35 (conformance plan section 3)

"Passed" means every test named passed in this run. Where a test uses another fixture or a
synthetic tree than the plan names, or covers only part of the required outcome, the row says so.

| # | Result | Tests (module: test functions) |
|---|---|---|
| P01 | Passed | `test_physical_check`: `test_p01_an_image_with_another_channel_order_is_not_homogeneous` (`v01`) |
| P02 | Passed | `test_physical_check`: `test_p02_an_image_with_one_channel_is_not_homogeneous_not_a_count_mismatch` (`v01`) |
| P03 | Passed | `test_physical_check`: `test_p03_no_case_folding_and_no_similarity` (`v11`, 2 cases) |
| P04 | Passed | `test_spec_run_description`: `test_p04_heterogeneous_layouts_are_refused_and_nothing_is_processed` (`v04`, `v05`, `v06`, `v17`), `test_p04_the_message_names_the_layouts_and_the_units_in_plain_words`; `test_heterogeneous_refusal_first` (9) |
| P05 | Passed | `test_spec_run_description`: `test_p05_binary_mask_with_values_0_to_5_is_refused_by_the_mask_step_not_the_run`, `test_p05_an_instances_mask_holding_only_0_and_1_is_accepted` (`v08`) |
| P06 | Passed, partly | `test_spec_run_aliases`: `test_p26_images_without_header_names_need_no_evidence_v12`; `test_physical_check`: `test_an_image_with_names_when_the_channel_has_none_contradicts` (`v12`); `test_physical_check`: `test_p23_pixels_that_look_like_rgb_are_never_examined` (no check reads pixels). **No test builds two unnamed units with physically swapped fluorophores**; the "preview + confirmation only" part is the notebook's channel grid (`test_notebook_channels`, 30) |
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
| P20 | Passed | `test_spec_publication`: `test_p20_a_dropped_used_id_is_refused` (`v25`, `t04`), `test_p20_removing_the_last_member_of_a_condition_needs_the_recorded_confirmation` (on `v01`) |
| P21 | Passed | `test_spec_discovery`: `test_p21_a_decomposed_folder_is_refused_with_a_description`, `test_p21_a_composed_folder_is_accepted`, `test_p21_without_a_description_discovery_is_as_in_v0_4`, `test_p21_preflight_carries_the_refusal` (synthetic tree) |
| P22 | Passed | `test_spec_publication`: `test_p22_confirmed_on_stays_byte_for_byte_through_an_unrelated_publication`, `test_p22_a_new_date_on_an_unchanged_repeat_is_refused` (`t05`), `test_p22_after_a_reopening_and_a_new_confirmation_the_new_date_is_accepted` (`v01`) |
| P23 | Passed | `test_physical_check`: `test_p23_a_unit_declared_rgb_whose_images_are_two_channel_grayscale` (`v28`), `test_p23_an_rgb_tiff_in_a_unit_that_does_not_declare_it` (`v01`), `test_p23_pixels_that_look_like_rgb_are_never_examined` |
| P24 | **Not run** | The outcome is a brick's: a request through a provider manifest with or without `rgb_support`. Preprocessing has no provider manifest; it only exports the error class (`test_a_error_classes`: `A_RGB_NOT_SUPPORTED` class, code and pickling, passed). No test of (a)-(d) exists here |
| P25 | Passed | `test_spec_run_inputs_semantic`: `test_p25_each_fov_carries_the_source_label_and_the_added_repeat_group` (`v03`, `v30`), `test_p25_in_v30_grouping_by_the_source_label_would_merge_different_repeats` |
| P26 | Passed | `test_spec_run_aliases` (9 `test_p26_*`), `test_physical_check` (`test_p26_*`, 2 functions), `test_spec_discovery` (`test_p26_the_header_names_are_recorded_as_read_untrimmed`, `test_p26_the_manifest_records_the_header_name_as_read`): 19 cases (`v11`, `v31`, `v12`) |
| P27 | **Not run** | The outcome is a brick's: an object-set request with or without `binary_mask_policy`. Preprocessing only exports `A_MASK_NOT_INSTANCES` (`test_a_error_classes`, passed) and refuses `binary_mask_policy` in the file (`i112`, `E_FORBIDDEN_FIELD`, through the reader tests). (a)-(c) are not tested here |
| P28 | Passed | `test_spec_run_description`: `test_p28_each_new_batch_is_not_yet_checked_until_the_mask_step_has_read_it` (`v08`); `test_spec_run_inputs_semantic`: `test_p28_a_mask_file_not_yet_checked_is_refused_when_read_until_the_step_checked_it` |
| P29 | Passed | `test_spec_discovery`: `test_p29_na_and_a_leading_space_are_invalid_mask_type_names`, `test_p29_a_decomposed_mask_type_is_not_nfc`, `test_p29_the_three_cases_together`, `test_p29_valid_mask_types_are_accepted`, `test_p29_without_a_description_mask_types_are_accepted_as_in_v0_4` (synthetic tree) |
| P30 | Passed, partly | `test_draft`: `test_p30_a_group_with_two_dates_takes_the_latest_and_a_note_lists_both`, `test_a_confirmed_group_without_any_date_stays_open_and_takes_no_clock_date`; golden `v13` (below). **Not tested:** the derived CSV of the two-date group compared with the legacy file (differing only in the earlier date) |
| P31 | **Not run** | The outcome is MorphoCoverage's (prefill, confirmation, refusal of an unconfirmed prefill). Preprocessing's part passed: editing `target` changes no digest (`test_description_write`: `test_a_display_name_target_or_treatment_key_changes_no_digest`; `v27` valid with no warning in the reader tests) |
| P32 | Passed | `test_draft`: `test_p32_forbidden_folders_get_no_proposal_and_nothing_is_adopted`, `test_p32_ordinary_folders_are_proposed_as_their_labels`; `test_spec_derived_design`: golden `v33`, `v34` and `test_the_file_names_folders_never_ids` |
| P33 | Passed | `test_spec_reconcile`: `test_p33_a_a_renamed_condition_folder_is_not_attached_to_the_old_id`, `test_p33_b_the_statement_keeps_the_id_moves_the_folder_and_reopens_the_repeats`, `test_p33_c_the_old_folder_removal_needs_its_confirmation_and_the_id_stays_used` (`v33`); `test_condition_folder_reopens` (2) |
| P34 | Passed, on other fixtures | `test_spec_records`: `test_a_changed_image_count_writes_the_old_and_new_counts_with_recorded_on` (no reopening, `confirmed_on` kept), `test_several_changed_members_are_written_in_condition_then_repeat_order`, `test_an_image_count_change_needs_a_date_and_writes_nothing_without_one`, `test_a_removal_and_a_count_change_in_one_publication_write_both_in_order` (RR3). They use `v06` and `v13`, not the `v01` the plan names |
| P35 | Passed, on other fixtures | `test_spec_records`: `test_a_removed_unit_writes_one_unit_record_naming_all_six_keys` (with its `reconfirmation` lines), `test_a_removed_repeat_writes_the_repeat_then_each_of_its_units`, `test_a_removed_condition_writes_the_condition_then_each_of_its_units`, `test_a_removed_technical_repeat_names_its_id_and_folder_then_its_unit`, `test_a_dropped_unit_without_a_removal_is_not_published`, `test_nothing_is_written_when_publication_is_refused`; `test_spec_publication`: `test_p20_removing_the_last_member_of_a_condition_needs_the_recorded_confirmation`. They use `v01`, `v03`, `v06`, not `v25` with `t07` (`t07` itself passes in `test_spec_transitions`) |

### Golden CSV comparisons (conformance plan section 2, "Derived CSV")

`tests/test_spec_derived_design.py`, 79 passed:

- `test_the_golden_files_are_the_three_the_catalogue_lists`: `fixtures/expected/` holds exactly
  `v13`, `v33`, `v34`;
- `test_the_derived_file_equals_the_golden_file_byte_for_byte`: `v13`, `v33`, `v34`, byte for byte;
- `test_the_derived_file_equals_the_oracles_on_every_valid_fixture` and
  `test_the_derived_file_is_read_back_by_the_strict_reader`: every valid fixture, against
  `refval.derive_csv` and through `read_confirmed_design`;
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
| (d) Heterogeneous refusal explicit and tested | `test_heterogeneous_refusal_first` (9, including `test_the_check_and_save_list_leads_with_the_layout_refusal`); P04 above |
| Shipped notebook install | `test_notebook_file::test_remote_install_is_pinned_to_one_exact_commit`, `test_version_metadata_matches_labconstrictor_index`; `test_notebook_setup` (18, including `test_setup_installs_missing_package_from_the_exact_commit`) |

**Not part of this record:** the browser smoke check of `docs/colab-release-check.md` (a
fresh Google Colab runtime and JupyterLab/LabConstrictor). Headless tests cannot show that a
browser renders the widgets; that check is the owner's, before the tag.

### Gaps against the conformance plan

The plan's exit criterion (section 5) asks that every section 2 test and every section 3
scenario run and pass. This run does not meet it in full:

- **P24, P27, P31 are not run**: their outcomes belong to the consuming bricks and MorphoCoverage
  (conformance plan section 4 puts `A_RGB_NOT_SUPPORTED`, `A_MASK_NOT_INSTANCES` and the
  MorphoCoverage prefill in Stage 2), and preprocessing has no code that could run them.
- **P06 and P30 are partly covered** (see their rows); **P34 and P35** pass on other fixtures
  than the ones the plan names.
- Section 2, "Provenance": `i99` is refused (`E_FORBIDDEN_FIELD`), but no run-manifest test
  records lab-tool links: preprocessing has no lab-tool provenance input yet.
- Section 2, "`confirmed_on`": no writer test with a frozen clock moved by a day; the writer
  writes the model's date (`test_description_write::test_confirmed_on_is_written_as_the_model_holds_it`)
  and records take the user's date, not the clock
  (`test_spec_records::test_the_date_of_each_record_is_the_users_confirmation_not_the_clock`).

So this record does not by itself declare any reader conforming; deciding Gate G1 with these
gaps is the owner's.

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
