"""``read_description`` and ``publish_description``: symbolic links, the lock, the records file.

The Spec scenarios (P09, P16-P20, P22) are in ``test_spec_publication.py``; this file holds the
mechanics around them: a symbolic link is never followed or replaced, a held lock stops a
publication, the records file is append-only, and a failed publication leaves the folder as it was.
"""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

import pytest

from morphobricks_preprocessing.description import DescriptionError, description_from_bytes
from morphobricks_preprocessing.description_file import (
    DESCRIPTION_FILENAME,
    FILE_PHASE,
    RECORDS_FILENAME,
    read_description,
    sha256_hex,
)
from morphobricks_preprocessing.description_rules import Refusal
from morphobricks_preprocessing.description_transition import Reconfirmation
from morphobricks_preprocessing.description_write import canonical_bytes
from morphobricks_preprocessing.run import description_file as publication
from morphobricks_preprocessing.run.description_file import (
    ABSENT,
    PublicationRefused,
    RecordsFileError,
    publish_description,
    read_records,
)
from morphobricks_preprocessing.run.lock import LOCK_FILENAME, OutputLockedError, output_lock
from spec_pin import fixtures

BY_NAME = {item.name.split("_")[0]: item for item in [*fixtures("valid"), *fixtures("transitions")]}
V01 = description_from_bytes(BY_NAME["v01"].data)
DAY = "2026-11-07"
RENAMES = (
    Reconfirmation("repeats", "rep1", "Day1", DAY),
    Reconfirmation("repeats", "rep2", "Day2", DAY),
)


def _link(target: Path, link: Path) -> None:
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("this system does not allow symbolic links")


def _snapshot(folder: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in folder.iterdir() if not p.is_symlink()}


def test_p09_a_symlinked_description_is_refused_by_the_reader(tmp_path):
    real = tmp_path / "elsewhere.toml"
    real.write_bytes(BY_NAME["v01"].data)
    _link(real, tmp_path / DESCRIPTION_FILENAME)
    with pytest.raises(DescriptionError) as refusal:
        read_description(tmp_path / DESCRIPTION_FILENAME)
    assert [i.code for i in refusal.value.issues] == [Refusal.R_FILE_SYMLINK]
    assert refusal.value.phase == FILE_PHASE
    assert read_description(real) == V01  # the same bytes, not through a link


def test_p09_a_dangling_symlink_is_refused_not_missing(tmp_path):
    _link(tmp_path / "nowhere.toml", tmp_path / DESCRIPTION_FILENAME)
    with pytest.raises(DescriptionError) as refusal:
        read_description(tmp_path / DESCRIPTION_FILENAME)
    assert refusal.value.issues[0].code == Refusal.R_FILE_SYMLINK


def test_p09_publication_refuses_a_symlink_and_neither_follows_nor_replaces_it(tmp_path):
    real = tmp_path / "elsewhere.toml"
    real.write_bytes(BY_NAME["v01"].data)
    link = tmp_path / DESCRIPTION_FILENAME
    _link(real, link)
    before = _snapshot(tmp_path)
    with pytest.raises(PublicationRefused) as refusal:
        publish_description(
            tmp_path,
            description_from_bytes(BY_NAME["v16"].data),
            base_sha256=sha256_hex(real.read_bytes()),
        )
    assert refusal.value.codes == {Refusal.R_FILE_SYMLINK}
    assert link.is_symlink() and _snapshot(tmp_path) == before
    assert not (tmp_path / LOCK_FILENAME).exists()


def test_a_missing_file_is_not_an_empty_description(tmp_path):
    with pytest.raises(FileNotFoundError):
        read_description(tmp_path / DESCRIPTION_FILENAME)


def test_the_reader_reports_every_issue_of_the_first_failing_phase(tmp_path):
    (tmp_path / DESCRIPTION_FILENAME).write_bytes(
        next(f for f in fixtures("invalid") if f.name.startswith("i59")).data
    )
    with pytest.raises(DescriptionError) as refusal:
        read_description(tmp_path / DESCRIPTION_FILENAME)
    assert refusal.value.issues and refusal.value.phase == 4


def test_a_held_lock_stops_publication_and_nothing_is_written(tmp_path):
    with output_lock(tmp_path), pytest.raises(OutputLockedError):
        publish_description(tmp_path, V01, base_sha256=ABSENT)
    assert list(tmp_path.iterdir()) == []


def test_the_lock_is_released_after_a_publication_and_after_a_refusal(tmp_path):
    publish_description(tmp_path, V01, base_sha256=ABSENT)
    assert not (tmp_path / LOCK_FILENAME).exists()
    with pytest.raises(PublicationRefused):
        publish_description(tmp_path, V01, base_sha256=ABSENT)
    assert not (tmp_path / LOCK_FILENAME).exists()


def test_the_published_file_is_replaced_atomically_and_leaves_no_temporary_file(tmp_path):
    publish_description(tmp_path, V01, base_sha256=ABSENT)
    assert sorted(p.name for p in tmp_path.iterdir()) == [DESCRIPTION_FILENAME]


def _published(tmp_path: Path) -> str:
    return publish_description(tmp_path, V01, base_sha256=ABSENT).sha256


def test_a_second_publication_appends_after_the_first_records(tmp_path):
    base = _published(tmp_path)
    t01 = description_from_bytes(BY_NAME["t01"].data)
    first = publish_description(tmp_path, t01, base_sha256=base, confirmations=RENAMES)
    path = tmp_path / RECORDS_FILENAME
    written = path.read_bytes()
    channel = replace(t01.channels["DNA"], display_name="Nuclei")
    named = replace(t01, channels={**t01.channels, "DNA": channel})
    publish_description(tmp_path, named, base_sha256=first.sha256)
    assert path.read_bytes() == written  # no new record: none was needed
    assert read_records(tmp_path) == first.records


def test_a_torn_records_file_refuses_before_anything_is_written(tmp_path):
    base = _published(tmp_path)
    (tmp_path / RECORDS_FILENAME).write_bytes(b'{"type": "reconfirmation"')
    before = _snapshot(tmp_path)
    with pytest.raises(RecordsFileError):
        publish_description(
            tmp_path,
            description_from_bytes(BY_NAME["t01"].data),
            base_sha256=base,
            confirmations=RENAMES,
        )
    assert _snapshot(tmp_path) == before


@pytest.mark.parametrize("content", [b"not json\n", b"[1, 2]\n"])
def test_a_records_file_that_is_not_lines_of_objects_is_refused(tmp_path, content):
    (tmp_path / RECORDS_FILENAME).write_bytes(content)
    with pytest.raises(RecordsFileError):
        read_records(tmp_path)


def test_a_symlinked_records_file_is_refused(tmp_path):
    real = tmp_path / "log.jsonl"
    real.write_bytes(b"")
    _link(real, tmp_path / RECORDS_FILENAME)
    with pytest.raises(PublicationRefused) as refusal:
        publish_description(tmp_path, V01, base_sha256=ABSENT)
    assert refusal.value.codes == {Refusal.R_FILE_SYMLINK}
    assert not (tmp_path / DESCRIPTION_FILENAME).exists()


def test_no_records_file_reads_as_no_records(tmp_path):
    assert read_records(tmp_path) == ()


def test_a_failed_append_puts_the_previous_file_back(tmp_path, monkeypatch):
    base = _published(tmp_path)
    before = _snapshot(tmp_path)

    def broken(folder, records):
        raise OSError("disk full")

    monkeypatch.setattr(publication, "_append_records", broken)
    with pytest.raises(OSError, match="disk full"):
        publish_description(
            tmp_path,
            description_from_bytes(BY_NAME["t01"].data),
            base_sha256=base,
            confirmations=RENAMES,
        )
    assert _snapshot(tmp_path) == before and not (tmp_path / LOCK_FILENAME).exists()


def test_a_failed_append_removes_a_first_publication(tmp_path, monkeypatch):
    def broken(folder, records):
        raise OSError("disk full")

    monkeypatch.setattr(publication, "_append_records", broken)
    with pytest.raises(OSError, match="disk full"):
        publish_description(tmp_path, V01, base_sha256=ABSENT)
    assert list(tmp_path.iterdir()) == []


def test_a_reconfirmation_the_candidate_does_not_need_is_an_error_and_writes_nothing(tmp_path):
    base = _published(tmp_path)
    before = _snapshot(tmp_path)
    with pytest.raises(ValueError, match="does not make"):
        publish_description(
            tmp_path,
            V01,
            base_sha256=base,
            confirmations=(Reconfirmation("repeats", "rep1", "Day1", DAY),),
        )
    assert _snapshot(tmp_path) == before
    empty = tmp_path / "new"
    empty.mkdir()
    with pytest.raises(ValueError, match="does not make"):  # nothing published yet: nothing to name
        publish_description(empty, V01, base_sha256=ABSENT, confirmations=RENAMES)
    assert list(empty.iterdir()) == []
    assert not (tmp_path / RECORDS_FILENAME).exists()


def test_a_candidate_the_reader_would_refuse_is_not_published(tmp_path):
    bad = replace(V01, conditions={})
    with pytest.raises(DescriptionError) as refusal:
        publish_description(tmp_path, bad, base_sha256=ABSENT)
    assert refusal.value.issues
    assert list(tmp_path.iterdir()) == []


def test_an_unconfirmed_repeat_is_not_published(tmp_path):
    repeat = replace(V01.repeats["rep1"], confirmed=False)
    with pytest.raises(DescriptionError):
        publish_description(
            tmp_path, replace(V01, repeats={**V01.repeats, "rep1": repeat}), base_sha256=ABSENT
        )
    assert list(tmp_path.iterdir()) == []


def test_a_published_file_that_is_not_valid_is_not_a_base(tmp_path):
    (tmp_path / DESCRIPTION_FILENAME).write_bytes(b"schema = 1\n")
    base = sha256_hex(b"schema = 1\n")
    with pytest.raises(DescriptionError):
        publish_description(tmp_path, V01, base_sha256=base)
    assert (tmp_path / DESCRIPTION_FILENAME).read_bytes() == b"schema = 1\n"


def test_confirmed_on_is_never_taken_from_the_clock(tmp_path):
    publish_description(tmp_path, V01, base_sha256=ABSENT)
    text = (tmp_path / DESCRIPTION_FILENAME).read_text(encoding="utf-8")
    assert text.count('confirmed_on = "2026-11-06"') == 2


def test_the_records_are_written_with_the_same_hashes_as_the_files(tmp_path):
    base = _published(tmp_path)
    done = publish_description(
        tmp_path,
        description_from_bytes(BY_NAME["t01"].data),
        base_sha256=base,
        confirmations=RENAMES,
    )
    assert done.sha256 == sha256_hex((tmp_path / DESCRIPTION_FILENAME).read_bytes())
    assert {r["old_sha256"] for r in read_records(tmp_path)} == {base}
    assert {r["new_sha256"] for r in read_records(tmp_path)} == {done.sha256}
    assert canonical_bytes(V01) != (tmp_path / DESCRIPTION_FILENAME).read_bytes()
    assert os.path.getsize(tmp_path / RECORDS_FILENAME) > 0
