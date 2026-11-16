"""The verified code identity of a distribution (Spec ADR 0005 BM15; design §3, review w2 B1).

Small installations are written in ``tmp_path`` (a package, its ``.dist-info`` with ``METADATA``,
``RECORD`` and PEP 610 ``direct_url.json``), so every check (1)-(5) is driven on purpose: an
editable install, a missing ``RECORD``, a file whose hash differs, a ``.py`` file added but not in
``RECORD``, one listed without a hash, and a source tree earlier on ``sys.path``. Two builds of
one commit with different ``RECORD`` bytes are two identities. The installed normalization (a
non-editable VCS install) is checked for real.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import sys
from importlib import metadata

import pytest

from code_identity_tree import COMMIT, FILES, install, modules_of
from morphobricks_preprocessing.code_identity import code_identity
from morphobricks_preprocessing.lineage_schema import IDENTITY_KEYS


def test_a_verified_install_records_its_record_digest_and_commit(tmp_path):
    dist = install(tmp_path)
    found = code_identity(dist, modules_of(tmp_path))
    assert tuple(found) == IDENTITY_KEYS
    record = (tmp_path / "fakepkg-1.0.dist-info" / "RECORD").read_bytes()
    assert found == {
        "distribution": "fakepkg",
        "version": "1.0",
        "verified": True,
        "record_sha256": hashlib.sha256(record).hexdigest(),
        "vcs_commit": COMMIT,
        "reason": None,
    }


def test_an_editable_install_is_not_verified(tmp_path):
    found = code_identity(install(tmp_path, editable=True), modules_of(tmp_path))
    assert (found["verified"], found["reason"], found["vcs_commit"]) == (
        False,
        "editable install",
        None,
    )


def test_a_missing_record_is_not_verified(tmp_path):
    dist = install(tmp_path)
    (tmp_path / "fakepkg-1.0.dist-info" / "RECORD").unlink()
    found = code_identity(dist, modules_of(tmp_path))
    assert (found["verified"], found["reason"], found["record_sha256"]) == (
        False,
        "RECORD missing",
        None,
    )


def test_a_file_whose_hash_differs_is_not_verified(tmp_path):
    dist = install(tmp_path)
    (tmp_path / "fakepkg" / "mod.py").write_bytes(b"Y = 3\n")
    found = code_identity(dist, modules_of(tmp_path))
    assert (found["verified"], found["reason"]) == (False, "hash differs: fakepkg/mod.py")
    assert found["record_sha256"] is not None  # recorded as found


def test_a_py_file_added_to_the_package_is_not_verified(tmp_path):
    dist = install(tmp_path)
    (tmp_path / "fakepkg" / "added.py").write_bytes(b"Z = 1\n")
    found = code_identity(dist, modules_of(tmp_path))
    assert (found["verified"], found["reason"]) == (False, "not in RECORD: fakepkg/added.py")


def test_an_extension_module_added_to_the_package_is_not_verified(tmp_path):
    dist = install(tmp_path)
    (tmp_path / "fakepkg" / "fast.so").write_bytes(b"\x7fELF")
    assert code_identity(dist, modules_of(tmp_path))["reason"] == "not in RECORD: fakepkg/fast.so"


def test_a_py_file_listed_without_a_hash_is_not_verified(tmp_path):
    dist = install(tmp_path, unhashed=("fakepkg/mod.py",))
    found = code_identity(dist, modules_of(tmp_path))
    assert (found["verified"], found["reason"]) == (False, "no hash in RECORD: fakepkg/mod.py")


def test_pycache_files_are_not_package_code(tmp_path):
    dist = install(tmp_path)
    (tmp_path / "fakepkg" / "__pycache__").mkdir()
    (tmp_path / "fakepkg" / "__pycache__" / "mod.cpython-313.py").write_bytes(b"")
    assert code_identity(dist, modules_of(tmp_path))["verified"]


def test_a_module_from_a_source_tree_earlier_on_the_path_is_not_verified(tmp_path, monkeypatch):
    dist = install(tmp_path / "site")
    tree = tmp_path / "tree"
    (tree / "fakepkg").mkdir(parents=True)
    (tree / "fakepkg" / "__init__.py").write_bytes(b"X = 1\n")
    monkeypatch.syspath_prepend(str(tmp_path / "site"))
    monkeypatch.syspath_prepend(str(tree))  # the source tree wins the import
    monkeypatch.delitem(sys.modules, "fakepkg", raising=False)
    importlib.invalidate_caches()
    imported = importlib.import_module("fakepkg")
    try:
        assert imported.__file__.startswith(str(tree))
        found = code_identity(dist)  # sys.modules, as the helper checks it
        assert (found["verified"], found["reason"]) == (
            False,
            "module not from this installation: fakepkg",
        )
    finally:
        sys.modules.pop("fakepkg", None)


def test_two_builds_of_one_commit_are_two_identities(tmp_path):
    first = code_identity(install(tmp_path / "a"), modules_of(tmp_path / "a"))
    rebuilt = {**FILES, "fakepkg/extra.py": b"W = 1\n"}
    second = code_identity(install(tmp_path / "b", rebuilt), modules_of(tmp_path / "b"))
    assert first["verified"] and second["verified"]
    assert first["vcs_commit"] == second["vcs_commit"] == COMMIT
    assert first["record_sha256"] != second["record_sha256"]
    assert first != second


def test_the_installed_normalization_is_identified_for_real():
    dist = metadata.distribution("morphobricks-normalization")
    direct = json.loads(dist.read_text("direct_url.json") or "{}")
    if direct.get("dir_info", {}).get("editable"):
        pytest.skip("normalization is an editable install here: it is never verified")
    import morphobricks_normalization.stain  # noqa: F401  (imported, as by the helper)

    found = code_identity("morphobricks-normalization")
    record = (dist._path / "RECORD").read_bytes()
    assert found["record_sha256"] == hashlib.sha256(record).hexdigest()
    assert found["vcs_commit"] == direct.get("vcs_info", {}).get("commit_id")
    assert found["verified"], found["reason"]
