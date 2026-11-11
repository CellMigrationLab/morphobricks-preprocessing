"""Issue #252: small notebook cleanups from the 17c and #249 reviews.

- A preview's read failure names the image relative to the experiment folder, never by its
  absolute path (item 3).
- The non-finite note of a panel says "1 non-finite pixel" (the plural helper; item 2: the note
  is no longer repeated since batch 34b).
- *Check and save* closes an Enlarge view once its replicate boxes show other folders or
  pictures (item 4).
- The strict reader's warnings of the description (valid, not errors; experiment-v1 4.3) are
  listed on *Check and save* (item 5).
"""

from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from channel_images import write_rgb
from morphobricks_preprocessing import header_preview as hp
from morphobricks_preprocessing.description import Channel
from morphobricks_preprocessing.preview_render import display_panel
from test_notebook_check_save import answer_all, exp  # noqa: F401
from test_notebook_thumbnails import _bound, _meta

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")
import ipywidgets as widgets  # noqa: E402

from morphobricks_preprocessing.description_candidate import check_candidate  # noqa: E402
from morphobricks_preprocessing.notebook_check_view import checks_html  # noqa: E402
from test_notebook_axes_quick import stepper  # noqa: E402


def test_a_read_failure_names_the_image_relative_to_the_experiment_folder(tmp_path):
    write_rgb(tmp_path / "ctrl" / "rgb.tif")
    meta = _meta(tmp_path, "ctrl/rgb.tif")
    path = tmp_path / "ctrl" / "rgb.tif"
    path.write_bytes(path.read_bytes()[:400])  # truncated
    broken = hp.header_preview(tmp_path, meta, bound=_bound(tmp_path, "ctrl/rgb.tif"))
    assert broken.problem.startswith("cannot be read for a preview: ")
    assert str(tmp_path) not in broken.problem
    assert "ctrl" in broken.problem and "rgb.tif" in broken.problem


def test_read_failure_keeps_the_message_and_strips_only_the_folder(tmp_path):
    exc = OSError(f"[Errno 5] cannot read: '{tmp_path / 'a' / 'b.tif'}'")
    text = hp.read_failure(exc, tmp_path)
    assert text == f"cannot be read for a preview: [Errno 5] cannot read: '{Path('a', 'b.tif')}'"


def test_one_non_finite_pixel_is_said_in_the_singular_and_once():
    panel = display_panel(np.array([[1.0, np.nan], [2.0, 3.0]]))
    assert panel.note == "1 non-finite pixel shown in magenta"
    flat = display_panel(np.array([[7.0, np.nan], [7.0, 7.0]]))
    assert flat.note == "flat: every finite value is 7; 1 non-finite pixel shown in magenta"


def _button(widget, text):
    """Return the first button under ``widget`` whose description holds ``text``."""
    if isinstance(widget, widgets.Button) and text in widget.description:
        return widget
    for child in getattr(widget, "children", ()):
        found = _button(child, text)
        if found is not None:
            return found
    return None


def test_check_and_save_has_one_viewer_and_no_picture_per_folder(exp):  # noqa: F811
    nb = stepper(*exp)
    nb.quick_confirm.click()
    answer_all(nb)
    nb._refresh_check_save()
    assert not [w for w in _all(nb.replicates_box) if isinstance(w, widgets.Image)]
    assert not [w for w in _all(nb.replicates_box) if getattr(w, "description", "") == " Enlarge"]
    viewer = nb.check_viewer
    assert viewer.view is not None and len(viewer.entries) == len(nb.preflight.discovery.images)
    assert "read with the confirmed axis roles" in viewer.caption.value
    nb.check_button.click()  # a new folder check: no picture of unconfirmed roles
    assert viewer.entries == () and viewer.view is None


def _all(widget):
    yield widget
    for child in getattr(widget, "children", ()):
        yield from _all(child)


def test_check_and_save_lists_the_reader_s_warnings(exp):  # noqa: F811
    nb = stepper(*exp)
    nb.quick_confirm.click()
    answer_all(nb)
    nb._refresh_check_save()
    candidate = nb._check.description
    assert nb._check.warnings == ()
    used = {
        **candidate.used_ids,
        "channels": tuple(sorted({*candidate.used_ids["channels"], "TUBULIN"})),
    }
    channels = {**candidate.channels, "TUBULIN": Channel()}
    unused = replace(candidate, channels=channels, used_ids=used)
    check = check_candidate(unused, nb.preflight, nb._review().file_mappings)
    assert check.can_save and [w.code for w in check.warnings] == ["W_UNUSED_CHANNEL"]
    shown = checks_html(check)
    assert "Valid, with warnings:" in shown
    assert "The label TUBULIN is declared, but no folder" in shown and "W_UNUSED_CHANNEL" in shown
