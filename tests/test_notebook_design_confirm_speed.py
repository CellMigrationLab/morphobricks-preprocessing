"""*Looks right: confirm the design* on a sized experiment (owner bug, 12 November 2026).

On the v0.5.2 notebook the click seemed to do nothing, or was very slow. It saved the answers
twice and refreshed every page three times, each time rebuilding one image-type row per folder
and every replicate box of *Check and save*; the time grew with the number of folders. Now the
click saves one answer, refreshes once, rebuilds only rows whose content changed, shows a busy
button and a working line before any work, and ends with a result line.
"""

import shutil
import time

import pytest

from channel_images import write_channels

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")

from morphobricks_preprocessing import notebook_style as ui  # noqa: E402
from morphobricks_preprocessing.notebook_channels_type import ImageTypeRows  # noqa: E402
from morphobricks_preprocessing.notebook_design_step import (  # noqa: E402
    CONFIRM_LABEL,
    CONFIRMING,
)
from morphobricks_preprocessing.notebook_ui import PreprocessingNotebook  # noqa: E402

CONDITIONS, REPEATS, TECHNICAL, FOVS = 4, 4, 2, 2
BUDGET = 2.0
"""Seconds for the click on 64 images; it took about 1.5 s here before the fix, 0.15 s after.

Generous for slow CI runners: the call counts below are the exact check.
"""


@pytest.fixture
def sized(tmp_path):
    """Return a stepper with its axes confirmed on 4 conditions x 4 repeats x 2 x 2 images."""
    root = tmp_path / "exp"
    one = write_channels(tmp_path / "one.tif", ["DAPI", "Actin", "Ch3"], ["blobs"] * 3, size=32)
    for c in range(CONDITIONS):
        for r in range(REPEATS):
            for t in range(TECHNICAL):
                for f in range(FOVS):
                    path = root / f"cond{c}" / f"rep{r + 1}" / f"tech{t + 1}" / f"fov{f + 1}.tif"
                    path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy(one, path)
    nb = PreprocessingNotebook(str(root), output_folder=str(tmp_path / "out"))
    nb.welcome()
    nb.steps()
    nb.check_button.click()
    nb.quick_confirm.click()
    assert ui.is_visible(nb.design_confirm) and not nb.design_confirm.disabled
    return nb


def _count(monkeypatch, owner, name):
    calls = []
    original = getattr(owner, name)

    def spy(self, *args, **kwargs):
        calls.append(name)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(owner, name, spy)
    return calls


def test_the_click_saves_once_refreshes_once_and_keeps_the_unchanged_rows(sized, monkeypatch):
    nb = sized
    cls = type(nb)
    answers = _count(monkeypatch, cls, "_answer")
    refreshes = _count(monkeypatch, cls, "_refresh_description")
    type_rows = _count(monkeypatch, ImageTypeRows, "_build_rows")
    started = time.perf_counter()
    nb.design_confirm.click()
    took = time.perf_counter() - started
    assert nb._design_confirmed()
    assert len(answers) == 1 and len(refreshes) == 1
    assert type_rows == []  # the image types did not change: their rows are kept
    assert took < BUDGET, f"the click took {took:.2f} s on 64 images"
    assert "Design confirmed: 4 conditions × 4 biological repeats" in nb.design_result.value


def test_the_click_shows_a_busy_button_and_a_working_line_before_any_work(sized, monkeypatch):
    nb = sized
    seen = {}
    original = type(nb)._confirm_design

    def watched(self):
        button = self.design_confirm
        seen.update(
            disabled=button.disabled,
            label=button.description,
            icon=button.icon,
            line=self.design_result.value,
        )
        return original(self)

    monkeypatch.setattr(type(nb), "_confirm_design", watched)
    nb.design_confirm.click()
    assert seen["disabled"] and seen["icon"] == "spinner"
    assert seen["label"].strip() == CONFIRMING and "Confirming the design" in seen["line"]
    # Afterwards: the result, and the button given back (hidden once confirmed).
    assert nb.design_confirm.description == CONFIRM_LABEL and nb.design_confirm.icon == "check"
    assert "Design confirmed" in nb.design_result.value
    assert not ui.is_visible(nb.design_confirm) and ui.is_visible(nb.design_change)


def test_an_unexpected_failure_is_said_and_the_button_is_given_back(sized, monkeypatch):
    nb = sized

    def broken(self):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(type(nb), "_confirm_design", broken)
    nb.design_confirm.click()
    said = nb.design_result.value
    assert "The design was not confirmed" in said and "RuntimeError: disk on fire" in said
    assert "Technical details" in said
    assert nb.design_confirm.description == CONFIRM_LABEL and not nb.design_confirm.disabled
    assert ui.is_visible(nb.design_confirm) and not nb._design_confirmed()
    assert any("disk on fire" in line for line in nb.log.lines)


def test_a_disabled_button_does_nothing(sized, monkeypatch):
    nb = sized
    calls = _count(monkeypatch, type(nb), "_confirm_design")
    nb.design_confirm.disabled = True
    nb._design_clicked()
    assert calls == [] and not nb._design_confirmed()


def test_a_changed_image_type_still_rebuilds_its_rows(sized):
    nb = sized
    rows = nb.type_rows
    before = rows.rows.children
    nb.type_line.apply.click()  # separate channels for all folders: what the rows show changed
    assert rows.rows.children is not before
    assert all(radio.value == "channels" for radio in nb.channel_types.radios.values())
    kept = rows.rows.children
    nb.design_confirm.click()
    assert rows.rows.children is kept  # the design does not change them
