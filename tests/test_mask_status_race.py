"""The mask step records exactly the fingerprint it verified (v0.6.1, audit P1 of v0.6.0).

``check_representation`` verified each file against the fingerprint bound to its checked header
(``scan_fingerprints``), then discarded that answer and took a fresh fingerprint to record. A
file replaced between the two was recorded as checked (by BM11, for a ``binary`` mask) with the
replacement's fingerprint, so once a run recorded the replacement, the resolver read a file the
mask step never read as ``checked``. The recorded fingerprint is now the verified one, which is
also the one ``binary_refusals`` read the values between: a replacement is ``not yet checked``
until the mask step reads it.
"""

from __future__ import annotations

import os

import pytest
import tifffile

import morphobricks_preprocessing.mask_representation as representation
import morphobricks_preprocessing.run.mask_step as mask_step_module
from described_tree import run_described
from description_tree import MAPPING
from morphobricks_preprocessing.fingerprint import Fingerprint
from morphobricks_preprocessing.mask_representation import read_checked
from morphobricks_preprocessing.mask_rules import refuse_binary_values
from morphobricks_preprocessing.run.mask_step import describe_masks, prepare_mask_step
from morphobricks_preprocessing.run_inputs_request import unchecked_refusal
from test_spec_binary_masks import APART, BAD, _blobs, _objects, _resolve, _status, _v08

REPLACEMENTS = {
    # A file BM11 refuses ({3, 7}): recorded as checked, it would be read as a binary mask.
    "contradicting": (_blobs((1, 3, 1, 3), value=3) + _blobs((5, 7, 5, 7), value=7), [3, 7]),
    # A valid binary file with other pixels: still never read by the mask step that recorded it.
    "valid": (_blobs((2, 4, 2, 4), (5, 7, 1, 3)), None),
}


def _recorded(out, path):
    """The fingerprint the status file records for ``path``, or None."""
    for entry in read_checked(out)["TumorRegion"]["files"]:
        if entry["path"] == path:
            return {key: value for key, value in entry.items() if key != "path"}
    return None


def _replace(location, pixels):
    """Replace the mask file with other pixels and a later modification time."""
    before = location.stat().st_mtime_ns
    tifffile.imwrite(location, pixels, photometric="minisblack", metadata={"axes": "YX"})
    os.utime(location, ns=(before + 5_000_000_000, before + 5_000_000_000))


def _mask_step_with_a_replacement(monkeypatch, out, location, pixels):
    """Run the mask step; replace the file right after the status writer verified it."""
    verify = representation.scan_fingerprints
    write = mask_step_module.check_representation
    armed, replaced, taken = [False], [], []

    def verified_then_replaced(masks, *args):
        masks = list(masks)
        found = verify(masks, *args)
        if masks[0].mask_type == "tumor_region":
            taken.append(found[masks[0].path][0].to_data())
        if armed[0] and not replaced and masks[0].mask_type == "tumor_region":
            _replace(location, pixels)  # between the verification and the record
            replaced.append(masks[0].path)
        return found

    def writer(*args, **kwargs):
        armed[0] = True  # only the status writer's verification, not binary_refusals'
        return write(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(representation, "scan_fingerprints", verified_then_replaced)
        patch.setattr(mask_step_module, "check_representation", writer)
        step = describe_masks(prepare_mask_step(None, out))
    assert replaced, "the replacement was not injected"
    return step, taken


@pytest.mark.parametrize("case", sorted(REPLACEMENTS))
def test_a_mask_replaced_after_its_check_is_never_recorded_as_checked(tmp_path, monkeypatch, case):
    pixels, refused_values = REPLACEMENTS[case]
    out, masks, (image,), _ = _v08(tmp_path, APART, mask_step=False)
    path = f"tumor_region/{image}"
    location = masks / path
    checked = Fingerprint.of(location).to_data()  # the file the mask step reads and checks

    step, taken = _mask_step_with_a_replacement(monkeypatch, out, location, pixels)
    assert step.issues == ()  # the original file passed BM11
    replacement = Fingerprint.of(location).to_data()
    assert replacement != checked
    # The status records the file it checked, never the replacement: the snapshot
    # binary_refusals read the values between, which the status writer verified again.
    values_read_at, verified_at = taken
    assert _recorded(out, path) == values_read_at == verified_at == checked

    # A run records the replacement; the resolver refuses it: never read, so not checked.
    assert run_described(tmp_path / "exp", MAPPING, output_folder=out, masks_folder=masks).written
    assert _status(out, path) == "checked"  # the entry stays, for the old fingerprint only
    _, resolved = _resolve(out, _objects())
    expected = unchecked_refusal("TumorRegion", [path], 1)
    assert [(r.code, r.subject, r.message, r.files) for r in resolved.refusals] == [
        ("A_MASK_UNCHECKED", "TumorRegion", expected.message, (path,))
    ]

    # The mask step reads the replacement: refused by BM11, or checked with its own fingerprint.
    again = describe_masks(prepare_mask_step(None, out))
    if refused_values is None:
        assert again.issues == ()
        assert _recorded(out, path) == replacement
        _, resolved = _resolve(out, _objects())
        assert resolved.refusals == ()
    else:
        (issue,) = again.issues
        assert (issue.code, issue.message) == (
            BAD,
            str(refuse_binary_values(path, values=refused_values)),
        )
        assert _recorded(out, path) is None and _status(out, path) == "not yet checked"
        _, resolved = _resolve(out, _objects())
        assert [r.code for r in resolved.refusals] == ["A_MASK_UNCHECKED"]


def test_a_mask_without_a_bound_header_is_never_recorded(tmp_path):
    """An inventory found without binding has no checked header to record against."""
    from dataclasses import replace

    from morphobricks_preprocessing.run.mask_step import _published

    out, _, _, _ = _v08(tmp_path, APART, mask_step=False)
    step = prepare_mask_step(None, out)
    unbound = replace(step.masks, files=tuple(replace(f, bound=None) for f in step.masks.files))
    described, _ = _published(out)
    with pytest.raises(ValueError, match="bound"):
        representation.check_representation(
            described.description, unbound, step.preflight.discovery.root, out
        )
    assert not (out / representation.REPRESENTATION_FILENAME).exists()
