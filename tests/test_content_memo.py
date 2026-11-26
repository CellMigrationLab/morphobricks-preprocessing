"""One press's content SHA-256s, reused only while the file's identity is unchanged (#578 TODO 2).

Owner decision on #578: within one press of **Register the masks**, a SHA-256 already taken of a
file is reused when its identity (device, inode, size, modification time; here also the
status-change time) is unchanged since it was hashed; any doubt means hashing again, and the
checks at publication never reuse one.
"""

import hashlib
import os

from morphobricks_preprocessing import content_memo
from morphobricks_preprocessing.content_memo import ContentMemo, file_identity
from morphobricks_preprocessing.manifest import FileEntry
from morphobricks_preprocessing.source_content import content_changed, with_content


def entry(name, sha256=None):
    return FileEntry(
        relative_path=name, size=0, mtime_ns=0, labels={}, axes="YX", content_sha256=sha256
    )


def sha(data):
    return hashlib.sha256(data).hexdigest()


def counting(calls):
    def compute(path):
        calls.append(path.name)
        return sha(path.read_bytes())

    return compute


def test_an_unchanged_file_is_hashed_once_per_memo(tmp_path):
    image = tmp_path / "a.tif"
    image.write_bytes(b"pixels")
    memo, calls = ContentMemo(), []
    assert memo.sha256(image, counting(calls)) == sha(b"pixels")
    assert memo.sha256(image, counting(calls)) == sha(b"pixels")
    assert calls == ["a.tif"] and (memo.hashed, memo.reused) == (1, 1)
    other, again = ContentMemo(), []  # another press: hashed again
    other.sha256(image, counting(again))
    assert again == ["a.tif"]


def test_a_file_rewritten_with_the_same_size_and_mtime_is_hashed_again(tmp_path):
    # Mutated between two checks of one press, keeping its size and modification time: the
    # status-change time moved (os.utime cannot set it back), so the SHA-256 is taken again.
    image = tmp_path / "a.tif"
    image.write_bytes(b"pixels")
    stat = image.stat()
    memo, calls = ContentMemo(), []
    memo.sha256(image, counting(calls))
    image.write_bytes(b"PIXELS")
    os.utime(image, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert image.stat().st_size == stat.st_size and image.stat().st_mtime_ns == stat.st_mtime_ns
    assert memo.sha256(image, counting(calls)) == sha(b"PIXELS")
    assert calls == ["a.tif", "a.tif"]


def test_a_file_replaced_by_another_inode_is_hashed_again(tmp_path):
    image = tmp_path / "a.tif"
    image.write_bytes(b"pixels")
    memo, calls = ContentMemo(), []
    memo.sha256(image, counting(calls))
    replacement = tmp_path / "b.tif"
    replacement.write_bytes(b"pixels")
    os.replace(replacement, image)
    memo.sha256(image, counting(calls))
    assert len(calls) == 2


def test_a_file_that_moves_while_it_is_hashed_is_not_remembered(tmp_path):
    image = tmp_path / "a.tif"
    image.write_bytes(b"pixels")
    memo, calls = ContentMemo(), []

    def touching(path):
        calls.append(path.name)
        path.write_bytes(b"pixel2")
        return sha(b"pixels")

    memo.sha256(image, touching)
    memo.sha256(image, counting(calls))
    assert calls == ["a.tif", "a.tif"]


def test_a_folder_is_never_remembered(tmp_path):
    store = tmp_path / "image.zarr"
    store.mkdir()
    assert file_identity(store) is None and file_identity(tmp_path / "gone") is None
    memo, calls = ContentMemo(), []
    memo.sha256(store, lambda path: calls.append(path) or "0" * 64)
    memo.sha256(store, lambda path: calls.append(path) or "0" * 64)
    assert len(calls) == 2


def test_the_identity_rule_cannot_see_what_keeps_every_value(tmp_path, monkeypatch):
    # Documented limit: a file system that keeps no status-change time (or a rewrite that keeps
    # all five values) is not seen by the memo. The run's check before it publishes never uses
    # a memo, so such a change is still caught there (see test_mask_register_progress.py).
    image = tmp_path / "a.tif"
    image.write_bytes(b"pixels")
    frozen = file_identity(image)
    monkeypatch.setattr(content_memo, "file_identity", lambda path: frozen)
    memo = ContentMemo()
    memo.sha256(image, counting([]))
    image.write_bytes(b"PIXELS")
    assert memo.sha256(image, counting([])) == sha(b"pixels")  # the limit, as documented
    assert content_changed([entry("a.tif", sha(b"pixels"))], tmp_path) == ["a.tif"]  # caught


def test_with_content_and_content_changed_use_the_memo(tmp_path):
    for name in ("a.tif", "b.tif"):
        (tmp_path / name).write_bytes(name.encode())
    entries = {name: entry(name) for name in ("a.tif", "b.tif")}
    memo = ContentMemo()
    found, unreadable = with_content(entries, tmp_path, memo=memo)
    assert unreadable == [] and memo.hashed == 2
    assert content_changed(found.values(), tmp_path, memo) == []
    assert (memo.hashed, memo.reused) == (2, 2)
