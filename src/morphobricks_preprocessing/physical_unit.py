"""One unit's images as their headers state them: the values the physical checks share.

The key of an imaging unit, one refusal, one image's layout, the image-type test against a
declaration and the readable images of a unit with their effective type (experiment-v1 sections
4.4b and 8). Used by the strict checks (:mod:`.physical_check`) and the fast-mode checks
(:mod:`.physical_fast`, Spec ADR 0006). Headers only: no pixel is read here.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .axis_names import OPEN_AXIS
from .description import Unit
from .fast_layout import Contradiction
from .image_names import position_id
from .metadata import OME_ZARR, TiffMetadata, header_channel_count, named_axes
from .rgb_override import OVERRIDE_SAMPLES, RGB, declares_override, effectively_rgb

UNIT_NOT_HOMOGENEOUS = "R_UNIT_NOT_HOMOGENEOUS"
IMAGE_TYPE_CONTRADICTS_HEADER = "R_IMAGE_TYPE_CONTRADICTS_HEADER"
RGB_OVERRIDE_NOT_THREE_SAMPLES = "R_RGB_OVERRIDE_NOT_THREE_SAMPLES"


@dataclass(frozen=True)
class UnitKey:
    """An imaging unit by its source folder labels (never by canonical IDs)."""

    condition: str
    biological_repeat: str
    technical_repeat: str


@dataclass(frozen=True)
class PhysicalIssue:
    """One refusal: its code, the unit, and where it was found.

    ``path`` is the image (relative to the experiment root) and ``fov`` its position ID;
    ``c_index`` the source channel index, None when the issue is not about one channel. For
    ``R_MASK_TYPE_NOT_DECLARED`` ``unit`` is None and ``detail`` is the mask type.
    """

    code: str
    unit: UnitKey | None
    path: str | None = None
    fov: str | None = None
    c_index: int | None = None
    detail: str = ""
    contradictions: tuple[Contradiction, ...] = ()
    """For ``R_FAST_LAYOUT_CONTRADICTION``: every contradiction of the image with the experiment
    list, in the order of the kinds (:mod:`.fast_layout`); empty for every other code."""


@dataclass(frozen=True)
class Layout:
    """What one image's header says about its channels: the unit's signature when all agree.

    ``channels`` is None when the header does not name the channel axis and no confirmed
    roles were given, so the count is not known. ``names`` are the raw header names by C
    index, as read (None where there is none).
    """

    channels: int | None
    names: tuple[str | None, ...]
    rgb: bool
    axes: str

    @property
    def named(self) -> bool:
        """True when every channel has a header name."""
        return bool(self.channels) and len(self.names) == self.channels and all(self.names)


def layout_of(
    meta: TiffMetadata, roles: Sequence[str | None] | None = None, *, rgb: bool = False
) -> Layout:
    """Return the layout one readable image's header states.

    The channel count is the length of the confirmed ``C`` role when ``roles`` are given, else
    what the header names (the ``C`` axis, or the samples of an RGB image); None when the header
    leaves an axis open and no roles say which dimension is the channel. ``rgb`` reads the image
    as RGB whatever its header's colour interpretation: its effective type after a valid RGB
    header override (:func:`.rgb_override.effectively_rgb`), so that images differing only in
    colour interpretation are homogeneous (experiment-v1 4.4b).
    """
    axes = named_axes(meta).replace("T", "")
    if meta.rgb_samples or rgb:
        count: int | None = header_channel_count(meta)
    elif roles is not None:
        count = dict(zip(roles, meta.shape, strict=True)).get("C", 1)
    elif OPEN_AXIS in named_axes(meta):
        count = None
    else:
        count = header_channel_count(meta)
    return Layout(count, tuple(meta.raw_channel_names), meta.rgb_samples or rgb, axes)


def type_issue(declared: Unit, layout: Layout, meta: TiffMetadata) -> tuple[str, str] | None:
    """Return the code and text of one image whose header contradicts its unit, else None.

    A header that does not say RGB under a unit declared ``rgb`` with the override is accepted
    when the image has exactly three interleaved samples (RH1), else it is
    ``R_RGB_OVERRIDE_NOT_THREE_SAMPLES``. Only the header is read.
    """
    if meta.kind == OME_ZARR or layout.rgb == (declared.image_type == RGB):
        return None
    three = meta.interleaved_samples == OVERRIDE_SAMPLES
    if declares_override(declared):
        if three:
            return None
        samples = meta.interleaved_samples or "no"
        return RGB_OVERRIDE_NOT_THREE_SAMPLES, (
            f"rgb_header_override needs exactly {OVERRIDE_SAMPLES} interleaved samples per "
            f"pixel; the header states {samples} interleaved samples "
            f"(photometric {meta.photometric or 'not stated'})"
        )
    what = (
        f"declared {declared.image_type or 'not rgb'}, the header says "
        f"{'rgb' if layout.rgb else 'not rgb'}"
    )
    if declared.image_type == RGB and three:
        what += (
            f" (photometric {meta.photometric or 'not stated'}) but stores "
            f"{OVERRIDE_SAMPLES} interleaved samples per pixel: if they are R, G and B in this "
            "order, declare rgb_header_override = true on this unit (experiment-v1 4.4b)"
        )
    return IMAGE_TYPE_CONTRADICTS_HEADER, what


def type_issues(
    key: UnitKey, declared: Unit, layouts: Mapping[str, Layout], metas: Mapping[str, TiffMetadata]
) -> list[PhysicalIssue]:
    """Compare the declared image type with each header that states a colour interpretation."""
    found = ((path, type_issue(declared, layout, metas[path])) for path, layout in layouts.items())
    return [
        PhysicalIssue(issue[0], key, path, position_id(path), None, issue[1])
        for path, issue in found
        if issue is not None
    ]


def unnamed_positions(unit: Unit, layouts: Mapping[str, Layout]) -> dict[str, tuple[int, ...]]:
    """Return per image the declared channel positions its header gives no name (not RGB)."""
    if unit.image_type == RGB:  # R, G and B take no header names (§4.4b)
        return {}
    found = {
        path: tuple(
            i for i in range(len(unit.channels)) if i >= len(layout.names) or not layout.names[i]
        )
        for path, layout in layouts.items()
    }
    return {path: positions for path, positions in found.items() if positions}


@dataclass
class UnitImages:
    """The readable images of one unit: effective layouts, unchecked images, verified."""

    layouts: dict[str, Layout]
    unchecked: list[str]
    verified: bool
    samples: list[PhysicalIssue]
    """``R_RGB_OVERRIDE_NOT_THREE_SAMPLES``: images that break the override's condition."""


def unit_images(
    key: UnitKey,
    unit: Unit,
    paths: Sequence[str],
    metas: Mapping[str, TiffMetadata],
    roles: Mapping[str, Sequence[str | None]],
) -> UnitImages:
    """Read one unit's images' layouts from their headers, with the effective image type.

    An image the override lifts is read as RGB (4.4b); an image that breaks the override's
    condition is in ``samples`` and not in ``layouts``.
    """
    readable = [p for p in paths if p in metas and metas[p].readable]
    layouts = {p: layout_of(metas[p], roles.get(p)) for p in readable}
    unchecked = [p for p in paths if p not in layouts]
    verified = bool(readable) and all(metas[p].kind != OME_ZARR for p in readable)
    samples = [
        issue
        for issue in type_issues(key, unit, layouts, metas)
        if issue.code == RGB_OVERRIDE_NOT_THREE_SAMPLES
    ]
    broken = {issue.path for issue in samples}
    effective = {
        p: layout_of(metas[p], roles.get(p), rgb=True) if effectively_rgb(unit, metas[p]) else lay
        for p, lay in layouts.items()
        if p not in broken
    }
    return UnitImages(effective, unchecked, verified, samples)
