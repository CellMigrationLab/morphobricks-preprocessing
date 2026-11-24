"""Issue #480: the mask scan refuses masks whose headers are not bound to a fingerprint.

A mask's header (axes, planes) is evidence about one version of the file. Before #480 the
public :func:`scan_masks` accepted an inventory found without ``bind=True``: its masks had
no fingerprint to check, so a mask rewritten after its header was read (a ``TYX`` stack
replaced by a ``ZYX`` stack of the same shape) was scanned with the old header's planes,
and every time point read the first depth slice of the new file. Now a mask without a bound
header is refused (``mask_header_unbound``) before any mask is read, and no report is made.
"""

import numpy as np
import pytest

from morphobricks_preprocessing import mask_scan, preflight, scan_masks
from morphobricks_preprocessing.mask_binding import (
    MASK_HEADER_UNBOUND,
    MaskChangedError,
    MaskUnboundError,
    scan_fingerprints,
)
from tiff_tree import write_data

SHAPE = (3, 16, 16)
MASK = "Cells/ctrl/a.tif"


def stack(values):
    data = np.zeros(SHAPE, dtype=np.uint16)
    for plane, value in enumerate(values):
        data[plane, 2:6, 2:6] = value
    return data


def experiment(tmp_path):
    """One TYX image with a TYX mask whose three time points hold labels 11, 22 and 33."""
    root, folder = tmp_path / "exp", tmp_path / "masks"
    write_data(root / "ctrl" / "a.tif", np.zeros(SHAPE, dtype=np.uint8), "TYX")
    write_data(folder / MASK, stack((11, 22, 33)), "TYX")
    return root, folder


def rewrite_as_depth(folder):
    """Replace the mask by a ZYX stack of the same shape holding other labels."""
    write_data(folder / MASK, stack((111, 222, 333)), "ZYX")


def counting_reads(monkeypatch):
    reads = []
    read = mask_scan.read_mask

    def counted(mask, *args, **kwargs):
        pixels = read(mask, *args, **kwargs)
        reads.append(sorted(int(v) for v in np.unique(pixels) if v))
        return pixels

    monkeypatch.setattr(mask_scan, "read_mask", counted)
    return reads


def test_an_unbound_header_rewritten_from_time_to_depth_is_never_scanned(tmp_path, monkeypatch):
    root, folder = experiment(tmp_path)
    inventory = preflight(root, masks_folder=folder).masks
    assert inventory.ok, inventory.issues
    (mask,) = inventory.files
    assert mask.axes == "TYX" and mask.bound is None
    rewrite_as_depth(folder)
    reads = counting_reads(monkeypatch)
    with pytest.raises(MaskUnboundError) as raised:
        scan_masks(inventory, root)
    # Before #480 the three time points were read as the new file's first depth slice.
    assert reads == []
    assert isinstance(raised.value, ValueError)
    assert raised.value.paths == [MASK]
    issue = raised.value.issue
    assert issue.code == MASK_HEADER_UNBOUND == "mask_header_unbound" and issue.is_error
    assert issue.paths == (MASK,) and "No mask was read" in issue.message
    assert "bind=True" in issue.action and "bind_headers=True" in issue.action


def test_an_unchanged_unbound_inventory_is_refused_before_any_read(tmp_path, monkeypatch):
    root, folder = experiment(tmp_path)
    inventory = preflight(root, masks_folder=folder).masks
    reads = counting_reads(monkeypatch)
    calls = []
    with pytest.raises(MaskUnboundError):
        scan_masks(inventory, root, progress=lambda *a: calls.append(a))
    assert reads == [] and calls == []


def test_a_bound_header_scans_its_own_planes(tmp_path, monkeypatch):
    root, folder = experiment(tmp_path)
    inventory = preflight(root, masks_folder=folder, bind_headers=True).masks
    assert inventory.ok, inventory.issues
    assert inventory.files[0].bound is not None
    reads = counting_reads(monkeypatch)
    report = scan_masks(inventory, root)
    assert reads == [[11], [22], [33]]
    assert report.types["Cells"].objects == 3


@pytest.mark.parametrize("verify_content", [False, True])
def test_a_bound_header_rewritten_from_time_to_depth_is_refused(
    tmp_path, monkeypatch, verify_content
):
    root, folder = experiment(tmp_path)
    inventory = preflight(root, masks_folder=folder, bind_headers=True).masks
    rewrite_as_depth(folder)
    reads = counting_reads(monkeypatch)
    with pytest.raises(MaskChangedError) as raised:
        scan_masks(inventory, root, verify_content=verify_content)
    assert raised.value.paths == [MASK] and reads == []


def test_the_fingerprints_before_a_read_refuse_an_unbound_mask(tmp_path):
    # The guard every reader between two fingerprints shares (scan, binary values check).
    root, folder = experiment(tmp_path)
    (mask,) = preflight(root, masks_folder=folder).masks.files
    with pytest.raises(MaskUnboundError):
        scan_fingerprints([mask], folder, root.resolve(), False)
