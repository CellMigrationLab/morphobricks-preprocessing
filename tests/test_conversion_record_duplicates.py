"""A conversion record listing one image twice is refused (#387)."""

import dataclasses
import json

import pytest

from morphobricks_preprocessing import RunInputs
from morphobricks_preprocessing.conversion_record import (
    RECORD_FILENAME,
    ConversionRecord,
    ConversionRecordError,
    read_record,
)
from morphobricks_preprocessing.manifest import file_sha256
from morphobricks_preprocessing.run_inputs_error import RunInputsError

pytestmark = pytest.mark.filterwarnings("ignore:No experiment description:UserWarning")


@pytest.fixture
def projected(tmp_path):
    from test_run_projection import projected_run

    copies, out, _, _ = projected_run(tmp_path, method="max")
    return copies, out


def duplicate_first_entry(copies, change=None):
    """Append a second entry for the record's first image; return the record's new SHA-256."""
    path = copies / RECORD_FILENAME
    data = json.loads(path.read_text(encoding="utf-8"))
    twin = json.loads(json.dumps(data["files"][0]))
    if change:
        change(twin)
    data["files"].append(twin)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return data["files"][0]["relative_path"]


@pytest.mark.parametrize("change", [None, lambda e: e.update(copy_sha256="0" * 64)])
def test_read_record_refuses_an_image_listed_twice(projected, change):
    copies, _ = projected
    twice = duplicate_first_entry(copies, change)
    with pytest.raises(ConversionRecordError, match=f"lists {twice!r} more than once"):
        read_record(copies)


def test_the_constructor_refuses_an_image_listed_twice(projected):
    copies, _ = projected
    record, _ = read_record(copies)
    with pytest.raises(ConversionRecordError, match="more than once"):
        dataclasses.replace(record, files=(*record.files, record.files[0]))
    assert isinstance(record, ConversionRecord)


def test_a_run_whose_record_lists_an_image_twice_does_not_open(projected, monkeypatch):
    copies, out = projected
    duplicate_first_entry(copies)
    # The manifest pins the record's SHA-256: pin the edited record, as an edit of both would.
    manifest = json.loads((out / "run_manifest.json").read_text(encoding="utf-8"))
    sha = file_sha256(copies / RECORD_FILENAME)
    manifest["conversion_record_sha256"] = sha
    manifest["projection"]["conversion_record"]["sha256"] = sha
    (out / "run_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    with pytest.raises(RunInputsError, match="more than once"):
        RunInputs.open(out)
