"""``channel_index`` resolves a FOV through this run, never through the FOV's own IDs (#394)."""

from dataclasses import replace

import pytest

from morphobricks_preprocessing.run_inputs import RunInputsError
from test_run_inputs_semantic import VALID, _open


@pytest.fixture
def inputs(tmp_path):
    return _open(tmp_path, VALID["v01"].data)[0]


def test_a_fov_whose_channels_are_not_this_run_s_is_refused(inputs):
    fov = inputs.fovs()[0]
    assert fov.channel_ids == ("DNA", "ACTIN")
    swapped = replace(fov, channel_ids=("ACTIN", "DNA"))  # as another run would give it
    with pytest.raises(RunInputsError, match="another run"):
        inputs.channel_index(swapped, "DNA")


def test_a_fov_of_an_image_not_in_this_run_is_refused(inputs):
    fov = replace(inputs.fovs()[0], relative_path="elsewhere/x.tif")
    with pytest.raises(RunInputsError, match="not an image of this run"):
        inputs.channel_index(fov, "DNA")


def test_a_fov_and_its_path_give_the_same_index(inputs):
    for fov in inputs.fovs():
        for channel in fov.channel_ids:
            assert inputs.channel_index(fov, channel) == inputs.channel_index(
                fov.relative_path, channel
            )
