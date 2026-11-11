"""Z and T in the shipped notebook (owner, 11 November 2026).

A time-lapse goes through the steps as it is (one plane per time point). A Z-stack is refused
as before (``z_stack_not_supported``) and the Axes step offers the recorded way on: the user
chooses the projection (no default), one click writes the projected copies with their
conversion record into the output folder, and the later steps use the copies. The original
stacks are only read and stay recorded; the 3-D option is shown greyed with its reason.
"""

import hashlib
import html
import json
import re

import numpy as np
import pytest
import tifffile

from morphobricks_preprocessing.axes import AxisMapping
from morphobricks_preprocessing.conversion_record import RECORD_FILENAME, read_record
from morphobricks_preprocessing.image_view import ViewRequest, read_projection_view
from morphobricks_preprocessing.projection import Projection

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")

from morphobricks_preprocessing import notebook_style as ui  # noqa: E402
from morphobricks_preprocessing.notebook_projection import (  # noqa: E402
    KEEP_3D,
    NOT_ENABLED,
    copies_folder,
    earlier_projections,
    projection_words,
)
from morphobricks_preprocessing.notebook_ui import PreprocessingNotebook  # noqa: E402
from test_notebook_check_save import FOLDERS, answer_all  # noqa: E402

NAMES = ["DAPI", "Actin", "Ch3"]


def _text(text):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text))).strip()


def write_stacks(root, axes, shape):
    """Write one image per folder of FOLDERS; return ``{relative path: array}``."""
    written = {}
    for seed, folder in enumerate(FOLDERS):
        rng = np.random.default_rng(seed)
        data = rng.integers(100, 4000, shape, dtype=np.uint16)
        path = root / folder / "a.tif"
        path.parent.mkdir(parents=True, exist_ok=True)
        meta = {
            "axes": axes,
            "Channel": {"Name": NAMES},
            "PhysicalSizeX": 0.3,
            "PhysicalSizeY": 0.3,
        }
        tifffile.imwrite(path, data, ome=True, photometric="minisblack", metadata=meta)
        written[f"{folder}/a.tif"] = data
    return written


def started(tmp_path, axes, shape):
    root, out = tmp_path / "exp", tmp_path / "out"
    data = write_stacks(root, axes, shape)
    nb = PreprocessingNotebook(str(root), output_folder=str(out))
    nb.steps()
    nb.scan_button.click()
    nb.check_button.click()
    return nb, root, out, data


def digests(root):
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*.tif"))
    }


def test_a_time_lapse_goes_through_every_step_one_plane_per_time_point(tmp_path):
    nb, _root, _out, _ = started(tmp_path, "TCYX", (3, 3, 16, 16))
    assert not ui.is_visible(nb.projection_card)  # no depth: nothing to project
    assert ui.is_visible(nb.axes_viewer.sliders["T"])
    nb.quick_confirm.click()
    answer_all(nb)
    assert nb.save_experiment()
    result = nb.run()
    assert result.written and result.plane_count == len(FOLDERS) * 3


def test_a_z_stack_offers_the_projection_and_chooses_nothing(tmp_path):
    nb, root, out, _ = started(tmp_path, "ZCYX", (5, 3, 16, 16))
    assert "z stack not supported" in _text(nb.axis_status.value)  # the refusal is still shown
    assert ui.is_visible(nb.projection_card) and not ui.is_visible(nb.quick_card)
    assert nb.projection_method.value == ""  # no default method
    assert (nb.projection_first.value, nb.projection_last.value) == (0, 4)
    assert nb.projection_button.disabled and nb.confirm_button.disabled
    said = _text(nb.projection_blocker.value)
    assert "no projection method is chosen" in said and "the example is not ticked" in said
    assert "choose a projection under Z-stacks below" in _text(nb.confirm_blocker.value)
    keep = nb.projection_keep  # 3-D kept compatible: shown, greyed, with its reason
    assert keep.description == KEEP_3D and keep.disabled and not keep.value
    card = _text(
        " ".join(
            getattr(w, "value", "") or ""
            for w in _all(nb.projection_card)
            if isinstance(getattr(w, "value", None), str)
        )
    )
    assert NOT_ENABLED in card and "never changed, moved or deleted" in card
    assert nb._write_projection() is None and not out.joinpath("projected_max").exists()
    nb.projection_method.value = "max"
    assert nb.projection_button.disabled  # the roles are the user's: the example first
    nb.preview_checked.button.click()
    assert not nb.projection_button.disabled and nb.projection_blocker.value == ""
    assert "Now choose the projection under Z-stacks below" in nb.preview_checked.message.value
    assert str(out / "projected_max") in nb.projection_where.value
    nb.projection_method.value = ""  # taken back: nothing can be written again
    assert nb.projection_button.disabled
    assert not list(out.glob("projected_*")) and digests(root)  # nothing written so far


def _all(widget):
    yield widget
    for child in getattr(widget, "children", ()):
        yield from _all(child)


@pytest.mark.parametrize(
    ("axes", "shape", "planes"),
    [("ZCYX", (5, 3, 16, 16), 1), ("TZCYX", (2, 5, 3, 16, 16), 2)],
)
def test_the_projected_copies_are_written_recorded_and_used_by_the_later_steps(
    tmp_path, axes, shape, planes
):
    nb, root, out, data = started(tmp_path, axes, shape)
    before = digests(root)
    nb.projection_method.value = "max"
    nb.preview_checked.button.click()
    result = nb._write_projection()
    copies = out / "projected_max"
    assert result.written and len(result.projected) == len(FOLDERS)
    assert digests(root) == before  # the original stacks are only read
    # The copies are the maximum along Z of every (t, c), bit for bit.
    z = axes.index("Z")
    for rel, stack in data.items():
        assert np.array_equal(tifffile.imread(copies / rel), stack.max(axis=z))
    # The record names the originals: where they are, what they were, the slices used.
    record, _sha = read_record(copies)
    assert record.source_root == root.resolve().as_posix()
    assert (record.projection.method, record.projection.z_range) == ("max", None)
    entry = next(e for e in record.files if e.relative_path == "ctrl/rep1/a.tif")
    made = entry.projection
    assert made.source_relative_path == "ctrl/rep1/a.tif" and made.source_z_size == 5
    assert made.source_content_sha256 == before["ctrl/rep1/a.tif"] and made.z_range == (0, 5)
    # The app now works on the copies, scanned and checked again; nothing was confirmed for it.
    assert nb.folder.value == str(copies) and nb.output.value == str(out)
    assert nb.preflight.discovery.root == copies and nb.confirmed_mapping is None
    assert "Z" not in nb.mapping.roles and not ui.is_visible(nb.projection_card)
    note = _text(nb.copy_note.value)
    assert "recorded 2-D copies (max projection of all slices)" in note and str(root) in note
    assert "originals are kept" in note and nb.copy_note_images.value == nb.copy_note.value
    assert ui.is_visible(nb.quick_card)  # the copies name their axes: one click, the user's
    nb.quick_confirm.click()
    answer_all(nb)
    assert nb.save_experiment()
    run = nb.run()
    assert run.written and run.plane_count == len(FOLDERS) * planes
    report = json.loads((out / "discovery_report.json").read_text(encoding="utf-8"))
    assert report["conversion"]["source_root"] == root.resolve().as_posix()
    assert report["conversion"]["projection"]["method"] == "max"
    assert digests(root) == before  # still untouched after the run


def test_a_slice_range_and_another_method_go_to_their_own_folder(tmp_path):
    nb, root, out, data = started(tmp_path, "ZCYX", (5, 3, 16, 16))
    nb.projection_method.value = "mean"
    nb.projection_first.value, nb.projection_last.value = 1, 3
    nb.preview_checked.button.click()
    assert "mean projection of slices 1 to 3" in _text(nb.projection_where.value)
    result = nb._write_projection()
    copies = out / "projected_mean_z1-4"
    assert result.written and result.destination == copies
    stack = data["drug/rep2/a.tif"]
    assert np.array_equal(tifffile.imread(copies / "drug/rep2/a.tif"), stack[1:4].mean(axis=0))
    nb.projection_first.value = 4  # after the last: said, never swapped
    assert copies_folder(out, Projection("sum", (0, 2))).name == "projected_sum_z0-2"
    assert projection_words("max", None) == "max projection of all slices"


def test_a_refused_projection_writes_nothing_and_says_why(tmp_path):
    nb, root, out, _ = started(tmp_path, "ZCYX", (5, 3, 16, 16))
    nb.projection_method.value = "max"
    nb.projection_first.value, nb.projection_last.value = 3, 1
    assert nb.projection_button.disabled
    assert "the first slice is after the last one" in _text(nb.projection_blocker.value)
    assert not list(out.glob("projected_*"))


def test_opening_the_stacks_again_offers_the_copies_written_earlier(tmp_path):
    nb, root, out, _ = started(tmp_path, "ZCYX", (5, 3, 16, 16))
    nb.projection_method.value = "sum"
    nb.preview_checked.button.click()
    assert nb._write_projection().written
    assert [(p.name, made.method) for p, made in earlier_projections(root.resolve(), out)] == [
        ("projected_sum", "sum")
    ]
    again = PreprocessingNotebook(str(root), output_folder=str(out))
    again.steps()
    again.scan_button.click()
    again.check_button.click()
    (offer,) = again.projection_earlier.children
    assert "Continue with the copies written earlier: sum projection of all slices" in (
        offer.description
    )
    stamp = (out / "projected_sum" / RECORD_FILENAME).stat().st_mtime_ns
    offer.click()
    assert again.folder.value == str(out / "projected_sum") and again.confirmed_mapping is None
    assert (out / "projected_sum" / RECORD_FILENAME).stat().st_mtime_ns == stamp  # not rewritten


def test_the_projection_can_be_looked_at_before_anything_is_written(tmp_path):
    nb, root, out, data = started(tmp_path, "ZCYX", (5, 3, 16, 16))
    assert nb.projection_look.disabled  # no method yet
    nb.projection_method.value = "max"
    nb.projection_look.click()
    assert len(nb.projection_tiles.children) == 3
    assert "for looking only" in nb.projection_caption.value
    assert not list(out.glob("projected_*"))
    rel = nb.axes_viewer.image
    mapping = AxisMapping("ZCYX", tuple("ZCYX"))
    view = read_projection_view(
        root / rel, mapping, (5, 3, 16, 16), Projection("max"), ViewRequest()
    )
    assert len(view.tiles) == 3 and view.z is None and view.dtype == "projection"
    with pytest.raises(ValueError, match="no depth"):
        read_projection_view(
            root / rel, AxisMapping("ZCYX", tuple("TCYX")), (5, 3, 16, 16), Projection("max")
        )
