"""Owner feedback on v0.5.2 (12 November 2026): the axes click shows at once, the image type moves.

1. *Confirm axis roles* (and *Confirm these axes*) was slow and "it feels like nothing is
   happening": the button is now disabled and says *Working…*, and a progress line under it
   says *Working…*, before any of the work starts; the parts of the work then advance its bar
   (n of N, with what it does now). The click no longer reads every folder's picture for the
   folded per-folder image-type rows.
2. The image type is **proposed** at the quick scan (display only, from the headers; nothing is
   recorded there) and **confirmed** on the Channels step; it is no longer on Axes.
"""

import re

import pytest

from channel_images import write_channels, write_rgb
from test_notebook_check_save import exp  # noqa: F401

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")

from morphobricks_preprocessing import notebook_axis_preview  # noqa: E402
from morphobricks_preprocessing import notebook_style as ui  # noqa: E402
from morphobricks_preprocessing.notebook_ui import PreprocessingNotebook  # noqa: E402
from test_notebook_axes_quick import stepper  # noqa: E402

FOLDERS = ["ctrl/rep1", "ctrl/rep2", "drug/rep1", "drug/rep2"]
WORKING = "Working…"
AXES, CHANNELS = 1, 2


def _text(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def _holds(widget, target):
    if widget is target:
        return True
    return any(_holds(child, target) for child in getattr(widget, "children", ()))


def _types(nb):
    answers = nb.draft_answers
    return {} if answers is None else {k: u.image_type for k, u in answers.units.items()}


def _spy_first_work(monkeypatch, button, progress):
    """Record the button and its progress line when the first work of the click starts."""
    seen = []
    real = notebook_axis_preview.review_axes

    def spy(*args, **kwargs):
        if not seen:
            seen.append((button.disabled, button.description, progress.value))
        return real(*args, **kwargs)

    monkeypatch.setattr(notebook_axis_preview, "review_axes", spy)
    return seen


# --- 1. the click is seen at once ----------------------------------------------------------------


@pytest.mark.parametrize("path", ["quick", "roles"])
def test_the_working_state_is_set_before_the_work_starts(exp, monkeypatch, path):  # noqa: F811
    nb = stepper(*exp)
    if path == "quick":
        button, progress = nb.quick_confirm, nb.quick_progress
    else:
        nb.preview_checked.set(True)
        button, progress = nb.confirm_button, nb.confirm_progress
    assert not button.disabled
    seen = _spy_first_work(monkeypatch, button, progress)
    button.click()
    ((disabled, words, line),) = seen
    assert disabled and WORKING in words  # the button, before any work
    assert "mb-bar" in line and "0 of" in _text(line)  # the folder check's progress line
    assert nb.confirmed_mapping is not None
    assert progress.value == ""  # cleared once done; the confirmation says the rest
    if path == "roles":
        assert not button.disabled and "Change axis roles" in button.description


def test_the_progress_bar_advances_through_every_part_of_the_click(exp):  # noqa: F811
    nb = stepper(*exp)
    lines = []
    nb.quick_progress.observe(lambda change: lines.append(change["new"]), names="value")
    nb.quick_confirm.click()
    steps = notebook_axis_preview.CONFIRM_STEPS
    shown = [_text(line) for line in lines if line]
    assert len(steps) > 1 and shown
    assert shown[0].startswith(WORKING) and f"0 of {len(steps)}" in shown[0]
    for index, label in enumerate(steps):
        assert any(label in s and f"{index} of {len(steps)}" in s for s in shown), label
    widths = [float(w) for w in re.findall(r"width:([\d.]+)%", "".join(lines))]
    assert widths == sorted(widths) and widths[-1] > widths[0]  # the bar only moves on
    assert lines[-1] == ""


def test_taking_the_confirmation_back_shows_working_too(exp, monkeypatch):  # noqa: F811
    nb = stepper(*exp)
    nb.quick_confirm.click()
    lines = []
    nb.confirm_progress.observe(lambda change: lines.append(change["new"]), names="value")
    nb.confirm_button.click()  # Change axis roles
    assert nb.confirmed_mapping is None
    assert WORKING in _text(lines[0]) and lines[-1] == ""


@pytest.mark.parametrize("path", ["quick", "roles"])
def test_a_failure_part_way_gives_the_button_back_and_shows_the_error(exp, path):  # noqa: F811
    nb = stepper(*exp)

    def boom():
        raise RuntimeError("the disk went away")

    nb._refresh_run_step = boom  # part 4 of 5: after the roles are confirmed
    if path == "quick":
        button = nb.quick_confirm
    else:
        nb.preview_checked.set(True)
        button = nb.confirm_button
    button.click()
    assert not button.disabled and WORKING not in button.description  # never stuck
    assert WORKING not in nb.quick_confirm.description + nb.confirm_button.description
    shown = [p for p in (nb.quick_progress, nb.confirm_progress) if p.value]
    assert len(shown) == 1
    (said,) = shown
    assert "mb-error" in said.value
    assert "RuntimeError: the disk went away" in _text(said.value)
    card = nb.quick_card if said is nb.quick_progress else nb.axis_box
    assert ui.is_visible(card)  # said where it is seen, not under a hidden card
    assert "the disk went away" in nb.details.value  # the full report in Technical details


def test_a_disabled_confirmation_does_nothing_and_shows_no_progress(exp):  # noqa: F811
    nb = stepper(*exp)
    assert nb.confirm_button.disabled  # the example is not ticked
    nb.confirm_button.click()
    assert nb.confirmed_mapping is None and nb.confirm_progress.value == ""
    assert "Confirm axis roles" in nb.confirm_button.description


def test_the_click_reads_no_picture_for_the_folded_per_folder_rows(exp, monkeypatch):  # noqa: F811
    nb = stepper(*exp)
    nb.quick_confirm.click()
    thumbs = nb._channel_thumbs
    assert thumbs is not None and not thumbs._strips  # no folder's channels were read
    nb.type_line.per_folder.selected_index = 0  # Set per folder… opened: now they are
    assert thumbs._strips and "no picture" not in repr(nb.type_rows.rows.children)


# --- 2. the image type: proposed at the scan, confirmed on Channels ------------------------------


def _colour_exp(tmp_path):
    root = tmp_path / "exp"
    for i, folder in enumerate(FOLDERS):
        write_rgb(root / folder / "a.tif", seed=i + 1)
    return root, tmp_path / "out"


def test_the_image_type_is_not_on_axes_and_is_confirmed_on_channels(exp):  # noqa: F811
    nb = stepper(*exp)
    pages = nb._stepper.pages
    assert not _holds(pages[AXES], nb.type_line.widget)
    assert not _holds(pages[AXES], nb.image_type_card)  # nor the old card
    assert _holds(pages[CHANNELS], nb.type_line.widget)
    page = pages[CHANNELS].children[1].children[1].children
    assert list(page).index(nb.type_line.widget) < list(page).index(nb.channel_labels.widget)
    nb.quick_confirm.click()
    assert nb._stepper.states()[AXES].done  # Axes asks nothing about the type
    assert not nb._stepper.states()[CHANNELS].done
    assert set(_types(nb).values()) <= {None}  # pre-filled, not recorded
    nb.type_line.apply.click()  # the explicit click, on Channels
    assert set(_types(nb).values()) == {"channels"}


def test_the_image_type_is_proposed_at_the_scan_and_nothing_is_recorded(tmp_path):
    root, out = _colour_exp(tmp_path)
    nb = PreprocessingNotebook(str(root), output_folder=str(out))
    nb.steps()
    nb.scan_button.click()
    # Before the folder check: the shown image's header, display only.
    note = _text(nb.scan_type_note.value)
    assert "Image type , from this image's header: colour (RGB)" in note
    assert "nothing is recorded here" in note and "Channels (step 3)" in note
    assert nb.preflight is None and nb.draft is None
    assert not out.exists() or not any(out.iterdir())
    nb.check_button.click()
    # After it: every header, display only; nothing recorded until the click on Channels.
    line = _text(nb.scan_type_line.value)
    assert "Image type proposed from the headers: colour (RGB) in all 4 images" in line
    assert _holds(nb._stepper.pages[0], nb.scan_type_line)
    assert set(_types(nb).values()) <= {None}
    nb.quick_confirm.click()
    assert nb.type_line.choice.value == "rgb"  # the same headers pre-fill the Channels line
    assert set(_types(nb).values()) <= {None}
    nb.type_line.apply.click()
    assert set(_types(nb).values()) == {"rgb"}


def test_headers_that_differ_are_said_at_the_scan_with_the_refusal(tmp_path):
    root = tmp_path / "exp"
    for i, folder in enumerate(FOLDERS):
        if folder == "drug/rep2":
            write_rgb(root / folder / "a.tif", seed=i + 1)
        else:
            write_channels(root / folder / "a.tif", ["DAPI", "Actin", "Ch3"], ["blobs"] * 3, seed=i)
    nb = PreprocessingNotebook(str(root), output_folder=str(tmp_path / "out"))
    nb.steps()
    nb.scan_button.click()
    nb.check_button.click()
    line = _text(nb.scan_type_line.value)
    assert "The image headers differ: colour (RGB) in 1 image, separate channels in 3" in line
    assert "R_HETEROGENEOUS_LAYOUT_NOT_ENABLED" in line and ui.is_visible(nb.scan_box)
    assert set(_types(nb).values()) <= {None}
