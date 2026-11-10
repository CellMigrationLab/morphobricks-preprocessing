"""The ``A_*`` error classes and ``RunInputs.section_digests()`` (Stage 2 design PR 2).

Spec codes (experiment-v1 section 8) as classes the bricks share (contract section 16): one
class per code, each a ``RunInputsError`` that carries its ``code``, exported at the package
top level, picklable (a worker process raises them), and with the code in the message text.
"""

from __future__ import annotations

import pickle

import pytest

import morphobricks_preprocessing as package
from description_tree import MAPPING, build_experiment
from morphobricks_preprocessing import run_experiment
from morphobricks_preprocessing.description import description_from_bytes
from morphobricks_preprocessing.description_write import section_digests
from morphobricks_preprocessing.manifest import read_manifest
from morphobricks_preprocessing.run_inputs import RunInputs, RunInputsError
from morphobricks_preprocessing.run_inputs_error import (
    ChannelNotFoundError,
    DescriptionRequiredError,
    DraftRefusedError,
    MaskNotInstancesError,
    MaskUncheckedError,
    RgbNotSupportedError,
)
from spec_pin import fixtures

VALID = {item.name.split("_")[0]: item for item in fixtures("valid")}
CODES = [
    (DescriptionRequiredError, "A_DESCRIPTION_REQUIRED"),
    (DraftRefusedError, "A_DRAFT_REFUSED"),
    (MaskUncheckedError, "A_MASK_UNCHECKED"),
    (ChannelNotFoundError, "A_SCOPE_INCOMPLETE"),
    (MaskNotInstancesError, "A_MASK_NOT_INSTANCES"),
    (RgbNotSupportedError, "A_RGB_NOT_SUPPORTED"),
]


def _open(tmp_path, name="v01"):
    data = VALID[name].data
    root, out = tmp_path / "exp", tmp_path / "out"
    out.mkdir()
    build_experiment(root, description_from_bytes(data))
    (out / "morphobricks.toml").write_bytes(data)
    assert run_experiment(root, MAPPING, output_folder=out).written
    return RunInputs.open(out), out


def test_an_output_folder_without_a_description_is_refused_at_open(tmp_path):
    inputs, out = _open(tmp_path)
    (out / "morphobricks.toml").unlink()
    with pytest.raises(DescriptionRequiredError, match="A_DESCRIPTION_REQUIRED") as refused:
        RunInputs.open(out)
    assert refused.value.code == "A_DESCRIPTION_REQUIRED" and "describe" in str(refused.value)


@pytest.mark.parametrize(("error", "code"), CODES)
def test_each_class_is_a_run_inputs_error_with_its_code_and_is_exported(error, code):
    assert issubclass(error, RunInputsError) and error.code == code
    assert getattr(package, error.__name__) is error and error.__name__ in package.__all__
    raised = error(f"refused ({code})")
    assert raised.code == code and isinstance(raised, ValueError)
    with pytest.raises(RunInputsError, match=code):
        raise raised


@pytest.mark.parametrize(("error", "code"), CODES)
def test_each_class_survives_pickling_with_its_code(error, code):
    again = pickle.loads(pickle.dumps(error("a message")))
    assert type(again) is error and again.code == code and str(again) == "a message"


def test_the_general_refusal_has_no_code():
    assert RunInputsError.code is None and RunInputsError("x").code is None


def test_an_absent_channel_is_a_scope_incomplete_refusal_naming_the_image(tmp_path):
    inputs, _ = _open(tmp_path)
    fov = inputs.fovs()[0]
    with pytest.raises(ChannelNotFoundError) as raised:
        inputs.channel_index(fov, "PHASE")
    assert raised.value.code == "A_SCOPE_INCOMPLETE"
    message = str(raised.value)
    assert "PHASE" in message and fov.relative_path in message and "DNA, ACTIN" in message
    assert "A_SCOPE_INCOMPLETE" in message
    with pytest.raises(ChannelNotFoundError, match=fov.relative_path) as by_path:
        inputs.channel_index(fov.relative_path, "dna")  # by path; case is part of the ID
    assert by_path.value.code == "A_SCOPE_INCOMPLETE"


def test_section_digests_equal_the_manifests(tmp_path):
    inputs, out = _open(tmp_path)
    recorded = read_manifest(out / "run_manifest.json").experiment_description["section_digests"]
    digests = inputs.section_digests()
    assert digests == recorded == section_digests(description_from_bytes(VALID["v01"].data))
    assert set(digests) == {"conditions", "channels", "repeats", "masks"}
    digests["masks"] = "changed"
    assert inputs.section_digests() == recorded  # a copy: the manifest is not changed


def test_section_digests_read_the_manifest_only(tmp_path):
    inputs, out = _open(tmp_path)
    (out / "morphobricks.toml").unlink()  # description() would now refuse the run
    with pytest.raises(RunInputsError):
        inputs.description()
    assert inputs.section_digests() == section_digests(description_from_bytes(VALID["v01"].data))
