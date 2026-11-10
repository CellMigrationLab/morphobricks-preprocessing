"""Size and plane-count warnings follow the confirmed roles of plain stacks (issue #74)."""

import numpy as np
import pytest

from described_tree import run_described
from morphobricks_preprocessing import inspect_images, prefill_mapping
from morphobricks_preprocessing.axes import AxisMapping
from morphobricks_preprocessing.axis_review import review_axes
from tiff_tree import write_data, write_plain, write_tiff


def codes(issues):
    return [issue.code for issue in issues]


def issue(issues, code):
    (found,) = [i for i in issues if i.code == code]
    return found


def plain_yxc(tmp_path):
    """Two (Y, X, C) arrays without axis metadata: tifffile reads them as QYX."""
    write_data(tmp_path / "a.tif", np.zeros((16, 16, 3), np.uint8))
    write_data(tmp_path / "b.tif", np.zeros((32, 16, 3), np.uint8))
    return inspect_images(tmp_path, ["a.tif", "b.tif"])


def test_plain_stacks_are_not_compared_by_their_header_letters(tmp_path):
    report = plain_yxc(tmp_path)
    assert report.files[0].axes == "QYX"
    # Before the roles are confirmed, the header's Q, Y and X say nothing: no warning.
    assert not {"different_sizes", "different_plane_counts"} & set(codes(report.issues))


def test_confirmed_yxc_compares_the_real_heights(tmp_path):
    report = plain_yxc(tmp_path)
    review = review_axes(report, AxisMapping("QYX", "YXC"))
    issues = review.combined_issues(report)
    assert codes(issues) == ["different_sizes", "axes_not_named"]
    sizes = issue(issues, "different_sizes")
    assert "16 x 16 (1 image), 32 x 16 (1 image)" in sizes.message
    assert sizes.paths == ("b.tif",)


@pytest.mark.parametrize(("role", "name"), [("Z", "depth slices (Z)"), ("T", "time points")])
def test_a_plain_stack_confirmed_as_depth_or_time(tmp_path, role, name):
    write_data(tmp_path / "a.tif", np.zeros((3, 8, 8), np.uint8))
    write_data(tmp_path / "b.tif", np.zeros((5, 8, 8), np.uint8))
    report = inspect_images(tmp_path, ["a.tif", "b.tif"])
    assert "different_plane_counts" not in codes(report.issues)
    review = review_axes(report, AxisMapping("QYX", f"{role}YX"))
    issues = review.combined_issues(report)
    planes = issue(issues, "different_plane_counts")
    assert f"{name}: 3 (1 image), 5 (1 image)" in planes.message
    assert "planes along Q" not in planes.message and planes.paths == ("b.tif",)
    assert "different_sizes" not in codes(issues)


def test_a_sequence_of_pages_confirmed_as_time(tmp_path):
    write_plain(tmp_path / "a.tif", np.zeros((2, 8, 8), np.uint8))
    write_plain(tmp_path / "b.tif", np.zeros((4, 8, 8), np.uint8))
    report = inspect_images(tmp_path, ["a.tif", "b.tif"])
    assert report.files[0].axes == "IYX"
    issues = review_axes(report, prefill_mapping("IYX").with_role(0, "T")).combined_issues(report)
    assert codes(issues) == ["different_plane_counts", "axes_not_named"]
    planes = issue(issues, "different_plane_counts")
    assert "time points: 2 (1 image), 4 (1 image)" in planes.message


def test_named_and_plain_images_together(tmp_path):
    write_tiff(tmp_path / "named.tif", (8, 9), axes="YX")
    write_data(tmp_path / "plain.tif", np.zeros((5, 8, 8), np.uint8))
    report = inspect_images(tmp_path, ["named.tif", "plain.tif"])
    # Only the named image is measured before the confirmation: nothing to compare.
    assert not {"different_sizes", "different_plane_counts"} & set(codes(report.issues))
    review = review_axes(report, AxisMapping("QYX", "TYX"), missing_as_singleton=True)
    issues = review.combined_issues(report)
    assert codes(issues).count("different_sizes") == 1
    assert codes(issues).count("different_plane_counts") == 1
    assert "8 x 8 (1 image), 8 x 9 (1 image)" in issue(issues, "different_sizes").message
    planes = issue(issues, "different_plane_counts")
    assert "time points: 1 (1 image), 5 (1 image)" in planes.message


def test_a_plain_tiff_with_an_ome_zarr_image(tmp_path):
    pytest.importorskip("zarr")
    from zarr_tree import write_ngff

    write_ngff(tmp_path / "z.ome.zarr", np.zeros((3, 8, 10), np.uint8), "tyx")
    write_data(tmp_path / "plain.tif", np.zeros((5, 8, 8), np.uint8))
    report = inspect_images(tmp_path, ["plain.tif", "z.ome.zarr"])
    review = review_axes(report, AxisMapping("QYX", "TYX"))
    issues = review.combined_issues(report)
    assert "8 x 10 (1 image), 8 x 8 (1 image)" in issue(issues, "different_sizes").message
    planes = issue(issues, "different_plane_counts")
    assert "time points: 3 (1 image), 5 (1 image)" in planes.message
    assert "planes along Q" not in planes.message


def test_the_run_reports_the_warnings_of_the_confirmed_roles(tmp_path):
    root = tmp_path / "exp"
    write_data(root / "a.tif", np.ones((16, 16, 3), np.uint16))
    write_data(root / "b.tif", np.ones((32, 16, 3), np.uint16))
    result = run_described(root, AxisMapping("QYX", "YXC"))
    assert "different_plane_counts" not in codes(result.issues)
    sizes = issue(result.issues, "different_sizes")
    assert "16 x 16 (1 image), 32 x 16 (1 image)" in sizes.message


def test_contradicted_roles_are_never_used_for_the_warnings(tmp_path):
    # OME-Zarr ZYX confirmed as TYX contradicts its OME-NGFF axes (issue #72): it is
    # measured by the Z its metadata names, never as time points.
    pytest.importorskip("zarr")
    from zarr_tree import write_ngff

    write_ngff(tmp_path / "a.ome.zarr", np.zeros((3, 8, 8), np.uint8), "zyx")
    write_ngff(tmp_path / "b.ome.zarr", np.zeros((5, 8, 8), np.uint8), "zyx")
    report = inspect_images(tmp_path, ["a.ome.zarr", "b.ome.zarr"])
    review = review_axes(report, AxisMapping("ZYX", "TYX"))
    issues = review.combined_issues(report)
    assert "ome_zarr_axes_mismatch" in codes(issues)
    planes = issue(issues, "different_plane_counts")
    assert "depth slices (Z): 3 (1 image), 5 (1 image)" in planes.message
    assert "time points" not in planes.message


def test_an_image_with_an_axis_error_is_measured_by_its_header(tmp_path):
    write_tiff(tmp_path / "a.tif", (3, 8, 9), axes="ZYX")
    write_tiff(tmp_path / "b.tif", (3, 8, 12), axes="ZYX")
    report = inspect_images(tmp_path, ["a.tif", "b.tif"])
    review = review_axes(report, AxisMapping("ZYX", "ZXY"))
    issues = review.combined_issues(report)
    assert "axes_yx_swapped" in codes(issues)
    # Height x width as the header names them, not as the swapped roles would give.
    assert "8 x 12 (1 image), 8 x 9 (1 image)" in issue(issues, "different_sizes").message
    assert codes(issues).count("different_sizes") == 1
