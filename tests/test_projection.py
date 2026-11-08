"""The pure arithmetic of Z projections (issue #176): types, bounds, exactness, NaN, inf."""

from __future__ import annotations

from fractions import Fraction

import numpy as np
import pytest

from morphobricks_preprocessing.projection import (
    PlaneAccumulator,
    Projection,
    ProjectionRefused,
    projection_dtype,
)

# The decided table, written out by hand: (input, method) -> output type.
TABLE = {
    ("uint8", "max"): "uint8",
    ("uint8", "sum"): "uint32",
    ("uint8", "mean"): "float64",
    ("uint16", "max"): "uint16",
    ("uint16", "sum"): "uint32",
    ("uint16", "mean"): "float64",
    ("int8", "max"): "int8",
    ("int8", "sum"): "int32",
    ("int8", "mean"): "float64",
    ("int16", "max"): "int16",
    ("int16", "sum"): "int32",
    ("int16", "mean"): "float64",
    ("uint32", "max"): "uint32",
    ("uint32", "sum"): "float64",
    ("uint32", "mean"): "float64",
    ("int32", "max"): "int32",
    ("int32", "sum"): "float64",
    ("int32", "mean"): "float64",
    ("float32", "max"): "float32",
    ("float32", "sum"): "float64",
    ("float32", "mean"): "float64",
    ("float64", "max"): "float64",
    ("float64", "sum"): "float64",
    ("float64", "mean"): "float64",
}


def _stack(dtype: str, slices: int = 5, shape=(3, 4), seed: int = 0) -> np.ndarray:
    """Return a random stack that includes the type's extremes."""
    rng = np.random.default_rng(seed)
    dt = np.dtype(dtype)
    if dt.kind == "f":
        stack = rng.normal(0, 1e3, size=(slices, *shape)).astype(dt)
    else:
        info = np.iinfo(dt)
        stack = rng.integers(info.min, int(info.max) + 1, size=(slices, *shape), dtype=dt)
    if dt.kind != "f":  # the extreme in the same pixel of three slices
        stack[:3, 0, 0] = np.iinfo(dt).max
    return stack


def _project(stack: np.ndarray, method: str) -> np.ndarray:
    acc = PlaneAccumulator(stack.shape[1:], stack.dtype, method, stack.shape[0])
    for plane in stack:
        acc.add(plane)
    return acc.result()


def _reference(stack: np.ndarray, method: str) -> np.ndarray:
    """Return the projection from Python ints and one correctly rounded Fraction."""
    out = np.empty(stack.shape[1:], dtype=np.float64 if method == "mean" else object)
    for index in np.ndindex(*stack.shape[1:]):
        values = [stack[(z, *index)].item() for z in range(stack.shape[0])]
        if stack.dtype.kind == "f":
            values = [Fraction(v) for v in values]
        if method == "max":
            out[index] = max(values)
        elif method == "sum":
            out[index] = sum(values)
        else:
            out[index] = float(Fraction(sum(values), len(values)))
    return out


@pytest.mark.parametrize(("dtype", "method"), sorted(TABLE))
def test_output_type_comes_from_the_table(dtype, method):
    rule = projection_dtype(dtype, method, 5)
    assert rule.output_dtype == np.dtype(TABLE[dtype, method])
    assert _project(_stack(dtype), method).dtype == np.dtype(TABLE[dtype, method])


INTEGER_CASES = [case for case in sorted(TABLE) if not case[0].startswith("float")]


@pytest.mark.parametrize(("dtype", "method"), INTEGER_CASES)
def test_integer_values_match_an_independent_reference(dtype, method):
    stack = _stack(dtype)
    got = _project(stack, method)
    want = _reference(stack, method)
    for index in np.ndindex(*got.shape):
        assert got[index].item() == want[index], (dtype, method, index)


@pytest.mark.parametrize("method", ["max", "mean"])
@pytest.mark.parametrize("dtype", ["float32", "float64"])
def test_float_max_and_mean_match_the_reference(dtype, method):
    stack = _stack(dtype)
    got = _project(stack, method)
    want = _reference(stack, method)
    for index in np.ndindex(*got.shape):
        if method == "max":
            assert got[index].item() == float(want[index])
        else:
            # float64 additions in order, one division: the reference is exact, so allow
            # the additions' rounding only.
            assert got[index].item() == pytest.approx(float(want[index]), rel=1e-12)


def test_float_sum_adds_in_float64_ascending():
    stack = _stack("float32")
    got = _project(stack, "sum")
    running = np.zeros(stack.shape[1:], dtype=np.float64)
    for plane in stack:
        running = running + plane.astype(np.float64)
    assert got.tobytes() == running.tobytes()


def test_uint16_sum_does_not_wrap():
    """The overflow regression: 65535 three times is 196605, not 65533."""
    stack = np.full((3, 2, 2), 65535, dtype=np.uint16)
    summed = _project(stack, "sum")
    assert summed.dtype == np.uint32
    assert (summed == 196605).all()
    assert int(stack.sum(axis=0, dtype=np.uint16)[0, 0]) == 65533  # what wrapping gives


@pytest.mark.parametrize(
    ("dtype", "method", "bound"),
    [
        ("uint8", "sum", 16843009),
        ("uint16", "sum", 65537),
        ("int8", "sum", 2**24),
        ("int16", "sum", 65536),
        ("uint32", "sum", 2**21),
        ("int32", "sum", 2**22),
        ("uint32", "mean", 2**21),
    ],
)
def test_the_bound_is_accepted_and_one_more_is_refused(dtype, method, bound):
    rule = projection_dtype(dtype, method, bound)
    assert rule.max_slices == bound
    with pytest.raises(ProjectionRefused) as refused:
        projection_dtype(dtype, method, bound + 1)
    assert refused.value.code == "projection_cannot_be_exact"
    assert str(bound) in str(refused.value)


@pytest.mark.parametrize("dtype", ["uint8", "int16", "uint32", "float32"])
def test_max_has_no_bound(dtype):
    assert projection_dtype(dtype, "max", 10**9).max_slices is None


def test_bounds_hold_at_the_extremes():
    """The worst case at the bound is representable; one more slice is not."""
    for dtype, out in (("int8", np.int32), ("int16", np.int32), ("uint16", np.uint32)):
        rule = projection_dtype(dtype, "sum", 1)
        info, oinfo = np.iinfo(dtype), np.iinfo(out)
        assert rule.max_slices * int(info.max) <= int(oinfo.max)
        assert rule.max_slices * int(info.min) >= int(oinfo.min)
        assert (rule.max_slices + 1) * int(info.max) > int(oinfo.max) or (
            rule.max_slices + 1
        ) * int(info.min) < int(oinfo.min)


@pytest.mark.parametrize("dtype", ["int64", "uint64", "bool", "float16", "complex64"])
@pytest.mark.parametrize("method", ["max", "mean", "sum"])
def test_types_outside_the_table_are_refused(dtype, method):
    with pytest.raises(ProjectionRefused) as refused:
        projection_dtype(dtype, method, 2)
    assert refused.value.code == "cannot_convert_pixel_type"


def test_byte_order_does_not_change_the_decision():
    assert projection_dtype(">u2", "sum", 3).output_dtype == np.dtype("uint32")


def test_nan_propagates_and_inf_is_kept():
    stack = np.array([[[1.0, np.inf, 2.0]], [[np.nan, 1.0, 3.0]], [[5.0, 1.0, np.inf]]])
    for method in ("max", "sum", "mean"):
        got = _project(stack, method)
        assert np.isnan(got[0, 0])
        assert got[0, 1] == np.inf
        assert got[0, 2] == np.inf


def test_a_nan_wins_over_an_infinity_in_a_max():
    stack = np.array([[[np.inf]], [[np.nan]]])
    assert np.isnan(_project(stack, "max")[0, 0])


def test_a_finite_sum_to_infinity_is_refused_with_its_pixel_count():
    big = np.finfo(np.float64).max
    stack = np.array([[[big, 1.0, big]], [[big, 1.0, big]]])
    for method in ("sum", "mean"):
        with pytest.raises(ProjectionRefused) as refused:
            _project(stack, method)
        assert refused.value.code == "projection_overflow"
        assert "2 pixels" in str(refused.value)


def test_float32_sum_is_float64_and_cannot_overflow():
    big = np.finfo(np.float32).max
    stack = np.full((2, 1, 1), big, dtype=np.float32)
    assert _project(stack, "sum")[0, 0] == 2.0 * float(big)


def test_an_infinity_added_to_its_opposite_is_refused():
    stack = np.array([[[np.inf]], [[-np.inf]]])
    with pytest.raises(ProjectionRefused) as refused:
        _project(stack, "sum")
    assert refused.value.code == "projection_undefined"
    assert np.isinf(_project(stack, "max")[0, 0])  # a max is defined


def test_one_slice_gives_the_same_values_in_the_methods_type():
    plane = np.arange(6, dtype=np.uint16).reshape(1, 2, 3)
    for method in ("max", "mean", "sum"):
        got = _project(plane, method)
        assert got.dtype == np.dtype(TABLE["uint16", method])
        assert (got == plane[0]).all()


def test_the_projection_is_deterministic():
    stack = _stack("float32", slices=7, seed=3)
    assert _project(stack, "mean").tobytes() == _project(stack, "mean").tobytes()


def test_rule_records_the_arithmetic():
    rule = projection_dtype("uint16", "sum", 3)
    assert rule.dtype_rule == "sum uint16->uint32 exact, bound Z<=65537"
    assert rule.rounding == "none"
    assert rule.significant_bits_dropped
    assert projection_dtype("uint16", "mean", 3).rounding == "float64, one division"
    assert projection_dtype("float32", "sum", 3).rounding == "float64 addition, z ascending"
    assert projection_dtype("uint16", "max", 3).significant_bits_dropped is None
    assert projection_dtype("uint16", "max", 3).rounding == "none"


def test_working_memory_counts_the_planes_held():
    rule = projection_dtype("uint16", "max", 4)
    assert rule.working_nbytes((10, 10)) == 100 * (2 + 2)
    rule = projection_dtype("uint16", "sum", 4)  # input + uint32 running plane
    assert rule.working_nbytes((10, 10)) == 100 * (2 + 4)
    rule = projection_dtype("uint16", "mean", 4)  # input + uint64 running + float64 out
    assert rule.working_nbytes((10, 10)) == 100 * (2 + 8 + 8)
    rule = projection_dtype("float32", "sum", 4)  # input + float64 + flag
    assert rule.working_nbytes((10, 10)) == 100 * (4 + 8 + 1)


def test_the_accumulator_refuses_what_it_was_not_told():
    acc = PlaneAccumulator((2, 2), "uint16", "sum", 2)
    with pytest.raises(ValueError, match="not"):
        acc.add(np.zeros((2, 2), dtype=np.uint8))  # a slice is never cast
    with pytest.raises(ValueError, match="not"):
        acc.add(np.zeros((3, 2), dtype=np.uint16))
    acc.add(np.zeros((2, 2), dtype=np.uint16))
    with pytest.raises(ValueError, match="declared"):
        acc.result()  # one of two slices
    acc.add(np.zeros((2, 2), dtype=np.uint16))
    with pytest.raises(ValueError, match="more than"):
        acc.add(np.zeros((2, 2), dtype=np.uint16))


def test_the_request_is_checked_before_anything_is_read():
    with pytest.raises(ValueError, match="needs a method"):
        Projection("median")  # type: ignore[arg-type]
    for bad in [(3, 3), (-1, 2), (2, 1), (0, 1.5), (0,), "02", (True, 2)]:
        with pytest.raises(ValueError, match="START:STOP"):
            Projection("max", bad)  # type: ignore[arg-type]
    assert Projection("max", [2, 5]).z_range == (2, 5)  # type: ignore[arg-type]
    assert Projection("max", (4, 5)).resolve(9) == (4, 5)  # one stated slice
    assert Projection("sum").resolve(9) == (0, 9)
    with pytest.raises(ValueError, match="does not fit"):
        Projection("sum", (0, 20)).resolve(12)
    with pytest.raises(ValueError, match="at least one slice"):
        projection_dtype("uint8", "max", 0)
