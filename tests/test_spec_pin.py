"""Pin integrity (conformance plan sections 1 and 2): the vendored Spec files hash to the pin.

The fixtures are pinned by the full Spec commit SHA. The vendored ``fixtures/`` folder must
hash to ``git rev-parse <sha>:fixtures`` (git's tree algorithm), and the two other vendored
files to their blob IDs, so one changed, added or missing byte fails a test and a local edit
of a fixture cannot make a reader pass.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

from spec_pin import (
    SpecPinError,
    git_blob_id,
    git_tree_id,
    pin_problems,
    read_pin,
    spec_root,
)


def test_vendored_files_hash_to_the_pin():
    assert pin_problems() == []


def test_the_pin_is_a_full_commit_sha_never_a_branch():
    pin = read_pin()
    assert re.fullmatch(r"[0-9a-f]{40}", pin["commit"])
    assert re.fullmatch(r"[0-9a-f]{40}", pin["fixtures_tree"])
    assert set(pin["files"]) == {
        "docs/experiment-v1.md",
        "tools/audit/refval.py",
        "tools/audit/refrecords.py",
    }
    assert all(re.fullmatch(r"[0-9a-f]{40}", blob) for blob in pin["files"].values())


def test_the_pinned_tree_is_the_folder_that_is_vendored():
    # The expected value is the pin's own: this fails when fixtures/ is not what it names.
    assert git_tree_id(spec_root() / "fixtures") == read_pin()["fixtures_tree"]


def test_git_blob_and_tree_ids_equal_git_s():
    # Known answers computed with git itself (git hash-object, git write-tree).
    assert git_blob_id(b"hello\n") == "ce013625030ba8dba906f756967f9e9ca394464a"


def test_git_tree_id_orders_a_folder_as_its_name_and_a_slash(tmp_path):
    # `a.b` sorts before the folder `a` (compared as `a/`), `a-b` before both: the order that
    # makes a hand-rolled tree hash differ from git's when it sorts by plain name.
    (tmp_path / "b").mkdir()
    (tmp_path / "a").mkdir()
    (tmp_path / "a.txt").write_bytes(b"hello\n")
    (tmp_path / "b" / "c").write_bytes(b"x")
    (tmp_path / "a.b").write_bytes(b"y")
    (tmp_path / "a-b").write_bytes(b"z")
    (tmp_path / "a" / "f").write_bytes(b"w")
    assert git_tree_id(tmp_path) == "fec6a54403e1c2a94724c1aab9f127c686f6dbb0"


def _copy(tmp_path: Path) -> Path:
    root = tmp_path / "spec"
    shutil.copytree(spec_root(), root)
    return root


def _flip_byte(path: Path) -> None:
    data = bytearray(path.read_bytes())
    data[len(data) // 2] ^= 0x01
    path.write_bytes(bytes(data))


def _flip_fixture(root: Path) -> None:
    _flip_byte(root / "fixtures" / "valid" / "v01_single_control.toml")


def _flip_catalogue(root: Path) -> None:
    _flip_byte(root / "fixtures" / "README.md")


def _add_fixture(root: Path) -> None:
    (root / "fixtures" / "valid" / "v99_added.toml").write_bytes(b"# added\n")


def _remove_fixture(root: Path) -> None:
    (root / "fixtures" / "invalid" / "i05_duplicate_field.toml").unlink()


def _rename_fixture(root: Path) -> None:
    folder = root / "fixtures" / "valid"
    (folder / "v02_multiple_controls.toml").rename(folder / "v02_renamed.toml")


def _crlf_fixture(root: Path) -> None:
    path = root / "fixtures" / "valid" / "v03_paired_different_folder_names.toml"
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))


def _flip_text(root: Path) -> None:
    _flip_byte(root / "docs" / "experiment-v1.md")


def _flip_oracle(root: Path) -> None:
    _flip_byte(root / "tools" / "audit" / "refval.py")


def _remove_oracle(root: Path) -> None:
    (root / "tools" / "audit" / "refval.py").unlink()


MUTATIONS = [
    _flip_fixture,
    _flip_catalogue,
    _add_fixture,
    _remove_fixture,
    _rename_fixture,
    _crlf_fixture,
    _flip_text,
    _flip_oracle,
    _remove_oracle,
]


@pytest.mark.parametrize("mutate", MUTATIONS, ids=lambda f: f.__name__.lstrip("_"))
def test_one_changed_added_or_missing_byte_fails_the_pin(tmp_path, mutate):
    root = _copy(tmp_path)
    assert pin_problems(root) == []
    mutate(root)
    assert pin_problems(root) != []


def test_a_symbolic_link_in_the_fixtures_is_refused(tmp_path):
    root = _copy(tmp_path)
    link = root / "fixtures" / "valid" / "v98_link.toml"
    try:
        link.symlink_to(root / "fixtures" / "valid" / "v01_single_control.toml")
    except OSError as error:  # a file system without links: nothing to check
        pytest.fail(f"cannot create a link to test the refusal: {error}")
    with pytest.raises(SpecPinError, match="symbolic link"):
        pin_problems(root)


def test_a_pin_that_is_not_a_full_sha_is_reported(tmp_path):
    root = _copy(tmp_path)
    pin = root / "PIN.toml"
    pin.write_text(pin.read_text(encoding="utf-8").replace(read_pin()["commit"], "main"))
    assert any("40-digit" in problem for problem in pin_problems(root))
