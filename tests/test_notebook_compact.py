"""The compact layout of the shipped notebook, and Compare groups in the run step.

Owner's test of v0.5.0 (10 November 2026): (a) each step fits a laptop screen: one row of
chips, the step's cards in one column (the page scrolls, never a box inside it: owner's test of
the redesign, same day), explanations folded; (b) the optional
statistics page is gone: after a run, the run step has a collapsed *Compare groups* panel for
that run's table, without a table chooser and without the intensity checks a second time.
The pixel heights themselves are looked at in screenshots, not here.
"""

import re

import pytest

from test_notebook_check_save import answer_all, exp  # noqa: F401

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")

import ipywidgets as widgets  # noqa: E402

from morphobricks_preprocessing import notebook_style as ui  # noqa: E402
from morphobricks_preprocessing.notebook_compact import CHIPS  # noqa: E402
from morphobricks_preprocessing.notebook_render import issues_brief_html  # noqa: E402
from morphobricks_preprocessing.notebook_stepper import STEPPER_CSS, about, field_rows  # noqa: E402
from morphobricks_preprocessing.notebook_ui import PreprocessingNotebook  # noqa: E402
from morphobricks_preprocessing.validation import error, warning  # noqa: E402
from test_notebook_axes_quick import stepper  # noqa: E402


def _text(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def _holds(widget, target):
    if widget is target:
        return True
    return any(_holds(child, target) for child in getattr(widget, "children", ()))


def _ran(exp):  # noqa: F811
    nb = stepper(*exp)
    nb.quick_confirm.click()
    answer_all(nb)
    assert nb.save_experiment()
    assert nb.run().written
    return nb


# --- (a) one screen ------------------------------------------------------------------------------


def test_the_strip_is_short_names_and_the_page_keeps_the_full_title(exp):  # noqa: F811
    nb = stepper(*exp)
    st = nb._stepper
    assert "mb-compact" in st.widget._dom_classes
    names = [re.sub(r"^(✓ )?\d+ ", "", chip.description).split(" · ")[0] for chip in st.chips]
    assert names == [CHIPS.get(step.title, step.title) for step in st.steps]
    assert names == ["Images", "Axes", "Channels", "Masks", "Design", "Save", "Run"]
    assert sum(len(chip.description) for chip in st.chips) < 150  # one row at 1366 px
    head = st.pages[5].children[1].children[0].value
    assert "Step 6 of 7 · Check and save" in head and "mb-purpose" in head


def test_each_page_is_a_title_line_and_one_column_that_never_scrolls_by_itself(exp):  # noqa: F811
    nb = stepper(*exp)
    for page in nb._stepper.pages:
        head, body = page.children[1].children
        assert isinstance(head, widgets.HTML) and "mb-step-body" in body._dom_classes
    # Owner's test of the redesign (10 November 2026): the page scrolls, never a box inside it.
    compact = [line for line in STEPPER_CSS.splitlines() if ".mb-compact" in line]
    assert not any("overflow-y: auto" in line or "calc(" in line for line in compact)
    assert ".mb-compact .mb-table-wrap, .mb-compact .mb-paths { max-height: none;" in STEPPER_CSS
    assert ".mb-compact .mb-label-rows { max-height: none !important;" in STEPPER_CSS
    assert "mb-label-rows" in nb.label_previews.rows._dom_classes


def _panel(widget):
    """Return True when ``widget`` is, or holds anywhere inside it, a card or a fold."""
    if "mb-card" in getattr(widget, "_dom_classes", ()) or isinstance(widget, widgets.Accordion):
        return True
    return any(_panel(child) for child in getattr(widget, "children", ()))


def _side_by_side(widget, found=None):
    """Return the boxes that lay cards or panels out in a row (pictures and inputs may be).

    A row is an ``HBox`` or a ``GridBox`` (the earlier axes step put its cards in a grid), and
    a card wrapped in another box is still a card in that row.
    """
    found = [] if found is None else found
    if isinstance(widget, (widgets.HBox, widgets.GridBox)):
        if len(widget.children) > 1 and any(_panel(child) for child in widget.children):
            found.append(widget)
    for child in getattr(widget, "children", ()):
        _side_by_side(child, found)
    return found


def test_a_row_of_cards_is_found_in_a_grid_and_inside_another_box():
    # Review of #275: the walk looked only at HBox rows holding a card directly, so the cards
    # side by side in a GridBox (main's axes step) or wrapped in a VBox were not found.
    card = ui.card("A", [widgets.HTML("a")])
    other = ui.card("B", [widgets.HTML("b")])
    assert _side_by_side(widgets.GridBox([card, other])) != []
    assert _side_by_side(widgets.HBox([widgets.VBox([card]), widgets.VBox([other])])) != []
    assert _side_by_side(widgets.HBox([widgets.HTML("a"), widgets.Dropdown()])) == []
    assert _side_by_side(widgets.VBox([card, other])) == []
    assert _side_by_side(widgets.HBox([card])) == []


def test_every_page_is_a_single_column_of_cards(exp):  # noqa: F811
    nb = _ran(exp)
    for page in nb._stepper.pages:
        assert _side_by_side(page) == []
        body = page.children[1].children[1]
        assert isinstance(body, widgets.VBox) and not isinstance(body, widgets.HBox)
    # The axes step: roles, then the example, then the pixel size, one under the other.
    order = list(nb.axis_box.children)
    assert order.index(nb.preview_card) < order.index(nb.axis_sizes_slot)
    assert isinstance(nb.scan_box, widgets.VBox)
    assert list(nb.scan_box.children) == [nb.scan_example, nb.scan_another, nb.scan_tree]


def _boxes(widget):
    yield widget
    for child in getattr(widget, "children", ()):
        yield from _boxes(child)


def test_the_run_log_is_text_in_the_page_not_a_box_that_scrolls(exp):  # noqa: F811
    # Review of #275: the run step's technical log was a 14em read-only Textarea, a box with
    # its own scroll bar. It is now wrapped text in its fold; the log is the same.
    nb = _ran(exp)
    for page in nb._stepper.pages:
        assert not [w for w in _boxes(page) if isinstance(w, widgets.Textarea) and w.disabled]
    assert nb.details.value and "run" in nb.details.value.lower()
    assert _holds(nb.details_box, nb.log_text)
    assert nb.details.value.splitlines()[-1].replace("&", "&amp;")[:20] in nb.log_text.value


def test_explanations_are_folded_behind_about_this_step(exp):  # noqa: F811
    nb = stepper(*exp)
    for index in range(6):
        first = nb._stepper.pages[index].children[1].children[1].children[1]
        assert first.value.startswith(
            "<details class='mb-about'><summary>About this step</summary>"
        )
    html = about("<b>Why</b>").value
    assert "<details" in html and "<b>Why</b>" in html and " open" not in html
    assert not ui.is_visible(nb.roles_help)  # said once, under About this step
    assert (
        "Exactly one Y and one X" in nb._stepper.pages[1].children[1].children[1].children[1].value
    )


def test_the_folders_are_one_card_of_three_labelled_rows(exp):  # noqa: F811
    nb = stepper(*exp)
    page = nb._stepper.pages[0]
    assert _holds(page, nb.folders_card)
    for box in (
        nb.folder,
        nb.masks_folder,
        nb.output,
        nb.output_hint,
        nb.scan_button,
        nb.exclude_box,
    ):
        assert _holds(page, box)
    for card in (nb.folder_card, nb.output_card, nb.masks_input_card):
        assert not _holds(page, card)  # their help paragraphs are under About this step
    assert not _holds(page, nb.masks_card)  # what the check paired is on Masks
    labels = [w.value for w in field_rows([("Images", []), ("Masks (optional)", [])]).children[::2]]
    assert [_text(v) for v in labels] == ["Images", "Masks (optional)"]


def test_after_the_check_the_viewer_stays_and_the_details_are_folded(exp):  # noqa: F811
    nb = PreprocessingNotebook(str(exp[0]), output_folder=str(exp[2]))
    nb.steps()
    nb.masks_folder.value = str(exp[1])
    assert not ui.is_visible(nb.found_fold) and not ui.is_visible(nb.report_fold)
    nb.scan_button.click()
    assert ui.is_visible(nb.scan_box)
    nb.check_button.click()
    # The viewer stays, now over the images as the check read them; the scan's table is gone.
    assert ui.is_visible(nb.scan_box) and nb.scan_status.value == ""
    assert not ui.is_visible(nb.scan_tree) and len(nb.scan_viewer.entries) == len(
        nb.preflight.discovery.images
    )
    assert ui.is_visible(nb.found_fold) and nb.found_fold.selected_index is None
    assert ui.is_visible(nb.report_fold) and nb.report_fold.selected_index is None
    assert _holds(nb.found_fold, nb.labels_card) and _holds(nb.report_fold, nb.check_report.widget)
    nb.folder.value = str(exp[0]) + "/"
    assert not ui.is_visible(nb.found_fold)  # another folder: nothing found is shown


def test_the_welcome_and_the_statistics_page_take_no_room(exp):  # noqa: F811
    nb = PreprocessingNotebook(str(exp[0]), output_folder=str(exp[2]))
    alone = nb.results_step()  # without the stepper: the page other notebooks may show
    assert "Calculate the statistics (optional)" in alone.children[1].children[0].value
    nb.steps()
    hidden = nb.results_step()
    assert hidden.children == () and hidden.layout.display == "none"
    assert len(nb.welcome().children) == 1


def test_a_refusal_is_one_sentence_with_the_files_and_the_fix_on_demand():
    paths = [f"ctrl/rep2/{i}.tif" for i in range(4)]
    issue = error(
        "different_channel_counts",
        "The images have different numbers of channels: 3 channels (10 images), 4 channels (4 "
        "images). Each row of the histogram table holds every channel.",
        paths,
        action="Split the images.",
    )
    html = issues_brief_html([warning("w", "Odd.", ["a.tif"]), issue])
    assert html.index("different channel counts") < html.index("Warning · w")  # errors first
    shown = html.split("<details>")[0]
    assert _text(shown) == (
        "Error · different channel counts: The images have different numbers of channels: 3 "
        "channels (10 images), 4 channels (4 images)."
    )
    assert "Files and what to do" in html and "Each row of the histogram table" in html
    for path in paths:
        assert path in html
    assert "What to do:</b> Split the images." in html and "mb-error" in html
    assert issues_brief_html([]) == ""


# --- (b) Compare groups in the run step ----------------------------------------------------------


def test_the_run_step_shows_only_what_is_missing_and_folds_the_rest(exp):  # noqa: F811
    nb = stepper(*exp)
    page = nb._stepper.pages[6]
    assert ui.is_visible(nb.readiness_card) and nb.run_button.disabled
    for fold, inner in (
        (nb.settings_fold, nb.settings_card),
        (nb.plan_fold, nb.plan_card),
        (nb.checks_fold, nb.run_checks.card),
        (nb.run_report_fold, nb.run_report.widget),
    ):
        assert _holds(page, fold) and _holds(fold, inner) and fold.selected_index is None
    assert not ui.is_visible(nb.compare_fold) and not ui.is_visible(nb.checks_fold)
    nb.quick_confirm.click()
    answer_all(nb)
    assert nb.save_experiment()
    assert not nb.run_button.disabled and not ui.is_visible(nb.readiness_card)  # nothing missing


def test_compare_groups_appears_collapsed_after_a_run_for_that_run_s_table(exp):  # noqa: F811
    nb = _ran(exp)
    page = nb._stepper.pages[6]
    assert ui.is_visible(nb.compare_fold) and nb.compare_fold.selected_index is None
    assert ui.is_visible(nb.checks_fold) and ui.is_visible(nb.run_report_fold)
    assert not ui.is_visible(
        nb.plan_fold
    )  # it plans a next run; shown again when a setting changes
    assert _holds(page, nb.compare_fold) and _holds(nb.compare_fold, nb.statistics_button)
    assert not _holds(page, nb.csv_card) and not _holds(page, nb.csv_file)  # no table to choose
    assert not _holds(page, nb.table_checks.card) and not _holds(page, nb.stats_next)
    assert nb.statistics.value == ""  # nothing is calculated before the click
    nb.statistics_button.click()
    assert (
        "calculated." in nb.stats_status.value and str(nb.result.csv_path) in nb.stats_status.value
    )
    assert ui.is_visible(nb.statistics_card) and ui.is_visible(nb.distribution_card)
    assert nb.distribution.value[:4] == b"\x89PNG"
    assert nb.table_checks.report is None  # the intensity checks are not run a second time
    before = nb.statistics.value
    nb.level.value = "biological_repeat"  # another grouping recalculates at once
    assert nb.statistics.value != before


def test_compare_groups_gives_the_same_statistics_as_the_old_page(exp):  # noqa: F811
    nb = _ran(exp)
    nb.statistics_button.click()
    root, masks, out = exp
    old = PreprocessingNotebook(str(root), output_folder=str(out))
    old.results_step()
    old.csv_file.value = str(nb.result.csv_path)
    old.statistics_button.click()
    assert old.statistics.value == nb.statistics.value
    assert old.distribution.value == nb.distribution.value
    assert old.table_checks.report is not None  # the old page still checks any table
