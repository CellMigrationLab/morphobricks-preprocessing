"""Roles that put X before Y are refused: nothing is ever read transposed (owner, 1 Nov 2026).

A plain stack names no axes, so the user confirms its roles; confirming X before Y (``TXY``)
would read every plane transposed. It is the error ``axes_x_before_y``, which recommends
saving the images again with Y before X, or as named-axis OME-TIFF copies.
"""

import numpy as np
import pytest

from morphobricks_preprocessing import (
    AXIS_ERROR_CODES,
    AxisMapping,
    header_axis_issue,
    inspect_image,
    review_axes,
    run_experiment,
)
from morphobricks_preprocessing.axis_check import axis_disagreements
from tiff_tree import write_data, write_plain


def stack():
    return np.arange(3 * 6 * 8, dtype=np.uint8).reshape(3, 6, 8)


@pytest.mark.parametrize("writer", ["plain-IYX", "shaped-QYX"])
@pytest.mark.parametrize("roles", ["TXY", "XTY", "XYT"])
def test_a_plain_stack_confirmed_x_before_y_is_refused(tmp_path, writer, roles):
    path = tmp_path / "a.tif"
    write_plain(path, stack()) if writer == "plain-IYX" else write_data(path, stack())
    meta = inspect_image(path)
    mapping = AxisMapping(meta.axes, roles)
    assert header_axis_issue(meta, mapping) == "axes_x_before_y"
    assert "axes_x_before_y" in AXIS_ERROR_CODES
    review = review_axes([meta], mapping)
    (issue,) = [i for i in review.issues if i.is_error]
    assert issue.code == "axes_x_before_y" and review.axis_error_files == ("a.tif",)
    assert "nothing is ever read transposed" in issue.message
    assert "Y before X" in issue.action and "OME-TIFF" in issue.action


def test_the_run_reads_nothing(tmp_path):
    root = tmp_path / "exp"
    write_plain(root / "ctrl/a.tif", stack())
    result = run_experiment(root, AxisMapping("IYX", "TXY"))
    assert not result.written and result.read == ()
    assert [i.code for i in result.issues if i.is_error] == ["axes_x_before_y"]
    # Y before X is read, with the plain stack's warning.
    assert run_experiment(root, AxisMapping("IYX", "TYX")).written


def test_only_y_and_x_are_marked(tmp_path):
    path = write_plain(tmp_path / "a.tif", stack())
    marks = axis_disagreements(inspect_image(path), AxisMapping("IYX", "TXY"))
    assert marks == {0: "axes_not_named", 1: "axes_x_before_y", 2: "axes_x_before_y"}


def test_a_named_swap_stays_a_swap(tmp_path):
    path = write_data(tmp_path / "a.tif", stack(), "ZYX")
    meta = inspect_image(path)
    assert header_axis_issue(meta, AxisMapping("ZYX", "ZXY")) == "axes_yx_swapped"
