"""The command line's ``tissue-mask``: the opt-in H&E tissue mask helper (Spec ADR 0005 BM15).

``tissue-mask --output FOLDER --to NEW_ID`` writes, for every image of the run in ``--output``
whose unit is declared ``rgb`` (or only the ``--image`` paths given, repeatable), its H&E tissue
mask as the new ``binary`` region mask ``NEW_ID`` (``RunInputs.he_tissue_mask``, rule
``he_tissue_v1``, provisional): nuclei, red blood cells and empty space left out, sizes in
pixels. The outputs go to the run's masks folder, each with its line of ``mask_lineage.jsonl``;
the image is never changed and nothing is overwritten. A same call again is a no-op, unless a
code identity is unverified: such a mask is written but never reused, so a retry is refused.
The command prints what it wrote, the kept share, and the next steps (declare the mask
``binary``, run preprocessing and the masks step again, use it as a region). It stops at the
first refusal (exit status 1), naming it; the images written before it stay written. Without
scikit-image it names the extra to install and writes nothing (exit status 1).
"""

from __future__ import annotations

import argparse

from .plane_errors import PlaneReadError
from .run_inputs import RunInputs
from .run_inputs_tissue import (
    HE_TISSUE_V1,
    NOT_REUSED,
    PROVISIONAL_NOTE,
    rgb_positions,
    tissue_next_steps,
)
from .validation import plural


def add_tissue_mask_parser(commands: argparse._SubParsersAction) -> argparse.ArgumentParser:
    """Add the ``tissue-mask`` command and its options; return its parser.

    Parameters
    ----------
    commands
        The command line's subcommands.

    Returns
    -------
    argparse.ArgumentParser
        The ``tissue-mask`` command's parser.
    """
    tissue = commands.add_parser(
        "tissue-mask",
        help="write an H&E tissue mask (nuclei, red blood cells and empty space left out) of "
        "each RGB image as a new binary region mask (needs the he-mask extra)",
    )
    tissue.add_argument(
        "--output", metavar="FOLDER", required=True, help="the output folder of the run"
    )
    tissue.add_argument(
        "--to",
        metavar="NEW_ID",
        required=True,
        help="the new mask ID; declare it binary afterwards",
    )
    tissue.add_argument(
        "--image",
        action="append",
        default=[],
        metavar="PATH",
        help="only this image, relative to the experiment folder (repeatable; default: every "
        "image of a unit declared rgb)",
    )
    tissue.add_argument(
        "--rule",
        default=HE_TISSUE_V1,
        help=f"the fixed rule (default and only rule: {HE_TISSUE_V1}, provisional)",
    )
    return tissue


def tissue_mask_command(args: argparse.Namespace) -> int:
    """Write the tissue mask of each image; print the outputs and the next steps."""
    try:
        run = RunInputs.open(args.output)
    except ValueError as exc:
        print(f"The run cannot be used: {exc}")
        return 1
    images = tuple(args.image) or rgb_positions(run)
    if not images:
        print("The run has no image of a unit declared rgb; nothing was written.")
        return 1
    print(PROVISIONAL_NOTE)
    written = []
    for image in images:
        try:
            result = run.he_tissue_mask(image, output_mask_id=args.to, rule=args.rule)
        # Every refusal: RunInputsError, a missing extra, and a plane that cannot be read (#469).
        except (ValueError, ImportError, PlaneReadError, OSError) as exc:
            print(f"{image}: no tissue mask written. {exc}")
            if written:
                print(f"{plural(len(written), 'image')} before it stay written.")
            return 1
        written.append(result)
        state = "written" if result.published else "already written"
        kept = 100 * result.fractions["kept"]
        line = f"{image}: {result.path} ({state}), kept {kept:.1f} %"
        if kept == 0:
            line += " (nothing kept: an empty region)"
        print(line if result.reusable else f"{line}; {NOT_REUSED}")
    print()
    print(tissue_next_steps((r.mask_id for r in written), run.masks_root))
    return 0
