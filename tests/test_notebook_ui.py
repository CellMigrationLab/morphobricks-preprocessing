import json
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")

from morphobricks_preprocessing import Plane, read_manifest  # noqa: E402
from morphobricks_preprocessing.notebook_ui import (  # noqa: E402
    PreprocessingNotebook,
    display_range,
    issues_html,
    plane_png,
    table_html,
)
from morphobricks_preprocessing.validation import error, warning  # noqa: E402
from tiff_tree import write_data, write_file  # noqa: E402

NOTEBOOK = Path(__file__).parents[1] / "notebooks" / "preprocessing.ipynb"


@pytest.fixture
def experiment(tmp_path):
    root = tmp_path / "exp"
    rng = np.random.default_rng(0)
    for rel in ["ctrl/rep1/a.tif", "ctrl/rep1/b.tif", "drug/rep1/a.tif"]:
        write_data(root / rel, rng.integers(0, 200, (3, 2, 16, 16), dtype=np.uint8), axes="ZCYX")
    return root


def ready(experiment, **kwargs):
    nb = PreprocessingNotebook(str(experiment), **kwargs)
    for step in (nb.folder_step, nb.axis_step, nb.run_step, nb.results_step):
        step()
    nb.check_button.click()
    return nb


def test_steps_build_without_a_folder():
    nb = PreprocessingNotebook()
    for step in (nb.folder_step, nb.axis_step, nb.run_step, nb.results_step):
        assert step().children
    assert nb.run_button.disabled
    assert "check the experiment folder" in nb.run_status.value
    assert nb.check_folder() is None
    assert "Enter the experiment folder" in nb.folder_summary.value


def test_check_shows_summary_tree_and_suggested_roles(experiment):
    nb = ready(experiment)
    assert nb.preflight is not None and nb.preflight.ok
    assert "Images to analyse: 3" in nb.folder_summary.value
    assert "rep1/  (2 images; technical repeat: single)" in nb.label_tree.value
    assert [d.value for d in nb.role_dropdowns] == ["Z", "C", "Y", "X"]
    assert "valid for every image" in nb.axis_issues.value
    assert nb.bins.value == 256
    assert not nb.labels_confirmed.disabled and not nb.labels_confirmed.value


def test_run_needs_confirmed_labels_and_axes(experiment):
    nb = ready(experiment)
    assert nb.run_button.disabled
    assert "confirm the labels" in nb.run_status.value
    nb.labels_confirmed.value = True
    assert "confirm the axis roles" in nb.run_status.value
    assert nb.run() is None
    nb.confirm_button.click()
    assert not nb.run_button.disabled and nb.run_status.value == ""


def test_confirmation_locks_and_unlocks_the_roles(experiment):
    nb = ready(experiment)
    nb.confirm_button.click()
    assert nb.confirmed_mapping == nb.mapping
    assert all(d.disabled for d in nb.role_dropdowns)
    assert nb.confirm_button.description == "Change axis roles"
    nb.confirm_button.click()
    assert nb.confirmed_mapping is None
    assert not any(d.disabled for d in nb.role_dropdowns)


def test_invalid_roles_cannot_be_confirmed(experiment):
    nb = ready(experiment)
    nb.role_dropdowns[1].value = "-"
    assert "No role is chosen for the axis C (channel)" in nb.axis_issues.value
    assert nb.confirm_button.disabled
    nb.role_dropdowns[1].value = "T"
    assert not nb.confirm_button.disabled
    assert nb.mapping.roles == ("Z", "T", "Y", "X")


def test_preview_shows_a_plane_per_layout(experiment):
    nb = ready(experiment)
    nb.preview_button.click()
    image_box, note = nb.preview_box.children
    image, caption = image_box.children
    assert bytes(image.value).startswith(b"\x89PNG")
    assert "ctrl/rep1/a.tif" in caption.value and "z=1, c=0" in caption.value
    assert "display only" in note.value


def test_full_run_and_statistics(experiment, tmp_path):
    nb = ready(experiment, output_folder=str(tmp_path / "out"))
    nb.labels_confirmed.value = True
    nb.confirm_button.click()
    nb.bins.value = 64
    nb.force_rebuild.value = True
    nb.run_button.click()
    assert nb.result.written
    assert "Wrote 18 planes" in nb.run_output.value
    assert nb.progress.value == nb.progress.max == 3
    assert "Wrote 18 planes" in nb.details.value  # the log reaches "Technical details"
    assert read_manifest(tmp_path / "out" / "run_manifest.json").settings.bin_count == 64
    assert "<td>ctrl</td>" in nb.statistics.value and "<th>p99.8</th>" in nb.statistics.value
    nb.level.value = "image"
    nb.percentiles.value = "50"
    nb.statistics_button.click()
    assert "<th>image</th>" in nb.statistics.value and "<th>p50</th>" in nb.statistics.value


def test_statistics_of_an_earlier_run(experiment):
    first = PreprocessingNotebook(str(experiment))
    first.check_folder()
    first.labels_confirmed.value = True
    first.confirm_button.click()
    first.run()
    later = PreprocessingNotebook(str(experiment))
    later.show_statistics()
    assert "<td>drug</td>" in later.statistics.value


def test_statistics_need_a_table_and_valid_percentiles(experiment):
    nb = ready(experiment)
    nb.show_statistics()
    assert "no histogram table yet" in nb.statistics.value
    nb.labels_confirmed.value = True
    nb.confirm_button.click()
    nb.run()
    nb.percentiles.value = "1, 120"
    nb.show_statistics()
    assert "from 0 to 100" in nb.statistics.value


def test_unreadable_image_can_be_excluded(experiment):
    write_file(experiment / "ctrl/rep1/bad.tif", b"garbage")
    nb = ready(experiment)
    assert "could not be opened" in nb.folder_issues.value
    assert nb.exclude_button.layout.display == ""
    nb.exclude_button.click()
    assert nb.exclude.value == "ctrl/rep1/bad.tif"
    assert nb.preflight.ok
    assert nb.exclude_button.layout.display == "none"
    nb.labels_confirmed.value = True
    nb.confirm_button.click()
    assert nb.run().excluded == ("ctrl/rep1/bad.tif",)


def test_missing_axes_checkbox(tmp_path):
    root = tmp_path / "exp"
    write_data(root / "a.tif", np.zeros((2, 3, 8, 8), np.uint8), axes="ZCYX")
    write_data(root / "b.tif", np.zeros((2, 8, 8), np.uint8), axes="ZYX")
    nb = ready(root)
    assert nb.missing_as_singleton.layout.display == ""
    assert nb.confirm_button.disabled
    nb.missing_as_singleton.value = True
    assert not nb.confirm_button.disabled


def test_conflicting_axes_explain_why_no_roles_are_offered(tmp_path):
    write_data(tmp_path / "exp/a.tif", np.zeros((2, 8, 8), np.uint8), axes="ZYX")
    write_data(tmp_path / "exp/b.tif", np.zeros((2, 8, 8), np.uint8), axes="CYX")
    nb = ready(tmp_path / "exp")
    (message,) = nb.axis_box.children
    assert "No axis roles can be suggested" in message.value
    assert "resolve the errors" in nb.run_status.value


def test_issues_html_escapes_and_limits_paths():
    paths = [f"<f{i}>.tif" for i in range(25)]
    text = issues_html([warning("w", "Odd & strange.", paths), error("e", "Bad.", action="Fix.")])
    assert text.index("Error:") < text.index("Warning:")
    assert "Odd &amp; strange." in text and "&lt;f0&gt;.tif" in text
    assert "... and 5 more" in text and "Processing is blocked" in text
    assert "No problems found" in issues_html([])


def test_table_html_escapes():
    assert table_html([["a<"], ["b&"]]) == (
        "<table><thead><tr><th>a&lt;</th></tr></thead><tbody><tr><td>b&amp;</td></tr></tbody>"
        "</table>"
    )


def test_display_range_ignores_non_finite_values():
    pixels = np.array([[np.nan, 1.0], [2.0, np.inf]])
    low, high = display_range(pixels, 0, 100)
    assert (low, high) == (1.0, 2.0)
    assert display_range(np.full((2, 2), np.nan)) == (0.0, 1.0)
    assert display_range(np.zeros((2, 2), np.uint8)) == (0.0, 1.0)


def test_plane_png():
    plane = Plane(None, None, None, np.arange(12, dtype=np.uint16).reshape(3, 4))
    assert plane_png(plane).startswith(b"\x89PNG")


# The notebook file ----------------------------------------------------------------------


def test_notebook_is_valid_and_has_no_outputs():
    notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    assert notebook["nbformat"] == 4
    code = [cell for cell in notebook["cells"] if cell["cell_type"] == "code"]
    assert all(cell["outputs"] == [] and cell["execution_count"] is None for cell in code)


def test_notebook_only_calls_the_package():
    notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    sources = ["".join(c["source"]) for c in notebook["cells"] if c["cell_type"] == "code"]
    assert sources[0].startswith("from morphobricks_preprocessing.notebook_ui import")
    assert sources[1:] == [
        "notebook.folder_step()",
        "notebook.axis_step()",
        "notebook.run_step()",
        "notebook.results_step()",
    ]


def test_notebook_cells_run():
    notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    namespace: dict = {}
    for cell in notebook["cells"]:
        if cell["cell_type"] == "code":
            exec("".join(cell["source"]), namespace)  # noqa: S102 - our own notebook
    assert isinstance(namespace["notebook"], PreprocessingNotebook)
