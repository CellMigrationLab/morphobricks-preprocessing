"""The planes whose central intensity range covered too few bins (contract §4, §9).

Recorded in the manifest so that a later run keeping their rows still reports them. Moved
from ``manifest.py`` (PP-4).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
from morphobricks_normalization.ranges import RangeCheck


@dataclass(frozen=True)
class NarrowRange:
    """A channel plane whose central intensity range covers too few histogram bins.

    Attributes
    ----------
    relative_path
        The image the plane belongs to.
    plane_id
        The plane's ``plane_id`` in the table.
    channel
        The zero-based channel.
    check
        The normalization brick's ``RangeCheck`` of the plane's channel.
    """

    relative_path: str
    plane_id: str
    channel: int
    check: RangeCheck

    def to_dict(self) -> dict:
        """Return the entry as JSON-compatible data."""
        check = {name: _plain(value) for name, value in asdict(self.check).items()}
        return {
            "relative_path": self.relative_path,
            "plane_id": self.plane_id,
            "channel": self.channel,
            **check,
        }

    @classmethod
    def from_dict(cls, data: dict) -> NarrowRange:
        """Read an entry written by :meth:`to_dict`."""
        return cls(
            relative_path=str(data["relative_path"]),
            plane_id=str(data["plane_id"]),
            channel=int(data["channel"]),
            check=RangeCheck(
                histogram_range=str(data["histogram_range"]),
                central_percentiles=tuple(float(v) for v in data["central_percentiles"]),
                central_bins=int(data["central_bins"]),
                robust_quantiles=tuple(float(v) for v in data["robust_quantiles"]),
                robust_limits=tuple(data["robust_limits"]),
                outside_robust_count=int(data["outside_robust_count"]),
                robust_central_bins=int(data["robust_central_bins"]),
                min_central_bins=int(data["min_central_bins"]),
            ),
        )


def _plain(value):  # noqa: ANN001, ANN202
    """Return a range check value as JSON data: Python numbers, lists for tuples."""
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    if isinstance(value, np.generic):
        return value.item()
    return value
