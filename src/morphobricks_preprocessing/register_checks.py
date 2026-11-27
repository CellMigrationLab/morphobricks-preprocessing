"""Cell 5's checks of the app of cell 3 against the run cell 4 segmented (moved, v0.9.1).

The refusal helpers of :mod:`.notebook_mask_register` (a declaration that collides, a run
published again, files the run did not record, the app's experiment or axes) moved here
unchanged, to keep that module under the 600-line rule.

Whether cell 5 can keep cell 3's confirmed axes: nothing changed since its folder check.

v0.9.1 (owner, 26 November 2026): **Register the masks** needed two presses. Its first press
made the app's whole folder check again only to pair the masks folder, and that check always
clears the confirmed axes, so the press stopped to have them confirmed again. Now, when the
folders and the image headers are unchanged since cell 3's check, cell 5 pairs the masks with
the app's own *Check the masks again* (which reads only the masks' headers and keeps the rest)
and goes on in the same press. Unchanged means, for every image of the segmented run:

- cell 3's check is of the run's experiment, and its axes are confirmed;
- the images it checked are exactly the run's images (the run's own opening has already listed
  the folders for any file added since the run);
- each image's fingerprint now (size and modification time, :mod:`.fingerprint`) is the one
  its header was read at (``Preflight.header_fingerprints``, bound when cell 3 checked it).

Anything else is a change, and :func:`changes_since_check` says which, in words, so cell 5 can
say why the axes must be confirmed again. Nothing here decides an axis or reads a pixel.
"""

from __future__ import annotations

from pathlib import Path

from .description_identity import nfc
from .fingerprint import current_fingerprint
from .manifest import MANIFEST_FILENAME, ManifestFormatError, read_manifest
from .mask_answers import INSTANCES
from .report import mapping_summary
from .run_inputs import UnrecordedFilesError
from .segmentation_cellpose_run import manifest_sha256
from .validation import plural

COLLISION = (
    "The mask type {mask_type} collides with the mask {mask_id} the description already "
    "declares (source type {source_type}). Choose another mask type in cell 4 and segment "
    "again; nothing is declared twice."
)
CHANGED = "The images changed since the run, or the run cannot be opened: {reason}"
OTHER_EXPERIMENT = (
    "The app of cell 3 now holds another experiment ({app_root}) than the one cell 4 "
    "segmented ({segmented}). Check {segmented} again in cell 3 and confirm its axes, then "
    "press Register the masks again. Nothing was declared."
)
OTHER_AXES = (
    "The axes confirmed in cell 3 ({confirmed}) are not those the segmented run recorded "
    "({recorded}): its masks were made on the images read with the recorded axes. In cell 3, "
    "step Axes, confirm the recorded axes, then press Register the masks again. Nothing was "
    "declared."
)
SHOWN = 3
"""How many changed images a reason names."""


def changes_since_check(app: object, inputs: object) -> list[str]:
    """Return what changed since the app's folder check, in plain words; [] when nothing did.

    Parameters
    ----------
    app
        The Preprocessing app of cell 3 (its ``preflight`` and ``confirmed_mapping``).
    inputs
        The run cell 4 segmented (:class:`.run_inputs.RunInputs`).

    Returns
    -------
    list of str
        Each reason the confirmed axes cannot be kept; empty when they can.
    """
    pre = getattr(app, "preflight", None)
    if pre is None:
        return ["cell 3 holds no folder check"]
    if getattr(app, "confirmed_mapping", None) is None:
        return ["the axes are not confirmed in cell 3"]
    root = Path(pre.discovery.root).resolve()
    if root != Path(inputs.experiment_root).resolve():
        return [f"cell 3 checked another experiment ({root})"]
    bound = pre.header_fingerprints
    images = sorted(entry.relative_path for entry in inputs.manifest.files)
    if not bound or sorted(bound) != images:
        return ["the images cell 3 checked are not the images of the run"]
    changed = [
        rel for rel in images if bound[rel] is None or current_fingerprint(root / rel) != bound[rel]
    ]
    if not changed:
        return []
    named = ", ".join(changed[:SHOWN]) + (", …" if len(changed) > SHOWN else "")
    return [f"{plural(len(changed), 'image')} changed since cell 3 checked the folder ({named})"]


def _collision(app: object, mask_type: str) -> str:
    """Return the refusal of ``mask_type`` when the app's description declares it, else "".

    A declaration whose mask ID or source type is ``mask_type`` (ignoring case) collides,
    unless it is exactly the one offered (an earlier press of **Register the masks** that
    stopped later, at the save or the run, already gave it).
    """
    answers = getattr(app, "draft_answers", None)
    masks = getattr(answers, "masks", None) or {}
    folded = nfc(mask_type).casefold()
    for mask_id, mask in masks.items():
        offered = (mask_id, mask.source_type, mask.representation)
        if offered == (mask_type, mask_type, INSTANCES):
            continue
        if folded in (nfc(mask_id).casefold(), nfc(mask.source_type).casefold()):
            return COLLISION.format(
                mask_type=mask_type, mask_id=mask_id, source_type=mask.source_type
            )
    return ""


def _same_publication(segmentation: object, segmented: Path) -> bool:
    """Return whether the run's manifest is the one cell 4 segmented, or cell 5's own.

    Cell 4 pins the manifest's SHA-256 when it opens the run (``run_manifest_sha256``,
    :func:`.segmentation_cellpose_run.open_pinned`); a run published again since (cell 3 run
    again, perhaps on changed images or other axes) is not the run the masks were made on. The
    only other publication accepted is the one cell 5 made registering them
    (``registered_manifest``), so that cell 5 run again after it is not refused.
    """
    now = manifest_sha256(segmented)
    pinned = getattr(segmentation.workflow, "run_manifest_sha256", None)
    ours = getattr(segmentation, "registered_manifest", None)
    return now is not None and now in {pinned, ours}


def _only_written(exc: UnrecordedFilesError, masks: Path, types: tuple[str, ...]) -> bool:
    """Return whether the run left out only masks cell 4 wrote: files in ``masks/<type>/``.

    Those are the masks to register (the run before them could not record them). An image,
    a label image or any other mask the run did not record is still refused.
    """
    return not exc.files.images and all(
        len(Path(path).parts) > 1 and Path(path).parts[0] in types and (masks / path).is_file()
        for path in exc.files.masks
    )


def _mapping_text(mapping: object) -> str:
    """Return an axis mapping on one line (:func:`.report.mapping_summary`'s words)."""
    return "; ".join(line.strip() for line in mapping_summary(mapping).splitlines())


def _other_experiment(app: object, inputs: object) -> str:
    """Return why the app's experiment or confirmed axes are not the segmented run's, or "".

    The app's checked experiment must be the run's experiment, and the axes confirmed in the
    app must be the axis mapping the run's manifest records: the masks were made on the images
    read with those axes. Read before anything is declared, saved or run.
    """
    app_root = Path(app.preflight.discovery.root).resolve()
    segmented = Path(inputs.experiment_root).resolve()
    if app_root != segmented:
        return OTHER_EXPERIMENT.format(app_root=app_root, segmented=segmented)
    try:
        recorded = read_manifest(Path(inputs.output_folder) / MANIFEST_FILENAME).mapping
    except (OSError, ManifestFormatError) as exc:
        return CHANGED.format(reason=exc)
    confirmed = app.confirmed_mapping
    if (confirmed.axes, tuple(confirmed.roles)) != (recorded.axes, tuple(recorded.roles)):
        return OTHER_AXES.format(
            confirmed=_mapping_text(confirmed), recorded=_mapping_text(recorded)
        )
    return ""


__all__ = [
    "CHANGED",
    "COLLISION",
    "OTHER_AXES",
    "OTHER_EXPERIMENT",
    "changes_since_check",
]
