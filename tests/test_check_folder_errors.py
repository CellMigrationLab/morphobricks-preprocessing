"""A failed Check folder clears the earlier check and says what to do (issue #101)."""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")

from described_tree import describe_tree  # noqa: E402
from morphobricks_preprocessing import notebook_folder  # noqa: E402
from morphobricks_preprocessing import notebook_style as ui  # noqa: E402
from morphobricks_preprocessing.notebook_ui import PreprocessingNotebook  # noqa: E402
from morphobricks_preprocessing.run_inputs_error import MasksFolderUnreadableError  # noqa: E402
from tiff_tree import write_data  # noqa: E402


@pytest.fixture
def experiment(tmp_path):
    """Two conditions of one repeat, two-channel time-lapse images."""
    rng = np.random.default_rng(1)
    for rel in ("ctrl/rep1/a.tif", "drug/rep1/a.tif"):
        data = rng.integers(0, 200, (2, 2, 12, 12), dtype=np.uint8)
        write_data(tmp_path / "exp" / rel, data, axes="TCYX")
    return tmp_path / "exp"


def ready(experiment, output):
    """Check the described folder, confirm the labels and the axis roles: ready to run."""
    describe_tree(experiment, str(output))
    nb = PreprocessingNotebook(str(experiment), output_folder=str(output))
    for step in (nb.welcome, nb.folder_step, nb.axis_step, nb.run_step, nb.results_step):
        step()
    nb.check_button.click()
    nb.labels_confirmed.set(True)
    nb.preview_button.click()
    nb.preview_checked.button.click()
    nb.confirm_button.click()
    assert nb.preflight is not None and nb.confirmed_mapping is not None
    assert not nb.run_button.disabled
    return nb


def failing_preflight(exc):
    """Return a ``preflight`` that reports the first phase, then raises ``exc`` (mid-check)."""

    def fail(*args, progress=None, **kwargs):
        progress("folders", 0, 0, "")
        raise exc

    return fail


def files_under(folder):
    return sorted(p.relative_to(folder) for p in folder.rglob("*") if p.is_file())


def assert_cleared(nb):
    """Nothing of the earlier check is shown as valid, and nothing can be run."""
    assert nb.preflight is None and nb.mapping is None and nb.confirmed_mapping is None
    assert nb.result is None and nb.pairing is None
    assert not nb.labels_confirmed.value
    assert nb.folder_tiles.value == "" and nb.folder_next.value == ""
    for card in (nb.labels_card, nb.consistency_card, nb.issues_card, nb.calibration_card):
        assert not ui.is_visible(card)
    assert nb.run_button.disabled
    assert not nb.check_button.disabled
    assert nb.check_button.description == " Check folder"
    # The bins recommended for the earlier check's pixel type are not shown as valid.
    assert nb._default_bins is None and nb.bins_hint.value == ""
    assert "bins per plane" not in nb.recommended_hint.value


@pytest.mark.parametrize(
    "exc",
    [
        OSError(5, "Input/output error"),
        TimeoutError("Transport endpoint is not connected"),
        MasksFolderUnreadableError("the masks folder cannot be listed"),
    ],
    ids=["oserror", "timeout", "coded"],
)
def test_a_failed_recheck_clears_the_checked_state_and_can_be_retried(
    experiment, tmp_path, monkeypatch, exc
):
    output = tmp_path / "out"
    nb = ready(experiment, output)
    assert nb._default_bins == 256 and "256 bins per plane" in nb.recommended_hint.value
    before = files_under(output)
    real = notebook_folder.preflight
    monkeypatch.setattr(notebook_folder, "preflight", failing_preflight(exc))
    assert nb.check_folder() is None  # the same folder, checked again: no traceback
    assert_cleared(nb)
    status = nb.folder_status.value
    assert "The folder could not be checked" in status
    assert type(exc).__name__ in status
    assert str(experiment) in status
    assert "Google Drive is mounted" in status and "Check folder again" in status
    assert "Traceback" in "".join(nb.log.lines)  # the details are in Technical details
    assert files_under(output) == before  # nothing written, nothing run
    assert nb.run() is None and nb.result is None
    monkeypatch.setattr(notebook_folder, "preflight", real)
    nb.check_button.click()  # the retry works
    assert nb.preflight is not None and nb.preflight.ok
    assert "could not be checked" not in nb.folder_status.value
    assert ui.is_visible(nb.labels_card)


def test_a_programming_error_is_raised_after_clearing_the_checked_state(
    experiment, tmp_path, monkeypatch
):
    nb = ready(experiment, tmp_path / "out")
    monkeypatch.setattr(notebook_folder, "preflight", failing_preflight(KeyError("bug")))
    with pytest.raises(KeyError, match="bug"):
        nb.check_folder()
    assert_cleared(nb)
    assert "KeyError" in nb.folder_status.value


@pytest.mark.parametrize(
    "exc",
    [SystemExit("x"), SystemExit(2), GeneratorExit()],
    ids=["exit-str", "exit-int", "generator-exit"],
)
def test_an_exit_is_cleared_then_raised_not_swallowed(experiment, tmp_path, monkeypatch, exc):
    """``SystemExit("msg")`` has a str ``code`` but is not a coded refusal: it propagates."""
    nb = ready(experiment, tmp_path / "out")
    monkeypatch.setattr(notebook_folder, "preflight", failing_preflight(exc))
    with pytest.raises(type(exc)):
        nb.check_folder()
    assert_cleared(nb)


def test_a_clearing_error_does_not_hide_a_programming_error(experiment, tmp_path, monkeypatch):
    nb = ready(experiment, tmp_path / "out")
    monkeypatch.setattr(notebook_folder, "preflight", failing_preflight(KeyError("bug")))

    def broken(self, status):
        raise RuntimeError("clearing failed")

    monkeypatch.setattr(type(nb), "_clear_checked", broken)
    with pytest.raises(KeyError, match="bug"):  # the original, not the clearing error
        nb.check_folder()


def test_a_clearing_error_after_an_expected_failure_is_raised_from_it(
    experiment, tmp_path, monkeypatch
):
    nb = ready(experiment, tmp_path / "out")
    original = OSError(5, "I/O")
    monkeypatch.setattr(notebook_folder, "preflight", failing_preflight(original))

    def broken(self, status):
        raise RuntimeError("clearing failed")

    monkeypatch.setattr(type(nb), "_clear_checked", broken)
    with pytest.raises(RuntimeError, match="clearing failed") as info:
        nb.check_folder()
    assert info.value.__cause__ is original
