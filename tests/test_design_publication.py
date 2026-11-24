"""The published description and its derived ``experiment_design.csv`` change together (#400).

A publication writes ``morphobricks.toml``, its whole new records file and the derived design
(experiment-v1 section 11) to temporary files, and only then renames them into place. A failed
write (a full disk) changes nothing published; a failed rename puts the previous description,
records file and design back byte for byte. A newly published description is never left with
the design of the previous one, and the draft stays resumable against an unchanged base.
"""

import errno
import os
import stat
import tempfile

import pytest

from draft_file_support import COUNTS, PATHS, complete, snapshot
from morphobricks_preprocessing import _io as io_module
from morphobricks_preprocessing import design as design_module
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
from test_spec_fast_mode_writer import FAST_ARGS, _plain_tree
from tiff_tree import make_tree

DAY = "2026-11-07"
RENAMES = (
    Reconfirmation("repeats", "rep1", "Day1", DAY),
    Reconfirmation("repeats", "rep2", "Day2", DAY),
)
SEEDED = (
    b'{"confirmed_on": "2026-01-01", "id_kind": "repeats", "new_id": "b", "new_sha256": "'
    + b"b" * 64
    + b'", "old_id": "a", "old_sha256": "'
    + b"a" * 64
    + b'", "type": "reconfirmation"}\n'
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


def _refuse_every_design(monkeypatch):
    """Make the design writer's own check refuse every design; return the temporary files made."""
    made = []
    real_mkstemp = tempfile.mkstemp

    def mkstemp(*args, **kwargs):
        made.append(kwargs.get("prefix"))
        return real_mkstemp(*args, **kwargs)

    monkeypatch.setattr(design_module, "_design_problems", lambda d: [(d.rows[0], "injected")])
    monkeypatch.setattr(io_module.tempfile, "mkstemp", mkstemp)
    return made


def _stats(folder):
    """Every file's bytes, inode and modification time: a rewrite with equal bytes shows."""
    return {
        p.name: (p.read_bytes(), p.stat().st_ino, p.stat().st_mtime_ns)
        for p in sorted(folder.iterdir())
    }


def test_a_design_the_writer_refuses_writes_nothing_at_all(exp, out, monkeypatch):
    _version_a_then_draft_b(exp, out)
    before = _stats(out)
    made = _refuse_every_design(monkeypatch)
    with pytest.raises(DesignFormatError, match="injected"):
        _publish_b(exp, out)
    assert made == []  # no temporary file was even created
    assert _stats(out) == before and not (out / LOCK_FILENAME).exists()
    monkeypatch.undo()
    assert load_draft(out, draft_from_discovery(exp)) is not None


def _disk_full_from_design(monkeypatch, at):
    """Fill the disk at the design: from its temporary file on, every file write fails ENOSPC.

    ``at`` is ``create`` (the design's temporary file cannot be created, nor any later one) or
    ``write`` (it is created, but no byte can be written to it or to any later file). The writes
    that would put the previous files back fail the same way.
    """
    real_mkstemp, real_fdopen = tempfile.mkstemp, os.fdopen
    full = []

    def no_space(*_):
        raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))

    def mkstemp(*args, prefix="", **kwargs):
        if prefix.startswith(f".{DESIGN_FILENAME}."):
            full.append(True)
        if full and at == "create":
            no_space()
        return real_mkstemp(*args, prefix=prefix, **kwargs)

    class Full:
        def __init__(self, stream):
            self._stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return self._stream.__exit__(*exc)

        write = no_space

        def __getattr__(self, name):
            return getattr(self._stream, name)

    def fdopen(handle, *args, **kwargs):
        stream = real_fdopen(handle, *args, **kwargs)
        return Full(stream) if full else stream

    monkeypatch.setattr(io_module.tempfile, "mkstemp", mkstemp)
    monkeypatch.setattr(io_module.os, "fdopen", fdopen)


@pytest.mark.parametrize("at", ["create", "write"])
def test_a_full_disk_at_the_design_leaves_version_a_and_no_temporary_file(
    exp, out, monkeypatch, at
):
    _version_a_then_draft_b(exp, out)
    before = snapshot(out)
    _disk_full_from_design(monkeypatch, at)
    with pytest.raises(OSError) as raised:
        _publish_b(exp, out)
    assert raised.value.errno == errno.ENOSPC
    monkeypatch.undo()
    assert snapshot(out) == before  # description, design, draft: version A; no .tmp left
    _publish_b(exp, out)  # the draft is published as it is once there is space
    assert (out / DESIGN_FILENAME).read_bytes() == _design_of(out) != before[DESIGN_FILENAME]


@pytest.mark.parametrize("at", ["create", "write"])
def test_a_full_disk_at_the_design_leaves_the_records_as_they_were(tmp_path, monkeypatch, at):
    v01 = description_from_bytes(FIXTURES["v01"].data)
    base = publish_description(tmp_path, v01, base_sha256=ABSENT, write_design=True).sha256
    (tmp_path / RECORDS_FILENAME).write_bytes(SEEDED)  # the records of earlier publications
    before = snapshot(tmp_path)
    _disk_full_from_design(monkeypatch, at)
    t01 = description_from_bytes(FIXTURES["t01"].data)
    with pytest.raises(OSError) as raised:
        publish_description(
            tmp_path, t01, base_sha256=base, confirmations=RENAMES, write_design=True
        )
    assert raised.value.errno == errno.ENOSPC
    monkeypatch.undo()
    assert snapshot(tmp_path) == before  # description, records and design as they were
    publish_description(tmp_path, t01, base_sha256=base, confirmations=RENAMES, write_design=True)
    assert (tmp_path / RECORDS_FILENAME).read_bytes().startswith(SEEDED)


def test_a_design_that_is_not_utf8_is_put_back_byte_for_byte(exp, out, monkeypatch):
    _version_a_then_draft_b(exp, out)
    (out / DESIGN_FILENAME).write_bytes(b"condition;\xff\xfe edited elsewhere\r\n")
    if os.name == "posix":
        (out / DESIGN_FILENAME).chmod(0o600)
    mode = stat.S_IMODE((out / DESIGN_FILENAME).stat().st_mode)
    before = snapshot(out)
    _fail_design_write(monkeypatch, "after_rename")  # renamed, then the failure: put back
    with pytest.raises(OSError, match="after the rename"):
        _publish_b(exp, out)
    monkeypatch.undo()
    assert snapshot(out) == before
    assert stat.S_IMODE((out / DESIGN_FILENAME).stat().st_mode) == mode


def test_a_put_back_that_fails_is_noted_and_the_others_are_still_put_back(exp, out, monkeypatch):
    _version_a_then_draft_b(exp, out)
    before = snapshot(out)
    _fail_design_write(monkeypatch, "after_rename")
    real = publication._replace_file_durably

    def put_back(path, data):
        if path.name == DESIGN_FILENAME:
            raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))
        real(path, data)

    monkeypatch.setattr(publication, "_replace_file_durably", put_back)
    with pytest.raises(OSError, match="after the rename") as raised:
        _publish_b(exp, out)
    monkeypatch.undo()
    assert any(DESIGN_FILENAME in note for note in raised.value.__notes__)
    after = snapshot(out)  # the documented limit: the design could not be put back
    assert after[DESIGN_FILENAME] != before[DESIGN_FILENAME]
    assert {**after, DESIGN_FILENAME: before[DESIGN_FILENAME]} == before


def test_a_failed_first_rename_touches_no_published_file(exp, out, monkeypatch):
    """A file that was not renamed is not put back either: it is not even rewritten."""
    _version_a_then_draft_b(exp, out)
    before = _stats(out)
    real = os.replace

    def replace(source, target, *args, **kwargs):
        if os.fspath(target).endswith(DESCRIPTION_FILENAME):
            raise OSError("rename failed")
        real(source, target, *args, **kwargs)

    monkeypatch.setattr(io_module.os, "replace", replace)
    with pytest.raises(OSError, match="rename failed"):
        _publish_b(exp, out)
    monkeypatch.undo()
    assert _stats(out) == before


def test_describe_publish_refused_design_is_not_published_and_writes_nothing(
    tmp_path, monkeypatch, capsys
):
    from morphobricks_preprocessing.cli import main

    root, out = _plain_tree(tmp_path / "exp"), tmp_path / "out"
    made = _refuse_every_design(monkeypatch)
    assert main(["describe", str(root), "--output", str(out), *FAST_ARGS]) == 1
    assert "Not published: The experiment design cannot be written" in capsys.readouterr().out
    assert made == [] and list(out.iterdir()) == []


def test_describe_publish_on_a_full_disk_raises_and_publishes_nothing(tmp_path, monkeypatch):
    """A failed write is not a refusal: the error is raised, as for any other write."""
    from morphobricks_preprocessing.cli import main

    root, out = _plain_tree(tmp_path / "exp"), tmp_path / "out"
    _disk_full_from_design(monkeypatch, "write")
    with pytest.raises(OSError) as raised:
        main(["describe", str(root), "--output", str(out), *FAST_ARGS])
    assert raised.value.errno == errno.ENOSPC
    assert list(out.iterdir()) == []


@pytest.mark.parametrize("fail", ["append", "design"])
def test_a_publication_with_records_is_whole_or_absent(tmp_path, monkeypatch, fail):
    v01 = description_from_bytes(FIXTURES["v01"].data)
    base = publish_description(tmp_path, v01, base_sha256=ABSENT, write_design=True).sha256
    before = snapshot(tmp_path)
    if fail == "append":
        real = publication._prepare_file

        def broken(path, data):
            if path.name == RECORDS_FILENAME:
                raise OSError("disk full")
            return real(path, data)

        monkeypatch.setattr(publication, "_prepare_file", broken)
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
