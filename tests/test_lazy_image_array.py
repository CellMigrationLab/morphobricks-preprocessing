"""A lazy image's ``__array__`` refuses ``copy=False`` and fills one array (#453)."""

import tracemalloc

import numpy as np
import pytest

from morphobricks_preprocessing.run_inputs_image import LazyImage

SHAPE = (4, 3, 2, 64, 64)  # T, Z, C, Y, X


class _Planes:
    """Stands in for ``RunInputs``: each plane is its (t, z) coordinates written in."""

    def __init__(self, shape=SHAPE[2:]):
        self.shape = shape

    def read_plane(self, relative_path, *, z, t):
        plane = np.empty(self.shape, np.uint16)
        plane[...] = 100 * t + 10 * z
        plane[-1] += 1
        return plane


def expected():
    t, z = np.meshgrid(np.arange(SHAPE[0]), np.arange(SHAPE[1]), indexing="ij")
    out = np.broadcast_to((100 * t + 10 * z)[..., None, None, None], SHAPE).astype(np.uint16)
    out[:, :, -1] += 1
    return out


def lazy(planes=None):
    return LazyImage(planes or _Planes(), "a.tif", SHAPE, np.dtype(np.uint16))


def test_copy_false_is_refused():
    with pytest.raises(ValueError, match="copy=False"):
        np.asarray(lazy(), copy=False)


@pytest.mark.parametrize("kwargs", [{}, {"copy": True}, {"dtype": np.uint16}])
def test_the_array_is_every_plane_in_order(kwargs):
    got = np.asarray(lazy(), **kwargs)
    assert got.dtype == np.uint16 and got.shape == SHAPE
    np.testing.assert_array_equal(got, expected())


def test_another_dtype_is_cast_as_astype_casts():
    got = np.asarray(lazy(), dtype=np.float32)
    assert got.dtype == np.float32
    np.testing.assert_array_equal(got, expected().astype(np.float32))


def test_a_plane_of_another_shape_is_refused_never_broadcast():
    with pytest.raises(ValueError, match=r"\(1, 64, 64\)"):
        np.asarray(lazy(_Planes((1, 64, 64))))


@pytest.mark.parametrize("dtype", [None, np.float32])
def test_the_peak_is_about_one_result(dtype):
    image = lazy()
    nbytes = np.prod(SHAPE) * np.dtype(dtype or np.uint16).itemsize
    tracemalloc.start()
    try:
        np.asarray(image, dtype=dtype)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 1.25 * nbytes
