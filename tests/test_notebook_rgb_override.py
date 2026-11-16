"""The RGB header override on the notebook's Image type card (Spec RH1, roadmap H1).

Scanner files store colour as three interleaved samples per pixel under a header that does not
say colour (TIFF ``minisblack``, ``SamplesPerPixel`` 3, contiguous). Declared colour (RGB), the
run refuses them (``R_IMAGE_TYPE_CONTRADICTS_HEADER``) unless the unit declares
``rgb_header_override = true`` (experiment-v1 4.4b). The Channels step offers that key:

- only when a folder's headers have three interleaved samples but do not say colour;
- as one checkbox, never pre-ticked, recorded only by the user's click; disabled, with its
  reason shown, while no such folder is answered colour (RGB);
- recorded exactly as RH1 says: the published description, for the same answers plus the tick,
  is the canonical form of a hand-written file with the key, and the run records
  ``rgb_header_override_images`` as for that file.
"""

import json
import re

import numpy as np
import pytest
import tifffile

from morphobricks_preprocessing.axes import prefill_mapping
from morphobricks_preprocessing.channel_answers import with_override, with_type
from morphobricks_preprocessing.channel_labels import UnitLayout
from morphobricks_preprocessing.channel_units import three_samples
from morphobricks_preprocessing.description import description_from_bytes
from morphobricks_preprocessing.description_file import DESCRIPTION_FILENAME
from morphobricks_preprocessing.description_write import canonical_bytes
from morphobricks_preprocessing.draft_answers import DraftAnswers
from morphobricks_preprocessing.metadata import inspect_tiff
from morphobricks_preprocessing.run import run_experiment

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")

from morphobricks_preprocessing import notebook_style as ui  # noqa: E402
from morphobricks_preprocessing.notebook_channels_type import (  # noqa: E402
    ImageTypeColumn,
    ImageTypeRows,
)
from morphobricks_preprocessing.notebook_image_type_line import ImageTypeLine  # noqa: E402
from morphobricks_preprocessing.notebook_rgb_override import CHOOSE_COLOUR, LABEL  # noqa: E402
from morphobricks_preprocessing.notebook_ui import PreprocessingNotebook  # noqa: E402

FOLDERS = ["ctrl/rep1", "ctrl/rep2", "drug/rep1", "drug/rep2"]
OVERRIDE = b'image_type = "rgb"\nrgb_header_override = true\n'
CONTRADICTS = "R_IMAGE_TYPE_CONTRADICTS_HEADER"


def _text(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def scanner(path, seed):
    """Write interleaved colour under a MINISBLACK header, as RH1's scanner files are."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.random.default_rng(seed).integers(0, 255, (16, 16, 3), dtype=np.uint8)
    tifffile.imwrite(path, data, photometric="minisblack", planarconfig="contig")
    return path


def rgb(path, seed):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.random.default_rng(seed).integers(0, 255, (16, 16, 3), dtype=np.uint8)
    tifffile.imwrite(path, data, photometric="rgb")
    return path


def _notebook(tmp_path, out="out", write=scanner):
    root = tmp_path / "exp"
    if not root.exists():
        for i, folder in enumerate(FOLDERS):
            write(root / folder / "a.tif", i + 1)
    nb = PreprocessingNotebook(str(root), output_folder=str(tmp_path / out))
    nb.steps()
    nb.check_button.click()
    nb.quick_confirm.click()
    return nb


def _colour_for_all(nb):
    nb.type_line.choice.value = "rgb"
    nb.type_line.apply.click()


def _finish(nb):
    """Answer the design and the replicates as shown (no label: every folder is colour)."""
    nb.reference_boxes["drug"].value = "ctrl"
    nb.design_confirm.click()
    for toggle in list(nb.same_way.values()):
        if not toggle.value:
            toggle.button.click()


def _run(tmp_path, out):
    """Run the published description of ``out`` on the scanner folders (axes ``YXS``)."""
    return run_experiment(tmp_path / "exp", prefill_mapping("YXS"), output_folder=tmp_path / out)


def _overrides(nb):
    return {key: unit.rgb_header_override for key, unit in nb.draft_answers.units.items()}


# --- the headers ----------------------------------------------------------------------------


def test_three_samples_is_header_evidence_of_the_override_s_condition(tmp_path):
    lifted = inspect_tiff(scanner(tmp_path / "a.tif", 1))
    said = inspect_tiff(rgb(tmp_path / "b.tif", 1))
    path = tmp_path / "c.tif"
    tifffile.imwrite(path, np.zeros((16, 16), np.uint8), photometric="minisblack")
    one = inspect_tiff(path)
    assert three_samples([lifted]) and three_samples([lifted, said])
    assert not three_samples([said])  # the header says colour: nothing to override
    assert not three_samples([lifted, one]) and not three_samples([one]) and not three_samples([])


def test_the_override_is_recorded_only_on_a_colour_unit_whose_headers_allow_it():
    key = ("ctrl", "rep1", "single")
    allowed = UnitLayout(key, "a.tif", 3, (None,) * 3, three_samples=True)
    answers = DraftAnswers("0" * 64, "spec")
    with pytest.raises(ValueError, match="choose colour first"):
        with_override(answers, allowed, True)
    colour = with_type(answers, allowed, "rgb")
    ticked = with_override(colour, allowed, True)
    assert ticked.units[key].rgb_header_override is True
    assert with_override(ticked, allowed, False) == colour  # unticked: the key is gone
    with pytest.raises(ValueError, match="three interleaved samples"):
        with_override(colour, UnitLayout(key, "a.tif", 3, (None,) * 3), True)


# --- the card ---------------------------------------------------------------------------------


def test_the_offer_is_not_shown_for_headers_that_say_colour_or_ordinary_channels(tmp_path):
    nb = _notebook(tmp_path, write=rgb)
    assert not ui.is_visible(nb.type_line.override.widget)
    _colour_for_all(nb)
    assert not ui.is_visible(nb.type_line.override.widget)
    assert set(_overrides(nb).values()) == {None}


def test_the_offer_waits_for_colour_with_its_reason_and_is_never_pre_ticked(tmp_path):
    nb = _notebook(tmp_path)
    offer = nb.type_line.override
    assert ui.is_visible(offer.widget) and offer.box.description == LABEL
    assert offer.box.value is False and offer.box.disabled
    assert CHOOSE_COLOUR in _text(offer.blocker.value)
    said = _text(offer.text.value)
    assert "4 folders: three values per pixel, header not colour" in said
    assert "rgb_header_override = true" in said and "Leave it unticked" in said
    _colour_for_all(nb)
    assert offer.box.value is False and not offer.box.disabled and offer.blocker.value == ""
    assert set(_overrides(nb).values()) == {None}  # choosing colour records no override
    # Without the tick each folder says the run refuses it, and where the fix is.
    nb.type_line.per_folder.selected_index = 0
    assert "unless you tick" in repr(nb.type_rows.rows.children)


def test_the_click_records_the_override_and_unticking_takes_it_back(tmp_path):
    nb = _notebook(tmp_path)
    _colour_for_all(nb)
    offer = nb.type_line.override
    offer.box.value = True
    assert set(_overrides(nb).values()) == {True} and len(_overrides(nb)) == 4
    assert offer.box.value is True
    nb.type_line.per_folder.selected_index = 0
    assert "read as colour because you ticked it" in repr(nb.type_rows.rows.children)
    offer.box.value = False
    assert set(_overrides(nb).values()) == {None}
    offer.box.value = True
    nb.type_line.choice.value = "channels"  # back to separate channels drops the key
    nb.type_line.apply.click()
    nb.type_line.replace_yes.click()
    assert set(_overrides(nb).values()) == {None}
    assert offer.box.disabled and offer.box.value is False


def test_one_column_on_the_image_type_card():
    layouts = tuple(
        UnitLayout((c, r, "single"), "a.tif", 3, (None,) * 3, three_samples=True)
        for c in ("ctrl", "drug")
        for r in ("rep1", "rep2")
    )

    class Store:
        saved = DraftAnswers("0" * 64, "spec")

        def answers(self):
            return self.saved

        def update(self, new):
            self.saved = new
            return True

        def refusal(self):
            return ""

    types = ImageTypeColumn(Store(), lambda: line.refresh())
    line = ImageTypeLine(types, ImageTypeRows(types))
    line.rows.open(layouts)
    line.refresh()
    assert any(child is line.override.widget for child in _descendants(line.widget))
    assert ui.is_visible(line.override.widget)
    assert type(line.override.widget).__name__ == "VBox"  # one column: text, box, reason


def _descendants(widget):
    for child in getattr(widget, "children", ()):
        yield child
        yield from _descendants(child)


# --- what is published and run ----------------------------------------------------------------


def test_the_published_file_is_a_hand_written_one_with_the_key_and_the_run_records_it(tmp_path):
    plain = _notebook(tmp_path, out="plain")
    _colour_for_all(plain)
    _finish(plain)
    assert plain.save_experiment()
    without = (tmp_path / "plain" / DESCRIPTION_FILENAME).read_bytes()
    refused = _run(tmp_path, "plain")
    assert not refused.written
    assert CONTRADICTS in {issue.code for issue in refused.issues}

    nb = _notebook(tmp_path, out="out")
    _colour_for_all(nb)
    nb.type_line.override.box.value = True
    _finish(nb)
    assert nb.save_experiment()
    published = (tmp_path / "out" / DESCRIPTION_FILENAME).read_bytes()
    # The same answers by hand: the key after each unit's image type, as RH1 writes it.
    hand = without.replace(b'image_type = "rgb"\n', OVERRIDE)
    assert hand.count(OVERRIDE) == 4
    assert published == hand == canonical_bytes(description_from_bytes(hand))
    result = _run(tmp_path, "out")
    assert result.written, [(i.code, i.message) for i in result.issues]
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    units = manifest["experiment_description"]["units"]
    assert len(units) == 4
    for unit in units:
        assert unit["rgb_header_override"] is True
        assert unit["image_type_header_overridden"] is True
        assert [u["override"] for u in unit["rgb_header_override_images"]] == ["used"]
        assert unit["rgb_header_override_images"][0]["header_photometric"] == "minisblack"
