"""PP-10: the functions the notebook cards share with the run and the command line."""

import numpy as np
import pytest

from described_tree import run_described
from morphobricks_preprocessing import prefill_mapping, preflight
from morphobricks_preprocessing.calibration import Calibration, calibration_entry
from morphobricks_preprocessing.design import default_design
from morphobricks_preprocessing.design_edit import DesignEdit, DesignEditError, apply_design_edit
from morphobricks_preprocessing.run import write_calibration
from morphobricks_preprocessing.run.check_step import suggested_review
from morphobricks_preprocessing.run.planned import planned_changes
from morphobricks_preprocessing.run.preflight import recalibrated
from morphobricks_preprocessing.run.recorded import GivenInputs
from tiff_tree import write_data

TYX = prefill_mapping("TYX")


@pytest.fixture
def experiment(tmp_path):
    root = tmp_path / "exp"
    for rel in ["ctrl/rep1/a.tif", "drug/rep1/a.tif", "drug/rep2/a.tif"]:
        write_data(root / rel, np.ones((2, 8, 8), np.uint8), axes="TYX")
    return root


def test_unconfirm_takes_a_confirmation_back_and_refuses_contradictions(experiment):
    pre = preflight(experiment, experiment.parent / "out")
    design = apply_design_edit(
        default_design(pre.dataset.repeats), DesignEdit(confirm_all=True), "2026-11-02"
    )
    assert design.confirmed
    undone = apply_design_edit(design, DesignEdit(unconfirm=("rep1",)), "2026-11-02")
    assert not undone.group_confirmed("rep1") and undone.group_confirmed("rep2")
    with pytest.raises(DesignEditError, match="unknown repeat group 'nope'"):
        apply_design_edit(design, DesignEdit(unconfirm=("nope",)), "2026-11-02")
    with pytest.raises(DesignEditError, match="cannot be confirmed and unconfirmed"):
        apply_design_edit(design, DesignEdit(confirm=("rep1",), unconfirm=("rep1",)), "2026-11-02")
    with pytest.raises(DesignEditError, match="cannot be confirmed and unconfirmed"):
        apply_design_edit(design, DesignEdit(unconfirm=("rep1",), confirm_all=True), "2026-11-02")
    # Taking back a confirmation never confirms anything.
    fresh = default_design(pre.dataset.repeats)
    assert not apply_design_edit(fresh, DesignEdit(unconfirm=fresh.groups), "2026-11-02").confirmed


def test_recalibrated_reads_calibration_again_and_replaces_its_issues(experiment, tmp_path):
    output = tmp_path / "out"
    pre = preflight(experiment, output)
    assert all(size.value is None for sizes in pre.calibration.sizes.values() for size in sizes)
    write_calibration(Calibration((calibration_entry("nowhere", "X", 0.5, "um"),)), output)
    wrong = recalibrated(pre, output)
    assert [i.code for i in wrong.issues if i.code.startswith("calibration")] == [
        "calibration_target_not_found"
    ]
    write_calibration(Calibration((calibration_entry(".", "X", 0.5, "um"),)), output)
    right = recalibrated(wrong, output)
    assert not [i for i in right.issues if i.code.startswith("calibration")]
    assert right.calibration.sizes["ctrl/rep1/a.tif"][0].source == "stated"
    assert right.discovery is pre.discovery and right.dataset is pre.dataset


def test_planned_changes_name_what_the_run_then_reports(experiment, tmp_path):
    output = tmp_path / "out"
    assert planned_changes(GivenInputs(experiment_root=experiment), output) == ()
    assert run_described(experiment, TYX, output_folder=output).written
    given = GivenInputs(experiment_root=experiment, exclude=("drug/rep2/a.tif",), bin_count=16)
    planned = planned_changes(given, output)
    assert [change.name for change in planned] == ["exclude", "bin_count"]
    result = run_described(
        experiment, TYX, output_folder=output, exclude=["drug/rep2/a.tif"], bin_count=16
    )
    assert result.input_changes == planned


def test_suggested_review_fits_the_sizes_as_check_does(experiment):
    pre, review = suggested_review(preflight(experiment))
    assert review is not None and "".join(review.mapping.roles) == "TYX"
    assert pre.calibration.sizes["ctrl/rep1/a.tif"][2].source is None  # no header size
