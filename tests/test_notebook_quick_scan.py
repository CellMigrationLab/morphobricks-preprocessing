"""The quick scan of the first step: look at the folders and one image, then read the headers.

Owner decision (10 November 2026, item (d)): choosing the folders, a quick scan (the folder
tree with labels and counts, no header read, and one randomly chosen image shown for looking
only), then the explicit click that reads every header. Changing a folder redoes the scan.
"""

import re

import pytest

from morphobricks_preprocessing.discovery import discover_experiment
from test_notebook_check_save import add_folder, exp  # noqa: F401

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")

from morphobricks_preprocessing import metadata, notebook_folder, notebook_quick_scan  # noqa: E402
from morphobricks_preprocessing import notebook_style as ui  # noqa: E402
from morphobricks_preprocessing.notebook_quick_scan import (  # noqa: E402
    DEEP_LABEL,
    random_image,
    scan_summary,
    tree_html,
)
from morphobricks_preprocessing.notebook_ui import PreprocessingNotebook  # noqa: E402


def _text(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def fresh(root, masks, out):
    nb = PreprocessingNotebook(str(root), output_folder=str(out))
    nb.steps()
    nb.masks_folder.value = str(masks)
    return nb


def test_the_check_waits_for_a_scan_and_says_so(exp):  # noqa: F811
    nb = fresh(*exp)
    assert nb.check_button.description == DEEP_LABEL and nb.check_button.disabled
    assert "click Scan the folders" in _text(nb.check_blocker.value)
    assert not ui.is_visible(nb.scan_box) and nb.preflight is None
    nb.folder.value = ""
    assert "no experiment folder is given" in _text(nb.check_blocker.value)
    nb.scan_button.click()
    assert "Enter the experiment folder first" in nb.scan_status.value and nb.check_button.disabled


def test_the_scan_reads_no_header_but_the_one_image_it_shows(exp, monkeypatch):  # noqa: F811
    nb = fresh(*exp)
    opened = []
    real = metadata.inspect_image

    def one_header(path, relative_path=None):
        opened.append(relative_path)
        return real(path, relative_path)

    def no_check(*args, **kwargs):
        raise AssertionError("the folder check ran")

    monkeypatch.setattr(notebook_quick_scan, "inspect_image", one_header)
    monkeypatch.setattr(metadata, "inspect_image", no_check)
    monkeypatch.setattr(notebook_folder, "preflight", no_check)
    nb.scan_button.click()
    found = discover_experiment(exp[0], exp[2])
    assert opened == [nb.scan_image] and nb.scan_image in found.images
    assert nb.preflight is None and nb.mapping is None and nb.draft is None  # nothing is kept
    assert not list(exp[2].glob("*")) if exp[2].exists() else True  # and nothing written
    assert ui.is_visible(nb.scan_box) and not nb.check_button.disabled
    assert nb.check_blocker.value == ""
    said = _text(nb.scan_status.value)
    assert (
        f"Found {scan_summary(found, frozenset())}" in said and "No image header was read" in said
    )
    assert nb.scan_tree.value == tree_html(found, frozenset())


def test_the_tree_lists_every_folder_with_its_labels_and_image_count(exp):  # noqa: F811
    found = discover_experiment(exp[0], exp[2])
    html = tree_html(found, frozenset())
    for leaf in found.leaves:
        for label in leaf.labels:
            assert f"<td>{label}</td>" in html
    assert html.count("<tr>") == len(found.leaves) + 1
    first = found.leaves[0]
    without = tree_html(found, frozenset(first.images))
    assert "<td class='mb-num'>0</td>" in without  # excluded images are not counted
    summary = scan_summary(found, frozenset(first.images[:1]))
    assert summary.endswith("1 image excluded") and f"{len(found.images) - 1} images" in summary


def test_the_random_image_is_named_with_its_seed_and_is_reproducible(exp):  # noqa: F811
    nb = fresh(*exp)
    nb.scan_button.click()
    images = list(discover_experiment(exp[0], exp[2]).images)
    assert 0 <= nb.scan_seed < notebook_quick_scan.SEED_RANGE
    assert nb.scan_image == random_image(images, nb.scan_seed)
    shown = _text(nb.scan_example.children[0].value)
    assert (
        f"seed {nb.scan_seed}" in shown and nb.scan_image in shown and "for looking only" in shown
    )
    assert "from the file header" in _text(nb.scan_example.children[1].children[1].value)
    nb._show_scan_example(7)
    assert nb.scan_seed == 7 and nb.scan_image == random_image(images, 7)
    assert random_image(images, 7) == random_image(list(reversed(images)), 7)  # order-free
    assert random_image([], 7) is None
    seeds = set()
    for _ in range(4):
        nb.scan_another.click()
        seeds.add(nb.scan_seed)
    assert len(seeds) > 1  # another image is another draw


def test_an_excluded_image_is_never_the_one_shown(exp):  # noqa: F811
    nb = fresh(*exp)
    images = sorted(discover_experiment(exp[0], exp[2]).images)
    nb.exclude.value = "\n".join(images[1:])
    nb.scan_button.click()
    assert nb.scan_image == images[0]
    assert not ui.is_visible(nb.scan_another)  # one image left: nothing else to draw


def test_changing_a_folder_makes_the_scan_stale_and_blocks_the_check(exp, tmp_path):  # noqa: F811
    nb = fresh(*exp)
    nb.scan_button.click()
    assert not nb.check_button.disabled
    for box, value in (
        (nb.output, str(tmp_path / "elsewhere")),
        (nb.masks_folder, ""),
        (nb.exclude, "ctrl/rep1/a.tif"),
        (nb.folder, str(tmp_path / "exp") + "/"),
    ):
        nb.scan_button.click()
        assert not nb.check_button.disabled
        box.value = value
        assert nb.check_button.disabled, value
        assert "click Scan the folders" in _text(nb.check_blocker.value)
        assert "The folders changed" in nb.scan_status.value and not ui.is_visible(nb.scan_box)
    nb.scan_button.click()
    assert not nb.check_button.disabled and ui.is_visible(nb.scan_box)


def test_after_the_scan_the_explicit_click_reads_every_header_as_before(exp):  # noqa: F811
    nb = fresh(*exp)
    nb.scan_button.click()
    nb.check_button.click()
    plain = PreprocessingNotebook(str(exp[0]), output_folder=str(exp[2].parent / "plain"))
    plain.masks_folder.value = str(exp[1])
    plain.check_button.click()  # a notebook without the stepper: one click, no scan
    assert nb.preflight is not None and nb.preflight.ok
    assert nb.preflight.metadata == plain.preflight.metadata
    assert nb.preflight.discovery.leaves == plain.preflight.discovery.leaves
    assert nb.mapping == plain.mapping and nb.confirmed_mapping is None
    assert plain.check_button.description == " Check folder" and not plain.check_button.disabled
    assert nb.check_button.description == DEEP_LABEL and not nb.check_button.disabled


def test_a_folder_without_images_or_a_missing_folder_is_said_and_not_checked(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    nb = PreprocessingNotebook(str(empty), output_folder=str(tmp_path / "out"))
    nb.steps()
    nb.scan_button.click()
    assert nb.check_button.disabled
    assert "the scan found no image to read" in _text(nb.check_blocker.value)
    nb.folder.value = str(tmp_path / "missing")
    nb.scan_button.click()
    assert "experiment not found" in _text(nb.scan_status.value)
    assert nb.check_button.disabled


def test_the_scan_shows_its_progress_folder_by_folder(exp):  # noqa: F811
    nb = fresh(*exp)
    seen = []
    nb.scan_status.observe(lambda change: seen.append(_text(change["new"])), names="value")
    nb.scan_button.click()
    assert seen[0].startswith("Listing the folders") and "elapsed" in seen[0]
    assert any(line.startswith("Opening one image") for line in seen)
    assert seen[-1].startswith("Found ")


def test_a_new_folder_appears_only_after_scanning_again(exp):  # noqa: F811
    root, masks, _ = exp
    nb = fresh(*exp)
    nb.scan_button.click()
    before = nb.scan_tree.value
    add_folder(root, masks, "ctrl/rep9", 9)
    assert nb.scan_tree.value == before
    nb.scan_button.click()
    assert "rep9" in nb.scan_tree.value and "rep9" not in before
