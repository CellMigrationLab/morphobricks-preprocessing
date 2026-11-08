"""Semantic ``RunInputs``: conformance P14 and P25, and ``A_MASK_UNCHECKED`` of P28 (PR 12).

P14: only the published, confirmed ``morphobricks.toml`` is read; a draft or a file with an
unconfirmed repeat is ``A_DRAFT_REFUSED``. P25: every position carries the key with the source
label unchanged and the added ``repeat_group``; in ``v30`` the two ``rep1`` folders are in
different repeats, so grouping by the source label would merge what the description separates.
P28: a mask file the mask step has not checked is refused when it is read.
"""

from __future__ import annotations

import json

import pytest

from description_tree import MAPPING, build_experiment, write_mask
from morphobricks_preprocessing import run_experiment
from morphobricks_preprocessing.description import description_from_bytes
from morphobricks_preprocessing.description_consume import read_published_description
from morphobricks_preprocessing.run.mask_step import describe_masks, prepare_mask_step
from morphobricks_preprocessing.run_inputs import RunInputs, RunInputsError
from morphobricks_preprocessing.run_inputs_error import DraftRefusedError, MaskUncheckedError
from spec_pin import fixtures

VALID = {item.name.split("_")[0]: item for item in fixtures("valid")}


def _described_run(tmp_path, name, images=2):
    description = description_from_bytes(VALID[name].data)
    root, out = tmp_path / "exp", tmp_path / "out"
    out.mkdir()
    (out / "morphobricks.toml").write_bytes(VALID[name].data)
    found = build_experiment(root, description, images)
    return root, out, found


def _run(root, out, masks=None):
    assert run_experiment(root, MAPPING, output_folder=out, masks_folder=masks).written


@pytest.mark.parametrize(("name", "groups"), [("v03", {"Day1", "Day2"}), ("v30", {"Day1", "Day2"})])
def test_p25_each_fov_carries_the_source_label_and_the_added_repeat_group(tmp_path, name, groups):
    root, out, _ = _described_run(tmp_path, name)
    description = description_from_bytes(VALID[name].data)
    _run(root, out)
    fovs = RunInputs.open(out).fovs()
    assert {fov.repeat_group for fov in fovs} == groups
    for fov in fovs:
        condition, repeat, technical, _ = fov.key
        member = description.repeats[fov.repeat_group].members[fov.condition_id]
        assert repeat == member.source_repeat  # the source label stays in the key
        assert condition == description.conditions[fov.condition_id].source_folder
        assert technical == member.technical_repeats[fov.technical_repeat_id].source_folder


def test_p25_in_v30_grouping_by_the_source_label_would_merge_different_repeats(tmp_path):
    root, out, _ = _described_run(tmp_path, "v30")
    _run(root, out)
    fovs = RunInputs.open(out).fovs()
    rep1 = [fov for fov in fovs if fov.key[1] == "rep1"]
    assert {fov.key[0] for fov in rep1} == {"CTRL", "DrugA"}
    by_label = {fov.key[1] for fov in rep1}
    by_group = {fov.repeat_group for fov in rep1}
    assert len(by_label) == 1 and by_group == {"Day1", "Day2"}
    assert {(f.key[0], f.repeat_group) for f in rep1} == {("CTRL", "Day1"), ("DrugA", "Day2")}


def test_p14_a_draft_is_never_read(tmp_path):
    root, out, _ = _described_run(tmp_path, "v01")
    description = description_from_bytes(VALID["v01"].data)
    _run(root, out)
    (out / "morphobricks.draft.json").write_text('{"repeats": "garbage"}', encoding="utf-8")
    assert RunInputs.open(out).description() == description  # the draft is ignored
    with pytest.raises(DraftRefusedError) as raised:
        read_published_description(out / "morphobricks.draft.json")
    assert raised.value.code == "A_DRAFT_REFUSED"


def test_p14_a_published_file_with_an_unconfirmed_repeat_is_refused(tmp_path):
    root, out, _ = _described_run(tmp_path, "v01")
    _run(root, out)
    text = VALID["v01"].data.decode().replace("confirmed = true", "confirmed = false")
    (out / "morphobricks.toml").write_text(text, encoding="utf-8")
    with pytest.raises(DraftRefusedError, match="A_DRAFT_REFUSED"):
        RunInputs.open(out)


def test_p14_a_description_changed_since_the_run_is_refused(tmp_path):
    root, out, _ = _described_run(tmp_path, "v01")
    _run(root, out)
    (out / "morphobricks.toml").write_bytes(b"# edited\n" + VALID["v01"].data)
    with pytest.raises(RunInputsError, match="not the one the run read") as raised:
        RunInputs.open(out)
    assert not isinstance(raised.value, DraftRefusedError)


def test_p28_a_mask_file_not_yet_checked_is_refused_when_read_until_the_step_checked_it(tmp_path):
    root, out, found = _described_run(tmp_path, "v08")
    masks, image = tmp_path / "masks", found[0]
    _run(root, out)  # no masks yet
    write_mask(masks, "nuclei", image, [0, 1, 2])
    write_mask(masks, "tumor_region", image, [0, 1])
    _run(root, out, masks)
    inputs = RunInputs.open(out)
    fov = inputs.fovs()[0]
    assert inputs.mask_check_status("TumorRegion", fov) == "not yet checked"
    with pytest.raises(MaskUncheckedError) as raised:
        inputs.read_mask(fov, "tumor_region")
    assert raised.value.code == "A_MASK_UNCHECKED"
    assert describe_masks(prepare_mask_step(None, out)).issues == ()
    inputs = RunInputs.open(out)
    assert inputs.mask_check_status("TumorRegion", fov) == "checked"
    assert inputs.read_mask(fov, "tumor_region").shape == (8, 8)
    assert inputs.read_mask(fov, "nuclei").max() == 2
    manifest = json.loads((out / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["experiment_description"]["masks"]["TumorRegion"]["status"] == "not yet checked"
