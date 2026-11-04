"""The command line's ``run``: its options and what it prints (moved from :mod:`.cli`).

Every input left out is the earlier run's in the same output folder (design v0.4 §1):
``--axes`` is needed for the first run of plain stacks (not when every image names its
axes, design v0.4 §3) and for plain stacks that are new or changed;
``--no-masks`` drops the recorded masks folder, ``--no-exclude`` the recorded exclusions;
``--mode full`` recomputes every row; ``--verify-content`` reuses rows only for the same
content SHA-256 (:class:`.run.modes.RunMode`). Given only ``--output``, ``run`` uses the recorded
experiment folder (``experiment_not_recorded`` without one).
Also the helpers every command shares: :func:`parse_size` and :func:`command_preflight`.
"""

from __future__ import annotations

import argparse
import re
from dataclasses import replace
from datetime import date
from fractions import Fraction
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
from .cli_design import design_instructions
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
from .metadata import header_axes
from .planes import DEFAULT_MEMORY_LIMIT
from .report import (
    axis_check_summary,
    design_summary,
    format_issues,
    mapping_summary,
    run_summary,
)
from .run import MODES, RESTART, Preflight, RunMode, preflight, run_experiment
from .run.lock import OutputLockedError, lock_issue, output_lock
from .run.output_guard import output_masks_issues
from .run.recorded import usable
from .run.reuse import read_previous, recorded_experiment_root
from .validation import ValidationIssue, has_errors

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
    """Parse a memory size such as ``"1GiB"``, ``"1.5 GiB"`` or ``"1073741824"``.

    The size must be an exact whole number of bytes above 0, computed without rounding
    (``"1.5GiB"`` is 1610612736 bytes; ``"1.5"`` and ``"0.5B"`` are refused, issue #80).
    ``argparse`` calls it while reading the command line, so a refused size stops the
    command before anything is read.

    Raises
    ------
    argparse.ArgumentTypeError
        If the text is not a whole positive number of bytes.
    """
    match = re.fullmatch(r"\s*([0-9]+(?:\.[0-9]+)?)\s*([A-Za-z]*)\s*", text, flags=re.ASCII)
    unit = match.group(2).upper() if match else None
    if not match or unit not in _UNITS:
        raise argparse.ArgumentTypeError(
            f"{text!r} is not a memory size; use for example 1GiB, 512MiB or a number of bytes."
        )
    size = Fraction(match.group(1)) * _UNITS[unit]
    if size.denominator != 1 or size <= 0:
        raise argparse.ArgumentTypeError(
            f"{text!r} is not a whole number of bytes above 0; use for example 1GiB, 512MiB "
            "or a whole number of bytes."
        )
    return int(size)


def command_preflight(args: argparse.Namespace) -> Preflight:
    """Run :func:`preflight` with the options every command shares."""
    return preflight(
        args.experiment,
        args.output,
        exclude=args.exclude,
        masks_folder=args.masks,
        mask_axes=args.mask_axes,
    )


def use_recorded_experiment(args: argparse.Namespace) -> bool:
    """Take the experiment folder the run in ``--output`` recorded, when none is given.

    Parameters
    ----------
    args
        The command's arguments; ``args.experiment`` is set to the recorded folder.

    Returns
    -------
    bool
        False, after printing ``experiment_not_recorded``, when none is given or recorded.
    """
    if args.experiment is not None:
        return True
    root, missing = recorded_experiment_root(args.output and Path(args.output).resolve())
    if missing is not None:
        print(format_issues([missing]))
        return False
    args.experiment = str(root)
    return True


def add_missing_as_singleton(parser: argparse.ArgumentParser) -> None:
    """Add ``--missing-as-singleton``, which ``check`` and ``run`` take alike (F10).

    Parameters
    ----------
    parser
        The ``check`` or ``run`` command's parser; :func:`missing_as_singleton` reads it.
    """
    parser.add_argument(
        "--missing-as-singleton",
        action=argparse.BooleanOptionalAction,
        help="confirm that images lacking some axes have one plane along them (default: as "
        "in the earlier run, else no)",
    )


def missing_as_singleton(args: argparse.Namespace, recorded: RunManifest | None) -> bool:
    """Return the answer of ``--missing-as-singleton``: given, else the earlier run's, else no.

    Parameters
    ----------
    args
        The ``check`` or ``run`` command's arguments.
    recorded
        The earlier run in the output folder (:func:`recorded_run`), or None.

    Returns
    -------
    bool
        Whether images lacking some axes are read with one plane along them.
    """
    if args.missing_as_singleton is not None:
        return args.missing_as_singleton
    return recorded.settings.missing_as_singleton if recorded is not None else False


def reused_missing_as_singleton(args: argparse.Namespace, recorded: RunManifest | None) -> str:
    """Return the line saying that the earlier run's ``--missing-as-singleton`` is reused.

    Parameters
    ----------
    args
        The ``check`` or ``run`` command's arguments.
    recorded
        The earlier run in the output folder (:func:`recorded_run`), or None.

    Returns
    -------
    str
        Empty unless the option is not given and the earlier run recorded yes: then
        ``check`` and ``run`` print it, so an answer read from the folder is never silent.
    """
    if args.missing_as_singleton is not None or recorded is None:
        return ""
    if not recorded.settings.missing_as_singleton:
        return ""
    return (
        "Images lacking some axes: read with one plane along them, as confirmed for the "
        "earlier run (give --no-missing-as-singleton to ask again)"
    )


def recorded_run(args: argparse.Namespace) -> RunManifest | None:
    """Return the earlier run in the command's output folder, or None when none is usable.

    Parameters
    ----------
    args
        The command's arguments: ``output``, else the default next to ``experiment``.

    Returns
    -------
    RunManifest or None
    """
    root = Path(args.experiment).resolve()
    output = Path(args.output) if args.output else default_output_folder(root)
    return usable(read_previous(output.resolve())[0])


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
        "first run unless every image names its axes, and again for plain stacks that are "
        "new or changed",
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
        "--no-exclude",
        action="store_true",
        help="clear the exclusions of the earlier run: analyse every image again "
        "(not with --exclude)",
    )
    add_missing_as_singleton(run)
    run.add_argument(
        "--mode",
        choices=MODES,
        default=RESTART,
        help="restart (default): reuse the rows of unchanged images; full: recompute every "
        "row and check. Both keep your confirmations and replace the results only on success",
    )
    run.add_argument(
        "--verify-content",
        action=argparse.BooleanOptionalAction,
        help="reuse rows only when each image's content SHA-256 is the one recorded with them, "
        "not only its size and modification time (reads every image once more; changing it "
        "recomputes every row; default: as in the earlier run, else no)",
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
    if not use_recorded_experiment(args):
        return 1
    recorded = recorded_run(args)
    if recorded is not None and not args.exclude and not args.no_exclude:
        args.exclude = list(recorded.settings.exclude)  # the preflight leaves them out too
    pre = command_preflight(args)
    mapping = mapping_option(args, pre, recorded)
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
        exclude=[] if args.no_exclude else args.exclude or None,
        mode=RunMode(args.mode, args.verify_content),
        progress=print_progress,
        design=design,
        masks_folder=False if args.no_masks else args.masks,
        mask_axes=args.mask_axes or None,
        image_type=image_type,
        intensity_settings=settings[0],
        histogram_settings=settings[1],
    )
    template, instructions = None, ""
    if _stopped_on_design(result.issues, design):
        template, instructions = _design_template(design, pre, args)
    if result.issues:
        print(format_issues(result.issues))
        print()
    print(run_summary(result, design_template=template is not None))
    if result.intensity is not None:
        print()
        print(intensity_text(result.intensity))
    if instructions:
        print()
        print(instructions)
    return 0 if result.written else 1


def print_progress(done: int, total: int, path: str) -> None:
    """Print which file is read or written, as ``[2/10] path``."""
    if path:
        print(f"[{done + 1}/{total}] {path}", flush=True)


def mapping_option(
    args: argparse.Namespace, pre: Preflight, recorded: RunManifest | None
) -> AxisMapping | None | Literal[False]:
    """Return the mapping of ``--axes``, None for the recorded one, or False when refused."""
    axes = reference_axes(pre.metadata.files)
    if args.axes is None:
        if recorded is not None:
            return None
        named = _named_mapping(pre)
        if named is not None:  # design v0.4 §3: --axes is only needed for plain stacks
            return named
        if axes is None:  # no arrangement to give roles to: say why, not "--axes None"
            print(format_issues(pre.issues) or "No readable images were found.")
            return False
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


def _named_mapping(pre: Preflight) -> AxisMapping | None:
    """Return the roles every image's own metadata names, or None when any image needs them.

    A plain stack names no axes, so its roles must be stated (``--axes``); when every
    readable image names all its axes (:func:`.metadata.header_axes`) and the suggested
    mapping gives each axis a role, the roles are the headers' and nothing is guessed.
    """
    suggested = pre.suggested_mapping
    readable = [f for f in pre.metadata.files if f.readable]
    if suggested is None or None in suggested.roles or not readable:
        return None
    return None if any(header_axes(f) is None for f in readable) else suggested


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
    missing = missing_as_singleton(args, recorded)
    review = review_axes(pre.metadata, mapping or recorded.mapping, missing_as_singleton=missing)
    if mapping is None:
        print(f"Axis roles: as confirmed for the earlier run ({recorded.mapping.roles_string})")
    else:
        named = args.axes is None  # mapping_option: every image names its axes
        print("Axis roles (as every image names them):" if named else "Axis roles:")
        print(mapping_summary(mapping, pre.metadata.files))
        print()
        print(axis_check_summary(review))
    reused = reused_missing_as_singleton(args, recorded)
    if reused:
        print(reused)
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
    """Return the experiment design brought up to date, or None and what went wrong.

    A design that is not fully confirmed is returned too: the run stops on it and writes
    its report, then :func:`_design_template` writes it for the user to fill in.
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
    return design.dated(date.today().isoformat()), ""


def _stopped_on_design(
    issues: tuple[ValidationIssue, ...], design: ExperimentDesign | None
) -> bool:
    """Return True when the run stopped on the design given (not, e.g., on the lock)."""
    if design is None:
        return False
    found = {issue.code for issue in issues}
    return any(issue.code in found for issue in design_issues(design))


def _design_template(
    design: ExperimentDesign, pre: Preflight, args: argparse.Namespace
) -> tuple[Path | None, str]:
    """Write the design for the user to fill in, holding the output folder's lock.

    The run, given the design, stopped on it and wrote its report; the template keeps
    every answer already given. Returns the file written (None when nothing was) and what
    the user has to do.
    """
    root = pre.discovery.root
    output = Path(args.output) if args.output else default_output_folder(root)
    masks = pre.masks.folder if pre.masks is not None else None
    if check_output_folder(root, output) or output_masks_issues(output, masks):
        return None, ""  # the run reported it; nothing is written inside these folders
    try:
        with output_lock(output):
            target = write_experiment_design(design, output / DESIGN_FILENAME)
    except OutputLockedError as exc:
        return None, format_issues([lock_issue(exc)])
    lines = [*design_instructions(target, output), "Then run again."]
    return target, "\n".join(lines)


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
