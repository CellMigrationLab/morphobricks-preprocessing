"""The OME metadata of a TIFF header: channel names (as read and trimmed) and significant bits.

Moved from ``metadata.py``; the names are read by :func:`.zarr_input.read_channel_names`
(trimmed, for the checks) and :func:`.zarr_input.raw_channel_names` (as the header gave them).
"""

from __future__ import annotations

import logging
from xml.etree import ElementTree

import tifffile

from .zarr_input import raw_channel_names, read_channel_names

logger = logging.getLogger(__name__)


def ome_details(tif: tifffile.TiffFile) -> dict:
    """Channel names and significant bits of the first OME image, if present."""
    if not tif.is_ome:
        return {}
    try:
        root = ElementTree.fromstring(tif.ome_metadata)
    except (ElementTree.ParseError, TypeError, ValueError):
        logger.debug("Cannot parse the OME metadata of %s", tif.filename, exc_info=True)
        return {}
    pixels = next((e for e in root.iter() if _local_name(e.tag) == "Pixels"), None)
    if pixels is None:
        return {}
    raw = [channel.get("Name") for channel in pixels if _local_name(channel.tag) == "Channel"]
    names, trimmed = read_channel_names(raw)
    bits = pixels.get("SignificantBits")
    return {
        "channel_names": names,
        "channel_names_trimmed": trimmed,
        "raw_channel_names": raw_channel_names(raw),
        "significant_bits": int(bits) if bits and bits.isdigit() else None,
    }


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]
