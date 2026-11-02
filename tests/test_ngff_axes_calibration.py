"""OME-Zarr axes and calibration: nothing guessed, nothing silent (issues #72, #73, #94)."""

import json

import numpy as np
import pytest
import tifffile

from morphobricks_preprocessing import (
    AxisMapping,
    NgffError,
    PhysicalSizes,
    file_mapping_for,
    inspect_image,
    open_ngff,
    preflight,
    review_axes,
    run_experiment,
    zarr_input,
)
from morphobricks_preprocessing.zarr_input import ngff_axes
from tiff_tree import write_data
from zarr_tree import write_ngff

zarr = pytest.importorskip("zarr")

VERSIONS = ["0.4", "0.5"]


def codes(issues):
    return [issue.code for issue in issues]


def issue(issues, code):
    (found,) = [i for i in issues if i.code == code]
    return found


def data(shape):
    return np.arange(int(np.prod(shape)), dtype=np.uint8).reshape(shape)


def edit_multiscale(path, change):
    """Apply ``change`` to the first multiscales entry of a 0.4 or 0.5 store."""
    group = zarr.open_group(str(path), mode="r+")
    attrs = dict(group.attrs)
    ome = attrs.get("ome")
    meta = dict(ome) if isinstance(ome, dict) else attrs
    multiscales = [dict(m) for m in meta["multiscales"]]
    change(multiscales[0], meta)
    meta["multiscales"] = multiscales
    if isinstance(ome, dict):
        group.attrs["ome"] = meta
    else:
        group.attrs.update(meta)
    return path


# Issue #72: same axis letters, contradictory confirmed roles --------------------------------


@pytest.fixture
def zyx_store(tmp_path):
    return write_ngff(tmp_path / "exp/ctrl/a.ome.zarr", data((3, 6, 8)), "zyx")


@pytest.mark.parametrize("roles", ["TYX", "ZXY", "CYX"])
def test_confirmed_roles_that_contradict_ngff_axes_are_refused(zyx_store, roles):
    meta = inspect_image(zyx_store, "ctrl/a.ome.zarr")
    assert meta.axes == "ZYX"
    mapping = AxisMapping("ZYX", roles)
    review = review_axes([meta], mapping)
    found = issue(review.issues, "ome_zarr_axes_mismatch")
    assert found.is_error and found.paths == ("ctrl/a.ome.zarr",)
    assert f"declared ZYX, confirmed {roles}: ctrl/a.ome.zarr" in found.message
    assert "ctrl/a.ome.zarr" not in review.file_mappings
    assert file_mapping_for(meta, mapping) is None


def test_a_role_for_an_axis_of_no_known_type_is_refused(tmp_path):
    path = write_ngff(
        tmp_path / "a.ome.zarr", data((3, 6, 8)), "qyx", axis_types=["other", "space", "space"]
    )
    meta = inspect_image(path, "a.ome.zarr")
    assert meta.axes == "QYX"
    mapping = AxisMapping("QYX", "CYX")
    review = review_axes([meta], mapping)
    assert "declared QYX, confirmed CYX" in issue(review.issues, "ome_zarr_axes_mismatch").message
    assert review.file_mappings == {}
    assert file_mapping_for(meta, mapping) is None


def test_a_run_refuses_ngff_depth_confirmed_as_time(zyx_store, tmp_path):
    result = run_experiment(
        tmp_path / "exp", AxisMapping("ZYX", "TYX"), output_folder=tmp_path / "o"
    )
    assert not result.written
    assert "ome_zarr_axes_mismatch" in codes(result.issues)
    assert not (tmp_path / "o" / "run_manifest.json").exists()


def test_roles_that_agree_with_ngff_axes_are_read_as_declared(zyx_store, tmp_path):
    write_data(tmp_path / "exp/ctrl/b.tif", data((3, 6, 8)), axes="ZYX", imagej=True)
    mapping = AxisMapping("ZYX", "ZYX")
    result = run_experiment(tmp_path / "exp", mapping, output_folder=tmp_path / "o")
    assert result.written, result.issues
    assert "ome_zarr_axes_mismatch" not in codes(result.issues)
    meta = inspect_image(zyx_store, "ctrl/a.ome.zarr")
    assert file_mapping_for(meta, mapping) == mapping


def test_another_arrangement_is_still_matched_by_role(tmp_path):
    tiff = tmp_path / "exp/ctrl/a.tif"
    write_data(tiff, data((3, 2, 6, 8)), axes="ZCYX", imagej=True)
    store = write_ngff(tmp_path / "exp/ctrl/b.ome.zarr", data((2, 3, 6, 8)), "czyx")
    mapping = AxisMapping("ZCYX", "ZCYX")
    review = review_axes(
        [inspect_image(tiff, "a.tif"), inspect_image(store, "b.ome.zarr")], mapping
    )
    assert not review.issues
    assert review.file_mappings["b.ome.zarr"].roles == ("C", "Z", "Y", "X")


# Issue #73: missing axes are never guessed -------------------------------------------------


def drop_axes(multiscale, meta):
    multiscale.pop("axes")


@pytest.mark.parametrize("version", VERSIONS)
def test_modern_ngff_without_axes_is_unreadable(tmp_path, version):
    # A 5-D array in a non-TCZYX order whose required axes were removed.
    path = write_ngff(
        tmp_path / "exp/ctrl/a.ome.zarr", data((2, 3, 4, 6, 8)), "zctyx", version=version
    )
    edit_multiscale(path, drop_axes)
    with pytest.raises(NgffError, match=f"OME-NGFF {version} .*no axes"):
        open_ngff(path)
    pre = preflight(tmp_path / "exp", tmp_path / "o")
    found = issue(pre.issues, "unreadable_ome_zarr")
    assert found.is_error and found.paths == ("ctrl/a.ome.zarr",)


def test_ngff_without_version_or_axes_is_unreadable(tmp_path):
    def unversioned(multiscale, meta):
        multiscale.pop("axes")
        multiscale.pop("version", None)

    path = edit_multiscale(
        write_ngff(tmp_path / "a.ome.zarr", data((2, 3, 4, 6, 8)), "tczyx"), unversioned
    )
    with pytest.raises(NgffError, match="states no OME-NGFF version"):
        open_ngff(path)
    assert not inspect_image(path, "a.ome.zarr").readable


@pytest.mark.parametrize("version", ["0.1", "0.2"])
def test_early_ngff_axes_are_those_its_specification_fixes(tmp_path, version):
    def early(multiscale, meta):
        multiscale.pop("axes")
        multiscale.pop("coordinateTransformations", None)
        multiscale["datasets"] = [{"path": "0"}]
        multiscale["version"] = version

    path = edit_multiscale(
        write_ngff(tmp_path / "a.ome.zarr", data((1, 2, 3, 6, 8)), "tczyx"), early
    )
    image = open_ngff(path)
    assert (image.axes, image.version) == ("TCZYX", version)


def test_ngff_axes_without_metadata_follow_the_version():
    assert ngff_axes(None, 5, "0.1") == ngff_axes([], 5, "0.2") == "TCZYX"
    with pytest.raises(NgffError, match="5-D"):
        ngff_axes(None, 3, "0.2")
    for version in ("", "0.3", "0.4", "0.5", "0.6"):
        with pytest.raises(NgffError):
            ngff_axes(None, 5, version)
    with pytest.raises(NgffError, match="not a list"):
        ngff_axes("tczyx", 5, "0.4")


# Issue #94: a malformed calibration is never dropped silently -------------------------------


MALFORMED = {
    "not a list": ({"type": "scale", "scale": [2.0, 0.5, 0.25]}, "are not a list"),
    "in a file": ([{"type": "scale", "path": "scale.bin"}], "stored in a separate file"),
    "wrong length": ([{"type": "scale", "scale": [0.5, 0.25]}], "2 values for 3 axes"),
    "other type": ([{"type": "affine", "affine": [[1, 0]]}], "type 'affine'"),
    "two scales": ([{"type": "scale", "scale": [1, 1, 1]}] * 2, "2 scale transformations"),
    "not numbers": ([{"type": "scale", "scale": "1,1,1"}], "not a list of numbers"),
    "zero and negative": ([{"type": "scale", "scale": [0.0, -1.0, 0.25]}], "gives 0.0 for axis Z"),
    "too large": ([{"type": "scale", "scale": [10**400, 0.5, 0.25]}], "for axis Z"),
}


@pytest.mark.parametrize("case", sorted(MALFORMED))
def test_a_malformed_calibration_says_what_is_wrong(tmp_path, case):
    transforms, reason = MALFORMED[case]
    path = write_ngff(
        tmp_path / "a.ome.zarr", data((3, 6, 8)), "zyx", dataset_transforms=transforms
    )
    sizes = open_ngff(path).physical_sizes
    assert reason in sizes.invalid
    assert sizes.z is None
    assert inspect_image(path, "a.ome.zarr").physical_sizes.invalid == sizes.invalid


def test_a_malformed_image_scale_is_named(tmp_path):
    path = write_ngff(
        tmp_path / "a.ome.zarr",
        data((6, 8)),
        "yx",
        scale=[0.5, 0.25],
        multiscale_transforms=[{"type": "scale", "path": "scale.bin"}],
    )
    sizes = open_ngff(path).physical_sizes
    assert sizes == PhysicalSizes()
    assert "the scale of the image is stored in a separate file" in sizes.invalid


@pytest.mark.parametrize(
    "transforms",
    [None, [], [{"type": "translation", "translation": [0.0, 0.0, 0.0]}]],
)
def test_a_usable_or_absent_calibration_is_not_invalid(tmp_path, transforms):
    path = write_ngff(
        tmp_path / "a.ome.zarr", data((3, 6, 8)), "zyx", dataset_transforms=transforms
    )
    assert open_ngff(path).physical_sizes.invalid is None


def test_every_image_with_a_malformed_calibration_is_named_and_kept_in_the_report(tmp_path):
    root = tmp_path / "exp"
    malformed = {"type": "scale", "scale": [2.0, 0.5, 0.25]}  # a dict, not a list
    for name in ("a", "b"):
        write_ngff(
            root / f"ctrl/{name}.ome.zarr", data((3, 6, 8)), "zyx", dataset_transforms=malformed
        )
    pre = preflight(root, tmp_path / "o")
    assert pre.ok
    found = issue(pre.issues, "invalid_physical_calibration")
    assert not found.is_error
    assert found.paths == ("ctrl/a.ome.zarr", "ctrl/b.ome.zarr")
    assert (
        "ctrl/a.ome.zarr: the coordinateTransformations of level 0 are not a list" in found.message
    )
    result = run_experiment(root, AxisMapping("ZYX", "ZYX"), output_folder=tmp_path / "o")
    assert result.written, result.issues
    assert "invalid_physical_calibration" in codes(result.issues)
    report = json.loads((tmp_path / "o/discovery_report.json").read_text(encoding="utf-8"))
    assert "invalid_physical_calibration" in [i["code"] for i in report["issues"]]


def test_a_stated_calibration_replaces_the_malformed_one(tmp_path):
    root = tmp_path / "exp"
    malformed = {"type": "scale", "scale": [2.0, 0.5, 0.25]}
    write_ngff(root / "ctrl/a.ome.zarr", data((1, 6, 8)), "zyx", dataset_transforms=malformed)
    out = tmp_path / "o"
    out.mkdir()
    (out / "calibration.csv").write_text(
        "target,axis,size,unit\n.,X,0.5,um\n.,Y,0.5,um\n", encoding="utf-8"
    )
    assert "invalid_physical_calibration" not in codes(preflight(root, out).issues)


def test_unexpected_errors_reading_the_scale_are_not_swallowed(tmp_path, monkeypatch):
    # Only malformed metadata is recorded as such; a failure of the reader is not hidden.
    def broken(*args, **kwargs):
        raise RuntimeError("not understood")

    monkeypatch.setattr(zarr_input, "ngff_physical_sizes", broken)
    path = write_ngff(tmp_path / "a.ome.zarr", data((2, 6, 8)), "zyx")
    with pytest.raises(RuntimeError):
        open_ngff(path)
    meta = inspect_image(path, "a.ome.zarr")
    assert not meta.readable and "not understood" in meta.error


def test_tiff_sizes_have_no_calibration_problem(tmp_path):
    tifffile.imwrite(tmp_path / "a.tif", data((6, 8)))
    assert inspect_image(tmp_path / "a.tif", "a.tif").physical_sizes.invalid is None
