"""The optional mask step: describe the masks and write ``mask_relationships.json``.

The command line's ``masks`` and the notebook's **Describe the masks** (plan §10, PP-10d)
run the same two functions (contract §15):

- :func:`prepare_mask_step` pairs every image with its masks, with the inputs the run in the
  output folder recorded unless they are given (design v0.4 §1): the experiment folder
  (``experiment_not_recorded`` without one), the masks folder, the mask axes and the
  exclusions; a value given that differs is used and named. It reads no pixels.
- :func:`recorded_masks` says what the run in the output folder recorded (a v2 manifest):
  the notebook offers the step only when it recorded masks, also for a run of an earlier
  session.
- :func:`describe_masks` reads the masks (:func:`.mask_scan.scan_masks`) and writes the
  report, holding the output folder's lock from the first mask read to the write: a run
  may be replacing or removing the same file. A locked folder is refused before any mask
  is read.

Every mask is bound to its fingerprint from its header read to the end of its scan (issue
#165, :mod:`..mask_binding`): :func:`prepare_mask_step` fingerprints each mask before and
after its header is read, and the scan refuses a mask that no longer has that fingerprint
or changes while it is read (``mask_changed_during_scan``, also its content SHA-256 when
``verify_content`` is on: given, else the run's recorded setting). Nothing is written then.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from ..discovery import default_output_folder
from ..mask_representation import check_representation
from ..mask_scan import MASK_REPORT_FILENAME, MaskReport, scan_masks, write_mask_report
from ..masks import MaskInventory
from ..validation import ValidationIssue, has_errors
from .description_run import load_description
from .lock import OutputLockedError, lock_issue, output_lock
from .preflight import Preflight, preflight
from .recorded import GivenInputs, InputChange, resolve_inputs, usable
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
    verify_content
        Compare each mask's content SHA-256 before and after it is read, not only its
        fingerprint (given, else the run's recorded setting).
    """

    preflight: Preflight | None
    output_folder: Path | None
    changes: tuple[InputChange, ...] = ()
    issues: tuple[ValidationIssue, ...] = ()
    refusal: ValidationIssue | None = None
    verify_content: bool = False

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
        The description of the masks, or None when the output folder's lock refused the
        step (no mask was read).
    path
        The report written, or None when the output folder's lock refused it.
    refusal
        ``output_locked`` when another run holds the output folder's lock, else None.
    issues
        With a published description: ``R_MASK_CONTRADICTS_REPRESENTATION`` for each mask file
        that holds more than two values though declared ``binary`` (such a file stays ``not yet
        checked``; the report is written all the same), or the error that the description
        cannot be read. Empty without a description.
    """

    report: MaskReport | None
    path: Path | None
    refusal: ValidationIssue | None = None
    issues: tuple[ValidationIssue, ...] = ()


@dataclass(frozen=True)
class RecordedMasks:
    """The run in an output folder, as the mask step would describe it.

    Attributes
    ----------
    output_folder
        The output folder, resolved.
    experiment_root
        The experiment folder the run recorded.
    masks_folder
        The masks folder it recorded, or None (masks of OME-Zarr images only, or none).
    mask_types
        The mask types it recorded; empty when it recorded no masks.
    written_at
        When its manifest was written (UTC, ISO 8601), or empty when unknown.
    """

    output_folder: Path
    experiment_root: Path
    masks_folder: Path | None
    mask_types: tuple[str, ...]
    written_at: str = ""


def prepare_mask_step(
    experiment_root: str | os.PathLike | None,
    output_folder: str | os.PathLike | None,
    *,
    exclude: Sequence[str] | None = None,
    masks_folder: str | os.PathLike | None = None,
    mask_axes: Mapping[str, str] | None = None,
    verify_content: bool | None = None,
) -> MaskStep:
    """Pair the masks the mask step reads, with the inputs the earlier run recorded.

    Each mask is fingerprinted before and after its header is read
    (:attr:`.masks.MaskFile.bound`); :func:`describe_masks` reads only masks that still
    have that fingerprint.

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
    verify_content
        Also compare each mask's content SHA-256 while it is read; None takes the run's
        recorded setting (False without one).

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
        verify_content=verify_content,
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
        bind_headers=True,
    )
    pre = replace(pre, masks=pre.masks or MaskInventory(folder=None))  # none found
    return MaskStep(
        preflight=pre,
        output_folder=output,
        changes=changes,
        issues=(*pre.discovery.issues, *pre.exclusion_issues, *pre.masks.issues),
        verify_content=resolved.verify_content,
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
        The masks are read and the report written holding the output folder's lock;
        ``refusal`` when another run holds it (no mask is read, nothing is written).

    Raises
    ------
    ValueError
        For a step that is not ready, and for masks whose pixels cannot be used (planes,
        labels; :func:`.mask_scan.scan_masks`); :class:`..mask_binding.MaskChangedError`
        (``mask_changed_during_scan``) for masks that changed since their headers were read
        or while they were read. No report is written then, and an earlier one is left as
        it was.
    """
    if not step.ready or step.preflight is None or step.output_folder is None:
        raise ValueError("the masks cannot be described: the step has errors or no masks")
    output = step.output_folder
    output.mkdir(parents=True, exist_ok=True)
    try:
        # Held from the first mask read to the write: a run may be replacing the masks'
        # recorded inputs or removing the report; a locked folder is refused before any read.
        with output_lock(output):
            report = scan_masks(
                step.masks,
                step.preflight.discovery.root,
                progress=progress,
                thresholds=thresholds,
                verify_content=step.verify_content,
            )
            issues = _representation_issues(step, output)
            path = write_mask_report(report, output / MASK_REPORT_FILENAME)
    except OutputLockedError as exc:
        return MaskStepResult(report=None, path=None, refusal=lock_issue(exc))
    return MaskStepResult(report=report, path=path, issues=issues)


def _representation_issues(step: MaskStep, output: Path) -> tuple[ValidationIssue, ...]:
    """Check the declared masks' representations when a description is published.

    The caller holds the output folder's lock and has read the masks. Without a published
    description nothing is checked or written (a v0.4 output folder).
    """
    described, problems = load_description(output)
    if described is None or step.preflight is None:
        return tuple(problems)
    root = step.preflight.discovery.root
    return check_representation(described.description, step.masks, root, output).issues


def recorded_masks(output_folder: str | os.PathLike) -> RecordedMasks | None:
    """Return what the run in the output folder recorded for the mask step (v2 manifest).

    Parameters
    ----------
    output_folder
        The output folder.

    Returns
    -------
    RecordedMasks or None
        None without a usable (v0.4.0 or later) manifest; ``mask_types`` is empty when the
        run recorded no masks.
    """
    output = Path(output_folder).resolve()
    manifest = usable(read_previous(output)[0])
    if manifest is None:
        return None
    masks = manifest.masks or {}
    folder = masks.get("folder")
    return RecordedMasks(
        output_folder=output,
        experiment_root=Path(manifest.experiment_root),
        masks_folder=Path(folder) if folder else None,
        mask_types=tuple(masks.get("types") or ()),
        written_at=manifest.written_at,
    )
