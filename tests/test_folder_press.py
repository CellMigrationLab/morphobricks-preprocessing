"""Folders are read on a button press only, and old results never stay on screen (#579, #517).

Owner decision on #579 (26 November 2026): typing or pasting a folder path never starts a
scan, a check or a restore; editing a folder box only clears what came from the old folders
and says which button to press. The folder check's line has a ticking time and counts the
parts of its comparison. #517: the Run step's result never outlives its output folder or a
new attempt.
"""

import re
import shutil

import pytest

from test_notebook_check_save import FOLDERS, add_folder

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")

from morphobricks_preprocessing import notebook_folder, notebook_progress  # noqa: E402
from morphobricks_preprocessing import notebook_style as ui  # noqa: E402
from morphobricks_preprocessing.notebook_ui import PreprocessingNotebook  # noqa: E402
from morphobricks_preprocessing.run.preflight import ACQUISITION_PARTS, preflight  # noqa: E402


def _text(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def experiment(base, name, seed=1):
    root = base / name
    for i, folder in enumerate(FOLDERS):
        add_folder(root, base / f"{name}_masks", folder, seed + i)
    shutil.rmtree(base / f"{name}_masks")
    return root


def nothing_read(monkeypatch):
    """Make every reading of a folder fail the test: the scan, the check and the restore."""

    def refuse(*args, **kwargs):
        raise AssertionError("a folder was read on an edit")

    monkeypatch.setattr(
        "morphobricks_preprocessing.notebook_quick_scan.discover_experiment", refuse
    )
    monkeypatch.setattr(notebook_folder, "preflight", refuse)
    monkeypatch.setattr("morphobricks_preprocessing.notebook_restore.earlier_work", refuse)


@pytest.mark.parametrize("stepper", [True, False])
def test_typing_a_folder_path_starts_no_scan_check_or_restore(tmp_path, monkeypatch, stepper):
    root = experiment(tmp_path, "A")
    nb = PreprocessingNotebook()
    nb.steps() if stepper else nb.folder_step()
    nothing_read(monkeypatch)
    for typed in (str(root)[:3], str(root)[:-1], str(root)):  # on the way, and the path
        nb.folder.value = typed
    for typed in (str(tmp_path / "o"), str(tmp_path / "out")):
        nb.output.value = typed
    assert nb.preflight is None and not ui.is_visible(nb.restore_box)


def test_two_scans_of_two_folders_leave_nothing_of_the_first(tmp_path):
    first, second = experiment(tmp_path, "A", 1), experiment(tmp_path, "B", 7)
    nb = PreprocessingNotebook(str(first), output_folder=str(tmp_path / "outA"))
    nb.steps()
    nb.scan_button.click()
    nb.check_button.click()
    nb.quick_confirm.click()
    assert nb.preflight is not None and nb.confirmed_mapping is not None
    assert str(first) in nb._scanned[0]
    nb.folder.value = str(second)  # edited: everything of A is cleared at once, nothing read
    nb.output.value = str(tmp_path / "outB")
    assert nb.preflight is None and nb.confirmed_mapping is None and nb.result is None
    assert not ui.is_visible(nb.scan_box) and nb.scan_tree.value == ""
    assert nb.scan_example.children == ()
    assert not ui.is_visible(nb.labels_card) and nb.folder_tiles.value == ""
    assert "Press Scan the folders" in _text(nb.scan_status.value)
    assert nb.check_button.disabled
    nb.scan_button.click()
    assert nb._scanned[0] == str(second) and nb._scan.root.name == "B"
    nb.check_button.click()
    assert str(nb.preflight.discovery.root) == str(second.resolve())
    assert all(str(first) not in str(path) for path in nb.preflight.header_fingerprints)
    assert str(first) not in nb.labels_table.value


def test_the_folder_check_runs_with_a_ticking_time(tmp_path, monkeypatch):
    # #579: the line stayed at "Comparing the images · 0:20 elapsed": the check did not run
    # inside Wait.ticking(), so the time moved only when a step was reported.
    root = experiment(tmp_path, "A")
    nb = PreprocessingNotebook(str(root), output_folder=str(tmp_path / "out"))
    nb.folder_step()
    ticking = []
    real_ticking = notebook_progress.Wait.ticking

    def spy(self, *args, **kwargs):
        ticking.append(self.target)
        return real_ticking(self, *args, **kwargs)

    monkeypatch.setattr(notebook_progress.Wait, "ticking", spy)
    inside = []
    real_preflight = notebook_folder.preflight

    def watched(*args, **kwargs):
        inside.append(list(ticking))
        return real_preflight(*args, **kwargs)

    monkeypatch.setattr(notebook_folder, "preflight", watched)
    nb.check_button.click()
    assert inside == [[nb.folder_status]] and nb.preflight is not None


def test_the_elapsed_time_moves_during_an_uncounted_phase():
    import ipywidgets as widgets

    target = widgets.HTML()
    now = [0.0]
    wait = notebook_progress.Wait(target, clock=lambda: now[0])
    wait.start("Comparing the images")
    with wait.ticking(interval=0.01):
        now[0] = 75.0
        drawn = wait.drawn
        for _ in range(500):  # the thread redraws without any step being reported
            if wait.drawn > drawn:
                break
            notebook_progress.threading.Event().wait(0.01)
    assert "1:15" in target.value


def test_the_comparison_of_the_images_is_counted_part_by_part(tmp_path):
    root = experiment(tmp_path, "A")
    told = []
    preflight(root, tmp_path / "out", progress=lambda *call: told.append(call))
    parts = [call for call in told if call[0] == "acquisitions"]
    total = len(ACQUISITION_PARTS)
    assert parts == [
        *(("acquisitions", done, total, part) for done, part in enumerate(ACQUISITION_PARTS)),
        ("acquisitions", total, total, ""),
    ]


def test_the_check_line_counts_the_comparison(tmp_path, monkeypatch):
    monkeypatch.setattr(notebook_progress, "MIN_INTERVAL", 0.0)  # every step drawn
    root = experiment(tmp_path, "A")
    nb = PreprocessingNotebook(str(root), output_folder=str(tmp_path / "out"))
    nb.folder_step()
    lines = []
    nb.folder_status.observe(lambda change: lines.append(_text(change["new"])), names="value")
    nb.check_button.click()
    assert any("Comparing the images" in line and "1 of 3" in line for line in lines), lines


# #517: the Run step's result is of its output folder and of the latest attempt ---------------


def ran(tmp_path):
    from test_notebook_preprocessing_cellpose import ran_app

    return ran_app(tmp_path)


@pytest.mark.usefixtures("legacy_grid")
def test_another_output_folder_clears_the_result_shown(tmp_path):
    nb = ran(tmp_path)
    assert nb.result is not None and nb.result.written
    nb.output.value = str(tmp_path / "empty_output")
    assert nb.result is None and not nb._run_state().done


@pytest.mark.usefixtures("legacy_grid")
def test_a_run_that_raises_leaves_no_earlier_result(tmp_path, monkeypatch):
    nb = ran(tmp_path)
    assert nb.result is not None

    def broken(*args, **kwargs):
        raise OSError(5, "Input/output error")

    monkeypatch.setattr(nb, "_call_run", broken)
    assert nb.run() is None
    assert nb.result is None


def test_a_rescan_of_the_checked_folder_shows_the_image_its_caption_names(tmp_path):
    # #520: a quick rescan of the checked folder chose an image from the fresh scan but drew
    # it from the earlier check's viewer, which did not list a file added since: the caption
    # named one image and another (or none) was shown. A new scan now ends the earlier check.
    root = experiment(tmp_path, "A")
    nb = PreprocessingNotebook(str(root), output_folder=str(tmp_path / "out"))
    nb.steps()
    nb.scan_button.click()
    nb.check_button.click()
    nb.quick_confirm.click()
    assert nb.preflight is not None
    add_folder(root, tmp_path / "late_masks", "late/rep1", 9)
    nb.scan_button.click()
    assert nb.preflight is None and nb.confirmed_mapping is None  # nothing carried over
    images = [e.image for e in nb.scan_viewer.entries]
    assert "late/rep1/a.tif" in images
    assert nb.scan_viewer.image == nb.scan_image
    assert nb.scan_image in _text(nb.scan_example.children[0].value)
