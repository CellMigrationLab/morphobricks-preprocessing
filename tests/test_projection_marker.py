"""Projected copies whose conversion record is gone are refused (owner decision, 2026-11-08).

The regression is ``test_projected_copies_without_their_record_are_refused``: before, a copy
tree whose record was deleted looked like plain 2-D images, and ``check`` and ``run`` accepted
it with no trace that it was a projection.
"""

import json

import numpy as np
import pytest
import tifffile

from morphobricks_preprocessing import AxisMapping, Projection, convert_experiment, run_experiment
from morphobricks_preprocessing.conversion_record import RECORD_FILENAME
from morphobricks_preprocessing.run.preflight import preflight
from morphobricks_preprocessing.testing import _confirmed_design
from test_projection_convert import NAMES, ZCYX, codes, stack_experiment

CYX = AxisMapping("CYX", tuple("CYX"))
CODE = "projection_record_missing"
pytestmark = pytest.mark.filterwarnings("ignore:No experiment description:UserWarning")


def projected_tree(tmp_path):
    exp, copies = tmp_path / "exp", tmp_path / "copies"
    stack_experiment(exp)
    done = convert_experiment(exp, ZCYX, copies, projection=Projection("max", None))
    assert done.written, done.issues
    return copies


def test_projected_copies_without_their_record_are_refused(tmp_path):
    copies = projected_tree(tmp_path)
    assert CODE not in codes(preflight(copies).issues)
    (copies / RECORD_FILENAME).unlink()
    pre = preflight(copies)
    assert not pre.ok and CODE in codes(pre.issues)
    issue = next(i for i in pre.issues if i.code == CODE)
    assert set(issue.paths) == set(NAMES) and "convert --project" in issue.action
    out = tmp_path / "out"
    run = run_experiment(copies, CYX, output_folder=out, design=_confirmed_design(copies))
    assert not run.written and CODE in codes(run.issues) and not (out / "run_manifest.json").exists()


def test_a_record_that_lists_the_image_as_copied_does_not_vouch_for_it(tmp_path):
    copies = projected_tree(tmp_path)
    path = copies / RECORD_FILENAME
    record = json.loads(path.read_text(encoding="utf-8"))
    record["files"][0].update(action="copied", projection=None)
    path.write_text(json.dumps(record), encoding="utf-8")
    pre = preflight(copies)
    assert not pre.ok
    assert CODE in codes(pre.issues) or "conversion_record_unreadable" in codes(pre.issues)


def test_a_plain_ome_tiff_without_the_marker_is_unaffected(tmp_path):
    folder = tmp_path / "plain"
    for name in NAMES:
        data = np.zeros((3, 8, 9), np.uint16)
        (folder / name).parent.mkdir(parents=True, exist_ok=True)
        tifffile.imwrite(
            folder / name, data, ome=True, photometric="minisblack",
            metadata={"axes": "CYX", "Description": "an ordinary description"},
        )  # fmt: skip
    pre = preflight(folder)
    assert CODE not in codes(pre.issues) and pre.conversion is None


def test_the_check_reads_no_pixels(tmp_path, monkeypatch):
    copies = projected_tree(tmp_path)
    (copies / RECORD_FILENAME).unlink()

    def no_pixels(self, *args, **kwargs):
        raise AssertionError("a pixel read")

    monkeypatch.setattr(tifffile.TiffPage, "asarray", no_pixels)
    from morphobricks_preprocessing.projection_marker import marked_images

    assert marked_images(copies, NAMES) == sorted(NAMES)
