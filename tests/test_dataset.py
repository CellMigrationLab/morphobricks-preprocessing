from morphobricks_preprocessing import (
    CONSISTENCY_CODES,
    balance_table,
    biological_repeats,
    check_dataset,
    discover_experiment,
    preflight,
    run_experiment,
)
from morphobricks_preprocessing.axes import prefill_mapping
from morphobricks_preprocessing.cli import main
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
    result = run_experiment(root, prefill_mapping("YX"))
    assert result.written
    assert {"unbalanced_images", "repeat_missing_conditions"} <= set(codes(result.issues))


def test_consistency_codes_cover_the_new_warnings():
    for code in (
        "unbalanced_images",
        "repeat_missing_conditions",
        "different_pixel_sizes",
        "missing_pixel_size",
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
    assert main(["check", str(root)]) == 0
    out = capsys.readouterr().out
    assert "Images per condition (columns) and biological repeat (rows)" in out
    assert "1 (0) !" in out
    assert "not found in every condition" in out
    assert "differs strongly between conditions" in out
