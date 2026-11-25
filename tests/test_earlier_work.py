"""``run_mismatch``: a run is shown as finished only when it is current and opens (v0.9.0).

Review of #547: the notebook's "finished run" is decided by the description, the images now and
:meth:`.run_inputs.RunInputs.open`, the one rule bricks use: anything ``RunInputs`` refuses
(the table, ``calibration.csv``, the design, a mask) is not a finished run, with its refusal.
"""

from __future__ import annotations

import hashlib
import shutil

import numpy as np
import pytest

from described_tree import run_described
from morphobricks_preprocessing import prefill_mapping
from morphobricks_preprocessing.cli import main
from morphobricks_preprocessing.description_file import DESCRIPTION_FILENAME
from morphobricks_preprocessing.earlier_work import earlier_work, run_mismatch
from morphobricks_preprocessing.run.preflight import preflight
from tiff_tree import write_data, write_plain

IMAGES = ["ctrl/rep1/a.tif", "drug/rep1/a.tif"]


@pytest.fixture
def run(tmp_path):
    root, out, masks = tmp_path / "exp", tmp_path / "out", tmp_path / "masks"
    rng = np.random.default_rng(0)
    for rel in IMAGES:
        write_data(root / rel, rng.integers(0, 200, (2, 16, 16), dtype=np.uint8), axes="TYX")
        write_plain(masks / "cells" / rel, np.ones((2, 16, 16), np.uint16))
    result = run_described(
        root,
        prefill_mapping("TYX"),
        output_folder=out,
        image_type="fluorescence",
        masks_folder=masks,
        mask_axes={"cells": "TYX"},
    )
    assert result.written
    return root, out, masks


def mismatch(root, out, *, exclude=()):
    """Return the notebook's verdict on the run in ``out`` for the folder ``root`` now."""
    pre = preflight(root, out, exclude=exclude, bind_headers=True)
    sha = hashlib.sha256((out / DESCRIPTION_FILENAME).read_bytes()).hexdigest()
    return run_mismatch(out, earlier_work(out).manifest, pre, sha)


def test_a_current_run_is_finished(run):
    assert mismatch(run[0], run[1]) == ""


def test_a_description_not_saved_as_the_run_used_it(run):
    root, out, _ = run
    pre = preflight(root, out, bind_headers=True)
    # None: no saved description, or one changed since the folders were checked.
    why = run_mismatch(out, earlier_work(out).manifest, pre, None)
    assert why == "the experiment description is not saved as the run used it"


def test_a_description_saved_again_after_the_run(run):
    root, out, _ = run
    path = out / DESCRIPTION_FILENAME
    path.write_bytes(path.read_bytes() + b"# saved again\n")
    assert mismatch(root, out) == "the experiment description was saved again after the run"


def test_a_changed_table_is_refused_with_run_inputs_words(run):
    root, out, _ = run
    with (out / "plane_histograms.csv").open("ab") as table:
        table.write(b"\n")
    assert "plane_histograms.csv" in mismatch(root, out)


def test_another_experiment_root(run, tmp_path):
    root, out, _ = run
    other = tmp_path / "copy"
    shutil.copytree(root, other)
    assert mismatch(other, out).startswith("it is of another experiment folder")


def test_an_image_added(run):
    root, out, _ = run
    write_data(root / "drug/rep1/b.tif", np.zeros((2, 16, 16), np.uint8), axes="TYX")
    assert mismatch(root, out) == "images were added, removed or excluded since the run"


def test_an_image_removed_or_excluded(run):
    root, out, _ = run
    assert mismatch(root, out, exclude=[IMAGES[0]]).startswith("images were added, removed")
    (root / IMAGES[0]).unlink()
    assert mismatch(root, out).startswith("images were added, removed")


def test_an_image_changed(run):
    root, out, _ = run
    write_data(root / IMAGES[1], np.ones((2, 16, 16), np.uint8), axes="TYX")
    assert mismatch(root, out) == f"{IMAGES[1]} changed since the run"


def test_a_calibration_added_after_the_run(run):
    root, out, _ = run
    assert (
        main(["calibrate", str(root), "--output", str(out), "--size", ".", "X", "0.5", "um"]) == 0
    )
    assert "calibration.csv" in mismatch(root, out)


def test_a_changed_design(run):
    root, out, _ = run
    with (out / "experiment_design.csv").open("ab") as design:
        design.write(b"\n")
    assert "experiment_design.csv" in mismatch(root, out)


def test_a_changed_mask(run):
    root, out, masks = run
    write_plain(masks / "cells" / IMAGES[0], np.full((2, 16, 16), 2, np.uint16))
    why = mismatch(root, out)
    assert why and IMAGES[0] in why
