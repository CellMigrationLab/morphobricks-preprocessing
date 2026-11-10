"""Pre-release follow-ups to #254 and #255 in the notebook and the command line.

- A header change under the same files is the run's refusal, shown at once; a replicate's
  "prepared and imaged the same way?" is reopened only when the description's own "unit's
  ordered channel list" changes (experiment-v1 section 10; owner decision on the review of
  #257). The pre-save check runs publication's transition checks, so Save is enabled only for
  what publication accepts. The command line's ``describe`` compares the headers it reads.
- Save stays allowed when the run's checks would refuse (owner decision, 2026-11-10); a line
  beside the button says that the run will stop, and why.
- The step-by-step "Yes, the preview looks right" is offered only while a preview of the current
  roles is shown, as the one-click *Confirm these axes* is (#255).
- "The run can start" is said only once the run's checks have run and passed.
"""

import re
from dataclasses import replace
from datetime import date

import pytest

from channel_images import write_channels
from morphobricks_preprocessing.cli import main
from morphobricks_preprocessing.description_reconcile import UnitFolders
from test_notebook_check_save import KINDS, answer_all, exp  # noqa: F401

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")

from morphobricks_preprocessing import notebook_check_save  # noqa: E402
from morphobricks_preprocessing import notebook_style as ui  # noqa: E402
from test_notebook_axes_quick import stepper  # noqa: E402

UNIT = UnitFolders("ctrl", "rep1", "single")
KEY = ("ctrl", "rep1", "single")


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


class _FirstOfJanuary(date):
    @classmethod
    def today(cls):
        return date(2026, 1, 1)


SAVED_ON = "2026-01-01"


def _saved_long_ago(exp, monkeypatch, names):  # noqa: F811
    """Save the experiment on 2026-01-01, then rewrite ctrl/rep1's header with ``names``."""
    monkeypatch.setattr(notebook_check_save, "date", _FirstOfJanuary)
    first = stepper(*exp)
    first.quick_confirm.click()
    answer_all(first)
    assert first.save_experiment()
    monkeypatch.setattr(notebook_check_save, "date", date)
    assert {r.confirmed_on for r in first.draft_answers.repeats.values()} == {SAVED_ON}
    write_channels(exp[0] / "ctrl/rep1/a.tif", names, KINDS, seed=1)
    nb = stepper(*exp)
    nb.quick_confirm.click()
    return nb


def _tag(nb, index, label):
    """Tag channel ``index`` of ctrl/rep1 by hand (through no tag, as a re-tag passes)."""
    nb.channel_grid.cells[(KEY, index)].value = None
    nb.channel_grid.cells[(KEY, index)].value = label


def _dates(nb):
    return {label: r.confirmed_on for label, r in nb.draft_answers.repeats.items()}


def _published_dates(folders):
    text = (folders[2] / "morphobricks.toml").read_text(encoding="utf-8")
    return re.findall(r'confirmed_on = "([0-9-]+)"', text)


def test_d_a_header_change_shows_its_refusal_at_once_and_reopens_nothing(exp, monkeypatch):  # noqa: F811
    # Owner decision (review of #257, experiment-v1 section 10 read literally): a repeat is
    # reopened when "a unit's ordered channel list" in the DESCRIPTION changes. Headers that no
    # longer fit it are the run's refusal, shown at once; the date stays (section 4.4a), and
    # nothing is ticked or hidden.
    nb = _saved_long_ago(exp, monkeypatch, ["Hoechst", "Actin", "Ch3"])
    rid = _repeat_of(nb, UNIT)
    assert nb._published().reconciliation.reopened == {rid: ("channels_changed",)}
    assert _dates(nb) == {"rep1": SAVED_ON, "rep2": SAVED_ON}  # byte for byte (4.4a)
    assert all(toggle.value for toggle in nb.same_way.values())
    assert "headers changed: ctrl/rep1" in nb.folder_changes.value
    shown = _text(nb.checks.value)
    assert "The images contradict it: the run would stop" in shown
    assert "R_HEADER_CONTRADICTION" in shown and "&#x27;Hoechst&#x27;" in shown
    assert "can start" not in nb.save_status.value and nb.run_button.disabled


def test_a_a_rename_resolved_through_header_names_keeps_the_dates_saves_and_runs(
    exp,  # noqa: F811
    monkeypatch,
):
    # The blocker of #257 (reproduced on f8b57e3): the header rename DAPI -> Hoechst, resolved by
    # tagging channel 1 DNA again (Hoechst becomes one of DNA's header names), left the unit's
    # channel list unchanged, yet rep1's date had been dropped; the re-answer was dated today
    # and publication refused R_CONFIRMED_ON_CHANGED at repeats.rep1.
    nb = _saved_long_ago(exp, monkeypatch, ["Hoechst", "Actin", "Ch3"])
    _tag(nb, 0, "DNA")
    assert set(nb.draft_answers.channels["DNA"].header_names) == {"DAPI", "Hoechst"}
    assert _dates(nb) == {"rep1": SAVED_ON, "rep2": SAVED_ON}  # not reopened: kept
    assert "rep1: answer" not in _text(nb.todo.value)
    nb._refresh_check_save()
    assert nb._check.can_run and not nb._check.refusals
    assert not nb.save_button.disabled
    assert nb.save_experiment(), _text(nb.save_status.value)
    assert _published_dates(exp) == [SAVED_ON, SAVED_ON]
    assert "The run can start." in nb.save_status.value
    assert nb.run().written


def test_b_a_swap_resolved_by_the_unit_s_channel_order_reopens_only_that_repeat(
    exp,  # noqa: F811
    monkeypatch,
):
    # Section 10: a change of the unit's declared ordered channel list reopens its repeat. The
    # "same way?" is asked again (never ticked for the user); answered, it takes today's date
    # for that repeat only, and the transition check (section 4.4a) accepts it. Moving the two
    # channels is also a changed channel entity (section 4.6 (c): a position it held is
    # reassigned), which needs a recorded re-confirmation this notebook does not make: Save is
    # disabled with that reason, never with R_CONFIRMED_ON_CHANGED.
    nb = _saved_long_ago(exp, monkeypatch, ["Actin", "DAPI", "Ch3"])
    rid = _repeat_of(nb, UNIT)
    other = next(label for label in _dates(nb) if label != rid)
    assert _dates(nb)[rid] == SAVED_ON  # the header change alone reopens nothing
    nb.channel_grid.cells[(KEY, 0)].value = None
    nb.channel_grid.cells[(KEY, 1)].value = None
    assert _dates(nb)[rid] == SAVED_ON  # an untagged channel on the way declares no list
    nb.channel_grid.cells[(KEY, 0)].value = "ACTIN"
    nb.channel_grid.cells[(KEY, 1)].value = "DNA"
    assert nb.draft_answers.units[KEY].channels == ("ACTIN", "DNA", "PHASE")
    assert _dates(nb) == {rid: None, other: SAVED_ON}  # reopened: asked again
    assert not nb.same_way[rid].value and f"{rid}: answer" in _text(nb.todo.value)
    nb.same_way[rid].button.click()
    today = date.today().isoformat()
    assert _dates(nb) == {rid: today, other: SAVED_ON}
    check = nb._check
    codes = {i.code for i in check.refusals}
    assert codes == {"R_CONFIRMED_ID_CHANGED"}  # channels ACTIN and DNA, section 4.6 (c)
    assert nb.save_button.disabled and not nb.save_experiment()
    assert "R_CONFIRMED_ID_CHANGED at channels.DNA" in _text(nb.checks.value)
    assert _published_dates(exp) == [SAVED_ON, SAVED_ON]  # nothing written
    # Publication, given the section 4.6 records, accepts the new date for that repeat only.
    from morphobricks_preprocessing.description_transition import (
        Reconfirmation,
        transition_issues,
    )
    from morphobricks_preprocessing.draft_answers import description_from_draft
    from morphobricks_preprocessing.run.description_file import publish_description

    state = nb._published()
    candidate = description_from_draft(
        nb._draft_base, nb.draft_answers, channel_counts=nb._channel_counts()
    )
    found = {
        (i.code.value, i.kind, i.old_id) for i in transition_issues(state.description, candidate)
    }
    assert found == {
        ("R_CONFIRMED_ID_CHANGED", "channels", "ACTIN"),
        ("R_CONFIRMED_ID_CHANGED", "channels", "DNA"),
    }  # no R_CONFIRMED_ON_CHANGED: the repeat was reopened
    records = tuple(Reconfirmation("channels", c, None, today) for c in ("ACTIN", "DNA"))
    result = publish_description(exp[2], candidate, base_sha256=state.sha256, confirmations=records)
    assert result.sha256
    published = (exp[2] / "morphobricks.toml").read_text(encoding="utf-8")
    assert re.search(rf'\[repeats\.{rid}\]\nconfirmed = true\nconfirmed_on = "{today}"', published)
    assert re.search(
        rf'\[repeats\.{other}\]\nconfirmed = true\nconfirmed_on = "{SAVED_ON}"', published
    )


def test_c_a_date_publication_would_refuse_never_reaches_a_save_click(exp, monkeypatch):  # noqa: F811
    # The pre-save check ran no transition check, so Save was enabled for a candidate that
    # publication then refused (R_CONFIRMED_ON_CHANGED). Here rep1's layout is as saved but its
    # date is another day (as an older draft could hold it): Save is disabled, with the reason.
    nb = _saved_long_ago(exp, monkeypatch, ["DAPI", "Actin", "Ch3"])
    answers = nb.draft_answers
    moved = replace(answers.repeats["rep1"], confirmed_on="2026-11-09")
    assert nb._answer(replace(answers, repeats={**answers.repeats, "rep1": moved}))
    nb._refresh_check_save()
    assert {i.code for i in nb._check.refusals} == {"R_CONFIRMED_ON_CHANGED"}
    assert nb.save_button.disabled and not nb.save_experiment()
    shown = _text(nb.checks.value)
    assert "The description would be refused" in shown
    assert "R_CONFIRMED_ON_CHANGED at repeats.rep1" in shown
    assert "Answer “same way?” of rep1 again" in shown  # the way out, said
    assert nb._replicate_fold.count == 1  # rep1's box, with its question, is not folded away
    assert _published_dates(exp) == [SAVED_ON, SAVED_ON]  # nothing written
    # The way out the reason names: "same way?" answered again takes the saved date back
    # (section 10: copied from the published file, never from the clock), on the user's click.
    nb.same_way["rep1"].button.click()  # Undo
    assert nb.draft_answers.repeats["rep1"].confirmed_on is None
    nb.same_way["rep1"].button.click()  # Yes, the same way
    assert nb.draft_answers.repeats["rep1"].confirmed_on == SAVED_ON
    assert not nb.save_button.disabled


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
    assert "the run will stop" in note and "Refused:" in note
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
    # Regression: an unchanged reopen said "Saved ..., The run can start." while the
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
    assert "The run can start." in nb.save_status.value
