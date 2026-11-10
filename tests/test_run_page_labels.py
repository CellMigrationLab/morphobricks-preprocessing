"""Step 3's run page: the labels of a described run (UX proposal 2026-11-09, Q4).

Channel IDs and replicate IDs come from the published description the run recorded (its
SHA-256 is in the manifest); an undescribed run, or a file edited since, keeps the table's
own names. The widgets are driven headless; plots are read from the figures they draw.
"""

from dataclasses import replace

import pytest

from channel_images import write_masks
from morphobricks_preprocessing.description_file import DESCRIPTION_FILENAME
from morphobricks_preprocessing.notebook_values import natural_key
from morphobricks_preprocessing.run_labels import (
    NO_LABELS,
    RunLabels,
    labels_from_description,
    recorded_labels,
)
from morphobricks_preprocessing.validation import warning
from test_notebook_check_save import TAGS, add_folder, answer_all, opened
from test_notebook_ui import bright_tree, confirmed

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")

from morphobricks_preprocessing import notebook_plots  # noqa: E402
from morphobricks_preprocessing import notebook_style as ui  # noqa: E402

FOLDERS = ["ctrl/rep1", "ctrl/rep2", "drug/rep1", "drug/day2"]  # day2 is replicate rep2


@pytest.fixture
def figures(monkeypatch):
    """Collect every figure the plots draw (instead of its PNG bytes)."""
    drawn = []
    monkeypatch.setattr(notebook_plots, "_png", lambda figure: drawn.append(figure) or b"\x89PNG")
    return drawn


def described(tmp_path, *, tissue=False):
    """Return a saved, described notebook (channels DNA/ACTIN/PHASE, day2 paired as rep2)."""
    root, masks, out = tmp_path / "exp", tmp_path / "masks", tmp_path / "out"
    for i, folder in enumerate(FOLDERS):
        add_folder(root, masks, folder, i + 1)
        if tissue:
            write_masks(masks, {f"{folder}/a.tif": i + 1}, {"tissue": "region"})
    nb = opened(root, masks, out)
    (dropdown,) = [
        row.children[2]
        for row in nb.pairing_grid.children[1:]
        if "day2" in row.children[1].children[0].value
    ]
    dropdown.value = "rep2"
    answer_all(nb)
    if tissue:
        nb.mask_card.questions["tissue"].choice.value = "binary"
    assert nb.save_experiment()
    return nb, out


def texts(figure):
    """Return the tick labels, titles and legend entries of every axes of ``figure``."""
    found = [figure._suptitle.get_text()] if figure._suptitle else []
    for axes in figure.axes:
        found += [t.get_text() for t in axes.get_xticklabels()] + [axes.get_title()]
        legend = axes.get_legend()
        found += [t.get_text() for t in legend.get_texts()] if legend else []
    return found


# --- 1. channel IDs on the plot tabs ----------------------------------------------------------


def test_a_described_run_names_the_plot_tabs_and_titles_by_channel_id(tmp_path, figures):
    nb, _ = described(tmp_path)
    assert nb.run().written
    panel = nb.run_checks
    assert [label for label, _ in panel.channel.options] == TAGS
    assert [value for _, value in panel.channel.options] == [0, 1, 2]
    assert any("Channel DNA: one point per image" in t for t in texts(figures[-2]))
    panel.channel.value = 1
    assert any("Channel ACTIN: one curve per biological repeat" in t for t in texts(figures[-1]))


def test_a_description_edited_after_the_run_is_not_used(tmp_path):
    nb, out = described(tmp_path)
    assert nb.run().written
    assert recorded_labels(out).channel(0) == "DNA"
    path = out / DESCRIPTION_FILENAME
    path.write_bytes(path.read_bytes() + b"\n# edited\n")  # not the bytes the run read
    assert recorded_labels(out) is NO_LABELS and NO_LABELS.channel(0) == "C0"
    assert recorded_labels(out / "missing") is NO_LABELS


def test_a_channel_position_the_units_do_not_agree_on_keeps_its_index(tmp_path):
    nb, out = described(tmp_path)
    assert nb.run().written
    from morphobricks_preprocessing.description_file import read_description

    description = read_description(out / DESCRIPTION_FILENAME)
    assert labels_from_description(description).channels == tuple(TAGS)
    unit = next(
        u
        for r in description.repeats.values()
        for m in r.members.values()
        for u in m.technical_repeats.values()
    )
    swapped = type(unit)(unit.source_folder, ("ACTIN", "DNA", "PHASE"), unit.display_name)
    member = next(iter(next(iter(description.repeats.values())).members.values()))
    changed = {**member.technical_repeats, next(iter(member.technical_repeats)): swapped}
    object.__setattr__(member, "technical_repeats", changed)
    assert labels_from_description(description).channels == (None, None, "PHASE")
    assert labels_from_description(description).channel(0) == "C0"


# --- 2. replicate IDs in plots and legends ----------------------------------------------------


def test_a_described_run_uses_the_replicate_id_not_the_folder_name(tmp_path, figures):
    nb, _ = described(tmp_path)
    assert nb.run().written
    boxes, curves = texts(figures[-2]), texts(figures[-1])
    assert "drug / rep2" in boxes and "drug / day2" not in boxes
    assert "rep2" in curves and "day2" not in curves  # the legend of drug's panel
    assert not any("day2" in t for t in boxes + curves)


def test_replicates_within_a_condition_are_ordered_by_replicate_id(tmp_path, figures):
    # drug/day2 is replicate rep2: by folder name it came before drug/rep1 (PR #241 kept that).
    nb, _ = described(tmp_path)
    assert nb.run().written
    ticks = [t.get_text() for t in figures[-2].axes[1].get_xticklabels()]
    assert ticks == ["ctrl / rep1", "ctrl / rep2", "drug / rep1", "drug / rep2"]
    (drug,) = [axes for axes in figures[-1].axes if axes.get_title() == "drug"]
    assert [t.get_text() for t in drug.get_legend().get_texts()] == ["rep1", "rep2"]


# --- 3. the no-problems icon -------------------------------------------------------------------


def test_no_problems_found_has_no_warning_icon(tmp_path):
    nb, _ = described(tmp_path)
    result = nb.run()
    assert result.written and not result.issues
    assert "no problems found" in nb.run_tiles.value
    assert "⚠" not in nb.run_tiles.value and "✅ no problems found" in nb.run_tiles.value


def test_problems_keep_the_warning_icon(tmp_path):
    nb, _ = described(tmp_path)
    result = nb.run()
    assert not result.issues and "\u2705 no problems found" in nb.run_tiles.value
    nb._show_run_tiles(replace(result, issues=(warning("odd", "Something to read."),)))
    assert "\u26a0\ufe0f 1 warning" in nb.run_tiles.value


# --- 4. the plan card after a run --------------------------------------------------------------


def test_what_will_happen_is_hidden_after_a_run_until_a_setting_changes(tmp_path):
    nb, _ = described(tmp_path)
    assert ui.is_visible(nb.plan_card)
    assert nb.run().written
    assert not ui.is_visible(nb.plan_card)  # it described the run that just happened
    nb._refresh_run_step()
    assert not ui.is_visible(nb.plan_card)
    nb.mode.value = "full"  # a setting changed: the card plans the next run again
    assert ui.is_visible(nb.plan_card) and "Mode <b>full</b>" in nb.plan.value
    assert nb.run().written
    assert not ui.is_visible(nb.plan_card)


def test_the_plan_stays_when_the_run_did_not_write(tmp_path):
    nb = confirmed(bright_tree(tmp_path / "exp"), output_folder=str(tmp_path / "out"))
    nb._call_run = lambda settings: (_ for _ in ()).throw(RuntimeError("boom"))
    nb.run()
    assert ui.is_visible(nb.plan_card)


# --- 5. Describe the masks ---------------------------------------------------------------------


def test_describe_the_masks_states_the_declared_kind_beside_the_suggestion(tmp_path):
    nb, _ = described(tmp_path, tissue=True)
    assert nb.run().written
    nb.mask_step_button.click()
    shown = nb.mask_step_result.value
    assert "<th>Mask</th><th>Declared kind</th><th>Kind (suggested)</th>" in shown
    assert "<td>Nuclei</td><td>objects</td><td>one object per label</td>" in shown
    assert "<td>tissue</td><td>regions</td><td>one value per image" in shown


def test_the_mask_table_has_one_mask_column_not_a_type_and_an_id(tmp_path):
    nb, _ = described(tmp_path, tissue=True)
    assert nb.run().written
    nb.mask_step_button.click()
    shown = nb.mask_step_result.value
    assert "<th>Mask type</th>" not in shown and "<th>Mask ID</th>" not in shown
    assert "<td>Nuclei</td><td>Nuclei</td>" not in shown  # the same text twice


def test_a_mask_without_an_answer_says_so_and_a_different_id_is_kept(tmp_path):
    nb, _ = described(tmp_path, tissue=True)
    assert nb.run().written
    answers = nb.draft_answers
    renamed = {
        ("Nuclei_seg" if key == "Nuclei" else key): mask
        for key, mask in answers.masks.items()
        if mask.source_type != "tissue"
    }
    nb.draft_answers = replace(answers, masks=renamed)
    nb.mask_step_button.click()
    shown = nb.mask_step_result.value
    assert "<td>Nuclei_seg (Nuclei)</td><td>objects</td>" in shown
    assert "<td>tissue</td><td>not described yet</td>" in shown


def test_replicate_ids_are_shown_in_natural_order_rep2_before_rep10():
    # Display convention (PR 17b): numbers in an ID are compared as numbers; the folder breaks
    # a tie. Nothing is renamed or stored in this order.
    labels = RunLabels(
        (),
        {
            ("drug", "b"): "rep10",
            ("drug", "a"): "rep2",
            ("drug", "c"): "rep2",
            ("ctrl", "x"): "rep1",
        },
    )
    found = sorted(
        [("drug", "b"), ("drug", "c"), ("ctrl", "x"), ("drug", "a")], key=lambda k: labels.order(*k)
    )
    assert found == [("ctrl", "x"), ("drug", "a"), ("drug", "c"), ("drug", "b")]
    assert sorted(["rep10", "rep2", "rep1", "rep02"], key=natural_key) == [
        "rep1",
        "rep02",
        "rep2",
        "rep10",
    ]


def test_natural_order_compares_a_bare_name_with_a_numbered_one_and_mixed_ids():
    # Regression (17b review): "rep" against "rep2" compared the tie-break text with a number.
    assert sorted(["rep2", "rep", "ctrl2", "ctrl"], key=natural_key) == [
        "ctrl",
        "ctrl2",
        "rep",
        "rep2",
    ]
    mixed = ["repA", "rep10", "rep02", "rep2", "rep", "2rep", "rep2b"]
    first = sorted(mixed, key=natural_key)
    assert first == sorted(reversed(mixed), key=natural_key)  # deterministic, any input order
    assert first == ["2rep", "rep", "rep02", "rep2", "rep2b", "rep10", "repA"]
    labels = RunLabels(repeats={("ctrl", "a"): "rep", ("ctrl", "b"): "rep2"})
    assert sorted([("ctrl", "b"), ("ctrl", "a")], key=lambda k: labels.order(*k)) == [
        ("ctrl", "a"),
        ("ctrl", "b"),
    ]
