"""The command line's ``convert-mask``: the explicit mask conversion helpers (Spec ADR 0005 BM12).

``convert-mask --output FOLDER --mask ID --to-instances NEW_ID`` writes, for every image of the
run in ``--output`` with a file of the ``binary`` mask ``ID``, its 8-connected objects as a new
``instances`` mask (``RunInputs.binary_to_instances``); ``--to-classes PREFIX`` writes one
``binary`` mask ``PREFIX_class<value>`` per class value of a 2-D class image declared
``parent_labels`` or ``instances`` (``RunInputs.classes_to_binary``). ``--image`` (repeatable)
converts only the images named. The outputs go to the run's masks folder, each with its line of
``mask_lineage.jsonl``; the source is never changed and nothing is overwritten. A same call
again is a no-op. The command prints what it wrote and the next steps: declare each output in
the description, then run preprocessing and the masks step again. It stops at the first
refusal (exit status 1), naming it; the images converted before it stay converted.
"""

from __future__ import annotations

import argparse

from .mask_conversion import MaskConversionResult, conversion_next_steps, images_with_mask
from .run_inputs import RunInputs
from .validation import plural


def add_convert_mask_parser(commands: argparse._SubParsersAction) -> argparse.ArgumentParser:
    """Add the ``convert-mask`` command and its options; return its parser.

    Parameters
    ----------
    commands
        The command line's subcommands.

    Returns
    -------
    argparse.ArgumentParser
        The ``convert-mask`` command's parser.
    """
    convert = commands.add_parser(
        "convert-mask",
        help="write new mask files from a checked mask: a binary mask's objects, or one binary "
        "mask per class of a class image (the source is only read)",
    )
    convert.add_argument(
        "--output", metavar="FOLDER", required=True, help="the output folder of the run"
    )
    convert.add_argument(
        "--mask", metavar="ID", required=True, help="the mask ID of the source, as declared"
    )
    target = convert.add_mutually_exclusive_group(required=True)
    target.add_argument(
        "--to-instances",
        metavar="NEW_ID",
        help="write the 8-connected objects of a binary mask as the instances mask NEW_ID",
    )
    target.add_argument(
        "--to-classes",
        metavar="PREFIX",
        help="write one binary mask PREFIX_class<value> per class value of a class image",
    )
    convert.add_argument(
        "--image",
        action="append",
        default=[],
        metavar="PATH",
        help="convert only this image, relative to the experiment folder (repeatable; default: "
        "every image with a file of the mask)",
    )
    return convert


def convert_mask_command(args: argparse.Namespace) -> int:
    """Convert the mask of each image; print the outputs and the next steps."""
    try:
        run = RunInputs.open(args.output)
    except ValueError as exc:
        print(f"The run cannot be used: {exc}")
        return 1
    images = (
        tuple(args.image)
        or images_with_mask(run, args.mask)
        or tuple(f.relative_path for f in run.files)
    )
    results = []
    for image in images:
        try:
            if args.to_instances is not None:
                result = run.binary_to_instances(image, args.mask, output_mask_id=args.to_instances)
            else:
                result = run.classes_to_binary(image, args.mask, output_prefix=args.to_classes)
        except ValueError as exc:  # every refusal of the helpers (RunInputsError included)
            print(f"{image}: not converted. {exc}")
            if results:
                print(f"{plural(len(results), 'image')} before it stay converted.")
            return 1
        results.append(result)
        print(_result_line(result))
    print()
    print(conversion_next_steps(results, run.masks_root))
    return 0


def _result_line(result: MaskConversionResult) -> str:
    """Return one image's outputs, or that it holds no class."""
    if not result.outputs:
        return f"{result.image}: {result.note}, nothing written"
    parts = [
        f"{output.path} ({'written' if output.published else 'already written'})"
        for output in result.outputs
    ]
    return f"{result.image}: " + ", ".join(parts)
