"""The optional mask step: describe the masks and write ``mask_relationships.json``.

The command line's ``masks`` and the notebook's **Describe the masks** (plan §10, PP-10d)
run the same two functions (contract §15):

- :func:`prepare_mask_step` pairs every image with its masks, with the inputs the run in the
  output folder recorded unless they are given (design v0.4 §1): the experiment folder
  (``experiment_not_recorded`` without one), the masks folder, the mask axes and the
  exclusions; a value given that differs is used and named. It reads no pixels.
- :func:`describe_masks` reads the masks (:func:`.mask_scan.scan_masks`) and writes the
  report, holding the output folder's lock while it writes: a run may be replacing or
  removing the same file.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from ..discovery import default_output_folder
from ..mask_scan import MASK_REPORT_FILENAME, MaskReport, scan_masks, write_mask_report
from ..masks import MaskInventory
from ..validation import ValidationIssue, has_errors
from .lock import OutputLockedError, lock_issue, output_lock
from .preflight import Preflight, preflight
from .recorded import GivenInputs, InputChange, resolve_inputs
from .reuse import read_previous, recorded_experiment_root

_RECORDED = ("experiment_root", "exclude", "masks_folder", "mask_axes")
"""The recorded inputs the mask step uses."""
_EFFECT = "used for this scan only; the run keeps the recorded value until it is given this one"


@dataclass(frozen=True)
class MaskStep:
    """The masks the mask step would read, found with the inputs it uses.

    Attributes
    ----------
    preflight
        The folder check with the masks paired; None when refused.
    output_folder
        Where the report is written; None when refused.
    changes
        The inputs given that differ from the recorded ones, with their effect.
    issues
        The issues that stop the step when one is an error: the folder's, the
        exclusions' and the masks'.
    refusal
        ``experiment_not_recorded`` when no experiment folder was given or recorded.
    """

    preflight: Preflight | None
    output_folder: Path | None
    changes: tuple[InputChange, ...] = ()
    issues: tuple[ValidationIssue, ...] = ()
    refusal: ValidationIssue | None = None

    @property
    def masks(self) -> MaskInventory:
        """The masks found (none when refused)."""
        if self.preflight is None or self.preflight.masks is None:
            return MaskInventory(folder=None)
        return self.preflight.masks

    @property
    def ready(self) -> bool:
        """True when the masks can be read: no refusal, no error, at least one mask type."""
        return self.refusal is None and not has_errors(self.issues) and bool(self.masks.types)


@dataclass(frozen=True)
class MaskStepResult:
    """What :func:`describe_masks` did.

    Attributes
    ----------
    report
        The description of the masks.
    path
        The report written, or None when the output folder's lock refused it.
    refusal
        ``output_locked`` when another run holds the output folder's lock, else None.
    """

    report: MaskReport
    path: Path | None
    refusal: ValidationIssue | None = None


def prepare_mask_step(
    experiment_root: str | os.PathLike | None,
    output_folder: str | os.PathLike | None,
    *,
    exclude: Sequence[str] | None = None,
    masks_folder: str | os.PathLike | None = None,
    mask_axes: Mapping[str, str] | None = None,
) -> MaskStep:
    """Pair the masks the mask step reads, with the inputs the earlier run recorded.

    Parameters
    ----------
    experiment_root
        The experiment folder; None takes the one the run in ``output_folder`` recorded.
    output_folder
        The output folder; None is the default next to the experiment.
    exclude
        Images to leave out; None takes the recorded exclusions, ``()`` clears them.
    masks_folder
        The masks folder; None takes the recorded one.
    mask_axes
        Mask axes stated per mask type; they add to (and replace per type) the recorded ones.

    Returns
    -------
    MaskStep
        With ``refusal`` set when no experiment folder was given or recorded.
    """
    if experiment_root is None:
        recorded, missing = recorded_experiment_root(
            output_folder and Path(output_folder).resolve()
        )
        if missing is not None:
            return MaskStep(preflight=None, output_folder=None, refusal=missing)
        experiment_root = str(recorded)
    root = Path(experiment_root).resolve()
    output = Path(output_folder) if output_folder else default_output_folder(root)
    given = GivenInputs(
        experiment_root=root,
        exclude=None if exclude is None else tuple(exclude),
        masks_folder=Path(masks_folder).resolve() if masks_folder else None,
        mask_axes=mask_axes or None,
    )
    resolved = resolve_inputs(given, read_previous(output.resolve())[0])
    changes = tuple(
        replace(change, effect=_EFFECT) for change in resolved.changes if change.name in _RECORDED
    )
    pre = preflight(
        experiment_root,
        output_folder,
        exclude=list(resolved.exclude),
        masks_folder=resolved.masks_folder,
        mask_axes=resolved.mask_axes,
    )
    pre = replace(pre, masks=pre.masks or MaskInventory(folder=None))  # none found
    return MaskStep(
        preflight=pre,
        output_folder=output,
        changes=changes,
        issues=(*pre.discovery.issues, *pre.exclusion_issues, *pre.masks.issues),
    )


def describe_masks(
    step: MaskStep,
    *,
    thresholds: Mapping[str, float] | None = None,
    progress: Callable[[int, int, str], None] | None = None,
) -> MaskStepResult:
    """Read the masks of a ready :class:`MaskStep` and write ``mask_relationships.json``.

    Parameters
    ----------
    step
        The masks to read; :attr:`MaskStep.ready`.
    thresholds
        Thresholds to change from their defaults (:func:`.mask_scan.mask_thresholds`).
    progress
        Called as ``progress(done, total, image)`` before each image is read, and once
        with ``image`` empty at the end.

    Returns
    -------
    MaskStepResult
        The report is written to the output folder holding its lock; ``refusal`` when
        another run holds it (nothing is written).

    Raises
    ------
    ValueError
        For a step that is not ready, and for masks whose pixels cannot be used (planes,
        labels; :func:`.mask_scan.scan_masks`).
    """
    if not step.ready or step.preflight is None or step.output_folder is None:
        raise ValueError("the masks cannot be described: the step has errors or no masks")
    report = scan_masks(
        step.masks, step.preflight.discovery.root, progress=progress, thresholds=thresholds
    )
    output = step.output_folder
    output.mkdir(parents=True, exist_ok=True)
    try:
        with output_lock(output):  # a run may be replacing or removing the same file
            path = write_mask_report(report, output / MASK_REPORT_FILENAME)
    except OutputLockedError as exc:
        return MaskStepResult(report=report, path=None, refusal=lock_issue(exc))
    return MaskStepResult(report=report, path=path)
