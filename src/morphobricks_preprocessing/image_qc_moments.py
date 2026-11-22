"""Combinable statistics of the image QC metrics: moments and co-moments (contract §19).

Each block of a plane gives a count, means and centred sums of squares and products; two
such results combine exactly into the result of the union (Chan, Golub and LeVeque's
pairwise update), so a plane read in blocks gives the whole plane's statistics, up to the
neighbour pairs that cross a block edge (:mod:`.image_qc`).
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Moments:
    """Count, mean and centred sum of squares (``M2``) of a set of values."""

    n: int = 0
    mean: float = 0.0
    m2: float = 0.0

    @classmethod
    def of_sums(cls, n: int, offset: float, total: float, total_sq: float) -> Moments:
        """Return the moments of ``n`` values given the sums of ``value - offset`` and squares."""
        if n == 0:
            return cls()
        shift = total / n
        return cls(n, offset + shift, max(0.0, total_sq - total * shift))

    def merged(self, other: Moments) -> Moments:
        """Return the moments of both sets together."""
        if other.n == 0:
            return self
        if self.n == 0:
            return other
        n = self.n + other.n
        delta = other.mean - self.mean
        mean = self.mean + delta * other.n / n
        return Moments(n, mean, self.m2 + other.m2 + delta * delta * self.n * other.n / n)

    @property
    def variance(self) -> float | None:
        """Population variance, or None for no values."""
        return None if self.n == 0 else self.m2 / self.n


@dataclass(frozen=True)
class CoMoments:
    """Count, means and centred sums of squares and products of paired values ``(a, b)``."""

    n: int = 0
    mean_a: float = 0.0
    mean_b: float = 0.0
    caa: float = 0.0
    cbb: float = 0.0
    cab: float = 0.0

    @classmethod
    def of_sums(
        cls, n: int, offset: float, sums: tuple[float, float, float, float, float]
    ) -> CoMoments:
        """Return the co-moments from the sums of ``a``, ``b``, ``a²``, ``b²`` and ``ab``.

        The values are offset by ``offset`` (the same constant for ``a`` and ``b``).
        """
        if n == 0:
            return cls()
        sa, sb, saa, sbb, sab = sums
        return cls(
            n,
            offset + sa / n,
            offset + sb / n,
            max(0.0, saa - sa * sa / n),
            max(0.0, sbb - sb * sb / n),
            sab - sa * sb / n,
        )

    def merged(self, other: CoMoments) -> CoMoments:
        """Return the co-moments of both sets of pairs together."""
        if other.n == 0:
            return self
        if self.n == 0:
            return other
        n = self.n + other.n
        weight = self.n * other.n / n
        da, db = other.mean_a - self.mean_a, other.mean_b - self.mean_b
        return CoMoments(
            n,
            self.mean_a + da * other.n / n,
            self.mean_b + db * other.n / n,
            self.caa + other.caa + da * da * weight,
            self.cbb + other.cbb + db * db * weight,
            self.cab + other.cab + da * db * weight,
        )

    @property
    def correlation(self) -> float | None:
        """Pearson correlation of ``a`` and ``b``; None without pairs or when either is constant."""
        if self.n == 0 or self.caa <= 0.0 or self.cbb <= 0.0:
            return None
        return max(-1.0, min(1.0, self.cab / math.sqrt(self.caa * self.cbb)))
