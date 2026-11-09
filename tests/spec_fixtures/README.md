# Vendored MorphoBricks-Spec files

A partial copy of [MorphoBricks-Spec](https://github.com/CellMigrationLab/MorphoBricks-Spec)
at the commit named in `PIN.toml`, laid out as in the Spec repository:

| Here | In the Spec | Used for |
|---|---|---|
| `fixtures/` | `fixtures/` | The conformance fixtures: every test of the experiment-description reader runs on them |
| `docs/experiment-v1.md` | `docs/experiment-v1.md` | The normative text; tests compare the reader's tables of codes, names and fields with it |
| `tools/audit/refval.py` | `tools/audit/refval.py` | The reference validator, used **only as a test oracle**; nothing in `src/` imports it |
| `tools/audit/refrecords.py` | `tools/audit/refrecords.py` | The reference validator of the records file (§4.6), imported by `refval.py`; also a test oracle only |

Rules (conformance plan, sections 1 and 2):

- **Never edit these files.** `tests/test_spec_pin.py` hashes `fixtures/` with git's tree
  algorithm and each other file with git's blob algorithm, and fails when one byte differs
  or a file is added or missing. A fixture that a reader fails is a finding for the Spec
  repository, not something to change here.
- **The pin is a full commit SHA**, never a branch.
- **A skipped Spec test is a failure.** Every test module named `tests/test_spec_*.py` is
  held to it by `tests/spec_gate.py`, so a missing package, file or weight cannot turn a
  conformance test into a silent pass.
- The reader has no copy of the rules besides its one table module
  (`tests/test_spec_no_fallback.py`).

## Updating the pin

1. In a checkout of the Spec, check out the new commit and read
   `git rev-parse HEAD`, `git rev-parse HEAD:fixtures`,
   `git rev-parse HEAD:docs/experiment-v1.md`, `git rev-parse HEAD:tools/audit/refval.py` and
   `git rev-parse HEAD:tools/audit/refrecords.py`.
2. Replace the vendored files from that commit, for example
   `git archive HEAD fixtures | tar -x -C tests/spec_fixtures`, and `git show HEAD:<path>` for
   the three single files.
3. Put the five values in `PIN.toml`.
4. Run the whole suite. The reader, its tables and its tests change in the same pull
   request when the Spec changed text or fixtures (the Spec changes first).
