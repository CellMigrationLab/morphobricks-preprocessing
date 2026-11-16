"""The explicit mask conversion helpers of ``RunInputs`` (Spec ADR 0005 BM12).

A user converts a mask on purpose, as a recorded step that writes **new** mask files and never
changes the source:

- :meth:`ConversionInputs.binary_to_instances` writes one ``int32`` instance mask: each plane of
  a ``binary`` mask labelled by :func:`.masks.binary.label_binary` (8-connected, 1 to n), equal to
  ``RunInputs.read_objects`` on that plane. Its output is declared ``instances``.
- :meth:`ConversionInputs.classes_to_binary` writes one ``uint8`` binary mask per class value
  present in a class label image (a 2-D plane declared ``parent_labels`` or ``instances``), in
  ascending class order, named ``<output_prefix>_class<value>``: 1 where the image holds the
  class, 0 elsewhere. Class 0 is the background and gives no output. Each output is declared
  ``binary``.

Both take a typed context, not paths: the opened run, the position and the source's mask ID,
so the run's recorded representation, mask-step status and fingerprint decide what may be read.
Before any pixel is read they refuse an undeclared mask ID, a label image inside an OME-Zarr, a
representation the helper does not convert, and a source file the mask step has not checked or
that changed since (``A_MASK_UNCHECKED``, the resolver's refusal for that file). Nothing is cast,
thresholded, squeezed or selected. An output goes to ``<output mask ID>/<the image's folders>/
<the image's position ID>.tif`` in the run's masks folder (the type-folder layout mask discovery
pairs with that image), whatever the source's layout. An output mask ID that is declared, a mask
type of that name holding files this conversion did not write, and an existing output file are
refused, never overwritten, unless it is the same conversion already published (its lineage line
names the same source in every key, the same output, operation and helper version, and the file
still has its digest): the call is then a no-op. Each output is published with its line of
``mask_lineage.jsonl`` (:mod:`.mask_lineage`). The user then declares each output in the
description, runs preprocessing and the mask step again, and reads it like any mask.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from .description_identity import Identities, nfc
from .description_report import Issues
from .discovery import discover_experiment
from .fingerprint import Fingerprint
from .image_names import position_id
from .mask_binding import MaskChangedError
from .mask_lineage import (
    LINE_KEYS,
    LineageLine,
    content_sha256,
    file_record,
    publish_output,
    published_lines,
    read_written,
    roll_back_pending,
)
from .mask_representation import CHECKED, file_status, predates_value_rule, read_checked
from .masks import FOLDER, MaskFile
from .masks.binary import CONNECTIVITY, label_binary
from .masks.files import _folder_masks
from .masks.labels import LARGEST_LABEL
from .masks.read import read_mask_plane
from .run_inputs_error import MaskUncheckedError
from .run_inputs_request import unchecked_refusal

if TYPE_CHECKING:
    from .description import Mask
    from .manifest import RunManifest
    from .run_inputs import Fov

BINARY_TO_INSTANCES = "binary_to_instances"
CLASSES_TO_BINARY = "classes_to_binary"
NO_CLASS_PRESENT = "no class present"
"""The note of a ``classes_to_binary`` result whose image holds no class (all zero)."""
_HELPER = "morphobricks_preprocessing.RunInputs."


@dataclass(frozen=True)
class ConvertedMask:
    """One output of a conversion helper.

    Attributes
    ----------
    mask_id
        The output mask ID (declare it with the representation the helper names).
    path
        The output file, relative to the masks folder, with ``/``.
    class_value
        The class of a ``classes_to_binary`` output; None for ``binary_to_instances``.
    line
        Its line of ``mask_lineage.jsonl``, as stored (with the newline).
    published
        True when this call wrote it; False when the same conversion was already published
        (an idempotent retry).
    """

    mask_id: str
    path: str
    class_value: int | None
    line: str
    published: bool

    @property
    def record(self) -> dict:
        """The lineage line as a new dict (its keys in the stored order)."""
        return json.loads(self.line)


@dataclass(frozen=True)
class MaskConversionResult:
    """What one call of a conversion helper wrote or found already written.

    Attributes
    ----------
    operation
        ``"binary_to_instances"`` or ``"classes_to_binary"``.
    image
        The position's image, relative to the experiment folder.
    mask_id, source_path
        The source mask ID and its file, relative to the masks folder.
    representation
        The representation to declare each output with: ``instances`` or ``binary``.
    outputs
        The outputs, in ascending class order for ``classes_to_binary``.
    note
        ``"no class present"`` for a class image holding only 0 (no output); else empty.
    """

    operation: str
    image: str
    mask_id: str
    source_path: str
    representation: str
    outputs: tuple[ConvertedMask, ...]
    note: str = ""


def check_class_image(pixels: np.ndarray, *, mask_id: str, path: str) -> None:
    """Refuse pixels that are not one 2-D plane of class values from 0 to the int32 limit.

    The pixel validation of :meth:`ConversionInputs.classes_to_binary`, with no run status: it
    takes the raw plane (never through ``check_labels``), so its messages are these.

    Parameters
    ----------
    pixels
        The class image as stored.
    mask_id, path
        The mask ID and the file, as the messages name them.

    Raises
    ------
    ValueError
        ``{mask_id!r} ({path}) has {ndim} dimensions ...`` for anything not 2-D (a Z, T or C
        stack and a one-hot image included), ``... has pixel type {dtype} ...`` for a dtype that
        is not a signed or unsigned integer (bool included), ``... holds negative values ...``
        and ``... holds class values above 2147483647 ...``.
    """
    if type(pixels) is not np.ndarray:
        raise ValueError(
            f"{mask_id!r} ({path}) is a {type(pixels).__name__}; a class image is a NumPy array"
        )
    _check_class_shape(pixels.shape, mask_id=mask_id, path=path)
    _check_class_dtype(pixels.dtype, mask_id=mask_id, path=path)
    if not pixels.size:
        return
    smallest, largest = int(pixels.min()), int(pixels.max())
    if smallest < 0:
        raise ValueError(
            f"{mask_id!r} ({path}) holds negative values (smallest {smallest}); class 0 is the "
            "background and classes are positive"
        )
    if largest > LARGEST_LABEL:
        raise ValueError(
            f"{mask_id!r} ({path}) holds class values above {LARGEST_LABEL} (largest {largest}); "
            "a class value must fit an int32 label"
        )


def images_with_mask(run: ConversionInputs, mask_id: str) -> tuple[str, ...]:
    """Return the images the run recorded a file of a declared mask for, sorted.

    The positions the command line and the notebook convert one by one; empty for a mask ID the
    description does not declare (the helpers refuse it, naming the declared IDs).
    """
    declared = run.description().masks.get(mask_id)
    if declared is None:
        return ()
    recorded = (run.manifest.masks or {}).get("files", ())
    return tuple(sorted({m["image"] for m in recorded if m["type"] == declared.source_type}))


def conversion_next_steps(results: Iterable[MaskConversionResult], masks_root: object) -> str:
    """Return what to do with the outputs: declare them, then run and check them again."""
    declared: dict[str, str] = {}
    for result in results:
        for output in result.outputs:
            declared.setdefault(output.mask_id, result.representation)
    if not declared:
        return "No mask was written."
    lines = [f"Written to the masks folder {masks_root}. Next:"]
    lines += [
        f"- declare the mask {mask_id} (source type {mask_id}) as {representation}"
        for mask_id, representation in sorted(declared.items())
    ]
    lines.append("- then run preprocessing and the masks step again to check them.")
    return "\n".join(lines)


def _check_class_shape(shape: tuple[int, ...], *, mask_id: str, path: str) -> None:
    if len(shape) != 2:
        raise ValueError(
            f"{mask_id!r} ({path}) has {len(shape)} dimensions (shape {tuple(shape)}); a class "
            "image is one 2-D plane of class values; a one-hot or multi-channel stack is not "
            "converted"
        )


def _check_class_dtype(dtype: object, *, mask_id: str, path: str) -> None:
    if np.dtype(dtype).kind not in "iu":
        raise ValueError(
            f"{mask_id!r} ({path}) has pixel type {np.dtype(dtype)}; a class image holds "
            "non-negative integer class values"
        )


@dataclass(frozen=True)
class _Source:
    """A source mask file checked for conversion: its record, header and fingerprint."""

    mask_id: str
    image: str
    mask: MaskFile
    folder: Path
    fingerprint: Fingerprint


class ConversionInputs:
    """The conversion helpers of ``RunInputs`` (mixed into it).

    They use ``RunInputs``'s ``output_folder``, ``experiment_root``, ``masks_root``,
    ``manifest``, ``description``, ``_checked``, ``_mask_entry`` and ``_recorded_mask``.
    """

    output_folder: Path
    experiment_root: Path
    masks_root: Path | None
    manifest: RunManifest

    def binary_to_instances(
        self, fov: Fov | str, mask_id: str, *, output_mask_id: str
    ) -> MaskConversionResult:
        """Write the 8-connected objects of a ``binary`` mask as a new ``instances`` mask.

        Parameters
        ----------
        fov
            The position, as a :class:`.run_inputs.Fov` or its image path.
        mask_id
            The source mask ID, declared ``binary``; its file has one Z plane and any number of
            time points, each labelled on its own.
        output_mask_id
            The new mask ID (experiment-v1 section 3.2), and its mask-type folder.

        Returns
        -------
        MaskConversionResult
            One ``int32`` output (``published`` False on an idempotent retry).

        Raises
        ------
        ValueError
            An undeclared mask ID, an OME-Zarr label image, a mask declared ``instances`` or
            ``parent_labels``, a file with several Z planes, an invalid or colliding output
            mask ID or file (nothing is written).
        MaskUncheckedError
            ``A_MASK_UNCHECKED``: the file is not checked, or changed since its check.
        MaskContradictsRepresentationError
            ``R_MASK_CONTRADICTS_REPRESENTATION`` from ``label_binary``.
        MaskChangedError
            The source changed while it was read.
        """
        source = self._conversion_source(fov, mask_id, BINARY_TO_INSTANCES)
        z_planes = source.mask.planes["Z"]
        if z_planes > 1:
            raise ValueError(
                f"{mask_id!r} ({source.mask.path}) has {z_planes} Z planes; binary_to_instances "
                "labels 2-D planes only, and a Z-stack is not converted"
            )
        relative = self._output_target(source, output_mask_id, BINARY_TO_INSTANCES)
        pixels = self._read_source(source)
        labels = _per_plane(pixels, lambda plane: label_binary(plane, name=source.mask.path))
        record = self._record(source, pixels, output_mask_id, relative, labels, None)
        outputs = self._publish(BINARY_TO_INSTANCES, [(relative, labels, record)])
        return MaskConversionResult(
            BINARY_TO_INSTANCES, source.image, mask_id, source.mask.path, "instances", outputs
        )

    def classes_to_binary(
        self, fov: Fov | str, mask_id: str, *, output_prefix: str
    ) -> MaskConversionResult:
        """Write one ``uint8`` binary mask per class value of a 2-D class label image.

        Parameters
        ----------
        fov
            The position, as a :class:`.run_inputs.Fov` or its image path.
        mask_id
            The source mask ID, declared ``parent_labels`` or ``instances``; its file is one 2-D
            plane of signed or unsigned integers from 0 to 2,147,483,647.
        output_prefix
            The outputs are ``<output_prefix>_class<value>``, one per class present.

        Returns
        -------
        MaskConversionResult
            The outputs in ascending class order; none, with the note ``"no class present"``,
            for an all-zero image. A retry after a failure skips the classes already published.

        Raises
        ------
        ValueError
            As :meth:`binary_to_instances`, with a mask declared ``binary`` refused and the
            pixel messages of :func:`check_class_image`.
        MaskUncheckedError, MaskChangedError
            As :meth:`binary_to_instances`.
        """
        source = self._conversion_source(fov, mask_id, CLASSES_TO_BINARY)
        _check_class_shape(source.mask.shape, mask_id=mask_id, path=source.mask.path)
        if source.mask.dtype is not None:
            _check_class_dtype(source.mask.dtype, mask_id=mask_id, path=source.mask.path)
        _check_output_id(output_prefix, "output prefix")
        pixels = self._read_source(source)
        check_class_image(pixels, mask_id=mask_id, path=source.mask.path)
        planned = []
        for value in (int(v) for v in np.unique(pixels) if v != 0):
            output_id = f"{output_prefix}_class{value}"
            relative = self._output_target(source, output_id, CLASSES_TO_BINARY)
            binary = (pixels == value).astype(np.uint8)
            planned.append(
                (relative, binary, self._record(source, pixels, output_id, relative, binary, value))
            )
        outputs = self._publish(CLASSES_TO_BINARY, planned)
        return MaskConversionResult(
            CLASSES_TO_BINARY,
            source.image,
            mask_id,
            source.mask.path,
            "binary",
            outputs,
            "" if planned else NO_CLASS_PRESENT,
        )

    # The checks before any read ------------------------------------------------------------

    def _conversion_source(self, fov: Fov | str, mask_id: str, operation: str) -> _Source:
        """Return the checked source file of a mask for one position; nothing is read."""
        description = self.description()
        declared = description.masks.get(mask_id)
        if declared is None:
            ids = ", ".join(repr(key) for key in sorted(description.masks)) or "none"
            raise ValueError(
                f"{mask_id!r} is not a mask of this run's description; it declares {ids}"
            )
        image = self._checked(fov).relative_path
        recorded = self._mask_entry(image, declared.source_type)
        if recorded["source"] != FOLDER:
            raise ValueError(
                f"{mask_id!r} for {image} is a label image inside an OME-Zarr; the conversion "
                "helpers convert masks in the masks folder only"
            )
        if self.masks_root is None:  # a recorded masks-folder file always has one
            raise ValueError(f"{mask_id!r} for {image}: the run has no masks folder")
        _check_representation(operation, mask_id, recorded["path"], declared)
        table = read_checked(self.output_folder)
        status = file_status(
            table, mask_id, declared.source_type, declared.representation, recorded
        )
        try:
            now = Fingerprint.of(self.masks_root / recorded["path"])
        except FileNotFoundError:
            now = None
        if status != CHECKED or now != Fingerprint.recorded(recorded):
            stale = status != CHECKED and predates_value_rule(
                table, mask_id, declared.source_type, declared.representation
            )
            refusal = unchecked_refusal(mask_id, [recorded["path"]], 1, stale=stale)
            raise MaskUncheckedError(refusal.message)
        mask, folder = self._recorded_mask(image, declared.source_type, None, None)
        return _Source(mask_id, image, mask, folder, now)

    def _output_target(self, source: _Source, output_id: str, operation: str) -> str:
        """Return an output's path after refusing an invalid or colliding output mask ID."""
        _check_output_id(output_id, "output mask ID")
        declared = self.description().masks
        if output_id in declared:
            raise ValueError(
                f"The output mask ID {output_id!r} is declared in the description already; "
                "choose another ID: an output never replaces a declared mask"
            )
        _check_case_collision(output_id, declared)
        lines = published_lines(self.masks_root, self.masks_root / output_id)
        written = {line.output_path for line in lines if line.record["operation"] == operation}
        foreign = [path for path in self._type_files(output_id) if path not in written]
        if foreign:
            raise ValueError(
                f"The masks folder holds masks of the type {output_id!r} that this conversion "
                f"did not write ({', '.join(foreign)}); choose another output mask ID: nothing "
                "is overwritten"
            )
        parent, _, name = source.image.rpartition("/")
        return "/".join(part for part in (output_id, parent, position_id(name) + ".tif") if part)

    def _type_files(self, mask_type: str) -> list[str]:
        """Return the masks-folder files discovery pairs under a mask type (any case)."""
        images = {entry.relative_path for entry in self.manifest.files}
        discovery = discover_experiment(self.experiment_root, self.output_folder)
        found, _ = _folder_masks(self.masks_root, discovery, images)
        recorded = (self.manifest.masks or {}).get("files", ())
        paths = {m.path for m in found if m.mask_type.casefold() == mask_type.casefold()}
        paths.update(
            m["path"]
            for m in recorded
            if m["source"] == FOLDER and m["type"].casefold() == mask_type.casefold()
        )
        return sorted(paths)

    # Reading, records and publication ------------------------------------------------------

    def _read_source(self, source: _Source) -> np.ndarray:
        """Read every plane of the source as stored, refusing a file that changed meanwhile."""
        mask = source.mask
        others = [axis for axis in mask.axes if axis not in "YX"]
        sizes = [mask.shape[mask.axes.index(axis)] for axis in others]
        planes = []
        for index in np.ndindex(*sizes):
            where = dict(zip(others, index, strict=True))
            planes.append(
                read_mask_plane(
                    mask, source.folder, self.experiment_root, z=where.get("Z"), t=where.get("T")
                )
            )
        pixels = np.stack(planes).reshape(mask.shape) if others else planes[0]
        try:
            after = Fingerprint.of(self.masks_root / mask.path)
        except FileNotFoundError:
            after = None
        if after != source.fingerprint:
            raise MaskChangedError([mask.path])
        return pixels

    def _record(
        self,
        source: _Source,
        pixels: np.ndarray,
        output_id: str,
        relative: str,
        output: np.ndarray,
        class_value: int | None,
    ) -> dict:
        """Return the lineage record of one output, its keys in the schema's order."""
        from . import __version__

        operation = BINARY_TO_INSTANCES if class_value is None else CLASSES_TO_BINARY
        values = (
            file_record(source.mask_id, source.mask.path, source.mask.axes, pixels),
            file_record(output_id, relative, source.mask.axes, output),
            operation,
            CONNECTIVITY if class_value is None else None,
            class_value,
            _HELPER + operation,
            __version__,
        )
        return dict(zip(LINE_KEYS, values, strict=True))

    def _publish(self, operation: str, planned: list) -> tuple[ConvertedMask, ...]:
        """Publish each planned output in order, reusing the ones already published.

        Every collision is refused before the first output is written.
        """
        reused = [self._published_before(relative, record) for relative, _, record in planned]
        outputs = []
        for (relative, pixels, record), line in zip(planned, reused, strict=True):
            output_id = record["output"]["mask_id"]
            if line is None:
                roll_back_pending(self.masks_root, relative)
                publish_output(self.masks_root, relative, pixels, record)
                text = published_lines(self.masks_root, self.masks_root / output_id)[-1].text
            else:
                text = line.text
            outputs.append(
                ConvertedMask(output_id, relative, record["class_value"], text, line is None)
            )
        return tuple(outputs)

    def _published_before(self, relative: str, record: Mapping) -> LineageLine | None:
        """Return the line of the same conversion already published, None for a new output.

        Raises
        ------
        ValueError
            When the output file exists and is not that conversion's (another source, even
            with identical pixels, another helper version, or pixels changed since).
        """
        target = self.masks_root / relative
        if not target.exists():
            return None
        folder = self.masks_root / record["output"]["mask_id"]
        for line in published_lines(self.masks_root, folder):
            if _same_conversion(line.record, record) and _digest_of(target, line.record):
                return line
        raise ValueError(
            f"{relative} exists in the masks folder and is not this conversion's output (no "
            "lineage line names this source, output and helper with the file as it is); choose "
            "another output mask ID: nothing is overwritten"
        )


def _check_representation(operation: str, mask_id: str, path: str, declared: Mask) -> None:
    """Refuse a declared representation the helper does not convert."""
    representation = declared.representation
    if operation == BINARY_TO_INSTANCES and representation != "binary":
        raise ValueError(
            f"{mask_id!r} ({path}) is declared {representation}; binary_to_instances converts "
            "a mask declared binary (an instances mask holds objects already, and a "
            "parent_labels mask is never split into objects)"
        )
    if operation == CLASSES_TO_BINARY and representation == "binary":
        raise ValueError(
            f"{mask_id!r} ({path}) is declared binary: it has one class already; declare or "
            "use it as it is"
        )


def _check_output_id(value: object, what: str) -> None:
    """Refuse an output mask ID (or prefix) the mask ID rules of experiment-v1 3.2 refuse."""
    if not isinstance(value, str):
        raise ValueError(f"The {what} must be text, not {type(value).__name__}")
    issues = Issues()
    ids = Identities(issues)
    ids.token(value, what)
    ids.nfc(value, what)
    ids.single(value, what)
    ids.reserved(value, what, object_set=True)
    if issues:
        codes = ", ".join(dict.fromkeys(str(item.code.value) for item in issues.items))
        raise ValueError(
            f"The {what} {value!r} breaks the mask ID rules of experiment-v1 section 3.2 "
            f"({codes}); choose another one"
        )


def _check_case_collision(output_id: str, declared: Mapping[str, Mask]) -> None:
    """Refuse an output mask ID spelled like a declared mask ID or source type but for case.

    experiment-v1 section 3.2 makes two mask IDs, or two mask source types, of one description
    that differ only in case ``E_ID_CASE_COLLISION``; the output is both, so it could never be
    declared next to them.
    """
    folded = nfc(output_id).casefold()
    names = [("mask ID", mask_id) for mask_id in sorted(declared)]
    names += [("source type", t) for t in sorted({m.source_type for m in declared.values()})]
    for kind, name in names:
        if nfc(name) != nfc(output_id) and nfc(name).casefold() == folded:
            raise ValueError(
                f"The output mask ID {output_id!r} differs only in case from the declared "
                f"{kind} {name!r}; experiment-v1 section 3.2 refuses such a pair "
                "(E_ID_CASE_COLLISION), so the output could never be declared: choose another ID"
            )


def _per_plane(pixels: np.ndarray, label: Callable[[np.ndarray], np.ndarray]) -> np.ndarray:
    """Apply ``label`` to every 2-D plane (the last two axes) of a stored array."""
    if pixels.ndim == 2:
        return label(pixels)
    found = np.empty(pixels.shape, dtype=np.int32)
    for index in np.ndindex(*pixels.shape[:-2]):
        found[index] = label(pixels[index])
    return found


def _same_conversion(existing: Mapping, wanted: Mapping) -> bool:
    """Return True when a lineage line records this very conversion (output digest aside)."""
    same_output = all(
        existing["output"][key] == wanted["output"][key] for key in ("root", "mask_id", "path")
    )
    rest = ("operation", "connectivity", "class_value", "helper", "helper_version")
    return (
        existing["source"] == wanted["source"]
        and same_output
        and all(existing[key] == wanted[key] for key in rest)
    )


def _digest_of(path: Path, record: Mapping) -> bool:
    """Return True when an existing output still has its lineage line's digest."""
    axes = str(record["output"]["axes"])
    try:
        pixels = read_written(path, axes)
    except (OSError, ValueError):
        return False
    return content_sha256(pixels, axes=axes) == record["output"]["content_sha256"]
