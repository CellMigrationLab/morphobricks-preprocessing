"""The notebook on the owner's extra-stain case (Stage 3 S3.3, owner decision of 11 November 2026).

Three stains in every folder and a fourth only in the ``rep2`` folders: the axes step accepts
the different channel counts (each acquisition is checked inside itself), the Channels step
tags four labels, the run writes ``plane_histograms.csv`` version 5 with the fourth stain blank
elsewhere, and *Compare groups* refuses to pool the fourth stain over a condition whose
acquisitions have it only partly until the user explicitly says yes (shown and logged).
"""

import pytest
from morphobricks_normalization.csv_io import read_plane_histogram_table

from channel_images import write_channels, write_masks
from test_notebook_check_save import FOLDERS, KINDS, answer_all, opened

pytest.importorskip("ipywidgets")

from morphobricks_preprocessing import notebook_style as ui  # noqa: E402
from morphobricks_preprocessing.notebook_results import REFUSE, SUBSET  # noqa: E402

LABELS = ["DNA", "ACTIN", "PHASE", "EXTRA"]


def extra_stain_notebook(tmp_path):
    root, masks, out = tmp_path / "exp", tmp_path / "masks", tmp_path / "out"
    for i, folder in enumerate(FOLDERS):
        extra = folder.endswith("rep2")
        names = ["DAPI", "Actin", "Ch3"] + (["Extra4"] if extra else [])
        write_channels(root / folder / "a.tif", names, KINDS + (["large"] if extra else []), i + 1)
        write_masks(masks, {f"{folder}/a.tif": i + 1}, {"Nuclei": "labels"})
    nb = opened(root, masks, out)
    assert "mb-ok" in nb.axis_status.value  # different counts between folders are accepted
    card = nb.channel_labels
    assert len(card.rows) == 4  # one slot per channel of the widest acquisition
    for row, text in zip(card.rows, LABELS, strict=True):
        row.id_box.value = text
    card.confirm.button.click()
    for (_key, index), cell in list(nb.channel_grid.cells.items()):
        if cell.value is None:
            cell.value = LABELS[index]
    for layout in nb.channel_grid.layouts:
        nb.channel_types.radios[layout.key].value = "channels"
    answer_all(nb)
    assert nb.save_experiment()
    return nb, out


def test_the_extra_stain_case_runs_through_the_notebook(tmp_path):
    nb, out = extra_stain_notebook(tmp_path)
    result = nb.run()
    assert result.written, [(i.code, i.message) for i in result.issues]
    table = read_plane_histogram_table(out / "plane_histograms.csv")
    assert table.channel_ids == ("ACTIN", "DNA", "EXTRA", "PHASE")
    present = {r.source_relative_path: r.channels for r in table.records}
    assert present["ctrl/rep2/a.tif"] == ("DNA", "ACTIN", "PHASE", "EXTRA")
    assert present["ctrl/rep1/a.tif"] == ("DNA", "ACTIN", "PHASE")
    assert {label for label, _ in nb.run_checks.channel.options} == set(LABELS)


def test_compare_groups_asks_before_pooling_a_partly_present_stain(tmp_path):
    nb, _ = extra_stain_notebook(tmp_path)
    assert nb.run().written
    nb.level.value = "condition"
    nb.show_statistics()
    assert "Partial channel presence" in nb.stats_status.value
    assert "A_SCOPE_INCOMPLETE" in nb.stats_status.value
    assert ui.is_visible(nb.partial_box) and nb.partial_choice.value == REFUSE
    assert not ui.is_visible(nb.statistics_card)
    nb.partial_choice.value = SUBSET  # the user's explicit answer: recalculated at once
    assert "calculated" in nb.stats_status.value and "Your choice" in nb.stats_status.value
    assert "EXTRA (only the 1 of 2 planes that have it, as chosen)" in nb.statistics.value
    assert any("planes_with_channel" in line for line in nb.log.lines)
    # By biological repeat every group has the stain in all or none of its planes: no question.
    nb.partial_choice.value = REFUSE
    nb.level.value = "biological_repeat"
    nb.show_statistics()
    assert "calculated" in nb.stats_status.value and "Your choice" not in nb.stats_status.value


def test_choosing_another_table_resets_the_answer_without_calculating(tmp_path):
    # Review of PR 288: resetting the answer while the question was shown recalculated the
    # statistics of the newly chosen table unasked (the observer saw the box still visible).
    nb, out = extra_stain_notebook(tmp_path)
    assert nb.run().written
    nb.level.value = "condition"
    nb.show_statistics()
    nb.partial_choice.value = SUBSET
    assert "calculated" in nb.stats_status.value
    nb.csv_file.value = str(out / "other.csv")
    assert nb.partial_choice.value == REFUSE
    assert not ui.is_visible(nb.partial_box)
    assert nb.stats_status.value == ""
    assert not ui.is_visible(nb.statistics_card)
