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
        """Read every plane into one array."""
        data = np.stack(
            [np.stack([self[t, z] for z in range(self.shape[1])]) for t in range(self.shape[0])]
        )
        return data if dtype is None else data.astype(dtype)
