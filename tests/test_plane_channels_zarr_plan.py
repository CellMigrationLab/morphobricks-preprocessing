"""The OME-Zarr plane plan checks the request as the read does (#379)."""

import numpy as np
import pytest

from morphobricks_preprocessing import prefill_mapping
from morphobricks_preprocessing.plane_channels import read_xyc_plane, xyc_plane_memory
from zarr_tree import write_ngff


@pytest.fixture
def czyx(tmp_path):
    data = np.arange(3 * 2 * 8 * 6, dtype=np.uint16).reshape(3, 2, 8, 6)
    return write_ngff(tmp_path / "a.zarr", data, axes="czyx"), prefill_mapping("CZYX")


@pytest.mark.parametrize(
    "request_",
    [
        {"channel_count": 0, "z": 0},
        {"channel_count": 4, "z": 0},
        {"z": 2},
        {"z": -1},
        {"z": 0, "t": 1},  # an axis the image lacks
    ],
)
def test_an_impossible_request_is_refused_by_the_plan_as_by_the_read(czyx, request_):
    path, mapping = czyx
    with pytest.raises(IndexError):
        read_xyc_plane(path, mapping, **request_)
    with pytest.raises(IndexError):
        xyc_plane_memory(path, mapping, **request_)


def test_a_possible_request_is_planned_as_before(czyx):
    path, mapping = czyx
    assert xyc_plane_memory(path, mapping, 2, z=1) > 0
    assert read_xyc_plane(path, mapping, z=1, channel_count=2).shape == (2, 8, 6)
