"""Workers 1, 2, 4 and ``auto`` write the same bytes in every file (audit P2 of v0.6.0).

On the Spec fixtures ``v05`` (units with different channels) and ``v09`` (masks), every file a
run leaves is compared as raw bytes, the reports and the manifest included, under a fixed clock
(:mod:`worker_bytes`): ``written_at`` is the only field excluded from byte identity (owner
decision of 16 November 2026), and a moved clock changes nothing else.
"""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

from description_tree import MAPPING, build_experiment, write_mask
from morphobricks_preprocessing import prefill_mapping
from morphobricks_preprocessing.description import description_from_bytes
from spec_pin import fixtures
from tiff_tree import write_data
from worker_bytes import WRITTEN_AT, expected_workers, fixed_clock, fresh_run

__all__ = ["fixed_clock"]  # the autouse fixture, imported to apply to this module

TYX = prefill_mapping("TYX")


VALID = {item.name.split("_")[0]: item for item in fixtures("valid")}


def _spec_experiment(tmp_path, name):
    """The ``name`` Spec fixture's experiment (three images per unit) and its masks, if any."""
    data = VALID[name].data
    root, out = tmp_path / "exp", tmp_path / "out"
    found = build_experiment(root, description_from_bytes(data), 3)
    masks = None
    if name == "v09":
        masks = tmp_path / "masks"
        for index, image in enumerate(found):
            write_mask(masks, "nuclei", image, [0, 1, 2 + index])
    out.mkdir()
    (out / "morphobricks.toml").write_bytes(data)
    return root, out, masks, found


@pytest.mark.parametrize("name", ["v05", "v09"])
def test_workers_1_2_4_and_auto_write_the_same_bytes_on_the_spec_fixtures(tmp_path, name):
    """v05 (units with different channels) and v09 (masks): every file, byte for byte."""
    root, out, masks, found = _spec_experiment(tmp_path, name)
    serial, expected = fresh_run(root, out, 1, MAPPING, masks_folder=masks)
    manifest = json.loads(expected["run_manifest.json"])
    assert manifest["written_at"] == WRITTEN_AT
    assert bool(manifest.get("masks")) == (name == "v09")
    assert {"plane_histograms.csv", "run_manifest.json", "discovery_report.json"} <= set(expected)
    for workers in (2, 4, "auto"):
        result, files = fresh_run(root, out, workers, MAPPING, masks_folder=masks)
        assert files == expected, sorted(k for k in expected if files.get(k) != expected[k])
        assert result.read == serial.read == tuple(found)
        assert result.workers == expected_workers(workers, len(found))


def test_written_at_is_the_only_field_outside_byte_identity(tmp_path, fixed_clock):
    """Another clock changes ``written_at`` only: the manifest there, the reports in its SHA-256."""
    experiment, out = tmp_path / "exp", tmp_path / "out"
    for seed, rel in enumerate(("ctrl/r1/a.tif", "ctrl/r1/b.tif", "drug/r1/a.tif")):
        pixels = np.random.default_rng(seed).integers(0, 255, (2, 8, 8), dtype=np.uint8)
        write_data(experiment / rel, pixels, "TYX")
    _, first = fresh_run(experiment, out, 1, TYX, keep=())
    fixed_clock.at = datetime(2027, 1, 2, 3, 4, 5, tzinfo=UTC)
    _, later = fresh_run(experiment, out, 4, TYX, keep=())
    assert later["run_manifest.json"] != first["run_manifest.json"]
    moved = later["run_manifest.json"].replace(
        b'"written_at": "2027-01-02T03:04:05+00:00"', f'"written_at": "{WRITTEN_AT}"'.encode()
    )
    assert moved == first["run_manifest.json"]
    # The reports name the manifest's SHA-256, which follows from its bytes; nothing else moves.
    old, new = (hashlib.sha256(f["run_manifest.json"]).hexdigest().encode() for f in (first, later))
    del first["run_manifest.json"], later["run_manifest.json"]
    assert {name: content.replace(new, old) for name, content in later.items()} == first


def test_the_contract_declares_written_at_the_only_volatile_field():
    """The owner's rule of 16 November 2026 is written where the manifest is described."""
    docs = Path(__file__).resolve().parents[1] / "docs"
    for name in ("contract.md", "user_guide.md"):
        text = " ".join((docs / name).read_text(encoding="utf-8").split())
        assert "`written_at` is the only field excluded from byte identity" in text, name
