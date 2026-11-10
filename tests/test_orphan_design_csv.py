"""An ``experiment_design.csv`` in the output folder never proposes or prefills a draft.

Experiment-v1 section 7 (Spec ADR 0002 AR10 (9)): the derived CSV is an output and is never read
back. The orphan below is exactly what the removed migration used to take: confirmed repeats
with dates, a control, and groups that differ from the default pairing. Draft creation,
``describe`` and the notebook (first open and reopen) must give what they give without it.
"""

import pytest

from morphobricks_preprocessing.design import DesignRow, DesignTable, write_experiment_design
from morphobricks_preprocessing.discovery import default_output_folder, discover_experiment
from morphobricks_preprocessing.draft import draft_from_discovery
from tiff_tree import design_tree

FOLDERS = ["ctrl/rep1", "ctrl/rep2", "drug/rep1", "drug/rep2"]


def orphan(output):
    """Write a fully confirmed design with a control and odd groups; return its bytes."""
    rows = [
        DesignRow(
            c,
            r,
            f"G-{r}",
            control=c == "ctrl",
            image_count=2,
            confirmed=True,
            confirmed_on="2026-10-30",
        )
        for c in ("ctrl", "drug")
        for r in ("rep1", "rep2")
    ]
    path = output / "experiment_design.csv"
    write_experiment_design(DesignTable(tuple(rows)), path)
    return path.read_bytes()


@pytest.fixture
def root(tmp_path):
    return design_tree(tmp_path / "exp", FOLDERS)


def test_draft_creation_ignores_an_orphan_design_csv(root):
    output = default_output_folder(root)
    expected = draft_from_discovery(discover_experiment(root, output))
    output.mkdir(parents=True)
    data = orphan(output)
    draft = draft_from_discovery(discover_experiment(root, output))
    assert draft == expected
    assert set(draft.repeats) == {"rep1", "rep2"}  # not the file's G-rep1, G-rep2
    assert not draft.references
    assert not any(r.confirmed or r.confirmed_on for r in draft.repeats.values())
    assert (output / "experiment_design.csv").read_bytes() == data


def test_the_notebook_ignores_an_orphan_design_csv_on_open_and_on_reopen(root, tmp_path):
    pytest.importorskip("ipywidgets")
    pytest.importorskip("matplotlib")
    from morphobricks_preprocessing.notebook_ui import PreprocessingNotebook

    def opened(output):
        nb = PreprocessingNotebook(str(root), output_folder=str(output))
        for step in (nb.welcome, nb.folder_step, nb.axis_step, nb.run_step, nb.results_step):
            step()
        nb.check_button.click()
        return nb

    def seen(nb):
        draft = nb.draft
        return (
            sorted(draft.repeats),
            dict(draft.references),
            [(r.confirmed, r.confirmed_on) for r in draft.repeats.values()],
            [(q.code, q.subject) for q in draft.open_questions()],
        )

    baseline = seen(opened(tmp_path / "plain"))
    output = tmp_path / "with-orphan"
    output.mkdir()
    data = orphan(output)
    assert seen(opened(output)) == baseline
    assert seen(opened(output)) == baseline  # reopened on the same output folder
    assert (output / "experiment_design.csv").read_bytes() == data
