"""Register the masks after the external review of #582 (bc, 27 November 2026): B1, B2, B3.

- B1: masks already paired in cell 3 skipped the check that nothing changed since cell 3's
  folder check; a stale check (another header fingerprint) was then used for the axes.
- B2: an image rewritten during the press (its status-change time moved, its size and
  modification time set back) was hashed again and published as a *new* image with the masks
  segmented from the old one. Register is now bound to the generation cell 4 segmented.
- B3: Register said the masks were registered even when *Describe the masks* failed.
- Our own review: an ``OSError`` while the masks' headers are read is a failed folder check
  (CHECK_FAILED), and the "Reading mask headers" line does not stay.
"""

import dataclasses
import os

import pytest

from cellpose_stub import install
from test_mask_register_progress import answered, segmented_app

widgets = pytest.importorskip("ipywidgets")

from morphobricks_preprocessing.description_file import DESCRIPTION_FILENAME  # noqa: E402
from morphobricks_preprocessing.manifest import MANIFEST_FILENAME  # noqa: E402
from morphobricks_preprocessing.notebook_mask_register import (  # noqa: E402
    AXES_AGAIN,
    CHECK_FAILED,
    DESCRIBE_FAILED,
    STOPPED,
)
from morphobricks_preprocessing.notebook_register_progress import step_label  # noqa: E402
from morphobricks_preprocessing.run.expected import NOT_SEGMENTED  # noqa: E402

pytestmark = pytest.mark.usefixtures("legacy_grid")


@pytest.fixture
def stub(monkeypatch, tmp_path):
    return install(monkeypatch, tmp_path)


def _paired(nb, step):
    """Pair the masks folder cell 4 wrote in the app of cell 3 (Check the masks again)."""
    nb.masks_folder.value = os.fspath(step.workflow.masks_root)
    nb._check_masks()
    assert "Cells" in nb.preflight.masks.types


def _manifest_bytes(nb):
    return (nb.result.output_folder / MANIFEST_FILENAME).read_bytes()


def _refused_as_not_segmented(nb):
    return nb.result is not None and any(i.code == NOT_SEGMENTED for i in nb.result.issues)


def _lines(registration):
    lines = []
    registration.progress.observe(lambda c: lines.append(c["new"]), names="value")
    return lines


# B1 --------------------------------------------------------------------------------------------


def test_masks_already_paired_with_a_stale_check_are_checked_again(tmp_path, stub, monkeypatch):
    nb, step = segmented_app(tmp_path)
    _paired(nb, step)
    first = step.workflow.inputs.fovs()[0].relative_path
    stale = dict(nb.preflight.header_fingerprints)
    stale[first] = None  # cell 3's header of that image is not bound to the file now
    nb.preflight = dataclasses.replace(nb.preflight, header_fingerprints=stale)
    checked = []
    real = nb.check_folder
    monkeypatch.setattr(nb, "check_folder", lambda: checked.append(1) or real())
    out = nb.result.output_folder
    registration = answered(nb, step)
    lines = _lines(registration)
    assert not registration.register()
    assert checked == [1]  # the app's folder check was made again
    assert any(step_label("folders") in line for line in lines)
    assert AXES_AGAIN.split(":")[0] in registration.status.value
    assert first in registration.status.value  # the change is named
    assert not registration.registered
    assert "[masks" not in (out / DESCRIPTION_FILENAME).read_text(encoding="utf-8")


def test_masks_already_paired_and_unchanged_register_in_one_press(tmp_path, stub, monkeypatch):
    nb, step = segmented_app(tmp_path)
    _paired(nb, step)
    monkeypatch.setattr(nb, "check_folder", lambda: pytest.fail("no folder check is needed"))
    registration = answered(nb, step)
    assert registration.register(), registration.status.value
    assert registration.registered


def test_the_check_is_made_again_just_before_the_save(tmp_path, stub, monkeypatch):
    # A header changed after the masks were paired, before the save: nothing is declared.
    nb, step = segmented_app(tmp_path)
    _paired(nb, step)
    first = step.workflow.inputs.fovs()[0].relative_path
    registration = answered(nb, step)
    answer = registration._answer_masks

    def answer_then_stale():
        stale = dict(nb.preflight.header_fingerprints)
        stale[first] = None
        nb.preflight = dataclasses.replace(nb.preflight, header_fingerprints=stale)
        return answer()

    monkeypatch.setattr(registration, "_answer_masks", answer_then_stale)
    saved = []
    monkeypatch.setattr(nb, "save_experiment", lambda: saved.append(1) or True)
    assert not registration.register()
    assert saved == [] and not registration.registered
    assert first in registration.status.value


# B2 --------------------------------------------------------------------------------------------


def _rewrite_in_place(image):
    """Change one pixel byte; set the modification time back (the status-change time moves)."""
    import tifffile

    stat = image.stat()
    with tifffile.TiffFile(image) as tif:
        offset = tif.pages[0].dataoffsets[0]
    data = bytearray(image.read_bytes())
    data[offset] ^= 0x01  # a valid pixel value still: the header and the axes are unchanged
    image.write_bytes(bytes(data))
    os.utime(image, ns=(stat.st_atime_ns, stat.st_mtime_ns))


@pytest.mark.parametrize("when", ["after_save", "after_refusal"])
def test_an_image_rewritten_during_the_press_is_not_registered(tmp_path, stub, monkeypatch, when):
    # The memo is NOT frozen: the rewrite is seen and the image hashed again. Before the fix
    # the run recomputed its rows and published the new bytes with the old masks.
    nb, step = segmented_app(tmp_path)
    registration = answered(nb, step)
    image = tmp_path / "exp" / step.workflow.inputs.fovs()[0].relative_path
    before = _manifest_bytes(nb)
    if when == "after_save":
        save = nb.save_experiment

        def save_then_change():
            saved = save()
            _rewrite_in_place(image)
            return saved

        monkeypatch.setattr(nb, "save_experiment", save_then_change)
    else:
        from morphobricks_preprocessing import notebook_mask_register as cell_5

        real = cell_5.refusal

        def refusal_then_change(*args, **kwargs):
            why = real(*args, **kwargs)
            _rewrite_in_place(image)
            return why

        monkeypatch.setattr(cell_5, "refusal", refusal_then_change)
    assert not registration.register()
    assert not registration.registered
    assert "The masks are registered" not in registration.status.value
    assert _manifest_bytes(nb) == before  # the segmented generation is unchanged
    assert _refused_as_not_segmented(nb), nb.run_issues.value


def test_an_image_whose_size_changed_during_the_press_is_not_registered(
    tmp_path, stub, monkeypatch
):
    nb, step = segmented_app(tmp_path)
    registration = answered(nb, step)
    image = tmp_path / "exp" / step.workflow.inputs.fovs()[0].relative_path
    before = _manifest_bytes(nb)
    save = nb.save_experiment

    def save_then_grow():
        saved = save()
        image.write_bytes(image.read_bytes() + b"\0")
        return saved

    monkeypatch.setattr(nb, "save_experiment", save_then_grow)
    assert not registration.register() and not registration.registered
    assert _manifest_bytes(nb) == before


def test_turning_content_verification_off_does_not_weaken_the_register_run(
    tmp_path, stub, monkeypatch
):
    nb, step = segmented_app(tmp_path)
    assert step.workflow.inputs.manifest.settings.verify_content
    registration = answered(nb, step)
    nb.verify_content.value = False
    before = _manifest_bytes(nb)
    assert not registration.register() and not registration.registered
    assert _manifest_bytes(nb) == before
    assert _refused_as_not_segmented(nb), nb.run_issues.value


def test_an_unchanged_strict_press_still_reads_each_image_whole_twice(tmp_path, stub):
    # The control: the binding adds no read (test_mask_register_progress pins the counts).
    nb, step = segmented_app(tmp_path)
    registration = answered(nb, step)
    assert registration.register(), registration.status.value


# B3 --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("how", ["refused", "raised", "disabled"])
def test_register_is_not_complete_when_describe_the_masks_fails(tmp_path, stub, monkeypatch, how):
    from morphobricks_preprocessing.run import mask_step

    nb, step = segmented_app(tmp_path)
    registration = answered(nb, step)
    failing = [True]
    prepare, describe, refresh = (
        mask_step.prepare_mask_step,
        mask_step.describe_masks,
        nb._refresh_mask_step,
    )

    def refused(*args, **kwargs):
        found = prepare(*args, **kwargs)
        if failing[0] and how == "refused":
            return dataclasses.replace(found, preflight=None)  # no masks found: not ready
        return found

    def broken(*args, **kwargs):
        if failing[0] and how == "raised":
            raise RuntimeError("the masks could not be read")
        return describe(*args, **kwargs)

    def disabled(**kwargs):
        refresh(**kwargs)
        if failing[0] and how == "disabled":
            nb.mask_step_button.disabled = True

    monkeypatch.setattr(mask_step, "prepare_mask_step", refused)
    monkeypatch.setattr(mask_step, "describe_masks", broken)
    monkeypatch.setattr(nb, "_refresh_mask_step", disabled)
    assert not registration.register()
    assert not registration.registered
    assert "The masks are registered" not in registration.status.value
    assert DESCRIBE_FAILED.split(":")[0] in registration.status.value
    failing[0] = False  # the retry: the describing step works again
    assert registration.register(), registration.status.value
    assert registration.registered
    assert "The masks are registered" in registration.status.value


# our review: an OSError while the masks' headers are read -------------------------------------


def test_an_unreadable_mask_header_is_a_failed_check_not_a_stop(tmp_path, stub, monkeypatch):
    from morphobricks_preprocessing import notebook_masks

    nb, step = segmented_app(tmp_path)
    registration = answered(nb, step)

    def unreadable(*args, **kwargs):
        raise OSError(5, "Input/output error")

    monkeypatch.setattr(notebook_masks, "remasked", unreadable)
    assert not registration.register()
    assert CHECK_FAILED.split(":")[0] in registration.status.value
    assert "Input/output error" in registration.status.value
    assert STOPPED.split("(")[0] not in registration.status.value
    assert nb.masks_wait.value == ""  # "Reading mask headers" does not stay
    assert not registration.registered
