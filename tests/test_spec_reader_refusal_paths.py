"""The reader names the offending field and value of five codes the oracle compares only by code.

``tests/spec_oracle.py`` (``COARSE``) drops the paths of these codes, because the oracle names
a table and the reader names each field. A refusal attached to the wrong field would therefore
pass the crosscheck; these tests pin the path and the value of each, on the Spec fixtures that
trigger them.
"""

from __future__ import annotations

import pytest

from morphobricks_preprocessing.description_read import read_description_bytes
from spec_oracle import COARSE
from spec_pin import fixtures

BY_NAME = {item.name: item for item in fixtures("invalid")}
UNIT = "repeats.rep1.members.{}.technical_repeats.acq1.source_folder"

CASES = {
    "i122_two_condition_ids_one_folder": (
        "E_SOURCE_FOLDER_REUSED",
        {
            ("conditions.CTRL.source_folder", "control"),
            ("conditions.DrugA.source_folder", "control"),
        },
    ),
    "i123_two_technical_repeat_ids_one_folder": (
        "E_SOURCE_FOLDER_REUSED",
        {
            (
                "repeats.rep1.members.CTRL.technical_repeats.acq1.source_folder",
                "acquisition_1",
            ),
            (
                "repeats.rep1.members.CTRL.technical_repeats.acq2.source_folder",
                "acquisition_1",
            ),
        },
    ),
    "i124_one_technical_repeat_id_two_folders": (
        "E_SOURCE_FOLDER_CONFLICT",
        {
            (UNIT.format("CTRL"), "acquisition_1"),
            (UNIT.format("DrugA"), "acq_1"),
        },
    ),
    "i74_source_folder_in_two_repeats": (
        "E_SOURCE_REPEAT_REUSED",
        {
            ("repeats.Day2.members.CTRL.source_repeat", "exp7"),
            ("repeats.Day3.members.CTRL.source_repeat", "exp7"),
        },
    ),
    "i80_mask_source_type_reused": (
        "E_MASK_SOURCE_TYPE_REUSED",
        {
            ("masks.Nuclei.source_type", "nuclei"),
            ("masks.Nuclei2.source_type", "nuclei"),
        },
    ),
    "i64_reference_cycle": (
        "E_REFERENCE_CYCLE",
        {
            ("conditions.CTRL.reference", ("CTRL", "DrugA")),
            ("conditions.DrugA.reference", ("CTRL", "DrugA")),
        },
    ),
    "i65_reference_cycle_long": (
        "E_REFERENCE_CYCLE",
        {
            (f"conditions.{name}.reference", ("CTRL", "DrugA", "DrugB"))
            for name in ("CTRL", "DrugA", "DrugB")
        },
    ),
}


def test_the_cases_cover_the_five_codes_the_oracle_compares_by_code():
    assert {code for code, _ in CASES.values()} == COARSE


@pytest.mark.parametrize("name", CASES)
def test_each_refusal_is_attached_to_its_offending_field_with_its_value(name):
    code, expected = CASES[name]
    issues = read_description_bytes(BY_NAME[name].data).errors
    found = {
        (issue.path, tuple(issue.value) if isinstance(issue.value, list) else issue.value)
        for issue in issues
        if issue.code.value == code
    }
    assert found == expected
    assert len([i for i in issues if i.code.value == code]) == len(expected)
