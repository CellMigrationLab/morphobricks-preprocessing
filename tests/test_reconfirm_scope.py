"""A removal or rename answer re-confirms only the change it answers (issue #279).

Experiment-v1 4.6 keys a re-confirmation by ID (old ID, new ID, date), never by folder: the
"Yes, remove" of one folder records every channel ID that folder held, and that record would
accept any other change of the same ID, such as a saved header name lost in another folder that
is still there and that the user never saw. Such an ID stays refused (``R_CONFIRMED_ID_CHANGED``)
and the reason is said beside the Save button; a header name held only by the removed folder is
still the removal's.
"""

import json
import shutil

import pytest

from channel_images import write_channels
from morphobricks_preprocessing.description_file import RECORDS_FILENAME
from test_notebook_check_save import KINDS, answer_all, exp  # noqa: F401

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")

from test_notebook_axes_quick import stepper  # noqa: E402
from test_notebook_prerelease import _tag, _text  # noqa: E402

GONE = "drug/rep2"


def _reopened(exp, unique, now):  # noqa: F811
    """Save with ``unique`` as ctrl/rep1's DNA header name, then rewrite it to ``now``; drop B."""
    write_channels(exp[0] / "ctrl/rep1/a.tif", [unique, "Actin", "Ch3"], KINDS, seed=1)
    first = stepper(*exp)
    first.quick_confirm.click()
    answer_all(first)
    assert first.save_experiment()
    assert first.draft_answers.channels["DNA"].header_names == ("DAPI", unique)
    write_channels(exp[0] / "ctrl/rep1/a.tif", [now, "Actin", "Ch3"], KINDS, seed=1)
    shutil.rmtree(exp[0] / GONE)
    shutil.rmtree(exp[1] / "Nuclei" / GONE)
    nb = stepper(*exp)
    nb.quick_confirm.click()
    return nb


def _remove(nb):
    answer_all(nb)
    (row,) = nb.removals_box.children
    row.children[1].click()  # Yes, remove (drug/rep2 holds DNA too)
    nb._refresh_check_save()


def _records(exp):  # noqa: F811
    path = exp[2] / RECORDS_FILENAME
    return [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []


@pytest.mark.parametrize("now", ["Nuc", None], ids=["other-name", "no-name"])
def test_a_removal_does_not_accept_a_header_name_lost_in_a_folder_still_there(exp, now):  # noqa: F811
    nb = _reopened(exp, "Hoechst", now)
    before = len(_records(exp))
    _tag(nb, 0, "DNA")  # re-tag ctrl/rep1's channel 1: Hoechst is no longer a name of DNA
    assert "Hoechst" not in (nb.draft_answers.channels["DNA"].header_names or ())
    _remove(nb)
    assert nb.save_button.disabled and not nb.save_experiment()
    assert "R_CONFIRMED_ID_CHANGED at channels.DNA" in _text(nb.checks.value)
    assert (
        "DNA lost a saved header name in ctrl/rep1, which is still there: a removal or rename "
        "does not confirm it (undo that tag on Channels)"
    ) in _text(nb.save_blocker.value)
    assert len(_records(exp)) == before  # nothing published, nothing recorded


def test_without_the_tag_change_the_removal_saves(exp):  # noqa: F811
    # The way out the reason names: DNA keeps its saved names, so only the removal changes it
    # (the run then stops on the header itself, R_HEADER_CONTRADICTION, as without a removal).
    nb = _reopened(exp, "Hoechst", "Nuc")
    _remove(nb)
    assert not nb.save_button.disabled
    assert nb.save_experiment(), _text(nb.save_status.value)
    kept = {r["old_id"] for r in _records(exp) if r["type"] == "reconfirmation"}
    assert {"ACTIN", "DNA", "PHASE"} <= kept


def test_a_header_name_held_only_by_the_removed_folder_is_the_removals(exp):  # noqa: F811
    # drug/rep2 alone named DNA "Hoechst"; with it removed, a re-tag drops that name. Every
    # folder still there gives a saved name, so the removal answer covers the lost name.
    write_channels(exp[0] / GONE / "a.tif", ["Hoechst", "Actin", "Ch3"], KINDS, seed=4)
    first = stepper(*exp)
    first.quick_confirm.click()
    answer_all(first)
    assert first.save_experiment()
    shutil.rmtree(exp[0] / GONE)
    shutil.rmtree(exp[1] / "Nuclei" / GONE)
    nb = stepper(*exp)
    nb.quick_confirm.click()
    _tag(nb, 0, "DNA")
    assert nb.draft_answers.channels["DNA"].header_names == ("DAPI",)
    _remove(nb)
    assert not nb.save_button.disabled, _text(nb.save_blocker.value)
    assert nb.save_experiment(), _text(nb.save_status.value)
    kept = [r["old_id"] for r in _records(exp) if r["type"] == "reconfirmation"]
    assert kept.count("DNA") == 1
