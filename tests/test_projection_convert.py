"""``convert_experiment(projection=...)``: recorded 2-D copies of Z-stacks (issue #176).

The regression is ``test_project_max_makes_a_z_stack_analysable``: before the projection a
Z-stack could only be refused. Arithmetic and the dtype table are in ``test_projection``.
"""

import os
from fractions import Fraction

import numpy as np
import pytest
import tifffile

import morphobricks_preprocessing.conversion_write as write_module
from described_tree import run_described
from morphobricks_preprocessing import (
    AxisMapping,
    Projection,
    convert_experiment,
    file_sha256,
    preflight,
    read_conversion_record,
)
from morphobricks_preprocessing.conversion_record import PROJECTED
from test_convert_run import codes
from tiff_tree import write_plain
from zarr_tree import write_ngff

ZCYX = AxisMapping("ZCYX", tuple("ZCYX"))
ZYX = AxisMapping("ZYX", tuple("ZYX"))
NAMES = ("ctrl/r1/a.tif", "drug/r1/a.tif")


def write_stack(path, data, axes, **sizes):
    """Write a named OME-TIFF stack; ``sizes`` are OME physical sizes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = {"axes": axes, **sizes}
    tifffile.imwrite(path, data, ome=True, photometric="minisblack", metadata=meta)
    return path


def stack_experiment(root, dtype=np.uint16, names=NAMES, slices=5, axes="ZCYX", seed=0):
    """Images with ``slices`` slices, 3 channels, Y=8, X=9; returns the data by name."""
    rng = np.random.default_rng(seed)
    shape = (slices, 3, 8, 9) if axes == "ZCYX" else (slices, 8, 9)
    info = np.iinfo(dtype)
    data = {}
    for name in names:
        data[name] = rng.integers(info.min, int(info.max) + 1, shape, dtype=dtype)
        write_stack(
            root / name, data[name], axes, PhysicalSizeZ=2.5, PhysicalSizeX=0.5, PhysicalSizeY=0.5
        )
    return data


def tree_files(folder):
    return sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file())


def test_project_max_makes_a_z_stack_analysable(tmp_path):
    """The regression: a Z-stack could only be refused; now it becomes a recorded 2-D copy."""
    exp, copies = tmp_path / "exp", tmp_path / "copies"
    data = stack_experiment(exp)
    source = exp / NAMES[0]
    before = file_sha256(source)
    # Without a projection the stack is still refused, by the check and by convert.
    assert "z_stack_not_supported" in codes(preflight(exp, tmp_path / "o").metadata.issues)
    refused = convert_experiment(exp, ZCYX, tmp_path / "plain")
    assert not refused.written and "z_stack_not_supported" in codes(refused.issues)
    assert not (tmp_path / "plain").exists() or not tree_files(tmp_path / "plain")

    result = convert_experiment(exp, ZCYX, copies, projection=Projection("max"))

    assert result.written and not result.issues
    assert result.projected == NAMES and result.suggested_axes == "CYX"
    copy = tifffile.imread(copies / NAMES[0])
    assert copy.dtype == np.uint16 and copy.shape == (3, 8, 9)
    assert (copy == data[NAMES[0]].max(axis=0)).all()
    with tifffile.TiffFile(copies / NAMES[0]) as tif:
        assert tif.series[0].axes == "CYX"
    # The copy has no Z-stack: the check on the tree has no z_stack_not_supported.
    assert "z_stack_not_supported" not in codes(preflight(copies, tmp_path / "o2").metadata.issues)
    record, _ = read_conversion_record(copies)
    entry = record.entry(NAMES[0])
    assert entry.action == PROJECTED and record.projection == Projection("max")
    details = entry.projection
    assert details.source_content_sha256 == before
    assert details.source_z_axis == ("Z", 0) and details.source_z_size == 5
    assert details.z_range == (0, 5) and details.dtype_rule == "max uint16->uint16"
    z_size = details.dropped_sizes["Z"]
    assert (z_size.value, z_size.unit, z_size.source) == (2.5, "micrometer", "header")
    assert entry.copy_sha256 == file_sha256(copies / NAMES[0])
    assert file_sha256(source) == before  # the source is only read


def test_the_copy_describes_itself_and_does_not_carry_z(tmp_path):
    exp, copies = tmp_path / "exp", tmp_path / "copies"
    stack_experiment(exp, names=NAMES[:1])
    assert convert_experiment(exp, ZCYX, copies, projection=Projection("max", (1, 4))).written
    with tifffile.TiffFile(copies / NAMES[0]) as tif:
        xml = tif.ome_metadata
    sha = file_sha256(exp / NAMES[0])
    assert "<Description>MorphoBricks Z projection: max of slices 1:4 (of 5)" in xml
    assert sha in xml and "ctrl/r1/a.tif" in xml
    assert "PhysicalSizeZ" not in xml and 'PhysicalSizeX="0.5"' in xml
    record, _ = read_conversion_record(copies)
    assert record.entry(NAMES[0]).projection.z_range == (1, 4)
    assert record.projection == Projection("max", (1, 4))


@pytest.mark.parametrize("dtype", [np.uint8, np.uint16, np.int16, np.uint32])
@pytest.mark.parametrize("method", ["max", "sum", "mean"])
def test_the_copy_has_the_tables_type_and_exact_values(tmp_path, dtype, method):
    exp, copies = tmp_path / "exp", tmp_path / "copies"
    data = stack_experiment(exp, dtype=dtype, names=NAMES[:1], slices=4)[NAMES[0]]
    assert convert_experiment(exp, ZCYX, copies, projection=Projection(method)).written
    copy = tifffile.imread(copies / NAMES[0])
    want_type = {
        "max": np.dtype(dtype),
        "mean": np.dtype("float64"),
        "sum": np.dtype({"uint8": "uint32", "uint16": "uint32", "int16": "int32"}.get(
            np.dtype(dtype).name, "float64")),
    }[method]  # fmt: skip
    assert copy.dtype == want_type
    for index in np.ndindex(*copy.shape):
        values = [int(data[(z, *index)]) for z in range(4)]
        want = {"max": max(values), "sum": sum(values), "mean": float(Fraction(sum(values), 4))}
        assert copy[index].item() == want[method], (method, index)


def test_a_uint16_sum_does_not_wrap_through_the_copy(tmp_path):
    exp, copies = tmp_path / "exp", tmp_path / "copies"
    write_stack(exp / "a.tif", np.full((3, 4, 5), 65535, np.uint16), "ZYX")
    assert convert_experiment(exp, ZYX, copies, projection=Projection("sum")).written
    copy = tifffile.imread(copies / "a.tif")
    assert copy.dtype == np.uint32 and (copy == 196605).all()


def test_nan_propagates_and_infinity_is_kept(tmp_path):
    exp, copies = tmp_path / "exp", tmp_path / "copies"
    data = np.ones((3, 2, 3), np.float32)
    data[1, 0, 0] = np.nan
    data[0, 0, 1] = np.inf
    write_stack(exp / "a.tif", data, "ZYX")
    assert convert_experiment(exp, ZYX, copies, projection=Projection("sum")).written
    copy = tifffile.imread(copies / "a.tif")
    assert copy.dtype == np.float64 and np.isnan(copy[0, 0]) and copy[0, 1] == np.inf
    assert copy[1, 2] == 3.0


def test_a_sum_that_overflows_to_infinity_is_refused_and_nothing_is_written(tmp_path):
    exp, copies = tmp_path / "exp", tmp_path / "copies"
    big = np.finfo(np.float64).max
    write_stack(exp / "a.tif", np.full((2, 2, 2), big), "ZYX")
    result = convert_experiment(exp, ZYX, copies, projection=Projection("sum"))
    assert not result.written and codes(result.issues) == ["projection_overflow"]
    assert "a.tif" in result.issues[0].message and "4 pixels" in result.issues[0].message
    assert not copies.exists() or not tree_files(copies)


def test_two_conversions_give_the_same_bytes_and_a_rerun_reuses_everything(tmp_path):
    exp = tmp_path / "exp"
    stack_experiment(exp, dtype=np.uint8)
    first = convert_experiment(exp, ZCYX, tmp_path / "c1", projection=Projection("mean"))
    second = convert_experiment(exp, ZCYX, tmp_path / "c2", projection=Projection("mean"))
    assert first.written and second.written
    rec1, rec2 = (
        read_conversion_record(tmp_path / "c1")[0],
        read_conversion_record(tmp_path / "c2")[0],
    )
    for name in NAMES:
        assert (tmp_path / "c1" / name).read_bytes() == (tmp_path / "c2" / name).read_bytes()
        assert rec1.entry(name).copy_sha256 == rec2.entry(name).copy_sha256
        assert rec1.entry(name).projection == rec2.entry(name).projection
    # A rerun reuses every copy and leaves the record as it was.
    again = convert_experiment(exp, ZCYX, tmp_path / "c1", projection=Projection("mean"))
    assert again.written and again.reused == NAMES and not again.projected
    assert again.record_sha256 == first.record_sha256


def test_another_method_or_range_into_the_same_tree_is_refused(tmp_path):
    exp, copies = tmp_path / "exp", tmp_path / "copies"
    stack_experiment(exp, names=NAMES[:1])
    assert convert_experiment(exp, ZCYX, copies, projection=Projection("max")).written
    snapshot = {p: (copies / p).read_bytes() for p in tree_files(copies)}
    for other in (Projection("mean"), Projection("max", (0, 3)), None):
        result = convert_experiment(exp, ZCYX, copies, projection=other)
        assert not result.written, other
        assert "copy_differs" in codes(result.issues) or "z_stack_not_supported" in codes(
            result.issues
        )
    result = convert_experiment(exp, ZCYX, copies, projection=Projection("mean"))
    assert "holds max projections of all slices" in result.issues[0].message
    assert "asked for mean projections" in result.issues[0].message
    assert {p: (copies / p).read_bytes() for p in tree_files(copies)} == snapshot


def test_t_is_kept_and_each_t_is_projected_on_its_own(tmp_path):
    exp, copies = tmp_path / "exp", tmp_path / "copies"
    data = np.random.default_rng(3).integers(0, 255, (2, 4, 3, 6, 7), dtype=np.uint8)
    write_stack(exp / "a.tif", data, "TZCYX", PhysicalSizeZ=1.0)
    roles = AxisMapping("TZCYX", tuple("TZCYX"))
    result = convert_experiment(exp, roles, copies, projection=Projection("max"))
    assert result.written and result.suggested_axes == "TCYX"
    copy = tifffile.imread(copies / "a.tif")
    assert copy.shape == (2, 3, 6, 7) and (copy == data.max(axis=1)).all()


def test_a_slice_range_uses_only_those_slices_and_a_single_slice_is_a_range(tmp_path):
    exp = tmp_path / "exp"
    data = stack_experiment(exp, names=NAMES[:1], slices=8)[NAMES[0]]
    assert convert_experiment(
        exp, ZCYX, tmp_path / "r", projection=Projection("max", (2, 5))
    ).written
    assert (tifffile.imread(tmp_path / "r" / NAMES[0]) == data[2:5].max(axis=0)).all()
    one = Projection("max", (4, 5))
    assert convert_experiment(exp, ZCYX, tmp_path / "s", projection=one).written
    assert (tifffile.imread(tmp_path / "s" / NAMES[0]) == data[4]).all()
    record, _ = read_conversion_record(tmp_path / "s")
    assert record.entry(NAMES[0]).projection.z_range == (4, 5)


def test_a_range_no_image_has_is_refused_and_names_the_short_ones(tmp_path):
    exp, copies = tmp_path / "exp", tmp_path / "copies"
    stack_experiment(exp, names=NAMES[:1], slices=8)
    stack_experiment(exp, names=NAMES[1:], slices=3, seed=1)
    result = convert_experiment(exp, ZCYX, copies, projection=Projection("max", (0, 6)))
    assert not result.written and result.issues[0].code == "projection_z_range_outside"
    assert result.issues[0].paths == (NAMES[1],) and "3 slices" in result.issues[0].message
    assert not copies.exists() or not tree_files(copies)


def test_every_image_goes_through_the_method_also_one_slice_and_no_z(tmp_path):
    exp, copies = tmp_path / "exp", tmp_path / "copies"
    deep = np.random.default_rng(5).integers(0, 1000, (3, 4, 5), dtype=np.uint16)
    write_stack(exp / "a.tif", deep, "ZYX")
    write_stack(exp / "b.tif", deep[:1], "ZYX")  # one slice
    write_stack(exp / "c.tif", deep[0], "YX")  # no Z axis
    result = convert_experiment(
        exp, ZYX, copies, projection=Projection("sum"), missing_as_singleton=True
    )
    assert result.written and result.projected == ("a.tif", "b.tif", "c.tif")
    for name, want in (("a.tif", deep.sum(0)), ("b.tif", deep[0]), ("c.tif", deep[0])):
        copy = tifffile.imread(copies / name)
        assert copy.dtype == np.uint32 and (copy == want).all(), name
    record, _ = read_conversion_record(copies)
    assert record.entry("c.tif").projection.source_z_axis is None
    assert record.entry("c.tif").projection.source_z_size == 1
    assert record.entry("c.tif").projection.dropped_sizes == {"Z": None}


def test_nothing_to_project_without_a_depth_axis(tmp_path):
    exp = tmp_path / "exp"
    write_stack(exp / "a.tif", np.zeros((3, 4, 5), np.uint8), "CYX")
    result = convert_experiment(exp, AxisMapping("CYX", tuple("CYX")), tmp_path / "c",
                                projection=Projection("max"))  # fmt: skip
    assert not result.written and codes(result.issues) == ["nothing_to_project"]
    assert "CYX" in result.issues[0].message


def test_a_plain_stack_is_projected_only_under_confirmed_roles(tmp_path):
    exp = tmp_path / "exp"
    data = np.random.default_rng(9).integers(0, 255, (4, 6, 5), dtype=np.uint8)
    write_plain(exp / "a.tif", data)
    # No role is guessed: confirmed as time, the stack has no depth axis to project.
    as_time = convert_experiment(
        exp, AxisMapping("IYX", ("T", "Y", "X")), tmp_path / "none", projection=Projection("max")
    )
    assert not as_time.written and codes(as_time.issues) == ["nothing_to_project"]
    result = convert_experiment(exp, AxisMapping("IYX", ("Z", "Y", "X")), tmp_path / "c",
                                projection=Projection("max"))  # fmt: skip
    assert result.written, result.issues
    assert (tifffile.imread(tmp_path / "c" / "a.tif") == data.max(0)).all()
    entry = read_conversion_record(tmp_path / "c")[0].entry("a.tif")
    assert entry.reason == "axes_not_named" and entry.projection.source_z_axis == ("I", 0)


def test_a_header_z_confirmed_as_time_is_still_an_axis_error_corrected_in_the_pass(tmp_path):
    exp = tmp_path / "exp"
    data = np.random.default_rng(2).integers(0, 255, (3, 6, 5), dtype=np.uint8)
    write_stack(exp / "a.tif", data, "ZYX")
    result = convert_experiment(exp, AxisMapping("ZYX", ("T", "Y", "X")), tmp_path / "c",
                                projection=Projection("max"))  # fmt: skip
    assert not result.written and "nothing_to_project" in codes(result.issues)


def test_64_bit_integers_are_refused(tmp_path):
    exp = tmp_path / "exp"
    write_plain(exp / "a.tif", np.zeros((3, 4, 5), np.uint64))
    result = convert_experiment(
        exp, AxisMapping("IYX", ("Z", "Y", "X")), tmp_path / "c", projection=Projection("max")
    )
    assert not result.written and codes(result.issues) == ["cannot_convert_pixel_type"]


def test_a_sum_that_could_not_be_exact_is_refused_at_the_plan(tmp_path, monkeypatch):
    import morphobricks_preprocessing.projection as projection_module

    exp = tmp_path / "exp"
    write_stack(exp / "a.tif", np.zeros((3, 4, 5), np.uint32), "ZYX")
    monkeypatch.setattr(projection_module, "EXACT_FLOAT_INTEGER", 2**32)  # a bound of 1 slice
    result = convert_experiment(exp, ZYX, tmp_path / "c", projection=Projection("sum"))
    assert not result.written and codes(result.issues) == ["projection_cannot_be_exact"]
    assert not (tmp_path / "c").exists() or not tree_files(tmp_path / "c")


def test_a_plane_and_its_running_total_over_the_memory_limit_is_refused(tmp_path):
    exp = tmp_path / "exp"
    write_stack(exp / "a.tif", np.zeros((3, 40, 50), np.uint16), "ZYX")
    need = 40 * 50 * (2 + 4)  # input plane + uint32 running plane
    ok = convert_experiment(exp, ZYX, tmp_path / "ok", projection=Projection("sum"),
                            memory_limit=need)  # fmt: skip
    assert ok.written
    result = convert_experiment(exp, ZYX, tmp_path / "c", projection=Projection("sum"),
                                memory_limit=need - 1)  # fmt: skip
    assert not result.written and codes(result.issues) == ["projection_over_memory_limit"]
    assert "a.tif" in result.issues[0].message and "limit" in result.issues[0].message


def test_an_ome_zarr_stack_is_refused_for_now(tmp_path):
    exp = tmp_path / "exp"
    write_ngff(exp / "c" / "a.zarr", np.zeros((3, 4, 5), np.uint16), axes="zyx")
    result = convert_experiment(exp, ZYX, tmp_path / "c", projection=Projection("max"))
    assert not result.written and "projection_zarr_not_supported" in codes(result.issues)


def test_the_projection_must_be_a_projection(tmp_path):
    exp = tmp_path / "exp"
    stack_experiment(exp, names=NAMES[:1])
    with pytest.raises(ValueError, match="Projection or None"):
        convert_experiment(exp, ZCYX, tmp_path / "c", projection="max")
    with pytest.raises(TypeError, match="unexpected"):
        convert_experiment(exp, ZCYX, tmp_path / "c", projectionn=None)


# Masks -------------------------------------------------------------------------------------


def write_mask(path, shape, axes):
    path.parent.mkdir(parents=True, exist_ok=True)
    labels = np.zeros(shape, np.uint16)
    labels[..., 1:3, 1:3] = 1
    tifffile.imwrite(path, labels, photometric="minisblack", metadata={"axes": axes})


def test_a_3d_mask_stops_the_projection_before_anything_is_written(tmp_path):
    exp, masks, copies = tmp_path / "exp", tmp_path / "masks", tmp_path / "copies"
    stack_experiment(exp, names=NAMES[:1])
    write_mask(masks / "Nuclei" / "ctrl" / "r1" / "a.tif", (5, 8, 9), "ZYX")
    result = convert_experiment(exp, ZCYX, copies, projection=Projection("max"),
                                masks_folder=masks)  # fmt: skip
    assert not result.written and codes(result.issues) == ["projection_masks_3d"]
    assert "Nuclei" in result.issues[0].message and "never projected" in result.issues[0].message
    assert not copies.exists() or not tree_files(copies)


def test_a_mask_of_unknown_axes_with_more_than_yx_is_refused_not_guessed(tmp_path):
    exp, masks = tmp_path / "exp", tmp_path / "masks"
    stack_experiment(exp, names=NAMES[:1])
    path = masks / "Nuclei" / "ctrl" / "r1" / "a.tif"
    path.parent.mkdir(parents=True)
    tifffile.imwrite(path, np.zeros((3, 8, 9), np.uint16), photometric="minisblack", metadata=None)
    result = convert_experiment(exp, ZCYX, tmp_path / "c", projection=Projection("max"),
                                masks_folder=masks)  # fmt: skip
    assert codes(result.issues) == ["projection_masks_axes_unknown"] and not result.written


def test_2d_masks_pair_with_the_projected_images(tmp_path):
    exp, masks, copies = tmp_path / "exp", tmp_path / "masks", tmp_path / "copies"
    stack_experiment(exp, names=NAMES[:1])
    mask = masks / "Nuclei" / "ctrl" / "r1" / "a.tif"
    write_mask(mask, (8, 9), "YX")
    before = mask.read_bytes()
    result = convert_experiment(exp, ZCYX, copies, projection=Projection("max"),
                                masks_folder=masks)  # fmt: skip
    assert result.written, result.issues
    run = run_described(copies, AxisMapping("CYX", tuple("CYX")), output_folder=tmp_path / "out",
                         masks_folder=masks)  # fmt: skip
    assert run.written, run.issues
    assert mask.read_bytes() == before


def test_the_source_content_is_checked_again_after_the_projection(tmp_path, monkeypatch):
    exp, copies = tmp_path / "exp", tmp_path / "copies"
    stack_experiment(exp, names=NAMES[:1])
    real = write_module.source_content_sha256
    calls = []

    def changing(path):
        calls.append(path)
        return real(path) if len(calls) == 1 else "0" * 64

    monkeypatch.setattr(write_module, "source_content_sha256", changing)
    result = convert_experiment(exp, ZCYX, copies, projection=Projection("max"))
    assert not result.written and "content changed" in result.issues[0].message
    assert not copies.exists() or not tree_files(copies)
    assert (
        not [p for p in os.listdir(copies) if p.endswith(".partial")] if copies.exists() else True
    )
