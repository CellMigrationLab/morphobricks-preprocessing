"""The command line's ``run``: what it prints and runs (moved from :mod:`.cli`, PP-7).

Also the helpers every command shares: :func:`parse_size` and :func:`command_preflight`.
"""

from __future__ import annotations

import argparse
import re
from datetime import date
from pathlib import Path

from morphobricks_normalization.checks import IntensityCheckSettings
from morphobricks_normalization.ranges import HistogramSettings

from .axes import AxisMapping, reference_axes
from .axis_review import review_axes
from .design import (
    DESIGN_FILENAME,
    DesignFormatError,
    ExperimentDesign,
    design_issues,
    read_experiment_design,
    reconcile_design,
    write_experiment_design,
)
from .discovery import check_output_folder, default_output_folder
from .image_type import RGB, ImageTypeReview, review_image_type
from .intensity import intensity_text
from .report import axis_check_summary, design_summary, format_issues, mapping_summary, run_summary
from .run import Preflight, preflight, run_experiment
from .validation import has_errors

_UNITS = {
    "": 1,
    "B": 1,
    "KIB": 2**10,
    "MIB": 2**20,
    "GIB": 2**30,
    "KB": 10**3,
    "MB": 10**6,
    "GB": 10**9,
}


def parse_size(text: str) -> int:
    """Parse a memory size such as ``"1GiB"``, ``"512 MiB"`` or ``"1073741824"``.

    Raises
    ------
    argparse.ArgumentTypeError
        If the text is not a positive size.
    """
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([A-Za-z]*)\s*", text)
    unit = match.group(2).upper() if match else None
    if not match or unit not in _UNITS or float(match.group(1)) <= 0:
        raise argparse.ArgumentTypeError(
            f"{text!r} is not a memory size; use for example 1GiB, 512MiB or a number of bytes."
        )
    return int(float(match.group(1)) * _UNITS[unit])


def command_preflight(args: argparse.Namespace) -> Preflight:
    """Run :func:`preflight` with the options every command shares."""
    return preflight(
        args.experiment,
        args.output,
        exclude=args.exclude,
        masks_folder=args.masks,
        mask_axes=args.mask_axes,
    )


def run_command(args: argparse.Namespace) -> int:  # noqa: PLR0915
    """Run pass 2 with the command's options, print what it did and return the status."""
    pre = command_preflight(args)
    axes = reference_axes(pre.metadata.files)
    if axes is None:
        print(format_issues(pre.issues) or "No readable images were found.")
        return 1
    try:
        mapping = AxisMapping(axes, tuple(args.axes))
    except ValueError:
        print(
            f"--axes {args.axes} does not fit the images, whose axes are {axes}: give one role "
            f"(X, Y, Z, C, T or -) for each of the {len(axes)} axes, for example "
            f"--axes {pre.suggested_mapping.roles_string if pre.suggested_mapping else axes}."
        )
        return 1
    try:
        intensity_settings = IntensityCheckSettings(
            outlier_limit=args.outlier_limit,
            saturation_limit=args.saturation_limit,
            min_images=args.min_images,
        )
        histogram_settings = HistogramSettings(
            histogram_range=args.histogram_range, robust_quantiles=tuple(args.robust_quantiles)
        )
    except ValueError as exc:
        print(exc)
        return 1
    design = None
    # Folder problems are reported by the run itself; the design is only asked about
    # once the folders can be processed.
    if not has_errors([*pre.discovery.issues, *pre.exclusion_issues]):
        design, message = _design_for_run(pre, args)
        if design is None:
            print(message)
            return 1
    print("Axis roles:")
    print(mapping_summary(mapping, pre.metadata.files))
    print()
    review = review_axes(pre.metadata, mapping, missing_as_singleton=args.missing_as_singleton)
    print(axis_check_summary(review))
    print()
    type_review = review_image_type(pre.metadata, review)
    image_type = args.image_type or type_review.suggested
    print(f"Image type: {image_type}")
    print(_image_type_note(type_review, args.image_type))
    print()
    if design is not None:
        print("Experiment design:")
        print(design_summary(design))
        print()

    def progress(done: int, total: int, path: str) -> None:
        if path:
            print(f"[{done + 1}/{total}] {path}", flush=True)

    result = run_experiment(
        args.experiment,
        mapping,
        output_folder=args.output,
        bin_count=args.bins,
        memory_limit=args.memory_limit,
        missing_as_singleton=args.missing_as_singleton,
        exclude=args.exclude,
        force_rebuild=args.force_rebuild,
        progress=progress,
        design=design,
        masks_folder=args.masks,
        mask_axes=args.mask_axes,
        image_type=image_type,
        intensity_settings=intensity_settings,
        histogram_settings=histogram_settings,
    )
    if result.issues:
        print(format_issues(result.issues))
        print()
    print(run_summary(result))
    if result.intensity is not None:
        print()
        print(intensity_text(result.intensity))
    return 0 if result.written else 1


def _design_for_run(
    pre: Preflight, args: argparse.Namespace
) -> tuple[ExperimentDesign | None, str]:
    """Return the confirmed experiment design, or None and what the user has to do.

    Without a fully confirmed design, a template (keeping every answer already
    given) is written to the output folder for the user to fill in.
    """
    root = pre.discovery.root
    output = Path(args.output) if args.output else default_output_folder(root)
    source = Path(args.design) if args.design else output / DESIGN_FILENAME
    previous = None
    if source.exists():
        try:
            previous = read_experiment_design(source)
        except DesignFormatError as exc:
            return None, (
                f"The experiment design cannot be read: {exc}\n"
                f"Correct the file, or delete it to get a new template."
            )
    elif args.design:
        return None, f"The experiment design {source} does not exist."
    design, _ = reconcile_design(previous, pre.dataset.repeats)
    # Rows confirmed by hand in the file get today's date.
    design = design.dated(date.today().isoformat())
    problems = design_issues(design)
    if not problems:
        return design, ""
    if check_output_folder(root, output):
        # The run reports the output folder; nothing is written inside the experiment.
        return design, ""
    target = write_experiment_design(design, output / DESIGN_FILENAME)
    lines = [
        format_issues(problems),
        "",
        f"The experiment design was written to {target}. Open it in a spreadsheet program "
        "or a text editor, one row per biological-repeat folder of each condition:",
        "  - repeat_group: rows with the same value are the same biological repeat across "
        "conditions (by default the folder name). Change it to pair repeats whose folders "
        "are named differently; each group may hold one folder per condition. It must not "
        "be empty or NA.",
        "  - confirmed_on: the date of the confirmation as YYYY-MM-DD, or empty (the run "
        "fills it in).",
        "  - confirmed: set it to true in every row of a repeat group once you are sure its "
        "images were prepared and acquired under the same circumstances, so they are "
        "comparable.",
        "  - control: set it to true in the rows of control conditions (optional; recorded only).",
        "Then run again.",
    ]
    return None, "\n".join(lines)


def _image_type_note(review: ImageTypeReview, given: str | None) -> str:
    """Say where the image type comes from and how to choose the other one."""
    if given is not None:
        return "  given with --image-type"
    other = "fluorescence" if review.suggested == RGB else RGB
    lines = [f"  suggested, because {review.reason}"]
    if other == RGB and not review.rgb_allowed:
        lines.append(f"  rgb is not possible: {review.rgb_problem}")
    else:
        lines.append(f"  Give --image-type {other} if these are {other} images.")
    return "\n".join(lines)
