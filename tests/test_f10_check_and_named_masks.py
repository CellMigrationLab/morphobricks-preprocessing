"""F10 (matrix run, 3 November 2026; owner decision, 4 November 2026).

1. ``check`` takes ``--missing-as-singleton`` as ``run`` does (same option, same default,
   same review), so an experiment mixing 2-D images and time-lapses passes ``check`` once
   it is confirmed, and ``check`` and ``run`` report the same errors for the same inputs.
   (These tests used Z-stacks until the owner decision of 5 November 2026 refused them;
   the time axis exercises the same missing-axis review.)
2. A 2-D mask whose own metadata names its axes ``YX`` is never shared by several planes:
   the owner decision of 4 November 2026 allowed it only when stated ``--mask-axes
   TYPE=YX``; that of 5 November 2026 removed the statement too. The removal itself is
   tested in ``test_2d_planes_only.py``; here what remains of F10's mask half: a manifest
   #148 wrote is refused, and a 2-D mask of a one-plane image is recorded as before.
"""

import json

import numpy as np
import pytest
import tifffile

from described_tree import main_described, run_described
from morphobricks_preprocessing import (
    default_output_folder,
    prefill_mapping,
    preflight,
    read_manifest,
)
from morphobricks_preprocessing.run.check_step import suggested_review


def ome(path, data, axes):
    """Write an OME-TIFF naming its axes, as microscopes and Fiji export them."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(path, data, ome=True, photometric="minisblack", metadata={"axes": axes})
    return path


def labels(shape, start=1):
    return (np.arange(np.prod(shape)).reshape(shape) % 5 + start).astype(np.uint16)


def error_codes(output):
    report = json.loads((output / "discovery_report.json").read_text(encoding="utf-8"))
    return sorted({i["code"] for i in report["issues"] if i["severity"] == "error"})


@pytest.fixture
def mixed(tmp_path):
    """Two 2-D images (CYX) and one time-lapse (TCYX, a unit of its own), with masks (synA)."""
    root, masks = tmp_path / "exp", tmp_path / "masks"
    rng = np.random.default_rng(4)
    for rel in ("ctrl/r1/a.tif", "drug/r1/a.ome.tif"):
        ome(root / rel, rng.integers(0, 200, (2, 6, 8), dtype=np.uint16), "CYX")
        ome(masks / "Cells" / rel, labels((6, 8)), "YX")
    ome(root / "ctrl/r2/b.ome.tif", rng.integers(0, 200, (2, 2, 6, 8), dtype=np.uint16), "TCYX")
    ome(masks / "Cells/ctrl/r2/b.ome.tif", labels((2, 6, 8)), "TYX")
    return root, masks


# 1. check --missing-as-singleton ---------------------------------------------------------


def test_check_refuses_a_mixed_experiment_until_it_is_confirmed(mixed, tmp_path, capsys):
    root, masks = mixed
    out = tmp_path / "out"
    args = ["check", str(root), "--masks", str(masks), "--output", str(out)]
    assert main_described(args) == 1
    printed = capsys.readouterr().out
    assert "lack some of the dimensions of the axis mapping (TCYX)" in printed
    assert error_codes(out) == ["missing_axes"]
    assert main_described([*args, "--missing-as-singleton"]) == 0
    printed = capsys.readouterr().out
    assert "Error" not in printed
    assert "--axes TCYX --missing-as-singleton" in printed
    assert error_codes(out) == []


@pytest.mark.parametrize("singleton", [False, True])
def test_check_and_run_report_the_same_errors(mixed, tmp_path, capsys, singleton):
    root, masks = mixed
    option = ["--missing-as-singleton"] if singleton else []
    checked, ran = tmp_path / "checked", tmp_path / "ran"
    common = [str(root), "--masks", str(masks), *option]
    status = main_described(["check", *common, "--output", str(checked)])
    assert main_described(["run", *common, "--output", str(ran), "--axes", "TCYX"]) == status
    capsys.readouterr()
    assert error_codes(checked) == error_codes(ran) == ([] if singleton else ["missing_axes"])


def test_check_takes_the_answer_the_earlier_run_recorded(mixed, tmp_path, capsys):
    root, masks = mixed
    out = tmp_path / "out"
    common = [str(root), "--masks", str(masks), "--output", str(out)]
    assert main_described(["run", *common, "--axes", "TCYX", "--missing-as-singleton"]) == 0
    assert main_described(["check", *common]) == 0  # as 'run' would read them again
    assert main_described(["check", *common, "--no-missing-as-singleton"]) == 1
    assert "lack some of the dimensions" in capsys.readouterr().out


def test_suggested_review_answers_the_missing_axes_as_the_run_does(mixed):
    root, _ = mixed
    pre = preflight(root)
    assert "missing_axes" in {i.code for i in pre.issues}
    refused, _ = suggested_review(pre)
    assert [i.code for i in refused.issues if i.is_error] == ["missing_axes"]
    accepted, review = suggested_review(pre, missing_as_singleton=True)
    assert accepted.ok
    assert review.file_mappings["ctrl/r1/a.tif"].singleton_roles == ("T",)


def test_the_output_folder_defaults_next_to_the_experiment(mixed, capsys):
    root, masks = mixed
    assert (
        main_described(["check", str(root), "--masks", str(masks), "--missing-as-singleton"]) == 0
    )
    capsys.readouterr()
    assert error_codes(default_output_folder(root)) == []


def test_the_notebooks_check_report_takes_the_recorded_answer(mixed, capsys):
    pytest.importorskip("ipywidgets")
    pytest.importorskip("matplotlib")
    from morphobricks_preprocessing.notebook_ui import PreprocessingNotebook

    root, masks = mixed
    out = default_output_folder(root)

    def checked():
        nb = PreprocessingNotebook(str(root))
        for step in (nb.welcome, nb.folder_step, nb.axis_step, nb.run_step, nb.results_step):
            step()
        nb.masks_folder.value = str(masks)
        nb.check_button.click()
        return nb

    first = checked()
    assert not first.missing_as_singleton.value
    common = [str(root), "--masks", str(masks), "--output", str(out)]
    assert main_described(["run", *common, "--axes", "TCYX", "--missing-as-singleton"]) == 0
    capsys.readouterr()
    again = checked()  # step 2 shows the recorded answer, and the report follows it
    assert again.missing_as_singleton.value
    assert error_codes(out) == []


# Review of PR #148: the reused answer is shown, and nothing else is hidden ---------------


def test_check_and_run_say_when_the_recorded_answer_is_reused(mixed, tmp_path, capsys):
    root, masks = mixed
    out = tmp_path / "out"
    common = [str(root), "--masks", str(masks), "--output", str(out)]
    assert main_described(["run", *common, "--axes", "TCYX", "--missing-as-singleton"]) == 0
    assert "as confirmed for the earlier run (give" not in capsys.readouterr().out
    for command in (["check", *common], ["run", *common, "--axes", "TCYX"]):
        assert main_described(command) == 0
        printed = capsys.readouterr().out
        assert "Images lacking some axes: read with one plane along them, as confirmed" in printed
    assert (
        main_described(["check", *common, "--missing-as-singleton"]) == 0
    )  # given: nothing reused
    assert "Images lacking some axes" not in capsys.readouterr().out


def test_check_suggests_the_option_only_for_images_lacking_axes(tmp_path, capsys):
    root, masks, out = tmp_path / "exp", tmp_path / "masks", tmp_path / "out"
    ome(root / "ctrl/r1/a.tif", np.zeros((3, 2, 6, 8), np.uint16), "TCYX")
    ome(masks / "Cells/ctrl/r1/a.tif", labels((3, 6, 8)), "TYX")
    args = ["check", str(root), "--masks", str(masks), "--output", str(out)]
    assert main_described([*args, "--missing-as-singleton"]) == 0
    assert "Suggested option for 'run': --axes TCYX (axes" in capsys.readouterr().out


def test_check_keeps_the_other_errors_of_a_confirmed_mixed_experiment(mixed, tmp_path, capsys):
    root, masks = mixed
    (root / "drug/r1/broken.tif").write_bytes(b"not a TIFF file")
    checked, ran = tmp_path / "checked", tmp_path / "ran"
    common = [str(root), "--masks", str(masks), "--missing-as-singleton"]
    assert main_described(["check", *common, "--output", str(checked)]) == 1
    assert main_described(["run", *common, "--output", str(ran), "--axes", "TCYX"]) == 1
    capsys.readouterr()
    found = error_codes(checked)
    assert found and "missing_axes" not in found
    assert found == error_codes(ran)


def test_a_named_2d_mask_of_a_single_plane_image_is_recorded_from_its_header(tmp_path):
    root, masks = tmp_path / "exp", tmp_path / "masks"
    ome(root / "ctrl/a.ome.tif", np.zeros((2, 6, 8), np.uint16), "CYX")
    ome(masks / "Cells/ctrl/a.ome.tif", labels((6, 8)), "YX")
    result = run_described(root, prefill_mapping("CYX"), masks_folder=masks)
    (entry,) = read_manifest(result.manifest_path).masks["files"]
    assert entry["axes_from"] == "header" and "shared" not in entry


def test_a_label_image_of_a_one_plane_ome_zarr_is_used_as_it_is(tmp_path):
    from zarr_tree import write_labels, write_ngff

    image = write_ngff(tmp_path / "exp/ctrl/a.ome.zarr", np.zeros((1, 6, 8), np.uint16), "zyx")
    write_labels(image, {"cells": labels((6, 8))}, "yx")
    (mask,) = preflight(tmp_path / "exp").masks.files
    assert (mask.axes, mask.axes_from) == ("YX", "header")
    assert run_described(tmp_path / "exp", prefill_mapping("ZYX")).written


# A manifest #148 wrote (main after v0.4.0rc5, no tag) is never trusted -------------------

F10_MAIN = "f7c54bd2985ec3bd72e59938270cb2f398247fee"
"""main with #148 merged: it recorded a named 2-D mask as shared with ``axes_from: "header"``."""

WRITE_WITH_F10 = """
from morphobricks_preprocessing import prefill_mapping, run_experiment
root, masks, out = (Path(a) for a in sys.argv[2:5])
result = run_described(root, prefill_mapping("TYX"), output_folder=out, masks_folder=masks)
print(json.dumps(result.written))
"""


def test_any_shared_mask_record_is_refused():
    from morphobricks_preprocessing.mask_record import shared_mask_text

    plain = {"path": "C/a.tif", "type": "C", "axes_from": "header"}
    old = {"path": "C/b.tif", "type": "C", "shared": False, "axes_from": "header"}
    assert shared_mask_text([plain, old]) is None
    for how in ("stated", "header", "", None):
        record = {"path": "N/a.tif", "type": "N", "shared": True, "axes_from": how}
        text = shared_mask_text([plain, record])
        assert text is not None and "N/a.tif" in text and "C/a.tif" not in text
        assert "same time points (T)" in text
