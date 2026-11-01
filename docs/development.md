# Development Conventions

## Environment

- Python 3.11 or newer.
- Core dependencies: `numpy`, `tifffile` and `morphobricks-normalization` (installed from
  its GitHub release tag; it holds the histogram, CSV, pooling and normalization code).
  Notebook dependencies (`ipywidgets`,
  `matplotlib`) are in the `notebook` extra, `zarr` (2.18 or 3.x, for OME-Zarr copies) in
  the `zarr` extra, and development tools in the `dev` extra.
- Set up a clone with `python -m venv .venv` and `pip install -e ".[dev]"`.

## Layout

```
src/morphobricks_preprocessing/   importable package, one module per job
tests/                            pytest suite with simulated data
docs/                             plan, contract, these conventions
experiments/                      studies behind design decisions (not part of the package)
notebooks/                        biologist-facing notebooks, calling the API only
```

The notebook and the CLI contain no processing logic; they call the same public
functions the tests use.
The notebook's Step 0 contains a standalone bootstrap using only the standard library,
so it can locate a checkout or install missing requirements before importing the package.
Its environment alternatives are tested without installing packages during the test suite.

## Style

- PEP 8 naming; lines up to 100 characters.
- `ruff format` formats the code and `ruff check` lints it; both run in CI.
- Type hints on all functions (see the code standards below).
- NumPy-style docstrings on public modules, classes and functions.
- Comments explain *why*, not *what*.

## Code standards (MorphoBricks spec §9)

`python tools/check.py` runs everything a pull request must pass: `ruff check`,
`ruff format --check`, pytest and the code rules. CI runs the code rules with
`python tools/check.py --rules`.

- **Size:** files at most 600 lines; functions at most 60 executable lines, complexity 15
  (`C901`), 8 parameters (`PLR0913`) and 50 statements (`PLR0915`).
- **Annotations** on every function in `src` and `tools`, private and nested ones included
  (`ANN`), with `from __future__ import annotations` in every module (`I002`). Tests are
  exempt, as they are from the docstring rules.
- **Docstrings:** NumPy style for public names (`D`); at least an imperative line for
  private functions over 10 lines.
- **Layers:** every module of the package is declared `pure`, `io`, `ui` or
  `orchestration` in `pyproject.toml` (`[tool.morphobricks.code-rules.layers]`). Pure
  modules do no I/O and no UI; UI modules do not compute.
- **Ownership:** shared logic lives in its owner repository; `RESERVED_NAMES` in
  `tools/code_rules.py` fails a copy anywhere else. Write files with
  `_io.write_text_atomic` and word messages with `validation.plural` and
  `validation.agree`, never a local copy.
- **Enforcement:** `tools/code_rules.py` is the shared file, identical in every MorphoBricks
  repository (`tests/test_code_rules.py` checks its SHA-256). The violations that existed
  when the rules came in are listed in `tools/code_rules_baseline.json`, with the number of
  `# noqa` comments of the rules above. The baseline may only shrink: a new or grown
  violation fails, and so does an entry the code no longer needs, until
  `python tools/check.py --update-baseline` lowers it (it never adds an entry). A `# noqa`
  must name its rules (`PGH004`), and line and file-level ones are counted, tests included.

## Design rules

- Small dataclasses for settings and results; no global state.
- Functions take explicit inputs and return results.
- Expected problems become validation issues with a severity, a plain-language
  message, the affected paths and a suggested action; they are never raw exceptions
  shown to users. Custom exception classes are for genuine failures.
- Technical details go through the `logging` module to the diagnostic log.
- Source images are never modified. Nothing is written inside the experiment folder.

## Determinism and files

- Folders and files are processed in sorted order; the same inputs always produce the
  same output.
- Paths written to outputs are relative and use `/`.
- Floats are written at full precision (`repr`); files are UTF-8.
- Outputs are written to a temporary file and replace the previous version only after a
  complete successful write.
- Experiment runs stage both the CSV and manifest (and any OME-Zarr copies) before
  publishing either, back up earlier outputs, and restore their previous state if
  publication raises. Preserve
  backups if recovery fails. The CSV hash check still detects forced termination between
  replacements; run only one writer per output folder.

## Tests

- `pytest`; simulated images are generated inside the tests (see `tests/simulation.py`).
- Every acceptance check in the plan has a test.
- A bug fix comes with a test that fails without it.

## Versioning

- Semantic versioning for the package.
- `schema_version` in the CSV changes whenever the contract in `docs/contract.md`
  changes in a way readers must know about.
