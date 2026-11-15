"""``masks.binary.label_binary`` and the binary value rule (Spec ADR 0005 BM1, BM6, BM11).

Called directly with invalid input (``{0, 3, 7}``, ``{0, -1}``, ``float32`` 0/1, a ``(1, 8, 8)``
stack, a list), its numbering (1 to n, no gap, row-major order of each component's first pixel),
its range and overflow rule, and :func:`.mask_rules.check_binary_values` (the dtype first, then
{0, v}; the values named ascending, at most ten).
"""

from __future__ import annotations

import numpy as np
import pytest
from scipy import ndimage

import morphobricks_preprocessing.masks.labels as labels_module
from morphobricks_preprocessing import MaskContradictsRepresentationError, label_binary
from morphobricks_preprocessing.mask_pairs import PIECE_STRUCTURE
from morphobricks_preprocessing.mask_rules import check_binary_values, refuse_binary_values
from morphobricks_preprocessing.masks.binary import CONNECTIVITY, STRUCTURE

BAD = "R_MASK_CONTRADICTS_REPRESENTATION"


def _values_message(path, shown):
    return (
        f"{BAD}: {path} is declared binary but holds the values {shown}; a binary mask holds 0 "
        "and at most one positive value (an all-zero or all-foreground file is accepted). "
        "Declare it instances or parent_labels, or fix the file."
    )


def _dtype_message(path, dtype):
    return (
        f"{BAD}: {path} is declared binary but has pixel type {dtype}; a binary mask is stored "
        "as integers or booleans, never as floats, even when its values are whole numbers. Save "
        "it as integers or booleans."
    )


# --- refusals of a direct call -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("pixels", "message"),
    [
        (np.resize(np.array([0, 3, 7], np.uint8), (8, 8)), _values_message("the mask", "[3, 7]")),
        (np.resize(np.array([0, -1], np.int16), (8, 8)), _values_message("the mask", "[-1]")),
        (np.resize(np.array([0, 1], np.float32), (8, 8)), _dtype_message("the mask", "float32")),
        (np.ones((8, 8), np.float64), _dtype_message("the mask", "float64")),
        (np.ones((8, 8), np.complex64), _dtype_message("the mask", "complex64")),
    ],
    ids=["0_3_7", "0_minus_1", "float32_0_1", "float64_ones", "complex"],
)
def test_label_binary_refuses_what_the_value_rule_refuses(pixels, message):
    before = pixels.copy()
    with pytest.raises(MaskContradictsRepresentationError) as raised:
        label_binary(pixels)
    assert raised.value.code == BAD and str(raised.value) == message
    np.testing.assert_array_equal(pixels, before)


def test_label_binary_names_the_plane_it_is_given():
    pixels = np.resize(np.array([0, 3, 7], np.uint8), (4, 4))
    with pytest.raises(MaskContradictsRepresentationError) as raised:
        label_binary(pixels, name="Cells/a.tif")
    assert str(raised.value) == str(refuse_binary_values("Cells/a.tif", values=[3, 7]))


@pytest.mark.parametrize(
    ("value", "match"),
    [
        (np.zeros((1, 8, 8), np.uint8), r"has 3 dimensions \(shape \(1, 8, 8\)\).*never squeezes"),
        (np.zeros(8, np.uint8), "has 1 dimensions"),
        ([[0, 1], [1, 0]], "is a list, not a NumPy array"),
        (np.ma.MaskedArray(np.zeros((4, 4), np.uint8)), "is a MaskedArray, not a NumPy array"),
    ],
    ids=["stack", "1d", "list", "masked"],
)
def test_label_binary_refuses_anything_but_a_plain_2d_array(value, match):
    with pytest.raises(ValueError, match=match) as raised:
        label_binary(value)
    assert not isinstance(raised.value, MaskContradictsRepresentationError)


# --- labelling ---------------------------------------------------------------------------------


def test_components_are_8_connected_and_numbered_in_row_major_order_of_their_first_pixel():
    pixels = np.zeros((6, 7), np.uint16)
    pixels[4, 0] = 9  # first pixel at row 4: label 3
    pixels[0, 5:7] = 9  # row 0, column 5: label 1
    pixels[1, 1] = pixels[2, 2] = 9  # a corner touch: one component, first pixel (1, 1): label 2
    found = label_binary(pixels)
    assert found.dtype == np.int32 and found.shape == pixels.shape
    assert found[0, 5] == found[0, 6] == 1
    assert found[1, 1] == found[2, 2] == 2
    assert found[4, 0] == 3
    assert sorted(np.unique(found)) == [0, 1, 2, 3]  # 1 to n, no gap
    assert (found == 0).sum() == (pixels == 0).sum()


@pytest.mark.parametrize("dtype", [bool, np.uint8, np.uint16, np.uint64, np.int8, np.int64])
def test_any_integer_or_boolean_type_gives_the_same_labels(dtype):
    rng = np.random.default_rng(3)
    foreground = rng.random((32, 40)) < 0.4
    pixels = foreground.astype(dtype) * (1 if dtype is bool else 5)
    expected = ndimage.label(foreground, np.ones((3, 3)))[0]
    np.testing.assert_array_equal(label_binary(pixels), expected)


def test_no_foreground_gives_zero_objects_and_all_foreground_one():
    np.testing.assert_array_equal(label_binary(np.zeros((5, 4), np.uint8)), 0)
    np.testing.assert_array_equal(label_binary(np.full((5, 4), 255, np.uint8)), 1)
    assert label_binary(np.zeros((0, 4), np.uint8)).shape == (0, 4)


def test_the_input_is_not_changed_and_the_output_is_new():
    pixels = np.zeros((4, 4), np.int32)
    pixels[0, 0] = pixels[3, 3] = 7
    before = pixels.copy()
    found = label_binary(pixels)
    np.testing.assert_array_equal(pixels, before)
    assert not np.shares_memory(found, pixels)


def test_the_structure_is_the_mask_steps_and_connectivity_is_8():
    assert CONNECTIVITY == 8
    np.testing.assert_array_equal(STRUCTURE, ndimage.generate_binary_structure(2, 2))
    assert PIECE_STRUCTURE is STRUCTURE  # imported by the piece counter, not restated
    assert not STRUCTURE.flags.writeable


# --- label range and overflow ------------------------------------------------------------------


def test_more_components_than_the_int32_limit_are_refused_not_wrapped(monkeypatch):
    # A plane reaches 2,147,483,647 components only above about 8.6e9 pixels: the limit is
    # lowered so the same code path is taken on a small plane.
    pixels = np.zeros((5, 5), np.uint8)
    pixels[0, 0] = pixels[0, 4] = pixels[4, 0] = 1
    monkeypatch.setattr(labels_module, "LARGEST_LABEL", 2)
    with pytest.raises(ValueError, match="has 3 connected components; at most 2 fit") as raised:
        label_binary(pixels, name="the plane")
    assert not isinstance(raised.value, MaskContradictsRepresentationError)
    monkeypatch.setattr(labels_module, "LARGEST_LABEL", 3)  # counted wide, then fits
    found = label_binary(pixels)
    assert found.dtype == np.int32 and sorted(np.unique(found)) == [0, 1, 2, 3]


# --- check_binary_values -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "values", [[0], [1], [0, 1], [0, 255], [7], [0, 2**40]], ids=lambda v: str(v)
)
def test_zero_and_one_positive_value_pass(values):
    assert check_binary_values(values, dtype=np.uint64, path="p") is None
    assert check_binary_values([False, True], dtype=bool, path="p") is None


def test_the_dtype_is_checked_before_the_values():
    with pytest.raises(MaskContradictsRepresentationError) as raised:
        check_binary_values([0, 3, 7], dtype=np.float32, path="p")
    assert str(raised.value) == _dtype_message("p", "float32")


def test_at_most_ten_values_are_named_ascending():
    with pytest.raises(MaskContradictsRepresentationError) as raised:
        check_binary_values(range(12, -1, -1), dtype=np.uint8, path="p")
    assert str(raised.value) == _values_message("p", "[1, 2, 3, 4, 5, 6, 7, 8, 9, 10, ...]")
    with pytest.raises(MaskContradictsRepresentationError) as raised:
        check_binary_values(np.arange(11), dtype=np.uint8, path="p")
    assert str(raised.value) == _values_message("p", "[1, 2, 3, 4, 5, 6, 7, 8, 9, 10]")


def test_a_negative_value_is_refused_with_the_other_non_zero_values():
    with pytest.raises(MaskContradictsRepresentationError) as raised:
        check_binary_values(np.array([-2, 0, 5], np.int16), dtype=np.int16, path="p")
    assert str(raised.value) == _values_message("p", "[-2, 5]")
