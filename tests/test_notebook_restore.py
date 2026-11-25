"""The notebook restores an output folder's earlier work as soon as it is set (v0.9.0).

Owner decision of 25 November 2026: when the output folder is set, earlier work found there is
restored with a message saying what, from when, and what was read again; *Start fresh* starts a
new session state without touching any file. The check reuses a recorded header only while its
image's fingerprint is unchanged; anything uncertain reads the header again and says so.
Both notebooks (Preprocessing and Preprocessing_Cellpose) run this same app in cell 3.
"""

from __future__ import annotations

import html
import importlib
import json
import re

import numpy as np
import pytest

from described_tree import describe_tree, run_described
from tiff_tree import write_data

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")

from morphobricks_preprocessing import earlier_work as earlier_module  # noqa: E402
from morphobricks_preprocessing import notebook_folder, prefill_mapping  # noqa: E402
from morphobricks_preprocessing import notebook_style as ui  # noqa: E402
from morphobricks_preprocessing.check_record import CHECK_RECORD_FILENAME  # noqa: E402
from morphobricks_preprocessing.description_file import DRAFT_FILENAME  # noqa: E402
from morphobricks_preprocessing.notebook_cellpose_app import app_folders  # noqa: E402
from morphobricks_preprocessing.notebook_ui import PreprocessingNotebook  # noqa: E402

IMAGES = ["ctrl/rep1/a.tif", "drug/rep1/a.tif"]
RUN, AXES = 6, 1
preflight_module = importlib.import_module("morphobricks_preprocessing.run.preflight")


def _text(value):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", value))).strip()


@pytest.fixture
def exp(tmp_path):
    root = tmp_path / "exp"
    rng = np.random.default_rng(0)
    for rel in IMAGES:
        write_data(root / rel, rng.integers(0, 200, (2, 16, 16), dtype=np.uint8), axes="TYX")
    return root, tmp_path / "out"


def stepper():
    """A new session of the shipped notebook (cell 3 of both notebooks)."""
    nb = PreprocessingNotebook()
    nb.steps()
    return nb


def checked(root, out):
    """Check the folder in a first session: the check writes its report and its record."""
    nb = PreprocessingNotebook(str(root), str(out))
    nb.steps()
    nb.scan_button.click()
    nb.check_button.click()
    assert nb.preflight is not None and (out / CHECK_RECORD_FILENAME).is_file()
    return nb


def files_of(folder):
    """Every file under ``folder`` with its bytes: what a test proves untouched."""
    return {p.relative_to(folder): p.read_bytes() for p in sorted(folder.rglob("*")) if p.is_file()}


def no_header_read(monkeypatch):
    """Make reading any image header fail: a restore that reads one fails the test."""

    def refuse(*args, **kwargs):
        raise AssertionError("an image header was read")

    monkeypatch.setattr(preflight_module, "inspect_image", refuse)


def counted_header_reads(monkeypatch):
    seen = []
    real = preflight_module.inspect_image

    def counted(path, rel=None):
        seen.append(rel)
        return real(path, rel)

    monkeypatch.setattr(preflight_module, "inspect_image", counted)
    return seen


def test_a_fresh_output_folder_restores_nothing_and_says_nothing(exp):
    root, out = exp
    nb = stepper()
    nb.folder.value = str(root)
    nb.output.value = str(out)
    assert nb.preflight is None and not ui.is_visible(nb.restore_box)
    assert nb.restore_status.value == "" and not out.exists()


def test_a_recorded_check_with_unchanged_images_is_reused_without_reading_a_header(
    exp, monkeypatch
):
    root, out = exp
    checked(root, out)
    no_header_read(monkeypatch)
    nb = stepper()
    nb.output.value = str(out)  # the experiment folder comes from the record
    assert nb.folder.value == str(root.resolve())
    assert nb.preflight is not None and nb.preflight.headers_reused == tuple(IMAGES)
    text = _text(nb.restore_status.value)
    assert text.startswith("Found earlier work from ") and "restored the folder check" in text
    assert "2 image headers reused" in text and "Read again" not in text
    assert ui.is_visible(nb.restore_box) and ui.is_visible(nb.restore_fresh)
    assert not nb.check_button.disabled  # Check folder again reads every header
    assert nb._stepper.current == AXES and "Opened on Axes" in text


def test_one_changed_image_is_read_again_and_the_message_says_so(exp, monkeypatch):
    root, out = exp
    checked(root, out)
    write_data(root / IMAGES[1], np.ones((2, 20, 20), np.uint8), axes="TYX")  # changed
    seen = counted_header_reads(monkeypatch)
    nb = stepper()
    nb.output.value = str(out)
    assert seen == [IMAGES[1]]
    assert nb.preflight.headers_reused == (IMAGES[0],)
    text = _text(nb.restore_status.value)
    assert "1 image header reused" in text
    assert f"Read again: 1 image header (new or changed since the recorded check): {IMAGES[1]}" in (
        text
    )
    assert "mb-warn" in nb.restore_status.value or "warn" in nb.restore_status.value


def test_a_record_that_cannot_be_used_reads_every_header_and_says_why(exp, monkeypatch):
    root, out = exp
    run_described(root, prefill_mapping("TYX"), output_folder=out, image_type="fluorescence")
    checked(root, out)
    (out / CHECK_RECORD_FILENAME).write_text("{not json", encoding="utf-8")
    seen = counted_header_reads(monkeypatch)
    nb = stepper()
    nb.folder.value = str(root)
    nb.output.value = str(out)
    assert sorted(seen) == IMAGES and nb.preflight.headers_reused == ()
    text = _text(nb.restore_status.value)
    assert f"{CHECK_RECORD_FILENAME} is not a usable record" in text
    assert "Read again: 2 image headers" in text


def test_a_record_of_another_software_version_is_not_reused(exp, monkeypatch):
    root, out = exp
    checked(root, out)
    path = out / CHECK_RECORD_FILENAME
    data = json.loads(path.read_text(encoding="utf-8"))
    data["versions"]["tifffile"] = "0.0.1"
    path.write_text(json.dumps(data), encoding="utf-8")
    seen = counted_header_reads(monkeypatch)
    nb = stepper()
    nb.output.value = str(out)
    assert sorted(seen) == IMAGES
    assert "written by another version of the software" in _text(nb.restore_status.value)


def test_saved_answers_are_restored_and_open_on_the_first_step_still_to_do(exp):
    root, out = exp
    first = checked(root, out)
    box = first.reference_boxes["drug"]
    box.value = "ctrl"  # an answer: saved in the draft at once
    assert (out / DRAFT_FILENAME).is_file()
    nb = stepper()
    nb.output.value = str(out)
    assert nb._draft_saved and nb.reference_boxes["drug"].value == "ctrl"
    text = _text(nb.restore_status.value)
    assert "restored the folder check, your saved answers" in text
    assert nb._stepper.current == nb._first_open() == AXES
    assert nb.confirmed_mapping is None  # nothing is confirmed for the user


def test_a_published_description_is_reopened(exp):
    root, out = exp
    checked(root, out)
    describe_tree(root, out)
    nb = stepper()
    nb.folder.value = str(root)
    nb.output.value = str(out)
    assert nb._published() is not None and not nb._published().changed
    text = _text(nb.restore_status.value)
    assert "restored the folder check, the saved experiment description" in text
    assert nb._stepper.current == nb._first_open() == AXES  # the roles wait for one click
    assert ui.is_visible(nb.quick_card)  # offered, never confirmed for the user


def test_without_a_recorded_experiment_folder_nothing_is_restored_and_it_says_so(exp):
    root, out = exp
    describe_tree(root, out)  # a description only: no check, no run recorded the folder
    nb = stepper()
    nb.output.value = str(out)
    assert nb.preflight is None and nb.folder.value == ""
    text = _text(nb.restore_status.value)
    assert "the saved experiment description" in text and "is not recorded" in text
    assert not ui.is_visible(nb.restore_fresh)


def test_a_finished_run_shows_its_results_and_the_next_steps(exp):
    root, out = exp
    result = run_described(
        root, prefill_mapping("TYX"), output_folder=out, image_type="fluorescence"
    )
    assert result.written
    nb = stepper()
    nb.folder.value = str(root)
    nb.output.value = str(out)
    assert nb.restored_run == out.resolve() and nb.result is None
    assert "the finished run of" in _text(nb.restore_status.value)
    assert _text(nb.run_status.value).startswith("The earlier run is complete")
    assert "Segment with Cellpose" in _text(nb.run_next.value)
    assert nb.csv_file.value == str(out.resolve() / "plane_histograms.csv")
    assert nb._stepper.current == RUN and nb._run_state().done
    # The combined notebook's cell 4 takes the restored run (it opens it through RunInputs).
    assert app_folders(nb) == (str(out.resolve()), "")


def test_the_combined_notebook_segments_the_restored_run(exp, monkeypatch):
    from morphobricks_preprocessing import notebook_preprocessing_cellpose as cell4

    root, out = exp
    run_described(root, prefill_mapping("TYX"), output_folder=out, image_type="fluorescence")
    nb = stepper()
    nb.output.value = str(out)
    monkeypatch.setattr(cell4, "cellpose_ready", lambda: False)  # no Cellpose installed here
    step = cell4.segmentation_step(nb)
    assert step.run_folder == str(out.resolve()) and step.refused == ""
    assert "The run of cell 3" in _text(step.notice.value)


def test_a_run_whose_images_changed_is_not_shown_as_finished(exp):
    root, out = exp
    run_described(root, prefill_mapping("TYX"), output_folder=out, image_type="fluorescence")
    write_data(root / IMAGES[0], np.ones((2, 16, 16), np.uint8), axes="TYX")
    nb = stepper()
    nb.folder.value = str(root)
    nb.output.value = str(out)
    assert nb.restored_run is None and app_folders(nb) == ("", "")
    assert "is not current" in _text(nb.restore_status.value)


def test_start_fresh_forgets_the_restore_and_touches_no_file(exp):
    root, out = exp
    run_described(root, prefill_mapping("TYX"), output_folder=out, image_type="fluorescence")
    checked(root, out)
    before = files_of(out), files_of(root)
    nb = stepper()
    nb.output.value = str(out)
    assert nb.preflight is not None and nb.restored_run is not None
    restored = files_of(out)
    nb.restore_fresh.click()
    assert nb.preflight is None and nb.restored_run is None and app_folders(nb) == ("", "")
    assert nb.run_status.value == ""
    assert _text(nb.restore_status.value).startswith("Started fresh: nothing restored is used.")
    assert not ui.is_visible(nb.restore_fresh)
    assert files_of(out) == restored and files_of(root) == before[1]
    # The restore itself rewrote only the check's report and record, as Check folder does.
    changed = {str(name) for name in restored if restored[name] != before[0].get(name)}
    assert changed <= {"discovery_report.json", "discovery_report.txt", CHECK_RECORD_FILENAME}
    nb.output.value = str(out)  # the same folder again: not restored behind the user's back
    assert nb.preflight is None


def test_another_output_folder_clears_what_was_restored(exp, tmp_path):
    root, out = exp
    checked(root, out)
    nb = stepper()
    nb.output.value = str(out)
    assert nb.preflight is not None
    nb.output.value = str(tmp_path / "other")
    assert nb.preflight is None and nb.mapping is None and nb.draft is None
    text = _text(nb.restore_status.value)
    assert text.startswith("The output folder changed: nothing restored from the earlier one")


def test_earlier_work_of_another_experiment_restores_nothing(exp, tmp_path):
    root, out = exp
    checked(root, out)
    other = tmp_path / "exp2"
    write_data(other / IMAGES[0], np.zeros((2, 16, 16), np.uint8), axes="TYX")
    nb = stepper()
    nb.folder.value = str(other)
    nb.output.value = str(out)
    assert nb.preflight is None
    assert "of another experiment folder" in _text(nb.restore_status.value)


def test_a_check_that_fails_during_the_restore_clears_and_says_so(exp, monkeypatch):
    root, out = exp
    checked(root, out)

    def broken(*args, **kwargs):
        raise OSError(5, "Input/output error")

    monkeypatch.setattr(notebook_folder, "preflight", broken)
    nb = stepper()
    nb.output.value = str(out)
    assert nb.preflight is None and nb.restored_run is None
    assert "the folder could not be checked" in _text(nb.restore_status.value)
    assert "Input/output error" in _text(nb.folder_status.value)


def test_an_unexpected_error_while_reading_the_earlier_work_never_stops_the_notebook(
    exp, monkeypatch
):
    root, out = exp
    checked(root, out)

    def broken(output):
        raise RuntimeError("cannot read it")

    monkeypatch.setattr("morphobricks_preprocessing.notebook_restore.earlier_work", broken)
    nb = stepper()
    nb.output.value = str(out)
    assert nb.preflight is None
    text = _text(nb.restore_status.value)
    assert "it could not be restored" in text and "RuntimeError: cannot read it" in text
    assert earlier_module.earlier_work(out).exists  # the real reader still finds the work


def test_check_folder_after_a_restore_reads_every_header_again(exp, monkeypatch):
    root, out = exp
    checked(root, out)
    nb = stepper()
    nb.output.value = str(out)
    seen = counted_header_reads(monkeypatch)
    nb.check_button.click()
    assert sorted(seen) == IMAGES and nb.preflight.headers_reused == ()
    assert not ui.is_visible(nb.restore_box)


def test_the_stacked_pages_restore_too(exp):
    root, out = exp
    checked(root, out)
    nb = PreprocessingNotebook()
    nb.folder_step()
    nb.output.value = str(out)
    assert nb.preflight is not None and nb.preflight.headers_reused == tuple(IMAGES)
    assert ui.is_visible(nb.restore_box) and "Opened on" not in _text(nb.restore_status.value)


def test_an_embedding_brick_without_the_output_folder_restores_nothing(exp):
    root, _ = exp
    checked(root, root.parent / f"{root.name}_MorphoBricks_Preprocessing")  # the default
    nb = PreprocessingNotebook()
    nb.folder_step(show_output=False)
    nb.folder.value = str(root)
    assert nb.preflight is None and not ui.is_visible(nb.restore_box)


# Review of #547 ------------------------------------------------------------------------------


def test_a_partial_path_typed_on_the_way_writes_and_restores_nothing(exp, tmp_path):
    root, out = exp
    checked(root, out)
    nb = stepper()
    # The boxes are read once typed (Enter or leaving them), never at each key.
    assert nb.output.continuous_update is False and nb.folder.continuous_update is False
    before = files_of(tmp_path)
    nb.folder.value = str(root)
    nb.output.value = str(out)[:-1]  # "ou": no such folder
    assert nb.preflight is None and not ui.is_visible(nb.restore_box)
    assert files_of(tmp_path) == before and not (tmp_path / "ou").exists()


def test_an_answer_after_a_restore_keeps_it_when_the_output_folder_changes(exp, tmp_path):
    root, out = exp
    checked(root, out)
    nb = stepper()
    nb.output.value = str(out)
    nb.reference_boxes["drug"].value = "ctrl"  # the user's own work on the restored check
    nb.output.value = str(tmp_path / "other")
    assert nb.preflight is not None  # kept, as after a manual Check folder (issue #98 rules)
    assert "The output folder changed" not in _text(nb.restore_status.value)


def test_a_confirmation_after_a_restore_keeps_it_too(exp, tmp_path):
    root, out = exp
    checked(root, out)
    nb = stepper()
    nb.output.value = str(out)
    assert ui.is_visible(nb.quick_card)
    nb.quick_confirm.click()
    assert nb.confirmed_mapping is not None
    nb.output.value = str(tmp_path / "other")
    assert nb.preflight is not None and nb.confirmed_mapping is not None


def test_a_run_started_after_a_restore_makes_it_the_user_s(exp):
    root, out = exp
    checked(root, out)
    nb = stepper()
    nb.output.value = str(out)
    assert nb._restored_from is not None
    nb._start_run()
    assert nb._restored_from is None and nb.restored_run is None


def test_a_record_and_a_run_of_different_experiment_folders_restore_nothing(exp, tmp_path):
    from morphobricks_preprocessing.check_record import write_check_record

    root, out = exp
    run_described(root, prefill_mapping("TYX"), output_folder=out, image_type="fluorescence")
    write_check_record(out, tmp_path / "elsewhere", {}, ())
    nb = stepper()
    nb.output.value = str(out)
    assert nb.preflight is None and nb.folder.value == ""
    text = _text(nb.restore_status.value)
    assert "names two experiment folders" in text and "They disagree" in text
