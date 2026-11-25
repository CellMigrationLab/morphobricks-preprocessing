"""A folder of an OME-Zarr store that cannot be listed is an error, never skipped (#392)."""

import os

import numpy as np
import pytest

from morphobricks_preprocessing import links
from morphobricks_preprocessing.discovery import discover_experiment

pytest.importorskip("zarr")


@pytest.fixture
def store(tmp_path):
    from zarr_tree import write_ngff

    root = tmp_path / "exp"
    store = write_ngff(root / "ctrl/rep1/z.ome.zarr", np.zeros((1, 1, 4, 4), np.uint8))
    nested = next(p for p in sorted(store.rglob("*")) if p.is_dir())
    return root, nested


def codes(issues):
    return sorted(issue.code for issue in issues if issue.is_error)


def test_a_store_folder_that_cannot_be_listed_is_refused(store, monkeypatch):
    root, nested = store
    assert discover_experiment(root).ok
    listing = os.scandir

    def refuse(path="."):
        if os.fspath(path) == os.fspath(nested):
            raise PermissionError(13, "Permission denied", os.fspath(path))
        return listing(path)

    monkeypatch.setattr(links.os, "scandir", refuse)
    found = discover_experiment(root)
    assert codes(found.issues) == ["store_unreadable"]
    (issue,) = found.issues
    assert issue.paths == (nested.relative_to(root).as_posix(),)
    assert not found.ok
    with pytest.raises(links.LinkRefusedError, match="store_unreadable|could not be listed"):
        links.refuse_links(found.issues)


@pytest.mark.skipif(os.name != "posix" or os.geteuid() == 0, reason="POSIX permissions, not root")
def test_a_store_folder_without_permissions_is_refused(store):
    root, nested = store
    nested.chmod(0)
    try:
        assert "store_unreadable" in codes(discover_experiment(root).issues)
    finally:
        nested.chmod(0o755)
