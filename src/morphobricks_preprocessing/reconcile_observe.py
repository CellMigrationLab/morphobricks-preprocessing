"""The units a folder walk found, as :func:`.description_reconcile.reconcile` takes them.

How many images each unit has now and, when the caller gives the image headers already read
(the preflight's), the channel evidence those headers state: the channel count and the raw
header names by C index (:func:`.physical_check.layout_of`, without axis roles: a count the
header leaves open stays unknown), whether they state a colour (RGB) image and whether every
image whose header does not say RGB stores three interleaved samples per pixel (the RGB header
override's condition, RH1). A unit whose readable headers disagree with each other is marked
not uniform; when they differ only in colour interpretation it is also marked uniform as RGB
(one layout under a declared override, experiment-v1 4.4b). Nothing is read
from an image here, and no unit is renamed or matched by similarity.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable

from .description_reconcile import ObservedUnit, UnitFolders
from .discovery import DiscoveryResult
from .metadata import OME_ZARR, TiffMetadata
from .physical_check import layout_of
from .rgb_override import OVERRIDE_SAMPLES, lifted


def _evidence(paths: Iterable[str], metas: dict[str, TiffMetadata], count: int) -> ObservedUnit:
    """Return one unit's observation from the headers of its readable images."""
    readable = [metas[p] for p in paths if p in metas and metas[p].readable]
    layouts = {layout_of(meta) for meta in readable}
    if not layouts:
        return ObservedUnit(count)  # no header read: no evidence, never assumed unchanged
    three = all(m.interleaved_samples == OVERRIDE_SAMPLES for m in readable if lifted(m))
    if len(layouts) > 1:
        as_rgb = {layout_of(meta, rgb=lifted(meta)) for meta in readable}
        if not three or len(as_rgb) > 1:
            return ObservedUnit(count, uniform=False)
        (layout,) = as_rgb  # differs only in colour interpretation: one layout if overridden
        names = layout.names or None
        return ObservedUnit(
            count, layout.channels, names, False, rgb=False, three_samples=True, uniform_as_rgb=True
        )
    (layout,) = layouts
    rgb = None if any(meta.kind == OME_ZARR for meta in readable) else layout.rgb
    return ObservedUnit(count, layout.channels, layout.names or None, rgb=rgb, three_samples=three)


def observe_folders(
    discovery: DiscoveryResult,
    excluded: Collection[str] = (),
    metadata: Iterable[TiffMetadata] | None = None,
) -> dict[UnitFolders, ObservedUnit]:
    """Return each unit of the walk with its image count, leaving out the images ``excluded``.

    A folder whose images are all excluded is not a unit (it is a removed one if published).
    Without ``metadata`` the channel evidence of every unit is left unknown (None), never
    assumed; with it, each unit carries what its headers state.
    """
    found: dict[UnitFolders, list[str]] = {}
    for leaf in discovery.leaves:
        images = [image for image in leaf.images if image not in excluded]
        if images:
            found.setdefault(UnitFolders(*leaf.labels), []).extend(images)
    if metadata is None:
        return {key: ObservedUnit(len(paths)) for key, paths in sorted(found.items())}
    metas = {meta.relative_path: meta for meta in metadata}
    return {key: _evidence(paths, metas, len(paths)) for key, paths in sorted(found.items())}
