"""Byte-level rules the Spec 7dbae0c states: section digests (9.3) and the records file (4.6).

The digests of every vendored fixture equal the oracle's; the records file is UTF-8, one JSON
object per line, LF after every line, keys in code point order, non-ASCII characters unescaped,
and a torn or foreign file is refused before anything is published.
"""

from __future__ import annotations

import hashlib
import tomllib

import pytest

from morphobricks_preprocessing.description import description_from_bytes
from morphobricks_preprocessing.description_file import DESCRIPTION_FILENAME, RECORDS_FILENAME
from morphobricks_preprocessing.description_transition import Reconfirmation
from morphobricks_preprocessing.description_write import canonical_bytes, section_digests
from morphobricks_preprocessing.run.description_file import (
    RecordsFileError,
    _append_records,
    publish_description,
    read_records,
)
from spec_oracle import refval
from spec_pin import fixtures

ALL = [*fixtures("valid"), *fixtures("transitions")]
BY_NAME = {item.name.split("_")[0]: item for item in ALL}


@pytest.mark.parametrize("item", ALL, ids=lambda item: item.name)
def test_the_section_digests_equal_the_oracles(item):
    oracle = refval()
    expected = oracle.section_digests(oracle.model(tomllib.loads(item.data.decode("utf-8"))))
    assert section_digests(description_from_bytes(item.data)) == expected


def test_a_section_with_no_tables_digests_the_empty_byte_string():
    model = description_from_bytes(BY_NAME["v01"].data)
    assert not model.masks
    assert section_digests(model)["masks"] == hashlib.sha256(b"").hexdigest()


def test_the_records_file_bytes_are_those_of_section_4_6(tmp_path):
    base = hashlib.sha256(BY_NAME["v01"].data).hexdigest()
    (tmp_path / DESCRIPTION_FILENAME).write_bytes(BY_NAME["v01"].data)
    candidate = description_from_bytes(BY_NAME["t01"].data)
    both = (
        Reconfirmation("repeats", "rep1", "Day1", "2026-11-07"),
        Reconfirmation("repeats", "rep2", "Day2", "2026-11-07"),
    )
    done = publish_description(tmp_path, candidate, base_sha256=base, confirmations=both)
    new = hashlib.sha256(canonical_bytes(candidate)).hexdigest()
    line = (
        '{"confirmed_on": "2026-11-07", "id_kind": "repeats", "new_id": "%s", "new_sha256": "%s", '
        '"old_id": "%s", "old_sha256": "%s", "type": "reconfirmation"}\n'
    )
    expected = (line % ("Day1", new, "rep1", base) + line % ("Day2", new, "rep2", base)).encode()
    assert (tmp_path / RECORDS_FILENAME).read_bytes() == expected
    assert done.sha256 == new


def test_non_ascii_text_is_written_unescaped_and_read_back_whole(tmp_path):
    # U+2028 and U+0085 are line breaks to str.splitlines but not to the file format.
    record = {"type": "reconfirmation", "old_id": "café x\u0085y", "new_id": None}
    _append_records(tmp_path, (record,))
    data = (tmp_path / RECORDS_FILENAME).read_bytes()
    assert "café x\u0085y".encode() in data and b"\\u" not in data
    assert data.count(b"\n") == 1 and data.endswith(b"\n")
    assert read_records(tmp_path) == (record,)


@pytest.mark.parametrize(
    "content",
    [b'{"a": 1}', b'{"a": 1}\n[1]\n', b'{"a": 1}\nnot json\n', b'{"a": 1}\n\n', b"\xff\xfe\n"],
)
def test_a_torn_or_foreign_records_file_is_refused_before_anything_is_written(tmp_path, content):
    (tmp_path / RECORDS_FILENAME).write_bytes(content)
    with pytest.raises(RecordsFileError):
        read_records(tmp_path)
    with pytest.raises(RecordsFileError):
        _append_records(tmp_path, ({"type": "reconfirmation"},))
    assert (tmp_path / RECORDS_FILENAME).read_bytes() == content
