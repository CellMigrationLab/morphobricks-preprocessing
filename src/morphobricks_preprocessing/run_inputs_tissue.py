"""The opt-in H&E tissue mask methods of ``RunInputs`` (Spec ADR 0005 BM15), imported lazily.

The work is in :mod:`.he_tissue_mask`, imported only when a method is called, so importing the
package, opening a run and every command that does not ask for a tissue mask never import
scikit-image, normalization's ``stain`` module or the helper's module (the ``he-mask`` extra is
optional). Nothing is computed unless the user calls a method or clicks the notebook card.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .he_tissue_mask import TissueMaskPreview, TissueMaskResult
    from .run_inputs import Fov

HE_TISSUE_V1 = "he_tissue_v1"
"""The default rule (provisional; :mod:`.he_tissue_rule`)."""


class TissueMaskInputs:
    """The H&E tissue mask helper of ``RunInputs`` (mixed into it)."""

    def he_tissue_mask(
        self, fov: Fov | str, *, output_mask_id: str, rule: str = HE_TISSUE_V1
    ) -> TissueMaskResult:
        """Write one RGB position's H&E tissue mask as a new ``binary`` region mask.

        The position is resolved from this run by its image path (a ``Fov``'s other attributes
        are ignored). The rule ``he_tissue_v1`` is **provisional** and fixed: it leaves out
        nuclei, red blood cells and empty space, sizes in pixels, colour maths from
        normalization. The output goes to ``<masks folder>/<output_mask_id>/...`` with its line
        of ``mask_lineage.jsonl``; declare it ``representation = "binary"``, run preprocessing
        and the masks step again, and use it as a region: in memory, never pass it as
        cell-mode labels.

        Parameters
        ----------
        fov
            The position, as a :class:`.run_inputs.Fov` or its image path.
        output_mask_id
            The new mask ID (experiment-v1 section 3.2), typed by the user.
        rule
            The rule ID; ``"he_tissue_v1"`` is the only one.

        Returns
        -------
        TissueMaskResult
            ``published`` False for an idempotent retry; ``reusable`` False when a code
            identity is unverified (the mask is written but never reused on retry).

        Raises
        ------
        ValueError
            Every refusal of :mod:`.he_tissue_mask` (nothing is written, and before any pixel is
            read where the module says so).
        ImportError
            scikit-image is missing: ``pip install "morphobricks-preprocessing[he-mask]"``.
        """
        from .he_tissue_mask import he_tissue_mask

        return he_tissue_mask(self, fov, output_mask_id=output_mask_id, rule=rule)

    def preview_he_tissue_mask(
        self, fov: Fov | str, *, rule: str = HE_TISSUE_V1
    ) -> TissueMaskPreview:
        """Compute one RGB position's H&E tissue mask in memory; nothing is written.

        Returns
        -------
        TissueMaskPreview
            ``E``, ``H`` and ``od`` as normalization returns them, the components, the kept
            share and each component's share, the rule record and the code identities.

        Raises
        ------
        ValueError, ImportError
            As :meth:`he_tissue_mask`, without the masks folder and output checks.
        """
        from .he_tissue_mask import preview

        return preview(self, fov, rule=rule)


PROVISIONAL_NOTE = (
    "Sizes are in pixels. Fixed rule he_tissue_v1 (provisional), not a trained segmentation."
)
NOT_REUSED = "this mask will not be reused on retry"
"""Said whenever a code identity is unverified (the mask is written but never reused)."""


def rgb_positions(run: object) -> tuple[str, ...]:
    """Return the images of the run whose unit is declared ``rgb``, sorted (nothing is read)."""
    return tuple(fov.relative_path for fov in run.fovs() if fov.image_type == "rgb")


def tissue_next_steps(mask_ids: Iterable[str], masks_root: object) -> str:
    """Return what to do with the tissue masks written: declare, run again, use as a region."""
    ids = sorted(set(mask_ids))
    if not ids:
        return "No mask was written."
    lines = [f"Written to the masks folder {masks_root}. Next:"]
    lines += [
        f"- declare the mask {mask_id} (source type {mask_id}) as binary, with source_channels = "
        '["R", "G", "B"]'
        for mask_id in ids
    ]
    lines.append("- then run preprocessing and the masks step again to check them.")
    lines.append(
        "- use it as a region (for example fiberfeatures MaskRequest(<ID>, 'region')); in "
        "memory, never pass it as cell-mode labels."
    )
    return "\n".join(lines)
