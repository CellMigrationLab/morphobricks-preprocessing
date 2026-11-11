"""The physical ``R_*`` checks of an experiment description against the folder (experiment-v1 §8).

What the reader cannot see from one file: do the declared imaging units exist, is each unit
homogeneous, do the image headers agree with the declared channels and image type, are the
mask types declared. Everything is decided from the image **headers** (and the folder walk):
pixels are never examined, nothing is guessed, trimmed or repaired, and a check that cannot be
decided is listed as unchecked instead of passing.

Order within one unit (§8): ``R_UNIT_NOT_HOMOGENEOUS`` is reported first and replaces
``R_CHANNEL_COUNT_MISMATCH`` and ``R_HEADER_CONTRADICTION`` for that unit. An image type that
contradicts the header also replaces them for its unit: the layout cannot be read until the
declaration is right. Homogeneity compares the interpreted axes with ``T`` left out (a
time-lapse may have any number of time points, owner decision Q7), the channel count, the image
type and the raw header name at each C index (equal after NFC, never trimmed).
"""

from __future__ import annotations

import unicodedata
from collections import Counter
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType

from .axis_names import OPEN_AXIS
from .description import Description, Unit
from .discovery import DiscoveryResult
from .image_names import position_id
from .metadata import OME_ZARR, TiffMetadata, header_channel_count, named_axes

UNIT_NOT_FOUND = "R_UNIT_NOT_FOUND"
UNIT_NOT_DECLARED = "R_UNIT_NOT_DECLARED"
CHANNEL_COUNT_MISMATCH = "R_CHANNEL_COUNT_MISMATCH"
UNIT_NOT_HOMOGENEOUS = "R_UNIT_NOT_HOMOGENEOUS"
HEADER_CONTRADICTION = "R_HEADER_CONTRADICTION"
IMAGE_TYPE_CONTRADICTS_HEADER = "R_IMAGE_TYPE_CONTRADICTS_HEADER"
MASK_TYPE_NOT_DECLARED = "R_MASK_TYPE_NOT_DECLARED"
HETEROGENEOUS_LAYOUT = "R_HETEROGENEOUS_LAYOUT_NOT_ENABLED"
MASK_CONTRADICTS_REPRESENTATION = "R_MASK_CONTRADICTS_REPRESENTATION"
PHYSICAL_CODES = frozenset(
    {
        UNIT_NOT_FOUND,
        UNIT_NOT_DECLARED,
        CHANNEL_COUNT_MISMATCH,
        UNIT_NOT_HOMOGENEOUS,
        HEADER_CONTRADICTION,
        IMAGE_TYPE_CONTRADICTS_HEADER,
        MASK_TYPE_NOT_DECLARED,
    }
)
"""The codes of this module (the rest of §8's ``R_*`` belong to publication and the run).

``HETEROGENEOUS_LAYOUT`` (the refusal of units of different image types; different channel
lists are accepted from Stage 3, S3.3; decided from the
description alone) and ``MASK_CONTRADICTS_REPRESENTATION`` (the mask step's) are named here so
the reader's modules hold no code literal; they are not folder checks and are not in the set."""

RGB = "rgb"


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


@dataclass(frozen=True)
class LayoutGroup:
    """Units with one identical layout, for the channel question (owner decision Q3).

    A group whose channels all have header names is confirmed once for all its units. A group
    without header names, or with some missing, is **not** batch-confirmable: each unit needs
    its own confirmation beside its own preview.
    """

    layout: Layout
    units: tuple[UnitKey, ...]

    @property
    def batch_confirmable(self) -> bool:
        """True only for a layout whose channels are all named by the header."""
        return self.layout.named


@dataclass(frozen=True)
class PhysicalReport:
    """The outcome of the physical checks.

    ``issues`` are the refusals. ``layouts`` holds the common layout of each unit that is
    homogeneous and whose image type agrees with its declaration. ``image_type_verified``
    says per unit whether the declared image type was checked against a header that states a
    colour interpretation (TIFF), False for a format that states none (OME-Zarr: it stands on
    the user's confirmation). ``unchecked`` lists images whose header could not be read or
    whose channel count is not stated by the header: they were not checked, which is not a pass.
    ``unnamed`` lists the images that pass without header channel names (:attr:`unnamed`).
    """

    issues: tuple[PhysicalIssue, ...] = ()
    # default_factory: Python 3.11 refuses an unhashable mappingproxy as a dataclass default.
    layouts: Mapping[UnitKey, Layout] = field(default_factory=lambda: MappingProxyType({}))
    image_type_verified: Mapping[UnitKey, bool] = field(
        default_factory=lambda: MappingProxyType({})
    )
    unchecked: tuple[str, ...] = ()
    unnamed: Mapping[UnitKey, Mapping[str, tuple[int, ...]]] = field(
        default_factory=lambda: MappingProxyType({})
    )
    """Per unit that passed, the images whose header names not every channel, each with the C
    positions without a name (all of them when the header names none). Such an image passes
    (§4.3: it needs no evidence, the user's confirmed layout is used) and is listed, not warned."""

    @property
    def codes(self) -> frozenset[str]:
        """The set of codes reported (what conformance compares)."""
        return frozenset(issue.code for issue in self.issues)

    @property
    def ok(self) -> bool:
        """True when nothing is refused. Unchecked images are listed in ``unchecked``."""
        return not self.issues


def nfc(text: str) -> str:
    """Return the NFC form of ``text``, a comparison key only (never stored)."""
    return unicodedata.normalize("NFC", text)


def _name(name: str | None) -> str | None:
    return nfc(name) if name else None


def layout_of(meta: TiffMetadata, roles: Sequence[str | None] | None = None) -> Layout:
    """Return the layout one readable image's header states.

    The channel count is the length of the confirmed ``C`` role when ``roles`` are given, else
    what the header names (the ``C`` axis, or the samples of an RGB image); None when the header
    leaves an axis open and no roles say which dimension is the channel.
    """
    axes = named_axes(meta).replace("T", "")
    if meta.rgb_samples:
        count: int | None = header_channel_count(meta)
    elif roles is not None:
        count = dict(zip(roles, meta.shape, strict=True)).get("C", 1)
    elif OPEN_AXIS in named_axes(meta):
        count = None
    else:
        count = header_channel_count(meta)
    return Layout(count, tuple(meta.raw_channel_names), meta.rgb_samples, axes)


def declared_units(description: Description) -> dict[UnitKey, tuple[str, Unit]]:
    """Return the declared units by source folders, with the repeat ID they belong to."""
    found: dict[UnitKey, tuple[str, Unit]] = {}
    for repeat_id, repeat in description.repeats.items():
        for condition_id, member in repeat.members.items():
            folder = description.conditions[condition_id].source_folder
            for unit in member.technical_repeats.values():
                found[UnitKey(folder, member.source_repeat, unit.source_folder)] = (
                    repeat_id,
                    unit,
                )
    return found


def discovered_units(
    discovery: DiscoveryResult, excluded: Collection[str]
) -> dict[UnitKey, list[str]]:
    """Return the images of each discovered unit (folder labels), without the excluded ones."""
    found: dict[UnitKey, list[str]] = {}
    for leaf in discovery.leaves:
        included = [image for image in leaf.images if image not in excluded]
        if included:  # a folder whose images are all excluded is not a unit
            found.setdefault(UnitKey(*leaf.labels), []).extend(included)
    return found


def _differences(reference: Layout, other: Layout) -> list[tuple[int | None, str]]:
    """Return ``(C index or None, what differs)`` of ``other`` from ``reference``."""
    out: list[tuple[int | None, str]] = []
    if reference.channels != other.channels:
        out.append((None, f"{other.channels} channels, the unit has {reference.channels}"))
    if reference.rgb != other.rgb:
        out.append((None, "image type (colour interpretation) differs"))
    if reference.axes != other.axes:
        out.append((None, f"axes {other.axes!r}, the unit has {reference.axes!r}"))
    for index in range(max(len(reference.names), len(other.names))):
        ours = reference.names[index] if index < len(reference.names) else None
        theirs = other.names[index] if index < len(other.names) else None
        if _name(ours) != _name(theirs):
            out.append((index, f"header name {theirs!r}, the unit has {ours!r}"))
    return out


def _homogeneity(key: UnitKey, layouts: Mapping[str, Layout]) -> tuple[Layout, list[PhysicalIssue]]:
    """Return the unit's common layout and one issue per image and difference from it.

    The common layout is the most frequent one (the first in path order on a tie).
    """
    counts = Counter(layouts.values())
    top = max(counts.values())
    reference = next(layout for layout in layouts.values() if counts[layout] == top)
    issues = [
        PhysicalIssue(UNIT_NOT_HOMOGENEOUS, key, path, position_id(path), index, what)
        for path, layout in layouts.items()
        for index, what in _differences(reference, layout)
    ]
    return reference, issues


def _type_issues(
    key: UnitKey, declared: Unit, layouts: Mapping[str, Layout], metas: Mapping[str, TiffMetadata]
) -> list[PhysicalIssue]:
    """Compare the declared image type with each header that states a colour interpretation."""
    return [
        PhysicalIssue(
            IMAGE_TYPE_CONTRADICTS_HEADER,
            key,
            path,
            position_id(path),
            None,
            f"declared {declared.image_type or 'not rgb'}, the header says "
            f"{'rgb' if layout.rgb else 'not rgb'}",
        )
        for path, layout in layouts.items()
        if metas[path].kind != OME_ZARR and layout.rgb != (declared.image_type == RGB)
    ]


def _quoted(names: Iterable[str | None]) -> str:
    """List header names as read, quoted so outer whitespace shows; ``none`` when empty."""
    shown = [repr(name) if name else "(no name)" for name in names]
    return "[" + ", ".join(shown) + "]" if shown else "none"


def _evidence_issues(
    key: UnitKey, unit: Unit, description: Description, layouts: Mapping[str, Layout]
) -> list[PhysicalIssue]:
    """Return ``R_CHANNEL_COUNT_MISMATCH`` and ``R_HEADER_CONTRADICTION`` for a unit."""
    out = []
    for path, layout in layouts.items():
        fov = position_id(path)
        if layout.channels is not None and layout.channels != len(unit.channels):
            what = (
                f"{layout.channels} channels, the description lists {len(unit.channels)} "
                f"({', '.join(unit.channels)})"
            )
            out.append(PhysicalIssue(CHANNEL_COUNT_MISMATCH, key, path, fov, None, what))
            continue
        if unit.image_type == RGB:  # R, G and B take no header names (§4.4b)
            continue
        for index, (channel, name) in enumerate(zip(unit.channels, layout.names, strict=False)):
            known = description.channels[channel].header_names or ()
            if name and nfc(name) not in {nfc(entry) for entry in known}:
                what = (
                    f"header name {name!r} at channel position {index} (the image's header "
                    f"names: {_quoted(layout.names)}) is not a confirmed name of channel "
                    f"{channel!r}, whose confirmed header names are {_quoted(known)}"
                )
                out.append(PhysicalIssue(HEADER_CONTRADICTION, key, path, fov, index, what))
    return out


def _unnamed(unit: Unit, layouts: Mapping[str, Layout]) -> dict[str, tuple[int, ...]]:
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


def _check_unit(
    key: UnitKey,
    repeat_unit: tuple[str, Unit],
    description: Description,
    paths: Sequence[str],
    metas: Mapping[str, TiffMetadata],
    roles: Mapping[str, Sequence[str | None]],
) -> tuple[list[PhysicalIssue], Layout | None, bool, list[str], dict[str, tuple[int, ...]]]:
    """Check one declared unit that has images.

    Returns the issues, the common layout, verified, the unchecked images and, for a unit
    without issues, the images whose header names not every channel (:func:`_unnamed`).
    """
    _, unit = repeat_unit
    readable = [p for p in paths if p in metas and metas[p].readable]
    layouts = {p: layout_of(metas[p], roles.get(p)) for p in readable}
    unchecked = [p for p in paths if p not in layouts]
    verified = bool(readable) and all(metas[p].kind != OME_ZARR for p in readable)
    if not layouts:
        return [], None, verified, unchecked, {}
    reference, issues = _homogeneity(key, layouts)
    if issues:
        return issues, None, verified, unchecked, {}
    issues = _type_issues(key, unit, layouts, metas)
    if issues:
        return issues, None, verified, unchecked, {}
    issues = _evidence_issues(key, unit, description, layouts)
    if reference.channels is None:  # the header does not say; never a guessed count
        unchecked += [p for p in layouts if p not in unchecked]
    unnamed = {} if issues else _unnamed(unit, layouts)
    return issues, None if issues else reference, verified, unchecked, unnamed


def check_physical(
    description: Description,
    discovery: DiscoveryResult,
    metadata: Iterable[TiffMetadata],
    *,
    excluded: Iterable[str] = (),
    roles: Mapping[str, Sequence[str | None]] | None = None,
    mask_types: Iterable[str] | None = None,
) -> PhysicalReport:
    """Check a description against the experiment folder and the image headers.

    Parameters
    ----------
    description
        A description the strict reader accepted.
    discovery
        The folder walk (:func:`.discovery.discover_experiment`).
    metadata
        The image headers (:func:`.metadata.inspect_images`), with raw channel names.
    excluded
        Images the user left out; they belong to no unit.
    roles
        The confirmed role of each axis by relative path (:attr:`.axes.AxisReview.file_mappings`);
        without them the channel count is read only from headers that name every axis.
    mask_types
        The mask types discovered in the masks folder, or None when masks were not scanned
        (then ``R_MASK_TYPE_NOT_DECLARED`` is not examined). A declared mask with no files is
        valid, and nothing here reads mask pixels (§4.5a).

    Returns
    -------
    PhysicalReport
        Every refusal (the unit's ``R_UNIT_NOT_HOMOGENEOUS`` first, replacing the count and
        header checks of that unit), the common layout of each clean unit, and what was not
        checked.
    """
    metas = {m.relative_path: m for m in metadata}
    declared = declared_units(description)
    found = discovered_units(discovery, set(excluded))
    issues = [PhysicalIssue(UNIT_NOT_FOUND, key) for key in declared if key not in found]
    issues += [PhysicalIssue(UNIT_NOT_DECLARED, key) for key in found if key not in declared]
    layouts: dict[UnitKey, Layout] = {}
    verified: dict[UnitKey, bool] = {}
    unchecked: list[str] = []
    unnamed: dict[UnitKey, Mapping[str, tuple[int, ...]]] = {}
    for key, paths in found.items():
        if key not in declared:
            continue
        found_issues, layout, ok, missing, nameless = _check_unit(
            key, declared[key], description, sorted(paths), metas, roles or {}
        )
        issues += found_issues
        unchecked += missing
        verified[key] = ok
        if nameless:
            unnamed[key] = MappingProxyType(nameless)
        if layout is not None:
            layouts[key] = layout
    if mask_types is not None:
        known = {mask.source_type for mask in description.masks.values()}
        issues += [
            PhysicalIssue(MASK_TYPE_NOT_DECLARED, None, detail=name)
            for name in sorted(set(mask_types) - known)
        ]
    return PhysicalReport(
        tuple(issues),
        MappingProxyType(layouts),
        MappingProxyType(verified),
        tuple(dict.fromkeys(unchecked)),
        MappingProxyType(unnamed),
    )


def layout_groups(layouts: Mapping[UnitKey, Layout]) -> tuple[LayoutGroup, ...]:
    """Group units by identical layout (channel count, raw header names, image type, axes).

    Header names are compared exactly as read: ``DAPI`` and ``dapi`` are two layouts. Only a
    group whose channels are all header-named is batch-confirmable (:class:`LayoutGroup`, Q3).
    """
    groups: dict[Layout, list[UnitKey]] = {}
    for key, layout in sorted(layouts.items(), key=lambda item: tuple(vars(item[0]).values())):
        groups.setdefault(layout, []).append(key)
    return tuple(LayoutGroup(layout, tuple(keys)) for layout, keys in groups.items())
