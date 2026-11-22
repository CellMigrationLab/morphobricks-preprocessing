"""The run manifest's ``image_qc`` entry: which image QC table the run wrote (contract §19).

The entry names the table (``image_qc.csv``), its SHA-256, the metrics' version and their
fixed settings, so the next run reuses the earlier rows only with the same table and the same
metric definitions. It is read strictly: anything other than these four keys, with these
types, makes the manifest malformed.
"""

from __future__ import annotations

import re

from .image_qc import QC_VERSION, qc_settings

KEY = "image_qc"
"""The manifest's key; absent in manifests written before the image QC table."""
IMAGE_QC_FILENAME = "image_qc.csv"
"""The image QC table in the output folder."""

_SHA256 = re.compile(r"[0-9a-f]{64}")
_KEYS = frozenset({"file", "sha256", "qc_version", "settings"})


def image_qc_entry(sha256: str) -> dict:
    """Return the manifest entry of an image QC table written by this version.

    Parameters
    ----------
    sha256
        The table's SHA-256.

    Returns
    -------
    dict
    """
    return {
        "file": IMAGE_QC_FILENAME,
        "sha256": sha256,
        "qc_version": QC_VERSION,
        "settings": qc_settings(),
    }


def recorded_image_qc(data: object) -> dict | None:
    """Return a recorded ``image_qc`` entry checked, or None when the manifest has none.

    Parameters
    ----------
    data
        The manifest's value for :data:`KEY`.

    Returns
    -------
    dict or None

    Raises
    ------
    ValueError
        If the entry is not an object with exactly ``file`` (``image_qc.csv``), ``sha256``
        (64 lowercase hexadecimal digits), ``qc_version`` (text) and ``settings`` (an
        object).
    """
    if data is None:
        return None
    if not isinstance(data, dict) or set(data) != _KEYS:
        raise ValueError(f"the {KEY} entry must have exactly the keys {sorted(_KEYS)}")
    sha256, version = data["sha256"], data["qc_version"]
    if data["file"] != IMAGE_QC_FILENAME:
        raise ValueError(f"the {KEY} entry must name {IMAGE_QC_FILENAME}")
    if not isinstance(sha256, str) or _SHA256.fullmatch(sha256) is None:
        raise ValueError(f"the {KEY} entry's sha256 is not a SHA-256")
    if not isinstance(version, str) or not isinstance(data["settings"], dict):
        raise ValueError(f"the {KEY} entry's qc_version must be text and settings an object")
    return dict(data)


def same_metrics(entry: dict | None) -> bool:
    """Return True when a recorded entry's metrics are this version's (version and settings)."""
    return (
        entry is not None
        and entry["qc_version"] == QC_VERSION
        and entry["settings"] == qc_settings()
    )
