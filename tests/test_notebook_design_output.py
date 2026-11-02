"""The notebook's experiment design card: its output folder (#98) and its counts (#82).

Changing the output folder reads the design saved there, or asks the questions again; the
earlier folder's confirmations and controls are never carried over, and nothing is written
until an answer. Widgets are driven headless, as in ``test_notebook_ui``.
"""

import pytest

from morphobricks_preprocessing import read_experiment_design
from tiff_tree import design_tree


@pytest.fixture(autouse=True)
def _widgets():
    pytest.importorskip("ipywidgets")
    pytest.importorskip("matplotlib")


def checked(root, output):
    from morphobricks_preprocessing.notebook_ui import PreprocessingNotebook

    nb = PreprocessingNotebook(str(root), output_folder=str(output))
    for step in (nb.welcome, nb.folder_step, nb.axis_step, nb.run_step, nb.results_step):
        step()
    nb.check_button.click()
    nb.labels_confirmed.button.click()
    return nb


def confirm_repeats(nb):
    for toggle in nb.group_confirmations.values():
        if not toggle.value:
            toggle.button.click()


def confirmed(root, output):
    """Confirm everything step 3 needs: labels, design, axis roles and image type."""
    nb = checked(root, output)
    confirm_repeats(nb)
    nb.preview_button.click()
    nb.preview_checked.button.click()
    nb.confirm_button.click()
    nb.image_type_checked.button.click()
    return nb


def visible(widget):
    from morphobricks_preprocessing import notebook_style as ui

    return ui.is_visible(widget)


def confirmed_output(root, output, control):
    """Save a confirmed design with ``control`` marked in ``output``, from a notebook."""
    nb = checked(root, output)
    nb.control_checks[control].value = True
    confirm_repeats(nb)
    path = output / "experiment_design.csv"
    assert read_experiment_design(path).controls == (control,)
    return path.read_bytes()


def test_changing_the_output_folder_reads_its_own_design(tmp_path):
    # Issue #98: the design confirmed for output A was kept, and run, for output B.
    root = design_tree(tmp_path / "exp", ["ctrl/rep1", "drug/rep1"])
    a, b = tmp_path / "a", tmp_path / "b"
    saved_a = confirmed_output(root, a, "ctrl")
    saved_b = confirmed_output(root, b, "drug")
    nb = confirmed(root, a)
    assert nb.design.controls == ("ctrl",)
    nb.output.value = str(b)
    assert nb.design.controls == ("drug",) and nb.design.confirmed
    assert "its experiment design was read" in nb.design_status.value
    assert "Controls: drug" in nb.design_status.value
    assert (a / "experiment_design.csv").read_bytes() == saved_a
    assert (b / "experiment_design.csv").read_bytes() == saved_b
    nb.run_button.click()
    assert nb.result.written
    assert read_experiment_design(b / "experiment_design.csv").controls == ("drug",)
    assert (a / "experiment_design.csv").read_bytes() == saved_a
    # And back: A's own answers again.
    nb.output.value = str(a)
    assert nb.design.controls == ("ctrl",)
    assert (a / "experiment_design.csv").read_bytes() == saved_a


def test_a_new_output_folder_asks_the_design_again_and_writes_nothing_until_answered(tmp_path):
    root = design_tree(tmp_path / "exp", ["ctrl/rep1", "drug/rep1"])
    a = tmp_path / "a"
    saved_a = confirmed_output(root, a, "ctrl")
    nb = confirmed(root, a)
    assert not nb.run_button.disabled
    new = tmp_path / "new"
    # Typed one character at a time: no folder on the way is written.
    for end in range(len(str(tmp_path)) + 1, len(str(new)) + 1):
        nb.output.value = str(new)[:end]
    assert not new.exists()
    assert not nb.design.confirmed and nb.design.controls == ()
    status = nb.design_status.value
    assert "the experiment design is asked again" in status
    assert "are not carried over" in status and "Saved in" not in status
    assert "Not saved yet: your next answer saves it in" in status
    assert visible(nb.design_editor)
    assert nb.run_button.disabled
    assert "Still to do: confirm the biological repeats" in nb.readiness.value
    confirm_repeats(nb)
    assert read_experiment_design(new / "experiment_design.csv").confirmed
    assert read_experiment_design(new / "experiment_design.csv").controls == ()
    assert (a / "experiment_design.csv").read_bytes() == saved_a


def test_an_unreadable_design_in_the_new_output_folder_is_refused_and_kept(tmp_path):
    root = design_tree(tmp_path / "exp", ["ctrl/rep1", "drug/rep1"])
    a, bad = tmp_path / "a", tmp_path / "bad"
    confirmed_output(root, a, "ctrl")
    bad.mkdir()
    (bad / "experiment_design.csv").write_bytes(b"not a design")
    nb = confirmed(root, a)
    nb.output.value = str(bad)
    assert "cannot be read" in nb.design_status.value
    assert not nb.design.confirmed and nb.design.controls == ()
    assert nb.run_button.disabled
    assert (bad / "experiment_design.csv").read_bytes() == b"not a design"


def test_the_design_card_says_which_folders_are_not_counted(tmp_path):
    # Issue #82: a technical-repeat folder whose images are all excluded is not counted.
    root = design_tree(tmp_path / "exp", ["ctrl/rep1/techA", "ctrl/rep1/techB", "drug/rep1/t1"])
    from morphobricks_preprocessing.notebook_ui import PreprocessingNotebook

    nb = PreprocessingNotebook(str(root), output_folder=str(tmp_path / "out"))
    for step in (nb.welcome, nb.folder_step, nb.axis_step, nb.run_step, nb.results_step):
        step()
    nb.exclude.value = "ctrl/rep1/techB/a.tif"
    nb.check_button.click()
    nb.labels_confirmed.button.click()
    assert "ctrl/rep1/techB" in nb.design_status.value
    assert "1 technical repeat<" in nb.balance_table.value
    (row,) = [r for r in nb.design.rows if r.condition == "ctrl"]
    assert (row.image_count, row.technical_repeat_count) == (1, 1)
    saved = read_experiment_design(tmp_path / "out" / "experiment_design.csv")
    assert [r.technical_repeat_count for r in saved.rows] == [1, 1]
