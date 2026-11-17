"""Workers 1, 2, 4 and ``auto`` write the same bytes in every file (audit P2 of v0.6.0).

On every valid Spec fixture (its tree as :func:`write_described_tree` writes it, with all its
masks), and on ``v05`` (units with different channels) and ``v09`` (masks) built by hand, every
file a run leaves is compared as raw bytes, the reports and the manifest included, under a
fixed clock (:mod:`worker_bytes`): ``written_at`` is the only field excluded from byte identity
(owner decision of 16 November 2026), and a moved clock changes nothing else. The table is also
the normalization brick's writer's bytes on its own rows (the byte-parity test of draft PR
#340, brought over without that PR's staging and table changes).
"""

import hashlib
import json
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest
from morphobricks_normalization.csv_io import read_plane_histogram_table, write_plane_histograms

from described_tree import run_described
from description_tree import MAPPING, build_experiment, write_mask
from morphobricks_preprocessing import prefill_mapping
from morphobricks_preprocessing.description import description_from_bytes
from morphobricks_preprocessing.image_type import RGB
from morphobricks_preprocessing.run import RunMode
from morphobricks_preprocessing.testing_described import described_images, write_described_tree
from spec_pin import fixtures
from tiff_tree import write_data
from worker_bytes import WRITTEN_AT, every_file, expected_workers, fixed_clock, fresh_run

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


def _whole_tree(tmp_path, data):
    """Write a fixture's tree with every declared mask; return the run's arguments."""
    description = description_from_bytes(data)
    rgb = {
        unit.image_type == RGB
        for repeat in description.repeats.values()
        for member in repeat.members.values()
        for unit in member.technical_repeats.values()
    }
    masks = tmp_path / "masks" if description.masks else None
    root, out = tmp_path / "exp", tmp_path / "out"
    if len(rgb) == 1:  # a tree mixing rgb and channels units cannot be written (v29)
        write_described_tree(root, masks, description, tuple(description.masks))
    else:
        root.mkdir()
    out.mkdir()
    (out / "morphobricks.toml").write_bytes(data)
    mapping = prefill_mapping("YXS" if rgb == {True} else "CYX")
    return root, out, masks, mapping, description


def _writers_bytes(table):
    """The normalization brick's writer's bytes on the rows of ``table``."""
    found = read_plane_histogram_table(table)
    again = table.parent / "writer.csv"
    write_plane_histograms(found.records, again, channel_ids=found.channel_ids)
    data = again.read_bytes()
    again.unlink()
    return data


def _run(root, out, workers, mapping, masks):
    """One run into ``out`` emptied first; its result and every file's bytes."""
    for path in out.iterdir():
        if path.name != "morphobricks.toml":
            shutil.rmtree(path) if path.is_dir() else path.unlink()
    mode = RunMode("restart", verify_content=True, workers=workers)
    result = run_described(root, mapping, output_folder=out, mode=mode, masks_folder=masks)
    return result, every_file(out)


@pytest.mark.parametrize("item", fixtures("valid"), ids=lambda item: item.name.split("_")[0])
def test_every_spec_fixture_writes_the_same_bytes_with_workers_1_2_4_and_auto(tmp_path, item):
    """Every valid fixture, every file byte for byte; the table is the writer's bytes."""
    data = re.sub(rb"image_count = (\d{3,})", b"image_count = 3", item.data)  # v09: 1000
    root, out, masks, mapping, description = _whole_tree(tmp_path, data)
    serial, expected = _run(root, out, 1, mapping, masks)
    for workers in (2, 4, "auto"):
        result, files = _run(root, out, workers, mapping, masks)
        assert files == expected, sorted(k for k in expected if files.get(k) != expected[k])
        assert result.written == serial.written and result.read == serial.read
        assert result.issues == serial.issues
    if not serial.written:  # v29: units of different image types, refused alike
        assert item.name.startswith("v29_") and "plane_histograms.csv" not in expected
        codes = {issue.code for issue in serial.issues if issue.is_error}
        assert codes == {"R_HETEROGENEOUS_LAYOUT_NOT_ENABLED"}, codes
        return
    assert serial.read == described_images(description)
    assert json.loads(expected["run_manifest.json"])["written_at"] == WRITTEN_AT
    assert _writers_bytes(out / "plane_histograms.csv") == expected["plane_histograms.csv"]


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
    """The owner's rule of 16 November 2026 is written where the manifest is described.

    It is about a run's outputs (contract §9): the conversion record of ``convert`` has its own
    ``written_at`` and the copies' ``mtime_ns``, so no document says "any output".
    """
    root = Path(__file__).resolve().parents[1]
    texts = {
        name: " ".join((root / name).read_text(encoding="utf-8").split())
        for name in ("docs/contract.md", "docs/user_guide.md", "AGENTS.md")
    }
    assert "`written_at` is the only field excluded from byte identity" in texts["docs/contract.md"]
    guide = "`written_at` is the only field of a run's outputs excluded from byte identity"
    assert guide in texts["docs/user_guide.md"]
    assert "the only field of a run's outputs excluded from byte identity" in texts["AGENTS.md"]
    for name, text in texts.items():
        assert "only field of any output" not in text, name
