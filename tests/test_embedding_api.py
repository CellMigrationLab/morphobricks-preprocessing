"""The notebook's embedding API: what other bricks build their notebooks from (spec §9).

``PreprocessingNotebook`` and ``notebook_style`` are a declared, tested embedding API.
MorphoRelate's ``notebook_steps.py`` embeds steps 1 and 2: it constructs
``PreprocessingNotebook(image_folder)``, shows ``folder_step``/``axis_step`` with its own
words, calls ``check_folder()``, adds handlers to ``check_button`` and ``confirm_button``,
reads ``preflight`` and ``confirmed_mapping``, and builds its own pages with
``notebook_style`` (``page``, ``card``, ``intro``, ``button``, ``status``, ``clean_path``).

These tests pin that surface so a change to it fails here first: names, parameter names,
kinds and defaults (not annotation text). Changing it needs a coordinated change in the
bricks that embed it, and then an update here.
"""

import importlib
import inspect

import numpy as np
import pytest

from tiff_tree import write_data

widgets = pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")
# Imported here, not with importorskip: a broken embedding API must fail, not skip.
notebook_style = importlib.import_module("morphobricks_preprocessing.notebook_style")
notebook_ui = importlib.import_module("morphobricks_preprocessing.notebook_ui")
PreprocessingNotebook = notebook_ui.PreprocessingNotebook
ValidationIssue = importlib.import_module("morphobricks_preprocessing.validation").ValidationIssue

EMPTY = inspect.Parameter.empty
POSITIONAL = inspect.Parameter.POSITIONAL_OR_KEYWORD
KEYWORD = inspect.Parameter.KEYWORD_ONLY


def _parameters(function):
    """Return a callable's parameters as (name, kind, default), ``self`` left out."""
    return [
        (p.name, p.kind, p.default)
        for p in inspect.signature(function).parameters.values()
        if p.name != "self"
    ]


NOTEBOOK_METHODS = {
    "__init__": [
        ("experiment_folder", POSITIONAL, ""),
        ("output_folder", POSITIONAL, ""),
        ("describe", KEYWORD, False),
    ],
    "welcome": [],
    "folder_step": [
        ("title", POSITIONAL, None),
        ("purpose", POSITIONAL, None),
        ("intro", KEYWORD, None),
        ("show_output", KEYWORD, True),
        ("show_next", KEYWORD, True),
        ("show_design", KEYWORD, True),
    ],
    "axis_step": [
        ("title", POSITIONAL, None),
        ("purpose", POSITIONAL, None),
        ("intro", KEYWORD, None),
        ("show_next", KEYWORD, True),
    ],
    "channels_step": [
        ("title", POSITIONAL, None),
        ("purpose", POSITIONAL, None),
    ],
    "masks_step": [
        ("title", POSITIONAL, None),
        ("purpose", POSITIONAL, None),
    ],
    "check_save_step": [("title", POSITIONAL, None)],
    "save_experiment": [],
    "run_step": [],
    "results_step": [],
    "check_folder": [],
    "show_preview": [],
    "run": [],
    "show_statistics": [],
}

STYLE_FUNCTIONS = {
    "page": [
        ("title", POSITIONAL, EMPTY),
        ("subtitle", POSITIONAL, EMPTY),
        ("children", POSITIONAL, EMPTY),
    ],
    "card": [
        ("header", POSITIONAL, EMPTY),
        ("children", POSITIONAL, EMPTY),
        ("visible", KEYWORD, True),
    ],
    "accordion": [
        ("title", POSITIONAL, EMPTY),
        ("children", POSITIONAL, EMPTY),
        ("selected_index", KEYWORD, None),
    ],
    "intro": [("text", POSITIONAL, EMPTY)],
    "help_text": [("text", POSITIONAL, EMPTY)],
    "set_visible": [("widget", POSITIONAL, EMPTY), ("visible", POSITIONAL, EMPTY)],
    "is_visible": [("widget", POSITIONAL, EMPTY)],
    "status": [
        ("kind", POSITIONAL, EMPTY),
        ("title", POSITIONAL, EMPTY),
        ("lines", POSITIONAL, ()),
        ("raw", KEYWORD, False),
    ],
    "working": [("text", POSITIONAL, EMPTY)],
    "next_steps": [("action", POSITIONAL, EMPTY), ("detail", POSITIONAL, "")],
    "path_list": [("paths", POSITIONAL, EMPTY), ("limit", POSITIONAL, 20)],
    "tiles": [("items", POSITIONAL, EMPTY)],
    "table": [("rows", POSITIONAL, EMPTY), ("numeric_from", POSITIONAL, None)],
    "checklist": [("items", POSITIONAL, EMPTY)],
    "button": [
        ("description", POSITIONAL, EMPTY),
        ("style", KEYWORD, ""),
        ("icon", KEYWORD, ""),
        ("tooltip", KEYWORD, ""),
        ("main", KEYWORD, False),
    ],
    "clean_path": [("text", POSITIONAL, EMPTY)],
    "ask_directory": [("initial", POSITIONAL, "")],
    "ask_open_file": [
        ("initial", POSITIONAL, ""),
        ("filetypes", POSITIONAL, (("All files", "*.*"),)),
    ],
    "no_dialog_status": [("dialog", POSITIONAL, EMPTY), ("target", POSITIONAL, EMPTY)],
    "ConfirmToggle": [
        ("question", POSITIONAL, EMPTY),
        ("explanation", POSITIONAL, EMPTY),
        ("confirm_label", POSITIONAL, EMPTY),
        ("confirmed_text", POSITIONAL, EMPTY),
        ("undo_label", POSITIONAL, EMPTY),
    ],
}


@pytest.mark.parametrize("name", sorted(NOTEBOOK_METHODS))
def test_notebook_methods_keep_their_parameters(name):
    assert _parameters(getattr(PreprocessingNotebook, name)) == NOTEBOOK_METHODS[name]


def test_notebook_public_methods_are_exactly_the_pinned_ones():
    public = {n for n in dir(PreprocessingNotebook) if not n.startswith("_")}
    assert public == set(NOTEBOOK_METHODS) - {"__init__"}


def test_notebook_style_public_names_are_exactly_the_pinned_ones():
    public = {
        name
        for name, value in vars(notebook_style).items()
        if not name.startswith("_")
        and getattr(value, "__module__", None) == notebook_style.__name__
    }
    assert public == set(STYLE_FUNCTIONS)
    assert isinstance(notebook_style.STYLE_CSS, str)


@pytest.mark.parametrize("name", sorted(STYLE_FUNCTIONS))
def test_notebook_style_functions_keep_their_parameters(name):
    assert _parameters(getattr(notebook_style, name)) == STYLE_FUNCTIONS[name]


def test_notebook_style_builds_what_an_embedding_page_uses():
    button = notebook_style.button("Run", style="success", icon="play", main=True)
    assert isinstance(button, widgets.Button)
    card = notebook_style.card("Output folder", [widgets.Text()])
    page = notebook_style.page("Step 3", "What it is for.", [notebook_style.intro("Why."), card])
    assert isinstance(card, widgets.VBox) and isinstance(page, widgets.VBox)
    assert "Done." in notebook_style.status("ok", "Done.", ["a line"])
    assert notebook_style.clean_path('  "/data/x"  ') == "/data/x"


def test_steps_one_and_two_embed_as_morphorelate_does(tmp_path):
    root = tmp_path / "images"
    write_data(root / "ctrl" / "rep1" / "a.tif", np.zeros((2, 8, 8), np.uint8), axes="TYX")
    write_data(root / "drug" / "rep1" / "a.tif", np.ones((2, 8, 8), np.uint8), axes="TYX")
    notebook = PreprocessingNotebook(str(root))
    assert notebook.preflight is None and notebook.confirmed_mapping is None
    assert isinstance(notebook.check_button, widgets.Button)
    assert isinstance(notebook.confirm_button, widgets.Button)
    folder = notebook.folder_step(
        "Step 1 · Choose your image folder",
        "Its purpose.",
        intro="<b>What happens here:</b> its words.",
        show_output=False,
        show_next=False,
    )
    axes = notebook.axis_step(
        "Step 2 · Check the image axes", "Its purpose.", intro="Why.", show_next=False
    )
    assert isinstance(folder, widgets.Widget) and isinstance(axes, widgets.Widget)
    seen = []
    notebook.check_button.on_click(lambda _: seen.append(notebook.preflight))
    notebook.check_button.click()  # the brick's handler runs first, then the embedder's
    assert seen and seen[0] is not None and seen[0].ok
    result = notebook.check_folder()
    assert result is notebook.preflight and result.ok
    assert len(result.discovery.images) == 2 and len(result.discovery.leaves) == 2
    # What MorphoRelate reads from the check: each issue's severity and the folder.
    assert result.issues == ()  # a clean folder; each issue says whether it is an error:
    assert isinstance(inspect.getattr_static(ValidationIssue, "is_error"), property)
    assert result.discovery.root == root
    # Step 2: an embedder's handler on confirm_button sees the brick's confirmed mapping.
    confirmed = []
    notebook.confirm_button.on_click(lambda _: confirmed.append(notebook.confirmed_mapping))
    notebook.preview_button.click()
    notebook.preview_checked.button.click()
    notebook.confirm_button.click()
    assert confirmed and confirmed[0] is notebook.confirmed_mapping
    assert "".join(notebook.confirmed_mapping.roles) == "TYX"
