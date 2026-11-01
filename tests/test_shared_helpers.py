"""The helpers that replaced copies (plan-structure audit D1 and D7)."""

from __future__ import annotations

import os

import pytest

from morphobricks_preprocessing._io import write_text_atomic
from morphobricks_preprocessing.validation import agree


def test_write_text_atomic_writes_lf_and_replaces(tmp_path):
    path = write_text_atomic(tmp_path / "a" / "b.txt", "one\ntwo\n", parents=True)
    assert path.read_bytes() == b"one\ntwo\n"
    write_text_atomic(path, "three\n")
    assert path.read_text(encoding="utf-8") == "three\n"
    assert sorted(p.name for p in path.parent.iterdir()) == ["b.txt"]


def test_write_text_atomic_keeps_the_earlier_file_on_failure(tmp_path, monkeypatch):
    path = write_text_atomic(tmp_path / "b.txt", "earlier\n")

    def broken(source, destination):
        raise OSError("simulated")

    monkeypatch.setattr(os, "replace", broken)
    with pytest.raises(OSError, match="simulated"):
        write_text_atomic(path, "later\n")
    assert path.read_text(encoding="utf-8") == "earlier\n"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["b.txt"]


def test_write_text_atomic_needs_the_folder_unless_asked(tmp_path):
    with pytest.raises(FileNotFoundError):
        write_text_atomic(tmp_path / "missing" / "b.txt", "x")


def test_agree():
    assert agree(["a"], "is", "are") == "is"
    assert agree([], "is", "are") == "are"
    assert agree(["a", "b"], "has", "have") == "have"
