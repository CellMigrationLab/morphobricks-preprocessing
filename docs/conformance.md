# Conformance run record (Stage 1, PR 17)

The design (Stage 1, PR 17) names no place for this record, so it is kept here. Every row is a
real run; a skip is never counted as a pass.

## Run of 8 November 2026

| | |
|---|---|
| Base commit | `68b2a4b` (`origin/main`, merge of PR 211); the run included the changes of PR 17 |
| Spec pin | the full SHA in `tests/spec_fixtures/PIN.toml` (vendored `experiment-v1.md` at `c8f6852`) |
| Command | `python tools/check.py` (Ruff, Ruff format, then `pytest -q` over the whole `tests/` folder), then `python tools/check.py --rules` |
| Whole suite | 4324 passed, 4 skipped, 0 failed (301 s) |
| Spec modules (`tests/test_spec_*.py`) | 1535 passed, 0 skipped, 0 failed (the Spec gate fails a skip or xfail there) |
| Code rules | passed, no baseline entry grew |

The 4 skips are outside the Spec modules, each an environment limit of the machine that ran:

- `tests/test_cli_log.py:165`, `tests/test_folder_rules.py:201`, `tests/test_folder_rules.py:229`:
  the file system tells names apart by case, so the case-only collision cannot be built;
- `tests/test_discovery.py:665`: the run had permissions that ignore file modes (root).

These four behaviours are therefore **not verified by this run**.

## Not covered

- The Spec's `fixtures/expected/` golden CSVs and the conformance items that need PRs 13 to 16
  (notebook cards) are outside this run; this is not a release-candidate sign-off for those.
- `describe --publish` publishes nothing from a draft of folders (see the pull request's open
  questions), so no end-to-end publication through the command line is part of this record.
