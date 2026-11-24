"""The published description model is immutable all the way down (#489).

``read_published_description`` parses the same bytes once and hands every reader in the process
the same model, so a nested table one consumer could change (a condition removed, a member's
units edited) would change what every later reader sees, while the file and its SHA-256 still
match. Every table of the model is a read-only copy and every array a tuple: a change is
refused, and the model pickles and copies through plain dicts, rebuilt read-only.
"""

from __future__ import annotations

import copy
import pickle
from concurrent.futures import ThreadPoolExecutor
from types import MappingProxyType

import pytest

from morphobricks_preprocessing.description import (
    Description,
    Member,
    Repeat,
    Unit,
    description_from_bytes,
)
from morphobricks_preprocessing.description_consume import read_published_description
from morphobricks_preprocessing.description_file import DESCRIPTION_FILENAME
from morphobricks_preprocessing.description_write import canonical_bytes, section_digests
from morphobricks_preprocessing.run_inputs import RunInputs
from morphobricks_preprocessing.run_inputs_request import Request
from morphobricks_preprocessing.testing import make_described_run
from spec_pin import fixtures

VALID = {f.name.split("_")[0]: f for f in fixtures("valid")}


def _tables(description: Description):
    """Yield every table of a model, nested ones included, with its path."""
    for name in ("conditions", "channels", "repeats", "used_ids", "masks"):
        yield name, getattr(description, name)
    for repeat_id, repeat in description.repeats.items():
        yield f"repeats.{repeat_id}.members", repeat.members
        for condition_id, member in repeat.members.items():
            yield (
                f"repeats.{repeat_id}.members.{condition_id}.technical_repeats",
                member.technical_repeats,
            )


def _refuses_every_change(description: Description) -> None:
    for path, table in _tables(description):
        assert isinstance(table, MappingProxyType), path
        with pytest.raises(TypeError):
            table["NEW"] = None
        for key in list(table):
            with pytest.raises(TypeError):
                del table[key]
        assert not hasattr(table, "pop") and not hasattr(table, "clear"), path
    assert all(isinstance(ids, tuple) for ids in description.used_ids.values())


@pytest.mark.parametrize("name", ["v01", "v08", "v41", "v43"])
def test_a_parsed_description_refuses_every_change_to_its_tables(name):
    _refuses_every_change(description_from_bytes(VALID[name].data))


def test_one_reader_cannot_change_what_a_later_reader_sees(tmp_path):
    run = make_described_run(tmp_path / "run", VALID["v01"].data)
    path = run.output / DESCRIPTION_FILENAME
    first, sha = read_published_description(path)
    digests = section_digests(first)
    _refuses_every_change(first)
    for _, table in _tables(first):
        for key in list(table):
            with pytest.raises((TypeError, AttributeError)):
                table.pop(key)
    again, sha_again = read_published_description(path)
    assert sha_again == sha
    assert again == description_from_bytes(path.read_bytes())
    assert section_digests(again) == digests
    assert set(again.conditions) == {"CTRL", "DrugA"}


def test_a_run_opened_twice_in_one_process_gives_the_same_answers(tmp_path):
    run = make_described_run(tmp_path / "run", VALID["v01"].data)
    inputs = RunInputs.open(run.output)
    described = inputs.description()
    request = inputs.resolve_request(Request(("DNA",)))
    _refuses_every_change(described)
    member = described.repeats["rep1"].members["CTRL"]
    with pytest.raises(TypeError):
        member.technical_repeats["single"] = None
    reopened = RunInputs.open(run.output)  # the digest and identity checks see the file
    assert reopened.description() == described
    assert reopened.section_digests() == inputs.section_digests()
    assert reopened.resolve_request(Request(("DNA",))) == request
    assert [f.channel_ids for f in reopened.fovs()] == [f.channel_ids for f in inputs.fovs()]


def test_two_outputs_with_the_same_bytes_and_concurrent_readers(tmp_path):
    data = VALID["v01"].data
    one = make_described_run(tmp_path / "one", data).output / DESCRIPTION_FILENAME
    two = make_described_run(tmp_path / "two", data).output / DESCRIPTION_FILENAME
    expected = description_from_bytes(data)
    with ThreadPoolExecutor(max_workers=8) as pool:
        read = list(pool.map(read_published_description, [one, two] * 8))
    assert all(description == expected for description, _ in read)
    assert len({sha for _, sha in read}) == 1
    for description, _ in read:
        _refuses_every_change(description)


def test_changed_bytes_give_the_changed_model(tmp_path):
    run = make_described_run(tmp_path / "run", VALID["v01"].data)
    path = run.output / DESCRIPTION_FILENAME
    before, sha = read_published_description(path)
    path.write_bytes(VALID["v41"].data)
    after, sha_after = read_published_description(path)
    assert sha_after != sha and after != before
    assert after == description_from_bytes(VALID["v41"].data)


@pytest.mark.parametrize("name", ["v01", "v08", "v41", "v43"])
def test_a_model_pickles_and_copies_read_only(name):
    description = description_from_bytes(VALID[name].data)
    for again in (
        pickle.loads(pickle.dumps(description)),
        copy.deepcopy(description),
        copy.copy(description),
    ):
        assert again == description
        assert canonical_bytes(again) == canonical_bytes(description)
        _refuses_every_change(again)


def test_tables_given_to_the_constructor_are_copied():
    unit = Unit("single", ("DNA",))
    units = {"single": unit}
    member = Member("rep1", 1, units)
    members = {"CTRL": member}
    repeat = Repeat(True, "2026-11-06", members)
    base = description_from_bytes(VALID["v01"].data)
    used = {kind: list(ids) for kind, ids in base.used_ids.items()}
    repeats = {"rep1": repeat}
    built = Description(dict(base.conditions), dict(base.channels), repeats, used)
    units["other"] = unit
    members.clear()
    repeats["rep2"] = repeat
    used["conditions"].append("X")
    assert set(member.technical_repeats) == {"single"}
    assert set(repeat.members) == {"CTRL"}
    assert set(built.repeats) == {"rep1"}
    assert built.used_ids["conditions"] == base.used_ids["conditions"]
    _refuses_every_change(built)
