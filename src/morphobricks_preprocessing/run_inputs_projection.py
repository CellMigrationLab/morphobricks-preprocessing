"""The Z projection of a run for :class:`.run_inputs.RunInputs` (issue #176, run side).

A run on projected copies records a ``projection`` summary in its manifest
(:mod:`.projection_run`). :meth:`.run_inputs.RunInputs.open` checks it against the conversion
record the run names (:func:`.conversion_check.conversion_problem`): the summary must be
exactly what the record says for the recorded images and masks, so projected copies are never
read as plain images and a record changed by hand is refused.
"""

from __future__ import annotations

from .manifest import RunManifest
from .projection_run import RunProjection


class ProjectionInputs:
    """Methods of ``RunInputs`` that need the projection."""

    manifest: RunManifest

    @property
    def projection(self) -> RunProjection | None:
        """Return how the run's images were projected, or None for an unprojected run.

        CellPatcher and MorphoFeature carry this into their own records (method, slices
        used per image, source SHA-256, the conversion record's SHA-256, and the warning
        ``masks_on_projected_images`` when masks are paired with the projections).
        """
        recorded = self.manifest.projection
        return None if recorded is None else RunProjection.from_dict(recorded)
