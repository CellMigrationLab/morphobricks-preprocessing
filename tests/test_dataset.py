import pytest

from described_tree import main_described, run_described
from morphobricks_preprocessing import (
    CONSISTENCY_CODES,
    balance_table,
    biological_repeats,
    check_dataset,
    discover_experiment,
    preflight,
)
from morphobricks_preprocessing.axes import prefill_mapping
from morphobricks_preprocessing.report import balance_rows, balance_summary
from morphobricks_preprocessing.validation import Severity
from tiff_tree import make_tree


def codes(issues):
    return [issue.code for issue in issues]


def issue(issues, code):
    (found,) = [i for i in issues if i.code == code]
    return found


def tree(root, counts):
    """Write ``counts[folder]`` images into each folder."""
    make_tree(root, [f"{folder}/img_{i:02d}.tif" for folder, n in counts.items() for i in range(n)])
    return root


BALANCED = {
    "control/rep1/t1": 2,
    "control/rep1/t2": 2,
    "control/rep2/t1": 3,
    "treated/rep1/t1": 4,
    "treated/rep2": 3,
}


def test_balanced_experiment_is_silent(tmp_path):
    report = check_dataset(discover_experiment(tree(tmp_path / "exp", BALANCED)))
    assert report.issues == ()
    balance = report.balance
    assert balance.conditions == ("control", "treated")
    assert balance.repeat_groups == ("rep1", "rep2")
    assert balance.median == 3.5
    cell = balance.cell("rep1", "control")
    assert (cell.image_count, cell.technical_repeat_count) == (4, 2)
    assert cell.folders == ("control/rep1",) and not cell.unbalanced
    # Images directly in the biological-repeat folder: no technical-repeat folders.
    assert balance.cell("rep2", "treated").technical_repeat_count == 0


def test_few_images_are_unbalanced(tmp_path):
    counts = {"control/rep1": 6, "control/rep2": 6, "treated/rep1": 6, "treated/rep2": 2}
    report = check_dataset(discover_experiment(tree(tmp_path / "exp", counts)))
    found = issue(report.issues, "unbalanced_images")
    assert found.severity is Severity.WARNING
    assert found.paths == ("treated/rep2",)
    assert "median is 6 images" in found.message and "treated / rep2: 2 images" in found.message
    assert report.balance.cell("rep2", "treated").unbalanced
    assert not report.balance.cell("rep1", "treated").unbalanced


def test_many_images_are_unbalanced(tmp_path):
    counts = {"control/rep1": 3, "control/rep2": 3, "treated/rep1": 3, "treated/rep2": 7}
    report = check_dataset(discover_experiment(tree(tmp_path / "exp", counts)))
    assert issue(report.issues, "unbalanced_images").paths == ("treated/rep2",)
    # Exactly half or twice the median is still balanced.
    counts = {"control/rep1": 2, "control/rep2": 4, "treated/rep1": 4, "treated/rep2": 8}
    report = check_dataset(discover_experiment(tree(tmp_path / "exp2", counts)))
    assert "unbalanced_images" not in codes(report.issues)


def test_repeat_missing_from_a_condition(tmp_path):
    counts = {"control/rep1": 2, "control/rep2": 2, "treated/rep1": 2, "treated/rep3": 2}
    report = check_dataset(discover_experiment(tree(tmp_path / "exp", counts)))
    found = issue(report.issues, "repeat_missing_conditions")
    assert found.severity is Severity.WARNING
    assert "rep2 (not in treated)" in found.message and "rep3 (not in control)" in found.message
    assert found.paths == ("control/rep2", "treated/rep3")
    assert report.balance.missing_conditions("rep2") == ("treated",)


def test_pairing_joins_differently_named_repeats(tmp_path):
    counts = {"control/rep1": 2, "control/rep2": 2, "treated/rep1": 2, "treated/rep2b": 2}
    discovery = discover_experiment(tree(tmp_path / "exp", counts))
    report = check_dataset(discovery, {("treated", "rep2b"): "rep2"})
    assert report.issues == ()
    assert report.balance.repeat_groups == ("rep1", "rep2")
    assert report.balance.cell("rep2", "treated").folders == ("treated/rep2b",)


def test_single_repeats_form_one_group(tmp_path):
    counts = {"control": 3, "treated": 3}
    report = check_dataset(discover_experiment(tree(tmp_path / "exp", counts)))
    assert report.issues == ()
    assert report.balance.repeat_groups == ("single",)
    assert [r.folder for r in report.repeats] == ["control", "treated"]
    root = check_dataset(discover_experiment(tree(tmp_path / "flat", {".": 2})))
    assert root.issues == () and root.repeats[0].folder == "."


def test_excluded_images_are_not_counted(tmp_path):
    counts = {"control/rep1": 2, "control/rep2": 1, "treated/rep1": 2, "treated/rep2": 2}
    discovery = discover_experiment(tree(tmp_path / "exp", counts))
    repeats = biological_repeats(discovery, excluded=["control/rep2/img_00.tif"])
    # A folder whose every image is excluded is no longer a biological repeat.
    assert [r.key for r in repeats] == [
        ("control", "rep1"),
        ("treated", "rep1"),
        ("treated", "rep2"),
    ]
    assert balance_table(repeats).missing_conditions("rep2") == ("control",)


def test_preflight_and_run_report_the_balance(tmp_path):
    root = tree(tmp_path / "exp", {"control/rep1": 4, "treated/rep1": 1, "treated/rep2": 4})
    pre = preflight(root)
    assert pre.ok
    assert {"unbalanced_images", "repeat_missing_conditions"} <= set(codes(pre.issues))
    assert pre.dataset.balance.cell("rep1", "treated").unbalanced
    result = run_described(root, prefill_mapping("YX"))
    assert result.written
    assert {"unbalanced_images", "repeat_missing_conditions"} <= set(codes(result.issues))


def test_consistency_codes_cover_the_new_warnings():
    for code in (
        "unbalanced_images",
        "repeat_missing_conditions",
        "different_pixel_sizes",
        "missing_pixel_size",
        "unsupported_pixel_size_unit",
        "different_plane_counts",
        "different_sizes",
        "mixed_dtypes",
        "channel_names_differ",
    ):
        assert code in CONSISTENCY_CODES


def test_balance_text_for_the_command_line(tmp_path):
    root = tree(tmp_path / "exp", {"control/rep1/t1": 4, "treated/rep1": 1, "treated/rep2": 4})
    balance = check_dataset(discover_experiment(root)).balance
    assert balance_rows(balance) == [
        ["biological repeat", "control", "treated"],
        ["rep1", "4 (1)", "1 (0) !"],
        ["rep2", "-", "4 (0)"],
    ]
    text = balance_summary(balance)
    assert "median of 4 images" in text and text.isascii()


def test_cli_check_prints_the_balance_and_warnings(tmp_path, capsys):
    root = tree(tmp_path / "exp", {"control/rep1": 4, "treated/rep1": 1, "treated/rep2": 4})
    assert main_described(["check", str(root)]) == 0
    out = capsys.readouterr().out
    assert "Images per condition (columns) and biological repeat (rows)" in out
    assert "1 (0) !" in out
    assert "not found in every condition" in out
    assert "differs strongly between conditions" in out


def test_a_technical_repeat_whose_images_are_all_excluded_is_not_counted(tmp_path):
    # Issue #82: techB's only image is excluded, so the repeat has one technical repeat.
    root = tree(tmp_path / "exp", {"ctrl/rep1/techA": 1, "ctrl/rep1/techB": 1})
    excluded = ["ctrl/rep1/techB/img_00.tif"]
    (repeat,) = biological_repeats(discover_experiment(root), excluded)
    assert (repeat.image_count, repeat.technical_repeat_count) == (1, 1)
    report = check_dataset(discover_experiment(root), excluded=excluded)
    cell = report.balance.cell("rep1", "ctrl")
    assert (cell.image_count, cell.technical_repeat_count) == (1, 1)
    assert report.balance.excluded_images == 1
    assert report.balance.excluded_folders == ("ctrl/rep1/techB",)
    # The texts say what the counts leave out.
    assert "ctrl/rep1/techB" in balance_summary(report.balance)
    assert balance_rows(report.balance)[1] == ["rep1", "1 (1)"]


def test_without_exclusions_nothing_is_said_about_them(tmp_path):
    root = tree(tmp_path / "exp", {"ctrl/rep1/techA": 1, "ctrl/rep1/techB": 1})
    balance = check_dataset(discover_experiment(root)).balance
    assert balance.not_counted() == "" and "Not counted" not in balance_summary(balance)
    assert balance.cell("rep1", "ctrl").technical_repeat_count == 2


def test_a_technical_repeat_folder_named_single_is_the_level_value(tmp_path):
    # Plan §12 question 23 (documented, not changed by #82): a lower-case folder named
    # single, alone in its place, is the level's value single, so it counts as no
    # technical-repeat folder, like images directly in the biological-repeat folder.
    root = tree(tmp_path / "exp", {"ctrl/rep1/single": 2})
    (repeat,) = biological_repeats(discover_experiment(root))
    assert (repeat.image_count, repeat.technical_repeat_count) == (2, 0)


def test_the_design_written_by_a_run_counts_only_analysed_technical_repeats(tmp_path):
    from morphobricks_preprocessing import read_confirmed_design
    from morphobricks_preprocessing.design import DESIGN_FILENAME

    root = tree(tmp_path / "exp", {"ctrl/rep1/techA": 1, "ctrl/rep1/techB": 1})
    excluded = ["ctrl/rep1/techB/img_00.tif"]
    output = tmp_path / "out"  # the description declares the analysed unit only
    result = run_described(root, prefill_mapping("YX"), output_folder=output, exclude=excluded)
    assert result.written
    (row,) = read_confirmed_design(output / DESIGN_FILENAME).design.rows
    assert (row.image_count, row.technical_repeat_count) == (1, 1)


def test_the_notebook_balance_table_says_what_is_not_counted(tmp_path):
    pytest.importorskip("ipywidgets")
    from morphobricks_preprocessing.notebook_render import balance_html

    root = tree(tmp_path / "exp", {"ctrl/rep1/techA": 1, "ctrl/rep1/techB": 1})
    balance = check_dataset(
        discover_experiment(root), excluded=["ctrl/rep1/techB/img_00.tif"]
    ).balance
    text = balance_html(balance)
    assert "1 technical repeat<" in text and "Not counted: 1 excluded image" in text
    assert "ctrl/rep1/techB" in text
