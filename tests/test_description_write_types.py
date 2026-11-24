"""The canonical writer refuses a value of the wrong type; it never drops or converts it (#405).

A model built or changed in code (``dataclasses.replace``) can hold a value its annotation does
not allow. The writer used to skip any mapping it met in a field (as if it were a child table),
so an optional field such as a condition's ``reference`` vanished and the strict re-read of the
bytes, which no longer held it, accepted the file; and it sorted a text ``header_names`` into
its characters. Now the writer runs the strict reader's own type checks (phases 2 and 3,
``E_TYPE``) on the values it would write and refuses before any byte, naming the field and the
type found, so what it writes the reader accepts as the same model.
"""

from __future__ import annotations

import tomllib
from dataclasses import replace

import numpy as np
import pytest

from description_tree import build_experiment
from morphobricks_preprocessing import preflight
from morphobricks_preprocessing.description import (
    Description,
    DescriptionError,
    description_from_bytes,
)
from morphobricks_preprocessing.description_candidate import check_candidate
from morphobricks_preprocessing.description_file import DESCRIPTION_FILENAME, RECORDS_FILENAME
from morphobricks_preprocessing.description_rules import Code
from morphobricks_preprocessing.description_write import (
    DescriptionTypeError,
    canonical_bytes,
    section_digests,
)
from morphobricks_preprocessing.run.description_file import ABSENT, publish_description
from spec_pin import fixtures

VALID = {item.name.split("_")[0]: item for item in fixtures("valid")}
WRONG = {"unexpected": "CTRL"}


def _v01() -> Description:
    return description_from_bytes(VALID["v01"].data)


def _condition(base: Description, key: str, **changes) -> Description:
    return replace(
        base, conditions={**base.conditions, key: replace(base.conditions[key], **changes)}
    )


def _channel(base: Description, key: str, **changes) -> Description:
    return replace(base, channels={**base.channels, key: replace(base.channels[key], **changes)})


def _unit(base: Description, **changes) -> Description:
    repeat = base.repeats["rep1"]
    member = repeat.members["CTRL"]
    unit = replace(member.technical_repeats["single"], **changes)
    member = replace(member, technical_repeats={**member.technical_repeats, "single": unit})
    repeat = replace(repeat, members={**repeat.members, "CTRL": member})
    return replace(base, repeats={**base.repeats, "rep1": repeat})


def _repeat(base: Description, **changes) -> Description:
    return replace(base, repeats={**base.repeats, "rep1": replace(base.repeats["rep1"], **changes)})


CASES = [
    # (model, table path, type found)
    (lambda b: _condition(b, "DrugA", reference=WRONG), "conditions.DrugA.reference", "dict"),
    (lambda b: _condition(b, "DrugA", target=WRONG), "conditions.DrugA.target", "dict"),
    (lambda b: _condition(b, "DrugA", display_name=WRONG), "conditions.DrugA.display_name", "dict"),
    (lambda b: _condition(b, "DrugA", treatment_key=7), "conditions.DrugA.treatment_key", "int"),
    (lambda b: _condition(b, "CTRL", source_folder=WRONG), "conditions.CTRL.source_folder", "dict"),
    (lambda b: _channel(b, "DNA", header_names=WRONG), "channels.DNA.header_names", "dict"),
    (lambda b: _channel(b, "DNA", header_names="DAPI"), "channels.DNA.header_names", "str"),
    (lambda b: _channel(b, "DNA", header_names={"DAPI"}), "channels.DNA.header_names", "set"),
    (lambda b: _channel(b, "DNA", display_name=WRONG), "channels.DNA.display_name", "dict"),
    (
        lambda b: _unit(b, display_name=WRONG),
        "repeats.rep1.members.CTRL.technical_repeats.single.display_name",
        "dict",
    ),
    (
        lambda b: _unit(b, rgb_header_override="false"),
        "repeats.rep1.members.CTRL.technical_repeats.single.rgb_header_override",
        "str",
    ),
    (lambda b: _repeat(b, display_name=WRONG), "repeats.rep1.display_name", "dict"),
    (lambda b: _repeat(b, confirmed=1), "repeats.rep1.confirmed", "int"),
    (lambda b: replace(b, schema_version="1"), "schema_version", "str"),
]
IDS = [f"{path}-{found}" for _, path, found in CASES]


@pytest.mark.parametrize(("build", "path", "found"), CASES, ids=IDS)
def test_the_writer_refuses_a_value_of_the_wrong_type_naming_field_and_type(build, path, found):
    model = build(_v01())
    with pytest.raises(DescriptionTypeError) as caught:
        canonical_bytes(model)
    assert f"{path} holds a value of type {found}" in str(caught.value)
    assert [(i.code, i.path) for i in caught.value.issues] == [(Code.E_TYPE, path)]
    assert isinstance(caught.value, DescriptionError) and isinstance(caught.value, TypeError)
    with pytest.raises(DescriptionTypeError, match=f"{path} holds a value of type {found}"):
        section_digests(model)


def test_a_mapping_reference_is_refused_not_dropped():
    """The issue's own case: on main the reference vanished and the file still parsed."""
    model = _condition(_v01(), "DrugA", reference=WRONG)
    with pytest.raises(DescriptionTypeError) as caught:
        canonical_bytes(model)
    assert str(caught.value) == (
        "the description cannot be written: conditions.DrugA.reference holds a value of type dict, "
        "a type the strict reader refuses (E_TYPE). Nothing was written."
    )


def test_every_wrong_value_is_named_at_once():
    model = _channel(_condition(_v01(), "DrugA", reference=WRONG), "DNA", header_names="DAPI")
    with pytest.raises(DescriptionTypeError) as caught:
        canonical_bytes(model)
    assert [i.path for i in caught.value.issues] == [
        "conditions.DrugA.reference",
        "channels.DNA.header_names",
    ]


def test_publication_refuses_before_any_file_or_record_changes(tmp_path):
    model = _condition(_v01(), "DrugA", reference=WRONG)
    with pytest.raises(
        DescriptionTypeError, match="conditions.DrugA.reference holds a value of type dict"
    ):
        publish_description(tmp_path, model, base_sha256=ABSENT)
    assert not (tmp_path / DESCRIPTION_FILENAME).exists()
    assert not (tmp_path / RECORDS_FILENAME).exists()
    published = publish_description(tmp_path, _v01(), base_sha256=ABSENT)
    before = (tmp_path / DESCRIPTION_FILENAME).read_bytes()
    with pytest.raises(DescriptionTypeError):
        publish_description(tmp_path, model, base_sha256=published.sha256)
    assert (tmp_path / DESCRIPTION_FILENAME).read_bytes() == before
    assert not (tmp_path / RECORDS_FILENAME).exists()


def test_check_and_save_refuses_the_candidate_with_the_field_named(tmp_path):
    root, out = tmp_path / "exp", tmp_path / "out"
    out.mkdir()
    build_experiment(root, _v01(), images=1)
    pre = preflight(root, out)
    check = check_candidate(_condition(_v01(), "DrugA", reference=WRONG), pre, None)
    assert not check.can_save and check.description is None
    assert [i.code for i in check.refusals] == ["E_TYPE"]
    assert "conditions.DrugA.reference" in check.refusals[0].message
    assert "of type dict" in check.refusals[0].message
    assert check_candidate(_v01(), pre, None).description == _v01()


@pytest.mark.parametrize("name", sorted(VALID))
def test_valid_models_are_written_unchanged_and_read_back_equal(name):
    model = description_from_bytes(VALID[name].data)
    written = canonical_bytes(model)
    assert description_from_bytes(written) == model
    assert canonical_bytes(description_from_bytes(written)) == written


def test_lists_and_tuples_write_the_same_array():
    """An array the model holds as a list is not a wrong type: TOML has one array."""
    base = _v01()
    as_list = _channel(base, "DNA", header_names=["DAPI"])
    assert canonical_bytes(as_list) == canonical_bytes(base)
    text = canonical_bytes(_condition(base, "DrugA", reference="CTRL")).decode("utf-8")
    assert tomllib.loads(text)["conditions"]["DrugA"]["reference"] == "CTRL"


@pytest.mark.parametrize(
    ("value", "found"), [(np.True_, "numpy.bool"), (np.int64(3), "numpy.int64")]
)
def test_a_numpy_value_is_named_with_its_module_everywhere_the_type_is_shown(
    tmp_path, value, found
):
    """``np.bool_.__name__`` is ``bool``: the message must not read as the builtin (#470)."""
    model = _repeat(_v01(), confirmed=value)
    path = "repeats.rep1.confirmed"
    with pytest.raises(DescriptionTypeError) as caught:
        canonical_bytes(model)
    assert f"{path} holds a value of type {found}," in str(caught.value)
    root, out = tmp_path / "exp", tmp_path / "out"
    out.mkdir()
    build_experiment(root, _v01(), images=1)
    check = check_candidate(model, preflight(root, out), None)
    assert f"of type {found})" in check.refusals[0].message
