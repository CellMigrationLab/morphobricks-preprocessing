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

from ipywidgets.widgets import widget_output  # noqa: E402

from morphobricks_preprocessing import _io, notebook_progress, source_content  # noqa: E402
from morphobricks_preprocessing import (  # noqa: E402
    notebook_preprocessing_cellpose as step_module,
)
from morphobricks_preprocessing import segmentation_cellpose_run as run_module  # noqa: E402
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


def test_opening_says_stopped_when_an_exception_goes_through_it():
    target = widgets.HTML()
    with pytest.raises(RuntimeError), opening(target) as checking:
        checking("images", 0, 2, "a.tif")
        assert OPENING["images"] in target.value and "0 of 2" in target.value
        raise RuntimeError("refused")
    assert "Stopped after 0:00." in target.value and "Opened" not in target.value
    with opening(target) as checking:
        checking("images", 2, 2, "a.tif")
    assert "Opened and checked in 0:00." in target.value


def test_in_a_notebook_the_step_shows_itself_first_and_the_cells_display_adds_nothing(
    app, tmp_path, monkeypatch
):
    # Cell 4 stays the pinned release's (segmentation_step(notebook); display(segmentation)),
    # and still shows the widget before the run is opened (#574 review).
    reads = count_whole_reads(monkeypatch, tmp_path / "exp")
    shown, drawn = [], []
    monkeypatch.setattr(step_module, "get_ipython", lambda: object())
    # How the widget itself is drawn (ipywidgets 7 or 8) is the next two tests'.
    monkeypatch.setattr(
        widgets.Widget, "_ipython_display_", lambda self, **k: drawn.append(self), raising=False
    )

    def display(obj, raw=False):
        assert not raw
        shown.append((obj, sum(reads.values())))
        obj._ipython_display_()  # what IPython's display does with such an object

    monkeypatch.setattr(step_module, "display", display)
    step = segmentation_step(app)
    assert shown == [(step, 0)]
    assert drawn == [step] and step.skip_next_display  # drawn once, before any read
    step._ipython_display_()  # the cell's display(segmentation): nothing more
    assert drawn == [step] and not step.skip_next_display
    step._ipython_display_()  # a later display shows it as any widget
    assert drawn == [step, step]


def test_with_ipywidgets_7_the_step_is_displayed_by_the_widgets_own_display(app, monkeypatch):
    # ipywidgets 7.7 (Colab) has Widget._ipython_display_ and no _repr_mimebundle_ (#575).
    calls = []
    monkeypatch.setattr(step_module, "display", lambda *a, **k: pytest.fail("raw display"))
    monkeypatch.delattr(widgets.Widget, "_repr_mimebundle_", raising=False)
    monkeypatch.setattr(
        widgets.Widget, "_ipython_display_", lambda self, **k: calls.append(self), raising=False
    )
    step = segmentation_step(app)
    step.skip_next_display = True  # as segmentation_step leaves it in a kernel
    step._ipython_display_()
    assert calls == []
    step._ipython_display_()
    assert calls == [step]


def test_with_ipywidgets_8_the_step_is_displayed_by_its_mimebundle(app, monkeypatch):
    if not hasattr(widgets.Widget, "_repr_mimebundle_"):
        pytest.skip("ipywidgets 7: a widget has no _repr_mimebundle_ (the test above is its)")
    published = []
    monkeypatch.setattr(step_module, "display", lambda obj, raw=False: published.append(obj))
    for parent in widgets.Widget.__mro__:
        if "_ipython_display_" in vars(parent):
            monkeypatch.delattr(parent, "_ipython_display_")
    step = segmentation_step(app)
    step._ipython_display_()
    assert published == [step._repr_mimebundle_()]


def test_outside_a_notebook_the_step_shows_nothing_itself(app, monkeypatch):
    monkeypatch.setattr(step_module, "display", lambda *a, **k: pytest.fail("displayed"))
    assert segmentation_step(app).skip_next_display is False


class Kernel:
    """Enough of IPython for ``widgets.Output``: it shows a traceback and swallows the error."""

    kernel = None

    def __init__(self):
        self.tracebacks = []

    def showtraceback(self, exc_info, tb_offset=None):
        self.tracebacks.append(exc_info[0])


def test_an_unexpected_error_while_cell_4_opens_the_run_leaves_the_cell(app, monkeypatch):
    kernel = Kernel()
    monkeypatch.setattr(widget_output, "get_ipython", lambda: kernel)

    def broken(*args, **kwargs):
        raise OSError("Drive disconnected")

    monkeypatch.setattr(run_module, "open_checked", broken)
    with pytest.raises(OSError, match="Drive disconnected"):
        segmentation_step(app)
    assert kernel.tracebacks == [OSError]  # also shown under Open the run


def test_a_refusal_or_a_buttons_error_stays_in_the_form(app, monkeypatch):
    kernel = Kernel()
    monkeypatch.setattr(widget_output, "get_ipython", lambda: kernel)
    step = segmentation_step(app)
    opener = step.workflow.forms["open"]
    monkeypatch.setattr(run_module, "open_checked", lambda *a, **k: 1 / 0)
    opener.submit()  # the button: shown under it, nowhere else to go
    assert kernel.tracebacks == [ZeroDivisionError]
    opener.fields["run_folder"].value = str(app.result.output_folder) + "_missing"
    monkeypatch.undo()
    monkeypatch.setattr(widget_output, "get_ipython", lambda: kernel)
    opener.submit(reraise=True)  # a refusal is shown, never raised
    assert opener.stopped is not None and kernel.tracebacks == [ZeroDivisionError]
