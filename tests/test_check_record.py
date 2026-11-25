"""``check_headers.json``: the headers a notebook check read, kept exactly (v0.9.0)."""

from __future__ import annotations

import importlib
import json
from dataclasses import fields

import numpy as np
import pytest

from morphobricks_preprocessing.check_record import (
    CHECK_RECORD_FILENAME,
    CheckRecordError,
    read_check_record,
    write_check_record,
)
from morphobricks_preprocessing.fingerprint import Fingerprint
from morphobricks_preprocessing.metadata import TiffMetadata
from morphobricks_preprocessing.physical_sizes import PhysicalSizes
from morphobricks_preprocessing.run.preflight import preflight
from tiff_tree import write_data


def _every_field(meta):
    """Every field, the ones equality leaves out (``invalid``, ``unsupported_units``) too."""
    values = [getattr(meta, f.name) for f in fields(TiffMetadata)]
    sizes = meta.physical_sizes
    return values, [getattr(sizes, f.name) for f in fields(PhysicalSizes)]


def test_a_header_is_read_back_exactly_as_it_was_written(tmp_path):
    meta = TiffMetadata(
        "c/r/a.tif",
        True,
        (2, 3, 16, 16),
        np.dtype(np.uint16),
        "TCYX",
        1,
        6,
        None,
        ("DAPI", None, " x"),
        12,
        physical_sizes=PhysicalSizes(
            0.1 + 0.2, 0.325, None, 2.5, "micrometer", "second",
            unsupported_units=(("Z", "inch"),), invalid="a scale that is not a number",
        ),
        raw_channel_names=("DAPI ", None, " x"),
        channel_names_trimmed=True,
        axes_from_metadata=False,
    )  # fmt: skip
    unreadable = TiffMetadata("c/r/b.tif", False, error="not a TIFF")
    bound = {"c/r/a.tif": Fingerprint(10, 20), "c/r/b.tif": Fingerprint(1, 2, "f" * 64)}
    write_check_record(tmp_path, tmp_path / "exp", bound, (meta, unreadable))
    record = read_check_record(tmp_path)
    assert record.experiment_root == str((tmp_path / "exp").resolve())
    assert record.problem(tmp_path / "exp") == ""
    for kept in (meta, unreadable):
        fingerprint, header = record.headers[kept.relative_path]
        assert fingerprint == bound[kept.relative_path]
        assert _every_field(header) == _every_field(kept)


def test_an_image_that_changed_while_its_header_was_read_is_not_recorded(tmp_path):
    meta = TiffMetadata("a.tif", True, (4, 4), np.dtype(np.uint8), "YX")
    write_check_record(tmp_path, tmp_path, {"a.tif": None}, (meta,))
    assert read_check_record(tmp_path).headers == {}


def test_a_record_of_other_fields_or_format_is_refused(tmp_path):
    meta = TiffMetadata("a.tif", True, (4, 4), np.dtype(np.uint8), "YX")
    path = write_check_record(tmp_path, tmp_path, {"a.tif": Fingerprint(1, 2)}, (meta,))
    data = json.loads(path.read_text(encoding="utf-8"))
    data["headers"]["a.tif"]["header"]["new_field"] = 1
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(CheckRecordError, match="not those of this version"):
        read_check_record(tmp_path)
    data["format"] = 99
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(CheckRecordError, match="another kind or format"):
        read_check_record(tmp_path)
    assert read_check_record(tmp_path / "none") is None


def test_the_record_names_another_experiment_folder(tmp_path):
    write_check_record(tmp_path, tmp_path / "a", {}, ())
    assert "another experiment folder" in read_check_record(tmp_path).problem(tmp_path / "b")


def test_preflight_reuses_a_header_only_at_the_same_fingerprint(tmp_path, monkeypatch):
    root = tmp_path / "exp"
    for name in ("a", "b"):
        write_data(root / "c" / "r" / f"{name}.tif", np.zeros((8, 8), np.uint8), axes="YX")
    first = preflight(root, tmp_path / "out", bind_headers=True)
    write_check_record(tmp_path, root, first.header_fingerprints, first.metadata.files)
    recorded = read_check_record(tmp_path).headers
    write_data(root / "c" / "r" / "b.tif", np.zeros((9, 9), np.uint8), axes="YX")
    module = importlib.import_module("morphobricks_preprocessing.run.preflight")

    read = []
    real = module.inspect_image
    monkeypatch.setattr(
        module, "inspect_image", lambda p, rel=None: read.append(rel) or real(p, rel)
    )
    again = preflight(root, tmp_path / "out", recorded=recorded)
    assert read == ["c/r/b.tif"] and again.headers_reused == ("c/r/a.tif",)
    assert again.metadata.files == preflight(root, tmp_path / "out").metadata.files
    assert set(again.header_fingerprints) == {"c/r/a.tif", "c/r/b.tif"}
    assert (tmp_path / CHECK_RECORD_FILENAME).is_file()
