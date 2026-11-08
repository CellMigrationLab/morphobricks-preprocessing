"""What a run records about Z-projected copies (issue #176, run side).

A run on a tree of projected copies (:mod:`.projection_record`) copies a ``projection``
summary into its manifest, so a later brick can carry it into its own records without
reading the conversion record: the method, the slices requested, and for each image the
slices used, the types and the rule, the source's content SHA-256, and which conversion
record it came from. A run without projected copies has no such key: its manifest is
byte for byte what it was.

Masks are never projected (design §7): a 2-D mask next to a projected image is paired as
usual, and the run records and reports ``masks_on_projected_images``, which names the
method. Everything here is pure data, read strictly: a summary that does not match the
conversion record it names is refused by :class:`.run_inputs.RunInputs`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .projection import METHODS
from .validation import ValidationIssue, plural, warning

if TYPE_CHECKING:  # the record module imports this one
    from .conversion_record import ConversionRecord, CopyEntry

KEY = "projection"
"""The manifest's key; absent for a run without projected copies."""
MASKS_WARNING = "masks_on_projected_images"
"""The warning code: masks drawn on (or for) the unprojected images are paired with copies."""

_SHA256 = re.compile(r"[0-9a-f]{64}")
_IMAGE_KEYS = (
    "relative_path",
    "source_relative_path",
    "source_content_sha256",
    "source_z_size",
    "z_range",
    "source_dtype",
    "output_dtype",
    "dtype_rule",
    "rounding",
    "significant_bits_dropped",
)
_KEYS = frozenset(
    {"method", "z_range", "conversion_record", "source_root", "images", "masks_paired", "warnings"}
)


@dataclass(frozen=True)
class ProjectedImage:
    """How one analysed image was projected, as the run recorded it.

    Attributes
    ----------
    relative_path
        The projected copy, relative to the experiment root (the run's image).
    source_relative_path
        The source image it was made from, relative to the source root.
    source_content_sha256
        The SHA-256 of the source's content as read when the copy was written.
    source_z_size
        The number of slices the source had.
    z_range
        ``(start, stop)``, the slices used (zero-based, stop excluded).
    source_dtype, output_dtype
        The pixel types read and written.
    dtype_rule, rounding
        How the output type was decided and how the values were rounded.
    significant_bits_dropped
        Why the source's significant bits are not kept, or None when they are.
    """

    relative_path: str
    source_relative_path: str
    source_content_sha256: str
    source_z_size: int
    z_range: tuple[int, int]
    source_dtype: str
    output_dtype: str
    dtype_rule: str
    rounding: str
    significant_bits_dropped: str | None

    def to_dict(self) -> dict:
        """Return the image as JSON-compatible data."""
        return {**{k: getattr(self, k) for k in _IMAGE_KEYS}, "z_range": list(self.z_range)}

    @classmethod
    def from_dict(cls, data: object) -> ProjectedImage:
        """Read an image written by :meth:`to_dict`, refusing anything else."""
        if not isinstance(data, Mapping) or set(data) != set(_IMAGE_KEYS):
            raise ValueError(f"a projected image needs exactly the keys {list(_IMAGE_KEYS)}")
        numbers = ("z_range", "source_z_size", "significant_bits_dropped")
        bounds, size = data["z_range"], data["source_z_size"]
        dropped = data["significant_bits_dropped"]
        if any(not isinstance(data[k], str) for k in _IMAGE_KEYS if k not in numbers):
            raise ValueError(f"{data.get('relative_path')!r}: a projected image field is not text")
        if not _SHA256.fullmatch(str(data["source_content_sha256"])):
            raise ValueError(f"{data['relative_path']!r}: the source SHA-256 is not a SHA-256")
        ints = isinstance(bounds, list) and len(bounds) == 2 and all(type(b) is int for b in bounds)
        if not ints or type(size) is not int or not 0 <= bounds[0] < bounds[1] <= size:
            raise ValueError(f"{data['relative_path']!r}: the slice range does not fit the size")
        if dropped is not None and not isinstance(dropped, str):
            raise ValueError(f"{data['relative_path']!r}: significant_bits_dropped is not text")
        fields = {k: data[k] for k in _IMAGE_KEYS}
        return cls(**{**fields, "z_range": (bounds[0], bounds[1])})


@dataclass(frozen=True)
class RunProjection:
    """The projection of a run's images (``RunInputs.projection``; None for no projection).

    Attributes
    ----------
    method
        ``"max"``, ``"mean"`` or ``"sum"``: the one method of the copy tree.
    z_range
        The slice range asked for (every image), or None for every slice. The slices each
        image used are in :attr:`images`.
    conversion_record
        The conversion record's ``(file name, SHA-256)``, the source of these facts.
    source_root
        The experiment folder the copies were made from.
    images
        One entry per analysed image, sorted by path.
    masks_paired
        The masks paired with projected images, sorted (never projected themselves).
    warnings
        The recorded warning codes: :data:`MASKS_WARNING` when masks are paired.
    """

    method: str
    z_range: tuple[int, int] | None
    conversion_record: tuple[str, str]
    source_root: str
    images: tuple[ProjectedImage, ...]
    masks_paired: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def image(self, relative_path: str) -> ProjectedImage | None:
        """Return how an analysed image was projected, or None if it is not listed."""
        return next((i for i in self.images if i.relative_path == relative_path), None)

    def describe(self) -> str:
        """Return the report line, e.g. ``max projections of slices 1:4 (2 images)``."""
        used = {i.z_range for i in self.images}
        shown = self.z_range or (next(iter(used)) if len(used) == 1 else None)
        slices = "all slices" if shown is None else f"slices {shown[0]}:{shown[1]}"
        return f"{self.method} projections of {slices} ({plural(len(self.images), 'image')})"

    def to_dict(self) -> dict:
        """Return the summary as the manifest's ``projection`` object."""
        name, sha256 = self.conversion_record
        return {
            "method": self.method,
            "z_range": None if self.z_range is None else list(self.z_range),
            "conversion_record": {"file": name, "sha256": sha256},
            "source_root": self.source_root,
            "images": [i.to_dict() for i in self.images],
            "masks_paired": list(self.masks_paired),
            "warnings": list(self.warnings),
        }

    @classmethod
    def from_dict(cls, data: object) -> RunProjection:
        """Read the manifest's ``projection`` object strictly.

        Raises
        ------
        ValueError
            For missing or extra keys, an unknown method, a bad range or SHA-256, or a
            warning list that does not match the masks listed.
        """
        if not isinstance(data, Mapping) or set(data) != _KEYS:
            raise ValueError(f"{KEY} must hold exactly {', '.join(sorted(_KEYS))}")
        bounds, record = data["z_range"], data["conversion_record"]
        if data["method"] not in METHODS:
            raise ValueError(f"{KEY}.method {data['method']!r} is not max, mean or sum")
        if bounds is not None and not (isinstance(bounds, list) and len(bounds) == 2):
            raise ValueError(f"{KEY}.z_range is not null or two numbers")
        if not isinstance(record, Mapping) or set(record) != {"file", "sha256"}:
            raise ValueError(f"{KEY}.conversion_record must be {{file, sha256}}")
        if not isinstance(record["file"], str) or not _SHA256.fullmatch(str(record["sha256"])):
            raise ValueError(f"{KEY}.conversion_record is not a file name and a SHA-256")
        masks, warns = data["masks_paired"], data["warnings"]
        if not isinstance(masks, list) or not all(isinstance(m, str) for m in masks):
            raise ValueError(f"{KEY}.masks_paired is not a list of paths")
        if warns != ([MASKS_WARNING] if masks else []) or not isinstance(data["source_root"], str):
            raise ValueError(
                f"{KEY}.warnings is not {MASKS_WARNING!r} exactly when masks are paired"
            )
        if not isinstance(data["images"], list):
            raise ValueError(f"{KEY}.images is not a list")
        return cls(
            method=data["method"],
            z_range=None if bounds is None else (bounds[0], bounds[1]),
            conversion_record=(record["file"], record["sha256"]),
            source_root=data["source_root"],
            images=tuple(ProjectedImage.from_dict(i) for i in data["images"]),
            masks_paired=tuple(masks),
            warnings=tuple(warns),
        )


def recorded_projection(value: object) -> dict | None:
    """Return the manifest's ``projection`` entry as read (None when absent), strictly.

    Raises
    ------
    ValueError
        When the entry is not a valid :class:`RunProjection`.
    """
    if value is None:
        return None
    RunProjection.from_dict(value)
    return dict(value)  # type: ignore[arg-type]


def run_projection(
    record: ConversionRecord,
    sha256: str,
    analysed: Iterable[str],
    masks: Iterable[tuple[str, str]] = (),
    file_name: str = ".morphobricks-conversion.json",
) -> RunProjection | None:
    """Summarise the projection a run analyses, from the conversion record it reads.

    Parameters
    ----------
    record
        The tree's conversion record.
    sha256
        The record file's SHA-256 (what the manifest names).
    analysed
        The images the run analyses, relative to the copy tree.
    masks
        ``(image, mask path)`` of every mask found for the images.
    file_name
        The record's file name.

    Returns
    -------
    RunProjection or None
        None when the record holds no projection.
    """
    if record.projection is None:
        return None
    wanted = set(analysed)
    entries = sorted(
        (e for e in record.files if e.projection is not None and e.relative_path in wanted),
        key=lambda e: e.relative_path,
    )
    images = tuple(_image(e) for e in entries)
    projected = {i.relative_path for i in images}
    paired = tuple(sorted({path for image, path in masks if image in projected}))
    return RunProjection(
        method=record.projection.method,
        z_range=record.projection.z_range,
        conversion_record=(file_name, sha256),
        source_root=record.source_root,
        images=images,
        masks_paired=paired,
        warnings=(MASKS_WARNING,) if paired else (),
    )


def masks_issue(projection: RunProjection) -> list[ValidationIssue]:
    """Return the ``masks_on_projected_images`` warning, or nothing when no mask is paired."""
    if not projection.masks_paired:
        return []
    method, count = projection.method, len(projection.masks_paired)
    return [
        warning(
            MASKS_WARNING,
            f"{plural(count, 'mask')} {'is' if count == 1 else 'are'} paired with images that "
            f"are {method} projections of Z-stacks. Masks are never projected: they are used "
            f"as they are, and nothing says they were drawn on the {method} projection. Each "
            "is a 2-D mask; a label image has no meaningful maximum, mean or sum, so a mask "
            "made on the stack would not describe these images.",
            projection.masks_paired,
            f"Check that the masks were made on, or for, the {method} projections. The "
            "run records this warning and the method in the manifest.",
        )
    ]


def _image(entry: CopyEntry) -> ProjectedImage:
    """Return the run's record of one projected copy."""
    done = entry.projection
    return ProjectedImage(
        relative_path=entry.relative_path,
        source_relative_path=done.source_relative_path,
        source_content_sha256=done.source_content_sha256,
        source_z_size=done.source_z_size,
        z_range=done.z_range,
        source_dtype=done.source_dtype,
        output_dtype=done.output_dtype,
        dtype_rule=done.dtype_rule,
        rounding=done.rounding,
        significant_bits_dropped=done.significant_bits_dropped,
    )
