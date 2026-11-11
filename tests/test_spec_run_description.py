"""A run with a published description: conformance P04, P05, P11 and P28 (Stage 1 PR 11).

P04: lifted at Stage 3 (Spec roadmap S3.3, owner decision of 11 November 2026): units with
different channel lists (v04, v05, v06, v17) are processed; units of different image types (v29)
are still refused with ``R_HETEROGENEOUS_LAYOUT_NOT_ENABLED`` and nothing is processed. P05, P11
and P28: the mask step checks a declared mask's representation (never publication or the run), a
mask with files for none, some or all images is valid at every step, completeness is in the
manifest, and a file is ``not yet checked`` until the mask step has read it, again for each new
batch.
"""

from __future__ import annotations

import json

import pytest

from described_tree import run_described
from description_tree import MAPPING, build_experiment, write_mask
from morphobricks_preprocessing.description import description_from_bytes
from morphobricks_preprocessing.run.mask_step import describe_masks, prepare_mask_step
from spec_pin import fixtures

VALID = {item.name.split("_")[0]: item for item in fixtures("valid")}
CODE = "R_HETEROGENEOUS_LAYOUT_NOT_ENABLED"
BAD = "R_MASK_CONTRADICTS_REPRESENTATION"


def _setup(tmp_path, name, images=2):
    description = description_from_bytes(VALID[name].data)
    root, out = tmp_path / "exp", tmp_path / "out"
    out.mkdir()
    (out / "morphobricks.toml").write_bytes(VALID[name].data)
    return root, out, build_experiment(root, description, images)


def _run(root, out, masks=None):
    result = run_described(root, MAPPING, output_folder=out, masks_folder=masks)
    assert result.written, [i.message for i in result.issues]
    return json.loads((out / "run_manifest.json").read_text(encoding="utf-8"))


def _mask_status(manifest, mask_id):
    entry = manifest["experiment_description"]["masks"][mask_id]
    return entry["status"], {f["path"]: f["status"] for f in entry["files"]}


def _mask_step(out):
    result = describe_masks(prepare_mask_step(None, out))
    return {issue.code for issue in result.issues}, result


@pytest.mark.parametrize("name", ["v04", "v05", "v06", "v17"])
def test_p04_lifted_at_stage_3_different_channel_lists_are_processed(tmp_path, name):
    # Spec roadmap Stage 3 S3.3 (owner decision of 11 November 2026) and experiment-v1 section 8
    # ("R_HETEROGENEOUS_LAYOUT_NOT_ENABLED: Stage 1-2 only ... lifted at Stage 3"): units with
    # different channel lists are processed, each image checked against its own unit.
    root, out, images = _setup(tmp_path, name)
    result = run_described(root, MAPPING, output_folder=out)
    assert result.written, [(i.code, i.message) for i in result.issues]
    assert CODE not in {i.code for i in result.issues}
    assert result.plane_count == len(images)
    manifest = json.loads((out / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "5"


def test_p04_units_of_different_image_types_are_still_refused_naming_the_units(tmp_path):
    # Not lifted by the owner's decision: RGB units beside channel units (v29) stay refused.
    root, out, _ = _setup(tmp_path, "v29")
    result = run_described(root, MAPPING, output_folder=out)
    assert not result.written
    issue = next(i for i in result.issues if i.code == CODE)
    assert "2 channel layouts of different image types" in issue.message
    assert "Nothing was processed" in issue.message
    assert "rep1/CTRL/" in issue.action
    assert not (out / "plane_histograms.csv").exists()


def test_p05_binary_mask_with_values_0_to_5_is_refused_by_the_mask_step_not_the_run(tmp_path):
    root, out, images = _setup(tmp_path, "v08")
    masks = tmp_path / "masks"
    for image in images:
        write_mask(masks, "nuclei", image, [0, 1, 2, 3])  # instances: accepted as declared
        write_mask(masks, "tumor_region", image, [0, 1, 2, 3, 4, 5])
    manifest = _run(root, out, masks)  # the run reads no mask pixels and does not refuse
    assert _mask_status(manifest, "TumorRegion")[0] == "not yet checked"
    codes, result = _mask_step(out)
    assert codes == {BAD}
    assert len(result.issues) == len(images)  # one per file
    _run(root, out)
    manifest = json.loads((out / "run_manifest.json").read_text(encoding="utf-8"))
    assert _mask_status(manifest, "TumorRegion")[0] == "not yet checked"
    assert _mask_status(manifest, "Nuclei")[0] == "checked"


def test_p05_an_instances_mask_holding_only_0_and_1_is_accepted(tmp_path):
    root, out, images = _setup(tmp_path, "v08")
    masks = tmp_path / "masks"
    for image in images:
        write_mask(masks, "nuclei", image, [0, 1])
        write_mask(masks, "tumor_region", image, [0, 1])
    _run(root, out, masks)
    assert _mask_step(out)[0] == set()
    manifest = _run(root, out)
    assert _mask_status(manifest, "Nuclei")[0] == "checked"
    assert _mask_status(manifest, "TumorRegion")[0] == "checked"


def test_p11_masks_for_none_some_then_all_images_are_valid_and_completeness_is_recorded(tmp_path):
    root, out, images = _setup(tmp_path, "v09")
    assert len(images) == 4
    manifest = _run(root, out)
    entry = manifest["experiment_description"]["masks"]["Nuclei"]
    assert (entry["images_with_masks"], entry["images"], entry["files"]) == (0, 4, [])
    masks = tmp_path / "masks"
    write_mask(masks, "nuclei", images[0], [0, 1, 2])
    entry = _run(root, out, masks)["experiment_description"]["masks"]["Nuclei"]
    assert (entry["images_with_masks"], entry["images"]) == (1, 4)
    for image in images[1:]:
        write_mask(masks, "nuclei", image, [0, 1, 2])
    entry = _run(root, out, masks)["experiment_description"]["masks"]["Nuclei"]
    assert (entry["images_with_masks"], entry["images"]) == (4, 4)


def test_p28_each_new_batch_is_not_yet_checked_until_the_mask_step_has_read_it(tmp_path):
    root, out, images = _setup(tmp_path, "v08", images=4)
    masks = tmp_path / "masks"
    for image in images[:2]:  # batch 1: fine
        write_mask(masks, "nuclei", image, [0, 1, 2])
        write_mask(masks, "tumor_region", image, [0, 1])
    manifest = _run(root, out, masks)
    states = [_mask_status(manifest, name)[1] for name in ("Nuclei", "TumorRegion")]
    assert {s for files in states for s in files.values()} == {"not yet checked"}
    assert _mask_step(out)[0] == set()
    manifest = _run(root, out)
    assert _mask_status(manifest, "TumorRegion")[0] == "checked"
    for image in images[2:3]:  # batch 2: a binary mask with values 0-5
        write_mask(masks, "nuclei", image, [0, 1, 2])
        write_mask(masks, "tumor_region", image, [0, 1, 2, 3, 4, 5])
    manifest = _run(root, out)
    status, files = _mask_status(manifest, "TumorRegion")
    assert status == "not yet checked"
    assert sorted(files.values()) == ["checked", "checked", "not yet checked"]
    codes, _ = _mask_step(out)
    assert codes == {BAD}
    for image in images[3:]:  # batch 3
        write_mask(masks, "nuclei", image, [0, 1, 2])
        write_mask(masks, "tumor_region", image, [0, 1])
    status, files = _mask_status(_run(root, out), "TumorRegion")
    assert sorted(files.values()) == ["checked", "checked", "not yet checked", "not yet checked"]
    assert status == "not yet checked"
    assert _mask_step(out)[0] == {BAD}  # the bad file again; batch 3 is read and recorded
    manifest = _run(root, out)
    _, files = _mask_status(manifest, "TumorRegion")
    bad = [p for p, s in files.items() if s == "not yet checked"]
    assert len(bad) == 1 and bad[0].endswith(images[2])
