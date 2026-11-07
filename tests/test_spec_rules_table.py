"""The reader's one table of rules equals the vendored normative text (conformance plan 1).

The tables of ``description_rules`` are not a second source: each one is compared here with the
table of ``docs/experiment-v1.md`` it states, so a change of the text without the reader (or
the reverse) fails.
"""

from __future__ import annotations

import re

import pytest

from morphobricks_preprocessing import description_rules as rules
from morphobricks_preprocessing.description_rules import Code, TableSpec
from morphobricks_preprocessing.discovery import MISSING_TEXT
from spec_text import (
    BACKTICKED,
    section,
    spec_codes,
    spec_forbidden,
    spec_label_sentinels,
    spec_reserved,
    spec_token_pattern,
    table_rows,
)


def _fields(heading: str, nth: int = 0) -> tuple[tuple[str, ...], frozenset[str]]:
    """Return the keys in order, and the required ones, of the nth ``Key`` table of a section."""
    rows = [r for r in table_rows(section(heading), "Key", nth) if BACKTICKED.match(r[0])]
    keys = tuple(BACKTICKED.match(r[0]).group(1) for r in rows)
    required = frozenset(BACKTICKED.match(r[0]).group(1) for r in rows if r[2].startswith("yes"))
    return keys, required


TABLES = {
    "4.1": (0, rules.TOP),
    "4.2": (0, rules.CONDITION),
    "4.3": (0, rules.CHANNEL),
    "4.4": (0, rules.REPEAT),
    "4.4 members": (1, rules.MEMBER),
    "4.4 units": (2, rules.UNIT),
    "4.5": (0, rules.MASK),
    "4.9": (0, rules.USED_IDS),
}


@pytest.mark.parametrize("name", TABLES)
def test_a_field_table_equals_the_text(name):
    nth, table = TABLES[name]
    assert isinstance(table, TableSpec)
    keys, required = _fields(name.split()[0], nth)
    assert table.fields == keys
    assert table.required == required


def test_the_codes_are_the_catalogue_of_section_6():
    assert {code.value for code in Code} == spec_codes()
    assert all(code.name == code.value for code in Code)


def test_the_forbidden_names_are_section_4_7():
    assert rules.FORBIDDEN == spec_forbidden()


def test_the_enumerations_and_the_schema_header_are_the_text():
    mask_rule = next(r for r in table_rows(section("4.5"), "Key") if r[0] == "`representation`")
    assert set(rules.REPRESENTATIONS) == {"instances", "binary"}
    assert all(f"`{value}`" in mask_rule[3] for value in rules.REPRESENTATIONS)
    units = table_rows(section("4.4"), "Key", 2)
    unit_rule = next(r for r in units if r[0] == "`image_type`")
    assert rules.IMAGE_TYPES == ("rgb",)
    assert re.search(r"Only the value `rgb`", unit_rule[3])
    top = {r[0]: r for r in table_rows(section("4.1"), "Key")}
    assert f"`{rules.SCHEMA_ID}`" in top["`schema`"][3]
    assert rules.SUPPORTED_VERSIONS == {1}
    assert "Exactly `1`" in top["`schema_version`"][3]


def test_the_reserved_list_is_the_table_of_section_3_2():
    assert rules.RESERVED == spec_reserved()


def test_the_token_pattern_and_the_rgb_tokens_are_the_text():
    assert rules.TOKEN.pattern == spec_token_pattern()
    rgb = section("4.4")
    assert any('exactly `["R", "G", "B"]`' in line for line in rgb)
    assert rules.RGB_TOKENS == ("R", "G", "B")


def test_the_label_rule_the_reader_calls_refuses_the_names_of_the_text():
    # The Label rule is the brick's check_identity_text (decision D-e), not a copy: its list
    # of refused names must be the one section 3.2 states.
    assert MISSING_TEXT == spec_label_sentinels()
