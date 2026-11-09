"""*Confirm these axes*: the axes step in one click (owner decision Q3, PR 17b).

The roles are offered only when nothing about them is open; nothing is confirmed without the
click; the click records what the two clicks of the role boxes record. A reopened experiment
whose folders did not change offers the roles of the earlier run and, once confirmed, opens its
next open step instead of stopping on the axes.
"""

import numpy as np
import pytest
import tifffile

from test_notebook_check_save import answer_all, exp  # noqa: F401

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")

from morphobricks_preprocessing import notebook_style as ui  # noqa: E402
from morphobricks_preprocessing.notebook_ui import PreprocessingNotebook  # noqa: E402


def stepper(root, masks, out):
    nb = PreprocessingNotebook(str(root), output_folder=str(out), describe=True)
    nb.welcome()
    nb.steps()
    nb.masks_folder.value = str(masks)
    nb.check_button.click()
    return nb


def test_complete_header_roles_are_offered_with_a_preview_and_one_button(exp):  # noqa: F811
    nb = stepper(*exp)
    assert ui.is_visible(nb.quick_card) and not ui.is_visible(nb.axis_box)
    assert ui.is_visible(nb.quick_image) and nb.quick_image.value[:4] == b"\x89PNG"
    assert "Nothing is confirmed until you click" in nb.quick_text.value
    assert nb.confirmed_mapping is None and not nb.preview_checked.value  # never automatic
    nb.axes_details.click()  # the role boxes stay one click away
    assert ui.is_visible(nb.axis_box)


def test_the_one_click_records_what_the_two_clicks_record(exp):  # noqa: F811
    quick = stepper(*exp)
    quick.quick_confirm.click()
    root, masks, out = exp
    manual = stepper(root, masks, out.parent / "manual")
    manual.axes_details.click()
    manual.preview_checked.set(True)
    manual.confirm_button.click()
    assert quick.confirmed_mapping == manual.confirmed_mapping is not None
    assert quick.preview_checked.value and manual.preview_checked.value
    assert quick.confirmed_status.value == manual.confirmed_status.value
    assert not ui.is_visible(quick.quick_card) and ui.is_visible(quick.axis_box)
    assert quick.confirm_button.description == " Change axis roles"


def test_roles_the_header_does_not_name_are_not_offered(tmp_path):
    root = tmp_path / "exp"
    for folder in ("ctrl/rep1", "drug/rep1"):
        path = root / folder / "a.tif"
        path.parent.mkdir(parents=True)
        data = np.arange(3 * 16 * 16, dtype=np.uint16).reshape(3, 16, 16)
        tifffile.imwrite(path, data, photometric="minisblack", metadata=None)  # a plain stack
    nb = PreprocessingNotebook(str(root), output_folder=str(tmp_path / "out"), describe=True)
    nb.steps()
    nb.check_button.click()
    assert not ui.is_visible(nb.quick_card) and ui.is_visible(nb.axis_box)
    assert not ui.is_visible(nb.axes_details)


def test_an_unchanged_reopen_offers_the_run_s_roles_and_moves_on(exp):  # noqa: F811
    first = stepper(*exp)
    first.quick_confirm.click()
    answer_all(first)
    assert first.save_experiment()
    assert first.run().written
    nb = stepper(*exp)
    assert nb._stepper.current == 1 and ui.is_visible(nb.quick_card)
    assert "the earlier run of this experiment recorded" in nb.quick_text.value
    nb.quick_confirm.click()
    assert nb.confirmed_mapping == first.confirmed_mapping
    assert nb._stepper.current == 11  # every answer is saved: the run is next


def test_a_role_edited_after_the_offer_is_not_confirmed_by_the_old_offer(exp):  # noqa: F811
    nb = stepper(*exp)
    offer = nb._quick_offer
    nb.axes_details.click()
    nb.role_dropdowns[0].value = "Z"  # C becomes Z: the axis check now has something to say
    assert offer is not None and nb._quick_offer is None  # the header names C: no offer now
    assert not ui.is_visible(nb.quick_card) and ui.is_visible(nb.axis_box)
    assert not nb._confirm_quick_axes() and nb.confirmed_mapping is None


def test_a_role_edited_on_a_reopen_is_kept_not_put_back_to_the_run_s_roles(exp):  # noqa: F811
    # Regression (17b review): every role change refilled the boxes with the recorded roles.
    first = stepper(*exp)
    first.quick_confirm.click()
    answer_all(first)
    assert first.save_experiment()
    assert first.run().written
    nb = stepper(*exp)
    assert nb._quick_offer is not None
    nb.axes_details.click()
    nb.role_dropdowns[0].value = "Z"
    assert nb.role_dropdowns[0].value == "Z"  # the user's edit stands
    assert nb._quick_offer is None and not ui.is_visible(nb.quick_card)
    assert nb.confirmed_mapping is None
    nb.role_dropdowns[0].value = "C"  # back to the recorded roles: offered again, not confirmed
    assert nb._quick_offer is not None and nb.confirmed_mapping is None
