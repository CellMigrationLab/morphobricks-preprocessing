"""Labels, look-alikes, tags and the draft file's tagging states (Stage 1, PR 15)."""

from dataclasses import replace

import pytest

from draft_file_support import UNITS, complete
from morphobricks_preprocessing import discover_experiment
from morphobricks_preprocessing.channel_answers import (
    synced,
    with_labels,
    with_tags,
    with_type,
)
from morphobricks_preprocessing.channel_labels import (
    UnitLayout,
    aliases,
    is_generic,
    label_problems,
    look_alike,
    proposal_for,
    propose_labels,
    remaining,
    usage,
)
from morphobricks_preprocessing.draft import draft_from_discovery
from morphobricks_preprocessing.draft_answers import (
    DraftAnswers,
    DraftIncomplete,
    UnitAnswer,
    carry_over,
    description_from_draft,
    missing_confirmations,
)
from morphobricks_preprocessing.draft_format import (
    ABSENT,
    SPEC_COMMIT,
    DraftFileError,
    answers_from_bytes,
    answers_to_bytes,
)
from tiff_tree import make_tree


def layout(key=("a", "r1", "single"), names=("DAPI", "Actin"), rgb=False, n=None):
    return UnitLayout(key, "x.tif", n or len(names), tuple(names), rgb, "", 1)


@pytest.mark.parametrize(
    "name",
    [
        "Ch1",
        "C0",
        "c=0",
        "Channel 2",
        "channel_3",
        "AF488",
        "Alexa Fluor 568",
        "Cy5",
        "DAPI",
        "Hoechst 33342",
        "FITC",
        "EGFP",
        "mCherry",
        "488",
        "561 nm",
        "Track 1",
        "Detector 2",
    ],
)
def test_generic_header_names_are_recognised(name):
    assert is_generic(name)


@pytest.mark.parametrize("name", ["Actin", "Tubulin", "LaminB1", "PHASE", "Nucleus", "Golgi"])
def test_stain_names_are_not_generic(name):
    assert not is_generic(name)


def test_proposals_leave_out_generic_and_invalid_names_and_never_repair():
    found = propose_labels(["DAPI", "Ch1", "AF488", "Actin", " Tubulin", None, "Actin"])
    assert found.single == ("Actin",)
    assert dict(found.skipped) == {
        "DAPI": "generic",
        "Ch1": "generic",
        "AF488": "generic",
        " Tubulin": "not_valid",
    }


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("DAPI", "Dapi"),
        ("Lamin B", "LaminB"),
        ("Lamin-B", "Lamin_B"),
        ("Actin", "Actim"),
        ("Actin", "Actn"),
    ],
)
def test_look_alikes(a, b):
    assert look_alike(a, b)


@pytest.mark.parametrize(("a", "b"), [("Actin", "Tubulin"), ("A", "B"), ("Actin", "Actin")])
def test_different_names_are_not_look_alikes(a, b):
    assert not look_alike(a, b)


def test_look_alikes_come_back_grouped_and_are_not_offered_one_by_one():
    found = propose_labels(["Lamin", "LaminB", "Actin", "Actin "])
    assert sorted(found.groups) == [("Actin", "Actin "), ("Lamin", "LaminB")]
    assert found.single == ()


def test_label_problems_refuse_a_case_collision_on_both_entries():
    out = label_problems(["Actin", "ACTIN", "DNA", "r"])
    assert out[0] == out[1] == ("E_ID_CASE_COLLISION",)
    assert 2 not in out
    assert "E_RGB_TOKEN_MISUSE" in out[3]
    assert "E_DUPLICATE_VALUE" in label_problems(["DNA", "DNA"])[0]


def test_a_header_proposal_needs_every_channel_to_equal_a_label_exactly():
    labels = ["Actin", "DNA"]
    assert proposal_for(layout(names=("DNA", "Actin")), labels) == ("DNA", "Actin")
    assert proposal_for(layout(names=("DNA", "actin")), labels) is None  # no case folding
    assert proposal_for(layout(names=("DNA", None)), labels) is None
    assert proposal_for(layout(names=("DNA", "DNA")), labels) is None
    assert proposal_for(layout(names=("R", "G", "B"), rgb=True), ["R", "G", "B"]) is None


def test_usage_and_aliases_come_from_the_saved_tags_only():
    lay = [layout(("a", "r1", "single")), layout(("a", "r2", "single"), names=("DAPI", None))]
    units = {
        ("a", "r1", "single"): UnitAnswer(("DNA", "ACTIN"), "channels"),
        ("a", "r2", "single"): UnitAnswer(("DNA", None), "channels"),
    }
    assert usage(lay, units, ["DNA", "ACTIN"]) == {
        "DNA": (("a", "r1", "single"), ("a", "r2", "single")),
        "ACTIN": (("a", "r1", "single"),),
    }
    assert aliases(lay, units) == {"DNA": ("DAPI",), "ACTIN": ("Actin",)}


def test_remaining_counts_untagged_cells_and_unanswered_types():
    lay = [layout()]
    assert remaining(lay, {}, ["DNA"]).cells == 2
    half = {lay[0].key: UnitAnswer(("DNA", None), None)}
    left = remaining(lay, half, ["DNA"])
    assert (left.cells, left.types, left.done) == (1, 1, False)
    full = {lay[0].key: UnitAnswer(("DNA", "unused"), "channels")}
    assert remaining(lay, full, ["DNA", "unused"]).done  # an unused slot has a label too
    assert not remaining(lay, {lay[0].key: UnitAnswer(("GONE", "DNA"), "channels")}, ["DNA"]).done


def test_tags_labels_and_types_change_one_slice_and_keep_the_rest():
    lay = layout()
    base = DraftAnswers(ABSENT, SPEC_COMMIT)
    tagged = with_tags(with_labels(base, {"DNA": "DAPI", "ACTIN": None}), lay, ["DNA", None])
    assert tagged.units[lay.key] == UnitAnswer(("DNA", None))
    assert tagged.channels["DNA"].display_name == "DAPI"
    assert synced(tagged, [lay]).channels["DNA"].header_names == ("DAPI",)
    # a label that is gone takes its tags back, never moves them to another label
    gone = with_labels(tagged, {"ACTIN": None})
    assert gone.units[lay.key].channels == (None, None)
    # colour: R, G, B and nothing else, declared once a unit is colour
    rgb = layout(names=(None, None, None), rgb=True)
    coloured = synced(with_type(base, rgb, "rgb"), [rgb])
    assert coloured.units[rgb.key] == UnitAnswer(("R", "G", "B"), "rgb")
    assert {"R", "G", "B"} <= set(coloured.channels)
    with pytest.raises(ValueError, match="3 channels"):
        with_type(base, lay, "rgb")


# --- the draft file ---------------------------------------------------------------------------


def tagged_answers():
    answers = complete()
    units = dict(answers.units)
    key = sorted(units)[0]
    units[key] = UnitAnswer(("DNA", None), "channels")
    units[sorted(units)[1]] = UnitAnswer((None, "ACTIN"), "rgb")
    return replace(answers, units=units)


def test_the_file_holds_untagged_positions_and_the_image_type():
    answers = tagged_answers()
    data = answers_to_bytes(answers)
    assert b"null" in data
    back = answers_from_bytes(data)
    assert back.units == answers.units
    assert answers_to_bytes(back) == data


def test_an_empty_text_tag_is_refused_in_the_file():
    data = answers_to_bytes(complete())
    bad = data.replace(b'"DNA",\n', b'"",\n', 1)
    assert bad != data
    with pytest.raises(DraftFileError, match="channel ID"):
        answers_from_bytes(bad)


def test_the_file_is_version_3_and_older_versions_are_refused():
    data = answers_to_bytes(complete())
    assert b'"version": 3' in data
    with pytest.raises(DraftFileError) as refused:
        answers_from_bytes(data.replace(b'"version": 3', b'"version": 2'))
    assert refused.value.code == "unknown_version"


def test_an_image_type_the_format_does_not_have_is_refused():
    data = answers_to_bytes(complete()).replace(b'"channels"\n', b'"channels"\n')
    answers = complete()
    key = sorted(answers.units)[0]
    odd = replace(
        answers, units={**answers.units, key: UnitAnswer(("DNA", "ACTIN"), "fluorescence")}
    )
    with pytest.raises(DraftFileError, match="image_type"):
        answers_from_bytes(answers_to_bytes(odd))
    assert data


def test_a_tag_that_is_neither_text_nor_null_is_refused():
    data = answers_to_bytes(complete())
    bad = data.replace(b'"DNA",\n', b"7,\n", 1)
    assert bad != data
    with pytest.raises(DraftFileError, match="channel ID"):
        answers_from_bytes(bad)


@pytest.fixture
def exp(tmp_path):
    from draft_file_support import PATHS

    make_tree(tmp_path / "exp", PATHS)
    return discover_experiment(tmp_path / "exp")


def test_an_untagged_position_blocks_the_description(exp):
    base = draft_from_discovery(exp)
    answers = complete()
    key = sorted(UNITS)[0]
    for tags, code in ((("DNA", None), "unit_channel_untagged"),):
        units = {**answers.units, key: UnitAnswer(tags)}
        new = replace(answers, units=units)
        codes = {q.code for q in missing_confirmations(base, new)}
        assert code in codes
        with pytest.raises(DraftIncomplete):
            description_from_draft(base, new)


def test_channels_image_type_is_an_ordinary_unit_in_the_description(exp):
    answers = complete()
    key = sorted(UNITS)[0]
    answers = replace(
        answers, units={**answers.units, key: UnitAnswer(("DNA", "ACTIN"), "channels")}
    )
    description = description_from_draft(draft_from_discovery(exp), answers)
    units = [
        u
        for r in description.repeats.values()
        for m in r.members.values()
        for u in m.technical_repeats.values()
    ]
    assert all(u.image_type is None for u in units)


def test_carry_over_keeps_the_tags_of_the_units_that_remain(exp):
    base = draft_from_discovery(exp)
    answers = tagged_answers()
    keep, drop = sorted(answers.units)[0], sorted(answers.units)[-1]
    units = {keep: answers.units[keep], drop: UnitAnswer(("DNA", None), "channels")}
    saved = replace(answers, units=units)
    trimmed = discover_experiment(exp.root)
    gone = tuple(
        leaf
        for leaf in trimmed.leaves
        if (leaf.condition, leaf.biological_repeat, leaf.technical_repeat) != drop
    )
    now = draft_from_discovery(replace(trimmed, leaves=gone))
    kept, dropped = carry_over(now, saved, {})
    assert kept.units == {keep: answers.units[keep]}
    assert any(" / ".join(drop) in line for line in dropped)
    assert base is not None
