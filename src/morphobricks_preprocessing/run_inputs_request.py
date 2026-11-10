"""The request resolver's types and pure rules (resolver contract version 1).

``RunInputs.resolve_request`` (:mod:`.run_inputs_resolve`) is the one interpreter of an
experiment description for an analysis run, on every route (a direct brick run and a run
through MorphoFeature alike; MorphoBricks architecture decision of 10 November 2026). This
module holds what it takes and gives, and the rules that need no file:

- a :class:`Request` names canonical channel IDs, masks with their role and, for a ``binary``
  mask used as objects, the explicit ``binary_mask_policy`` (experiment-v1 section 4.5c), the
  plane selection (one plane, or a Z-volume) and the consumer's :class:`Capabilities`;
- a :class:`ResolvedRequest` is immutable and picklable: one :class:`FovSelection` per position
  with the C index of every requested channel in **that** image, the masks as the description
  declares them and the planes to read, plus the :class:`Refusal` list, the coverage of every
  biological repeat (full, partial or unavailable) and the :class:`Provenance`;
- the resolver classifies and refuses; it never labels, converts, reads a pixel or chooses a
  device. A recorded ``binary_mask_policy`` comes back as ``MaskSelection.conversion`` for the
  consumer to apply with the one shared labeller (CellPatcher's), on every route.

Codes (experiment-v1 section 8, as the classes of :mod:`.run_inputs_error`): ``A_SCOPE_INCOMPLETE``
("present in k of n acquisitions", k may be 0, one rule for channels and masks, owner decision
SI1), ``A_MASK_NOT_INSTANCES``, ``A_MASK_UNCHECKED``, ``A_RGB_NOT_SUPPORTED`` and, from the
resolver contract, ``A_VOLUME_NOT_SUPPORTED`` (a volume asked of a consumer without
``volume_support``). A refused request selects nothing: no subset is taken for the user. The
contract's other code, ``A_DESCRIPTION_REQUIRED``, is raised before a ``RunInputs`` exists
(``RunInputs.open``): every run the resolver sees is described.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from .run_inputs_error import RunInputsError

RESOLVER_VERSION = 1
"""The resolver contract version a :class:`Provenance` records."""

OBJECTS = "objects"
REGION = "region"
MASK_ROLES = (OBJECTS, REGION)
"""How a request uses a mask: as an object set (instances) or as a region (any pixel set)."""

CONNECTED_COMPONENTS = "connected_components"
BINARY_MASK_POLICIES = (CONNECTED_COMPONENTS,)
"""The recorded conversions of a ``binary`` mask to objects (experiment-v1 section 4.5c)."""

PLANE = "plane"
VOLUME = "volume"
PLANE_KINDS = (PLANE, VOLUME)
"""What one selection reads: one (t, z) plane, or every Z slice of one time point."""

CPU = "cpu"
CUDA = "cuda"
DEVICES = (CPU, CUDA)
"""The explicit device of a request (owner decision of 10 November 2026): chosen by the user or
the notebook default, recorded in the provenance on every route, never switched silently. The
resolver records it; the consumer refuses ``cuda`` without a GPU."""

FULL = "full"
PARTIAL = "partial"
UNAVAILABLE = "unavailable"
COVERAGE = (FULL, PARTIAL, UNAVAILABLE)
"""A biological repeat's coverage for one requested channel or mask (experiment-v1 section 8)."""

BINARY = "binary"
INSTANCES = "instances"
RGB = "rgb"
CHECKED = "checked"


class VolumeNotSupportedError(RunInputsError):
    """A Z-volume was requested from a consumer that does not declare ``volume_support``.

    The resolver contract's own code: 3-D is enabled later, by its own decision, for the
    consumers that declare the capability; nothing enables it by default.
    """

    code = "A_VOLUME_NOT_SUPPORTED"


@dataclass(frozen=True)
class Capabilities:
    """What the consumer declares it can take; the resolver refuses what it does not.

    Attributes
    ----------
    rgb_support
        True when the consumer declares RGB support (its provider manifest's
        ``rgb_support = true``, experiment-v1 section 4.4b). False refuses ``rgb`` units.
    volume_support
        True when the consumer takes a Z-volume as one selection. False refuses a ``volume``
        request. No consumer declares it today.
    """

    rgb_support: bool = False
    volume_support: bool = False


@dataclass(frozen=True)
class MaskRequest:
    """One mask of the request, by its declared ID.

    Attributes
    ----------
    mask_id
        The mask ID of the description (``masks.<ID>``), never a source type.
    role
        ``objects`` (an object set: the mask must be ``instances``, or converted by the
        recorded policy) or ``region``.
    binary_mask_policy
        The explicit per-request conversion of a ``binary`` mask used as objects
        (``connected_components``), or None. Never a default.
    """

    mask_id: str
    role: str = OBJECTS
    binary_mask_policy: str | None = None


@dataclass(frozen=True)
class Request:
    """What an analysis asks of a described run.

    Attributes
    ----------
    channel_ids
        Canonical channel IDs, each resolved to its C index per image. May be empty.
    masks
        The masks used, with their roles and conversions.
    planes
        ``plane`` (one selection per time point and Z slice, today's behaviour) or
        ``volume`` (one selection per time point holding every Z slice).
    capabilities
        The consumer's declared capabilities.
    device
        ``cpu`` or ``cuda``, the explicit device the kernels run on. Recorded; the resolver
        runs no kernel and does not check that a GPU exists (the consumer refuses, never
        switches).
    """

    channel_ids: tuple[str, ...] = ()
    masks: tuple[MaskRequest, ...] = ()
    planes: str = PLANE
    capabilities: Capabilities = field(default_factory=Capabilities)
    device: str = CPU


@dataclass(frozen=True)
class Refusal:
    """One reason the request is refused, with its Spec code."""

    code: str
    subject: str
    message: str
    files: tuple[str, ...] = ()


@dataclass(frozen=True)
class RepeatCoverage:
    """One biological repeat's coverage of one requested channel or mask."""

    repeat_group: str
    subject: str
    status: str
    present: int
    total: int


@dataclass(frozen=True)
class PlaneSelection:
    """The planes of one read: time point ``t`` and the Z slices, one for a plane."""

    t: int
    z: tuple[int, ...]


@dataclass(frozen=True)
class MaskSelection:
    """One mask as the consumer reads it: ``read_mask(fov, source_type, ...)``.

    ``conversion`` is the recorded ``binary_mask_policy`` the consumer applies with the shared
    labeller (None when the mask is used as declared). ``check_status`` is ``checked``.
    """

    mask_id: str
    source_type: str
    representation: str
    role: str
    check_status: str
    conversion: str | None = None


@dataclass(frozen=True)
class FovSelection:
    """What to read of one position, with its canonical identity.

    ``channel_indices`` holds the C index of each ``channel_ids`` entry **in this image**;
    :meth:`index_of` looks one up. ``image_type`` is the unit's declared type (``rgb`` or None).
    """

    key: tuple[str, str, str, str]
    relative_path: str
    repeat_group: str
    condition_id: str
    technical_repeat_id: str
    image_type: str | None
    channel_ids: tuple[str, ...]
    channel_indices: tuple[int, ...]
    masks: tuple[MaskSelection, ...]
    planes: tuple[PlaneSelection, ...]

    def index_of(self, channel_id: str) -> int:
        """Return the C index of a requested channel in this image.

        Raises
        ------
        KeyError
            For a channel the request did not name.
        """
        try:
            return self.channel_indices[self.channel_ids.index(channel_id)]
        except ValueError:
            raise KeyError(f"{channel_id!r} is not a channel of this request") from None


@dataclass(frozen=True)
class Provenance:
    """What the selection was resolved from, for the consumer's own records.

    ``section_digests`` are the manifest's (``(section, hex)`` pairs); ``description_sha256``
    is provenance, never a cache key (experiment-v1 section 9.3).
    """

    resolver_version: int
    description_sha256: str
    section_digests: tuple[tuple[str, str], ...]
    request: Request


@dataclass(frozen=True)
class ResolvedRequest:
    """The resolver's answer: selections when nothing refuses, else the refusals.

    ``selections`` is empty whenever ``refusals`` is not: a refused request takes no subset.
    ``coverage`` is reported either way, so the orchestrator can record the scope.
    """

    selections: tuple[FovSelection, ...]
    refusals: tuple[Refusal, ...]
    coverage: tuple[RepeatCoverage, ...]
    provenance: Provenance

    @property
    def refused(self) -> bool:
        """True when at least one refusal applies."""
        return bool(self.refusals)

    def require(self) -> tuple[FovSelection, ...]:
        """Return the selections, or raise the first refusal as its error class.

        Raises
        ------
        RunInputsError
            The subclass of the first refusal's code (``error.code``), its message listing
            every refusal.
        """
        if not self.refusals:
            return self.selections
        first = self.refusals[0]
        text = "\n".join(r.message for r in self.refusals)
        raise _error_class(first.code)(text)


def _error_class(code: str) -> type[RunInputsError]:
    """Return the ``RunInputsError`` subclass carrying ``code``."""
    pending = [RunInputsError, *RunInputsError.__subclasses__()]
    while pending:
        cls = pending.pop()
        if cls.code == code:
            return cls
        pending.extend(cls.__subclasses__())
    return RunInputsError


def check_request(request: Request, declared_masks: Mapping[str, str]) -> None:
    """Refuse a malformed request before anything is classified.

    Parameters
    ----------
    request
        The request.
    declared_masks
        Mask ID to declared representation, for the policy rules.

    Raises
    ------
    ValueError
        Channels or masks not given as a tuple (of ``str`` IDs, of :class:`MaskRequest`), a
        capability that is not a ``bool``, a channel ID named twice, a mask ID named twice, a
        role or policy outside the contract, a policy on a mask that is not ``binary`` (nothing
        to convert) or on a ``region`` role, an unknown plane kind, or a device that is neither
        ``cpu`` nor ``cuda``.
    """
    _check_shape(request)
    if len(set(request.channel_ids)) != len(request.channel_ids):
        raise ValueError("a channel ID is requested twice")
    ids = [m.mask_id for m in request.masks]
    if len(set(ids)) != len(ids):
        raise ValueError("a mask ID is requested twice")
    if request.planes not in PLANE_KINDS:
        raise ValueError(f"planes must be one of {PLANE_KINDS}, not {request.planes!r}")
    if request.device not in DEVICES:
        raise ValueError(f"device must be one of {DEVICES}, not {request.device!r}")
    for mask in request.masks:
        if mask.role not in MASK_ROLES:
            raise ValueError(f"mask role must be one of {MASK_ROLES}, not {mask.role!r}")
        if mask.binary_mask_policy is None:
            continue
        if mask.binary_mask_policy not in BINARY_MASK_POLICIES:
            raise ValueError(
                f"binary_mask_policy must be one of {BINARY_MASK_POLICIES}, "
                f"not {mask.binary_mask_policy!r}"
            )
        if mask.role != OBJECTS or declared_masks.get(mask.mask_id, BINARY) != BINARY:
            raise ValueError(
                f"binary_mask_policy applies to a binary mask used as objects; {mask.mask_id!r} "
                f"is {declared_masks.get(mask.mask_id, 'undeclared')} in the role {mask.role!r}"
            )


def _check_shape(request: Request) -> None:
    """Refuse a request whose fields are not the frozen types the result records.

    The request is recorded in the provenance as given, so a list would leave the result
    mutable (and unhashable), and a string would be read as one ID per character.
    """
    if not isinstance(request.channel_ids, tuple) or not all(
        isinstance(c, str) for c in request.channel_ids
    ):
        raise ValueError(f"channel_ids must be a tuple of str, not {request.channel_ids!r}")
    if not isinstance(request.masks, tuple) or not all(
        isinstance(m, MaskRequest) and isinstance(m.mask_id, str) for m in request.masks
    ):
        raise ValueError(f"masks must be a tuple of MaskRequest (str IDs), not {request.masks!r}")
    capabilities = request.capabilities
    if not isinstance(capabilities, Capabilities) or not all(
        isinstance(v, bool) for v in (capabilities.rgb_support, capabilities.volume_support)
    ):
        raise ValueError(f"capabilities must be Capabilities of bool, not {capabilities!r}")


def scope_refusal(subject: str, kind: str, missing: Iterable[str], total: int) -> Refusal | None:
    """Return the ``A_SCOPE_INCOMPLETE`` refusal of a channel or mask, or None when complete.

    Parameters
    ----------
    subject
        The channel or mask ID.
    kind
        ``channel`` or ``mask``, for the message.
    missing
        The images that lack it.
    total
        The images of the run.
    """
    lacking = tuple(sorted(missing))
    if not lacking:
        return None
    present = total - len(lacking)
    shown = "\n".join(f"  - {path}" for path in lacking[:10])
    more = f"\n  ... and {len(lacking) - 10} more" if len(lacking) > 10 else ""
    return Refusal(
        "A_SCOPE_INCOMPLETE",
        subject,
        f"The {kind} {subject!r} is present in {present} of {total} acquisitions; the images "
        f"without it:\n{shown}{more}\nNo subset is taken: select and record an eligible "
        "subset, or complete the data (A_SCOPE_INCOMPLETE).",
        lacking,
    )


def coverage(
    subject: str, repeats: Mapping[str, Iterable[str]], present: Iterable[str]
) -> tuple[RepeatCoverage, ...]:
    """Classify every biological repeat for one subject: full, partial or unavailable.

    Parameters
    ----------
    subject
        The channel or mask ID.
    repeats
        ``repeat_group`` to the images of that repeat.
    present
        The images that have the subject.
    """
    have = set(present)
    found = []
    for group, images in sorted(repeats.items()):
        paths = list(images)
        count = sum(1 for path in paths if path in have)
        status = FULL if count == len(paths) else UNAVAILABLE if count == 0 else PARTIAL
        found.append(RepeatCoverage(group, subject, status, count, len(paths)))
    return tuple(found)


def plane_selections(t_count: int, z_count: int, kind: str) -> tuple[PlaneSelection, ...]:
    """Return the planes to read of one image: per (t, z), or every Z per t for a volume."""
    if kind == VOLUME:
        return tuple(PlaneSelection(t, tuple(range(z_count))) for t in range(t_count))
    return tuple(PlaneSelection(t, (z,)) for t in range(t_count) for z in range(z_count))


def mask_representation_refusal(mask: MaskRequest, representation: str) -> Refusal | None:
    """Return ``A_MASK_NOT_INSTANCES`` for a non-instances mask used as objects without a policy."""
    if mask.role != OBJECTS or representation == INSTANCES or mask.binary_mask_policy:
        return None
    return Refusal(
        "A_MASK_NOT_INSTANCES",
        mask.mask_id,
        f"The mask {mask.mask_id!r} is declared {representation}, so it has no object identity "
        "and cannot be an object set. Ask for the recorded conversion "
        f"binary_mask_policy={CONNECTED_COMPONENTS!r} in this request, or declare the mask "
        "instances (A_MASK_NOT_INSTANCES).",
    )


def unchecked_refusal(mask_id: str, unchecked: Iterable[str], total: int) -> Refusal | None:
    """Return ``A_MASK_UNCHECKED`` naming the files the mask step has not read, or None."""
    files = tuple(sorted(unchecked))
    if not files:
        return None
    return Refusal(
        "A_MASK_UNCHECKED",
        mask_id,
        f"The mask {mask_id!r} is not yet checked against its declared representation for "
        f"{len(files)} of {total} files. Run the masks step on this output folder first "
        "(A_MASK_UNCHECKED).",
        files,
    )


def rgb_refusal(capabilities: Capabilities, rgb_images: Iterable[str]) -> Refusal | None:
    """Return ``A_RGB_NOT_SUPPORTED`` when ``rgb`` units are requested without the capability."""
    images = tuple(sorted(rgb_images))
    if not images or capabilities.rgb_support:
        return None
    return Refusal(
        "A_RGB_NOT_SUPPORTED",
        RGB,
        f"{len(images)} images belong to units declared rgb and this consumer does not declare "
        "rgb_support; nothing is converted to grey or split (A_RGB_NOT_SUPPORTED).",
        images,
    )


def volume_refusal(request: Request) -> Refusal | None:
    """Return ``A_VOLUME_NOT_SUPPORTED`` for a volume asked without ``volume_support``."""
    if request.planes != VOLUME or request.capabilities.volume_support:
        return None
    return Refusal(
        "A_VOLUME_NOT_SUPPORTED",
        VOLUME,
        "A Z-volume selection was requested and this consumer does not declare volume_support; "
        "ask for planes (A_VOLUME_NOT_SUPPORTED).",
    )
