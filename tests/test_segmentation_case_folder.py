"""A mask type differing only in case from an entry of the masks folder is refused (#459).

A mask type is its exact name. The masks folder holds ``cells/`` and the user asks for
``Cells``: on Windows and macOS the two are one folder (masks and records of two types mixed),
on Linux a second folder is made beside the first. Every path that makes or writes a mask
type's folder refuses it the same way on every file system, naming both; the exact name stays
allowed (the resume and skip path). Nothing is renamed or merged.

The case-insensitive file system is simulated by an existing ``cells/`` and a request for
``Cells``: on a case-sensitive file system the refusal cannot come from the file system.
"""

import pytest

from cellpose_stub import install
from morphobricks_preprocessing import notebook_cellpose
from morphobricks_preprocessing import segmentation_cellpose as cp
from morphobricks_preprocessing.segmentation_handoff import images_to_segment, write_mask
from morphobricks_preprocessing.segmentation_record import (
    SegmentationError,
    check_mask_type_folder,
    write_record,
)
from test_notebook_cellpose import CHANNELS, OPEN, run_cell
from test_segmentation_cellpose import make_job
from test_segmentation_handoff import labels_like
from test_segmentation_record import batch

CODE = "mask_type_differs_in_case"


def names(folder):
    """Return the names in ``folder`` as listed from disk (never a case-insensitive lookup)."""
    return sorted(entry.name for entry in folder.iterdir())


@pytest.fixture
def run(mixed_run):
    (mixed_run.masks / "cells").mkdir(parents=True)  # an existing type, empty or not
    return mixed_run


def refused_naming_both(caught):
    text = str(caught.value)
    assert CODE in text and '"cells"' in text and '"Cells"' in text
    assert "exact name" in text and "another mask type" in text


# the rule ---------------------------------------------------------------------------------


@pytest.mark.parametrize("existing", ["cells", "CELLS", "cElls"])
def test_a_name_differing_only_in_case_is_refused_naming_both(tmp_path, existing):
    (tmp_path / existing).mkdir()
    with pytest.raises(SegmentationError, match=CODE) as caught:
        check_mask_type_folder(tmp_path, "Cells")
    assert f'"{existing}"' in str(caught.value) and '"Cells"' in str(caught.value)
    assert names(tmp_path) == [existing]  # nothing renamed, merged or made


def test_the_exact_name_and_other_names_are_allowed(tmp_path):
    for name in ("Cells", "Nuclei", "Cells2", ".segmentation_record.lock"):
        (tmp_path / name).mkdir()
    check_mask_type_folder(tmp_path, "Cells")  # the type's own folder: resume and skip
    check_mask_type_folder(tmp_path, "Nuclei")
    check_mask_type_folder(tmp_path / "missing", "Cells")  # no masks folder yet: nothing in it


def test_a_file_differing_only_in_case_is_refused_too(tmp_path):
    (tmp_path / "cells").write_bytes(b"")  # one name with Cells where case is ignored
    with pytest.raises(SegmentationError, match=CODE):
        check_mask_type_folder(tmp_path, "Cells")


def test_both_spellings_present_are_refused_even_for_the_exact_one(tmp_path):
    (tmp_path / "Cells").mkdir()
    (tmp_path / "cells").mkdir()  # only possible where case counts (Linux)
    with pytest.raises(SegmentationError, match=CODE):
        check_mask_type_folder(tmp_path, "Cells")


# the direct API: images_to_segment, write_mask, write_record, run_batch -----------------------


def test_images_to_segment_refuses_beside_an_empty_folder_of_another_case(run):
    with pytest.raises(SegmentationError, match=CODE) as caught:
        images_to_segment(run.inputs, "Cells", run.masks)
    refused_naming_both(caught)
    assert images_to_segment(run.inputs, "cells", run.masks) == run.inputs.fovs()


def test_write_mask_refuses_and_makes_no_second_folder(run):
    first = run.inputs.fovs()[0]
    with pytest.raises(SegmentationError, match=CODE) as caught:
        write_mask(run.masks, "Cells", first, labels_like(run, first), run.inputs)
    refused_naming_both(caught)
    assert names(run.masks) == ["cells"] and names(run.masks / "cells") == []
    written = write_mask(run.masks, "cells", first, labels_like(run, first), run.inputs)
    assert written.relative_to(run.masks).parts[0] == "cells"  # the exact name: allowed


def test_write_record_refuses_and_makes_no_second_folder(tmp_path):
    (tmp_path / "cells").mkdir()
    with pytest.raises(SegmentationError, match=CODE):
        write_record(tmp_path, "Cells", batch())
    assert names(tmp_path) == ["cells"] and names(tmp_path / "cells") == []


def test_run_batch_refuses_before_any_image(run, monkeypatch, tmp_path):
    stub = install(monkeypatch, tmp_path)
    job = make_job(run)  # mask type Cells
    with pytest.raises(SegmentationError, match=CODE):
        cp.run_batch(job)
    assert stub.calls == [] and names(run.masks) == ["cells"]


# the Cellpose notebook and the combined notebook's cell 4 (one component) -------------------


@pytest.fixture
def ns(run, monkeypatch, tmp_path):
    install(monkeypatch, tmp_path)
    namespace = {}
    run_cell(namespace, OPEN, run_folder=str(run.out), masks_folder=str(run.masks))
    return namespace


def test_the_notebook_refuses_the_mask_type_when_it_is_chosen(ns, run):
    with pytest.raises(notebook_cellpose.CellStop, match=CODE):
        run_cell(ns, CHANNELS, output="cells", mask_type="Cells")
    assert ns["workflow"].picker is None and ns["workflow"].mask_types == {}
    assert names(run.masks) == ["cells"]
    run_cell(ns, CHANNELS, output="cells", mask_type="cells")  # the existing folder's name
    assert ns["workflow"].mask_types == {"cells": "cells"}


def test_the_notebook_refuses_the_nuclei_mask_type_of_both(ns, run):
    (run.masks / "NUCLEI").mkdir()
    with pytest.raises(notebook_cellpose.CellStop, match=CODE) as caught:
        run_cell(ns, CHANNELS, output="both", mask_type="cells", nuclei_mask_type="Nuclei")
    assert '"NUCLEI"' in str(caught.value) and '"Nuclei"' in str(caught.value)
    assert ns["workflow"].picker is None
