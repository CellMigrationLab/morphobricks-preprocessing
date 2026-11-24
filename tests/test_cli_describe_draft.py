"""``describe --publish`` never publishes over the notebook's saved draft (external re-check ay).

External re-check ay (24 November 2026), BLOCKER: in strict mode a repeat-row click whose tags
every header of the repeat contradicts fills no unit and saves only the tag review's refusals
in ``morphobricks.draft.json``. ``describe --publish`` with complete flags then built a new
fast-mode description from the command's flags (the headers satisfy its channel list) and
published it, without reading the draft: the refused units were published without the action
on each unit that alone releases them. It is now refused (``R_DRAFT_EXISTS``) and writes
nothing; the draft is finished and published in the notebook (``publish_draft``, checked
against the generation it read). Without a draft the command publishes as before.
"""

from __future__ import annotations

import pytest

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")

from morphobricks_preprocessing import notebook_check_save  # noqa: E402
from morphobricks_preprocessing.cli import main  # noqa: E402
from morphobricks_preprocessing.description_file import (  # noqa: E402
    DESCRIPTION_FILENAME,
    DRAFT_FILENAME,
)
from morphobricks_preprocessing.design import DESIGN_FILENAME  # noqa: E402
from test_notebook_channels import set_labels  # noqa: E402
from test_notebook_check_save import answer_all, opened  # noqa: E402
from test_notebook_tag_by_repeat import TAGS, UNITS, _experiment, _set_row  # noqa: E402
from test_notebook_tag_by_repeat_atomic import _draft  # noqa: E402

SWAPPED = ("ACTIN", "DNA", "PHASE")
"""The rep2 row's tags: every header names DNA, ACTIN, PHASE in this order (refused)."""

REP2 = tuple(k for k in UNITS if k[1] == "rep2")
FLAGS = [
    "--channels",
    ",".join(TAGS),
    "--image-type",
    "channels",
    "--accept-ids",
    "--comparable",
    "--confirmed-on",
    "2026-11-24",
    "--publish",
]


def _headers(key):
    return list(TAGS)  # every header satisfies the command's channel list


def _listing(folder):
    return {p.name: p.read_bytes() for p in sorted(folder.iterdir()) if p.is_file()}


def _refuse_rep2(nb):
    """Strict mode: the rep2 row fills nothing and saves only the tag review's refusals."""
    rows = nb.channel_grid.by_repeat
    assert not nb.channel_grid.same_channels.value
    _set_row(nb, ("rep2", 3), SWAPPED)
    assert set(REP2) <= set(_draft(nb_output(nb)).tag_review.refused)
    assert not any(nb.draft_answers.units.get(k) for k in REP2)
    return rows


def nb_output(nb):
    return nb._design_folder()


def _describe(root, out, capsys):
    code = main(["describe", str(root), "--output", str(out), *FLAGS])
    return code, capsys.readouterr().out


def _refused_and_untouched(root, out, capsys):
    before = _listing(out)
    assert (out / DRAFT_FILENAME).exists()
    code, text = _describe(root, out, capsys)
    assert code == 1
    assert "Not published" in text and "R_DRAFT_EXISTS" in text and DRAFT_FILENAME in text
    assert _listing(out) == before  # the draft byte for byte; nothing else written
    assert not (out / DESCRIPTION_FILENAME).exists() and not (out / DESIGN_FILENAME).exists()


def test_a_fresh_output_folder_without_a_draft_is_published_by_the_command(
    tmp_path, capsys, statement_off
):
    root, _masks, _out = _experiment(tmp_path, names=_headers)
    fresh = tmp_path / "fresh"
    code, text = _describe(root, fresh, capsys)
    assert code == 0, text
    assert (fresh / DESCRIPTION_FILENAME).exists() and (fresh / DESIGN_FILENAME).exists()


@pytest.mark.parametrize("state", ["refused", "confirmed"])
def test_describe_publish_is_refused_over_a_saved_tag_review(
    tmp_path, capsys, monkeypatch, statement_off, state
):
    root, masks, out = _experiment(tmp_path, names=_headers)
    nb = opened(root, masks, out)
    set_labels(nb, list(TAGS))
    rows = _refuse_rep2(nb)
    if state == "confirmed":  # the action on each unit: confirmed, still in the active review
        for key in REP2:
            rows.override.buttons[key].click()
            rows.override.confirm_button.click()
        assert set(REP2) <= set(_draft(out).tag_review.confirmed)
    _refused_and_untouched(root, out, capsys)
    # The notebook's own route: every refused unit released by its action, then published
    # through publish_draft with the generation this notebook read or wrote.
    if state == "refused":
        for key in REP2:
            rows.override.buttons[key].click()
            rows.override.confirm_button.click()
    expected = []
    real = notebook_check_save.publish_draft

    def spy(*args, **kwargs):
        expected.append(kwargs.get("expected"))
        return real(*args, **kwargs)

    monkeypatch.setattr(notebook_check_save, "publish_draft", spy)
    _set_row(nb, ("rep1", 3), TAGS)
    answer_all(nb)
    assert nb.save_experiment()
    assert expected and expected[-1] is not None  # generation-checked publication
    assert (out / DESCRIPTION_FILENAME).exists() and not (out / DRAFT_FILENAME).exists()


def test_describe_publish_is_refused_over_a_draft_a_second_notebook_holds_stale(
    tmp_path, capsys, statement_off
):
    exp = _experiment(tmp_path, names=_headers)
    root, _masks, out = exp
    a = opened(*exp)
    set_labels(a, list(TAGS))
    b = opened(*exp)  # reads the draft before A's refusal: stale from now on
    _refuse_rep2(a)
    saved = (out / DRAFT_FILENAME).read_bytes()
    _refused_and_untouched(root, out, capsys)
    _set_row(b, ("rep1", 3), TAGS)  # B's stale save is refused too (R_DRAFT_CHANGED)
    assert (out / DRAFT_FILENAME).read_bytes() == saved
    assert not (out / DESCRIPTION_FILENAME).exists()
