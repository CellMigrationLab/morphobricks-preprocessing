"""Symbolic links stay inside the experiment and masks folders (issue #81, owner policy "contain").

Every test here failed before the fix: links were followed wherever they led, two paths of
one file were two images, and a linked folder in the masks folder was skipped silently.
"""

import os

import numpy as np
import pytest

from described_tree import run_described
from morphobricks_preprocessing import (
    RunInputs,
    RunInputsError,
    UnrecordedFilesError,
    convert_experiment,
    prefill_mapping,
)
from morphobricks_preprocessing.discovery import discover_experiment
from morphobricks_preprocessing.run import preflight
from morphobricks_preprocessing.testing import make_clean_run
from tiff_tree import write_data


def _can_link(folder):
    try:
        os.symlink(folder, folder / "probe")
    except (OSError, NotImplementedError):
        return False
    (folder / "probe").unlink()
    return True


@pytest.fixture
def tree(tmp_path):
    """An experiment of two leaf folders, and a folder outside it."""
    if not _can_link(tmp_path):
        pytest.skip("symbolic links cannot be made here (Windows without the privilege)")
    root, outside = tmp_path / "exp", tmp_path / "outside"
    for rel in ("ctrl/rep1/a.tif", "drug/rep1/a.tif"):
        write_data(root / rel, np.zeros((4, 4), np.uint8), "YX")
    write_data(outside / "far.tif", np.ones((4, 4), np.uint8), "YX")
    return root, outside


def _codes(issues):
    return sorted(issue.code for issue in issues if issue.is_error)


def _issue(issues, code):
    return next(issue for issue in issues if issue.code == code)


def test_a_file_link_out_of_the_experiment_is_refused_and_not_listed(tree):
    root, outside = tree
    (root / "ctrl/rep1/b.tif").symlink_to(outside / "far.tif")
    found = discover_experiment(root)
    assert _codes(found.issues) == ["link_outside_folder"]
    issue = _issue(found.issues, "link_outside_folder")
    assert issue.paths == (f"ctrl/rep1/b.tif -> {(outside / 'far.tif').resolve()}",)
    assert "ctrl/rep1/b.tif" not in found.images


def test_a_folder_link_out_of_the_experiment_is_refused_and_not_entered(tree):
    root, outside = tree
    (root / "ext").symlink_to(outside, target_is_directory=True)
    found = discover_experiment(root)
    assert _codes(found.issues) == ["link_outside_folder"]
    assert _issue(found.issues, "link_outside_folder").paths == (f"ext -> {outside.resolve()}",)
    assert found.images == ("ctrl/rep1/a.tif", "drug/rep1/a.tif")


def test_a_broken_link_out_of_the_experiment_is_refused(tree):
    root, outside = tree
    (root / "ctrl/rep1/b.tif").symlink_to(outside / "gone.tif")
    assert _codes(discover_experiment(root).issues) == ["link_outside_folder"]


def test_links_that_stay_inside_are_followed(tree):
    root, _ = tree
    write_data(root / ".raw/b.tif", np.zeros((4, 4), np.uint8), "YX")  # a hidden folder
    write_data(root / ".raw/rep2/c.tif", np.zeros((4, 4), np.uint8), "YX")
    (root / "ctrl/rep1/b.tif").symlink_to(root / ".raw/b.tif")
    (root / "drug/rep2").symlink_to(root / ".raw/rep2", target_is_directory=True)
    found = discover_experiment(root)
    assert found.ok and not found.issues
    assert found.images == (
        "ctrl/rep1/a.tif",
        "ctrl/rep1/b.tif",
        "drug/rep1/a.tif",
        "drug/rep2/c.tif",
    )


def test_an_image_found_through_a_link_and_its_target_is_refused_naming_both(tree):
    root, _ = tree
    (root / "drug/rep1/b.tif").symlink_to(root / "ctrl/rep1/a.tif")
    found = discover_experiment(root)
    assert _codes(found.issues) == ["linked_twice"]
    assert _issue(found.issues, "linked_twice").paths == (
        "drug/rep1/b.tif (the same file as ctrl/rep1/a.tif)",
    )


def test_a_linked_folder_holding_the_same_images_is_refused(tree):
    root, _ = tree
    (root / "drug/rep2").symlink_to(root / "ctrl/rep1", target_is_directory=True)
    found = discover_experiment(root)
    assert _codes(found.issues) == ["linked_twice"]
    assert "drug/rep2/a.tif (the same file as ctrl/rep1/a.tif)" in found.issues[0].paths


def test_a_link_back_to_a_folder_above_is_a_loop(tree):
    root, _ = tree
    (root / "ctrl/loop").symlink_to(root, target_is_directory=True)
    found = discover_experiment(root)
    assert "link_loop" in _codes(found.issues)
    assert _issue(found.issues, "link_loop").paths == (f"ctrl/loop -> {root.resolve()}",)


def test_a_run_with_a_link_out_is_refused_before_any_pixel_is_read(tree, tmp_path):
    root, outside = tree
    (root / "ctrl/rep1/b.tif").symlink_to(outside / "far.tif")
    result = run_described(root, prefill_mapping("YX"), output_folder=tmp_path / "out")
    assert not result.written
    assert "link_outside_folder" in _codes(result.issues)


def test_a_conversion_with_a_link_out_is_refused(tree, tmp_path):
    root, outside = tree
    (root / "ctrl/rep1/b.tif").symlink_to(outside / "far.tif")
    result = convert_experiment(root, prefill_mapping("YX"), tmp_path / "copies")
    assert not result.written
    assert "link_outside_folder" in _codes(result.issues)
    assert not (tmp_path / "copies" / "ctrl").exists()


def test_a_link_out_of_an_ome_zarr_store_is_refused(tree):
    pytest.importorskip("zarr")
    from zarr_tree import write_ngff

    root, outside = tree
    store = write_ngff(root / "ctrl/rep1/z.ome.zarr", np.zeros((1, 1, 4, 4), np.uint8))
    (store / "extra").symlink_to(outside, target_is_directory=True)
    found = discover_experiment(root)
    assert _codes(found.issues) == ["link_outside_folder"]
    assert _issue(found.issues, "link_outside_folder").paths == (
        f"ctrl/rep1/z.ome.zarr/extra -> {outside.resolve()}",
    )


def test_a_linked_ome_zarr_store_out_of_the_experiment_is_refused(tree):
    pytest.importorskip("zarr")
    from zarr_tree import write_ngff

    root, outside = tree
    write_ngff(outside / "z.ome.zarr", np.zeros((1, 1, 4, 4), np.uint8))
    (root / "ctrl/rep1/z.ome.zarr").symlink_to(outside / "z.ome.zarr", target_is_directory=True)
    found = discover_experiment(root)
    assert _codes(found.issues) == ["link_outside_folder"]
    assert "ctrl/rep1/z.ome.zarr" not in found.images


@pytest.fixture
def clean(tmp_path):
    if not _can_link(tmp_path):
        pytest.skip("symbolic links cannot be made here (Windows without the privilege)")
    return make_clean_run(tmp_path / "run", axes="YX", shape=(16, 20), mask_types=("Cells",))


def test_a_mask_linked_from_outside_the_masks_folder_is_refused(clean):
    mask = clean.masks / "Cells/ctrl/rep1/a.tif"
    real = clean.masks.parent / "elsewhere.tif"
    mask.rename(real)
    mask.symlink_to(real)
    pre = preflight(clean.experiment, clean.output, masks_folder=clean.masks)
    issue = _issue(pre.masks.issues, "link_outside_folder")
    assert issue.paths == (f"Cells/ctrl/rep1/a.tif -> {real.resolve()}",)
    assert "masks folder" in issue.message


def test_a_linked_folder_inside_the_masks_folder_is_followed(clean):
    # Before the fix the masks below a linked folder were skipped without a word.
    folder = clean.masks / "Cells/drug"
    folder.rename(clean.masks / ".drug")
    folder.symlink_to(clean.masks / ".drug", target_is_directory=True)
    pre = preflight(clean.experiment, clean.output, masks_folder=clean.masks)
    paths = sorted(mask.path for mask in pre.masks.files)
    assert "Cells/drug/rep1/a.tif" in paths and "Cells/drug/rep1/b.tif" in paths
    assert not [issue for issue in pre.masks.issues if issue.is_error]


def test_run_inputs_refuse_a_link_out_added_after_the_run(clean, tmp_path):
    outside = tmp_path / "far.tif"
    write_data(outside, np.zeros((16, 20), np.uint16), "YX")
    (clean.experiment / "ctrl/rep1/c.tif").symlink_to(outside)
    with pytest.raises(RunInputsError) as raised:
        RunInputs.open(clean.output)
    assert not isinstance(raised.value, UnrecordedFilesError)
    assert f"ctrl/rep1/c.tif -> {outside.resolve()}" in str(raised.value)


def test_run_inputs_refuse_a_mask_link_out_added_after_the_run(clean, tmp_path):
    outside = tmp_path / "far.tif"
    (clean.masks / "Cells/ctrl/rep1/a.tif").rename(outside)
    (clean.masks / "Cells/ctrl/rep1/a.tif").symlink_to(outside)
    with pytest.raises(RunInputsError, match="link"):
        RunInputs.open(clean.output)


def test_a_link_back_inside_an_ome_zarr_store_is_a_loop(tree):
    # Copying the store (convert) follows its links: this one would never end.
    pytest.importorskip("zarr")
    from zarr_tree import write_ngff

    root, _ = tree
    store = write_ngff(root / "ctrl/rep1/z.ome.zarr", np.zeros((1, 1, 4, 4), np.uint8))
    (store / "0" / "again").symlink_to(store, target_is_directory=True)
    found = discover_experiment(root)
    assert _codes(found.issues) == ["link_loop"]
    assert _issue(found.issues, "link_loop").paths == (
        f"ctrl/rep1/z.ome.zarr/0/again -> {store.resolve()}",
    )


def test_a_masks_folder_given_through_a_link_keeps_its_links_inside(clean, tmp_path):
    # The masks folder as RunInputs gives it may not be resolved: links are held to its real path.
    from morphobricks_preprocessing.masks.files import _folder_masks

    (clean.masks / "Cells/drug").rename(clean.masks / ".drug")
    (clean.masks / "Cells/drug").symlink_to(clean.masks / ".drug", target_is_directory=True)
    alias = tmp_path / "alias"
    alias.symlink_to(clean.masks, target_is_directory=True)
    discovery = discover_experiment(clean.experiment)
    found, issues = _folder_masks(alias, discovery, discovery.images)
    assert not [issue for issue in issues if issue.is_error]
    assert "Cells/drug/rep1/a.tif" in {mask.path for mask in found}


def _can_hard_link(folder):
    (folder / "probe-source").write_bytes(b"")
    try:
        os.link(folder / "probe-source", folder / "probe-link")
    except (OSError, NotImplementedError):
        return False
    finally:
        for name in ("probe-source", "probe-link"):
            (folder / name).unlink(missing_ok=True)
    return True


def test_a_hard_link_to_an_image_is_refused_naming_both(tmp_path):
    # Owner, 2 November 2026: one device and inode reached by two names is one file.
    if not _can_hard_link(tmp_path):
        pytest.skip("hard links cannot be made here")
    root = tmp_path / "exp"
    for rel in ("ctrl/rep1/a.tif", "drug/rep1/a.tif"):
        write_data(root / rel, np.zeros((4, 4), np.uint8), "YX")
    os.link(root / "ctrl/rep1/a.tif", root / "drug/rep1/b.tif")
    found = discover_experiment(root)
    assert _codes(found.issues) == ["linked_twice"]
    assert _issue(found.issues, "linked_twice").paths == (
        "drug/rep1/b.tif (the same file as ctrl/rep1/a.tif)",
    )
    result = run_described(root, prefill_mapping("YX"), output_folder=tmp_path / "out")
    assert not result.written
    assert "linked_twice" in _codes(result.issues)


def test_a_hard_link_between_masks_is_refused(tmp_path):
    if not _can_hard_link(tmp_path):
        pytest.skip("hard links cannot be made here")
    clean = make_clean_run(tmp_path / "run", axes="YX", shape=(16, 20), mask_types=("Cells",))
    mask = clean.masks / "Cells/ctrl/rep1/b.tif"
    mask.unlink()
    os.link(clean.masks / "Cells/ctrl/rep1/a.tif", mask)
    pre = preflight(clean.experiment, clean.output, masks_folder=clean.masks)
    assert _issue(pre.masks.issues, "linked_twice").paths == (
        "Cells/ctrl/rep1/b.tif (the same file as Cells/ctrl/rep1/a.tif)",
    )
    with pytest.raises(RunInputsError):  # refused: the mask changed since the run
        RunInputs.open(clean.output)


def test_a_broken_link_in_the_masks_folder_is_named(clean):
    # Before, it was skipped: its image only looked as if it had no mask (mask_missing).
    mask = clean.masks / "Cells/ctrl/rep1/a.tif"
    mask.unlink()
    mask.symlink_to(clean.masks / "Cells/ctrl/rep1/gone.tif")
    pre = preflight(clean.experiment, clean.output, masks_folder=clean.masks)
    issue = _issue(pre.masks.issues, "unreadable_mask")
    assert issue.is_error and issue.paths == ("Cells/ctrl/rep1/a.tif",)
