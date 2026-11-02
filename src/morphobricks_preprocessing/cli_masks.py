"""The command line's ``masks``: its options and what it prints (moved from :mod:`.cli`).

``masks`` is the optional mask step: it pairs every image with its masks (``--masks``, and
the label images of OME-Zarr images), reads them, and writes ``mask_relationships.json``
with each mask type and the relationships between them (contract §15). Like ``run``, it
takes every input it is not given from the run in the output folder (design v0.4 §1): the
experiment folder (``experiment_not_recorded`` without one), the masks folder, the mask
axes and the exclusions; a value given that differs is used and named. The pairing, the
scan and the write under the lock are :mod:`.run.mask_step`'s, shared with the notebook.
"""

from __future__ import annotations

import argparse

from .mask_scan import DEFAULT_THRESHOLDS, mask_thresholds
from .mask_text import mask_report_summary, mask_summary
from .report import format_issues
from .run.mask_step import describe_masks, prepare_mask_step
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
    step = prepare_mask_step(
        args.experiment,
        args.output,
        exclude=() if args.no_exclude else (tuple(args.exclude) or None),
        masks_folder=args.masks,
        mask_axes=args.mask_axes or None,
    )
    if step.refusal is not None:
        print(format_issues([step.refusal]))
        return 1
    print(mask_summary(step.masks))
    for change in step.changes:
        print(f"Changed {change.name}: {change.recorded} -> {change.given} ({change.effect})")
    if step.issues:
        print()
        print(format_issues(step.issues))
    if has_errors(step.issues):
        return 1
    if not step.masks.types:
        print("Give the masks folder with --masks, or use OME-Zarr images with label images.")
        return 1

    def progress(done: int, total: int, path: str) -> None:
        if path:
            print(f"[{done + 1}/{total}] {path}", flush=True)

    print()
    try:
        result = describe_masks(step, progress=progress, thresholds=thresholds)
    except ValueError as exc:  # masks whose pixels cannot be used (planes, labels)
        print(f"The masks cannot be used: {exc}")
        return 1
    if result.refusal is not None:
        print()
        print(format_issues([result.refusal]))
        return 1
    print()
    print(mask_report_summary(result.report))
    print()
    print(f"Report written to {result.path}")
    return 0
