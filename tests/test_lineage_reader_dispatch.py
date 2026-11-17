"""The lineage reader after the dispatch by ``operation`` (Spec ADR 0005 BM15 "Reading"; F1).

BM12 lines are refused exactly as on ``main`` for a missing key, an extra key (a BM15 key such as
``rule`` included), keys out of order, and a ``root`` other than ``"masks"`` (``"experiment"``
included). New refusals: a key repeated in one object at any depth (``json.loads`` kept the last
one silently), and an operation of neither schema. A BM15 line is refused with BM12's
``connectivity`` or ``class_value``, another ``root``, or a ``rule_sha256`` that is not its rule's
digest. The existing BM12 tests run unchanged in ``test_spec_mask_conversion.py``.
"""

from __future__ import annotations

import copy
import json

import numpy as np
import pytest

from conversion_tree import APART, expected_line, file
from morphobricks_preprocessing.lineage_schema import BM15_LINE_KEYS, rule_sha256
from morphobricks_preprocessing.mask_lineage import LINEAGE_FILENAME, read_lineage


def bm12():
    return expected_line(
        file("TumorRegion", "tumor_region/a.tif", "YX", APART),
        file("TumorObjects", "TumorObjects/a.tif", "YX", APART.astype(np.int32)),
        "binary_to_instances",
    )


def identity(name):
    return {
        "distribution": name,
        "version": "1",
        "verified": True,
        "record_sha256": "0" * 64,
        "vcs_commit": None,
        "reason": None,
    }


def bm15():
    rule = {"id": "he_tissue", "version": 1, "status": "provisional"}
    values = {
        "source": {
            "root": "experiment",
            "image": "CTRL/rep1/a.tif",
            "axes": "CYX",
            "shape": [3, 8, 8],
            "dtype": "|u1",
            "content_sha256": "1" * 64,
        },
        "output": file("HETissue", "HETissue/CTRL/rep1/a.tif", "YX", APART),
        "operation": "he_tissue_mask",
        "rule": rule,
        "rule_sha256": rule_sha256(rule),
        "code": {
            "preprocessing": identity("morphobricks-preprocessing"),
            "normalization": identity("morphobricks-normalization"),
        },
        "reusable": True,
        "numpy_version": "2",
        "scikit_image_version": "0.26",
        "scipy_version": "1",
        "image_type": "rgb",
        "rgb_header_override": {"declared": False, "image": None},
        "pixel_size": None,
        "fractions": {
            "kept": 0.5,
            "nuclei": 0.1,
            "red_cells": 0.0,
            "background": 0.4,
            "od_floored": 0.0,
        },
        "helper": "morphobricks_preprocessing.RunInputs.he_tissue_mask",
        "helper_version": "0.6.1",
    }
    return {key: values[key] for key in BM15_LINE_KEYS}


def read(tmp_path, *texts):
    folder = tmp_path / "Out"
    folder.mkdir(exist_ok=True)
    (folder / LINEAGE_FILENAME).write_text("".join(texts), encoding="utf-8")
    return read_lineage(folder)


def line(record):
    return json.dumps(record) + "\n"


NOT_A_RECORD = "line 1 is not a mask lineage record"


def test_both_schemas_read_side_by_side(tmp_path):
    found = read(tmp_path, line(bm12()), line(bm15()))
    assert [item.record["operation"] for item in found] == ["binary_to_instances", "he_tissue_mask"]


@pytest.mark.parametrize(
    "change",
    [
        lambda r: r.pop("helper_version"),  # a missing key
        lambda r: r.update(rule={"id": "x"}),  # an extra key: a BM15 key
        lambda r: r.update(extra=1),
    ],
)
def test_a_bm12_line_with_other_keys_is_refused_as_on_main(tmp_path, change):
    record = bm12()
    change(record)
    with pytest.raises(ValueError, match=NOT_A_RECORD):
        read(tmp_path, line(record))


def test_a_bm12_line_with_keys_out_of_order_is_refused_as_on_main(tmp_path):
    record = bm12()
    reordered = {"output": record.pop("output"), **record}
    with pytest.raises(ValueError, match=NOT_A_RECORD):
        read(tmp_path, line(reordered))


@pytest.mark.parametrize("side", ["source", "output"])
@pytest.mark.parametrize("root", ["experiment", "elsewhere"])
def test_a_bm12_root_other_than_masks_is_refused_as_on_main(tmp_path, side, root):
    record = bm12()
    record[side]["root"] = root
    with pytest.raises(ValueError, match=f"has the {side} root {root!r}"):
        read(tmp_path, line(record))


def test_an_unknown_operation_is_refused(tmp_path):
    record = bm12()
    record["operation"] = "magic_mask"
    with pytest.raises(ValueError, match=NOT_A_RECORD):
        read(tmp_path, line(record))


@pytest.mark.parametrize("make", [bm12, bm15])
def test_a_key_repeated_in_one_object_is_refused_at_any_depth(tmp_path, make):
    text = line(make())
    repeated = text.rstrip()[:-1] + ', "helper_version": "9.9"}\n'  # the last key, again
    with pytest.raises(ValueError, match="'helper_version' appears twice"):
        read(tmp_path, repeated)
    nested = text.replace('"root": "masks",', '"root": "masks", "root": "masks",', 1)
    with pytest.raises(ValueError, match="'root' appears twice"):
        read(tmp_path, nested)
    assert json.loads(repeated)["helper_version"] == "9.9"  # what json.loads alone would keep


@pytest.mark.parametrize("key", ["connectivity", "class_value"])
def test_a_bm15_line_carrying_a_bm12_key_is_refused(tmp_path, key):
    record = bm15()
    record[key] = None
    with pytest.raises(ValueError, match=NOT_A_RECORD):
        read(tmp_path, line(record))


def test_a_bm12_line_under_a_bm15_operation_is_refused(tmp_path):
    record = bm12()
    record["operation"] = "he_tissue_mask"
    with pytest.raises(ValueError, match=NOT_A_RECORD):
        read(tmp_path, line(record))


def test_a_bm15_source_root_other_than_experiment_and_output_root_are_refused(tmp_path):
    record = bm15()
    record["source"]["root"] = "masks"
    with pytest.raises(ValueError, match="has the source root 'masks'"):
        read(tmp_path, line(record))
    record = bm15()
    record["output"]["root"] = "experiment"
    with pytest.raises(ValueError, match="has the output root 'experiment'"):
        read(tmp_path, line(record))


def test_a_rule_sha256_that_is_not_the_rules_digest_is_refused(tmp_path):
    record = bm15()
    record["rule"]["status"] = "frozen"
    with pytest.raises(ValueError, match="rule_sha256 that is not the digest"):
        read(tmp_path, line(record))


@pytest.mark.parametrize(
    "change",
    [
        lambda r: r["code"].pop("normalization"),
        lambda r: r["code"]["preprocessing"].pop("record_sha256"),
        lambda r: r.update(reusable=False),  # not what the identities say
        lambda r: r["fractions"].pop("od_floored"),
        lambda r: r.update(rgb_header_override={"declared": False}),
    ],
)
def test_a_bm15_line_with_an_incomplete_part_is_refused(tmp_path, change):
    record = copy.deepcopy(bm15())
    change(record)
    with pytest.raises(ValueError, match="line 1 has"):
        read(tmp_path, line(record))
