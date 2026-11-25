"""A committed publication removes only the files a run may remove (#398)."""

import os

import pytest

from morphobricks_preprocessing import MANIFEST_FILENAME
from morphobricks_preprocessing.run.generation import (
    PublicationIncompleteError,
    finish_publication,
)
from morphobricks_preprocessing.run.lock import output_lock
from test_publication import MASK_REPORT, first_generation, staged_copy


def files(folder):
    """Every file under ``folder`` with its bytes, the publication folder included."""
    return {p.relative_to(folder): p.read_bytes() for p in folder.rglob("*") if p.is_file()}


def committed(out, removes):
    leftover = staged_copy(out, removes)
    (leftover / MANIFEST_FILENAME).unlink()  # the manifest moved: committed
    return leftover


@pytest.mark.parametrize(
    "removes",
    [
        ["../outside.txt"],
        ["plane_histograms.csv"],
        [MANIFEST_FILENAME],
        [MASK_REPORT, "../outside.txt"],  # one allowed entry does not excuse another
        ["sub/mask_relationships.json"],
        [7],
        [None],
    ],
)
def test_a_record_removing_any_other_file_is_refused_and_removes_nothing(tmp_path, removes):
    root, out = first_generation(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("not the run's", encoding="utf-8")
    leftover = committed(out, removes)
    before = files(out)
    with pytest.raises(PublicationIncompleteError, match="cannot remove") as caught:
        finish_publication(out)
    assert str(leftover) in str(caught.value)
    assert outside.read_text(encoding="utf-8") == "not the run's"
    assert files(out) == before and (out / MASK_REPORT).exists()
    assert leftover.exists()  # kept for the user to look at, never discarded as uncommitted


def test_an_absolute_path_is_refused(tmp_path):
    root, out = first_generation(tmp_path)
    victim = tmp_path / "victim.txt"
    victim.write_text("x", encoding="utf-8")
    committed(out, [os.fspath(victim)])
    with pytest.raises(PublicationIncompleteError):
        finish_publication(out)
    assert victim.exists()


def test_the_lock_refuses_the_same_record_before_anything_runs(tmp_path):
    root, out = first_generation(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("x", encoding="utf-8")
    committed(out, ["../outside.txt"])
    with pytest.raises(PublicationIncompleteError), output_lock(out):
        pass
    assert outside.exists()


def test_the_mask_report_is_still_removed(tmp_path):
    root, out = first_generation(tmp_path)
    leftover = committed(out, [MASK_REPORT])
    assert finish_publication(out) == "finished"
    assert not leftover.exists() and not (out / MASK_REPORT).exists()
