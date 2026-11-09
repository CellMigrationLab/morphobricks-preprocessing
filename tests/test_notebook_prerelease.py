"""Pre-release follow-ups to #254 and #255 in the notebook and the command line.

- A header change under the same files reopens that replicate's "prepared and imaged the same
  way?" (experiment-v1 section 10, "a unit's ordered channel list"), and the command line's
  ``describe`` says so too: both compare the headers they read.
- Save stays allowed when the run's checks would refuse (owner decision, 2026-11-10); a line
  beside the button says that the run will stop, and why.
- The step-by-step "Yes, the preview looks right" is offered only while a preview of the current
  roles is shown, as the one-click *Confirm these axes* is (#255).
- "The run (step 12) can start" is said only once the run's checks have run and passed.
"""

import re

import pytest

from channel_images import write_channels
from morphobricks_preprocessing.cli import main
from morphobricks_preprocessing.description_reconcile import UnitFolders
from test_notebook_check_save import KINDS, answer_all, exp  # noqa: F401

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")

from morphobricks_preprocessing import notebook_style as ui  # noqa: E402
from test_notebook_axes_quick import stepper  # noqa: E402

UNIT = UnitFolders("ctrl", "rep1", "single")


def _text(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def _saved_then_rewritten(exp):  # noqa: F811
    """Save the experiment, then rewrite one header (swapped names; same file, shape, count)."""
    first = stepper(*exp)
    first.quick_confirm.click()
    answer_all(first)
    assert first.save_experiment()
    write_channels(exp[0] / "ctrl/rep1/a.tif", ["Actin", "DAPI", "Ch3"], KINDS, seed=1)
    return first


def _repeat_of(nb, folders):
    return next(u.repeat_id for u in nb._published().reconciliation.units if u.folders == folders)


# --- item 1: a header change reopens the replicate's confirmation --------------------------------


def test_a_header_change_asks_the_replicate_s_same_way_question_again(exp):  # noqa: F811
    # Regression: the reopen took the date back only when the folders differed, so a unit whose
    # headers changed (channels_changed) kept "Confirmed on ..." beside a refused description.
    first = _saved_then_rewritten(exp)
    saved = dict(first.draft_answers.repeats)
    nb = stepper(*exp)
    rec = nb._published().reconciliation
    rid = _repeat_of(nb, UNIT)
    assert rec.reopened == {rid: ("channels_changed",)}  # the real path, for that repeat only
    assert nb.draft_answers.repeats[rid].confirmed_on is None  # asked again, never kept
    assert nb.draft_answers.repeats[rid].id == saved[rid].id  # its ID is kept
    others = [label for label in saved if label != rid]
    assert others and all(
        nb.draft_answers.repeats[label].confirmed_on == saved[label].confirmed_on
        for label in others
    )  # the other replicates keep their date byte for byte (section 4.4a)
    assert not nb.same_way[rid].value  # its toggle is open again, not ticked for the user
    assert all(nb.same_way[label].value for label in others)


# --- item 4: the command line compares the headers it reads ---------------------------------------


def test_the_cli_reports_a_header_only_rewrite_as_the_notebook_does(exp, capsys):  # noqa: F811
    # Regression: describe compared folders and image counts only and said "No change." for
    # headers rewritten under the same files, where the notebook reopens the replicate.
    _saved_then_rewritten(exp)
    nb = stepper(*exp)
    rid = _repeat_of(nb, UNIT)
    root, _, out = exp
    capsys.readouterr()
    assert main(["describe", str(root), "--output", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "No change." not in printed
    assert f"repeat {rid} must be confirmed again: the channels in its image headers" in printed
    assert "headers changed: ctrl / rep1 / single" in printed
    assert main(["describe", str(root), "--output", str(out), "--publish"]) == 1
    assert f"repeat {rid}: confirm it again" in capsys.readouterr().out


def test_the_cli_says_nothing_changed_when_the_headers_are_as_saved(exp, capsys):  # noqa: F811
    first = stepper(*exp)
    first.quick_confirm.click()
    answer_all(first)
    assert first.save_experiment()
    root, _, out = exp
    capsys.readouterr()
    assert main(["describe", str(root), "--output", str(out), "--publish"]) == 0
    printed = capsys.readouterr().out
    assert "No change." in printed and "must be confirmed again" not in printed
    assert "Nothing to publish" in printed


# --- item 2: Save allowed, the refusal said beside the button -------------------------------------


def test_save_stays_allowed_beside_a_line_saying_the_run_will_stop_and_why(exp):  # noqa: F811
    _saved_then_rewritten(exp)
    nb = stepper(*exp)
    nb.quick_confirm.click()
    answer_all(nb)  # the user answers again, the replicate included
    nb._refresh_check_save()
    check = nb._check
    assert check is not None and check.can_save and not check.can_run
    codes = sorted({i.code for i in check.run_refusals})
    assert codes and not nb.save_button.disabled  # owner decision: saving stays allowed
    note = _text(nb.save_note.value)
    assert "the run (step 12) will stop" in note and "Refused:" in note
    assert all(code in note for code in codes)
    assert nb.save_note in nb.save_row.children and nb.save_button in nb.save_row.children
    assert ui.is_visible(nb.save_note)


def test_no_stop_line_when_the_run_s_checks_pass_or_have_not_run(exp):  # noqa: F811
    nb = stepper(*exp)
    nb._refresh_check_save()  # axes not confirmed: nothing is checked, nothing is said
    assert not nb._check and nb.save_note.value == "" and not ui.is_visible(nb.save_note)
    nb.quick_confirm.click()
    answer_all(nb)
    nb._refresh_check_save()
    assert nb._check.can_run and not nb.save_button.disabled
    assert nb.save_note.value == "" and not ui.is_visible(nb.save_note)


# --- item 3: the step-by-step preview tick --------------------------------------------------------


def test_the_preview_tick_is_offered_only_while_a_preview_of_these_roles_is_shown(
    exp,  # noqa: F811
    monkeypatch,
):
    # Regression: Show preview enabled the tick even when no plane could be drawn, and a
    # preview that failed later left it enabled (the one-click gate of #255 did not apply).
    from morphobricks_preprocessing import notebook_axis_preview
    from morphobricks_preprocessing.planes import PlaneReadError

    nb = stepper(*exp)
    nb.axes_details.click()
    nb.show_preview()
    assert nb._preview_drawn is not None and not nb.preview_checked.disabled  # the control
    real = notebook_axis_preview.read_plane

    def broken(*_args, **_kwargs):
        raise PlaneReadError("the pixels cannot be decoded")

    monkeypatch.setattr(notebook_axis_preview, "read_plane", broken)
    nb._render_preview()  # another plane, which cannot be read
    assert nb._preview_drawn is None and nb.preview_checked.disabled
    assert not nb.preview_checked.value
    nb.show_preview()  # Show preview again: still nothing drawn, still nothing offered
    assert nb.preview_checked.disabled and not nb.preview_checked.value
    assert nb.confirm_button.disabled
    monkeypatch.setattr(notebook_axis_preview, "read_plane", real)
    nb.show_preview()
    assert not nb.preview_checked.disabled and not nb.preview_checked.value  # never ticked


def test_a_ticked_preview_that_then_fails_is_unticked(exp, monkeypatch):  # noqa: F811
    from morphobricks_preprocessing import notebook_axis_preview
    from morphobricks_preprocessing.planes import PlaneReadError

    nb = stepper(*exp)
    nb.axes_details.click()
    nb.show_preview()
    nb.preview_checked.button.click()
    assert nb.preview_checked.value and not nb.confirm_button.disabled
    monkeypatch.setattr(
        notebook_axis_preview,
        "read_plane",
        lambda *_a, **_k: (_ for _ in ()).throw(PlaneReadError("gone")),
    )
    nb._render_preview()
    assert not nb.preview_checked.value and nb.preview_checked.disabled
    assert nb.confirm_button.disabled and nb.confirmed_mapping is None


# --- item 7: "can start" only once the run's checks passed ----------------------------------------


def test_a_reopen_before_the_axes_are_confirmed_does_not_say_the_run_can_start(exp):  # noqa: F811
    # Regression: an unchanged reopen said "Saved ..., The run (step 12) can start." while the
    # run's checks (which need the axis roles) had not run.
    first = stepper(*exp)
    first.quick_confirm.click()
    answer_all(first)
    assert first.save_experiment()
    assert "can start" in first.save_status.value  # the control: checks ran and passed
    nb = stepper(*exp)
    assert nb.confirmed_mapping is None and nb._saved_current()
    said = _text(nb.save_status.value)
    assert "can start" not in said
    assert "Confirm the axis roles (step 2) first" in said
    assert "nothing changed" in nb.saved_line.value  # true: the folders are as saved
    nb.quick_confirm.click()  # the user confirms the roles: the checks run and pass
    assert nb._published_check().can_run
    assert "The run (step 12) can start." in nb.save_status.value
