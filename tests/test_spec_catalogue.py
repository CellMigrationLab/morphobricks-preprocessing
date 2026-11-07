"""The fixture catalogue (conformance plan section 2, "Header lines").

Every fixture is discovered from the directory, so a new fixture cannot be forgotten: it has
the ``Fixture``, ``Expected`` and ``Source`` header comments, the catalogue
(``fixtures/README.md``) lists it with the same codes, and the catalogue lists no file that
does not exist.
"""

from __future__ import annotations

import re

import pytest

from spec_pin import KINDS, all_fixtures, catalogue, codes_in, fixtures, spec_root

ALL = all_fixtures()


def _heading_count(kind: str) -> int:
    readme = (spec_root() / "fixtures" / "README.md").read_text(encoding="utf-8")
    title = {"valid": "Valid", "invalid": "Invalid", "transitions": "Transition"}[kind]
    return int(re.search(rf"^## {title} fixtures \((\d+)\)", readme, re.MULTILINE).group(1))


@pytest.mark.parametrize("kind", KINDS)
def test_the_catalogue_lists_every_file_and_nothing_else(kind):
    on_disk = {item.name for item in fixtures(kind)}
    listed = {name for name in catalogue() if name[0] == kind[0]}
    assert on_disk == listed
    assert len(on_disk) == _heading_count(kind)


def test_fixture_numbers_are_unique_per_kind():
    for kind in KINDS:
        numbers = [re.match(r"\D(\d+)", item.name).group(1) for item in fixtures(kind)]
        assert len(numbers) == len(set(numbers))


@pytest.mark.parametrize("item", ALL, ids=lambda item: f"{item.kind}/{item.name}")
def test_fixture_has_its_header_lines(item):
    header = item.header
    assert set(header) == {"Fixture", "Expected", "Source"}
    assert header["Fixture"] == item.name
    assert header["Expected"] and header["Source"]


@pytest.mark.parametrize("item", ALL, ids=lambda item: f"{item.kind}/{item.name}")
def test_header_and_catalogue_agree_on_the_expected_codes(item):
    row = catalogue()[item.name]
    if item.kind == "valid":
        assert item.header["Expected"].startswith("PASS")
        assert item.expected_codes == codes_in(row[1])
    elif item.kind == "invalid":
        assert item.expected_codes == codes_in(row[1])
        assert item.expected_codes and all(code.startswith("E_") for code in item.expected_codes)
        assert row[2] in {"1", "2", "3", "4", "5"}
    else:
        assert item.expected_codes == {c for c in codes_in(row[1]) if c.startswith("R_")}


def test_the_catalogue_names_each_file_by_its_link():
    readme = (spec_root() / "fixtures" / "README.md").read_text(encoding="utf-8")
    for item in ALL:
        assert f"({item.kind}/{item.name}.toml)" in readme


def test_the_encoding_fixtures_are_given_as_bytes():
    # i01 and i02 only reach the encoding check when a reader is given raw bytes.
    by_name = {item.name: item for item in ALL}
    assert by_name["i01_bom"].data.startswith(b"\xef\xbb\xbf")
    with pytest.raises(UnicodeDecodeError):
        by_name["i02_invalid_utf8"].data.decode("utf-8")
