"""The command line's ``run``: its options and what it prints (moved from :mod:`.cli`).

Every input left out is the earlier run's in the same output folder (design v0.4 §1):
``--axes`` is needed for the first run and for plain stacks that are new or changed;
``--no-masks`` drops the recorded masks folder; ``--mode full`` recomputes every row.
Also the helpers every command shares: :func:`parse_size` and :func:`command_preflight`.
"""

from __future__ import annotations

import argparse
import re
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Literal

from morphobricks_normalization.checks import IntensityCheckSettings
from morphobricks_normalization.ranges import (
    DEFAULT_ROBUST_QUANTILES,
    HISTOGRAM_RANGES,
    HistogramSettings,
)

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
from .image_type import IMAGE_TYPES, RGB, ImageTypeReview, review_image_type
from .intensity import intensity_text
from .manifest import RunManifest
from .planes import DEFAULT_MEMORY_LIMIT
from .report import (
    axis_check_summary,
    design_summary,
    format_issues,
    mapping_summary,
    run_summary,
)
from .run import MODES, RESTART, Preflight, preflight, run_experiment
from .run.recorded import usable
from .run.reuse import read_previous
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


def add_run_arguments(run: argparse.ArgumentParser) -> None:
    """Add the options of ``run`` after the shared ones.

    Parameters
    ----------
    run
        The ``run`` command's parser.
    """
    _run_inputs(run)
    _run_settings(run)
    run.add_argument("--log", metavar="FILE", help="write technical details to this file")


def _run_inputs(run: argparse.ArgumentParser) -> None:
    """Add the inputs of ``run``; each one left out is the earlier run's (design v0.4 §1)."""
    run.add_argument(
        "--axes",
        metavar="ROLES",
        help="role of each axis of the images, in the order of their axes: X, Y, Z, C, T, or "
        "- for none (for example ZCYX). 'check' prints the suggested value. Needed for the "
        "first run, and again for plain stacks that are new or changed",
    )
    run.add_argument(
        "--design",
        metavar="FILE",
        help="the confirmed experiment design (default: experiment_design.csv in the output "
        "folder); without a confirmed design, run writes a template there and stops",
    )
    run.add_argument(
        "--image-type",
        choices=IMAGE_TYPES,
        help="fluorescence (each channel a separate stain) or rgb (colour images with "
        "three channels); default: the earlier run's, else the type suggested from the "
        "image headers, printed",
    )
    run.add_argument(
        "--no-masks", action="store_true", help="drop the masks folder of the earlier run"
    )
    run.add_argument(
        "--missing-as-singleton",
        action=argparse.BooleanOptionalAction,
        help="confirm that images lacking some axes have one plane along them (default: as "
        "in the earlier run, else no)",
    )
    run.add_argument(
        "--mode",
        choices=MODES,
        default=RESTART,
        help="restart (default): reuse the rows of unchanged images; full: recompute every "
        "row and check. Both keep your confirmations and replace the results only on success",
    )


def _run_settings(run: argparse.ArgumentParser) -> None:
    """Add the settings of ``run``; each one left out is the earlier run's, else the default."""
    run.add_argument("--bins", type=int, help="bins per plane (default: depends on pixel type)")
    run.add_argument(
        "--histogram-range",
        choices=HISTOGRAM_RANGES,
        help="range of histograms that are not exact (float images, and integer images with "
        "more values than bins): minmax (default, each plane's minimum to maximum) or robust "
        "(between the --robust-quantiles, the pixels outside counted separately); 8- and "
        "16-bit histograms are the same either way",
    )
    run.add_argument(
        "--robust-quantiles",
        type=float,
        nargs=2,
        metavar=("LOW", "HIGH"),
        help="quantiles in percent of the robust range, also used by the range check "
        f"(default {DEFAULT_ROBUST_QUANTILES[0]:g} {DEFAULT_ROBUST_QUANTILES[1]:g})",
    )
    run.add_argument(
        "--memory-limit",
        type=parse_size,
        default=DEFAULT_MEMORY_LIMIT,
        metavar="SIZE",
        help="working-memory limit per plane, e.g. 2GiB (default: 1GiB)",
    )
    checks = run.add_argument_group(
        "intensity checks", "limits of the warnings printed after the run; the defaults suit most"
    )
    for name, (kind, metavar, text) in _INTENSITY_HELP.items():
        checks.add_argument(
            "--" + name.replace("_", "-"), dest=name, type=kind, metavar=metavar, help=text
        )


_INTENSITY_HELP = {
    "outlier_limit": (
        float,
        "N",
        "robust deviations beyond which an image or biological repeat is reported (default 3)",
    ),
    "saturation_limit": (
        float,
        "FRACTION",
        "fraction of saturated pixels above which an image is reported (default 0.01, 1 %%)",
    ),
    "min_images": (int, "N", "fewest images a comparison needs (default 5)"),
}


def run_command(args: argparse.Namespace) -> int:
    """Run pass 2 with the command's options, print what it did and return the status."""
    pre = command_preflight(args)
    output = Path(args.output) if args.output else default_output_folder(pre.discovery.root)
    recorded = usable(read_previous(output.resolve())[0])
    mapping = _mapping_option(args, pre, recorded)
    settings = _settings_options(args, recorded)
    if mapping is False or settings is None:
        return 1
    design = None
    # Folder problems are reported by the run itself; the design is only asked about
    # once the folders can be processed.
    if not has_errors([*pre.discovery.issues, *pre.exclusion_issues]):
        design, message = _design_for_run(pre, args)
        if design is None:
            print(message)
            return 1
    image_type = _print_inputs(args, pre, mapping, design, recorded)
    result = run_experiment(
        args.experiment,
        mapping,
        output_folder=args.output,
        bin_count=args.bins,
        memory_limit=args.memory_limit,
        missing_as_singleton=args.missing_as_singleton,
        exclude=args.exclude or None,
        mode=args.mode,
        progress=_progress,
        design=design,
        masks_folder=False if args.no_masks else args.masks,
        mask_axes=args.mask_axes or None,
        image_type=image_type,
        intensity_settings=settings[0],
        histogram_settings=settings[1],
    )
    if result.issues:
        print(format_issues(result.issues))
        print()
    print(run_summary(result))
    if result.intensity is not None:
        print()
        print(intensity_text(result.intensity))
    return 0 if result.written else 1


def _progress(done: int, total: int, path: str) -> None:
    """Print which file is read."""
    if path:
        print(f"[{done + 1}/{total}] {path}", flush=True)


def _mapping_option(
    args: argparse.Namespace, pre: Preflight, recorded: RunManifest | None
) -> AxisMapping | None | Literal[False]:
    """Return the mapping of ``--axes``, None for the recorded one, or False when refused."""
    axes = reference_axes(pre.metadata.files)
    if args.axes is None:
        if recorded is not None:
            return None
        suggested = pre.suggested_mapping.roles_string if pre.suggested_mapping else axes
        print(
            "Give the role of each axis with --axes (for example --axes "
            f"{suggested}); 'check' prints the suggested value. Later runs in the same output "
            "folder reuse it."
        )
        return False
    if axes is None:
        print(format_issues(pre.issues) or "No readable images were found.")
        return False
    try:
        return AxisMapping(axes, tuple(args.axes))
    except ValueError:
        print(
            f"--axes {args.axes} does not fit the images, whose axes are {axes}: give one role "
            f"(X, Y, Z, C, T or -) for each of the {len(axes)} axes, for example "
            f"--axes {pre.suggested_mapping.roles_string if pre.suggested_mapping else axes}."
        )
        return False


def _settings_options(
    args: argparse.Namespace, recorded: RunManifest | None
) -> tuple[IntensityCheckSettings | None, HistogramSettings | None] | None:
    """Return the intensity and histogram settings given, over the recorded ones.

    Each is None when none of its options is given (the run then uses the recorded one);
    None as a whole when a value is refused, which is printed.
    """
    intensity = {k: getattr(args, k) for k in _INTENSITY_HELP if getattr(args, k) is not None}
    histogram = {
        k: v
        for k, v in (
            ("histogram_range", args.histogram_range),
            ("robust_quantiles", args.robust_quantiles and tuple(args.robust_quantiles)),
        )
        if v is not None
    }
    base_intensity = (recorded and recorded.intensity_checks) or IntensityCheckSettings()
    base_histogram = recorded.settings.histogram if recorded else HistogramSettings()
    try:
        return (
            replace(base_intensity, **intensity) if intensity else None,
            replace(base_histogram, **histogram) if histogram else None,
        )
    except ValueError as exc:
        print(exc)
        return None


def _print_inputs(
    args: argparse.Namespace,
    pre: Preflight,
    mapping: AxisMapping | None,
    design: ExperimentDesign | None,
    recorded: RunManifest | None,
) -> str:
    """Print the axis roles, image type and design the run uses; return the image type."""
    missing = args.missing_as_singleton
    if missing is None:
        missing = recorded.settings.missing_as_singleton if recorded else False
    review = review_axes(pre.metadata, mapping or recorded.mapping, missing_as_singleton=missing)
    if mapping is None:
        print(f"Axis roles: as confirmed for the earlier run ({recorded.mapping.roles_string})")
    else:
        print("Axis roles:")
        print(mapping_summary(mapping, pre.metadata.files))
        print()
        print(axis_check_summary(review))
    print()
    type_review = review_image_type(pre.metadata, review)
    given = args.image_type or (recorded.image_type if recorded else None)
    image_type = given or type_review.suggested
    print(f"Image type: {image_type}")
    print(_image_type_note(type_review, given))
    print()
    if design is not None:
        print("Experiment design:")
        print(design_summary(design))
        print()
    return image_type


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
        return "  given with --image-type, or as in the earlier run"
    other = "fluorescence" if review.suggested == RGB else RGB
    lines = [f"  suggested, because {review.reason}"]
    if other == RGB and not review.rgb_allowed:
        lines.append(f"  rgb is not possible: {review.rgb_problem}")
    else:
        lines.append(f"  Give --image-type {other} if these are {other} images.")
    return "\n".join(lines)
