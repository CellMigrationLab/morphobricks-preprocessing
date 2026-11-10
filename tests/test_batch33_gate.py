"""Audit batches 33 and 34a, the publication gate: nothing unanswered, stale or doubled is saved.

- B2: a unit whose image type is not answered (None) is an open question
  (``unit_image_type_unanswered``) and is never published as an ordinary channel image, although
  the published file writes "channels" as an absent ``image_type`` (experiment-v1 §4.4b).
- B3: ``publish_draft`` requires the channel count of each unit's image now; tags saved for
  another count (``unit_channels_stale``) or a unit without a count are refused.
- B1: a unit tagging one label at two positions is refused before it is saved
  (``E_DUPLICATE_CHANNEL_IN_UNIT``).
- FILE 1: ``make_described_run`` refuses a member with fewer images than technical repeats
  (negative included) or none, and writes nothing.
- 34a item 5: only the exact tokens R, G, B make a colour unit count for "every folder is colour".
"""

from dataclasses import replace

import pytest

from draft_file_support import COUNTS, UNITS, complete, snapshot
from morphobricks_preprocessing.channel_answers import all_colour
from morphobricks_preprocessing.channel_labels import UnitLayout
from morphobricks_preprocessing.description import Description, description_from_bytes
from morphobricks_preprocessing.description_file import DESCRIPTION_FILENAME, DRAFT_FILENAME
from morphobricks_preprocessing.draft import DraftError, draft_from_discovery
from morphobricks_preprocessing.draft_answers import (
    DraftIncomplete,
    UnitAnswer,
    apply_answers,
    description_from_draft,
    missing_confirmations,
)
from morphobricks_preprocessing.draft_format import answers_from_bytes, answers_to_bytes
from morphobricks_preprocessing.run.draft_file import publish_draft, save_draft
from morphobricks_preprocessing.testing import make_described_run
from spec_pin import fixtures
from test_draft_file import exp, out  # noqa: F401  (the same folders)

VALID = {item.name.split("_")[0]: item for item in fixtures("valid")}
KEY = UNITS[0]


def _with_unit(answers, unit):
    return replace(answers, units={**answers.units, KEY: unit})


def _codes(draft, answers, counts=COUNTS):
    applied = apply_answers(draft, answers)
    return {q.code for q in missing_confirmations(applied, answers, channel_counts=counts)}


# --- B2 -----------------------------------------------------------------------------------------


def test_an_unanswered_image_type_is_an_open_question(exp):  # noqa: F811
    draft = draft_from_discovery(exp)
    unanswered = _with_unit(complete(), UnitAnswer(("DNA", "ACTIN"), None))
    questions = missing_confirmations(apply_answers(draft, unanswered), unanswered)
    assert [(q.code, q.subject) for q in questions] == [
        ("unit_image_type_unanswered", " / ".join(KEY))
    ]
    with pytest.raises(DraftIncomplete) as refusal:
        description_from_draft(draft, unanswered, channel_counts=COUNTS)
    assert refusal.value.codes == ("unit_image_type_unanswered",)


def test_only_an_explicit_type_answers_it_and_channels_is_published_absent(exp):  # noqa: F811
    draft = draft_from_discovery(exp)
    assert not _codes(draft, complete())  # "channels" answers it
    described = description_from_draft(draft, complete(), channel_counts=COUNTS)
    members = [m for r in described.repeats.values() for m in r.members.values()]
    assert all(u.image_type is None for m in members for u in m.technical_repeats.values())


def test_publish_refuses_an_unanswered_type_and_writes_nothing(exp, out):  # noqa: F811
    draft = draft_from_discovery(exp)
    save_draft(out, draft, _with_unit(complete(), UnitAnswer(("DNA", "ACTIN"), None)))
    before = snapshot(out)
    with pytest.raises(DraftIncomplete) as refusal:
        publish_draft(out, draft, channel_counts=COUNTS)
    assert refusal.value.codes == ("unit_image_type_unanswered",)
    assert snapshot(out) == before and not (out / DESCRIPTION_FILENAME).exists()


# --- B3 -----------------------------------------------------------------------------------------


def test_tags_saved_for_another_channel_count_are_refused_until_retagged(exp, out):  # noqa: F811
    draft = draft_from_discovery(exp)
    save_draft(out, draft, complete())  # two tags per unit
    before = snapshot(out)
    now = {**COUNTS, KEY: 3}  # the image at the same path now has three channels
    for counts, code in ((now, "unit_channels_stale"), ({}, "unit_channel_count_unknown")):
        with pytest.raises(DraftIncomplete) as refusal:
            publish_draft(out, draft, channel_counts=counts)
        assert code in refusal.value.codes
        assert snapshot(out) == before  # nothing written, the draft kept
    with pytest.raises(TypeError):
        publish_draft(out, draft)  # the current counts are required
    retagged = _with_unit(complete(), UnitAnswer(("DNA", "ACTIN", "DNA2"), "channels"))
    channels = {**retagged.channels, "DNA2": retagged.channels["ACTIN"]}
    save_draft(out, draft, replace(retagged, channels=channels))
    assert publish_draft(out, draft, channel_counts=now).path.exists()


# --- B1 -----------------------------------------------------------------------------------------


def test_a_label_at_two_positions_of_a_unit_is_never_saved(exp, out):  # noqa: F811
    draft = draft_from_discovery(exp)
    doubled = _with_unit(complete(), UnitAnswer(("DNA", "DNA"), "channels"))
    with pytest.raises(DraftError) as refusal:
        apply_answers(draft, doubled)
    assert refusal.value.codes == ("E_DUPLICATE_CHANNEL_IN_UNIT",)
    with pytest.raises(DraftError):
        save_draft(out, draft, doubled)
    assert not (out / DRAFT_FILENAME).exists()


# --- FILE 1 -------------------------------------------------------------------------------------


def _member_count(description: Description, count: int, units: dict | None = None):
    repeat_id = next(iter(description.repeats))
    repeat = description.repeats[repeat_id]
    cid = next(iter(repeat.members))
    member = repeat.members[cid]
    member = replace(
        member,
        image_count=count,
        technical_repeats=member.technical_repeats if units is None else units,
    )
    repeats = {
        **description.repeats,
        repeat_id: replace(repeat, members={**repeat.members, cid: member}),
    }
    return replace(description, repeats=repeats)


@pytest.mark.parametrize(("count", "units"), [(-1, None), (-3, None), (0, None), (2, {})])
def test_make_described_run_refuses_impossible_counts_and_writes_nothing(tmp_path, count, units):
    description = _member_count(description_from_bytes(VALID["v01"].data), count, units)
    with pytest.raises(ValueError, match="technical repeat"):
        make_described_run(tmp_path / "run", description)
    assert not (tmp_path / "run").exists() or not any((tmp_path / "run").rglob("*"))


# --- batch 34a, item 5 -----------------------------------------------------------------------


def test_only_the_exact_colour_tokens_make_every_folder_colour():
    layout = UnitLayout(KEY, "a.tif", 3, (None, None, None), rgb=True)
    assert all_colour([layout], {KEY: UnitAnswer(("R", "G", "B"), "rgb")})
    for tags in (("G", "R", "B"), ("R", "G", "G"), ("DNA", "G", "B"), (None, None, None)):
        assert not all_colour([layout], {KEY: UnitAnswer(tags, "rgb")})


def test_a_malformed_resumed_draft_does_not_make_the_stains_not_applicable():
    answers = _with_unit(complete(), UnitAnswer(("R", "G", "B"), "rgb"))
    raw = b'"R",\n            "G",\n            "B"'
    doc = answers_to_bytes(answers).replace(
        raw, raw.replace(b'"R"', b'"X"').replace(b'"B"', b'"R"').replace(b'"X"', b'"B"')
    )
    resumed = answers_from_bytes(doc)  # the file format reads it; the tokens are wrong
    assert resumed.units[KEY].channels == ("B", "G", "R")
    layout = UnitLayout(KEY, "a.tif", 3, (None, None, None), rgb=True)
    assert not all_colour([layout], {KEY: resumed.units[KEY]})
