"""Folder edits and presses after the external review of #582 (bc, 27 November 2026).

- B4: typing an output folder that holds a run opened that whole run (every image checked, in
  a strict run hashed) for the tissue-mask card, without a press. Now an edit opens nothing.
- B5: a scan made stale by an edit was only hidden: returning to the scanned text, or a scan
  that failed, left the old scan usable for the folder check. Now an edit, and the start of a
  press, forget it; a late result (the folders edited while they were listed) is discarded.
- F2 (#517): a press refused for its settings left the earlier result as if it were its own.
- F4: the time went on while the folders were listed, not while the one image was opened.
"""

import shutil

import pytest

from he_tissue_tree import channels_run, rgb_run
from test_folder_press import experiment

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")

from morphobricks_preprocessing import notebook_progress, notebook_quick_scan  # noqa: E402
from morphobricks_preprocessing import notebook_style as ui  # noqa: E402
from morphobricks_preprocessing import notebook_tissue_mask, run_inputs  # noqa: E402
from morphobricks_preprocessing import source_content  # noqa: E402
from morphobricks_preprocessing.notebook_ui import PreprocessingNotebook  # noqa: E402

# B4 --------------------------------------------------------------------------------------------


def _no_run_opened(monkeypatch, opened):
    """Record every opening of a run and every content SHA-256 taken (none on an edit)."""

    def trap(name):
        def refuse(*args, **kwargs):
            opened.append(name)
            raise AssertionError(f"{name} on an edit")

        return refuse

    monkeypatch.setattr(notebook_tissue_mask, "open_checked", trap("open_checked"))
    monkeypatch.setattr(run_inputs.RunInputs, "_read", trap("RunInputs._read"))
    monkeypatch.setattr(source_content, "source_content_sha256", trap("content SHA-256"))


@pytest.mark.parametrize("kind", ["rgb", "channels"])
def test_typing_an_output_folder_with_a_run_opens_nothing(tmp_path, monkeypatch, kind):
    run = (rgb_run if kind == "rgb" else channels_run)(tmp_path / "run")
    other = experiment(tmp_path, "A")
    nb = PreprocessingNotebook(str(other), output_folder=str(tmp_path / "outA"))
    nb.steps()
    opened = []
    _no_run_opened(monkeypatch, opened)
    for typed in (str(run.output_folder), str(tmp_path / "outA"), str(run.output_folder)):
        nb.output.value = typed  # A -> B -> A: nothing opened, nothing hashed
    assert opened == []
    assert nb.tissue_mask.pending == run.output_folder and not nb.tissue_mask.has_rgb
    assert ui.is_visible(nb.tissue_mask.open_button)
    assert "Press <b>Open the run</b>" in nb.tissue_mask.hint.value
    monkeypatch.undo()
    nb.tissue_mask.open_button.click()  # the press opens it, and only the press
    assert nb.tissue_mask.has_rgb is (kind == "rgb")
    assert nb.tissue_mask.pending is None


def test_an_experiment_folder_whose_default_output_holds_a_run_opens_nothing(
    tmp_path, monkeypatch
):
    run = rgb_run(tmp_path / "run")
    default = run.experiment_root.parent / f"{run.experiment_root.name}_MorphoBricks_Preprocessing"
    shutil.copytree(run.output_folder, default)
    nb = PreprocessingNotebook()
    nb.steps()
    opened = []
    _no_run_opened(monkeypatch, opened)
    nb.folder.value = str(run.experiment_root)  # the default output folder follows it
    nb._refresh_mask_step()  # as any later refresh of step 3 after the edit
    assert opened == []
    assert nb.tissue_mask.pending == default.resolve()


def test_a_run_press_opens_the_run_it_wrote(tmp_path):
    run = rgb_run(tmp_path / "run")
    nb = PreprocessingNotebook(str(run.experiment_root), output_folder=str(run.output_folder))
    nb.steps()
    assert not nb.tissue_mask.has_rgb  # shown, not opened
    nb.tissue_mask.refresh(run.output_folder, open_run=True)
    assert nb.tissue_mask.has_rgb


# B5 --------------------------------------------------------------------------------------------


def _scanned(tmp_path):
    first, second = experiment(tmp_path, "A", 1), experiment(tmp_path, "B", 7)
    nb = PreprocessingNotebook(str(first), output_folder=str(tmp_path / "outA"))
    nb.steps()
    nb.scan_button.click()
    assert not nb.check_button.disabled
    return nb, first, second


def test_back_to_the_scanned_folder_without_a_press_needs_a_new_scan(tmp_path):
    nb, first, second = _scanned(tmp_path)
    nb.folder.value = str(second)
    nb.folder.value = str(first)  # A -> B -> A, no press
    assert nb.check_button.disabled
    assert nb._scan is None and nb._scanned is None and nb.scan_image is None
    assert "Scan the folders" in nb.check_blocker.value
    nb.scan_button.click()  # then a press scans and the check is offered again
    assert not nb.check_button.disabled and nb._scan.root.name == "A"


def test_a_failed_rescan_leaves_no_earlier_scan_usable(tmp_path, monkeypatch):
    nb, first, _ = _scanned(tmp_path)

    def unreadable(*args, **kwargs):
        raise OSError(5, "Input/output error")

    monkeypatch.setattr(notebook_quick_scan, "discover_experiment", unreadable)
    nb.scan_button.click()
    assert nb._scan is None and nb._scanned is None
    assert nb.check_button.disabled
    assert "Input/output error" in nb.scan_status.value
    assert "The folders could not be scanned" in nb.scan_status.value
    monkeypatch.undo()
    nb.scan_button.click()
    assert not nb.check_button.disabled


def test_a_scan_whose_folders_were_edited_meanwhile_is_discarded(tmp_path, monkeypatch):
    nb, first, second = _scanned(tmp_path)
    real = notebook_quick_scan.discover_experiment

    def edited_meanwhile(*args, **kwargs):
        found = real(*args, **kwargs)
        nb.folder.value = str(second)  # the user typed while the folders were listed
        return found

    monkeypatch.setattr(notebook_quick_scan, "discover_experiment", edited_meanwhile)
    assert nb._quick_scan() is None
    assert nb._scan is None and nb._scanned is None and nb.check_button.disabled
    assert "changed while they were listed" in nb.scan_status.value
    assert nb.scan_tree.value == "" and nb.scan_example.children == ()


def test_a_folder_check_whose_folders_were_edited_meanwhile_is_discarded(tmp_path, monkeypatch):
    from morphobricks_preprocessing import notebook_folder

    nb, first, second = _scanned(tmp_path)
    real = notebook_folder.preflight

    def edited_meanwhile(*args, **kwargs):
        found = real(*args, **kwargs)
        nb.output.value = str(tmp_path / "elsewhere")
        return found

    monkeypatch.setattr(notebook_folder, "preflight", edited_meanwhile)
    assert nb.check_folder() is None
    assert nb.preflight is None and nb.confirmed_mapping is None
    assert "changed while they were checked" in nb.folder_status.value


def test_the_earlier_check_is_not_current_while_a_new_check_runs(tmp_path, monkeypatch):
    from morphobricks_preprocessing import notebook_folder

    nb, first, _ = _scanned(tmp_path)
    nb.check_button.click()
    nb.quick_confirm.click()
    assert nb.preflight is not None and nb.confirmed_mapping is not None
    seen = []
    real = notebook_folder.preflight

    def look(*args, **kwargs):
        seen.append((nb.preflight, nb.confirmed_mapping, nb.result))
        return real(*args, **kwargs)

    monkeypatch.setattr(notebook_folder, "preflight", look)
    nb.check_folder()
    assert seen == [(None, None, None)]


def test_a_same_root_rescan_with_an_addition_is_still_new(tmp_path):
    # #520 stays fixed: the rescan of the same folder shows what is there now.
    from test_notebook_check_save import add_folder

    nb, first, _ = _scanned(tmp_path)
    before = len(nb._scan.images)
    add_folder(first, tmp_path / "unused_masks", "ctrl/rep3", 11)
    nb.scan_button.click()
    assert len(nb._scan.images) == before + 1 and not nb.check_button.disabled


# F4 --------------------------------------------------------------------------------------------


def test_the_time_goes_on_while_the_one_image_is_opened(tmp_path, monkeypatch):
    first = experiment(tmp_path, "A")
    nb = PreprocessingNotebook(str(first), output_folder=str(tmp_path / "outA"))
    nb.steps()
    ticking, phases = [], []
    real_ticking, real_phase = notebook_progress.Wait.ticking, notebook_progress.Wait.phase

    def spy_ticking(self, *args, **kwargs):
        ticking.append(self.target)
        return real_ticking(self, *args, **kwargs)

    def spy_phase(self, label, *args, **kwargs):
        phases.append((label, len(ticking)))
        return real_phase(self, label, *args, **kwargs)

    opened = []
    show = nb._show_scan_example

    def spy_show(*args, **kwargs):
        opened.append(nb._scan_ticking)
        return show(*args, **kwargs)

    monkeypatch.setattr(notebook_progress.Wait, "ticking", spy_ticking)
    monkeypatch.setattr(notebook_progress.Wait, "phase", spy_phase)
    monkeypatch.setattr(nb, "_show_scan_example", spy_show)
    nb.scan_button.click()
    assert ("Opening one image", 1) in phases
    assert opened == [True]  # the one image is opened while the time goes on
    assert nb._scan_ticking is False


# F2 (#517) -------------------------------------------------------------------------------------


@pytest.mark.usefixtures("legacy_grid")
def test_a_press_refused_for_its_settings_clears_the_earlier_result(tmp_path):
    from test_notebook_preprocessing_cellpose import ran_app

    nb = ran_app(tmp_path)
    out = nb.result.output_folder
    files = sorted(p.name for p in out.iterdir())
    nb.recommended.value = False
    nb.histogram_range.value = "robust"
    nb.robust_low.value, nb.robust_high.value = 90.0, 10.0  # refused: low above high
    assert nb.run() is None
    assert nb.result is None
    assert nb.run_tiles.value == "" and nb.run_report.where.value == ""
    assert "The histogram range cannot be used" in nb.run_status.value
    assert sorted(p.name for p in out.iterdir()) == files  # nothing removed
