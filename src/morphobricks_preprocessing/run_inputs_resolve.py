"""``RunInputs.resolve_request``: the one experiment interpreter for analysis runs.

:class:`ResolvingInputs` is mixed into :class:`.run_inputs.RunInputs`. From a published
description and the run's manifest it classifies what a :class:`.run_inputs_request.Request`
asks for and gives back an immutable :class:`.run_inputs_request.ResolvedRequest`:

- every requested channel resolved to its C index **per image** (by canonical ID, never by a
  shared index), refused as ``A_SCOPE_INCOMPLETE`` when any image lacks it (k of n, k may be 0);
- every requested mask by its declared ID, refused as ``A_SCOPE_INCOMPLETE`` when some images
  have no file of it, ``A_MASK_UNCHECKED`` when the mask step has not read every file, and
  ``A_MASK_NOT_INSTANCES`` for a non-instances mask used as objects without the recorded
  ``binary_mask_policy`` and for a ``parent_labels`` mask used as objects, always
  (experiment-v1 sections 4.5a, 4.5b, 4.5c, 8); a ``parent_labels`` region for a consumer
  without ``parent_labels_support`` is selected with ``conversion = "parent_labels_as_area"``;
- ``rgb`` units refused as ``A_RGB_NOT_SUPPORTED`` unless the consumer declares ``rgb_support``
  (section 4.4b), and a Z-volume refused as ``A_VOLUME_NOT_SUPPORTED`` unless it declares
  ``volume_support``;
- the coverage of every biological repeat (``repeat_group``) per channel and mask: full,
  partial or unavailable, reported whether or not the request is refused;
- the provenance: the resolver contract version, the description's SHA-256 and section
  digests as the manifest recorded them, and the request itself (its device included).

It reads only the manifest, the published description (checked against the run's SHA-256) and
the mask step's status file: no pixel, no brick, no CellPatcher module. Every run is described:
a run without a published description is refused before a ``RunInputs`` exists
(``RunInputs.open``, ``A_DESCRIPTION_REQUIRED``), so there is no undescribed path here.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from .description import Description
from .manifest import RunManifest
from .mask_representation import CHECKED, file_status, read_checked
from .run_inputs_request import (
    RESOLVER_VERSION,
    RGB,
    FovSelection,
    MaskRequest,
    MaskSelection,
    Provenance,
    Refusal,
    RepeatCoverage,
    Request,
    ResolvedRequest,
    check_request,
    coverage,
    mask_conversion,
    mask_representation_refusal,
    plane_selections,
    rgb_refusal,
    scope_refusal,
    unchecked_refusal,
    volume_refusal,
)

if TYPE_CHECKING:
    from .run_inputs import Fov


class ResolvingInputs:
    """The ``resolve_request`` method of ``RunInputs`` (resolver contract version 1)."""

    output_folder: Path
    manifest: RunManifest

    def description(self) -> Description:  # pragma: no cover - SemanticInputs provides it
        """Return the published description (``SemanticInputs``)."""
        raise NotImplementedError

    def fovs(self) -> tuple[Fov, ...]:  # pragma: no cover - RunInputs provides it
        """Return every position (``RunInputs``)."""
        raise NotImplementedError

    def resolve_request(self, request: Request) -> ResolvedRequest:
        """Resolve an analysis request against this described run.

        Parameters
        ----------
        request
            The channels, masks, planes, capabilities and device of the analysis.

        Returns
        -------
        ResolvedRequest
            One selection per position when nothing refuses; else the refusals and no
            selection. The coverage per repeat and the provenance are given either way.

        Raises
        ------
        RunInputsError
            When the published description is gone or changed since the run
            (:meth:`.run_inputs_semantic.SemanticInputs.description`).
        ValueError
            For a malformed request (:func:`.run_inputs_request.check_request`).
        """
        description = self.description()
        check_request(request, {k: m.representation for k, m in description.masks.items()})
        fovs = self.fovs()
        repeats = _repeats(fovs)
        refusals: list[Refusal] = []
        covered: list[RepeatCoverage] = []
        for channel_id in request.channel_ids:
            present = [f.relative_path for f in fovs if channel_id in f.channel_ids]
            missing = [f.relative_path for f in fovs if channel_id not in f.channel_ids]
            refusals.extend(_some(scope_refusal(channel_id, "channel", missing, len(fovs))))
            covered.extend(coverage(channel_id, repeats, present))
        masks_table = read_checked(self.output_folder) if request.masks else None
        for mask in request.masks:
            found, more = self._mask(mask, description, fovs, repeats, masks_table)
            refusals.extend(found)
            covered.extend(more)
        rgb = [f.relative_path for f in fovs if f.image_type == RGB]
        refusals.extend(_some(rgb_refusal(request.capabilities, rgb)))
        refusals.extend(_some(volume_refusal(request)))
        selections = () if refusals else self._selections(request, description, fovs)
        return ResolvedRequest(
            selections, tuple(refusals), tuple(covered), self._provenance(request)
        )

    def _mask(
        self,
        mask: MaskRequest,
        description: Description,
        fovs: tuple[Fov, ...],
        repeats: Mapping[str, tuple[str, ...]],
        table: object,
    ) -> tuple[list[Refusal], tuple[RepeatCoverage, ...]]:
        """Classify one requested mask: presence per image, check status, representation."""
        declared = description.masks.get(mask.mask_id)
        if declared is None:
            missing = [f.relative_path for f in fovs]
            refusal = scope_refusal(mask.mask_id, "mask", missing, len(fovs))
            return _some(refusal), coverage(mask.mask_id, repeats, ())
        recorded = _mask_entries(self.manifest, declared.source_type)
        present = [f.relative_path for f in fovs if f.relative_path in recorded]
        missing = [f.relative_path for f in fovs if f.relative_path not in recorded]
        refusals = _some(scope_refusal(mask.mask_id, "mask", missing, len(fovs)))
        unchecked = [
            recorded[path]["path"]
            for path in present
            if file_status(
                table, mask.mask_id, declared.source_type, declared.representation, recorded[path]
            )
            != CHECKED
        ]
        refusals.extend(_some(unchecked_refusal(mask.mask_id, unchecked, len(present))))
        refusals.extend(_some(mask_representation_refusal(mask, declared.representation)))
        return refusals, coverage(mask.mask_id, repeats, present)

    def _selections(
        self,
        request: Request,
        description: Description,
        fovs: tuple[Fov, ...],
    ) -> tuple[FovSelection, ...]:
        """Build one selection per position; nothing here can refuse any more."""
        masks = tuple(
            MaskSelection(
                m.mask_id,
                description.masks[m.mask_id].source_type,
                description.masks[m.mask_id].representation,
                m.role,
                CHECKED,
                mask_conversion(
                    m, description.masks[m.mask_id].representation, request.capabilities
                ),
            )
            for m in request.masks
        )
        return tuple(
            FovSelection(
                key=fov.key,
                relative_path=fov.relative_path,
                repeat_group=fov.repeat_group,
                condition_id=fov.condition_id,
                technical_repeat_id=fov.technical_repeat_id,
                image_type=fov.image_type,
                channel_ids=request.channel_ids,
                channel_indices=tuple(fov.channel_ids.index(c) for c in request.channel_ids),
                masks=masks,
                planes=plane_selections(fov.t_count, fov.z_count, request.planes),
            )
            for fov in fovs
        )

    def _provenance(self, request: Request) -> Provenance:
        """Return the provenance from the manifest's description record (always present)."""
        recorded = self.manifest.experiment_description
        digests = tuple(sorted(recorded["section_digests"].items()))
        return Provenance(RESOLVER_VERSION, recorded["sha256"], digests, request)


def _some(refusal: Refusal | None) -> list[Refusal]:
    """Return a one-item list for a refusal, an empty one for None."""
    return [] if refusal is None else [refusal]


def _repeats(fovs: tuple[Fov, ...]) -> dict[str, tuple[str, ...]]:
    """Return the images of every biological repeat (``repeat_group``), sorted."""
    groups: dict[str, list[str]] = {}
    for fov in fovs:
        groups.setdefault(fov.repeat_group, []).append(fov.relative_path)
    return {group: tuple(sorted(paths)) for group, paths in groups.items()}


def _mask_entries(manifest: RunManifest, source_type: str) -> dict[str, Mapping]:
    """Return the recorded mask entries of one source type, by image path."""
    files = (manifest.masks or {}).get("files", ())
    return {entry["image"]: entry for entry in files if entry["type"] == source_type}
