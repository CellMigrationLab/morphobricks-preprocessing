"""Fast mode as the writers write it (Spec ADR 0006): the mode kept, new units, FC1 (P38, P40).

Experiment-v1 section 10 (switching modes is a user action; new units under a fast-mode file
are drafted ``experiment`` with the list materialised) and conformance plan P38 (c), (d) and
P40 on the draft, publication and the command line. The notebook route of P40 is
``tests/test_notebook_fast_statement.py``.
"""

from __future__ import annotations

import pytest

from morphobricks_preprocessing.description import description_from_bytes
from morphobricks_preprocessing.description_file import DESCRIPTION_FILENAME
from morphobricks_preprocessing.description_write import canonical_bytes, section_digests
from morphobricks_preprocessing.discovery import discover_experiment
from morphobricks_preprocessing.metadata import inspect_images
from morphobricks_preprocessing.physical_check import check_physical
from morphobricks_preprocessing.run.description_file import publish_description
from test_spec_fast_layout import FAST, MATCH, SPEC, TRANSITIONS, V41, V43, _fast, _ome

# --- Section 10: the mode is kept on reopening, and switched only by the user ----------------


def _drafted(root, published):
    from morphobricks_preprocessing.draft import draft_from_discovery
    from morphobricks_preprocessing.published_answers import retired_ids

    return draft_from_discovery(discover_experiment(root), used_ids=retired_ids(published))


@pytest.mark.parametrize("number", ["v41", "v42", "v43", "v01"])
def test_10_a_published_file_reopens_in_its_own_mode_and_round_trips(tmp_path, number):
    """Review of #362, item 1: published -> answers -> draft file -> description is the file."""
    from morphobricks_preprocessing.draft_answers import description_from_draft
    from morphobricks_preprocessing.draft_format import answers_from_bytes, answers_to_bytes
    from morphobricks_preprocessing.published_answers import published_answers
    from morphobricks_preprocessing.testing_described import write_described_tree

    published = description_from_bytes(SPEC[number].data)
    root = tmp_path / "exp"
    write_described_tree(root, None, published)
    answers = answers_from_bytes(answers_to_bytes(published_answers(published, "0" * 64)))
    assert (answers.channel_layout is None) == (number == "v01")
    again = description_from_draft(_drafted(root, published), answers)
    assert again == published
    assert canonical_bytes(again) == canonical_bytes(published)


def _publish(out, data):
    out.mkdir(parents=True, exist_ok=True)
    publish_description(out, description_from_bytes(data), base_sha256="absent")
    return (out / DESCRIPTION_FILENAME).read_bytes()


def _strict_of(description):
    from dataclasses import replace

    repeats = {
        rid: replace(
            repeat,
            members={
                cid: replace(
                    member,
                    technical_repeats={
                        tid: replace(unit, channels_from=None)
                        for tid, unit in member.technical_repeats.items()
                    },
                )
                for cid, member in repeat.members.items()
            },
        )
        for rid, repeat in description.repeats.items()
    }
    return replace(description, channel_layout=None, repeats=repeats)


def test_10_an_implicit_switch_from_fast_to_strict_is_refused_and_writes_nothing(tmp_path):
    from morphobricks_preprocessing.description_file import sha256_hex
    from morphobricks_preprocessing.run.description_file import (
        MODE_SWITCH_UNCONFIRMED,
        ModeSwitchRefused,
    )

    out = tmp_path / "out"
    before = _publish(out, V41)
    strict = _strict_of(description_from_bytes(V41))
    assert section_digests(strict) == section_digests(description_from_bytes(V41))
    with pytest.raises(ModeSwitchRefused) as refused:
        publish_description(out, strict, base_sha256=sha256_hex(before))
    assert refused.value.code == MODE_SWITCH_UNCONFIRMED
    assert (out / DESCRIPTION_FILENAME).read_bytes() == before
    publish_description(out, strict, base_sha256=sha256_hex(before), switch_mode=True)
    assert description_from_bytes((out / DESCRIPTION_FILENAME).read_bytes()).channel_layout is None


def test_10_p40d_adopting_fast_mode_on_v01_is_t16_only_as_the_users_switch(tmp_path):
    from morphobricks_preprocessing.description_file import sha256_hex
    from morphobricks_preprocessing.run.description_file import ModeSwitchRefused

    out = tmp_path / "out"
    before = _publish(out, SPEC["v01"].data)
    t16 = description_from_bytes(TRANSITIONS["t16"].data)
    with pytest.raises(ModeSwitchRefused):
        publish_description(out, t16, base_sha256=sha256_hex(before))
    with pytest.raises(ModeSwitchRefused):  # a recorded switch that switches nothing
        publish_description(
            out,
            description_from_bytes(SPEC["v01"].data),
            base_sha256=sha256_hex(before),
            switch_mode=True,
        )
    publish_description(out, t16, base_sha256=sha256_hex(before), switch_mode=True)
    assert description_from_bytes((out / DESCRIPTION_FILENAME).read_bytes()) == t16


def test_10_p40c_a_strict_file_reopened_with_unchanged_folders_stays_strict(tmp_path):
    from morphobricks_preprocessing.draft_answers import description_from_draft
    from morphobricks_preprocessing.published_answers import published_answers
    from morphobricks_preprocessing.testing_described import write_described_tree

    published = description_from_bytes(SPEC["v01"].data)
    write_described_tree(tmp_path / "exp", None, published)
    answers = published_answers(published, "0" * 64)
    assert answers.channel_layout is None and not answers.switch_mode
    again = description_from_draft(_drafted(tmp_path / "exp", published), answers)
    assert not again.fast_mode
    assert b"channels_from" not in canonical_bytes(again)


def test_a_strict_draft_with_a_marker_is_refused(tmp_path):
    """No marker without a statement: never a silent half of fast mode (E_FAST_MARKER_...)."""
    from dataclasses import replace

    from morphobricks_preprocessing.draft import DraftError
    from morphobricks_preprocessing.draft_answers import apply_answers, mode_problems
    from morphobricks_preprocessing.published_answers import published_answers
    from morphobricks_preprocessing.testing_described import write_described_tree

    published = description_from_bytes(V41)
    write_described_tree(tmp_path / "exp", None, published)
    strict = replace(published_answers(published, "0" * 64), channel_layout=None)
    assert mode_problems(strict)
    with pytest.raises(DraftError, match="channels_from"):
        apply_answers(_drafted(tmp_path / "exp", published), strict)


# --- P38 (c), (d): new units under a fast file ----------------------------------------------


def _appended(tmp_path, *, three_channels=False):
    from morphobricks_preprocessing.draft_answers import RepeatAnswer
    from morphobricks_preprocessing.published_answers import published_answers
    from morphobricks_preprocessing.testing_described import write_described_tree

    published = description_from_bytes(V41)
    t19 = description_from_bytes(TRANSITIONS["t19"].data)
    root = tmp_path / "exp"
    write_described_tree(root, None, t19)
    if three_channels:
        _ome(root / "DrugA/rep3/FOV099.ome.tif", [*MATCH, "Cy5"], 3)
    answers = published_answers(published, "0" * 64)
    from dataclasses import replace

    repeats = {**answers.repeats, "rep3": RepeatAnswer("rep3", "2026-11-17")}
    return root, published, replace(answers, repeats=repeats)


def test_p38c_new_units_under_a_fast_file_are_drafted_with_the_materialised_list(tmp_path):
    from morphobricks_preprocessing.draft_answers import description_from_draft

    root, published, answers = _appended(tmp_path)
    assert ("CTRL", "rep3", "single") not in answers.units  # new: not answered
    candidate = description_from_draft(_drafted(root, published), answers)
    for condition in ("CTRL", "DrugA"):
        unit = candidate.repeats["rep3"].members[condition].technical_repeats["single"]
        assert unit.channels_from == "experiment" and unit.channels == ("DNA", "ACTIN")
    assert canonical_bytes(candidate) == canonical_bytes(
        description_from_bytes(TRANSITIONS["t19"].data)
    )


def test_p38d_a_contradicting_new_unit_is_refused_never_switched_to_unit(tmp_path):
    from morphobricks_preprocessing.draft_answers import description_from_draft

    root, published, answers = _appended(tmp_path, three_channels=True)
    candidate = description_from_draft(_drafted(root, published), answers)
    unit = candidate.repeats["rep3"].members["DrugA"].technical_repeats["single"]
    assert unit.channels_from == "experiment"  # the draft never sets "unit" itself
    discovery = discover_experiment(root)
    report = check_physical(candidate, discovery, inspect_images(root, discovery.images).files)
    assert report.codes == {FAST}
    assert set(_fast(report)) == {"DrugA/rep3/FOV099.ome.tif"}


def test_the_draft_file_keeps_the_statement_markers_and_switch():
    from dataclasses import replace

    from morphobricks_preprocessing.draft_format import answers_from_bytes, answers_to_bytes
    from morphobricks_preprocessing.published_answers import published_answers

    answers = replace(published_answers(description_from_bytes(V43), "0" * 64), switch_mode=True)
    data = answers_to_bytes(answers)
    assert answers_from_bytes(data) == answers
    assert b'"channels_from": "experiment"' in data and b'"switch_mode": true' in data
    with pytest.raises(ValueError, match="channels_from"):
        answers_from_bytes(data.replace(b'"channels_from": "experiment"', b'"channels_from": "x"'))


# --- P40: fast mode is the writer's default (FC1) ---------------------------------------------


def _plain_tree(root, names=MATCH):
    for condition in ("CTRL", "DrugA"):
        for repeat in ("rep1", "rep2"):
            for index in range(2):
                _ome(root / condition / repeat / f"FOV{index:03d}.ome.tif", names)
    return root


FAST_ARGS = [
    "--channels",
    "DNA,ACTIN",
    "--image-type",
    "channels",
    "--accept-ids",
    "--comparable",
    "--confirmed-on",
    "2026-11-18",
    "--publish",
]


def test_p40a_the_one_command_describe_path_publishes_fast_mode(tmp_path, capsys):
    from morphobricks_preprocessing.cli import main

    root, out = _plain_tree(tmp_path / "exp"), tmp_path / "out"
    assert main(["describe", str(root), "--output", str(out), *FAST_ARGS]) == 0
    published = description_from_bytes((out / DESCRIPTION_FILENAME).read_bytes())
    layout = published.channel_layout
    assert (layout.channels, layout.image_type, layout.confirmed_on) == (
        ("DNA", "ACTIN"),
        None,
        "2026-11-18",  # the day the user gave, never the clock's
    )
    assert published.channels["DNA"].header_names == ("DAPI",)  # the tagged unit's names
    members = [m for r in published.repeats.values() for m in r.members.values()]
    units = [u for m in members for u in m.technical_repeats.values()]
    assert {u.channels_from for u in units} == {"experiment"}
    assert "tagged unit CTRL / rep1 / single" in capsys.readouterr().out
    assert (out / "experiment_design.csv").exists()


def test_p40a_the_one_command_path_refuses_contradictions_and_writes_nothing(tmp_path, capsys):
    from morphobricks_preprocessing.cli import main

    root, out = _plain_tree(tmp_path / "exp"), tmp_path / "out"
    _ome(root / "DrugA/rep2/FOV009.ome.tif", ["Phalloidin-488", "DAPI"])
    assert main(["describe", str(root), "--output", str(out), *FAST_ARGS]) == 1
    text = capsys.readouterr().out
    assert "contradict the experiment channel list" in text
    assert "DrugA/rep2/FOV009.ome.tif" in text and 'channels_from = "unit"' in text
    assert not (out / DESCRIPTION_FILENAME).exists()


def test_p40a_the_one_command_path_confirms_nothing_for_the_user(tmp_path, capsys):
    from morphobricks_preprocessing.cli import main

    root, out = _plain_tree(tmp_path / "exp"), tmp_path / "out"
    partial = [a for a in FAST_ARGS if a not in ("--comparable", "--accept-ids")]
    assert main(["describe", str(root), "--output", str(out), *partial]) == 1
    text = capsys.readouterr().out
    assert "--comparable" in text and "--accept-ids" in text
    assert not out.exists()


def test_p40b_strict_mode_chosen_on_the_command_line_publishes_no_fast_file(tmp_path, capsys):
    from morphobricks_preprocessing.cli import main

    root, out = _plain_tree(tmp_path / "exp"), tmp_path / "out"
    assert main(["describe", str(root), "--output", str(out), "--strict", *FAST_ARGS]) == 1
    assert not (out / DESCRIPTION_FILENAME).exists()  # strict answers are the notebook's


def test_a_draft_file_of_version_3_is_refused_as_an_unknown_version():
    """Draft file version 4 (ADR 0006): a version 3 draft has no fast-mode statement."""
    import json

    from morphobricks_preprocessing.draft_format import (
        DraftFileError,
        answers_from_bytes,
        answers_to_bytes,
    )
    from morphobricks_preprocessing.published_answers import published_answers

    doc = json.loads(answers_to_bytes(published_answers(description_from_bytes(V41), "0" * 64)))
    doc["version"] = 3
    del doc["channel_layout"], doc["switch_mode"]
    with pytest.raises(DraftFileError) as refused:
        answers_from_bytes(json.dumps(doc).encode("utf-8"))
    assert refused.value.code == "unknown_version"


def test_p40a_an_image_whose_header_cannot_be_read_is_never_published(tmp_path, capsys):
    """Re-review of #362: inspect_images' issues refuse the one-command path."""
    from morphobricks_preprocessing.cli import main

    root, out = _plain_tree(tmp_path / "exp"), tmp_path / "out"
    (root / "DrugA/rep2/FOV009.ome.tif").write_bytes(b"not a TIFF file")
    assert main(["describe", str(root), "--output", str(out), *FAST_ARGS]) == 1
    assert "FOV009.ome.tif" in capsys.readouterr().out
    assert not (out / DESCRIPTION_FILENAME).exists()


def test_p40a_images_of_unknown_channel_count_are_never_published(tmp_path, capsys):
    """Re-review of #362: plain TIFFs whose planes are not named are unchecked, so refused."""
    import numpy as np
    import tifffile

    from morphobricks_preprocessing.cli import main

    root, out = tmp_path / "exp", tmp_path / "out"
    for condition, planes in (("CTRL", 2), ("DrugA", 5)):
        for repeat in ("rep1", "rep2"):
            path = root / condition / repeat / "FOV000.tif"
            path.parent.mkdir(parents=True)
            data = np.zeros((planes, 8, 8), np.uint8)
            tifffile.imwrite(path, data, photometric="minisblack", metadata=None)
    assert main(["describe", str(root), "--output", str(out), *FAST_ARGS]) == 1
    text = capsys.readouterr().out
    assert "could not be checked" in text and "DrugA/rep1/FOV000.tif" in text
    assert not (out / DESCRIPTION_FILENAME).exists()
