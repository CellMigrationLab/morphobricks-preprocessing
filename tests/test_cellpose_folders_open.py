"""The Cellpose step Folders opens the run once, on screen, with its wait (issue #574, item 2).

Owner's Colab test of v0.9.0 (26 November 2026): "Step 1 of 4 · Folders" showed nothing for
about 4 minutes on ~200 images of 10-50 MB on Google Drive. Cell 4 opened the app's run twice
before its widget was shown (once for the notice's refusal, once by **Open the run**), and each
opening of a ``verify_content`` run reads every image whole for its content SHA-256. It now
shows the widget first, opens the run once, and names each phase with "n of N" and the time.
The checks are the same: :func:`.run_inputs.open_checked` refuses and returns what
:meth:`.RunInputs.open` does.
"""

import os
from collections import Counter
from pathlib import Path

import numpy as np
import pytest

from cellpose_stub import install
from test_notebook_preprocessing_cellpose import ran_app

widgets = pytest.importorskip("ipywidgets")

from morphobricks_preprocessing import _io, notebook_progress, source_content  # noqa: E402
from morphobricks_preprocessing.notebook_open_progress import OPENING, opening  # noqa: E402
from morphobricks_preprocessing.notebook_preprocessing_cellpose import (  # noqa: E402
    RUN_REFUSED,
    segmentation_step,
)
from morphobricks_preprocessing.open_checks import OPEN_PHASES  # noqa: E402
from morphobricks_preprocessing.run_inputs import (  # noqa: E402
    RunInputs,
    RunInputsError,
    open_checked,
)

pytestmark = pytest.mark.usefixtures("legacy_grid")


def count_whole_reads(monkeypatch, root, seen=None):
    """Count, per image of ``root``, the whole reads for a content SHA-256 (``file_sha256``).

    ``seen`` (a list) gets, at each read, what ``seen_now()`` returns, if given as a pair.
    """
    reads, real = Counter(), _io.file_sha256
    root = Path(root).resolve()

    def counting(path):
        where = Path(path).resolve()
        if where.is_relative_to(root):
            reads[where.relative_to(root).as_posix()] += 1
            if seen is not None:
                seen[0].append(seen[1]())
        return real(path)

    monkeypatch.setattr(source_content, "file_sha256", counting)
    return reads


@pytest.fixture
def app(tmp_path, monkeypatch):
    nb = ran_app(tmp_path, with_masks=True)  # the app's masks folder: cell 4 opens the run
    install(monkeypatch, tmp_path)
    assert nb.result.written
    return nb


def test_cell_4_reads_each_image_whole_once_not_twice(app, tmp_path, monkeypatch):
    reads = count_whole_reads(monkeypatch, tmp_path / "exp")
    step = segmentation_step(app)
    assert step.refused == "" and step.workflow.inputs is not None
    images = [f.relative_path for f in step.workflow.inputs.fovs()]
    assert len(images) == 4
    assert reads == Counter({image: 1 for image in images})  # was 2 each before #574


def test_cell_4_is_shown_before_any_image_is_read(app, tmp_path, monkeypatch):
    reads = count_whole_reads(monkeypatch, tmp_path / "exp")
    shown = []
    step = segmentation_step(app, show=lambda widget: shown.append((widget, sum(reads.values()))))
    assert shown == [(step, 0)]
    assert sum(reads.values()) == 4


def test_the_wait_names_the_phase_counts_the_images_and_ticks(app, tmp_path, monkeypatch):
    holder, lines = [], []
    reads = count_whole_reads(
        monkeypatch, tmp_path / "exp", (lines, lambda: holder[0].workflow.opening.value)
    )
    monkeypatch.setattr(notebook_progress, "MIN_INTERVAL", 0.0)  # every step drawn
    ticking, original = [], notebook_progress.Wait.ticking

    def spy(self, *args, **kwargs):
        ticking.append(self.target)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(notebook_progress.Wait, "ticking", spy)
    step = segmentation_step(app, show=holder.append)
    assert sum(reads.values()) == 4 and len(lines) == 4
    for done, line in enumerate(lines):
        assert OPENING["contents"] in line
        assert f"{done} of 4" in line and "elapsed" in line
    # The read image is named (escaped as HTML): the four images in path order.
    names = [f.relative_path for f in step.workflow.inputs.fovs()]
    assert [next(n for n in names if n in line) for line in lines] == names
    assert ticking == [step.workflow.opening]  # the timer thread of #567 redraws the line
    assert step.workflow.opening.value.endswith("</div>") and "Opened and checked in" in (
        step.workflow.opening.value
    )


def test_open_checked_tells_every_phase_in_order_and_opens_what_open_opens(app):
    out = app.result.output_folder
    told = []
    checked = open_checked(out, checking=lambda *call: told.append(call))
    plain = RunInputs.open(out)
    assert checked.fovs() == plain.fovs()
    assert checked.manifest_sha256 == plain.manifest_sha256
    assert checked.masks_root == plain.masks_root
    assert [r.plane_id for r in checked.records] == [r.plane_id for r in plain.records]
    for ours, theirs in zip(checked.records, plain.records, strict=True):
        for a, b in zip(ours.histograms, theirs.histograms, strict=True):
            assert np.array_equal(a.counts, b.counts)
    phases = list(dict.fromkeys(phase for phase, *_ in told))
    assert phases == list(OPEN_PHASES)
    for phase in ("images", "contents"):
        calls = [c for c in told if c[0] == phase]
        assert [c[1:3] for c in calls] == [(i, 4) for i in range(5)]  # before each, then 4 of 4
        assert calls[-1][3] == ""


def test_a_changed_image_is_refused_the_same_way_with_or_without_the_wait(app, tmp_path):
    out = app.result.output_folder
    image = sorted((tmp_path / "exp").rglob("*.tif"))[0]
    before = image.stat()
    data = bytearray(image.read_bytes())
    data[-1] ^= 0xFF  # same size, same modification time: only the content SHA-256 sees it
    image.write_bytes(bytes(data))
    os.utime(image, ns=(before.st_atime_ns, before.st_mtime_ns))
    with pytest.raises(RunInputsError) as plain:
        RunInputs.open(out)
    with pytest.raises(RunInputsError) as checked:
        open_checked(out, checking=lambda *call: None)
    assert str(checked.value) == str(plain.value)
    step = segmentation_step(app)
    assert step.refused == str(plain.value)
    assert RUN_REFUSED.split(".")[0] in step.notice.value.replace("&#x27;", "'")


def test_the_step_without_a_masks_folder_shows_its_own_wait(tmp_path, monkeypatch):
    nb = ran_app(tmp_path)  # no masks folder: the run is opened for its refusal only
    install(monkeypatch, tmp_path)
    reads = count_whole_reads(monkeypatch, tmp_path / "exp")
    step = segmentation_step(nb)
    assert step.refused == "" and sum(reads.values()) == 4
    assert "Opened and checked in" in step.opening.value


def test_opening_leaves_the_time_it_took_even_when_refused():
    target = widgets.HTML()
    with pytest.raises(RuntimeError), opening(target) as checking:
        checking("images", 0, 2, "a.tif")
        assert OPENING["images"] in target.value and "0 of 2" in target.value
        raise RuntimeError("refused")
    assert "Opened and checked in 0:00" in target.value
