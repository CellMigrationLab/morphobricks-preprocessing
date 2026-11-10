"""What a run records and refuses when the output folder holds a published description.

The Spec scenarios are in ``test_spec_run_description.py``. Here: the manifest entry (the file's
SHA-256, section digests, records copied unchanged, image type verification), a run without a
description (refused, ``A_DESCRIPTION_REQUIRED``), an invalid description or records file, and a
description that contradicts the folder.
"""

from __future__ import annotations

import json
import warnings
from dataclasses import replace

import pytest

from description_tree import MAPPING, build_experiment
from morphobricks_preprocessing import run_experiment
from morphobricks_preprocessing.description import description_from_bytes
from morphobricks_preprocessing.description_file import sha256_hex
from morphobricks_preprocessing.description_write import canonical_bytes, section_digests
from morphobricks_preprocessing.manifest import ManifestFormatError, read_manifest
from spec_pin import fixtures

V01 = next(f for f in fixtures("valid") if f.name.startswith("v01_"))
DESCRIPTION = description_from_bytes(V01.data)
RECORD = {
    "type": "reconfirmation",
    "confirmed_on": "2026-01-01",
    "id_kind": "repeats",
    "old_id": "rep1",
    "new_id": "Day1",
    "old_sha256": "a" * 64,
    "new_sha256": "b" * 64,
}


def _folder(tmp_path):
    root, out = tmp_path / "exp", tmp_path / "out"
    out.mkdir()
    build_experiment(root, DESCRIPTION)
    return root, out


def _manifest(out):
    return json.loads((out / "run_manifest.json").read_text(encoding="utf-8"))


def test_the_manifest_records_the_file_its_digests_and_the_records_unchanged(tmp_path):
    root, out = _folder(tmp_path)
    (out / "morphobricks.toml").write_bytes(V01.data)
    line = json.dumps(RECORD, sort_keys=True) + "\n"
    (out / "morphobricks.records.jsonl").write_bytes(line.encode("utf-8"))  # no CRLF (4.6)
    assert run_experiment(root, MAPPING, output_folder=out).written
    entry = _manifest(out)["experiment_description"]
    assert entry["sha256"] == sha256_hex(V01.data)
    assert entry["schema_version"] == 1
    assert entry["section_digests"] == section_digests(DESCRIPTION)
    assert entry["records"] == [RECORD]
    assert all(u["image_type_header_verified"] for u in entry["units"])
    assert read_manifest(out / "run_manifest.json").experiment_description == entry


def test_an_unchanged_rerun_keeps_the_manifest_bytes(tmp_path):
    root, out = _folder(tmp_path)
    (out / "morphobricks.toml").write_bytes(V01.data)
    run_experiment(root, MAPPING, output_folder=out)
    first = (out / "run_manifest.json").read_bytes()
    assert run_experiment(root, output_folder=out).written
    assert (out / "run_manifest.json").read_bytes() == first


def test_the_sha_is_the_exact_bytes_even_when_not_canonical(tmp_path):
    root, out = _folder(tmp_path)
    text = b"# a comment the canonical writer drops\n" + canonical_bytes(DESCRIPTION)
    (out / "morphobricks.toml").write_bytes(text)
    assert run_experiment(root, MAPPING, output_folder=out).written
    assert _manifest(out)["experiment_description"]["sha256"] == sha256_hex(text)
    assert sha256_hex(text) != sha256_hex(canonical_bytes(DESCRIPTION))


def test_a_run_without_a_description_is_refused_before_anything_is_read(tmp_path):
    root, out = _folder(tmp_path)
    result = run_experiment(root, MAPPING, output_folder=out)
    assert not result.written and not result.report_written
    (refusal,) = result.issues
    assert refusal.code == "A_DESCRIPTION_REQUIRED" and refusal.is_error
    assert "morphobricks.toml" in refusal.message and "describe" in refusal.action
    assert list(out.iterdir()) == []  # nothing written, not even the report


def test_a_run_with_a_description_does_not_warn(tmp_path):
    root, out = _folder(tmp_path)
    (out / "morphobricks.toml").write_bytes(V01.data)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert run_experiment(root, MAPPING, output_folder=out).written


def test_a_draft_alone_is_not_a_description(tmp_path):
    root, out = _folder(tmp_path)
    (out / "morphobricks.draft.json").write_text("{}", encoding="utf-8")
    result = run_experiment(root, MAPPING, output_folder=out)
    assert [i.code for i in result.issues] == ["A_DESCRIPTION_REQUIRED"]


def test_an_invalid_description_stops_the_run_with_its_code_and_path(tmp_path):
    root, out = _folder(tmp_path)
    (out / "morphobricks.toml").write_text('schema = "wrong"\n', encoding="utf-8")
    result = run_experiment(root, MAPPING, output_folder=out)
    assert not result.written and not (out / "run_manifest.json").exists()
    assert result.issues and all(i.is_error for i in result.issues)
    assert result.issues[0].code.startswith("E_")
    assert "morphobricks.toml cannot be used" in result.issues[0].message


def test_an_unreadable_records_file_stops_the_run(tmp_path):
    root, out = _folder(tmp_path)
    (out / "morphobricks.toml").write_bytes(V01.data)
    (out / "morphobricks.records.jsonl").write_text("not json\n", encoding="utf-8")
    result = run_experiment(root, MAPPING, output_folder=out)
    assert not result.written and not (out / "run_manifest.json").exists()
    assert result.issues[0].code == "R_RECORDS_FILE_INVALID"  # the Spec code (batch 34a)
    assert "E_RECORDS_NOT_JSON_OBJECT" in result.issues[0].message


def test_a_malformed_record_stops_the_run_with_the_spec_code_and_its_reason(tmp_path):
    root, out = _folder(tmp_path)
    (out / "morphobricks.toml").write_bytes(V01.data)
    (out / "morphobricks.records.jsonl").write_text('{"type": "reconfirmation"}\n', "utf-8")
    result = run_experiment(root, MAPPING, output_folder=out)
    assert not result.written and not (out / "run_manifest.json").exists()
    assert [i.code for i in result.issues] == ["R_RECORDS_FILE_INVALID"]
    assert "E_RECORDS_KEY_SET" in result.issues[0].message


def test_a_description_that_lists_a_missing_unit_is_refused_with_its_code(tmp_path):
    root, out = _folder(tmp_path)
    other = replace(DESCRIPTION, conditions={**DESCRIPTION.conditions})
    repeat = DESCRIPTION.repeats["rep1"]
    member = repeat.members["CTRL"]
    moved = replace(member, source_repeat="rep9")
    repeats = {
        **DESCRIPTION.repeats,
        "rep1": replace(repeat, members={**repeat.members, "CTRL": moved}),
    }
    other = replace(other, repeats=repeats)
    (out / "morphobricks.toml").write_bytes(canonical_bytes(other))
    result = run_experiment(root, MAPPING, output_folder=out)
    assert not result.written
    assert {"R_UNIT_NOT_FOUND", "R_UNIT_NOT_DECLARED"} <= {i.code for i in result.issues}


def test_a_malformed_manifest_entry_is_refused_on_read(tmp_path):
    root, out = _folder(tmp_path)
    (out / "morphobricks.toml").write_bytes(V01.data)
    run_experiment(root, MAPPING, output_folder=out)
    data = _manifest(out)
    data["experiment_description"]["sha256"] = "nope"
    (out / "run_manifest.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ManifestFormatError, match="SHA-256"):
        read_manifest(out / "run_manifest.json")
