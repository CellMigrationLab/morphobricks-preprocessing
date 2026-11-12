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
    nb = PreprocessingNotebook(str(root), output_folder=str(out))
    nb.welcome()
    nb.steps()
    nb.masks_folder.value = str(masks)
    nb.check_button.click()
    return nb


def test_complete_header_roles_are_offered_with_a_preview_and_one_button(exp):  # noqa: F811
    nb = stepper(*exp)
    assert ui.is_visible(nb.quick_card) and ui.is_visible(nb.axis_box)  # roles and example
    assert nb.axes_viewer.view is not None and nb.axes_viewer.tiles.children
    assert "Nothing is confirmed until you click" in nb.quick_text.value
    assert nb.confirmed_mapping is None and not nb.preview_checked.value  # never automatic
    assert not ui.is_visible(nb.confirm_button)  # one button while the roles are offered
    assert not ui.is_visible(nb.preview_checked.widget)


def test_the_one_click_records_what_the_two_clicks_record(exp):  # noqa: F811
    quick = stepper(*exp)
    quick.quick_confirm.click()
    root, masks, out = exp
    manual = stepper(root, masks, out.parent / "manual")
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
    nb = PreprocessingNotebook(str(root), output_folder=str(tmp_path / "out"))
    nb.steps()
    nb.check_button.click()
    assert not ui.is_visible(nb.quick_card) and ui.is_visible(nb.axis_box)
    assert ui.is_visible(nb.confirm_button) and ui.is_visible(nb.preview_checked.widget)


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
    assert nb._stepper.current == 6  # every answer is saved: the run is next


def test_a_role_edited_after_the_offer_is_not_confirmed_by_the_old_offer(exp):  # noqa: F811
    nb = stepper(*exp)
    offer = nb._quick_offer
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
    nb.role_dropdowns[0].value = "Z"
    assert nb.role_dropdowns[0].value == "Z"  # the user's edit stands
    assert nb._quick_offer is None and not ui.is_visible(nb.quick_card)
    assert nb.confirmed_mapping is None
    nb.role_dropdowns[0].value = "C"  # back to the recorded roles: offered again, not confirmed
    assert nb._quick_offer is not None and nb.confirmed_mapping is None


def test_a_preview_that_cannot_be_read_is_not_confirmed_by_one_click(exp, monkeypatch):  # noqa: F811
    # Regression (batch 34b BLOCKER 3): the headers are readable but the pixels are not; the
    # card still offered the click, and the click set "the preview looks right" on no preview.
    from morphobricks_preprocessing import image_view
    from morphobricks_preprocessing.planes import PlaneReadError

    def broken(*_args, **_kwargs):
        raise PlaneReadError("the pixels cannot be decoded")

    monkeypatch.setattr(image_view, "read_plane", broken)
    nb = stepper(*exp)
    assert nb._quick_offer is not None and ui.is_visible(nb.quick_card)
    assert nb.axes_viewer.view is None and not nb.axes_viewer.tiles.children
    assert (
        "the pixels cannot be decoded" in nb.axes_viewer.caption.value
    )  # the failure stays visible
    assert "No picture of" in nb.axes_viewer.caption.value
    assert "no example of these roles is drawn" in nb.quick_caption.value
    assert nb.quick_confirm.disabled
    assert not nb._confirm_quick_axes()  # even a click that reaches it is refused
    assert nb.confirmed_mapping is None and not nb.preview_checked.value
    assert "the pixels cannot be decoded" in nb.axes_viewer.caption.value


def test_a_preview_that_fails_later_withdraws_the_click(exp, monkeypatch):  # noqa: F811
    from morphobricks_preprocessing import image_view
    from morphobricks_preprocessing.planes import PlaneReadError

    nb = stepper(*exp)
    assert not nb.quick_confirm.disabled  # a preview of these roles is shown
    monkeypatch.setattr(
        image_view,
        "read_plane",
        lambda *_a, **_k: (_ for _ in ()).throw(PlaneReadError("gone")),
    )
    nb._render_preview()  # e.g. another plane chosen: it cannot be read
    assert not nb._confirm_quick_axes()
    assert nb.confirmed_mapping is None and not nb.preview_checked.value


def test_a_header_change_does_not_offer_the_earlier_run_s_roles(exp):  # noqa: F811
    # Regression (batch 34b FILE 3): headers rewritten with other channel names (same files,
    # shape and count) still offered "the roles the earlier run recorded".
    from channel_images import write_channels
    from test_notebook_check_save import KINDS

    first = stepper(*exp)
    first.quick_confirm.click()
    answer_all(first)
    assert first.save_experiment()
    assert first.run().written
    root, _, _ = exp
    write_channels(root / "ctrl/rep1/a.tif", ["Actin", "DAPI", "Ch3"], KINDS, seed=1)
    nb = stepper(*exp)
    assert nb._saved_axes() is None
    assert "the earlier run of this experiment recorded" not in nb.quick_text.value
    assert "image headers changed since the experiment was saved" in nb.quick_text.value
    assert nb.confirmed_mapping is None and not nb.preview_checked.value  # never automatic


def test_an_image_changed_since_the_folder_check_is_not_previewed_or_confirmed(exp):  # noqa: F811
    # Regression (batch 34b, with batch 33's bound pictures): the axis preview read the file as
    # it is now, not as the folder check read its header, and one click vouched for it.
    import os

    from channel_images import write_channels
    from test_notebook_check_save import KINDS

    nb = stepper(*exp)
    rel = nb.axes_viewer.image
    assert nb.preflight.header_fingerprints.get(rel) is not None  # the check bound the header
    assert not nb.quick_confirm.disabled
    root, _, _ = exp
    path = root / rel
    write_channels(path, ["DAPI", "Actin", "Ch3"], KINDS, seed=7)  # same name, header, shape
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10**10))
    nb._refresh_quick_axes()
    assert nb.axes_viewer.view is None and not nb.axes_viewer.tiles.children
    assert "changed since the folder check read it" in nb.axes_viewer.caption.value
    assert nb.quick_confirm.disabled
    assert not nb._confirm_quick_axes()
    assert nb.confirmed_mapping is None and not nb.preview_checked.value


def test_an_image_changed_during_the_preview_read_is_not_shown(exp, monkeypatch):  # noqa: F811
    # Review of #255: the axis preview checks the bound fingerprint after the read too, so a
    # file rewritten while its plane was read is a failed preview, never the image checked.
    import os

    from channel_images import write_channels
    from morphobricks_preprocessing import image_view
    from test_notebook_check_save import KINDS

    nb = stepper(*exp)
    rel = nb.axes_viewer.image
    path = exp[0] / rel
    real = image_view.read_plane

    def read_then_rewrite(*args, **kwargs):
        plane = real(*args, **kwargs)
        write_channels(path, ["DAPI", "Actin", "Ch3"], KINDS, seed=9)
        stat = path.stat()
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10**10))
        return plane

    monkeypatch.setattr(image_view, "read_plane", read_then_rewrite)
    nb._refresh_quick_axes()
    assert nb.axes_viewer.view is None and nb._preview_drawn is None
    assert "changed since the folder check read it" in nb.axes_viewer.caption.value
    assert nb.quick_confirm.disabled and not nb._confirm_quick_axes()
    assert nb.confirmed_mapping is None and not nb.preview_checked.value
