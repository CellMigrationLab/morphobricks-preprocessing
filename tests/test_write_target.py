"""The shared write-target rule (``write_target_refusal``; owner, 3 November 2026).

``--log`` (``log_target``) applies it; other bricks (MorphoFeature's ``--to-segment``)
call it with their own protected folders and files instead of repeating the rule.
"""

import os

import pytest

import morphobricks_preprocessing as pp
from morphobricks_preprocessing.log_target import log_refusal
from morphobricks_preprocessing.write_target import same_file, write_target_refusal


@pytest.fixture
def tree(tmp_path):
    for folder in ("exp/ctrl", "masks", "out", "elsewhere"):
        (tmp_path / folder).mkdir(parents=True)
    (tmp_path / "exp/ctrl/a.tif").write_bytes(b"image")
    (tmp_path / "design.csv").write_text("design", encoding="utf-8")
    return tmp_path


def _refusal(tree, path, **more):
    folders = {"experiment folder": tree / "exp", "masks folder": None}
    return write_target_refusal(path, protected_folders=folders, **more)


def test_it_is_public():
    assert pp.write_target_refusal is write_target_refusal
    assert "write_target_refusal" in pp.__all__


def test_a_file_elsewhere_may_be_written(tree):
    assert _refusal(tree, tree / "elsewhere/new.csv") is None
    (tree / "elsewhere/old.csv").write_text("old", encoding="utf-8")
    assert _refusal(tree, tree / "elsewhere/old.csv") is None


def test_inside_a_protected_folder_by_resolved_path(tree):
    expected = f"it is inside the experiment folder ({(tree / 'exp').resolve()})"
    assert _refusal(tree, tree / "exp/ctrl/new.csv") == expected
    assert _refusal(tree, tree / "exp/ctrl/a.tif") == expected
    assert _refusal(tree, tree / "exp") == expected
    assert _refusal(tree, tree / "elsewhere/../exp/x.csv") == expected


def test_inside_a_protected_folder_through_a_link(tree):
    try:
        (tree / "elsewhere/link").symlink_to(tree / "exp", target_is_directory=True)
    except OSError:
        pytest.skip("this system cannot make symbolic links")
    assert "inside the experiment folder" in _refusal(tree, tree / "elsewhere/link/x.csv")


def test_inside_a_protected_folder_by_device_and_inode(tree, monkeypatch):
    # Another spelling of the folder that resolves elsewhere (case on macOS and Windows):
    # the folder is found above the path by its device and inode.
    other = tree / "elsewhere"
    spelling, real = os.fspath(other.resolve()), os.stat  # resolved before stat is patched

    def stat(path, *args, **kwargs):
        if os.fspath(path) == spelling:
            return real(tree / "exp")
        return real(path, *args, **kwargs)

    monkeypatch.setattr(os, "stat", stat)
    assert "inside the experiment folder" in _refusal(tree, other / "x.csv")


def test_a_protected_file_by_path_and_by_samefile(tree):
    files = {"design file given with --design": tree / "design.csv"}
    expected = f"it is the design file given with --design ({(tree / 'design.csv').resolve()})"
    assert _refusal(tree, tree / "design.csv", protected_files=files) == expected
    assert _refusal(tree, tree / "elsewhere/../design.csv", protected_files=files) == expected
    try:
        os.link(tree / "design.csv", tree / "elsewhere/second.csv")
    except OSError:
        pytest.skip("this file system cannot make hard links")
    assert _refusal(tree, tree / "elsewhere/second.csv", protected_files=files) == expected
    assert same_file(tree / "elsewhere/second.csv", tree / "design.csv")
    assert not same_file(tree / "missing.csv", tree / "design.csv")


def test_an_existing_folder_and_a_missing_parent(tree):
    assert _refusal(tree, tree / "elsewhere") == "it is a folder"
    missing = (tree / "nowhere").resolve()
    assert _refusal(tree, tree / "nowhere/x.csv") == f"its folder {missing} does not exist"


def test_a_hard_linked_existing_file(tree):
    try:
        os.link(tree / "exp/ctrl/a.tif", tree / "elsewhere/a.tif")
    except OSError:
        pytest.skip("this file system cannot make hard links")
    expected = "it is a file that has another name too (a hard link)"
    assert _refusal(tree, tree / "elsewhere/a.tif") == expected


def test_only_the_output_names_and_the_output_folder_are_refused(tree):
    out = tree / "out"
    more = {"output": out, "output_names": ("results.csv", ".staging")}
    found = _refusal(tree, out / "RESULTS.csv", **more)
    assert found == "it would write over the run's RESULTS.csv"
    assert _refusal(tree, out / ".staging/x", **more) == "it would write over the run's .staging"
    assert _refusal(tree, out, **more) == "it would write over the run's folder"
    assert _refusal(tree, out / "run.log", **more) is None


def test_the_order_of_the_checks(tree):
    # A protected file first, then protected folders, then the output's names.
    files = {"design file": tree / "exp/ctrl/a.tif"}
    found = _refusal(tree, tree / "exp/ctrl/a.tif", protected_files=files)
    assert found.startswith("it is the design file")
    found = _refusal(tree, tree / "exp/x", output=tree / "exp", output_names=("x",))
    assert found.startswith("it is inside the experiment folder")


def test_a_brick_protects_its_own_output_folder(tree):
    # MorphoFeature's use: its run folder protected whole, beside the preprocessing folders.
    folders = {"preprocessing run": tree / "out", "MorphoFeature output folder": tree / "mf"}
    (tree / "mf").mkdir()
    found = write_target_refusal(tree / "mf/to_segment.csv", protected_folders=folders)
    assert found == f"it is inside the MorphoFeature output folder ({(tree / 'mf').resolve()})"
    assert write_target_refusal(tree / "elsewhere/x.csv", protected_folders=folders) is None


def test_log_refusal_words_the_shared_reason(tree):
    found = log_refusal(tree / "exp/x.log", {"experiment folder": tree / "exp"})
    assert found == (
        f"The log {tree / 'exp/x.log'} was not opened: it is inside the experiment folder "
        f"({(tree / 'exp').resolve()}). Nothing was written. Give --log another file, outside "
        "the experiment and masks folders, for example run.log in the output folder."
    )


def test_a_lone_output_name_is_refused(tree):
    # Review of #147: a string is a collection of letters; "r" would have been protected.
    with pytest.raises(TypeError, match="not one name"):
        _refusal(tree, tree / "out/r", output=tree / "out", output_names="results.csv")
