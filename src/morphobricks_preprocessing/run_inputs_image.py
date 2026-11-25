"""A run's image as a lazy ``(T, Z, C, Y, X)`` array (moved from ``run_inputs``, PR 12)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from .run_inputs import RunInputs


@dataclass(frozen=True)
class LazyImage:
    """An image of a clean run as a ``(T, Z, C, Y, X)`` array whose planes are read on demand.

    Attributes
    ----------
    inputs
        The run.
    relative_path
        The image.
    shape
        ``(T, Z, C, Y, X)``.
    dtype
        The stored pixel type.
    """

    inputs: RunInputs
    relative_path: str
    shape: tuple[int, ...]
    dtype: np.dtype

    def __getitem__(self, index: tuple[int, int]) -> np.ndarray:
        """Read the ``(C, Y, X)`` plane at ``[t, z]``."""
        t, z = index
        return self.inputs.read_plane(self.relative_path, z=z, t=t)

    def __array__(self, dtype: np.dtype | None = None, copy: bool | None = None) -> np.ndarray:
        """Read every plane into one new array of ``dtype`` (default: the stored type).

        The array is allocated once and filled plane by plane (cast as ``astype`` casts), so
        the peak is about one result, not the stacked planes plus the result (#453).

        Raises
        ------
        ValueError
            If ``copy`` is False: the planes are read from the files, so the array is always
            a new one (NumPy 2's ``__array__`` protocol); or a plane read is not the
            ``(C, Y, X)`` of :attr:`shape` (never broadcast into it).
        """
        if copy is False:
            raise ValueError(
                f"{self.relative_path} is read from its files, so it cannot be given as an "
                "array without a copy (copy=False); use numpy.asarray without copy=False."
            )
        out = np.empty(self.shape, dtype=self.dtype if dtype is None else dtype)
        for t in range(self.shape[0]):
            for z in range(self.shape[1]):
                plane = self[t, z]
                if plane.shape != self.shape[2:]:
                    raise ValueError(
                        f"The plane t={t}, z={z} of {self.relative_path} is {plane.shape}, "
                        f"not the (C, Y, X) {self.shape[2:]} of the image."
                    )
                np.copyto(out[t, z], plane, casting="unsafe")
        return out
