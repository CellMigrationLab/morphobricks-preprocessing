"""Progress for every wait of the notebook, and the readers' progress callbacks (owner, (c)).

A wait shows what is done of the total, the item being read, the elapsed time and, once it
can be measured, the time left. The folder walk and the mask headers report their progress.
"""

import re

import pytest

from morphobricks_preprocessing.discovery import discover_experiment
from morphobricks_preprocessing.run import preflight
from morphobricks_preprocessing.run.preflight import remasked
from test_notebook_check_save import answer_all, exp  # noqa: F401

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")

import ipywidgets as widgets  # noqa: E402

from morphobricks_preprocessing.notebook_folder import CHECK_PHASES, check_progress  # noqa: E402
from morphobricks_preprocessing.notebook_progress import (  # noqa: E402
    MIN_INTERVAL,
    Wait,
    clock_text,
    progress_html,
    time_left,
)
from morphobricks_preprocessing.run.phases import PHASES  # noqa: E402
from test_notebook_axes_quick import stepper  # noqa: E402


def _text(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def _history(widget):
    seen = []
    widget.observe(lambda change: seen.append(_text(change["new"])), names="value")
    return seen


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


# --- the pure parts ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("seconds", "text"), [(0, "0:00"), (9.4, "0:09"), (75, "1:15"), (3725, "1:02:05"), (-3, "0:00")]
)
def test_clock_text(seconds, text):
    assert clock_text(seconds) == text


def test_time_left_only_once_it_can_be_measured():
    assert time_left(0, 10, 5.0) is None  # nothing done yet
    assert time_left(3, 10, 0.5) is None  # too early to measure
    assert time_left(3, 0, 5.0) is None  # the total is not known
    assert time_left(11, 10, 5.0) is None
    assert time_left(2, 10, 4.0) == pytest.approx(16.0)  # 2 s per item, 8 left
    assert time_left(10, 10, 4.0) == 0


def test_progress_html_says_done_of_total_item_elapsed_and_time_left():
    said = _text(progress_html("Reading image headers", 34, 120, "CTRL/rep1/<a>.tif", 17.0))
    assert said == (
        "Reading image headers · 34 of 120 · CTRL/rep1/&lt;a&gt;.tif 0:17 elapsed · about 0:43 left"
    )
    html = progress_html("Reading image headers", 30, 120, "", 2.0)
    assert "width:25.0%" in html and "mb-bar-unknown" not in html
    early = _text(progress_html("Reading image headers", 0, 120, "a.tif", 0.2))
    assert "0 of 120" in early and "elapsed" in early and "left" not in early


def test_progress_html_of_uncounted_work_has_no_total_and_no_estimate():
    html = progress_html("Listing the folders", 7, 0, "CTRL/rep1", 3.0)
    assert "mb-bar-unknown" in html
    assert _text(html) == "Listing the folders · 7 so far · CTRL/rep1 0:03 elapsed"
    assert "mb-bar" not in progress_html("Reading images", 1, 3, "a.tif", 3.0, bar=False)


def test_a_wait_is_redrawn_at_most_every_interval_and_at_the_end():
    clock, target = Clock(), widgets.HTML()
    wait = Wait(target, clock=clock)
    wait.start("Reading image headers", 1000)
    assert wait.drawn == 1 and "0 of 1,000" in _text(target.value)
    for done in range(1, 500):
        wait.step(done, 1000, f"{done}.tif")  # no time passes: nothing is redrawn
    assert wait.drawn == 1
    clock.now += 2 * MIN_INTERVAL
    wait.step(500, 1000, "500.tif")
    assert wait.drawn == 2 and "500 of 1,000 · 500.tif" in _text(target.value)
    wait.step(1000, 1000, "")  # the end is always drawn
    assert wait.drawn == 3 and "1,000 of 1,000" in _text(target.value)


def test_a_wait_estimates_from_its_current_part_and_keeps_the_whole_elapsed_time():
    clock, target = Clock(), widgets.HTML()
    wait = Wait(target, clock=clock)
    wait.start("Listing the folders")
    clock.now += 10
    wait.phase("Reading image headers", 100)
    clock.now += 5
    wait.step(10, 100, "a.tif")
    said = _text(target.value)
    assert "0:15 elapsed" in said  # the whole wait
    assert "about 0:45 left" in said  # 0.5 s per header, 90 headers left
    assert wait.took() == "0:15"


# --- the readers' callbacks ----------------------------------------------------------------------


def test_the_folder_walk_reports_each_folder_and_finds_the_same(exp):  # noqa: F811
    root = exp[0]
    seen = []
    found = discover_experiment(root, exp[2], progress=seen.append)
    assert found == discover_experiment(root, exp[2])
    assert seen[0] == "." and seen == sorted(seen, key=lambda p: (p != ".", p))
    assert {leaf.relative_path for leaf in found.leaves} <= set(seen)
    assert len(seen) == len(set(seen))  # every folder once


def test_the_check_reports_each_mask_header_and_each_folder(exp):  # noqa: F811
    root, masks, out = exp
    calls = []
    pre = preflight(root, out, masks_folder=masks, progress=lambda *call: calls.append(call))
    same = preflight(root, out, masks_folder=masks)
    assert pre.masks == same.masks and pre.metadata == same.metadata
    order = [phase for phase, *_ in calls]
    assert order == sorted(order, key=PHASES.index)
    reading = [
        (done, total, path) for phase, done, total, path in calls if phase == "masks" and path
    ]
    assert [done for done, _, _ in reading] == list(range(len(pre.masks.files)))
    assert {total for _, total, _ in reading} == {len(pre.masks.files)}
    assert [path for _, _, path in reading] == [mask.path for mask in pre.masks.files]
    folders = [path for phase, _, _, path in calls if phase == "folders" and path]
    assert folders[0] == "." and len(folders) > len(pre.discovery.leaves)
    for phase in PHASES:  # the documented first and last call of every phase are unchanged
        mine = [call for call in calls if call[0] == phase]
        assert mine[0][1] == 0 and mine[-1][1] == mine[-1][2]


def test_pairing_the_masks_again_reports_each_mask(exp):  # noqa: F811
    root, masks, out = exp
    pre = preflight(root, out, masks_folder=masks)
    calls = []
    again = remasked(pre, masks, None, out, lambda *call: calls.append(call))
    assert again.masks == pre.masks
    assert [call[0] for call in calls] == list(range(len(pre.masks.files)))


def test_check_progress_words_each_phase():
    clock, target = Clock(), widgets.HTML()
    wait = Wait(target, clock=clock)
    wait.start(CHECK_PHASES["folders"])
    report = check_progress(wait)
    seen = _history(target)
    report("folders", 0, 1, "")
    for folder in (".", "ctrl", "ctrl/rep1"):
        clock.now += 1
        report("folders", 0, 1, folder)
    report("folders", 1, 1, "")
    report("headers", 0, 2, "")
    clock.now += 1
    report("headers", 1, 2, "ctrl/rep1/b.tif")
    report("headers", 2, 2, "")
    report("masks", 0, 1, "")
    clock.now += 1
    report("masks", 3, 8, "Nuclei/ctrl/rep1/b.tif")
    report("acquisitions", 0, 1, "")
    assert any(line.startswith("Listing the folders · 3 so far · ctrl/rep1") for line in seen)
    assert any(line.startswith("Reading image headers · 0 of 2") for line in seen)
    assert any("Reading image headers · 1 of 2 · ctrl/rep1/b.tif" in line for line in seen)
    assert any("Reading mask headers · 3 of 8 · Nuclei/ctrl/rep1/b.tif" in line for line in seen)
    assert seen[-1].startswith("Comparing the images")
    assert set(CHECK_PHASES) == set(PHASES)


# --- the notebook's waits ------------------------------------------------------------------------


def test_the_folder_check_shows_its_phases_with_counts(exp):  # noqa: F811
    nb = stepper(*exp)
    seen = _history(nb.folder_status)
    nb.check_button.click()
    waits = [line for line in seen if "elapsed" in line]
    for label in ("Listing the folders", "Reading image headers", "Reading mask headers"):
        assert any(line.startswith(label) for line in waits), label
    assert any(re.match(r"Reading image headers · 0 of \d+", line) for line in waits)
    assert any(line.startswith("Writing the check report") for line in waits)
    assert any(re.match(r"Drawing a picture per folder · 0 of \d+", line) for line in waits)
    assert "elapsed" not in _text(nb.folder_status.value)  # the result replaced the wait
    assert not any("Checking the folders and image headers" in line for line in seen)


def test_the_run_and_the_mask_step_show_what_is_read_of_how_many(exp):  # noqa: F811
    nb = stepper(*exp)
    nb.quick_confirm.click()
    answer_all(nb)
    assert nb.save_experiment()
    seen = _history(nb.run_status)
    assert nb.run().written
    assert seen[0].startswith("Preparing: checking the folders and the image headers again")
    assert any(re.match(r"Reading images · 0 of \d+ .*elapsed", line) for line in seen)
    assert any(line.startswith("Writing the table") for line in seen)
    assert nb.progress.value == nb.progress.max and nb.progress.bar_style == "success"
    seen = _history(nb.mask_step_status)
    nb.mask_step_button.click()
    assert seen[0].startswith("Pairing the masks the run recorded")
    assert any(
        re.match(r"Reading the masks, one image at a time · 0 of \d+", line) for line in seen
    )
    assert nb.mask_step_progress.bar_style == "success"


def test_check_the_masks_shows_the_mask_headers_read(exp):  # noqa: F811
    nb = stepper(*exp)
    seen = _history(nb.masks_wait)
    nb.masks_button.click()
    assert seen[0].startswith("Reading mask headers") and seen[-1] == ""
    assert nb.preflight.masks is not None and nb.preflight.masks.files


def test_the_statistics_show_their_stage_of_four(exp):  # noqa: F811
    nb = stepper(*exp)
    nb.quick_confirm.click()
    answer_all(nb)
    assert nb.save_experiment()
    assert nb.run().written
    seen = _history(nb.stats_status)
    nb.statistics_button.click()
    assert seen[0].startswith("Reading the histogram table")
    for stage, label in enumerate(
        ("Pooling the planes of each group", "Drawing the intensity distributions"), start=1
    ):
        assert any(line.startswith(f"{label} · {stage} of 4") for line in seen)
    assert "calculated." in seen[-1]
