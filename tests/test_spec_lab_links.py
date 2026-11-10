"""Lab-tool links in the run manifest: conformance §2 "Provenance" row, the Stage 2 part (PP-2).

Spec experiment-v1 §9.3: provenance from lab tools (repository URL, commit SHA, experiment path
and per-repeat lab-tool repeat codes) is an optional, user-supplied preprocessing input recorded
only in the run manifest, never in ``morphobricks.toml`` (``i99``: ``E_FORBIDDEN_FIELD``); it is
excluded from every identity, pairing and cache key; a mapping from a lab-tool code to a
canonical repeat ID must be confirmed by the user, and equal strings are not evidence.

Driven by the vendored fixtures: ``v03`` (canonical repeats ``Day1`` and ``Day2`` over the source
folders ``rep1``, ``experiment_23``, ``rep2`` and ``rep2_drug``), ``v08`` (masks, for the mask
step's check) and ``i99``. The same run is made with and without links, and with changed links:
every output but the manifest's ``lab_links`` block (and the report's record of the manifest's
SHA-256) is byte for byte the same, every row is reused, and the section digests, the
description's SHA-256, the row keys and the resolver's provenance do not move.
"""

from __future__ import annotations

import datetime
import json

import pytest

from description_tree import MAPPING, build_experiment, write_mask
from frozen_clock import FrozenClock
from morphobricks_preprocessing import RunInputs, run_experiment
from morphobricks_preprocessing.description import DescriptionError, description_from_bytes
from morphobricks_preprocessing.fingerprint import RowKey
from morphobricks_preprocessing.lab_links import (
    MISSING_REPEAT,
    UNCONFIRMED,
    UNKNOWN_REPEAT,
    LabLinks,
)
from morphobricks_preprocessing.run import RunMode
from morphobricks_preprocessing.run.mask_step import describe_masks, prepare_mask_step
from morphobricks_preprocessing.run_inputs_request import Request
from spec_pin import fixtures

VALID = {item.name.split("_")[0]: item for item in fixtures("valid")}
INVALID = {item.name.split("_")[0]: item for item in fixtures("invalid")}
COMMIT = "0123456789abcdef0123456789abcdef01234567"
OTHER_COMMIT = "fedcba9876543210fedcba9876543210fedcba98"
CODES = {"R001": "Day1", "R002": "Day2"}


def _links(commit=COMMIT, codes=CODES, confirmed=True):
    return LabLinks(
        "https://example.org/lab/database", commit, "experiments/2026-11-06", codes, confirmed
    )


@pytest.fixture
def clock(monkeypatch):
    # One frozen time, so a manifest's written_at is never what tells two runs apart.
    return FrozenClock(monkeypatch, datetime.date(2026, 11, 10))


def _setup(tmp_path, name):
    root, out = tmp_path / "exp", tmp_path / "out"
    out.mkdir()
    (out / "morphobricks.toml").write_bytes(VALID[name].data)
    build_experiment(root, description_from_bytes(VALID[name].data), 2)
    return root, out


def _run(root, out, links=None, mode="restart", **kwargs):
    result = run_experiment(
        root, MAPPING, output_folder=out, mode=RunMode(mode, lab_links=links), **kwargs
    )
    assert result.written, [i.message for i in result.issues]
    return result


def _outputs(out):
    """Every file of the output folder by relative path, the manifest and report decoded."""
    found = {}
    for path in sorted(out.rglob("*")):
        if path.is_file():
            found[path.relative_to(out).as_posix()] = path.read_bytes()
    manifest = json.loads(found.pop("run_manifest.json"))
    report = json.loads(found.pop("discovery_report.json"))
    text = found.pop("discovery_report.txt").decode("utf-8").splitlines()
    return manifest, report, text, found


def _row_keys(inputs):
    """The key each file's rows are reused by (``fingerprint.RowKey``, contract §9)."""
    manifest = inputs.manifest
    return [RowKey.of(f, manifest.settings, manifest.experiment_root) for f in inputs.files]


def _without_links(snapshot):
    """The outputs with the manifest's links block and the manifest's own SHA-256 left out."""
    manifest, report, text, files = snapshot
    manifest = {k: v for k, v in manifest.items() if k != "lab_links"}
    report = {k: v for k, v in report.items() if k != "manifest_sha256"}
    text = [line for line in text if not line.startswith("Run manifest SHA-256:")]
    return manifest, report, text, files


def test_links_are_recorded_verbatim_in_the_manifest_only(tmp_path, clock):
    root, out = _setup(tmp_path, "v03")
    _run(root, out, _links())
    manifest, _, _, files = _outputs(out)
    assert manifest["lab_links"] == {
        "repository_url": "https://example.org/lab/database",
        "commit": COMMIT,
        "experiment_path": "experiments/2026-11-06",
        "repeat_codes": {"R001": "Day1", "R002": "Day2"},
    }
    # Nowhere else: not in the description, the design, the table or the records.
    for name, data in files.items():
        for text in (COMMIT, "example.org", "R001"):
            assert text.encode() not in data, (name, text)
    assert (out / "morphobricks.toml").read_bytes() == VALID["v03"].data


@pytest.mark.parametrize("mode", ["restart", "full"])
def test_links_change_no_output_digest_or_cache_key(tmp_path, clock, mode):
    root, out = _setup(tmp_path, "v03")
    _run(root, out)
    _run(root, out, None, mode)  # the same rerun without links: what each rerun is compared with
    without = _outputs(out)
    assert "lab_links" not in without[0]  # a run without links has no such key
    inputs = RunInputs.open(out)
    digests, keys = inputs.section_digests(), _row_keys(inputs)
    provenance = inputs.resolve_request(Request(("DNA",))).provenance

    for links in (_links(), _links(commit=OTHER_COMMIT), _links(codes=None), None):
        result = _run(root, out, links, mode)
        if mode == "restart":
            assert result.read == () and len(result.reused) == 8  # every row key unchanged
        snapshot = _outputs(out)
        assert _without_links(snapshot) == _without_links(without)
        assert snapshot[0].get("lab_links") == (None if links is None else links.to_dict())
        inputs = RunInputs.open(out)
        assert inputs.section_digests() == digests
        assert _row_keys(inputs) == keys
        assert inputs.resolve_request(Request(("DNA",))).provenance == provenance
        recorded = snapshot[0]["experiment_description"]
        assert recorded["sha256"] == without[0]["experiment_description"]["sha256"]
        assert (
            recorded["section_digests"] == without[0]["experiment_description"]["section_digests"]
        )


def test_links_are_not_taken_over_by_the_next_run(tmp_path, clock):
    root, out = _setup(tmp_path, "v03")
    _run(root, out, _links())
    _run(root, out)
    assert "lab_links" not in _outputs(out)[0]


def test_links_keep_the_mask_steps_check(tmp_path, clock):
    root, out = _setup(tmp_path, "v08")
    masks = tmp_path / "masks"
    for image in sorted(p.relative_to(root).as_posix() for p in root.rglob("*.tif")):
        write_mask(masks, "nuclei", image, [0, 1, 2, 3])
        write_mask(masks, "tumor_region", image, [0, 1])
    _run(root, out, masks_folder=masks)
    step = describe_masks(prepare_mask_step(None, out))
    assert not [i for i in step.issues if i.is_error], step.issues
    checked = (out / "mask_relationships.json").read_bytes()
    _run(root, out, masks_folder=masks)  # the rerun after the mask step, without links
    before = _without_links(_outputs(out))
    codes = {"A": "rep1"}  # v08 declares the one repeat rep1
    result = _run(root, out, _links(codes=codes), masks_folder=masks)
    assert "mask_relationships.json" not in result.replaced  # the mask check is kept
    assert (out / "mask_relationships.json").read_bytes() == checked
    assert _without_links(_outputs(out)) == before


def test_unconfirmed_repeat_codes_are_refused_even_when_the_strings_are_equal(tmp_path, clock):
    root, out = _setup(tmp_path, "v03")
    for codes in (CODES, {"Day1": "Day1", "Day2": "Day2"}):
        result = run_experiment(
            root,
            MAPPING,
            output_folder=out,
            mode=RunMode(lab_links=_links(codes=codes, confirmed=False)),
        )
        assert not result.written
        assert {i.code for i in result.issues if i.is_error} == {UNCONFIRMED}
        assert not (out / "run_manifest.json").exists()


@pytest.mark.parametrize(
    ("codes", "expected"),
    [
        ({"R001": "Day1"}, {MISSING_REPEAT}),  # Day2 has no code
        ({"R001": "Day1", "R002": "rep2"}, {UNKNOWN_REPEAT, MISSING_REPEAT}),  # a folder, not an ID
        ({"R001": "Day1", "R002": "Day2", "R003": "Day3"}, {UNKNOWN_REPEAT}),
        ({"R001": "day1", "R002": "Day2"}, {UNKNOWN_REPEAT, MISSING_REPEAT}),  # IDs exactly
    ],
)
def test_repeat_codes_must_name_every_described_repeat_and_no_other(
    tmp_path, clock, codes, expected
):
    root, out = _setup(tmp_path, "v03")
    result = run_experiment(
        root, MAPPING, output_folder=out, mode=RunMode(lab_links=_links(codes=codes))
    )
    assert not result.written
    assert {i.code for i in result.issues if i.is_error} == expected
    assert not (out / "run_manifest.json").exists()


def test_i99_lab_tool_provenance_in_the_description_is_still_refused(tmp_path, clock):
    with pytest.raises(DescriptionError) as caught:
        description_from_bytes(INVALID["i99"].data)
    assert {issue.code for issue in caught.value.issues} == {"E_FORBIDDEN_FIELD"}
    root, out = _setup(tmp_path, "v01")
    (out / "morphobricks.toml").write_bytes(INVALID["i99"].data)
    result = run_experiment(
        root, MAPPING, output_folder=out, mode=RunMode(lab_links=_links(codes=None))
    )
    assert not result.written
    assert "E_FORBIDDEN_FIELD" in {i.code for i in result.issues}
    assert not (out / "run_manifest.json").exists()
