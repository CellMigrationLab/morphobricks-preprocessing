"""The canonical writer, the round trip and the section digests on the Spec fixtures.

Experiment-v1 section 9: every valid and transition file survives ``parse(write(parse(F)))``,
the writer is idempotent and its bytes follow section 9.1, ``v18`` means what ``v01`` means,
and ``v16`` and ``v21`` have the four section digests of ``v01``.
"""

from __future__ import annotations

import re
import tomllib

import pytest

from morphobricks_preprocessing.description import (
    Description,
    DescriptionError,
    description_from_bytes,
)
from morphobricks_preprocessing.description_read import read_description_bytes
from morphobricks_preprocessing.description_write import (
    SECTIONS,
    canonical_bytes,
    section_digests,
    toml_key,
    toml_string,
)
from spec_pin import fixtures

VALID = {item.name.split("_")[0]: item for item in fixtures("valid")}
TRANSITIONS = fixtures("transitions")
ROUND_TRIP = [*fixtures("valid"), *TRANSITIONS]


def _model(name: str) -> Description:
    return description_from_bytes(VALID[name].data)


def _meaning(doc: dict) -> dict:
    """The parsed file with unordered arrays sorted and an empty optional table dropped."""

    def walk(value, key=None):
        if isinstance(value, dict):
            return {k: walk(v, k) for k, v in value.items()}
        if isinstance(value, list) and key in {"header_names", "source_channels", *doc["used_ids"]}:
            return sorted(value)
        return value

    out = walk(doc)
    if out.get("masks") == {}:
        del out["masks"]
    return out


@pytest.mark.parametrize("item", ROUND_TRIP, ids=lambda item: item.name)
def test_parse_of_write_of_parse_is_parse(item):
    first = description_from_bytes(item.data)
    written = canonical_bytes(first)
    assert description_from_bytes(written) == first
    assert _meaning(tomllib.loads(written.decode("utf-8"))) == _meaning(
        tomllib.loads(item.data.decode("utf-8"))
    )


@pytest.mark.parametrize("item", ROUND_TRIP, ids=lambda item: item.name)
def test_the_writer_is_idempotent_and_its_bytes_are_valid(item):
    written = canonical_bytes(description_from_bytes(item.data))
    assert canonical_bytes(description_from_bytes(written)) == written
    report = read_description_bytes(written)
    assert report.errors == ()
    original = read_description_bytes(item.data)
    assert report.warning_codes == original.warning_codes


@pytest.mark.parametrize("item", ROUND_TRIP, ids=lambda item: item.name)
def test_the_bytes_follow_the_layout_of_section_9_1(item):
    written = canonical_bytes(description_from_bytes(item.data))
    text = written.decode("utf-8")
    assert not written.startswith(b"\xef\xbb\xbf")
    assert b"\r" not in written and text.endswith("\n") and not text.endswith("\n\n")
    assert not [line for line in text.splitlines() if line.lstrip().startswith("#")]
    heads = re.findall(r"^\[([^\]]+)\]$", text, flags=re.MULTILINE)
    assert heads[-1] == "used_ids"
    first = text.splitlines()[:2]
    assert first == ['schema = "morphobricks-experiment"', "schema_version = 1"]
    order = [head.split(".")[0] for head in heads]
    assert order == sorted(
        order, key=["conditions", "channels", "repeats", "masks", "used_ids"].index
    )


def test_v18_is_v01():
    assert _model("v18") == _model("v01")
    assert VALID["v18"].data != VALID["v01"].data  # another spelling of the same file
    assert canonical_bytes(_model("v18")) == canonical_bytes(_model("v01"))


@pytest.mark.parametrize("name", ["v16", "v21"])
def test_a_display_name_target_or_treatment_key_changes_no_digest(name):
    assert _model(name) != _model("v01")
    assert section_digests(_model(name)) == section_digests(_model("v01"))
    assert canonical_bytes(_model(name)) != canonical_bytes(_model("v01"))


def test_the_digests_cover_the_four_sections_and_a_semantic_change_moves_them():
    base = _model("v01")
    digests = section_digests(base)
    assert tuple(digests) == SECTIONS
    assert all(re.fullmatch(r"[0-9a-f]{64}", digest) for digest in digests.values())
    # source_folder is in conditions and repeats; a unit's channels are in repeats only.
    folder = Description(
        {
            k: type(c)(c.source_folder + "x", c.display_name, c.reference)
            for k, c in base.conditions.items()
        },
        base.channels,
        base.repeats,
        base.used_ids,
        base.masks,
    )
    moved = {name for name, digest in section_digests(folder).items() if digest != digests[name]}
    assert moved == {"conditions"}


def test_a_changed_unit_channel_list_changes_the_repeats_digest_only():
    base = _model("v01")
    repeat_id, repeat = next(iter(base.repeats.items()))
    condition, member = next(iter(repeat.members.items()))
    unit_id, unit = next(iter(member.technical_repeats.items()))
    other = type(unit)(unit.source_folder, tuple(reversed(unit.channels)), unit.display_name)
    member2 = type(member)(
        member.source_repeat, member.image_count, {**member.technical_repeats, unit_id: other}
    )
    repeat2 = type(repeat)(
        repeat.confirmed,
        repeat.confirmed_on,
        {**repeat.members, condition: member2},
        repeat.display_name,
    )
    changed = Description(
        base.conditions,
        base.channels,
        {**base.repeats, repeat_id: repeat2},
        base.used_ids,
        base.masks,
    )
    assert len(unit.channels) > 1
    before, after = section_digests(base), section_digests(changed)
    assert {name for name in SECTIONS if before[name] != after[name]} == {"repeats"}


def test_reference_and_used_ids_are_handled_as_section_9_3_says():
    base = _model("v01")
    ref = next(k for k, c in base.conditions.items() if c.reference)
    other = {
        **base.conditions,
        ref: type(base.conditions[ref])(base.conditions[ref].source_folder, None, None),
    }
    changed = Description(other, base.channels, base.repeats, base.used_ids, base.masks)
    after = section_digests(changed)
    before = section_digests(base)
    assert before["channels"] == after["channels"] and before["repeats"] == after["repeats"]
    longer = {**base.used_ids, "channels": (*base.used_ids["channels"], "ZZZ")}
    unused = Description(base.conditions, base.channels, base.repeats, longer, base.masks)
    assert section_digests(unused) == before


def test_confirmed_on_is_written_as_the_model_holds_it():
    base = _model("v01")
    repeat_id, repeat = next(iter(base.repeats.items()))
    stamped = type(repeat)(repeat.confirmed, "1999-12-31", repeat.members, repeat.display_name)
    text = canonical_bytes(
        Description(
            base.conditions,
            base.channels,
            {**base.repeats, repeat_id: stamped},
            base.used_ids,
            base.masks,
        )
    ).decode("utf-8")
    assert 'confirmed_on = "1999-12-31"' in text and "1999-12-31" in text.split("[used_ids]")[0]


def test_a_refused_file_is_not_a_model():
    with pytest.raises(DescriptionError) as error:
        description_from_bytes(b"schema = 1\n")
    assert error.value.issues and error.value.phase == 2


@pytest.mark.parametrize(
    ("key", "spelled"),
    [
        ("DNA", "DNA"),
        ("a-b_c1", "a-b_c1"),
        ("DAPI 405", '"DAPI 405"'),
        ("é", '"é"'),
        ('a"b', '"a\\"b"'),
    ],
)
def test_keys_are_bare_when_they_can_be(key, spelled):
    assert toml_key(key) == spelled


def test_strings_use_the_minimal_escapes():
    assert toml_string('a"b\\c\n\t\x01\x7f é') == '"a\\"b\\\\c\\n\\t\\u0001\\u007F é"'
    assert (
        tomllib.loads(f"x = {toml_string(chr(1) + chr(127) + chr(8) + chr(12))}")["x"]
        == "\x01\x7f\b\f"
    )
