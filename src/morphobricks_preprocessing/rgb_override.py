"""The RGB header override of a unit, declared and effective (Spec experiment-v1 4.4b, RH1).

A unit declared ``rgb`` may declare ``rgb_header_override = true``. The override is **used** by
an image whose TIFF header does not say RGB (it is *lifted*): such an image must have exactly
three interleaved samples per pixel, and its effective image type is then ``rgb``. It is
**unused** by an image whose header already says RGB (header-verified) and by an image whose
format states no colour interpretation (OME-Zarr: not header-verified, as without the key).
Declared and effective are recorded separately (owner answers of 14 November 2026). Only the
header is read here; pixels are never examined.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from .description import Unit
from .metadata import OME_ZARR, TiffMetadata

RGB = "rgb"
OVERRIDE_SAMPLES = 3
"""The interleaved samples per pixel an RGB header override requires (experiment-v1 4.4b, RH1)."""
USED, UNUSED = "used", "unused"
"""Whether the override lifted an image's header."""
HEADER_RGB = "header_already_rgb"
NO_INTERPRETATION = "no_colour_interpretation"
"""Why the override is unused for an image: its header already says RGB (header-verified), or
its format states no colour interpretation (OME-Zarr; not header-verified)."""
NOT_STATED = "not stated"
"""Stands for a TIFF photometric value ``tifffile`` does not name."""


@dataclass(frozen=True)
class OverrideUse:
    """What a declared override did for one image.

    ``use`` is :data:`USED` or :data:`UNUSED`; ``reason`` is None when used, else
    :data:`HEADER_RGB` or :data:`NO_INTERPRETATION`; ``photometric`` is the image's own header
    colour interpretation (kept as evidence), None for a format that states none.
    """

    use: str
    reason: str | None
    photometric: str | None


def declares_override(unit: Unit) -> bool:
    """Return whether the unit declares ``rgb`` with ``rgb_header_override = true``."""
    return unit.image_type == RGB and unit.rgb_header_override is True


def lifted(meta: TiffMetadata) -> bool:
    """Return whether a declared override must lift this image's header.

    True for a TIFF header that does not say RGB; False for one that does and for a format that
    states no colour interpretation (OME-Zarr). The three-sample requirement applies to exactly
    these images.
    """
    return meta.kind != OME_ZARR and not meta.rgb_samples


def effectively_rgb(unit: Unit, meta: TiffMetadata) -> bool:
    """Return whether the override makes this image's effective type ``rgb`` (a valid lift)."""
    return declares_override(unit) and lifted(meta) and meta.interleaved_samples == OVERRIDE_SAMPLES


def override_use(meta: TiffMetadata) -> OverrideUse:
    """Return what a declared override did for one image the checks accepted."""
    if meta.kind == OME_ZARR:
        return OverrideUse(UNUSED, NO_INTERPRETATION, None)
    if meta.rgb_samples:
        return OverrideUse(UNUSED, HEADER_RGB, meta.photometric or NOT_STATED)
    return OverrideUse(USED, None, meta.photometric or NOT_STATED)


def override_uses(
    unit: Unit, paths: Iterable[str], metas: Mapping[str, TiffMetadata]
) -> dict[str, OverrideUse]:
    """Return per readable image of a unit declaring the override what it did; else empty."""
    if not declares_override(unit):
        return {}
    readable = (p for p in paths if p in metas and metas[p].readable)
    return {path: override_use(metas[path]) for path in sorted(readable)}


def header_overridden(
    declared: Unit, paths: Iterable[str], metas: Mapping[str, TiffMetadata]
) -> str | None:
    """Return the colour interpretation a unit's used header override names, or None.

    None unless the unit declares the override and it is used by one of its images (a header
    that says RGB has nothing to override; an OME-Zarr header states no colour interpretation).
    The caller has checked the images; unreadable images are left out. Distinct
    interpretations are joined in sorted order with ``", "``; :data:`NOT_STATED` stands for a
    value ``tifffile`` does not name.
    """
    names = {
        use.photometric or NOT_STATED
        for use in override_uses(declared, paths, metas).values()
        if use.use == USED
    }
    return ", ".join(sorted(names)) or None
