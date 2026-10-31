import os

import numpy as np
import pytest

from morphobricks_preprocessing import (
    SINGLE,
    AxisMapping,
    Severity,
    check_output_folder,
    default_output_folder,
    discover_experiment,
    position_id,
    run_experiment,
)
from morphobricks_preprocessing.discovery import is_system_name, is_tiff_name
from tiff_tree import make_tree, write_data


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


def test_images_beside_subfolders_are_an_error(tmp_path):
    # Owner decision (October 2026): a folder holding both images and subfolders is refused,
    # as by every other brick; its images are never skipped with only a warning.
    make_tree(tmp_path, ["stray.tif", "cond/bio/b.tif", "cond/bio/tech/a.tif"])
    result = discover_experiment(tmp_path)
    assert not result.ok
    found = issue(result, "tiff_outside_leaf")
    assert found.severity is Severity.ERROR
    assert found.paths == ("cond/bio/b.tif", "stray.tif")
    assert "never both" in found.message and "folder of their own" in found.action
    assert result.images == ("cond/bio/tech/a.tif",)


def test_a_run_with_images_beside_subfolders_writes_nothing(tmp_path):
    root, output = tmp_path / "exp", tmp_path / "out"
    write_data(root / "cond" / "stray.tif", np.zeros((8, 9), dtype=np.uint8))
    write_data(root / "cond" / "bio" / "a.tif", np.zeros((8, 9), dtype=np.uint8))
    result = run_experiment(root, AxisMapping("YX", "YX"), output_folder=output)
    assert not result.written
    assert "tiff_outside_leaf" in [i.code for i in result.issues if i.is_error]
    assert not output.exists()


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
    make_tree(tmp_path, ["a/Rep1/x.tif", "b/Rep1/x.tif"])
    assert discover_experiment(tmp_path).issues == ()


@pytest.mark.parametrize(
    ("paths", "clashing"),
    [
        # Decision D-e: one spelling per name anywhere in the run, at every level. Before
        # D86 only siblings were compared, so these were accepted.
        (["a/Rep1/x.tif", "b/rep1/x.tif"], ["a/Rep1", "b/rep1"]),
        # The report's MF-5 example: technical repeats Well1 and well1 under two conditions
        # (MorphoFeature #97 refused it, this brick accepted it).
        (
            ["ctrl/rep1/Well1/a.tif", "treated/rep1/well1/b.tif"],
            ["ctrl/rep1/Well1", "treated/rep1/well1"],
        ),
    ],
)
def test_names_differing_only_in_case_clash_anywhere_in_the_run(tmp_path, paths, clashing):
    make_tree(tmp_path, paths)
    result = discover_experiment(tmp_path)
    assert not result.ok
    assert list(issue(result, "case_conflict").paths) == clashing


def test_the_case_rule_compares_names_of_one_level(tmp_path):
    # A condition and a technical repeat may share a name in other cases.
    make_tree(tmp_path, ["ctrl/rep1/A/x.tif", "a/rep1/b/x.tif"])
    assert discover_experiment(tmp_path).issues == ()


@pytest.mark.parametrize(
    ("path", "bad"),
    [
        ("NA/rep1/x.tif", "NA"),
        ("ctrl/nan/x.tif", "ctrl/nan"),
        ("ctrl/rep1/None/x.tif", "ctrl/rep1/None"),
        ("ctrl /x.tif", "ctrl "),
        ("ctrl/ rep1/x.tif", "ctrl/ rep1"),
        ("ctrl/NA.tif", "ctrl/NA.tif"),
        ("ctrl/img .tif", "ctrl/img .tif"),
        ("ctrl/ img.ome.tif", "ctrl/ img.ome.tif"),
        ("ctrl/none.ome.zarr/.zattrs", "ctrl/none.ome.zarr"),
        ("ctrl/NULL/x.tif", "ctrl/NULL"),
        ("ctrl/null.tif", "ctrl/null.tif"),
    ],
)
def test_names_that_read_as_missing_or_have_outer_spaces_are_refused(tmp_path, path, bad):
    # Decision D-e: such names are refused, in any case, as labels and as position_id.
    make_tree(tmp_path, [path])
    parent, _, name = bad.rpartition("/")
    if name not in os.listdir(tmp_path / parent):
        pytest.skip("the file system drops trailing spaces from folder names (Windows)")
    found = issue(discover_experiment(tmp_path), "invalid_name")
    assert found.is_error
    assert found.paths == (bad,)


def test_names_with_inner_spaces_and_text_values_are_fine(tmp_path):
    make_tree(tmp_path, ["ctrl A/rep 1/img 01.tif", "TRUE/001/FALSE.tif", "nana/rep1/x.tif"])
    assert discover_experiment(tmp_path).issues == ()


# The shared identity rules (MorphoBricks D86, decision D-e) ---------------------------------


def test_fov_key():
    from morphobricks_preprocessing import FOV_KEY

    assert FOV_KEY == ("condition", "biological_repeat", "technical_repeat", "position_id")


@pytest.mark.parametrize(
    "value", ["ctrl", "rep 1", "001", "TRUE", "single", "nullify", "NAN1", "n-a", "na1"]
)
def test_check_identity_text_accepts(value):
    from morphobricks_preprocessing import check_identity_text

    assert check_identity_text(value, "condition") == value


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("", "missing value"),
        ("NA", "missing value"),
        ("na", "missing value"),
        ("nan", "missing value"),
        ("NaN", "missing value"),
        ("none", "missing value"),
        ("None", "missing value"),
        ("null", "missing value"),
        ("NULL", "missing value"),
        ("n/a", "missing value"),
        ("N/A", "missing value"),
        ("<NA>", "missing value"),
        ("<na>", "missing value"),
        (" ctrl", "whitespace"),
        ("ctrl ", "whitespace"),
        ("ctrl\t", "whitespace"),
        ("\u00a0ctrl", "whitespace"),
        (None, "must be text"),
        (1, "must be text"),
        (float("nan"), "must be text"),
    ],
)
def test_check_identity_text_refuses_by_name(value, message):
    from morphobricks_preprocessing import IdentityError, check_identity_text

    with pytest.raises(IdentityError, match=f"position_id .*{message}"):
        check_identity_text(value, "position_id")
    assert issubclass(IdentityError, ValueError)


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, None), (0, 0), (3, 3), (np.int64(1), 1), (np.uint8(0), 0), (np.int32(7), 7)],
)
def test_check_plane_index_accepts(value, expected):
    # NumPy integers (from a table column) are indices, returned as plain ints.
    from morphobricks_preprocessing import check_plane_index

    found = check_plane_index(value, "z")
    assert found == expected and type(found) is type(expected)


@pytest.mark.parametrize(
    "value", [-1, True, False, 1.0, "1", np.int64(-1), np.float64(1.0), np.bool_(True)]
)
def test_check_plane_index_refuses_what_is_not_an_index(value):
    # The report's MF-5 example: "1" (accepted by CellPatcher's plane_index) is refused,
    # never converted; so are floats, bools and negative NumPy integers.
    from morphobricks_preprocessing import IdentityError, check_plane_index

    with pytest.raises(IdentityError, match="^t must be an int"):
        check_plane_index(value, "t")


@pytest.mark.parametrize(
    ("keys", "message"),
    [
        # A label of any level clashes with another spelling anywhere in the run (D-e) ...
        ([("ctrl", "Rep1", "single"), ("treated", "rep1", "single")], "biological_repeat 'Rep1'"),
        ([("ctrl", "r1", "A"), ("treated", "r2", "a")], "technical_repeat 'A'"),
        ([("Ctrl", "r1", "single", "f1"), ("ctrl", "r2", "single", "f2")], "condition 'Ctrl'"),
        # ... and position_id within its technical repeat (D85).
        ([("c", "r", "t", "Img"), ("c", "r", "t", "img")], "position_id 'Img'"),
    ],
)
def test_check_identity_case_refuses_names_differing_only_in_case(keys, message):
    # The run-wide case rule for bricks that read identities from tables (MorphoFeature,
    # MorphoObjects, CellPatcher), the rule discovery applies to folder names.
    from morphobricks_preprocessing import IdentityError, check_identity_case

    with pytest.raises(IdentityError, match=f"^{message} and .* differ only in upper/lower case"):
        check_identity_case(keys)


def test_check_identity_case_accepts_one_spelling_and_position_scope():
    from morphobricks_preprocessing import check_identity_case

    check_identity_case(
        [
            ("ctrl", "rep1", "single", "f1"),
            ("ctrl", "rep1", "single", "f1"),  # several planes of one position
            ("treated", "rep1", "single", "f1"),
            ("ctrl", "rep2", "single", "F1"),  # another technical repeat: no clash (D85)
            ("ctrl", "rep1", "A", "x"),  # one level may share a name with another
            ("a", "rep1", "single", "y"),
        ]
    )
    check_identity_case([])


def test_check_identity_case_refuses_keys_of_another_length():
    from morphobricks_preprocessing import check_identity_case

    with pytest.raises(ValueError, match="does not have the values"):
        check_identity_case([("ctrl", "rep1")])


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
        # The other formats MorphoBricks bricks read (D85), any case; naming only.
        ("a.npy", "a"),
        ("ctrl/b.PNG", "b"),
        ("c.jpeg", "c"),
        ("d.jpg", "d"),
        ("e.bmp", "e"),
        ("f.gif", "f"),
        ("g.json", "g.json"),
    ],
)
def test_position_id_is_the_image_name_without_extension(image, expected):
    assert position_id(image) == expected


@pytest.mark.parametrize(
    "image", [".tif", ".png", ".ome.tif", ".OME.TIFF", "ctrl/.ome.zarr", ".npy"]
)
def test_a_name_that_is_only_an_extension_is_refused(image):
    """The longest extension decides once: ``.ome.tif`` is not read as the position ``.ome``."""
    with pytest.raises(ValueError, match="only an extension"):
        position_id(image)


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


def test_naming_other_formats_does_not_make_them_images(tmp_path):
    """NAME_SUFFIXES only names positions: discovery still accepts TIFF and Zarr images only."""
    from morphobricks_preprocessing.discovery import IMAGE_SUFFIXES, NAME_SUFFIXES

    assert set(IMAGE_SUFFIXES) < set(NAME_SUFFIXES) and ".npy" not in IMAGE_SUFFIXES
    make_tree(tmp_path, ["ctrl/bio1/img_01.tif", "ctrl/bio1/img_02.png"])
    result = discover_experiment(tmp_path)
    names = [image for leaf in result.leaves for image in leaf.images]
    assert [position_id(image) for image in names] == ["img_01"]
