"""The derived ``experiment_design.csv`` equals the Spec's golden files byte for byte (section 11).

Conformance P30 and P32 (derived part). Each golden is the derivation of a named valid fixture;
the oracle's own derivation (``refval.derive_csv``) must agree with both.
"""

from __future__ import annotations

import datetime
import tomllib

import pytest

from frozen_clock import FrozenClock
from morphobricks_preprocessing import parse_design_bytes
from morphobricks_preprocessing.description import description_from_bytes
from morphobricks_preprocessing.design_derive import derive_design, write_derived_design
from spec_oracle import refval
from spec_pin import fixtures, spec_root

VALID = {item.name.split("_")[0]: item for item in fixtures("valid")}
GOLDEN = sorted((spec_root() / "fixtures" / "expected").glob("*.experiment_design.csv"))


def test_the_golden_files_are_the_three_the_catalogue_lists():
    assert [p.name.split("_")[0] for p in GOLDEN] == ["v13", "v33", "v34"]


@pytest.mark.parametrize("golden", GOLDEN, ids=lambda p: p.name.split("_")[0])
def test_the_derived_file_equals_the_golden_file_byte_for_byte(golden, tmp_path):
    item = VALID[golden.name.split("_")[0]]
    path = write_derived_design(description_from_bytes(item.data), tmp_path / "design.csv")
    assert path.read_bytes() == golden.read_bytes()


@pytest.mark.parametrize("item", VALID.values(), ids=lambda item: item.name.split("_")[0])
def test_the_derived_file_equals_the_oracles_on_every_valid_fixture(item, tmp_path):
    oracle = refval()
    expected = oracle.derive_csv(oracle.model(tomllib.loads(item.data.decode("utf-8"))))
    path = write_derived_design(description_from_bytes(item.data), tmp_path / "design.csv")
    assert path.read_bytes() == expected


@pytest.mark.parametrize("item", VALID.values(), ids=lambda item: item.name.split("_")[0])
def test_the_derived_file_is_read_back_by_the_strict_parser(item, tmp_path):
    description = description_from_bytes(item.data)
    path = write_derived_design(description, tmp_path / "design.csv")
    design = parse_design_bytes(path.read_bytes())
    assert design == derive_design(description)
    assert all(row.confirmed for row in design.rows)


def test_the_file_names_folders_never_ids():
    description = description_from_bytes(VALID["v33"].data)  # folder control, ID ctrl
    rows = derive_design(description).rows
    golden = (next(p for p in GOLDEN if p.name.startswith("v33"))).read_text().splitlines()[1:]
    assert [row.condition for row in rows] == [line.split(",")[0] for line in golden]
    assert "ctrl" not in {row.condition for row in rows}
    assert {row.condition for row in rows if row.control} == {"control"}


# P30: v13 derived twice under a frozen clock moved by a day ------------------------------


def test_p30_v13_derived_twice_with_a_clock_moved_by_a_day_equals_the_golden(tmp_path, monkeypatch):
    """P30 (amended 10 Nov 2026, ADR 0002 AR10 (9)): no legacy CSV is read; the clock never is."""
    golden = next(p for p in GOLDEN if p.name.startswith("v13")).read_bytes()
    description = description_from_bytes(VALID["v13"].data)
    clock = FrozenClock(monkeypatch, datetime.date(2026, 11, 9))
    first = write_derived_design(description, tmp_path / "first.csv")
    clock.move(1)
    second = write_derived_design(description, tmp_path / "second.csv")
    assert clock.reads == 0  # the clock is never read
    assert first.read_bytes() == second.read_bytes() == golden
    for row in derive_design(description).rows:  # every row carries its repeat's date
        assert (
            row.confirmed_on == description.repeats[row.repeat_group].confirmed_on == "2026-10-30"
        )
