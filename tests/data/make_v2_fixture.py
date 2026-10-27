"""Write the schema-version-2 fixture of ``test_schema_v2_equivalence.py``.

Run once, from the repository root, with the code of version 2: this repository at
commit ``55f1074`` (before follow-up P3b) and morphobricks-normalization v0.1.0,
in an environment of their own:

    git worktree add ../v2 55f1074
    git clone -b v0.1.0 https://github.com/CellMigrationLab/morphobricks-normalization ../n1
    pip install ../n1 tifffile zarr pytest
    PYTHONPATH=../v2/src python tests/data/make_v2_fixture.py

It writes, next to this script:

- ``v2_pixels.npz``: the pixels of every image of the experiments in
  ``tests/v2_fixture.py``;
- ``v2_<dtype>.csv``: the ``plane_histograms.csv`` that version's
  ``run_experiment`` wrote for them (schema version 2, one row per plane and
  channel).

The fixture is committed, so the test compares the current code with the output
of the earlier code rather than with itself.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))

from morphobricks_normalization.csv_io import SCHEMA_VERSION  # noqa: E402

from morphobricks_preprocessing import prefill_mapping, run_experiment  # noqa: E402
from v2_fixture import EXPERIMENTS, PIXELS, make_pixels, write_experiment  # noqa: E402


def main() -> None:
    """Write the pixels and the version-2 tables."""
    if SCHEMA_VERSION != "2":
        sys.exit(f"Run this with the code of schema version 2, not {SCHEMA_VERSION}.")
    pixels = make_pixels()
    np.savez_compressed(PIXELS, **pixels)
    for dtype in EXPERIMENTS:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "exp"
            bins, axes = write_experiment(root, dtype, pixels)
            result = run_experiment(root, prefill_mapping(axes), bin_count=bins)
            if not result.written:
                sys.exit(f"{dtype}: {[issue.message for issue in result.issues]}")
            shutil.copyfile(result.csv_path, HERE / f"v2_{dtype}.csv")
        print(f"{dtype}: {result.plane_count} rows")


if __name__ == "__main__":
    main()
