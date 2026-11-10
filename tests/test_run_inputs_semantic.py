"""What ``RunInputs`` gives from a published description, and that it gives nothing without one.

The Spec scenarios (P14, P25, P28) are in ``test_spec_run_inputs_semantic.py``. Here: canonical
channel IDs and ``channel_index`` (an absent channel is an error, never a default), canonical IDs
that differ from the folder labels, the condition reference graph, the masks' semantics, a unit
that does not match its image, and a run without a description: every field None, nothing changed.
"""

from __future__ import annotations

import hashlib
import json
import pickle
import warnings
from dataclasses import replace

import pytest

from described_tree import run_described
from description_tree import MAPPING, build_experiment
from morphobricks_preprocessing.description import description_from_bytes
from morphobricks_preprocessing.description_write import canonical_bytes
from morphobricks_preprocessing.run_inputs import RunInputs, RunInputsError
from morphobricks_preprocessing.run_inputs_error import ChannelNotFoundError
from morphobricks_preprocessing.run_inputs_semantic import MaskSemantics
from spec_pin import fixtures

VALID = {item.name.split("_")[0]: item for item in fixtures("valid")}


def _open(tmp_path, data, *, publish=True):
    description = description_from_bytes(data)
    root, out = tmp_path / "exp", tmp_path / "out"
    out.mkdir()
    build_experiment(root, description)
    if publish:
        (out / "morphobricks.toml").write_bytes(data)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        assert run_described(root, MAPPING, output_folder=out).written
    return RunInputs.open(out), description


def test_channel_ids_are_the_units_in_c_order_and_channel_index_finds_them(tmp_path):
    inputs, _ = _open(tmp_path, VALID["v01"].data)
    fov = inputs.fovs()[0]
    assert fov.channel_ids == ("DNA", "ACTIN") and fov.c_count == 2
    assert inputs.channel_index(fov, "DNA") == 0
    assert inputs.channel_index(fov, "ACTIN") == 1
    assert inputs.channel_index(fov.relative_path, "ACTIN") == 1  # by path as well


def test_an_absent_channel_is_an_error_never_a_default(tmp_path):
    inputs, _ = _open(tmp_path, VALID["v01"].data)
    fov = inputs.fovs()[0]
    with pytest.raises(ChannelNotFoundError, match="PHASE.*DNA, ACTIN"):
        inputs.channel_index(fov, "PHASE")
    with pytest.raises(ChannelNotFoundError):
        inputs.channel_index(fov, "dna")  # case is part of the ID


def test_canonical_ids_are_added_beside_the_unchanged_key(tmp_path):
    inputs, _ = _open(tmp_path, VALID["v33"].data)  # condition folder "control", ID "ctrl"
    fovs = inputs.fovs()
    control = [f for f in fovs if f.condition_id == "ctrl"]
    assert control and all(f.key[0] == "control" for f in control)
    assert {f.technical_repeat_id for f in fovs} == {"single"}
    assert all(f.key[2] == "single" for f in fovs)


def test_the_condition_reference_graph_uses_canonical_ids(tmp_path):
    inputs, _ = _open(tmp_path, VALID["v33"].data)
    assert inputs.references() == {"DrugA": "ctrl", "ctrl": None}


def test_a_reference_chain_is_given_as_it_is_declared(tmp_path):
    inputs, description = _open(tmp_path, VALID["v10"].data)
    graph = inputs.references()
    assert graph == {k: v.reference for k, v in sorted(description.conditions.items())}
    assert graph["VEC_SiCol6a1"] == "VEC_SiNC" and graph["VEC_SiNC"] == "VEC"


def test_mask_semantics_are_the_declared_ones(tmp_path):
    inputs, _ = _open(tmp_path, VALID["v08"].data)
    assert inputs.mask_semantics() == {
        "Nuclei": MaskSemantics("nuclei", "instances", ("DNA",)),
        "TumorRegion": MaskSemantics("tumor_region", "binary", None),
    }


def test_a_unit_that_does_not_match_its_image_is_refused(tmp_path):
    description = description_from_bytes(VALID["v01"].data)
    root, out = tmp_path / "exp", tmp_path / "out"
    out.mkdir()
    build_experiment(root, description)
    (out / "morphobricks.toml").write_bytes(VALID["v01"].data)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        assert run_described(root, MAPPING, output_folder=out).written
    repeat = description.repeats["rep1"]
    member = repeat.members["CTRL"]
    unit = member.technical_repeats["single"]
    short = replace(unit, channels=unit.channels[:1])
    changed = replace(member, technical_repeats={**member.technical_repeats, "single": short})
    repeats = {**description.repeats, "rep1": replace(repeat, members={"CTRL": changed})}
    data = canonical_bytes(replace(description, repeats=repeats))
    # Same file bytes recorded by the run: forge the manifest's SHA so only the units differ.
    manifest = json.loads((out / "run_manifest.json").read_text())
    manifest["experiment_description"]["sha256"] = hashlib.sha256(data).hexdigest()
    (out / "run_manifest.json").write_text(json.dumps(manifest))
    (out / "morphobricks.toml").write_bytes(data)
    inputs = RunInputs.open(out)
    with pytest.raises(RunInputsError, match="declares 1"):
        inputs.fovs()


def test_run_inputs_with_a_description_is_picklable(tmp_path):
    inputs, description = _open(tmp_path, VALID["v01"].data)
    again = pickle.loads(pickle.dumps(inputs))
    assert again.description() == description
    assert again.fovs() == inputs.fovs()
