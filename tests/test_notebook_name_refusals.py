"""Conformance P21 and P29 in the notebook: the folder check refuses names as the run does.

Spec experiment-v1 section 3.3 (``R_FOLDER_NOT_NFC``: a folder label read from disk that is not
NFC is refused, never normalized or renamed) and section 8 / AQ11 (``R_MASK_TYPE_NAME_INVALID``:
refused at discovery, never reaching ``E_ID_TEXT``). They apply when a description is in use
(owner decision Q1): while the user describes the experiment (``describe=True``), and whenever
the output folder holds a published ``morphobricks.toml``. A refused name blocks *Check and
save* (section 10: a conflicting unit blocks publication). Without a description, the folder
check is as in v0.4.
"""

import pytest

from channel_images import write_masks
from description_tree import build_experiment, write_image
from morphobricks_preprocessing.description import description_from_bytes
from morphobricks_preprocessing.description_file import DESCRIPTION_FILENAME
from spec_pin import fixtures

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")

from morphobricks_preprocessing.notebook_ui import PreprocessingNotebook  # noqa: E402
from test_notebook_check_save import FOLDERS, add_folder, answer_all, opened  # noqa: E402

DECOMPOSED = "Café"
NAME_CODES = {"R_FOLDER_NOT_NFC", "R_MASK_TYPE_NAME_INVALID"}
V01 = next(item for item in fixtures("valid") if item.name.startswith("v01_"))


def codes(nb):
    return {issue.code for issue in nb.preflight.issues}


def paths(nb, code):
    return next(issue.paths for issue in nb.preflight.issues if issue.code == code)


def tree(tmp_path, extra_folder=None, mask_types=()):
    """The notebook tests' experiment, with one more folder and more mask types if asked."""
    root, masks = tmp_path / "exp", tmp_path / "masks"
    for i, folder in enumerate(FOLDERS):
        add_folder(root, masks, folder, i + 1)
    if extra_folder is not None:
        add_folder(root, masks, extra_folder, 9)
    for mask_type in mask_types:
        write_masks(masks, {f"{folder}/a.tif": 1 for folder in FOLDERS}, {mask_type: "labels"})
    return root, masks, tmp_path / "out"


def checked(root, masks, out):
    """Open the notebook and check the folder (step 1) with the masks folder given."""
    nb = PreprocessingNotebook(str(root), output_folder=str(out))
    for step in (nb.welcome, nb.folder_step, nb.axis_step, nb.masks_step, nb.run_step):
        step()
    nb.masks_folder.value = str(masks)
    nb.check_button.click()
    return nb


def test_p21_the_described_folder_check_refuses_a_decomposed_folder(tmp_path):
    nb = checked(*tree(tmp_path, f"ctrl/{DECOMPOSED}"))
    assert paths(nb, "R_FOLDER_NOT_NFC") == (f"ctrl/{DECOMPOSED}",)
    assert not nb.preflight.ok
    assert (tmp_path / "exp" / "ctrl" / DECOMPOSED).is_dir()  # never renamed


def test_p29_the_described_folder_check_refuses_the_mask_type_names(tmp_path):
    nb = checked(*tree(tmp_path, mask_types=("NA", " cells", DECOMPOSED)))
    assert paths(nb, "R_MASK_TYPE_NAME_INVALID") == (" cells", "NA")
    assert paths(nb, "R_FOLDER_NOT_NFC") == (DECOMPOSED,)
    assert {"NA", " cells", DECOMPOSED} <= set(nb.preflight.masks.types)  # as discovered


def test_p29_pairing_the_masks_again_keeps_the_refusal(tmp_path):
    root, masks, out = tree(tmp_path)
    nb = checked(root, masks, out)
    assert not NAME_CODES & codes(nb)
    write_masks(masks, {f"{folder}/a.tif": 1 for folder in FOLDERS}, {"NA": "labels"})
    nb.masks_button.click()  # the masks are paired again (remasked), the folder is not
    assert paths(nb, "R_MASK_TYPE_NAME_INVALID") == ("NA",)


def test_p21_p29_a_refused_name_blocks_check_and_save(tmp_path):
    root, masks, out = tree(tmp_path, mask_types=("NA",))
    nb = opened(root, masks, out)
    answer_all(nb)
    nb.mask_card.questions["NA"].choice.value = "instances"
    assert not nb.save_experiment()
    assert "R_MASK_TYPE_NAME_INVALID" in nb.todo.value
    assert not (out / DESCRIPTION_FILENAME).exists()


def test_p21_the_folder_check_with_a_published_description_refuses_too(tmp_path):
    root, out = tmp_path / "exp", tmp_path / "out"
    build_experiment(root, description_from_bytes(V01.data))
    write_image(root / "CTRL" / DECOMPOSED / "a.tif", 2)
    out.mkdir()
    (out / DESCRIPTION_FILENAME).write_bytes(V01.data)  # the run uses it, so the check does
    nb = checked(root, tmp_path / "masks", out)
    assert paths(nb, "R_FOLDER_NOT_NFC") == (f"CTRL/{DECOMPOSED}",)
