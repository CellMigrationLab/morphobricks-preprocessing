# Development Conventions

## Environment

- Python 3.10 or newer.
- Core dependencies: `numpy` and `tifffile` only. Notebook dependencies (`ipywidgets`,
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
- Type hints on all public functions.
- NumPy-style docstrings on public modules, classes and functions.
- Comments explain *why*, not *what*.

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
