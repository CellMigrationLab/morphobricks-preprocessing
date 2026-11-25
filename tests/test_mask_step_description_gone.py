"""The mask step reads no mask and writes no report without its description (#418)."""

import pytest

from described_tree import main_described, without_mask_step
from morphobricks_preprocessing.mask_representation import REPRESENTATION_FILENAME
from morphobricks_preprocessing.mask_scan import MASK_REPORT_FILENAME
from morphobricks_preprocessing.run import describe_masks, mask_step, prepare_mask_step
from morphobricks_preprocessing.testing import make_clean_run


@pytest.fixture
def described(tmp_path):
    """A run whose mask step was described once, and a step prepared again."""
    run = without_mask_step(
        make_clean_run(tmp_path, axes="YX", shape=(8, 8), mask_types=("Cells", "Nuclei"))
    )
    assert describe_masks(prepare_mask_step(None, run.output)).path is not None
    return run, prepare_mask_step(None, run.output)


def outputs(folder):
    return {
        name: (folder / name).read_bytes() if (folder / name).exists() else None
        for name in (MASK_REPORT_FILENAME, REPRESENTATION_FILENAME)
    }


@pytest.mark.parametrize("change", ["removed", "corrupted"])
def test_a_description_gone_since_the_step_was_prepared_stops_it(described, monkeypatch, change):
    run, step = described
    description = run.output / "morphobricks.toml"
    if change == "removed":
        description.unlink()
    else:
        description.write_text("not = [toml", encoding="utf-8")
    before = outputs(run.output)
    assert before[MASK_REPORT_FILENAME] is not None
    scanned = []
    monkeypatch.setattr(mask_step, "scan_masks", lambda *a, **k: scanned.append(a))
    result = describe_masks(step)
    assert scanned == []  # no mask read
    assert result.report is None and result.path is None
    assert result.issues and all(issue.is_error for issue in result.issues)
    if change == "removed":
        assert [issue.code for issue in result.issues] == ["A_DESCRIPTION_REQUIRED"]
    assert outputs(run.output) == before  # the earlier reports are left as they were


def _gone(monkeypatch):
    """Make the description read under the lock gone, as if removed after the step was ready."""
    from morphobricks_preprocessing.required_description import description_required_issue

    monkeypatch.setattr(
        mask_step, "_published", lambda output: (None, [description_required_issue(output)])
    )


def test_the_command_says_why_and_writes_nothing(described, monkeypatch, capsys):
    run, _ = described
    before = outputs(run.output)
    _gone(monkeypatch)
    assert main_described(["masks", "--output", str(run.output)]) == 1
    shown = capsys.readouterr().out
    assert "A_DESCRIPTION_REQUIRED" in shown and "Report written" not in shown
    assert outputs(run.output) == before


def test_the_notebook_shows_a_red_card_and_writes_nothing(tmp_path, monkeypatch):
    pytest.importorskip("ipywidgets")
    pytest.importorskip("matplotlib")
    from test_notebook_mask_step import experiment, nested_masks, output_of, ran

    root = experiment(tmp_path)
    nb, _ = ran(root, nested_masks(tmp_path))
    assert nb.run().written
    _gone(monkeypatch)
    assert nb._on_describe_masks() is None
    assert "the experiment description cannot be read" in nb.mask_step_status.value
    assert "A_DESCRIPTION_REQUIRED" in nb.mask_step_result.value
    assert not (output_of(root) / MASK_REPORT_FILENAME).exists()
