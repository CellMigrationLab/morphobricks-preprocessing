"""A ``binary`` check recorded before Spec ADR 0005 BM11 is refused until the mask step runs again.

Owner decision of 16 November 2026 (Spec ADR 0002 AR10: an old form is refused with its fix
named). The v0.5.6 mask step allowed "at most two distinct values over the file", so a file
holding {3} at one time point and {7} at another was recorded ``checked``, and the per-plane read
guard ``label_binary`` passes it too. The mask step now writes ``"value_rule": "BM11"`` in each
``binary`` record of ``mask_representation.json``; a ``binary`` record without it is
``A_MASK_UNCHECKED`` before any read, with a message naming the fix. ``instances`` records are
unchanged.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

import morphobricks_preprocessing.run.mask_step as mask_step_module
import morphobricks_preprocessing.run_inputs_mask as mask_reads
from morphobricks_preprocessing import MaskUncheckedError, RunInputs
from morphobricks_preprocessing.mask_representation import (
    BINARY_VALUE_RULE,
    REPRESENTATION_FILENAME,
    VALUE_RULE,
    read_checked,
)
from morphobricks_preprocessing.mask_rules import refuse_binary_values, stale_check_message
from morphobricks_preprocessing.run.mask_step import describe_masks, prepare_mask_step
from morphobricks_preprocessing.run_inputs_request import unchecked_refusal
from test_spec_binary_masks import APART, BAD, NUCLEI, _blobs, _objects, _resolve, _status, _v08

SPLIT = np.stack([np.full((8, 8), 3, np.uint8), np.full((8, 8), 7, np.uint8)])
"""{3} at T = 0 and {7} at T = 1: v0.5.6's rule (at most two distinct values over the file) and
``label_binary`` (one plane) both pass it; BM11 refuses it ({3, 7} over the whole file)."""


def _old_check(monkeypatch, tmp_path, pixels, *, t=1, rule=None):
    """A ``v08`` run whose mask-step record is v0.5.6's: every binary file passed, no rule."""
    with monkeypatch.context() as patch:
        patch.setattr(mask_step_module, "binary_refusals", lambda *a, **k: {})
        out, masks, found, step = _v08(tmp_path, pixels, t=t)
    assert step.issues == ()
    path = out / REPRESENTATION_FILENAME
    document = json.loads(path.read_text(encoding="utf-8"))
    for record in document["masks"].values():
        record.pop(VALUE_RULE, None)
        if rule is not None and record["representation"] == "binary":
            record[VALUE_RULE] = rule
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return out, masks, found


def _assert_stale(out, path, monkeypatch):
    """The resolver and every read refuse the file, before any read, naming the fix."""
    expected = unchecked_refusal("TumorRegion", [path], 1, stale=True)
    with monkeypatch.context() as patch:
        patch.setattr(mask_reads, "label_binary", lambda *a, **k: pytest.fail("read"))
        reads = []
        for name in ("read_plane", "read_mask_window"):
            patch.setattr(RunInputs, name, lambda *a, _n=name, **k: reads.append(_n))
        inputs, resolved = _resolve(out, _objects())
        assert [(r.code, r.subject, r.message, r.files) for r in resolved.refusals] == [
            ("A_MASK_UNCHECKED", "TumorRegion", expected.message, (path,))
        ]
        assert "older than the binary value rule (Spec ADR 0005 BM11" in expected.message
        assert expected.message.endswith(
            "Run the masks step again on this output folder (A_MASK_UNCHECKED)."
        )
        with pytest.raises(MaskUncheckedError) as raised:
            resolved.require()
        assert raised.value.code == "A_MASK_UNCHECKED"
        assert str(raised.value) == expected.message
        image = path.split("/", 1)[1]
        assert inputs.mask_check_status("TumorRegion", image) == "not yet checked"
        with pytest.raises(MaskUncheckedError) as raised:
            inputs.read_mask(image, "tumor_region", z=0, t=0)
        assert str(raised.value) == stale_check_message("TumorRegion", f"({path})")
        with pytest.raises(MaskUncheckedError) as raised:
            inputs.binary_to_instances(image, "TumorRegion", output_mask_id="TumorObjects")
        assert str(raised.value) == expected.message
        assert reads == []
        # The instances mask of the same record is unchanged: still checked, still read.
        (selection,) = inputs.resolve_request(_objects(None, "Nuclei")).require()
        np.testing.assert_array_equal(inputs.read_objects(selection, "Nuclei", 0, 0), NUCLEI)
    assert not (out.parent / "masks" / "TumorObjects").exists()


def test_a_binary_check_older_than_bm11_with_values_split_over_planes_is_refused(
    tmp_path, monkeypatch
):
    out, _, (image,) = _old_check(monkeypatch, tmp_path, SPLIT, t=2)
    path = f"tumor_region/{image}"
    assert path in {f["path"] for f in read_checked(out)["TumorRegion"]["files"]}  # old: checked
    _assert_stale(out, path, monkeypatch)
    # The masks step run again applies BM11: the file is refused there, before any run.
    step = describe_masks(prepare_mask_step(None, out))
    (issue,) = step.issues
    assert (issue.code, issue.message) == (BAD, str(refuse_binary_values(path, values=[3, 7])))
    assert _status(out, path) == "not yet checked"
    _, resolved = _resolve(out, _objects())
    assert [r.message for r in resolved.refusals] == [
        unchecked_refusal("TumorRegion", [path], 1).message  # now the ordinary message
    ]


@pytest.mark.parametrize("rule", [None, "BM10"])
def test_a_binary_check_older_than_bm11_passes_once_the_masks_step_runs_again(
    tmp_path, monkeypatch, rule
):
    out, _, (image,) = _old_check(monkeypatch, tmp_path, APART, rule=rule)
    _assert_stale(out, f"tumor_region/{image}", monkeypatch)
    step = describe_masks(prepare_mask_step(None, out))
    assert step.issues == ()
    inputs, resolved = _resolve(out, _objects())
    (selection,) = resolved.require()
    expected = _blobs((1, 3, 1, 3), dtype=np.int32) + _blobs((5, 7, 5, 7), dtype=np.int32, value=2)
    np.testing.assert_array_equal(inputs.read_objects(selection, "TumorRegion", 0, 0), expected)


def test_the_masks_step_names_the_value_rule_in_binary_records_only(tmp_path):
    out, _, (image,), step = _v08(tmp_path, APART)
    assert step.issues == ()
    document = json.loads((out / REPRESENTATION_FILENAME).read_text(encoding="utf-8"))
    assert document["version"] == "1"
    tumor, nuclei = document["masks"]["TumorRegion"], document["masks"]["Nuclei"]
    assert list(tumor) == ["source_type", "representation", VALUE_RULE, "files"]
    assert tumor[VALUE_RULE] == BINARY_VALUE_RULE == "BM11"
    assert list(nuclei) == ["source_type", "representation", "files"]  # unchanged
    assert _status(out, f"tumor_region/{image}") == "checked"
