"""The command line's ``masks``: its options and what it prints (moved from :mod:`.cli`).

``masks`` is the optional mask step: it pairs every image with its masks (``--masks``, and
the label images of OME-Zarr images), reads them, and writes ``mask_relationships.json``
with each mask type and the relationships between them (contract §15). Like ``run``, it
takes every input it is not given from the run in the output folder (design v0.4 §1): the
experiment folder (``experiment_not_recorded`` without one), the masks folder, the mask
axes and the exclusions; a value given that differs is used and named.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path

from .cli_run import command_preflight, use_recorded_experiment
from .discovery import default_output_folder
from .mask_scan import (
    DEFAULT_THRESHOLDS,
    MASK_REPORT_FILENAME,
    mask_thresholds,
    scan_masks,
    write_mask_report,
)
from .masks import MaskInventory
from .report import format_issues, mask_report_summary, mask_summary
from .run import OutputLockedError
from .run.lock import lock_issue, output_lock
from .run.recorded import GivenInputs, InputChange, resolve_inputs
from .run.reuse import read_previous
from .validation import has_errors

THRESHOLD_HELP = {
    "inside_pixel_share": "share of a child's pixels in one parent for it to be inside",
    "inside_object_share": "share of the children inside for a mask to be inside another",
    "parent_larger_share": "share of the inside children whose parent is larger",
    "same_objects_iou": "intersection over union for two objects to match one to one",
    "same_objects_share": "share of the objects of both masks matched for the same objects",
    "grouped_label_share": "share of a grouped mask's pixels carrying the parent's label",
    "grouped_median_pieces": "median pieces per label above which a mask is grouped",
}


_RECORDED = ("experiment_root", "exclude", "masks_folder", "mask_axes")
"""The recorded inputs ``masks`` uses."""
_EFFECT = "used for this scan only; the run keeps the recorded value until it is given this one"


def add_masks_arguments(masks: argparse.ArgumentParser) -> None:
    """Add the options of ``masks`` after the shared ones.

    Parameters
    ----------
    masks
        The ``masks`` command's parser.
    """
    masks.add_argument(
        "--no-exclude",
        action="store_true",
        help="clear the exclusions of the earlier run for this scan (not with --exclude)",
    )
    masks.add_argument("--log", metavar="FILE", help="write technical details to this file")
    limits = masks.add_argument_group(
        "relationship thresholds", "change the defaults (contract §15.1); shares are 0 to 1"
    )
    for name, text in THRESHOLD_HELP.items():
        limits.add_argument(
            "--" + name.replace("_", "-"),
            dest=name,
            type=float,
            metavar="X",
            help=f"{text} (default {DEFAULT_THRESHOLDS[name]:g})",
        )


def masks_command(args: argparse.Namespace) -> int:
    """Scan the masks and write ``mask_relationships.json``, holding the folder's lock."""
    changes = {name: getattr(args, name) for name in THRESHOLD_HELP}
    try:
        thresholds = mask_thresholds({k: v for k, v in changes.items() if v is not None})
    except ValueError as exc:
        print(exc)
        return 1
    if not use_recorded_experiment(args):
        return 1
    changes = _recorded_inputs(args)
    pre = command_preflight(args)
    pre = replace(pre, masks=pre.masks or MaskInventory(folder=None))  # none found
    print(mask_summary(pre.masks))
    for change in changes:
        print(f"Changed {change.name}: {change.recorded} -> {change.given} ({change.effect})")
    blocking = [*pre.discovery.issues, *pre.exclusion_issues, *pre.masks.issues]
    if blocking:
        print()
        print(format_issues(blocking))
    if has_errors(blocking):
        return 1
    if not pre.masks.types:
        print("Give the masks folder with --masks, or use OME-Zarr images with label images.")
        return 1

    def progress(done: int, total: int, path: str) -> None:
        if path:
            print(f"[{done + 1}/{total}] {path}", flush=True)

    print()
    try:
        report = scan_masks(pre.masks, pre.discovery.root, progress=progress, thresholds=thresholds)
    except ValueError as exc:  # masks whose pixels cannot be used (planes, labels)
        print(f"The masks cannot be used: {exc}")
        return 1
    output = Path(args.output) if args.output else default_output_folder(pre.discovery.root)
    output.mkdir(parents=True, exist_ok=True)
    try:
        with output_lock(output):  # a run may be replacing or removing the same file
            path = write_mask_report(report, output / MASK_REPORT_FILENAME)
    except OutputLockedError as exc:
        print()
        print(format_issues([lock_issue(exc)]))
        return 1
    print()
    print(mask_report_summary(report))
    print()
    print(f"Report written to {path}")
    return 0


def _recorded_inputs(args: argparse.Namespace) -> tuple[InputChange, ...]:
    """Fill the masks folder, mask axes and exclusions not given from the recorded run.

    Returns the inputs given that differ from the recorded ones, with their effect.
    """
    root = Path(args.experiment).resolve()
    output = Path(args.output) if args.output else default_output_folder(root)
    exclude = () if args.no_exclude else (tuple(args.exclude) or None)
    given = GivenInputs(
        experiment_root=root,
        exclude=exclude,
        masks_folder=Path(args.masks).resolve() if args.masks else None,
        mask_axes=args.mask_axes or None,
    )
    resolved = resolve_inputs(given, read_previous(output.resolve())[0])
    args.masks, args.mask_axes = resolved.masks_folder, resolved.mask_axes
    args.exclude = list(resolved.exclude)
    return tuple(
        replace(change, effect=_EFFECT) for change in resolved.changes if change.name in _RECORDED
    )
