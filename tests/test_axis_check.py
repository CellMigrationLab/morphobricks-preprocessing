"""The axis check (contract §2, v0.4.0).

Confirmed roles that swap Y and X (``axes_yx_swapped``) or otherwise contradict the header
(``axes_contradict_header``) are errors naming the file with its header and confirmed axes
and the likely cause; such an image is never read. Before v0.4.0 a swap was read transposed
and a contradiction under the declaration, silently. A plain stack whose metadata does not
name its axes is read with the confirmed roles, with the ``axes_not_named`` warning, and
recorded as confirmed (``AxisReview.axes_from``).
"""

import numpy as np
import pytest
import tifffile

from morphobricks_preprocessing import (
    AXIS_CHECK_CODES,
    AXIS_ERROR_CODES,
    AxisMapping,
    TiffMetadata,
    find_masks,
    header_axis_issue,
    inspect_image,
    mask_size_issues,
    prefill_mapping,
    preflight,
    review_axes,
    run_experiment,
)
from morphobricks_preprocessing.axis_check import axis_disagreements
from morphobricks_preprocessing.cli import main
from morphobricks_preprocessing.design import default_design, write_experiment_design
from morphobricks_preprocessing.discovery import default_output_folder
from tiff_tree import write_data


def imagej(path, data, axes):
    path.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(path, data, imagej=True, metadata={"axes": axes})
    return path


def ome(path, data, axes):
    path.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(path, data, ome=True, metadata={"axes": axes}, photometric="minisblack")
    return path


def plain(path, data):
    """A plain stack of pages without axis metadata (``IYX``)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(path, data, photometric="minisblack", metadata=None)
    return path


def data(shape, dtype=np.uint8):
    return (np.arange(np.prod(shape)) % 251).reshape(shape).astype(dtype)


def codes(issues):
    return [i.code for i in issues]


def one(issues, code):
    (found,) = [i for i in issues if i.code == code]
    return found


# The rule, position by position ---------------------------------------------------------


WRITERS = {
    # ImageJ hyperstacks of two channels (three would be stored as RGB).
    "imagej": lambda p, a: imagej(p, data((2 if a[0] == "C" else 3, 6, 8)), a),
    "ome": lambda p, a: ome(p, data((3, 6, 8)), a),
    "shaped": lambda p, a: write_data(p, data((3, 6, 8)), a),
}


@pytest.mark.parametrize("kind", sorted(WRITERS))
@pytest.mark.parametrize(
    ("header", "roles", "expected"),
    [
        ("ZYX", "ZYX", None),
        ("ZYX", "ZXY", "axes_yx_swapped"),
        ("ZYX", "TYX", "axes_contradict_header"),
        ("ZYX", "CYX", "axes_contradict_header"),
        ("ZYX", "TXY", "axes_contradict_header"),  # a contradiction is more than a swap
        ("CYX", "CYX", None),
        ("CYX", "CXY", "axes_yx_swapped"),
        ("CYX", "ZYX", "axes_contradict_header"),
        ("TYX", "ZYX", "axes_contradict_header"),
    ],
)
def test_named_headers(tmp_path, kind, header, roles, expected):
    meta = inspect_image(WRITERS[kind](tmp_path / "a.tif", header))
    assert meta.axes == header
    mapping = AxisMapping(header, roles)
    assert header_axis_issue(meta, mapping) == expected
    review = review_axes([meta], mapping)
    if expected is None:
        assert review.ok and not review.axis_errors
    else:
        assert codes(review.issues) == [expected]
        assert review.axis_error_files == ("a.tif",)


@pytest.mark.parametrize("roles", ["TYX", "ZYX", "CYX", "TXY"])
@pytest.mark.parametrize("writer", ["plain-IYX", "shaped-QYX"])
def test_plain_stacks_never_name_their_axes(tmp_path, writer, roles):
    path = tmp_path / "a.tif"
    if writer == "plain-IYX":
        plain(path, data((3, 6, 8)))
    else:
        write_data(path, data((3, 6, 8)))  # metadata={}: shaped without axes
    meta = inspect_image(path)
    assert meta.axes in ("IYX", "QYX")
    review = review_axes([meta], AxisMapping(meta.axes, roles))
    found = one(review.issues, "axes_not_named")
    assert not found.is_error and found.paths == ("a.tif",)
    assert review.ok and review.axes_from == {"a.tif": "confirmed"}
    assert review.confirmed_files == ("a.tif",)
    assert f"header {meta.axes}, confirmed {roles}: a.tif" in found.message
    assert "read with the roles you confirmed" in found.message
    assert "named-axis copies (OME-TIFF)" in found.action


def test_shaped_axes_with_a_placeholder_are_not_named(tmp_path):
    meta = inspect_image(write_data(tmp_path / "a.tif", data((3, 6, 8)), "QYX"))
    assert meta.axes_from_metadata and meta.axes == "QYX"
    assert header_axis_issue(meta, AxisMapping("QYX", "ZYX")) == "axes_not_named"
    # Only the placeholder is open: Y and X are named.
    assert axis_disagreements(meta, AxisMapping("QYX", "ZYX")) == {0: "axes_not_named"}


def test_a_plain_2d_page_names_its_axes(tmp_path):
    meta = inspect_image(plain(tmp_path / "a.tif", data((6, 8))))
    assert meta.axes == "YX"
    assert review_axes([meta], prefill_mapping("YX")).ok
    # Decision D-d: the rows and columns of a single page are its Y and X.
    assert header_axis_issue(meta, AxisMapping("YX", "XY")) == "axes_yx_swapped"


def test_rgb_samples_as_channels_agree(tmp_path):
    path = tmp_path / "a.tif"
    tifffile.imwrite(path, data((6, 8, 3)), photometric="rgb")
    meta = inspect_image(path)
    assert meta.axes == "YXS"
    review = review_axes([meta], prefill_mapping("YXS"))
    assert review.ok and review.mapping.roles == ("Y", "X", "C")
    assert header_axis_issue(meta, AxisMapping("YXS", "XYC")) == "axes_yx_swapped"
    assert header_axis_issue(meta, AxisMapping("YXS", "YXZ")) == "axes_contradict_header"


def hand(axes, shape, from_metadata=True):
    return TiffMetadata(
        "a.tif", True, shape, np.dtype("uint8"), axes, 1, 1, axes_from_metadata=from_metadata
    )


def test_length_1_axes_without_a_role_are_not_contradictions():
    assert header_axis_issue(hand("TZYX", (1, 3, 6, 8)), AxisMapping("TZYX", "-ZYX")) is None
    assert header_axis_issue(hand("QZYX", (1, 3, 6, 8)), AxisMapping("QZYX", "-ZYX")) is None
    # A length-1 axis given another role is still a contradiction of its header.
    found = header_axis_issue(hand("TZYX", (1, 3, 6, 8)), AxisMapping("TZYX", "ZTYX"))
    assert found == "axes_contradict_header"


def test_named_axes_without_a_role_are_contradicted_when_given_one():
    # Lambda (E) is named but is not a role: giving it one contradicts the header.
    meta = hand("EYX", (4, 6, 8))
    assert header_axis_issue(meta, AxisMapping("EYX", "CYX")) == "axes_contradict_header"
    # Without a role it is check_mapping's unassigned_axis, not a second error.
    review = review_axes([meta], AxisMapping("EYX", "-YX"))
    assert codes(review.issues) == ["unassigned_axis"]


def test_position_axes_are_left_to_unsupported_axis():
    review = review_axes([hand("RYX", (3, 6, 8))], AxisMapping("RYX", "ZYX"))
    assert "unsupported_axis" in codes(review.issues)
    assert not review.axis_errors


def test_ome_zarr_images_never_have_an_axis_error(tmp_path):
    pytest.importorskip("zarr")
    from zarr_tree import write_ngff

    write_ngff(tmp_path / "a.ome.zarr", data((3, 6, 8)), axes="zyx")
    meta = inspect_image(tmp_path / "a.ome.zarr", "a.ome.zarr")
    assert meta.is_ome_zarr
    assert header_axis_issue(meta, AxisMapping("ZYX", "ZYX")) is None
    assert header_axis_issue(meta, AxisMapping("ZYX", "TXY")) is None
    assert review_axes([meta], prefill_mapping("ZYX")).ok


def test_every_file_is_named_with_its_header_and_confirmed_axes(tmp_path):
    files = [hand("CYX", (2, 6, 8)) for _ in range(5)]
    files = [
        TiffMetadata(f"c/r/img_{i}.tif", *(f.readable, f.shape, f.dtype, f.axes, 1, 1))
        for i, f in enumerate(files)
    ]
    review = review_axes(files, AxisMapping("CYX", "CXY"))
    found = one(review.issues, "axes_yx_swapped")
    assert found.paths == tuple(f"c/r/img_{i}.tif" for i in range(5))
    assert "The axes confirmed for 5 images swap the Y and X their header names" in found.message
    assert "header CYX, confirmed CXY: c/r/img_0.tif, c/r/img_1.tif, c/r/img_2.tif and 2 more" in (
        found.message
    )
    assert "They are not read transposed." in found.message
    assert "likely cause: the image was saved with its rows and columns exchanged" in (
        found.message
    )
    assert "Correct the axes" in found.action and "Or convert" in found.action


def test_the_likely_cause_of_tifffiles_cyx_default_is_named(tmp_path):
    # tifffile labels any 3-D stack saved with imagej=True or ome=True CYX.
    meta = inspect_image(ome(tmp_path / "a.tif", data((5, 6, 8)), "CYX"))
    review = review_axes([meta], AxisMapping("CYX", "TYX"))
    found = one(review.issues, "axes_contradict_header")
    assert "likely cause: tifffile labels a 3-D stack saved with imagej=True or ome=True" in (
        found.message
    )
    relabel = review_axes(
        [inspect_image(imagej(tmp_path / "b.tif", data((3, 6, 8)), "ZYX"))],
        AxisMapping("ZYX", "TYX"),
    )
    assert "an ImageJ stack of slices reads as depth" in relabel.issues[0].message


def test_the_codes():
    assert AXIS_ERROR_CODES == ("axes_yx_swapped", "axes_contradict_header")
    assert AXIS_CHECK_CODES == ("axes_not_named", "axes_yx_swapped", "axes_contradict_header")


def test_named_images_are_recorded_as_from_the_header(tmp_path):
    meta = inspect_image(write_data(tmp_path / "a.tif", data((2, 6, 8)), "CYX"))
    assert review_axes([meta], prefill_mapping("CYX")).axes_from == {"a.tif": "header"}


# The run stops, reading nothing --------------------------------------------------------


@pytest.fixture
def reads(monkeypatch):
    import morphobricks_preprocessing.run.rows as rows_module

    seen = []
    original = rows_module.iter_xyc_planes

    def spy(path, *args, **kwargs):
        seen.append(str(path))
        return original(path, *args, **kwargs)

    monkeypatch.setattr(rows_module, "iter_xyc_planes", spy)
    return seen


@pytest.mark.parametrize(
    ("write", "axes", "roles", "code"),
    [
        (lambda p: write_data(p, data((2, 6, 8)), "CYX"), "CYX", "CXY", "axes_yx_swapped"),
        (lambda p: imagej(p, data((3, 6, 8)), "ZYX"), "ZYX", "TYX", "axes_contradict_header"),
    ],
)
def test_run_stops_naming_every_file(tmp_path, reads, write, axes, roles, code):
    root = tmp_path / "exp"
    for rel in ["ctrl/r1/a.tif", "ctrl/r1/b.tif", "drug/r1/a.tif"]:
        write(root / rel)
    result = run_experiment(root, AxisMapping(axes, roles))
    assert not result.written and reads == []
    found = one(result.issues, code)
    assert found.paths == ("ctrl/r1/a.tif", "ctrl/r1/b.tif", "drug/r1/a.tif")
    assert [i.code for i in result.issues if i.is_error] == [code]
    assert not default_output_folder(root).exists()


def test_a_plain_stack_runs_with_a_warning(tmp_path):
    root = tmp_path / "exp"
    plain(root / "ctrl/a.tif", data((3, 6, 8)))
    result = run_experiment(root, AxisMapping("IYX", "ZYX"))
    assert result.written and one(result.issues, "axes_not_named").paths == ("ctrl/a.tif",)


def test_a_clean_run_is_unchanged(tmp_path):
    root = tmp_path / "exp"
    write_data(root / "ctrl/a.tif", data((2, 6, 8)), "CYX")
    result = run_experiment(root, prefill_mapping("CYX"))
    assert result.written and not [i for i in result.issues if i.code in AXIS_ERROR_CODES]


# Masks: sizes by the confirmed roles (B1) ------------------------------------------------


def masks_tree(tmp_path, image, mask_shape, mask_axes=None):
    root = tmp_path / "exp"
    image(root / "ctrl/a.tif")
    write_data(tmp_path / "masks/Cells/ctrl/a.tif", np.ones(mask_shape, np.uint16), mask_axes)
    return root, tmp_path / "masks"


def test_a_swapped_image_gets_no_spurious_mask_size_error(tmp_path):
    # Header CYX of 6 x 8; confirmed CXY, so by the roles the image is 8 high and 6 wide.
    root, masks = masks_tree(tmp_path, lambda p: write_data(p, data((2, 6, 8)), "CYX"), (8, 6))
    result = run_experiment(root, AxisMapping("CYX", "CXY"), masks_folder=masks)
    assert [i.code for i in result.issues if i.is_error] == ["axes_yx_swapped"]


def test_a_relabelled_stack_gets_no_spurious_mask_plane_error(tmp_path):
    root, masks = masks_tree(
        tmp_path, lambda p: imagej(p, data((3, 6, 8)), "ZYX"), (3, 6, 8), "ZYX"
    )
    result = run_experiment(root, AxisMapping("ZYX", "TYX"), masks_folder=masks)
    assert [i.code for i in result.issues if i.is_error] == ["axes_contradict_header"]


def test_mask_sizes_are_compared_by_the_confirmed_roles(tmp_path):
    root, masks = masks_tree(tmp_path, lambda p: write_data(p, data((2, 6, 8)), "CYX"), (6, 8))
    pre = preflight(root, masks_folder=masks)
    files = pre.metadata.files
    review = review_axes(files, prefill_mapping("CYX"))
    assert mask_size_issues(pre.masks, files, review.file_mappings) == []
    # By roles that swap Y and X the image is 8 x 6, so the 6 x 8 mask does not fit...
    swapped = review_axes(files, AxisMapping("CYX", "CXY"))
    found = mask_size_issues(pre.masks, files, swapped.file_mappings)
    assert codes(found) == ["mask_size_mismatch"]
    # ...but the image is never read, so the run leaves its masks to the conversion.
    assert mask_size_issues(pre.masks, files, swapped.file_mappings, skip=["ctrl/a.tif"]) == []


def test_a_mask_of_the_wrong_size_is_still_an_error(tmp_path):
    root, masks = masks_tree(tmp_path, lambda p: write_data(p, data((2, 6, 8)), "CYX"), (5, 5))
    result = run_experiment(root, prefill_mapping("CYX"), masks_folder=masks)
    assert [i.code for i in result.issues if i.is_error] == ["mask_size_mismatch"]
    assert one(result.issues, "mask_size_mismatch").paths == ("Cells/ctrl/a.tif",)


def test_plain_stack_masks_are_not_compared_before_the_roles(tmp_path):
    root, masks = masks_tree(tmp_path, lambda p: plain(p, data((3, 6, 8))), (8, 6))
    pre = preflight(root, masks_folder=masks)
    # The plain stack's Y and X are only where its pages' rows and columns are stored.
    assert "mask_size_mismatch" not in codes(pre.masks.issues)
    inventory = find_masks(masks, pre.discovery, pre.metadata.files)
    assert "mask_size_mismatch" not in codes(inventory.issues)


# CLI and notebook ------------------------------------------------------------------------


def confirm(root):
    design = default_design(preflight(root).dataset.repeats)
    for group in design.groups:
        design = design.with_confirmed(group, True, "2026-11-01")
    output = default_output_folder(root)
    output.mkdir(parents=True, exist_ok=True)
    write_experiment_design(design, output / "experiment_design.csv")


def test_cli_check_reports_plain_stacks(tmp_path, capsys):
    root = tmp_path / "exp"
    plain(root / "ctrl/a.tif", data((3, 6, 8)))
    # A warning: 'run' reads the stack with the roles given in --axes.
    assert main(["check", str(root)]) == 0
    out = capsys.readouterr().out
    assert "1 image do not name their axes, so their roles are recorded as confirmed" in out
    assert "do not name their axes in their metadata (header IYX, confirmed -YX" in out


def test_cli_check_and_run_say_the_axes_are_correct(tmp_path, capsys):
    root = tmp_path / "exp"
    write_data(root / "ctrl/a.tif", data((2, 6, 8)), "CYX")
    assert main(["check", str(root)]) == 0
    assert "Axes checked: every image names its axes in its metadata" in capsys.readouterr().out
    confirm(root)
    assert main(["run", str(root), "--axes", "CYX"]) == 0
    assert "Axes checked: every image names its axes" in capsys.readouterr().out


def test_cli_run_names_the_swapped_files(tmp_path, capsys):
    root = tmp_path / "exp"
    write_data(root / "ctrl/a.tif", data((2, 6, 8)), "CYX")
    confirm(root)
    assert main(["run", str(root), "--axes", "CXY"]) == 1
    out = capsys.readouterr().out
    assert "header CYX, confirmed CXY: ctrl/a.tif" in out
    assert "Nothing was written" in out


def test_notebook_marks_roles_that_disagree_with_the_header(tmp_path):
    pytest.importorskip("ipywidgets")
    pytest.importorskip("matplotlib")
    from morphobricks_preprocessing.notebook_ui import PreprocessingNotebook

    root = tmp_path / "exp"
    write_data(root / "ctrl/a.tif", data((2, 6, 8)), "CYX")
    nb = PreprocessingNotebook(str(root))
    nb.axis_step()
    nb.check_button.click()
    assert "⚠" not in "".join(note.value for note in nb.role_notes)
    nb.role_dropdowns[1].value = "X"
    nb.role_dropdowns[2].value = "Y"
    assert "The image header names this axis Y (height)" in nb.role_notes[1].value
    assert "swaps the Y and X it names" in nb.role_notes[1].value
    assert "⚠" not in nb.role_notes[0].value
    assert "axes_yx_swapped" in nb.axis_status.value or "swap the Y and X" in nb.axis_status.value
    nb.preview_button.click()
    nb.preview_checked.button.click()
    assert nb.confirm_button.disabled
    nb.role_dropdowns[1].value = "Y"
    nb.role_dropdowns[2].value = "X"
    nb.preview_checked.button.click()
    nb.confirm_button.click()
    assert "Axes checked: they are correct for every image" in nb.confirmed_status.value


def test_notebook_marks_axes_a_plain_stack_does_not_name(tmp_path):
    pytest.importorskip("ipywidgets")
    pytest.importorskip("matplotlib")
    from morphobricks_preprocessing.notebook_axes import header_marks

    root = tmp_path / "exp"
    plain(root / "ctrl/a.tif", data((3, 6, 8)))
    files = preflight(root).metadata.files
    marks = header_marks(AxisMapping("IYX", "TYX"), files)
    assert sorted(marks) == [0, 1, 2]
    assert "does not name this axis" in marks[0]
