"""A failed or stale Check folder in the shipped steps() leaves nothing of the earlier check.

Issue #101 (review of PR #528): the stepper shows more of a check than the stacked pages (the
Images page's axes and type lines, the Axes page's one-click offer, the copies note, the checked
viewer, the recommended bins); a failed check, an error after the check and a changed folder
clear all of it, and an interrupted check is cleared, then raised.
"""

from __future__ import annotations

import html
import re

import pytest

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")

from morphobricks_preprocessing import notebook_folder  # noqa: E402
from morphobricks_preprocessing import notebook_style as ui  # noqa: E402
from morphobricks_preprocessing.notebook_ui import PreprocessingNotebook  # noqa: E402
from test_check_folder_errors import failing_preflight  # noqa: E402
from test_notebook_projection import write_stacks  # noqa: E402


def _text(text):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text))).strip()


@pytest.fixture
def copies(tmp_path):
    """Scan and check Z-stacks, write their projected copies: the stepper works on the copies.

    Everything a check shows on the steps is then on screen: the axes and type lines and the
    copies note on Images, the one-click offer with its enabled button on Axes, the viewer over
    the checked images and the recommended bins.
    """
    root, out = tmp_path / "exp", tmp_path / "out"
    write_stacks(root, "ZCYX", (5, 3, 16, 16))
    nb = PreprocessingNotebook(str(root), output_folder=str(out))
    nb.steps()
    nb.scan_button.click()
    nb.check_button.click()
    nb.projection_method.value = "max"
    nb.preview_checked.button.click()
    assert nb._write_projection().written
    assert nb.preflight is not None and nb.preflight.ok
    assert "they fit every one" in _text(nb.scan_axes_line.value)
    assert "Image type" in _text(nb.scan_type_line.value)
    assert "recorded 2-D copies" in _text(nb.copy_note.value) and nb.copy_note_images.value
    assert ui.is_visible(nb.quick_card) and not nb.quick_confirm.disabled
    assert nb.quick_text.value and "valid for every image" in _text(nb.axis_status.value)
    assert nb.scan_viewer.entries and ui.is_visible(nb.scan_viewer.widget)
    assert nb.axes_viewer.entries
    assert nb._default_bins and "Recommended: 65,536 bins" in _text(nb.recommended_hint.value)
    assert nb.bins_hint.value
    return nb


def assert_nothing_of_the_check(nb):
    """No widget of the stepper shows the earlier check, and nothing can be confirmed or run."""
    assert nb.preflight is None and nb.mapping is None and nb.confirmed_mapping is None
    assert nb.scan_axes_line.value == "" and nb.scan_type_line.value == ""
    assert nb.copy_note.value == "" and nb.copy_note_images.value == ""
    assert not ui.is_visible(nb.quick_card) and nb.quick_confirm.disabled
    assert nb.quick_text.value == "" and nb.quick_caption.value == ""
    assert nb.axis_status.value == ""
    assert not ui.is_visible(nb.projection_card) and not ui.is_visible(nb.zarr_card)
    for viewer in (nb.scan_viewer, nb.axes_viewer, nb.channels_viewer, nb.check_viewer):
        assert viewer.entries == () and not ui.is_visible(viewer.widget)
    assert not ui.is_visible(nb.scan_roles_card)
    assert nb._default_bins is None and nb.bins_hint.value == ""
    assert "bins per plane" not in _text(nb.recommended_hint.value)
    assert nb.run_button.disabled


def test_a_failed_check_clears_what_the_steps_showed_of_the_earlier_one(copies, monkeypatch):
    nb = copies
    monkeypatch.setattr(notebook_folder, "preflight", failing_preflight(OSError(5, "I/O")))
    assert nb.check_folder() is None
    assert_nothing_of_the_check(nb)
    assert "The folder could not be checked" in nb.folder_status.value
    assert not nb.check_button.disabled  # still scanned as typed: Check folder again works
    nb.quick_confirm.click()  # a hidden button clicked anyway confirms nothing
    assert nb.confirmed_mapping is None
    monkeypatch.undo()
    nb.check_button.click()  # the retry shows the check again
    assert nb.preflight is not None and ui.is_visible(nb.quick_card)
    assert not nb.quick_confirm.disabled and nb.scan_axes_line.value


def test_a_changed_folder_clears_what_the_steps_showed_of_the_earlier_one(copies, tmp_path):
    nb = copies
    nb.folder.value = str(tmp_path / "exp")  # the originals again: the copies' check is stale
    assert_nothing_of_the_check(nb)
    assert "The folder changed" in nb.folder_status.value
    assert not ui.is_visible(nb.scan_box)  # the scan is of the other folder: scan again


@pytest.mark.parametrize("where", ["_show_folder_results", "_reset_settings", "_refresh_axis_step"])
def test_an_error_after_the_check_leaves_no_check_to_run(copies, monkeypatch, where):
    nb = copies
    real = getattr(type(nb), where)
    calls = []

    def broken(self, *args, **kwargs):  # fails while the check is shown, not while cleared
        calls.append(where)
        if len(calls) == 1:
            raise OSError(5, "I/O while showing the check")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(type(nb), where, broken)
    assert nb.check_folder() is None
    monkeypatch.undo()
    assert_nothing_of_the_check(nb)
    assert "The folder could not be checked" in nb.folder_status.value


def test_a_programming_error_after_the_check_is_raised_after_clearing(copies, monkeypatch):
    nb = copies
    monkeypatch.setattr(
        type(nb), "_reset_settings", lambda *a, **k: (_ for _ in ()).throw(KeyError("bug"))
    )
    with pytest.raises(KeyError, match="bug"):
        nb.check_folder()
    monkeypatch.undo()
    assert_nothing_of_the_check(nb)


def test_an_interrupted_check_is_cleared_then_raised(copies, monkeypatch):
    nb = copies
    monkeypatch.setattr(notebook_folder, "preflight", failing_preflight(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        nb.check_folder()
    assert_nothing_of_the_check(nb)
    assert "KeyboardInterrupt" in nb.folder_status.value
    assert not nb.check_button.disabled


@pytest.mark.parametrize("where", ["_prefill_from_output", "_update_output_hint"])
def test_a_storage_error_while_the_folder_changes_leaves_no_check(
    copies, tmp_path, monkeypatch, where
):
    nb = copies

    def broken(*args, **kwargs):
        raise OSError(5, "I/O while reading the output folder")

    monkeypatch.setattr(type(nb), where, broken)
    nb.folder.value = str(tmp_path / "exp")  # no traceback out of the observer
    monkeypatch.undo()
    assert_nothing_of_the_check(nb)
    status = _text(nb.folder_status.value)
    assert "The folder changed" in status and "OSError" in status


@pytest.mark.parametrize(
    "exc",
    [SystemExit("x"), SystemExit(2), GeneratorExit()],
    ids=["exit-str", "exit-int", "generator-exit"],
)
def test_an_exit_is_cleared_then_raised_not_swallowed(copies, monkeypatch, exc):
    """``SystemExit("msg")`` has a str ``code`` but is not a coded refusal: it propagates."""
    nb = copies
    monkeypatch.setattr(notebook_folder, "preflight", failing_preflight(exc))
    with pytest.raises(type(exc)):
        nb.check_folder()
    monkeypatch.undo()
    assert_nothing_of_the_check(nb)


def test_a_clearing_error_does_not_hide_the_original_on_the_steps(copies, monkeypatch):
    nb = copies
    monkeypatch.setattr(notebook_folder, "preflight", failing_preflight(KeyError("bug")))

    def broken(self, status):
        raise RuntimeError("clearing failed")

    monkeypatch.setattr(type(nb), "_clear_checked", broken)
    with pytest.raises(KeyError, match="bug"):
        nb.check_folder()
    monkeypatch.undo()
