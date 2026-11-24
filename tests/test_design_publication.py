"""The published description and its derived ``experiment_design.csv`` change together (#400).

A publication writes ``morphobricks.toml``, appends its records and writes the derived design
(experiment-v1 section 11). A failure at any of these writes puts the previous description,
records file and design back byte for byte: a newly published description is never left with
the design of the previous one, and the draft stays resumable against an unchanged base.
"""

import os
import tempfile

import pytest

from draft_file_support import COUNTS, PATHS, complete, snapshot
from morphobricks_preprocessing import _io as io_module
from morphobricks_preprocessing import discover_experiment
from morphobricks_preprocessing.description import description_from_bytes
from morphobricks_preprocessing.description_file import (
    DESCRIPTION_FILENAME,
    DRAFT_FILENAME,
    RECORDS_FILENAME,
    read_description,
)
from morphobricks_preprocessing.description_rules import Refusal
from morphobricks_preprocessing.description_transition import Reconfirmation
from morphobricks_preprocessing.design import DESIGN_FILENAME, DesignFormatError
from morphobricks_preprocessing.design_derive import write_derived_design
from morphobricks_preprocessing.draft import draft_from_discovery
from morphobricks_preprocessing.draft_answers import (
    ConditionAnswer,
    DraftAnswers,
    description_from_draft,
)
from morphobricks_preprocessing.draft_format import ABSENT
from morphobricks_preprocessing.run import description_file as publication
from morphobricks_preprocessing.run.description_file import PublicationRefused, publish_description
from morphobricks_preprocessing.run.draft_file import load_draft, publish_draft, save_draft
from morphobricks_preprocessing.run.lock import LOCK_FILENAME
from spec_pin import fixtures
from tiff_tree import make_tree

DAY = "2026-11-07"
RENAMES = (
    Reconfirmation("repeats", "rep1", "Day1", DAY),
    Reconfirmation("repeats", "rep2", "Day2", DAY),
)
FIXTURES = {
    item.name.split("_")[0]: item for item in [*fixtures("valid"), *fixtures("transitions")]
}


@pytest.fixture
def exp(tmp_path):
    make_tree(tmp_path / "exp", PATHS)
    return discover_experiment(tmp_path / "exp")


@pytest.fixture
def out(tmp_path):
    """The output folder, beside the experiment."""
    (tmp_path / "out").mkdir()
    return tmp_path / "out"


def _without_reference(base):
    """The complete answers with no reference: CTRL is no longer a control in the design."""
    answers = complete(base)
    conditions = {**answers.conditions, "DrugA": ConditionAnswer(id="DrugA", target="SRC")}
    return DraftAnswers(
        answers.base_sha256,
        answers.spec_commit,
        conditions,
        answers.technical_repeats,
        answers.repeats,
        answers.units,
        answers.channels,
        answers.masks,
    )


def _version_a_then_draft_b(exp, out):
    """Publish version A (description and design), then save draft B, whose design differs."""
    save_draft(out, draft_from_discovery(exp), complete())
    first = publish_draft(out, draft_from_discovery(exp), channel_counts=COUNTS)
    save_draft(out, draft_from_discovery(exp), _without_reference(first.sha256))
    return first


def _publish_b(exp, out):
    return publish_draft(out, draft_from_discovery(exp), channel_counts=COUNTS)


def _design_of(out):
    """The design derived from the published description, written outside ``out``."""
    published = read_description(out / DESCRIPTION_FILENAME)
    return write_derived_design(published, out.parent / "derived.csv").read_bytes()


def _fail_design_write(monkeypatch, fail):
    """Make the derived design's write raise ``OSError`` at the stage ``fail`` names.

    The design's temporary file is the only one whose name starts with ``.experiment_design``;
    its stream fails before or after its bytes, or its rename fails before or after replacing.
    """
    real_mkstemp, real_fdopen, real_replace = tempfile.mkstemp, os.fdopen, os.replace
    design_handles = set()

    def mkstemp(*args, prefix="", **kwargs):
        handle, name = real_mkstemp(*args, prefix=prefix, **kwargs)
        if prefix.startswith(f".{DESIGN_FILENAME}."):
            design_handles.add(handle)
        return handle, name

    class Stream:
        def __init__(self, stream):
            self._stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return self._stream.__exit__(*exc)

        def write(self, text):
            if fail == "before_bytes":
                raise OSError("disk full before any byte")
            self._stream.write(text)
            if fail == "after_bytes":
                raise OSError("disk full after the bytes")

        def __getattr__(self, name):
            return getattr(self._stream, name)

    def fdopen(handle, *args, **kwargs):
        stream = real_fdopen(handle, *args, **kwargs)
        if handle not in design_handles:
            return stream
        design_handles.discard(handle)  # a later file may be given the same descriptor
        return Stream(stream)

    def replace(source, target, *args, **kwargs):
        design = os.fspath(target).endswith(DESIGN_FILENAME)
        if fail == "rename" and design:
            raise OSError("rename failed")
        real_replace(source, target, *args, **kwargs)
        if fail == "after_rename" and design:
            raise OSError("failed after the rename")

    monkeypatch.setattr(io_module.tempfile, "mkstemp", mkstemp)
    monkeypatch.setattr(io_module.os, "fdopen", fdopen)
    monkeypatch.setattr(io_module.os, "replace", replace)


@pytest.mark.parametrize("fail", ["before_bytes", "after_bytes", "rename", "after_rename"])
def test_a_failed_design_write_leaves_the_previous_publication(exp, out, monkeypatch, fail):
    _version_a_then_draft_b(exp, out)
    before = snapshot(out)
    _fail_design_write(monkeypatch, fail)
    with pytest.raises(OSError):
        _publish_b(exp, out)
    monkeypatch.undo()
    assert snapshot(out) == before  # description, records, design and draft: version A
    assert not (out / LOCK_FILENAME).exists()
    assert load_draft(out, draft_from_discovery(exp)) is not None  # the draft still resumes
    _publish_b(exp, out)  # and is published as it is once the write succeeds
    assert (out / DESIGN_FILENAME).read_bytes() == _design_of(out) != before[DESIGN_FILENAME]
    assert not (out / DRAFT_FILENAME).exists()


def test_a_design_the_writer_refuses_leaves_the_previous_publication(exp, out, monkeypatch):
    _version_a_then_draft_b(exp, out)
    before = snapshot(out)

    def refused(design, path):
        raise DesignFormatError("The experiment design cannot be written: injected.")

    monkeypatch.setattr("morphobricks_preprocessing.design_derive.write_experiment_design", refused)
    with pytest.raises(DesignFormatError):
        _publish_b(exp, out)
    assert snapshot(out) == before and not (out / LOCK_FILENAME).exists()


@pytest.mark.parametrize("fail", ["append", "design"])
def test_a_publication_with_records_is_whole_or_absent(tmp_path, monkeypatch, fail):
    v01 = description_from_bytes(FIXTURES["v01"].data)
    base = publish_description(tmp_path, v01, base_sha256=ABSENT, write_design=True).sha256
    before = snapshot(tmp_path)
    if fail == "append":

        def broken(folder, records):
            raise OSError("disk full")

        monkeypatch.setattr(publication, "_append_records", broken)
    else:
        _fail_design_write(monkeypatch, "after_rename")
    t01 = description_from_bytes(FIXTURES["t01"].data)
    with pytest.raises(OSError):
        publish_description(
            tmp_path, t01, base_sha256=base, confirmations=RENAMES, write_design=True
        )
    assert snapshot(tmp_path) == before and RECORDS_FILENAME not in before
    monkeypatch.undo()
    publish_description(tmp_path, t01, base_sha256=base, confirmations=RENAMES, write_design=True)
    assert (tmp_path / RECORDS_FILENAME).exists()
    assert (tmp_path / DESIGN_FILENAME).read_bytes() == _design_of(tmp_path)


def test_a_failed_first_design_write_leaves_no_description(exp, out, monkeypatch):
    save_draft(out, draft_from_discovery(exp), complete())
    before = snapshot(out)
    _fail_design_write(monkeypatch, "rename")
    with pytest.raises(OSError):
        publish_draft(out, draft_from_discovery(exp), channel_counts=COUNTS)
    assert snapshot(out) == before == {DRAFT_FILENAME: before[DRAFT_FILENAME]}


def test_a_failed_draft_deletion_keeps_a_matching_pair(exp, out, monkeypatch):
    """The documented exception: the publication stands, description and design match."""
    _version_a_then_draft_b(exp, out)
    real_unlink = os.unlink

    def unlink(path, *args, **kwargs):
        if os.fspath(path).endswith(DRAFT_FILENAME):
            raise OSError("cannot delete the draft")
        real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "unlink", unlink)
    with pytest.raises(OSError, match="cannot delete"):
        _publish_b(exp, out)
    monkeypatch.undo()
    assert (out / DESIGN_FILENAME).read_bytes() == _design_of(out)
    assert (out / DRAFT_FILENAME).exists()  # refused as stale when resumed (documented)


def test_publish_description_writes_the_design_with_the_description(exp, out):
    candidate = description_from_draft(draft_from_discovery(exp), complete())
    publish_description(out, candidate, base_sha256="absent", write_design=True)
    derived = write_derived_design(candidate, out.parent / "derived.csv")
    assert (out / DESIGN_FILENAME).read_bytes() == derived.read_bytes()
    assert sorted(p.name for p in out.iterdir()) == [DESIGN_FILENAME, DESCRIPTION_FILENAME]


def test_publish_description_alone_never_writes_a_design(exp, out):
    candidate = description_from_draft(draft_from_discovery(exp), complete())
    publish_description(out, candidate, base_sha256="absent")
    assert [p.name for p in out.iterdir()] == [DESCRIPTION_FILENAME]


def test_a_symlinked_design_refuses_the_publication(exp, out, tmp_path):
    save_draft(out, draft_from_discovery(exp), complete())
    elsewhere = tmp_path / "elsewhere.csv"
    elsewhere.write_bytes(b"theirs\n")
    try:
        (out / DESIGN_FILENAME).symlink_to(elsewhere)
    except (OSError, NotImplementedError):
        pytest.skip("this system does not allow symbolic links")
    before = snapshot(out)
    with pytest.raises(PublicationRefused) as refusal:
        publish_draft(out, draft_from_discovery(exp), channel_counts=COUNTS)
    assert refusal.value.codes == {Refusal.R_FILE_SYMLINK}
    assert snapshot(out) == before and elsewhere.read_bytes() == b"theirs\n"
