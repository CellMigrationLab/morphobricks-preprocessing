"""``binary_refusals`` refuses an inventory with unbound headers before any read (#490, #480).

``binary_refusals`` reads the pixels of every file of a ``binary`` mask. A file whose header is
not bound to a fingerprint (found without ``bind=True``) has nothing tying its header to the
file read now. Before, each file was refused only when its turn came (``scan_fingerprints``),
so the bound files before it were read first, the error named one file, and an unbound file
of a mask that is not ``binary`` passed. Now the whole inventory is checked up front: every
unbound file is named and nothing is read.
"""

from __future__ import annotations

import dataclasses
import inspect

import pytest

import morphobricks_preprocessing.mask_representation as representation
from morphobricks_preprocessing.description import description_from_bytes
from morphobricks_preprocessing.mask_binding import MASK_HEADER_UNBOUND, MaskUnboundError
from morphobricks_preprocessing.mask_representation import binary_refusals
from morphobricks_preprocessing.run.mask_step import prepare_mask_step
from test_spec_binary_masks import APART, VALID, _v08

TUMOR = "tumor_region"
NUCLEI = "nuclei"


@pytest.fixture
def made(tmp_path):
    """A ``v08`` run of two images (``binary`` TumorRegion, ``instances`` Nuclei), bound."""
    out, _, _, _ = _v08(tmp_path, APART, images=2, mask_step=False)
    step = prepare_mask_step(None, out)
    inventory = step.masks
    assert all(found.bound is not None for found in inventory.files)
    types = sorted(found.mask_type for found in inventory.files)
    assert types == [NUCLEI, NUCLEI, TUMOR, TUMOR]
    description = description_from_bytes(VALID["v08"].data)
    return description, inventory, step.preflight.discovery.root


def _unbind(inventory, which):
    files = tuple(
        dataclasses.replace(found, bound=None) if which(found) else found
        for found in inventory.files
    )
    return dataclasses.replace(inventory, files=files)


def _reads(monkeypatch):
    reads = []
    distinct = representation._distinct

    def counted(mask, *args, **kwargs):
        reads.append(mask.path)
        return distinct(mask, *args, **kwargs)

    monkeypatch.setattr(representation, "_distinct", counted)
    return reads


def test_a_bound_inventory_is_read_and_passes(made, monkeypatch):
    description, inventory, root = made
    reads = _reads(monkeypatch)
    assert binary_refusals(description, inventory, root) == {}
    assert sorted(reads) == sorted(f.path for f in inventory.files if f.mask_type == TUMOR)


def _last_tumor(inventory):
    return max(f.path for f in inventory.files if f.mask_type == TUMOR)


@pytest.mark.parametrize(
    "case",
    ["last binary file", "every binary file", "an instances file only", "every file"],
)
def test_unbound_files_are_refused_up_front_naming_every_one(made, monkeypatch, case):
    description, inventory, root = made
    last = _last_tumor(inventory)
    which = {
        "last binary file": lambda f: f.path == last,
        "every binary file": lambda f: f.mask_type == TUMOR,
        "an instances file only": lambda f: (
            f.mask_type == NUCLEI and f.image == min(g.image for g in inventory.files)
        ),
        "every file": lambda f: True,
    }[case]
    unbound = _unbind(inventory, which)
    expected = sorted(f.path for f in unbound.files if f.bound is None)
    reads = _reads(monkeypatch)
    with pytest.raises(MaskUnboundError) as refused:
        binary_refusals(description, unbound, root)
    assert reads == []  # no file is read, not even the bound ones before the unbound one
    assert refused.value.paths == expected
    assert refused.value.issue.code == MASK_HEADER_UNBOUND
    assert isinstance(refused.value, ValueError)


def test_the_docstring_names_the_refusal():
    raises = inspect.getdoc(binary_refusals).split("Raises", 1)[1]
    assert "MaskUnboundError" in raises
