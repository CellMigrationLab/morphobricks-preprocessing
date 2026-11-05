"""PP-8c item 5: ``RunInputs.read_plane`` and ``read_mask`` refuse coordinates of another type.

Passing a ``plane_id`` (or any other non-integer) as ``z`` or ``t`` gave a misleading
``IndexError`` ("has no Z img.tif::z=0,t=0"), or reached the reader. Now it is a
``TypeError`` naming the expected arguments. NumPy integers are integers.
"""

import numpy as np
import pytest

from morphobricks_preprocessing import RunInputs
from morphobricks_preprocessing.testing import make_clean_run


@pytest.fixture(scope="module")
def flat(tmp_path_factory):
    folder = tmp_path_factory.mktemp("flat")
    clean = make_clean_run(folder, axes="YX", shape=(8, 8), mask_types=("Cells",))
    return RunInputs.open(clean.output, masks_root=clean.masks)


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    clean = make_clean_run(tmp_path_factory.mktemp("stack"), axes="TCYX", shape=(3, 2, 8, 8))
    return RunInputs.open(clean.output)


@pytest.mark.parametrize("value", ["ctrl/rep1/a.ome.tif::z=0,t=0", "0", 0.0, 1.5, True, None])
def test_read_plane_refuses_a_z_or_t_that_is_not_an_integer(flat, value):
    fov = flat.fovs()[0]
    for kwargs in ({"z": value}, {"t": value}):
        with pytest.raises(TypeError, match=r"read_plane\(fov, z, t\).*whole numbers"):
            flat.read_plane(fov, **kwargs)


def test_read_plane_refuses_a_plane_id_given_as_z(stack):
    fov = stack.fovs()[0]
    with pytest.raises(TypeError, match="plane_id"):
        stack.read_plane(fov, fov.plane_ids[0])


def test_read_plane_takes_numpy_integers(stack):
    fov = stack.fovs()[0]
    assert np.array_equal(stack.read_plane(fov, t=np.int64(2)), stack.read_plane(fov, t=2))


@pytest.mark.parametrize("value", ["ctrl/rep1/a.ome.tif::z=0,t=0", 0.0, True])
def test_read_mask_refuses_a_z_or_t_that_is_not_an_integer(flat, value):
    fov = flat.fovs()[0]
    for kwargs in ({"z": value}, {"t": value}):
        with pytest.raises(TypeError, match=r"read_mask\(fov, mask_type, z, t\)"):
            flat.read_mask(fov, "Cells", **kwargs)


def test_read_mask_still_takes_none_and_integers(flat):
    fov = flat.fovs()[0]
    assert np.array_equal(
        flat.read_mask(fov, "Cells"), flat.read_mask(fov, "Cells", np.int32(0), 0)
    )
