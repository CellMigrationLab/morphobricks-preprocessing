"""The **Stop** button of the long steps (owner request of 26 November 2026, v0.9.1).

Run (with its checks before the first image) and cell 5's **Register the masks** can be
stopped between images: nothing is half published, the run stays resumable, and one line says
"Stopped after m:ss; run again to continue". In a kernel the work runs in a thread so that the
click of **Stop** is handled (a click waits while a callback blocks); here it also runs in the
calling thread, as outside a kernel, and once in a real thread.
"""

import shutil

import pytest

from cellpose_stub import install
from test_notebook_check_save import FOLDERS, add_folder, answer_all

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")

from morphobricks_preprocessing import notebook_stop_button  # noqa: E402
from morphobricks_preprocessing import notebook_style as ui  # noqa: E402
from morphobricks_preprocessing.manifest import MANIFEST_FILENAME  # noqa: E402
from morphobricks_preprocessing.notebook_mask_register import (  # noqa: E402
    STOPPED_BY_USER,
    register_step,
)
from morphobricks_preprocessing.notebook_preprocessing_cellpose import (  # noqa: E402
    segmentation_step,
)
from morphobricks_preprocessing.notebook_stop_button import STOPPED, StopPressed  # noqa: E402
from morphobricks_preprocessing.notebook_ui import PreprocessingNotebook  # noqa: E402
from morphobricks_preprocessing.run import resume  # noqa: E402
from test_notebook_preprocessing_cellpose import segment  # noqa: E402

pytestmark = pytest.mark.usefixtures("legacy_grid")
STOPPED_WORDS = STOPPED.split("{")[0]


def ready_app(tmp_path):
    """Return the app of cell 3 ready to run (checked, confirmed, saved), never run."""
    root, masks = tmp_path / "exp", tmp_path / "masks"
    for i, folder in enumerate(FOLDERS):
        add_folder(root, masks, folder, i + 1)
    shutil.rmtree(masks)
    nb = PreprocessingNotebook(str(root), output_folder=str(tmp_path / "out"))
    nb.welcome()
    nb.steps()
    nb.check_button.click()
    nb.quick_confirm.click()
    answer_all(nb)
    assert nb.save_experiment()
    return nb


def stop_at(nb, image_number):
    """Press Stop as the run reports image ``image_number`` (0-based) of its reading."""
    real = nb._progress

    def progress(done, total, path):
        if path and done == image_number:
            nb.stopper.button.click()
        real(done, total, path)

    nb._progress = progress


def walk(widget):
    yield widget
    for child in getattr(widget, "children", ()):
        yield from walk(child)


def published(out):
    names = (MANIFEST_FILENAME, "plane_histograms.csv")
    return {name: (out / name).read_bytes() for name in names if (out / name).is_file()}


def reads(monkeypatch):
    seen = []
    real = resume._read_file

    def counted(*args, **kwargs):
        seen.append(args[0] if args else kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(resume, "_read_file", counted)
    return seen


def test_run_stopped_between_images_publishes_nothing_and_resumes(tmp_path, monkeypatch):
    nb = ready_app(tmp_path)
    out = tmp_path / "out"
    nb.workers.value = 1  # one image at a time: the count of the images read is exact
    stop_at(nb, 2)
    read = reads(monkeypatch)
    nb.run_button.click()  # outside a kernel the work runs here, as a press would in a thread
    assert len(read) == 2  # two images read and staged, then stopped before the third
    assert nb.result is None and STOPPED_WORDS in nb.run_status.value
    assert "run again to continue" in nb.run_status.value
    assert published(out) == {}  # nothing half published
    assert not ui.is_visible(nb.stopper.button)  # Stop hidden once it ended
    assert not nb.run_button.disabled  # the app's inputs are back
    del nb._progress
    nb.run_button.click()  # pressed again: the staged rows are resumed, two more read
    assert len(read) == 4 and nb.result is not None and nb.result.written
    first = published(out)
    other = ready_app(tmp_path / "uninterrupted")
    assert other.run().written
    assert (
        published(tmp_path / "uninterrupted" / "out")["plane_histograms.csv"]
        == (first["plane_histograms.csv"])
    )


def test_a_stopped_rerun_leaves_the_earlier_publication_as_it_was(tmp_path):
    nb = ready_app(tmp_path)
    assert nb.run().written
    out = tmp_path / "out"
    before = published(out)
    nb.mode.value = "full"  # every image read again
    stop_at(nb, 1)
    nb.run_button.click()
    assert STOPPED_WORDS in nb.run_status.value
    assert published(out) == before


def test_the_checks_before_the_first_image_can_be_stopped(tmp_path):
    nb = ready_app(tmp_path)
    real = nb._checking

    def checking(phase, done, total, path):
        if phase == "headers" and done == 1:
            nb.stopper.button.click()
        real(phase, done, total, path)

    nb._checking = checking
    nb.run_button.click()
    assert STOPPED_WORDS in nb.run_status.value and published(tmp_path / "out") == {}


def test_stop_is_handled_while_the_run_works_in_a_thread(tmp_path, monkeypatch):
    # The case of a kernel: the click returns at once, the run goes on in a thread, the app's
    # inputs are disabled meanwhile, and a click of Stop (here from this thread) ends it.
    import threading

    nb = ready_app(tmp_path)
    monkeypatch.setattr(notebook_stop_button, "in_kernel", lambda: True)
    reading, release = threading.Event(), threading.Event()
    real = nb._progress

    def progress(done, total, path):
        if path and done == 1:
            reading.set()
            release.wait(10)
        real(done, total, path)

    nb._progress = progress
    nb.run_button.click()
    worker = nb.stopper.running
    assert worker is not None and reading.wait(10)
    assert nb.check_button.disabled and not nb.stopper.button.disabled  # frozen but Stop
    nb.stopper.button.click()
    release.set()
    worker.join(10)
    assert not worker.is_alive() and nb.stopper.running is None
    assert STOPPED_WORDS in nb.run_status.value and published(tmp_path / "out") == {}
    assert not nb.run_button.disabled


def test_stop_before_any_step_runs_does_nothing(tmp_path):
    nb = ready_app(tmp_path)
    nb.stopper.button.click()  # hidden, never armed: no flag is left for the next run
    assert not nb.stopper.requested
    nb.stopper.check()


def test_register_the_masks_can_be_stopped_and_pressed_again(tmp_path, monkeypatch):
    install(monkeypatch, tmp_path)
    nb = ready_app(tmp_path)
    assert nb.run().written
    step = segmentation_step(nb)
    segment(step, tmp_path / "masks")
    registration = register_step(nb, step, show=None)
    registration.choices["Cells"].value = "instances"
    assert nb.stopper.button in list(walk(registration))  # the app's Stop, shown here too
    before = published(tmp_path / "out")
    real = registration._checking

    def checking(wait):
        inner = real(wait)

        def told(phase, done, total, path):
            if phase == "contents" and done == 1:
                nb.stopper.button.click()
            inner(phase, done, total, path)

        return told

    registration._checking = checking
    registration.button.click()
    assert not registration.registered
    assert STOPPED_BY_USER in registration.status.value
    assert STOPPED_WORDS in registration.status.value
    assert published(tmp_path / "out") == before  # nothing declared, saved or run
    del registration._checking
    registration.button.click()
    assert registration.registered, registration.status.value


def test_stop_pressed_is_an_interrupt():
    assert issubclass(StopPressed, KeyboardInterrupt)  # every interrupt-safe path handles it
