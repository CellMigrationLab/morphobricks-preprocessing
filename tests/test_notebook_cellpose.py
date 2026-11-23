"""The Cellpose notebook file, and its cells run headless on a synthetic run with a stand-in model.

Cellpose is not installed in CI, so the cells run in one namespace (as in a kernel) against the
stand-in of ``tests/cellpose_stub.py``; the install cell is run with the package installer
replaced in ``test_notebook_cellpose_install.py``. Like ``test_notebook_file.py`` this checks
the file's shape (a Colab form with its code hidden, Markdown above every code cell, the pins)
and what the cells do.
"""

import html
import json
import re
import subprocess
from pathlib import Path

import numpy as np
import pytest
import tifffile

from cellpose_stub import custom_model, install
from morphobricks_preprocessing import notebook_cellpose
from morphobricks_preprocessing import segmentation_cellpose as cp
from morphobricks_preprocessing.manifest import MANIFEST_FILENAME
from morphobricks_preprocessing.segmentation_record import read_record
from notebook_pin import code_sources, history_missing, install_setting, pin_differences
from test_segmentation_handoff import MASKS_SECTION

ROOT = Path(__file__).parents[1]
NOTEBOOK = ROOT / "notebooks/MorphoBricks_Cellpose.ipynb"
VERSIONS = ROOT / "notebooks/notebook_latest_versions.yaml"
INSTALL, DRIVE, OPEN, CHANNELS, MODEL, PREVIEW, BATCH, NEXT = range(8)
TITLES = (
    "# @title 1. Install Cellpose and MorphoBricks preprocessing",
    "# @title 2. Mount Google Drive",
    "# @title 3. Open the preprocessing run",
    "# @title 4. Choose the mask type and the channels",
    "# @title 5. Choose the model and the settings, then load the model",
    "# @title 6. Preview one image",
    "# @title 7. Segment all images",
    "# @title 8. What to do next",
)
HEADINGS = (
    "## **1. Install Cellpose and MorphoBricks preprocessing**",
    "## **2. Mount Google Drive**",
    "## **3. Open the preprocessing run**",
    "## **4. Choose the mask type and the channels**",
    "## **5. Choose the model and the settings, then load the model**",
    "## **6. Preview one image**",
    "## **7. Segment all images**",
    "## **8. What to do next**",
)


def notebook():
    return json.loads(NOTEBOOK.read_text(encoding="utf-8"))


def source(number):
    return code_sources(NOTEBOOK)[number]


def setting(name):
    return install_setting(source(INSTALL), name)


def with_params(number, params):
    """Return cell ``number`` with its Colab form fields set to ``params`` (each must exist)."""
    text = source(number)
    for name, value in params.items():
        line = f"{name} = {value!r}"  # a function, so backslashes (Windows paths) stay as given
        text, count = re.subn(
            rf"^{name} = .*?(  # @param.*)$", lambda m, line=line: line + m[1], text, flags=re.M
        )
        assert count == 1, f"cell {number + 1} has no form field {name}"
    return text


def run_cell(namespace, number, **params):
    exec(compile(with_params(number, params), f"<cell {number + 1}>", "exec"), namespace)


# the file ---------------------------------------------------------------------------------------


def test_the_notebook_is_an_introduction_and_eight_explained_code_cells():
    nb = notebook()
    assert nb["nbformat"] == 4
    cells = nb["cells"]
    assert [c["cell_type"] for c in cells] == ["markdown", *("markdown", "code") * 8]
    intro = "".join(cells[0]["source"])
    for text in (
        "Cellpose 4.2.1.1",
        "Hugging Face",
        "2D images only",
        "at most three",
        "native scale",
    ):
        assert text in intro
    code = [c for c in cells if c["cell_type"] == "code"]
    assert [c["source"][0].rstrip("\n") for c in code] == list(TITLES)
    for position, (heading, cell) in enumerate(zip(HEADINGS, code, strict=True)):
        assert heading in "".join(cells[1 + 2 * position]["source"]).splitlines()
        assert cells[2 + 2 * position] is cell or cells[2 + 2 * position] == cell
        assert cell["outputs"] == [] and cell["execution_count"] is None
        assert cell["metadata"].get("jupyter", {}).get("source_hidden") is True
        assert cell["metadata"].get("cellView") == "form"
    assert all("\\n" not in "".join(c["source"]) for c in cells if c["cell_type"] == "markdown")


def test_the_cells_stay_thin_the_logic_is_in_the_package():
    for number in range(OPEN, NEXT + 1):
        text = source(number)
        assert len(text.splitlines()) < 45
        for other in ("import cellpose", "from cellpose", "import torch", "tifffile", "np."):
            assert other not in text  # nothing here reads, computes or writes pixels
    assert "workflow.run(" in source(BATCH) and "workflow.preview(" in source(PREVIEW)
    assert "workflow.load(" in source(MODEL) and "workflow.choose_channels(" in source(CHANNELS)


def test_the_pins_are_cellpose_4_2_1_1_and_a_preprocessing_commit_with_this_code():
    assert setting("CELLPOSE_VERSION") == cp.CELLPOSE_VERSION == "4.2.1.1"
    ref = setting("RELEASE_REF")
    assert re.fullmatch(r"[0-9a-f]{40}", ref)
    assert setting("REPOSITORY").endswith("CellMigrationLab/morphobricks-preprocessing.git")
    assert "DEVELOPMENT_OVERRIDE = False" in source(INSTALL)
    command = ["git", "-C", str(ROOT), "cat-file", "-e", f"{ref}^{{commit}}"]
    if subprocess.run(command, capture_output=True).returncode != 0:
        history_missing("the pinned commit is not in this checkout (shallow clone or no git)")
    # The notebook installs the package at RELEASE_REF: that commit must hold the code the
    # notebook's cells run (every module they import, at any depth), the build's inputs and
    # the install-relevant pyproject.toml, exactly as HEAD has them (issue #421).
    changed = pin_differences(ROOT, ref, "HEAD", code_sources(NOTEBOOK))
    assert changed == [], (
        f"what the notebook runs changed after RELEASE_REF: move the pin ({changed})"
    )


def test_the_version_file_names_the_notebooks_current_version():
    index = dict(
        re.fullmatch(r'"([^"]+)": "([^"]+)"', line).groups()
        for line in VERSIONS.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    )
    assert (
        index[NOTEBOOK.stem] == setting("current_version")
        and setting("notebook_name") == NOTEBOOK.stem
    )


def test_the_text_warns_about_the_restarts_the_downloads_and_the_unverified_dinov3_pin():
    text = {n: "".join(c["source"]) for n, c in enumerate(notebook()["cells"])}
    assert "Restart session" in text[1] and "numpy" in text[1]
    assert "restart the runtime" in text[9] and "--no-deps" in text[9]
    assert "not verified against `cpdino`" in text[9] and "22 November 2026" in text[9]
    for number in (0, 9):  # the introduction and cell 5
        assert "flow-threshold quality step to the CPU" in text[number]
        assert "not refused" in text[number] or "is **not refused**" in text[number]
    assert "every parameter and buffer" in text[9]


# the cells run on a synthetic run ----------------------------------------------------------


@pytest.fixture
def cells(mixed_run, monkeypatch, tmp_path):
    state = install(monkeypatch, tmp_path)
    namespace = {}
    return type("C", (), {"ns": namespace, "stub": state, "run": mixed_run})()


def open_and_choose(cells, **extra):
    run = cells.run
    run_cell(cells.ns, OPEN, run_folder=str(run.out), masks_folder=str(run.masks))
    run_cell(cells.ns, CHANNELS, mask_type="Cells")
    picker = cells.ns["workflow"].picker
    for dropdown, value in zip(picker.dropdowns, extra.get("ids", ("TUB", "DNA", "")), strict=True):
        dropdown.value = value
    return picker


def test_the_cells_segment_a_mixed_run_end_to_end(cells):
    run = cells.run
    picker = open_and_choose(cells)
    assert picker.channel_ids == ("TUB", "DNA") and picker.plan is not None
    run_cell(cells.ns, MODEL, device="cpu", diameter="30", batch_size=4)
    workflow = cells.ns["workflow"]
    assert workflow.loaded.name == "cpsam_v2" and workflow.loaded.device == "cpu"
    assert workflow.loaded.file == "cpsam_v2"
    run_cell(cells.ns, PREVIEW)
    assert len(cells.stub.calls) == 1 and not run.masks.exists()  # a preview writes nothing
    run_cell(cells.ns, BATCH)
    result = cells.ns["result"]
    fovs = run.inputs.fovs()
    assert result.counts() == {
        "written": len(fovs),
        "skipped": 0,
        "needs_attention": 0,
        "failed": 0,
    }
    assert len(cells.stub.calls) == 1 + len(fovs)
    table = workflow.shown[-1]
    assert all(f.relative_path in table for f in fovs) and "written" in table
    batch = read_record(run.masks, "Cells").batches[0]
    assert batch.channel_ids == ("TUB", "DNA") and batch.parameters["diameter"] == 30.0
    assert batch.parameters["batch_size"] == 4 and batch.device == "cpu"
    assert not list(run.masks.rglob("*.png"))  # the preview figure is never saved
    stored = tifffile.imread(run.masks / "Cells" / fovs[0].relative_path)
    assert stored.dtype.name == "int32" and stored.shape == (8, 8)


def test_a_partial_channel_is_refused_and_the_message_is_shown_verbatim(cells):
    picker = open_and_choose(cells, ids=("DNA", "EXTRA", ""))
    assert picker.plan is None
    assert "A_SCOPE_INCOMPLETE" in picker.refusal and "EXTRA" in picker.refusal
    assert picker.refusal.startswith("The channel 'EXTRA' is present in 4 of 12 images")
    assert html.escape(picker.refusal) in picker.message.value  # shown to the user, unchanged
    options = [label for label, _ in picker.dropdowns[1].options]
    assert any(
        label.startswith("EXTRA (in ") for label in options
    )  # the dropdown says it is partial
    with pytest.raises(notebook_cellpose.CellStop) as stopped:
        run_cell(cells.ns, MODEL, device="cpu")
    assert str(stopped.value) == picker.refusal and cells.stub.constructed == []


def test_a_blank_diameter_is_the_native_scale_in_the_cell_and_the_record(cells):
    open_and_choose(cells)
    run_cell(cells.ns, MODEL, device="cpu")
    shown = " ".join(cells.ns["workflow"].shown)
    assert "native scale" in shown and "does not estimate" in shown
    run_cell(cells.ns, BATCH)
    assert all(call.kwargs["diameter"] is None for call in cells.stub.calls)
    assert read_record(cells.run.masks, "Cells").batches[0].parameters["diameter"] is None


@pytest.mark.parametrize("diameter", ["0", "-5", "abc"])
def test_a_diameter_of_zero_stops_the_cell_with_the_reason(cells, diameter):
    open_and_choose(cells)
    with pytest.raises(notebook_cellpose.CellStop, match="diameter"):
        run_cell(cells.ns, MODEL, device="cpu", diameter=diameter)
    assert cells.stub.constructed == []


def test_a_gpu_that_is_not_there_stops_the_cell_and_nothing_loads(cells):
    open_and_choose(cells)
    with pytest.raises(notebook_cellpose.CellStop, match="cuda was asked for but is not available"):
        run_cell(cells.ns, MODEL)  # the form's default device is cuda: no silent CPU
    assert cells.stub.constructed == [] and cells.ns["workflow"].loaded is None


def test_an_unknown_or_missing_model_stops_the_cell_with_no_substitution(cells):
    open_and_choose(cells)
    with pytest.raises(notebook_cellpose.CellStop, match="never substitutes"):
        run_cell(cells.ns, MODEL, device="cpu", custom_model_file="/no/such/model")
    assert cells.stub.constructed == []
    path = custom_model(cells.run.out.parent)
    run_cell(cells.ns, MODEL, device="cpu", custom_model_file=str(path))
    assert cells.ns["workflow"].loaded.name == "my_model"


def test_the_weights_download_needs_its_box_and_the_cell_says_where_from(cells):
    cells.stub.models_dir.joinpath("cpsam_v2").unlink()
    open_and_choose(cells)
    with pytest.raises(notebook_cellpose.CellStop, match="Hugging Face"):
        run_cell(cells.ns, MODEL, device="cpu")
    assert cells.stub.downloaded == []
    assert "downloaded from Hugging Face on first use" in " ".join(cells.ns["workflow"].shown)
    run_cell(cells.ns, MODEL, device="cpu", download_weights=True)
    assert cells.stub.downloaded == ["cpsam_v2"]


def test_the_load_card_says_whether_the_weights_were_downloaded_and_from_where(cells):
    open_and_choose(cells)
    run_cell(cells.ns, MODEL, device="cpu")
    assert "nothing downloaded" in cells.ns["workflow"].shown[-1]
    cells.stub.models_dir.joinpath("cpsam").unlink()
    run_cell(cells.ns, MODEL, model="cpsam", device="cpu", download_weights=True)
    assert "downloaded now from https://huggingface.co/" in cells.ns["workflow"].shown[-1]
    run_cell(cells.ns, BATCH)
    params = read_record(cells.run.masks, "Cells").batches[0].parameters
    assert params["weights_downloaded"] is True and params["weights_source"].endswith("/cpsam")


@pytest.mark.parametrize("cell", [PREVIEW, BATCH])
def test_a_channel_choice_refused_after_loading_stops_the_cell_once_and_starts_nothing(cells, cell):
    picker = open_and_choose(cells)
    run_cell(cells.ns, MODEL, device="cpu")
    picker.dropdowns[1].value = "EXTRA"  # the dropdowns change after the model is loaded
    assert picker.plan is None
    with pytest.raises(notebook_cellpose.CellStop) as stopped:
        run_cell(cells.ns, cell)
    assert str(stopped.value) == picker.refusal and "A_SCOPE_INCOMPLETE" in str(stopped.value)
    assert (
        stopped.value.__cause__ is not None and stopped.value.__context__ is stopped.value.__cause__
    )
    assert cells.stub.calls == [] and not cells.run.masks.exists()  # no image, no record
    picker.dropdowns[1].value = "DNA"  # a valid choice again: the cell runs
    run_cell(cells.ns, cell)
    assert len(cells.stub.calls) >= 1


def test_the_preview_figure_uses_the_display_contrast_and_shows_non_finite_pixels():
    stack = np.arange(32, dtype=np.float32).reshape(2, 4, 4)
    stack[0, 0, 0] = np.nan
    labels = np.zeros((4, 4), np.uint16)
    labels[1:3, 1:3] = 1
    figure = notebook_cellpose.preview_figure(stack, labels, ("A", "B"))
    assert len(figure.axes) == 3 and "1 objects" in figure.axes[-1].get_title()


def test_failures_are_in_the_final_table_and_the_batch_goes_on(cells):
    open_and_choose(cells)
    run_cell(cells.ns, MODEL, device="cpu")

    def fail_third(stack):
        if len(cells.stub.calls) == 3:
            raise RuntimeError("out of memory")

    cells.stub.fail = fail_third
    run_cell(cells.ns, BATCH)
    result, fovs = cells.ns["result"], cells.run.inputs.fovs()
    assert result.counts() == {
        "written": len(fovs) - 1,
        "skipped": 0,
        "needs_attention": 0,
        "failed": 1,
    }
    table = cells.ns["workflow"].shown[-1]
    assert "RuntimeError: out of memory" in table and "failed" in table
    run_cell(cells.ns, BATCH)  # again: the written images are skipped, the failed one is redone
    assert cells.ns["result"].counts() == {
        "written": 1,
        "skipped": len(fovs) - 1,
        "needs_attention": 0,
        "failed": 0,
    }


def test_the_batch_summary_reports_stray_backups_and_deletes_none(cells):
    open_and_choose(cells)
    run_cell(cells.ns, MODEL, device="cpu")
    folder = cells.run.masks / "Cells"
    folder.mkdir(parents=True)
    stray = folder / ".a.tif.previous.k3j2h1"  # left by a killed overwrite
    stray.write_bytes(b"earlier")
    run_cell(cells.ns, BATCH)
    shown = cells.ns["workflow"].shown
    assert any("Copies of earlier masks" in card and stray.name in card for card in shown)
    assert stray.read_bytes() == b"earlier"


def test_a_preview_names_an_image_of_the_run_or_is_refused(cells):
    open_and_choose(cells)
    run_cell(cells.ns, MODEL, device="cpu")
    with pytest.raises(notebook_cellpose.CellStop, match="not an image of this run"):
        run_cell(cells.ns, PREVIEW, preview_image="nowhere.tif")
    name = cells.run.inputs.fovs()[3].relative_path
    run_cell(cells.ns, PREVIEW, preview_image=name)
    assert name in cells.ns["workflow"].shown[-1] and len(cells.stub.calls) == 1


def test_a_cell_run_too_early_says_which_cell_to_run_first(cells):
    with pytest.raises(RuntimeError, match="Run cell 3 first"):
        run_cell(cells.ns, CHANNELS)
    run_cell(cells.ns, OPEN, run_folder=str(cells.run.out), masks_folder=str(cells.run.masks))
    with pytest.raises(RuntimeError, match="Run cell 4 first"):
        run_cell(cells.ns, NEXT)
    with pytest.raises(RuntimeError, match="Run cell 5 first"):
        run_cell(cells.ns, BATCH)


def test_a_run_folder_that_is_not_a_run_stops_the_cell_with_the_reason(cells, tmp_path):
    with pytest.raises(notebook_cellpose.CellStop):
        run_cell(
            cells.ns, OPEN, run_folder=str(tmp_path / "nothing"), masks_folder=str(tmp_path / "m")
        )
    with pytest.raises(notebook_cellpose.CellStop, match="masks folder"):
        run_cell(cells.ns, OPEN, run_folder=str(cells.run.out), masks_folder=" ")
    with pytest.raises(notebook_cellpose.CellStop, match="inside"):
        run_cell(
            cells.ns,
            OPEN,
            run_folder=str(cells.run.out),
            masks_folder=str(cells.run.experiment / "masks"),
        )
        run_cell(cells.ns, CHANNELS, mask_type="Cells")


def test_a_mask_type_preprocessing_would_refuse_stops_the_cell(cells):
    run_cell(cells.ns, OPEN, run_folder=str(cells.run.out), masks_folder=str(cells.run.masks))
    with pytest.raises(notebook_cellpose.CellStop):
        run_cell(cells.ns, CHANNELS, mask_type="NA")


def test_the_closing_cell_gives_the_next_steps_and_the_masks_pass_a_rerun(cells):
    from described_tree import check_masks, run_described
    from description_tree import MAPPING
    from morphobricks_preprocessing.run_inputs import RunInputs
    from morphobricks_preprocessing.segmentation_handoff import images_to_segment

    run = cells.run
    open_and_choose(cells)
    run_cell(cells.ns, MODEL, device="cpu")
    run_cell(cells.ns, BATCH)
    run_cell(cells.ns, NEXT)
    text = cells.ns["workflow"].shown[-1]
    for needle in (
        "[masks.Cells]",
        "representation = ",
        "Save the description",
        "Run preprocessing again",
        "Describe the masks",
        "MorphoFeature",
        str(run.masks),
    ):
        assert needle in text
    (run.out / "morphobricks.toml").write_bytes(run.data + MASKS_SECTION)
    again = run_described(run.experiment, MAPPING, output_folder=run.out, masks_folder=run.masks)
    assert again.written, [(i.code, i.message) for i in again.issues]
    assert sorted(i.code for i in again.issues) == []
    reopened = RunInputs.open(check_masks(run.out))
    assert reopened.mask_types() == ("Cells",)
    assert images_to_segment(reopened, "Cells", run.masks) == ()
    assert (run.out / MANIFEST_FILENAME).is_file()
