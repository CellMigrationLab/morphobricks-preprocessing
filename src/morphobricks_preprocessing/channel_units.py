"""The imaging units of an experiment as the channel screen shows them (headers only).

One :class:`.channel_labels.UnitLayout` per unit: the first image of the unit by sorted path is its
representative, the channel count comes from the axis roles the user confirmed (a colour image
has three samples), and the raw header names are read exactly as the header holds them. No pixel
is read here and nothing is inferred from the image content.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable

from .axes import AxisReview
from .channel_labels import UnitLayout
from .discovery import DiscoveryResult
from .draft_answers import UnitKey
from .metadata import MetadataReport, TiffMetadata, header_channel_count


def channel_count(meta: TiffMetadata, review: AxisReview) -> int | None:
    """Return the channels of one image under the confirmed roles; None when they do not fit.

    A colour (RGB) image has its three samples; otherwise the length of the ``C`` role, and one
    channel when the file has no ``C`` axis.
    """
    mapping = review.file_mappings.get(meta.relative_path)
    if mapping is None or meta.relative_path in review.axis_error_files:
        return None
    if meta.rgb_samples:
        return header_channel_count(meta)
    index = mapping.index_of("C")
    return 1 if index is None else int(meta.shape[index])


def slot_count(metadata: MetadataReport, review: AxisReview) -> int:
    """Return the largest channel count of any non-colour image (0 if none)."""
    counts = [
        channel_count(f, review) or 0 for f in metadata.files if f.readable and not f.rgb_samples
    ]
    return max(counts, default=0)


def header_names(metadata: MetadataReport) -> list[str]:
    """Return every raw channel name any readable image's header gives, once each, in order."""
    names = (n for f in metadata.files if f.readable for n in f.raw_channel_names if n)
    return list(dict.fromkeys(names))


def unit_images(
    discovery: DiscoveryResult, excluded: Collection[str]
) -> dict[UnitKey, tuple[str, ...]]:
    """Return the images to analyse of every unit, sorted by path."""
    found: dict[UnitKey, list[str]] = {}
    for leaf in discovery.leaves:
        key = (leaf.condition, leaf.biological_repeat, leaf.technical_repeat)
        found.setdefault(key, []).extend(i for i in leaf.images if i not in excluded)
    return {key: tuple(sorted(images)) for key, images in found.items() if images}


def unit_layouts(
    keys: Iterable[UnitKey],
    discovery: DiscoveryResult,
    excluded: Collection[str],
    metadata: MetadataReport,
    review: AxisReview,
) -> tuple[UnitLayout, ...]:
    """Return one layout per unit key, from the unit's first image by sorted path.

    A unit whose first image cannot be read, or does not fit the confirmed roles, is returned
    with a ``problem`` and no channels: the next image is **not** used instead.
    """
    images = unit_images(discovery, excluded)
    headers = {f.relative_path: f for f in metadata.files}
    out = []
    for key in sorted(keys):
        found = images.get(key, ())
        if not found:
            out.append(UnitLayout(key, None, problem="the unit has no image to analyse"))
            continue
        first = found[0]
        meta = headers.get(first)
        count = channel_count(meta, review) if meta is not None and meta.readable else None
        if count is None:
            out.append(
                UnitLayout(
                    key,
                    first,
                    problem=f"{first} cannot be read or does not fit the confirmed axis roles",
                    images=len(found),
                )
            )
            continue
        out.append(
            UnitLayout(
                key, first, count, tuple(meta.raw_channel_names), meta.rgb_samples, "", len(found)
            )
        )
    return tuple(out)


__all__ = ["channel_count", "header_names", "slot_count", "unit_images", "unit_layouts"]
