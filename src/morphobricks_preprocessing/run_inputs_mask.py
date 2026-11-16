"""The masks of a run for :class:`.run_inputs.RunInputs`: whole planes and exact windows.

:meth:`MaskInputs.read_mask` reads one mask plane by the recorded pairing (moved from
``run_inputs``, unchanged); :meth:`MaskInputs.read_mask_window` reads an exact window of it,
decoding only the stored pieces the window intersects (:mod:`.masks.window`), and
:meth:`MaskInputs.mask_window_plan` says, from the header only, what that read costs.
:meth:`MaskInputs.read_objects` reads the objects of a resolved selection's ``objects`` use,
applying its recorded conversion with the one labeller (:func:`.masks.binary.label_binary`,
Spec ADR 0005 BM6), and :meth:`MaskInputs.objects_read_plan` says, from the header only, what
that read costs (``int32`` labels, issue #316). All five refuse a mask as the whole read refuses
it: no recorded mask of that type, a mask the mask step has not checked (``A_MASK_UNCHECKED``),
a mask changed since the run or stored otherwise than recorded.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from .manifest import RunManifest
from .masks import FOLDER, MaskFile, check_labels, read_mask
from .masks.binary import label_binary
from .masks.header import _mask_header
from .masks.layout import _yx
from .masks.objects import ObjectsReadPlan, objects_read_plan
from .masks.read import read_mask_plane
from .masks.window import mask_window_plan, read_mask_window
from .planes import DEFAULT_MEMORY_LIMIT
from .run_inputs_error import RunInputsError
from .run_inputs_request import (
    BINARY,
    CONNECTED_COMPONENTS,
    INSTANCES,
    OBJECTS,
    FovSelection,
    MaskSelection,
)
from .run_inputs_text import _changed_text
from .run_inputs_window import _check_planes, _coordinate
from .windows import LEVEL, WindowPlan, check_level

if TYPE_CHECKING:
    from .run_inputs import Fov


class MaskInputs:
    """The mask reads of ``RunInputs``.

    They use ``RunInputs``'s ``experiment_root``, ``masks_root``, ``manifest``, ``_checked``,
    ``_fov``, ``_mask_entry``, ``require_checked`` and ``_mask_unchanged``.
    """

    experiment_root: Path
    masks_root: Path | None
    manifest: RunManifest

    def mask_types(self) -> tuple[str, ...]:
        """Return the mask types the run recorded, sorted."""
        return tuple((self.manifest.masks or {}).get("types", ()))

    def read_mask(
        self, fov: Fov | str, mask_type: str, z: int | None = None, t: int | None = None
    ) -> np.ndarray:
        """Read one mask plane by the recorded pairing, checking its labels.

        Raises
        ------
        TypeError
            If ``z`` or ``t`` is neither None nor a whole number.
        IndexError
            If ``z`` or ``t`` is outside the image (``Fov.z_count``, ``Fov.t_count``).
        RunInputsError
            If the image has no recorded mask of that type, or the mask changed since the run.
        ValueError
            For a label outside 0 to the int32 limit (:func:`.masks.check_labels`).
        """
        call = "read_mask(fov, mask_type, z, t)"
        z, t = (None if v is None else _coordinate(v, k, call) for k, v in (("z", z), ("t", t)))
        mask, folder = self._recorded_mask(fov, mask_type, z, t)
        return read_mask(mask, folder, self.experiment_root, z=z, t=t)

    def read_objects(self, selection: FovSelection, mask_id: str, t: int, z: int) -> np.ndarray:
        """Read one plane of a selection's ``objects`` use as int32 object labels.

        The one helper that applies a recorded conversion (Spec ADR 0005 BM2, BM6), whole
        planes only (there is no window argument):

        - an ``instances`` mask without a conversion: its stored labels, checked by
          :func:`.masks.check_labels`, never relabelled (an ``instances`` mask holding 0 and 1
          is one object);
        - ``conversion = "connected_components"`` (a ``binary`` mask): the stored plane labelled
          by :func:`.masks.binary.label_binary`, 8-connected, numbered 1 to n in row-major
          order of each component's first pixel. ``label_binary`` checks the plane first (the
          read guard, a per-plane repeat of the mask step's file-wide check): boolean or
          integers in {0, v} for one v > 0, else ``R_MASK_CONTRADICTS_REPRESENTATION``. A
          plane with no foreground gives zero objects, an all-foreground plane one object.

        Parameters
        ----------
        selection
            A :class:`.run_inputs_request.FovSelection` of :meth:`resolve_request` on this run.
        mask_id
            The mask ID of its ``objects`` use.
        t, z
            The plane's zero-based coordinates (0 for an axis the image lacks).

        Returns
        -------
        numpy.ndarray
            A new ``int32`` array of the plane's shape.

        Raises
        ------
        ValueError
            A mask ID with no ``objects`` selection in ``selection`` (a mask used only as a
            region is read with :meth:`read_mask` or :meth:`read_mask_window`), a
            ``parent_labels`` mask, or a selection whose conversion does not fit its declared
            representation (a programming error: the resolver never gives one); a label
            outside 0 to the int32 limit (``check_labels``).
        MaskContradictsRepresentationError
            ``R_MASK_CONTRADICTS_REPRESENTATION`` from ``label_binary``.
        TypeError, IndexError, RunInputsError, MaskUncheckedError
            As :meth:`read_mask`.
        """
        call = "read_objects(selection, mask_id, t, z)"
        z, t = (_coordinate(v, k, call) for k, v in (("z", z), ("t", t)))
        use = _objects_use(selection, mask_id)
        mask, folder = self._recorded_mask(selection.relative_path, use.source_type, z, t)
        pixels = read_mask_plane(mask, folder, self.experiment_root, z=z, t=t)
        if use.conversion == CONNECTED_COMPONENTS:
            return label_binary(pixels, name=mask.path)
        check_labels(pixels, f"the mask {mask.path}")
        return pixels.astype(np.int32)

    def objects_read_plan(self, selection: FovSelection, mask_id: str) -> ObjectsReadPlan:
        """Return what one :meth:`read_objects` plane costs, from the mask's header only.

        The estimate counts the ``int32`` result, 4 bytes per pixel whatever the stored type (a
        ``uint8`` mask: 4 times its stored size), plus, for ``connected_components``, an upper
        bound of the labeller's own working memory (:func:`.masks.binary.labelling_bytes`);
        see :class:`.masks.objects.ObjectsReadPlan`. The region-read estimate,
        :meth:`mask_window_plan`, is the stored type's. No pixel is read and no digest
        computed; the selection and the mask are refused as :meth:`read_objects` refuses them.

        Raises
        ------
        ValueError, RunInputsError, MaskUncheckedError
            As :meth:`read_objects`, before any pixel is read.
        """
        use = _objects_use(selection, mask_id)
        mask, _ = self._recorded_mask(selection.relative_path, use.source_type, None, None)
        return objects_read_plan(tuple(_yx(mask.axes, mask.shape)), mask.dtype, use.conversion)

    def read_mask_window(
        self,
        fov: Fov | str,
        mask_type: str,
        *,
        t: int,
        z: int,
        bounds: tuple[int, int, int, int],
        level: int = LEVEL,
        memory_limit: int = DEFAULT_MEMORY_LIMIT,
    ) -> np.ndarray:
        """Read an exact window of one mask plane as a ``(h, w)`` array of its stored labels.

        The window is bitwise that slice of :meth:`read_mask`, in the mask's stored integer
        (or boolean) pixel type: nothing is relabelled, cast or made binary, whatever the
        declared representation. Only the stored pieces the window intersects are read (TIFF
        strips or tiles, OME-Zarr chunks), and only the window's labels are checked
        (:func:`.masks.check_labels`).

        Parameters
        ----------
        fov
            The position, as a :class:`.run_inputs.Fov` or its image path.
        mask_type
            The recorded mask type (:meth:`mask_types`), as :meth:`read_mask` takes it.
        t, z
            The plane's zero-based coordinates (0 for an axis the image lacks).
        bounds
            ``(y0, y1, x0, x1)``: half-open ``pixel_edge`` bounds in the plane frame (Spec
            ADR 0004), inside the plane. Nothing is padded or clipped.
        level
            The resolution level; only the full-resolution level 0 is read.
        memory_limit
            Largest working memory allowed for the read, in bytes.

        Returns
        -------
        numpy.ndarray
            ``(y1 - y0, x1 - x0)``. A caller records, with what it computes from it, the
            mask's fingerprint as the run recorded it (the manifest's ``masks`` entry),
            ``level``, ``bounds``, ``t`` and ``z``. No digest of the file's content is computed.

        Raises
        ------
        TypeError
            If ``t`` or ``z`` is not a whole number.
        IndexError
            If ``t`` or ``z`` is outside the image.
        RunInputsError
            As :meth:`read_mask`: no recorded mask of that type, the image or the mask changed
            since the run, or the mask stored otherwise than recorded.
        MaskUncheckedError
            ``A_MASK_UNCHECKED``: the mask step has not checked the mask.
        WindowBoundsError, WindowLevelError, WindowLayoutError, WindowMemoryError
            As :meth:`read_window`.
        ValueError
            For a label in the window outside 0 to the int32 limit.
        """
        call = "read_mask_window(fov, mask_type, t=, z=, ...)"
        z, t = (_coordinate(v, k, call) for k, v in (("z", z), ("t", t)))
        check_level(level)
        mask, folder = self._recorded_mask(fov, mask_type, z, t)
        return read_mask_window(
            mask,
            folder,
            self.experiment_root,
            bounds=bounds,
            z=z,
            t=t,
            level=level,
            memory_limit=memory_limit,
        )

    def mask_window_plan(
        self,
        fov: Fov | str,
        mask_type: str,
        *,
        bounds: tuple[int, int, int, int],
        level: int = LEVEL,
        memory_limit: int = DEFAULT_MEMORY_LIMIT,
    ) -> WindowPlan:
        """Return what :meth:`read_mask_window` would touch, from the header only.

        The plan's ``shape`` is the window's ``(h, w)``, its ``dtype`` the mask's stored pixel
        type and ``memory_estimate`` the working memory of the read in bytes, against
        ``memory_limit``; ``channels`` is empty. No pixel is read and no digest computed. The
        mask is refused as :meth:`read_mask_window` refuses it.
        """
        check_level(level)
        mask, folder = self._recorded_mask(fov, mask_type, None, None)
        return mask_window_plan(
            mask,
            folder,
            self.experiment_root,
            bounds=bounds,
            level=level,
            memory_limit=memory_limit,
        )

    def _recorded_mask(
        self, fov: Fov | str, mask_type: str, z: int | None, t: int | None
    ) -> tuple[MaskFile, Path | None]:
        """Return a position's recorded mask of a type, checked, and the folder it is read from.

        The image must be unchanged and ``z``, ``t`` inside it; the mask must be checked by the
        mask step (a described run), unchanged since the run, and stored with the axes and
        shape the run recorded. Only headers and fingerprints are read.
        """
        entry = self._checked(fov)
        _check_planes(entry.relative_path, self._fov(entry), z, t)
        recorded = self._mask_entry(entry.relative_path, mask_type)
        self.require_checked(recorded, mask_type)  # A_MASK_UNCHECKED (a described run)
        if not self._mask_unchanged(recorded):
            raise RunInputsError(_changed_text([recorded["path"]], "mask"))
        mask = MaskFile(recorded["image"], recorded["type"], recorded["path"], recorded["source"])
        folder = self.masks_root if mask.source == FOLDER else None
        stored, shape, dtype = _mask_header(mask, folder, self.experiment_root)
        # The mask is read through the axes it is stored with, as the run recorded them.
        if stored != recorded["axes"] or list(shape) != list(recorded["shape"]):
            raise RunInputsError(_changed_text([mask.path], "mask"))
        mask = MaskFile(
            mask.image,
            mask.mask_type,
            mask.path,
            mask.source,
            tuple(shape),
            dtype,
            str(recorded["roles"]),
            str(recorded["axes"]),
            str(recorded["axes_from"]),
        )
        return mask, folder


def _objects_use(selection: FovSelection, mask_id: str) -> MaskSelection:
    """Return the selection's ``(mask_id, "objects")`` mask selection, as the resolver gave it.

    Raises
    ------
    ValueError
        When there is none, or its conversion does not fit its declared representation.
    """
    if not isinstance(selection, FovSelection):
        raise ValueError(
            f"read_objects takes a FovSelection of resolve_request, not {type(selection).__name__}"
        )
    for mask in selection.masks:
        if mask.mask_id == mask_id and mask.role == OBJECTS:
            break
    else:
        raise ValueError(
            f"{mask_id!r} has no objects use in this selection of {selection.relative_path}; "
            "a mask used only as a region is read with read_mask or read_mask_window"
        )
    fits = (mask.conversion is None and mask.representation == INSTANCES) or (
        mask.conversion == CONNECTED_COMPONENTS and mask.representation == BINARY
    )
    if not fits:
        raise ValueError(
            f"{mask_id!r} is declared {mask.representation} with the conversion "
            f"{mask.conversion!r}; read_objects reads an instances mask, or a binary mask "
            f"converted by {CONNECTED_COMPONENTS!r}, as resolve_request selects them"
        )
    return mask
