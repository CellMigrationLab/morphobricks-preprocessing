"""#497: a run whose every image is excluded reports one error, not two.

The design is reconciled with the folders found now (``run.steps.header_checks``); with every
image excluded none is left, and ``design_issues`` refuses the empty design (#470). The
discovery's ``no_images`` already says why, so the run does not add the design's error to it.
"""

import numpy as np

from described_tree import run_described
from morphobricks_preprocessing import DesignTable, design_issues, prefill_mapping, run_experiment
from tiff_tree import write_data

IMAGES = ["ctrl/r1/a.tif", "drug/r1/a.tif"]


def test_every_image_excluded_is_one_no_images_error_not_two(tmp_path):
    root, output = tmp_path / "exp", tmp_path / "out"
    for rel in IMAGES:
        write_data(root / rel, np.ones((3, 8, 8), np.uint8), "TYX")
    assert run_described(root, prefill_mapping("TYX"), output_folder=output).written
    result = run_experiment(root, output_folder=output, exclude=IMAGES)
    assert not result.written
    codes = [issue.code for issue in result.issues]
    assert codes.count("no_images") == 1
    assert "invalid_experiment_design" not in codes


def test_an_empty_design_action_does_not_only_say_to_save_the_description_again():
    (issue,) = design_issues(DesignTable(()))
    assert issue.code == "invalid_experiment_design"
    assert "not all excluded" in issue.action
