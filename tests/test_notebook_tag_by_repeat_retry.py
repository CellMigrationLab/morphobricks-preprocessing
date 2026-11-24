"""Tag by repeat: retrying a refused review save never overwrites a confirmation (PR #376).

External review of 23 November, blocker A: ``TagGrid.commit`` retried an earlier refused review
write inside the confirmation's own save, before the confirmation was in memory, so it wrote the
review without it over the one just saved. The draft lost the confirmation while the session
counted the unit, and the save was open.
"""

from __future__ import annotations

import pytest

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")

from morphobricks_preprocessing.description_file import DRAFT_FILENAME  # noqa: E402
from morphobricks_preprocessing.draft_format import answers_from_bytes  # noqa: E402
from test_notebook_check_save import answer_all  # noqa: E402
from test_notebook_tag_by_repeat_lapses import _confirmed_on, _writes  # noqa: E402
from test_notebook_tag_by_repeat_oneclick import GONE, OTHER, _confirm, _strict  # noqa: E402


def test_a_confirmation_after_a_refused_review_write_stays_in_the_draft(
    tmp_path, statement_off, monkeypatch
):
    nb, _, rows = _strict(tmp_path, statement_off)
    out = tmp_path / "out"
    _confirm(nb, rows, GONE, "override", None)
    rows.unsaved = True  # an earlier write of the review was refused
    written = _writes(nb, monkeypatch)
    assert rows.override.confirm_same(OTHER)
    assert len(written) == 1 and _confirmed_on(written[0], OTHER)  # at e010834: 2 writes
    saved = answers_from_bytes((out / DRAFT_FILENAME).read_bytes()).tag_review
    assert set(saved.confirmed) == {GONE, OTHER}  # at e010834: OTHER's confirmation lost
    assert saved == rows._review() and not rows.unsaved
    answer_all(nb)
    assert not rows.listed() and not nb.save_button.disabled
