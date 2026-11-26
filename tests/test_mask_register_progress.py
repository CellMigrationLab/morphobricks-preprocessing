"""Cell 5's question and its wait (owner Colab test of 26 November 2026, issue #574 items 6-7).

Item 6: "Declare Cells as separate objects" with a TOML preview was awkward. Each mask type is
now asked what it is, with no answer chosen: each object with its own number (a label image) or
foreground / background only (a binary mask); the TOML is folded under it. What is recorded,
and the refusal when nothing is chosen, are unchanged.

Item 7: after **Register the masks** something ran for a long time with nothing in cell 5. It
reads, per image of a ``verify_content`` run, 4 whole files (the run's checks, then the app's
run, its check before it publishes and its tissue-mask step's opening, each for the content
SHA-256) and per mask 5 (the run, its check before publishing, the mask step's fingerprints
before and after, and the pixels); the app drew its waits in cell 3. Cell 5 now shows each step
with the waits of #567. What is read is unchanged: no check could read less without becoming
weaker (the reads per file are pinned here).
"""

import os
from collections import Counter
from pathlib import Path

import pytest

from cellpose_stub import install
from channel_images import write_channels
from test_notebook_check_save import KINDS
from test_notebook_preprocessing_cellpose import ran_app, segment

widgets = pytest.importorskip("ipywidgets")

import builtins  # noqa: E402
import io  # noqa: E402

import test_notebook_preprocessing_cellpose as cell_tests  # noqa: E402
from morphobricks_preprocessing import notebook_progress  # noqa: E402
from morphobricks_preprocessing.description_file import DESCRIPTION_FILENAME  # noqa: E402
from morphobricks_preprocessing.notebook_mask_register import (  # noqa: E402
    BINARY_REFUSED,
    BINARY_REFUSED_CODE,
    CHOICES,
    PAIRING,
    WHY_WE_ASK,
    refusal,
    register_step,
    toml_section,
)
from morphobricks_preprocessing.notebook_preprocessing_cellpose import (  # noqa: E402
    segmentation_step,
)
from morphobricks_preprocessing.notebook_register_progress import (  # noqa: E402
    CHECKS,
    STOPPED_AFTER,
    RegisterWait,
    step_label,
)
from morphobricks_preprocessing.run_inputs import RunInputs  # noqa: E402

pytestmark = pytest.mark.usefixtures("legacy_grid")

SIDE = 256
"""Images and masks large enough that reading a header is not reading the whole file."""


@pytest.fixture
def stub(monkeypatch, tmp_path):
    return install(monkeypatch, tmp_path)


@pytest.fixture
def larger(monkeypatch):
    """Write the experiment of ``ran_app`` at :data:`SIDE` pixels."""

    def add_folder(root, masks, folder, seed):
        Path(masks).mkdir(parents=True, exist_ok=True)
        write_channels(root / folder / "a.tif", ["DAPI", "Actin", "Ch3"], KINDS, seed, SIDE)

    monkeypatch.setattr(cell_tests, "add_folder", add_folder)


class Reads:
    """Per file of the experiment and the masks folder: opens, and whole reads.

    A whole read is one opening of the file through which at least its size was read.
    """

    def __init__(self, base):
        self.base = Path(base).resolve()
        self.opens, self.whole = Counter(), Counter()

    def key(self, path):
        where = Path(os.fspath(path)).resolve()
        for folder in ("exp", "masks"):
            if where.is_relative_to(self.base / folder):
                return where.relative_to(self.base).as_posix()
        return None


@pytest.fixture
def reads(monkeypatch, tmp_path):
    """Count the reads with a patched opener (``builtins.open``, as tifffile and hashing use)."""
    counted, real = Reads(tmp_path), builtins.open

    class Counting(io.BufferedReader):
        def __init__(self, path, key):
            super().__init__(io.FileIO(path, "rb"))
            self.key, self.size, self.got = key, os.path.getsize(path), 0

        def read(self, *args):
            data = super().read(*args)
            self.got += len(data)
            return data

        def readinto(self, buffer):
            n = super().readinto(buffer)
            self.got += n or 0
            return n

        def close(self):
            if not self.closed and self.got >= self.size > 0:
                counted.whole[self.key] += 1
            super().close()

    def opener(file, mode="r", *args, **kwargs):
        key = counted.key(file) if isinstance(file, (str, os.PathLike)) else None
        if key is None:
            return real(file, mode, *args, **kwargs)
        counted.opens[key] += 1
        if mode in ("rb", "br") and not args and all(v is None for v in kwargs.values()):
            return Counting(os.fspath(file), key)
        return real(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", opener)
    return counted


def segmented_app(tmp_path):
    """Return the app and cell 4 after it segmented the app's run into ``tmp_path/masks``."""
    nb = ran_app(tmp_path)
    step = segmentation_step(nb)
    segment(step, tmp_path / "masks")
    return nb, step


def answered(nb, step):
    """Return cell 5 answered for Cells (one press registers it since v0.9.1)."""
    registration = register_step(nb, step)
    registration.choices["Cells"].value = "instances"
    return registration


# item 6: the question --------------------------------------------------------------------------


def test_each_mask_type_is_asked_what_it_is_with_no_answer_chosen(tmp_path, stub):
    nb, step = segmented_app(tmp_path)
    registration = register_step(nb, step)
    choice = registration.choices["Cells"]
    assert choice.value is None
    assert [label for label, _ in CHOICES] == [
        "Each object has its own number (label image: every cell/nucleus is a separate object)",
        "Foreground / background only (binary mask)",
    ]
    assert list(choice.options) == CHOICES
    text = repr(registration.children)
    assert WHY_WE_ASK in text.replace("\\'", "'")
    assert "Declare Cells as separate objects" not in text
    # The declaration is shown folded only.
    section = toml_section("Cells").replace('"', "&quot;")
    with_section = [w.value for w in _all_html(registration) if "[masks.Cells]" in w.value]
    assert len(with_section) == 1 and with_section[0].startswith("<details>")
    assert section in with_section[0]


def _all_html(widget):
    """Yield every HTML widget inside ``widget``."""
    if isinstance(widget, widgets.HTML):
        yield widget
    for child in getattr(widget, "children", ()):
        yield from _all_html(child)


def test_nothing_chosen_is_refused_as_before_and_nothing_is_declared(tmp_path, stub):
    nb, step = segmented_app(tmp_path)
    out = nb.result.output_folder
    registration = register_step(nb, step)
    assert registration.button.disabled and not registration.register()
    assert "choose what Cells is" in registration.blocker.value
    assert "[masks" not in (out / DESCRIPTION_FILENAME).read_text(encoding="utf-8")
    assert registration.progress.value == ""  # nothing ran


def test_a_binary_answer_is_refused_saying_why_and_nothing_is_declared(tmp_path, stub):
    nb, step = segmented_app(tmp_path)
    out = nb.result.output_folder
    registration = register_step(nb, step)
    registration.choices["Cells"].value = "binary"
    assert registration.button.disabled and not registration.register()
    text = BINARY_REFUSED.format(mask_type="Cells")
    assert text.split(":")[0] in registration.blocker.value
    # #578 TODO 1: a coded refusal that says why (Cellpose numbers each object).
    assert BINARY_REFUSED_CODE in registration.blocker.value
    assert "gives each object its own number" in registration.blocker.value
    assert "[masks" not in (out / DESCRIPTION_FILENAME).read_text(encoding="utf-8")
    registration.choices["Cells"].value = "instances"
    assert not registration.button.disabled and registration.blocker.value == ""


def test_the_recorded_declaration_is_unchanged(tmp_path, stub):
    nb, step = segmented_app(tmp_path)
    registration = answered(nb, step)
    assert registration.register(), registration.status.value
    text = (nb.result.output_folder / DESCRIPTION_FILENAME).read_text(encoding="utf-8")
    assert '[masks.Cells]\nsource_type = "Cells"\nrepresentation = "instances"' in text
    assert RunInputs.open(nb.result.output_folder).mask_types() == ("Cells",)


# item 7: what Register reads, and its wait ----------------------------------------------------


def test_one_press_reads_each_image_whole_twice(tmp_path, stub, larger, reads):
    # Before v0.9.1 (pinned here then): 4 whole reads per image per registering press (the
    # run's checks in cell 5, the app's run, its check before it publishes, the tissue-mask
    # step's opening), after a first press that stopped for the axes and read each image whole
    # once more. Now one press, and within it a SHA-256 is reused while the file's identity is
    # unchanged (#578 TODO 2): the run's content pass and the tissue-mask step's opening reuse
    # the one cell 5's checks took; the check before the run publishes takes its own. Per mask:
    # the run and its check before publishing (SHA-256), the mask step's fingerprints before and
    # after reading, and its pixels, as before; the first mask is read once more, whole (it is
    # small here), by the masks pairing that the first press's folder check used to make.
    nb, step = segmented_app(tmp_path)
    registration = answered(nb, step)
    images = sorted(f"exp/{f.relative_path}" for f in step.workflow.inputs.fovs())
    masks = sorted(f"masks/Cells/{f.relative_path}" for f in step.workflow.inputs.fovs())
    assert len(images) == len(masks) == 4
    reads.opens.clear(), reads.whole.clear()
    assert registration.register(), registration.status.value
    first, *others = images
    first_mask, *other_masks = masks
    assert reads.whole == Counter(
        {**dict.fromkeys(images, 2), first_mask: 6, **dict.fromkeys(other_masks, 5)}
    )
    assert reads.opens == Counter(
        {first: 24, **dict.fromkeys(others, 8), first_mask: 9, **dict.fromkeys(other_masks, 8)}
    )


def test_a_file_changed_mid_press_is_still_caught(tmp_path, stub, monkeypatch):
    # #578 TODO 2: an image rewritten between two checks of one press. Its status-change time
    # moves, so the memo hashes it again. Where the identity cannot see a change (frozen here,
    # as on a file system without one), the run's check before it publishes, which never uses
    # the memo, still refuses it: nothing is declared as registered.
    from morphobricks_preprocessing import content_memo

    nb, step = segmented_app(tmp_path)
    registration = answered(nb, step)
    image = tmp_path / "exp" / step.workflow.inputs.fovs()[0].relative_path
    frozen = {}
    real_identity = content_memo.file_identity

    def blind(path):
        key = str(path)
        frozen.setdefault(key, real_identity(path))
        return frozen[key]

    monkeypatch.setattr(content_memo, "file_identity", blind)
    save = nb.save_experiment

    def save_then_change():
        saved = save()
        stat = image.stat()
        data = bytearray(image.read_bytes())
        data[-1] ^= 0xFF  # same size; the modification time is set back below
        image.write_bytes(bytes(data))
        os.utime(image, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        return saved

    monkeypatch.setattr(nb, "save_experiment", save_then_change)
    assert not registration.register()
    assert not registration.registered
    assert "The run of cell 3 stopped" in registration.status.value


def test_showing_cell_5_reads_each_image_whole_once(tmp_path, stub, larger, reads):
    nb, step = segmented_app(tmp_path)
    reads.opens.clear(), reads.whole.clear()
    register_step(nb, step)
    images = [f"exp/{f.relative_path}" for f in step.workflow.inputs.fovs()]
    assert reads.whole == Counter(dict.fromkeys(images, 1)) == reads.opens


def test_register_names_each_step_counts_the_images_and_ticks(tmp_path, stub, monkeypatch):
    nb, step = segmented_app(tmp_path)
    registration = answered(nb, step)
    monkeypatch.setattr(notebook_progress, "MIN_INTERVAL", 0.0)  # every step drawn
    ticking, original = [], notebook_progress.Wait.ticking

    def spy(self, *args, **kwargs):
        ticking.append(self.target)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(notebook_progress.Wait, "ticking", spy)
    lines, details = [], []
    registration.progress.observe(lambda c: lines.append(c["new"]), names="value")
    registration.progress_detail.observe(lambda c: details.append(c["new"]), names="value")
    names = [f.relative_path for f in step.workflow.inputs.fovs()]
    assert registration.register(), registration.status.value
    for label in (step_label(k) for k in ("run", "save", "rerun", "describe")):
        assert any(label in line for line in lines), label
    assert not any(step_label("folders") in line for line in lines)  # no folder check made
    assert any(step_label("folders", PAIRING) in line for line in lines)  # the masks paired
    hashed = [d for d in details if CHECKS["contents"] in d]
    for done, name in enumerate(names):
        assert any(f"{done} of 4" in d and name in d for d in hashed), name
    assert any("Preparing:" in d for d in details)  # the app's run's own wait, copied
    assert any("Pairing the masks" in d for d in details)  # the app's mask step's
    assert ticking[0] is registration.progress  # the timer thread of #567 redraws the line
    assert "Took " in registration.progress.value and registration.progress_detail.value == ""
    assert "The masks are registered" in registration.status.value
    assert not registration.button.disabled  # back once it ended


def test_a_press_after_a_change_shows_the_folder_check_of_the_app(tmp_path, stub):
    nb, step = segmented_app(tmp_path)
    registration = register_step(nb, step)
    registration.choices["Cells"].value = "instances"
    nb.confirm_button.click()  # the axes taken back in cell 3: the folders are checked again
    lines, details = [], []
    registration.progress.observe(lambda c: lines.append(c["new"]), names="value")
    registration.progress_detail.observe(lambda c: details.append(c["new"]), names="value")
    assert not registration.register()  # the axes to confirm again
    assert any(step_label("folders") in line for line in lines)
    assert details and any(d for d in details)  # the app's folder check, copied
    assert "Took " in registration.progress.value


def test_the_run_checks_refuse_the_same_with_or_without_the_wait(tmp_path, stub):
    nb, step = segmented_app(tmp_path)
    image = tmp_path / "exp" / "ctrl" / "rep1" / "a.tif"
    image.write_bytes(image.read_bytes()[:-1] + b"\x07")
    told = []
    quiet = refusal(nb, step)
    shown = refusal(nb, step, checking=lambda *args: told.append(args))
    assert quiet == shown and "The images changed since the run" in shown
    assert told and told[0][0] == "run"


def test_the_wait_says_stopped_after_an_error_and_stops_copying():
    line, detail, source = widgets.HTML(), widgets.HTML(), widgets.HTML()
    wait = RegisterWait(line, detail)
    with pytest.raises(KeyboardInterrupt), wait.running():
        with wait.step("rerun", source):
            source.value = "the app's line"
            assert detail.value == "the app's line"
            raise KeyboardInterrupt
    assert STOPPED_AFTER.split("{")[0] in line.value and detail.value == ""
    source.value = "later"
    assert detail.value == ""  # copied only while its step ran


# #578 TODO 3: cell 5 is shown first, then opens the run with its wait (#575's pattern) -------


def test_cell_5_is_shown_before_the_run_is_read_then_says_how_long(tmp_path, stub, reads):
    from morphobricks_preprocessing import notebook_mask_register as cell_5
    from morphobricks_preprocessing.notebook_open_progress import TOOK

    nb, step = segmented_app(tmp_path)
    reads.opens.clear(), reads.whole.clear()
    seen = []

    def show(registration):
        seen.append((sum(reads.whole.values()), list(registration.choices)))
        cell_5.display(registration)

    shown = []
    cell_5_display = cell_5.display
    cell_5.display = shown.append
    try:
        registration = register_step(nb, step, show=show)
    finally:
        cell_5.display = cell_5_display
    assert seen == [(0, ["Cells"])]  # shown, with its question, before any image is read
    assert shown == [registration]
    assert sum(reads.whole.values()) == 4  # then the run is opened: each image once
    assert TOOK.split("{")[0] in registration.opening.value


def test_cell_5_shown_first_is_not_shown_twice_by_the_cell(tmp_path, stub, monkeypatch):
    from morphobricks_preprocessing import notebook_mask_register as module

    nb, step = segmented_app(tmp_path)
    monkeypatch.setattr(module, "get_ipython", lambda: object())  # as in a notebook
    shown = []
    monkeypatch.setattr(module, "display", shown.append)
    registration = register_step(nb, step)
    assert shown == [registration] and registration.skip_next_display
    registration._ipython_display_()  # the cell's own display(registration): nothing more
    assert not registration.skip_next_display


def test_a_refusal_found_when_cell_5_opens_replaces_the_question(tmp_path, stub):
    nb, step = segmented_app(tmp_path)
    image = tmp_path / "exp" / step.workflow.inputs.fovs()[0].relative_path
    image.write_bytes(image.read_bytes()[:-1] + b"\x07")
    registration = register_step(nb, step, show=None)
    assert registration.choices == {} and registration.refused
    assert "The images changed since the run" in registration.status.value
