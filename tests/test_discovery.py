import os

import pytest

from morphobricks_preprocessing import (
    SINGLE,
    Severity,
    check_output_folder,
    default_output_folder,
    discover_experiment,
    position_id,
)
from morphobricks_preprocessing.discovery import is_system_name, is_tiff_name
from tiff_tree import make_tree


def codes(result):
    return [issue.code for issue in result.issues]


def issue(result, code):
    (found,) = [i for i in result.issues if i.code == code]
    return found


def leaf_labels(result):
    return {leaf.relative_path: leaf.labels for leaf in result.leaves}


# Layouts ------------------------------------------------------------------------------


def test_full_hierarchy(tmp_path):
    make_tree(
        tmp_path,
        [
            "control/bio_rep1/tech_rep1/a.tif",
            "control/bio_rep1/tech_rep1/b.tif",
            "control/bio_rep1/tech_rep2/a.tif",
            "treated/bio_rep1/tech_rep1/a.tif",
        ],
    )
    result = discover_experiment(tmp_path)
    assert result.ok and result.issues == ()
    assert leaf_labels(result) == {
        "control/bio_rep1/tech_rep1": ("control", "bio_rep1", "tech_rep1"),
        "control/bio_rep1/tech_rep2": ("control", "bio_rep1", "tech_rep2"),
        "treated/bio_rep1/tech_rep1": ("treated", "bio_rep1", "tech_rep1"),
    }
    assert result.leaves[0].images == (
        "control/bio_rep1/tech_rep1/a.tif",
        "control/bio_rep1/tech_rep1/b.tif",
    )
    assert result.images == (
        "control/bio_rep1/tech_rep1/a.tif",
        "control/bio_rep1/tech_rep1/b.tif",
        "control/bio_rep1/tech_rep2/a.tif",
        "treated/bio_rep1/tech_rep1/a.tif",
    )


def test_root_is_the_leaf(tmp_path):
    make_tree(tmp_path, ["a.tif", "b.tiff"])
    result = discover_experiment(tmp_path)
    assert result.ok
    (leaf,) = result.leaves
    assert leaf.relative_path == "."
    assert leaf.labels == (SINGLE, SINGLE, SINGLE)
    assert result.images == ("a.tif", "b.tiff")


@pytest.mark.parametrize(
    ("image", "labels"),
    [
        ("control/a.tif", ("control", SINGLE, SINGLE)),
        ("control/bio_rep1/a.tif", ("control", "bio_rep1", SINGLE)),
        ("control/bio_rep1/tech_rep1/a.tif", ("control", "bio_rep1", "tech_rep1")),
    ],
)
def test_branches_stopping_early_get_single(tmp_path, image, labels):
    make_tree(tmp_path, [image])
    result = discover_experiment(tmp_path)
    assert result.ok
    assert result.labels_for(image) == labels


def test_branches_of_different_depths_in_one_experiment(tmp_path):
    make_tree(
        tmp_path,
        [
            "control/a.tif",
            "drug/bio_rep1/a.tif",
            "drug/bio_rep2/tech_rep1/a.tif",
        ],
    )
    result = discover_experiment(tmp_path)
    assert result.ok
    assert leaf_labels(result) == {
        "control": ("control", SINGLE, SINGLE),
        "drug/bio_rep1": ("drug", "bio_rep1", SINGLE),
        "drug/bio_rep2/tech_rep1": ("drug", "bio_rep2", "tech_rep1"),
    }


def test_labels_are_scoped_to_their_parents(tmp_path):
    make_tree(tmp_path, ["control/bio_rep1/a.tif", "treated/bio_rep1/a.tif"])
    result = discover_experiment(tmp_path)
    assert [leaf.labels for leaf in result.leaves] == [
        ("control", "bio_rep1", SINGLE),
        ("treated", "bio_rep1", SINGLE),
    ]


def test_order_is_sorted_and_repeatable(tmp_path):
    make_tree(tmp_path, ["b/z.tif", "b/a.tif", "a/m.tif", "C/x.tif"])
    first = discover_experiment(tmp_path)
    assert first.images == ("C/x.tif", "a/m.tif", "b/a.tif", "b/z.tif")
    assert discover_experiment(tmp_path) == first


def test_tiff_extensions_are_case_insensitive(tmp_path):
    make_tree(tmp_path, ["cond/A.TIF", "cond/b.Tiff", "cond/c.ome.tif"])
    result = discover_experiment(tmp_path)
    assert result.ok and result.issues == ()
    assert result.images == ("cond/A.TIF", "cond/b.Tiff", "cond/c.ome.tif")


def test_accepts_relative_paths_and_returns_absolute_root(tmp_path, monkeypatch):
    make_tree(tmp_path, ["exp/cond/a.tif"])
    monkeypatch.chdir(tmp_path)
    result = discover_experiment("exp")
    assert result.root == (tmp_path / "exp").resolve()
    assert result.images == ("cond/a.tif",)


# Warnings -----------------------------------------------------------------------------


def test_non_tiff_files_are_warned_and_skipped(tmp_path):
    make_tree(tmp_path, ["cond/a.tif", "cond/notes.txt", "cond/image.png", "readme.md"])
    result = discover_experiment(tmp_path)
    assert result.ok
    assert codes(result) == ["non_tiff_file"]
    found = issue(result, "non_tiff_file")
    assert found.severity is Severity.WARNING
    assert found.paths == ("cond/image.png", "cond/notes.txt", "readme.md")
    assert "3 files are not images (TIFF or OME-Zarr)" in found.message
    assert result.images == ("cond/a.tif",)


def test_empty_folders_are_warned(tmp_path):
    make_tree(tmp_path, ["cond/a.tif", "empty/", "cond2/bio/", "only_notes/notes.txt"])
    result = discover_experiment(tmp_path)
    assert result.ok
    found = issue(result, "empty_folder")
    assert found.severity is Severity.WARNING
    assert found.paths == ("cond2/bio", "empty", "only_notes")
    assert result.images == ("cond/a.tif",)


def test_tiffs_outside_leaf_folders_are_warned_and_skipped(tmp_path):
    make_tree(tmp_path, ["stray.tif", "cond/bio/b.tif", "cond/bio/tech/a.tif"])
    result = discover_experiment(tmp_path)
    assert result.ok
    found = issue(result, "tiff_outside_leaf")
    assert found.severity is Severity.WARNING
    assert found.paths == ("cond/bio/b.tif", "stray.tif")
    assert result.images == ("cond/bio/tech/a.tif",)


def test_system_files_are_ignored_silently(tmp_path):
    make_tree(
        tmp_path,
        [
            ".DS_Store",
            "Thumbs.db",
            "desktop.ini",
            "__MACOSX/cond/._a.tif",
            ".ipynb_checkpoints/x.tif",
            "cond/a.tif",
            "cond/._a.tif",
            "cond/.DS_Store",
            "cond/.hidden/b.tif",
        ],
    )
    result = discover_experiment(tmp_path)
    assert result.issues == ()
    assert leaf_labels(result) == {"cond": ("cond", SINGLE, SINGLE)}
    assert result.images == ("cond/a.tif",)


def test_folder_with_only_system_files_is_empty(tmp_path):
    make_tree(tmp_path, ["cond/a.tif", "other/.DS_Store", "other/Thumbs.db"])
    assert issue(discover_experiment(tmp_path), "empty_folder").paths == ("other",)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        (".DS_Store", True),
        ("._img.tif", True),
        ("Thumbs.db", True),
        ("THUMBS.DB", True),
        ("desktop.ini", True),
        ("__MACOSX", True),
        (".ipynb_checkpoints", True),
        ("img.tif", False),
        ("control", False),
    ],
)
def test_is_system_name(name, expected):
    assert is_system_name(name) is expected


@pytest.mark.parametrize(
    ("name", "expected"),
    [("a.tif", True), ("a.TIFF", True), ("a.ome.tif", True), ("a.png", False), ("tif", False)],
)
def test_is_tiff_name(name, expected):
    assert is_tiff_name(name) is expected


# Folders named single (the MorphoBricks one-member level) -----------------------------


def test_folder_named_single_is_a_level_with_one_member(tmp_path):
    # Shaped like an experiment whose conditions have one biological repeat, written
    # as a folder named single: <condition>/single/<images>.
    make_tree(
        tmp_path,
        [
            "control/single/img_01.tif",
            "control/single/img_02.tif",
            "treated/single/img_01.tif",
            "treated/single/img_02.tif",
        ],
    )
    result = discover_experiment(tmp_path)
    assert result.ok and result.issues == ()
    assert leaf_labels(result) == {
        "control/single": ("control", SINGLE, SINGLE),
        "treated/single": ("treated", SINGLE, SINGLE),
    }
    assert result.labels_for("treated/single/img_02.tif") == ("treated", SINGLE, SINGLE)


def test_folder_named_single_gives_the_labels_of_a_missing_level(tmp_path):
    # The same labels as images placed directly in the condition folder.
    named = discover_experiment(make_tree(tmp_path / "named", ["control/single/a.tif"]))
    missing = discover_experiment(make_tree(tmp_path / "missing", ["control/a.tif"]))
    assert named.ok and missing.ok
    assert named.leaves[0].labels == missing.leaves[0].labels == ("control", SINGLE, SINGLE)


@pytest.mark.parametrize(
    ("tree", "labels"),
    [
        (["single/rep1/a.tif"], ("single", "rep1", SINGLE)),
        (["control/single/tech_1/a.tif"], ("control", SINGLE, "tech_1")),
        (["control/rep1/single/a.tif"], ("control", "rep1", SINGLE)),
        (["single/single/single/a.tif"], (SINGLE, SINGLE, SINGLE)),
    ],
)
def test_folder_named_single_at_any_level(tmp_path, tree, labels):
    result = discover_experiment(make_tree(tmp_path, tree))
    assert result.ok and result.issues == ()
    assert [leaf.labels for leaf in result.leaves] == [labels]


def test_folder_named_single_ignores_system_folders_beside_it(tmp_path):
    make_tree(tmp_path, ["control/single/a.tif", "control/.ipynb_checkpoints/x.txt"])
    (tmp_path / "control" / "__MACOSX").mkdir()
    result = discover_experiment(tmp_path)
    assert result.ok and result.issues == ()
    assert leaf_labels(result) == {"control/single": ("control", SINGLE, SINGLE)}


# Errors -------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["Single", "SINGLE"])
def test_folder_named_single_in_another_case_is_an_error(tmp_path, name):
    # "Single" would be a different label from the "single" written for one-member
    # levels, so only the lower-case spelling stands for that value.
    make_tree(tmp_path, [f"control/{name}/a.tif", "treated/a.tif"])
    result = discover_experiment(tmp_path)
    assert not result.ok
    found = issue(result, "reserved_folder_name")
    assert found.severity is Severity.ERROR
    assert found.paths == (f"control/{name}",)
    assert "lower case" in found.action


def test_folder_named_single_beside_other_folders_is_an_error(tmp_path):
    # "single" says the level has one member; beside rep2 it would have two.
    make_tree(tmp_path, ["control/single/a.tif", "control/rep2/a.tif", "treated/a.tif"])
    result = discover_experiment(tmp_path)
    assert not result.ok
    found = issue(result, "reserved_folder_name")
    assert found.severity is Severity.ERROR
    assert found.paths == ("control/single",)
    assert "only folder" in found.message


def test_folder_named_single_with_an_empty_sibling_is_an_error(tmp_path):
    make_tree(tmp_path, ["control/single/a.tif", "control/rep2/"])
    result = discover_experiment(tmp_path)
    assert issue(result, "reserved_folder_name").paths == ("control/single",)


def test_folders_differing_only_in_case_are_an_error(tmp_path):
    # Created one level apart so the test also works on case-insensitive file systems.
    (tmp_path / "Ctrl").mkdir()
    try:
        (tmp_path / "ctrl").mkdir()
    except FileExistsError:
        pytest.skip("the file system does not distinguish upper and lower case")
    make_tree(tmp_path, ["Ctrl/a.tif", "ctrl/a.tif"])
    result = discover_experiment(tmp_path)
    assert not result.ok
    assert issue(result, "case_conflict").paths == ("Ctrl", "ctrl")


def test_same_names_under_different_parents_are_fine(tmp_path):
    make_tree(tmp_path, ["a/Rep1/x.tif", "b/rep1/x.tif"])
    assert discover_experiment(tmp_path).issues == ()


def test_folders_below_technical_repeat_are_an_error(tmp_path):
    make_tree(
        tmp_path,
        [
            "cond/bio/tech/extra/a.tif",
            "cond/bio/tech/extra/deeper/b.tif",
            "cond/bio/tech2/a.tif",
        ],
    )
    result = discover_experiment(tmp_path)
    assert not result.ok
    found = issue(result, "folder_too_deep")
    assert found.severity is Severity.ERROR
    assert found.paths == ("cond/bio/tech/extra",)
    assert result.images == ("cond/bio/tech2/a.tif",)


# Positions (cross-brick decision D02) -------------------------------------------------


@pytest.mark.parametrize(
    ("image", "expected"),
    [
        ("a.tif", "a"),
        ("ctrl/rep1/tech1/img_01.tiff", "img_01"),
        ("ctrl/img_01.ome.tif", "img_01"),
        ("IMG_01.OME.TIFF", "IMG_01"),
        ("ctrl/img_01.ome.zarr", "img_01"),
        ("ctrl/img_01.zarr", "img_01"),
        ("ctrl\\001.tif", "001"),
        ("a.b.tif", "a.b"),
        ("ome.tif", "ome"),
    ],
)
def test_position_id_is_the_image_name_without_extension(image, expected):
    assert position_id(image) == expected


def test_equal_names_in_different_technical_repeats_are_fine(tmp_path):
    make_tree(tmp_path, ["ctrl/bio1/tech1/img_01.tif", "ctrl/bio1/tech2/img_01.tif"])
    result = discover_experiment(tmp_path)
    assert result.issues == ()
    keys = {(*leaf.labels, position_id(image)) for leaf in result.leaves for image in leaf.images}
    assert keys == {("ctrl", "bio1", "tech1", "img_01"), ("ctrl", "bio1", "tech2", "img_01")}


@pytest.mark.parametrize(
    "names",
    [
        ("img_01.tif", "img_01.ome.zarr/"),
        ("img_01.tif", "img_01.tiff"),
        ("img_01.ome.tif", "img_01.tif"),
        ("IMG_01.tiff", "img_01.tif"),
    ],
)
def test_images_with_the_same_position_id_in_one_folder_are_an_error(tmp_path, names):
    make_tree(tmp_path, [f"ctrl/tech1/{name}" for name in names] + ["ctrl/tech2/img_01.tif"])
    result = discover_experiment(tmp_path)
    assert not result.ok
    found = issue(result, "duplicate_position_id")
    assert found.severity is Severity.ERROR
    assert found.paths == tuple(sorted(f"ctrl/tech1/{name.rstrip('/')}" for name in names))
    assert found.action


def test_output_folder_inside_experiment_is_an_error(tmp_path):
    make_tree(tmp_path, ["exp/cond/a.tif", "exp/results/plane_histograms.csv"])
    result = discover_experiment(tmp_path / "exp", output_folder=tmp_path / "exp" / "results")
    assert not result.ok
    found = issue(result, "output_inside_experiment")
    assert found.paths == ("results",)
    # The output folder itself is not scanned, so its files raise no extra warnings.
    assert codes(result) == ["output_inside_experiment"]


@pytest.mark.parametrize("inside", [".", "cond", "cond/sub"])
def test_check_output_folder_inside(tmp_path, inside):
    (tmp_path / "exp").mkdir()
    issues = check_output_folder(tmp_path / "exp", tmp_path / "exp" / inside)
    assert [i.code for i in issues] == ["output_inside_experiment"]
    assert issues[0].paths == (inside,)


def test_check_output_folder_outside(tmp_path):
    (tmp_path / "exp").mkdir()
    assert check_output_folder(tmp_path / "exp", tmp_path / "exp_out") == []
    assert check_output_folder(tmp_path / "exp", default_output_folder(tmp_path / "exp")) == []
    # A shared name prefix is not containment.
    assert check_output_folder(tmp_path / "exp", tmp_path / "exp2" / "out") == []


def test_default_output_folder_is_a_sibling(tmp_path):
    out = default_output_folder(tmp_path / "exp1")
    assert out == (tmp_path / "exp1_MorphoBricks_Preprocessing").resolve()


def test_no_images_is_an_error(tmp_path):
    make_tree(tmp_path, ["cond/notes.txt"])
    result = discover_experiment(tmp_path)
    assert not result.ok
    assert issue(result, "no_images").severity is Severity.ERROR


def test_empty_experiment_folder(tmp_path):
    result = discover_experiment(tmp_path)
    assert codes(result) == ["empty_folder", "no_images"]
    assert issue(result, "empty_folder").paths == (".",)


def test_missing_experiment_folder(tmp_path):
    result = discover_experiment(tmp_path / "missing")
    assert codes(result) == ["experiment_not_found"]
    assert not result.ok and result.leaves == ()


def test_experiment_path_is_a_file(tmp_path):
    make_tree(tmp_path, ["a.tif"])
    assert codes(discover_experiment(tmp_path / "a.tif")) == ["experiment_not_found"]


@pytest.mark.skipif(os.name == "nt", reason="permissions are not enforced this way on Windows")
def test_unreadable_folder_is_an_error(tmp_path):
    make_tree(tmp_path, ["cond/a.tif", "locked/b.tif"])
    (tmp_path / "locked").chmod(0)
    try:
        if os.access(tmp_path / "locked", os.R_OK):
            pytest.skip("running with permissions that ignore file modes")
        result = discover_experiment(tmp_path)
    finally:
        (tmp_path / "locked").chmod(0o755)
    assert issue(result, "unreadable_folder").paths == ("locked",)


# Combined -----------------------------------------------------------------------------


def test_all_warnings_together_keep_a_stable_order(tmp_path):
    make_tree(
        tmp_path,
        [
            "stray.tif",
            "notes.txt",
            "empty/",
            "single/a.tif",
            "cond/bio/tech/deep/a.tif",
            "cond/bio/tech2/a.tif",
        ],
    )
    result = discover_experiment(tmp_path)
    assert codes(result) == [
        "reserved_folder_name",
        "folder_too_deep",
        "non_tiff_file",
        "empty_folder",
        "tiff_outside_leaf",
    ]
    for found in result.issues:
        assert found.message and found.action and found.paths


def test_issue_text_lists_paths_and_action(tmp_path):
    make_tree(tmp_path, ["cond/a.tif", "cond/notes.txt"])
    text = str(issue(discover_experiment(tmp_path), "non_tiff_file"))
    assert text.startswith(
        "Warning: 1 file is not an image (TIFF or OME-Zarr) and will not be analysed."
    )
    assert "  - cond/notes.txt" in text
    assert "Suggested action:" in text
