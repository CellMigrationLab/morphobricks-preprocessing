"""The request resolver's types and pure rules (resolver contract version 3).

``RunInputs.resolve_request`` (:mod:`.run_inputs_resolve`) is the one interpreter of an
experiment description for an analysis run, on every route (a direct brick run and a run
through MorphoFeature alike; MorphoBricks architecture decision of 10 November 2026). This
module holds what it takes and gives, and the rules that need no file:

- a :class:`Request` names canonical channel IDs, masks with their role and, for a ``binary``
  mask used as objects, the explicit ``binary_mask_policy`` (experiment-v1 section 4.5c), the
  plane selection (one plane, or a Z-volume) and the consumer's :class:`Capabilities`. A mask
  request is identified by ``(mask_id, role)``: a consumer sends one entry per use, a mask ID
  may appear in several, entries of one ``(mask_id, role)`` with the same conversion collapse
  into one selection, and the ``objects`` use of one mask ID with two different conversions is
  a ``ValueError`` (contract version 3, Spec ADR 0005);
- a :class:`ResolvedRequest` is immutable and picklable: one :class:`FovSelection` per position
  with the C index of every requested channel in **that** image, the masks as the description
  declares them and the planes to read, plus the :class:`Refusal` list, the coverage of every
  biological repeat (full, partial or unavailable) and the :class:`Provenance`;
- the resolver classifies and refuses; it never labels, converts, reads a pixel or chooses a
  device. A recorded ``binary_mask_policy`` comes back as ``MaskSelection.conversion`` and as
  one :class:`MaskConversion` record in ``Provenance.mask_conversions``; the one helper
  ``RunInputs.read_objects`` applies it (8-connected, :mod:`.masks.binary`), on every route. A
  conversion on a position with more than one Z plane is ``A_VOLUME_NOT_SUPPORTED``.
- a ``parent_labels`` mask (experiment-v1 section 4.5b, resolver contract version 2, Spec ADR
  0002) is never an object set (``A_MASK_NOT_INSTANCES`` with or without the capability). In the
  role ``region`` it is selected as the groups for a consumer that declares
  ``parent_labels_support``, else with ``conversion = "parent_labels_as_area"``: that consumer
  reads every non-zero pixel as inside, records the marker and shows it. Never split or relabelled.

Codes (experiment-v1 section 8, as the classes of :mod:`.run_inputs_error`): ``A_SCOPE_INCOMPLETE``
("present in k of n acquisitions", k may be 0, one rule for channels and masks, owner decision
SI1), ``A_MASK_NOT_INSTANCES`` (built by :func:`.mask_rules.refuse_not_instances`),
``A_MASK_UNCHECKED``, ``A_RGB_NOT_SUPPORTED`` and, from the resolver contract,
``A_VOLUME_NOT_SUPPORTED`` (a volume asked of a consumer without ``volume_support``, and a
conversion of a Z-stack; built only by :func:`volume_refusals`). A refused request selects
nothing: no subset is taken for the user. The contract's other code,
``A_DESCRIPTION_REQUIRED``, is raised before a ``RunInputs`` exists (``RunInputs.open``): every
run the resolver sees is described.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from .mask_requests import (
    CONNECTED_COMPONENTS,
    OBJECTS,
    MaskConversion,
    mask_uses,
    objects_conflict,
)
from .mask_requests import LABELLER as LABELLER  # re-exported: the record's names
from .mask_requests import LABELLER_CONNECTIVITY as LABELLER_CONNECTIVITY
from .mask_requests import conversion_record as conversion_record
from .mask_requests import mask_ids as mask_ids
from .mask_rules import refuse_not_instances
from .run_inputs_error import RunInputsError

RESOLVER_VERSION = 3
"""The resolver contract version a :class:`Provenance` records: 3 since Spec ADR 0005 (the
conversion record ``Provenance.mask_conversions``, the refusal of a conversion on a Z-stack, and
mask requests identified by ``(mask_id, role)``; Spec ADR 0002 "Resolver contract version 3").
Version 2 added parent-label masks."""

REGION = "region"
MASK_ROLES = (OBJECTS, REGION)
"""How a request uses a mask: as an object set (instances) or as a region (any pixel set)."""

BINARY_MASK_POLICIES = (CONNECTED_COMPONENTS,)
"""The recorded conversions of a ``binary`` mask to objects (experiment-v1 section 4.5c)."""
PARENT_LABELS_AS_AREA = "parent_labels_as_area"
"""The recorded marker of a ``parent_labels`` mask used as a region by a consumer without
``parent_labels_support`` (experiment-v1 section 4.5b): every non-zero pixel is inside, 0 is
outside, the groups are not kept. Not a request field: the resolver sets it."""

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
PARENT_LABELS = "parent_labels"
RGB = "rgb"
CHANNELS = "channels"
IMAGE_TYPES = (CHANNELS, RGB)
"""A unit's declared image type: ``rgb``, or ``channels`` for an ordinary channel image (the
description's absent ``image_type``, experiment-v1 section 4.4b). Never read from a header."""
CHECKED = "checked"


class VolumeNotSupportedError(RunInputsError):
    """A Z-volume was requested without ``volume_support``, or a Z-stack would be converted.

    The resolver contract's own code: 3-D is enabled later, by its own decision, for the
    consumers that declare the capability; nothing enables it by default. A
    ``binary_mask_policy`` on a position with more than one Z plane is refused with it too,
    with or without the capability (Spec ADR 0005 BM5): the labeller labels 2-D planes only.
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
    parent_labels_support
        True when the consumer reads the groups of a ``parent_labels`` mask (its provider
        manifest's ``parent_labels_support = true``, experiment-v1 section 4.5b, with the rules
        of ``rgb_support``). False: such a mask in the role ``region`` is selected with the
        marker ``conversion = "parent_labels_as_area"``. Either way it is never an object set.
    """

    rgb_support: bool = False
    volume_support: bool = False
    parent_labels_support: bool = False


@dataclass(frozen=True)
class MaskRequest:
    """One use of a mask in the request, by its declared ID.

    Identified by ``(mask_id, role)`` (contract version 3): a consumer sends one entry per use
    (each object set, each region or fraction use, a fraction being a ``region``) and does not
    merge them first; the resolver validates them together.

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
    """One ``(mask_id, role)`` of the request as the consumer reads it.

    The ``objects`` one is read by ``RunInputs.read_objects(selection, mask_id, t, z)``, which
    applies its ``conversion``; a ``region`` one by ``read_mask(fov, source_type, ...)`` or
    ``read_mask_window``. ``conversion`` is the recorded ``binary_mask_policy`` (``objects``
    only), or ``parent_labels_as_area`` for a ``parent_labels`` region read as an area by a
    consumer without ``parent_labels_support`` (it records the marker and shows it); None when
    the mask is used as declared. ``representation`` is always the declared one.
    ``check_status`` is ``checked``.
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
    :meth:`index_of` looks one up. ``image_type`` is the unit's declared type, ``rgb`` or
    ``channels`` (:data:`IMAGE_TYPES`), the same as the position's ``Fov.image_type``.
    """

    key: tuple[str, str, str, str]
    relative_path: str
    repeat_group: str
    condition_id: str
    technical_repeat_id: str
    image_type: str
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
    is provenance, never a cache key (experiment-v1 section 9.3). ``mask_conversions`` holds one
    :class:`MaskConversion` per mask ID whose selection has a conversion, in request order of
    that selection (empty for a refused request, which selects nothing); a consumer copies
    ``[c.as_record() for c in mask_conversions]`` under ``mask_conversions`` in its run record,
    with no key added, renamed, dropped or converted.
    """

    resolver_version: int
    description_sha256: str
    section_digests: tuple[tuple[str, str], ...]
    request: Request
    mask_conversions: tuple[MaskConversion, ...] = ()


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
        capability that is not a ``bool``, a channel ID named twice, a role or policy outside
        the contract, a policy on a mask that is not ``binary`` (nothing to convert; for a
        ``parent_labels`` mask the message says it is never converted) or on a ``region``
        role, one mask ID requested as ``objects`` with two different conversions (a policy
        and none count as two; contract version 3, :func:`objects_conflict`), an unknown plane
        kind, or a device that is neither ``cpu`` nor ``cuda``. A mask ID may appear in several
        entries (one per use).
    """
    _check_shape(request)
    if len(set(request.channel_ids)) != len(request.channel_ids):
        raise ValueError("a channel ID is requested twice")
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
        if declared_masks.get(mask.mask_id) == PARENT_LABELS:
            raise ValueError(
                f"binary_mask_policy cannot be applied to {mask.mask_id!r}: it is declared "
                "parent_labels (each non-zero value one group, in any number of pieces), which "
                "is never split, relabelled or converted to objects (experiment-v1 section "
                "4.5b). Remove the policy; use the mask in the role 'region'."
            )
        if mask.role != OBJECTS or declared_masks.get(mask.mask_id, BINARY) != BINARY:
            raise ValueError(
                f"binary_mask_policy applies to a binary mask used as objects; {mask.mask_id!r} "
                f"is {declared_masks.get(mask.mask_id, 'undeclared')} in the role {mask.role!r}"
            )
    objects_conflict(request.masks)


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
        isinstance(v, bool)
        for v in (
            capabilities.rgb_support,
            capabilities.volume_support,
            capabilities.parent_labels_support,
        )
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
    """Return ``A_MASK_NOT_INSTANCES`` for a non-instances mask used as objects without a policy.

    A ``parent_labels`` mask as objects is refused with or without ``parent_labels_support``,
    and its message offers no conversion: there is none (experiment-v1 section 4.5b). This
    decides only; the error and its message come from the one constructor,
    :func:`.mask_rules.refuse_not_instances`.
    """
    if mask.role != OBJECTS or representation == INSTANCES or mask.binary_mask_policy:
        return None
    error = refuse_not_instances(mask.mask_id, declared=representation)
    return Refusal(str(error.code), mask.mask_id, str(error))


def mask_conversion(
    mask: MaskRequest, representation: str, capabilities: Capabilities
) -> str | None:
    """Return the ``MaskSelection.conversion`` of an accepted mask request, or None.

    The recorded ``binary_mask_policy`` when the request gives one; ``parent_labels_as_area``
    for a ``parent_labels`` mask in the role ``region`` when the consumer does not declare
    ``parent_labels_support`` (experiment-v1 section 4.5b); otherwise None (used as declared).
    """
    if mask.binary_mask_policy is not None:
        return mask.binary_mask_policy
    if (
        representation == PARENT_LABELS
        and mask.role == REGION
        and not capabilities.parent_labels_support
    ):
        return PARENT_LABELS_AS_AREA
    return None


def unchecked_refusal(
    mask_id: str, unchecked: Iterable[str], total: int, *, stale: bool = False
) -> Refusal | None:
    """Return ``A_MASK_UNCHECKED`` naming the files the mask step has not read, or None.

    ``stale`` is True for a ``binary`` mask whose check was recorded by a mask step that
    predates the value rule BM11 (:func:`.mask_representation.predates_value_rule`): the message
    says so and names the fix, running the mask step again.
    """
    files = tuple(sorted(unchecked))
    if not files:
        return None
    if stale:
        message = stale_check_message(mask_id, f"for {len(files)} of {total} files")
    else:
        message = (
            f"The mask {mask_id!r} is not yet checked against its declared representation for "
            f"{len(files)} of {total} files. Run the masks step on this output folder first "
            "(A_MASK_UNCHECKED)."
        )
    return Refusal("A_MASK_UNCHECKED", mask_id, message, files)


def stale_check_message(mask_id: str, scope: str) -> str:
    """Return the ``A_MASK_UNCHECKED`` message of a ``binary`` check older than BM11.

    ``scope`` says which files: ``"for k of n files"`` (the resolver) or ``"(path)"`` (one
    file read).
    """
    return (
        f"The mask {mask_id!r} is declared binary and its check {scope} was recorded by a "
        "masks step older than the binary value rule (Spec ADR 0005 BM11: one positive value "
        "over all the planes of a file), so it is not checked. Run the masks step again on "
        "this output folder (A_MASK_UNCHECKED)."
    )


def rgb_refusal(capabilities: Capabilities, rgb_images: Iterable[str]) -> Refusal | None:
    """Return ``A_RGB_NOT_SUPPORTED`` when ``rgb`` units are requested without the capability.

    ``rgb_images`` are the images of ``rgb`` units whose channels the request names (experiment-v1
    §8: the refusal applies to a request that includes an RGB unit's channels); a mask-only request
    names none, so it is never refused for RGB.
    """
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


def volume_refusals(request: Request, z_counts: Mapping[str, int]) -> tuple[Refusal, ...]:
    """Return every ``A_VOLUME_NOT_SUPPORTED`` refusal of a request: the one place that builds it.

    Parameters
    ----------
    request
        The request.
    z_counts
        Each position's image path to its number of Z planes (``Fov.z_count``).

    Returns
    -------
    tuple of Refusal
        First a ``volume`` request without ``volume_support``; then, for each mask ID with a
        ``binary_mask_policy`` (in request order), one refusal per position with more than one
        Z plane (sorted by path), as a ``plane`` or a ``volume`` request, with or without
        ``volume_support`` (Spec ADR 0005 BM5: the labeller labels 2-D planes only).
    """
    found = []
    if request.planes == VOLUME and not request.capabilities.volume_support:
        found.append(
            Refusal(
                VolumeNotSupportedError.code,
                VOLUME,
                "A Z-volume selection was requested and this consumer does not declare "
                "volume_support; ask for planes (A_VOLUME_NOT_SUPPORTED).",
            )
        )
    stacks = sorted((path, count) for path, count in z_counts.items() if count > 1)
    for mask in mask_uses(request.masks):
        if mask.binary_mask_policy is None:
            continue
        found.extend(
            Refusal(
                VolumeNotSupportedError.code,
                mask.mask_id,
                f"{mask.mask_id!r} would be converted by {mask.binary_mask_policy}, which labels "
                f"2-D planes only; the images of {path} have {count} Z planes. Converting a "
                "Z-stack is not supported (A_VOLUME_NOT_SUPPORTED).",
                (path,),
            )
            for path, count in stacks
        )
    return tuple(found)
