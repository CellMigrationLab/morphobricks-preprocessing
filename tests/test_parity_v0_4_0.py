"""Cross-version parity: legacy homogeneous datasets are identical under v0.4.0 and this code.

Stage 1 design section 4. The same script runs under the extracted ``v0.4.0`` tag and under
this checkout's ``src`` (``released_code``; in CI a missing tag fails, it does not skip):
confirm a design, run, rerun. Required equal: ``plane_histograms.csv`` and
``experiment_design.csv`` bytes (so plane IDs, FOV keys and ``position_id`` are the same), the
identity of every manifest file entry, and, over a v0.4.0 output folder, no row recomputed.
The derived ``experiment_design.csv`` of the dataset's description equals v0.4.0's bytes.
"""

from __future__ import annotations

import shutil

import pytest

from morphobricks_preprocessing import read_confirmed_design
from morphobricks_preprocessing.description import description_from_bytes
from morphobricks_preprocessing.design_derive import write_derived_design
from parity_datasets import DATASETS, DATE, description_bytes
from released_code import PRELUDE, ROOT, extract, run_with

RELEASE = "v0.4.0"
ADDED_ENTRY_KEYS = {"raw_channel_names"}
"""Manifest file-entry keys added since v0.4.0 (optional, never read by the row-reuse check)."""

RUN = (
    PRELUDE
    + """
import dataclasses
root, out, axes, image_type, control = sys.argv[2:7]
image_type = None if image_type == "-" else image_type
controls = set() if control == "-" else {control}
mapping = package.prefill_mapping(axes)
from morphobricks_preprocessing.dataset import biological_repeats
found = package.default_design(biological_repeats(package.discover_experiment(root)))
design = package.ExperimentDesign(tuple(
    dataclasses.replace(r, control=r.condition in controls, confirmed=True, confirmed_on="@DATE@")
    for r in found.rows
))
first = package.run_experiment(
    root, mapping, output_folder=out, image_type=image_type, design=design
)
assert first.written, first.issues
again = package.run_experiment(root, output_folder=out)
manifest = json.loads((Path(out) / "run_manifest.json").read_text(encoding="utf-8"))
print(json.dumps({
    "written": first.written,
    "first": {"new": list(first.new), "read": list(first.read)},
    "again": {"written": again.written, "read": list(again.read),
              "unchanged": list(again.unchanged), "rebuild_reason": again.rebuild_reason},
    "files": [{k: v for k, v in e.items() if k != "content_sha256"} for e in manifest["files"]],
}))
""".replace("@DATE@", DATE)
)

RERUN = (
    PRELUDE
    + """
root, out = sys.argv[2:4]
result = package.run_experiment(root, output_folder=out)
print(json.dumps({"written": result.written, "read": list(result.read),
                  "new": list(result.new), "changed": list(result.changed),
                  "unchanged": list(result.unchanged), "rebuild_reason": result.rebuild_reason,
                  "resumed": list(result.resumed)}))
"""
)


@pytest.fixture(scope="module")
def released(tmp_path_factory):
    return extract(RELEASE, tmp_path_factory.mktemp("released"))


def _args(root, out, name):
    _, _, axes, image_type, _, control = DATASETS[name]
    return root, out, axes, image_type or "-", control or "-"


def _build(tmp_path, name):
    images, writer = DATASETS[name][:2]
    root = tmp_path / "exp"
    writer(root, images)
    return root


@pytest.fixture(params=sorted(DATASETS))
def case(request, tmp_path, released):
    name = request.param
    root = _build(tmp_path, name)
    old = run_with(released, RUN, *_args(root, tmp_path / "old", name))
    new = run_with(ROOT / "src", RUN, *_args(root, tmp_path / "new", name))
    return name, root, tmp_path, old, new


def test_the_exports_are_byte_identical(case):
    _, _, tmp_path, _, _ = case
    for name in ("plane_histograms.csv", "experiment_design.csv"):
        old, new = tmp_path / "old" / name, tmp_path / "new" / name
        assert old.read_bytes() == new.read_bytes()
    assert (tmp_path / "old" / "plane_histograms.csv").read_text().count("\n") > 1


def test_the_identities_and_the_run_are_the_same(case):
    _, _, _, old, new = case
    for before, after in zip(old["files"], new["files"], strict=True):
        assert set(after) - set(before) <= ADDED_ENTRY_KEYS
        assert {k: after[k] for k in before} == before  # every v0.4.0 key keeps its value
    assert old["files"]
    assert old["first"] == new["first"]
    assert old["again"]["written"] == new["again"]["written"]
    assert old["again"]["read"] == new["again"]["read"] == []  # a rerun recomputes no row
    assert old["again"]["unchanged"] == new["again"]["unchanged"]


def test_this_code_reuses_every_row_of_a_v0_4_0_output_folder(case):
    name, root, tmp_path, old, _ = case
    copy = tmp_path / "old-copy"
    shutil.copytree(tmp_path / "old", copy)
    before = (copy / "plane_histograms.csv").read_bytes()
    found = run_with(ROOT / "src", RERUN, root, copy)
    assert found["rebuild_reason"] is None and found["new"] == [] and found["changed"] == []
    assert found["read"] == [] and found["resumed"] == []  # zero rows recomputed
    assert sorted(found["unchanged"]) == sorted(old["first"]["new"])
    assert (copy / "plane_histograms.csv").read_bytes() == before


def test_the_derived_design_equals_the_bytes_v0_4_0_wrote(case):
    name, _, tmp_path, _, _ = case
    images, _, _, image_type, channels, control = DATASETS[name]
    data = description_bytes(images, channels, control, image_type)
    path = write_derived_design(description_from_bytes(data), tmp_path / "derived.csv")
    assert path.read_bytes() == (tmp_path / "old" / "experiment_design.csv").read_bytes()
    assert read_confirmed_design(path).design.rows
