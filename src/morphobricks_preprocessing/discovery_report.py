"""The run report: what one run (or ``check``) found, checked and did (design v0.4 §4).

The report is written on every run, including runs that stop, to
``discovery_report.json`` and a readable text version, ``discovery_report.txt``, in the
output folder. It has ``status`` (``"stopped"`` or ``"written"``) and the SHA-256 of the
manifest it describes. It is for people: bricks never read it.

It contains the experiment root (and whether it changed), the conditions, repeats and
positions with image counts; the channels and their names; each image's axes with
``axes_from`` and pixel sizes with their source (``header``, ``stated`` in
``calibration.csv``, or ``pixels``), or ``"uncalibrated"``; the calibration file; for a
tree of named-axis copies, its conversion record and the folder the copies were made from;
the design and image type; the mask types with paired, unpaired and ambiguous masks and
the images without masks; every warning and error with its action; and what the mode reused,
recomputed and replaced, and why. Nothing here checks anything: the issues are those the
checks gave, with their own severities.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from .image_names import position_id
from .recorded_sizes import HEADER, PIXELS, STATED, RecordedSize
from .validation import ValidationIssue, plural

if TYPE_CHECKING:
    from .axes import AxisReview
    from .design import ExperimentDesign
    from .run.preflight import Preflight
    from .run.recorded import InputChange

REPORT_FILENAME = "discovery_report.json"
REPORT_TEXT_FILENAME = "discovery_report.txt"
REPORT_VERSION = "1"
STOPPED = "stopped"
WRITTEN = "written"

_SHOWN = 20
"""Files listed per heading in the text version; the JSON lists every one."""


@dataclass(frozen=True)
class RunOutcome:
    """What a run did with the earlier results, for the report.

    Attributes
    ----------
    step
        ``"run"`` or ``"check"`` (the same step, stopped after the report).
    mode
        ``"restart"`` or ``"full"``; None for ``check``.
    previous_root
        The experiment root the earlier run recorded, or None without an earlier run.
    reused
        Files whose rows were reused.
    recomputed
        ``{file: reason}`` for the files read in this run.
    removed
        Files of the earlier run no longer analysed; their rows are removed.
    replaced
        ``{output file: what happened}`` for the earlier output files replaced or removed.
    changes
        Inputs given that differ from the recorded ones (:class:`.run.recorded.InputChange`).
    """

    step: str = "run"
    mode: str | None = None
    previous_root: str | None = None
    reused: tuple[str, ...] = ()
    recomputed: Mapping[str, str] = field(default_factory=dict)
    removed: tuple[str, ...] = ()
    replaced: Mapping[str, str] = field(default_factory=dict)
    changes: tuple[InputChange, ...] = ()


@dataclass(frozen=True)
class DiscoveryReport:
    """The report of one run, as data (:meth:`to_dict`) and as text (:meth:`text`).

    Attributes
    ----------
    data
        Everything found, as JSON-compatible data, without the status.
    issues
        Every warning and error, errors first.
    outcome
        What the run did with the earlier results.
    status
        ``"stopped"`` (nothing published) or ``"written"``.
    provisional
        True for the report written after the header checks, before the pixels are
        read; the final report replaces it.
    manifest_sha256
        SHA-256 of the manifest published with the report (``"written"`` only).
    """

    data: Mapping[str, object]
    issues: tuple[ValidationIssue, ...] = ()
    outcome: RunOutcome = field(default_factory=RunOutcome)
    status: str = STOPPED
    provisional: bool = False
    manifest_sha256: str | None = None

    def to_dict(self) -> dict:
        """Return the report as JSON-compatible data."""
        outcome = self.outcome
        return {
            "report_version": REPORT_VERSION,
            "step": outcome.step,
            "status": self.status,
            "provisional": self.provisional,
            "manifest_sha256": self.manifest_sha256,
            **self.data,
            "mode": outcome.mode,
            "reused": list(outcome.reused),
            "recomputed": dict(outcome.recomputed),
            "removed": list(outcome.removed),
            "replaced": dict(outcome.replaced),
            "input_changes": [change.to_dict() for change in outcome.changes],
            "issues": [_issue_dict(issue) for issue in self.issues],
        }

    def text(self) -> str:
        """Describe the report in plain text."""
        data = self.data
        heading = f"MorphoBricks preprocessing report: {self.outcome.step}, {self.status}"
        lines = [heading + (" (provisional)" if self.provisional else "")]
        if self.manifest_sha256:
            lines.append(f"Run manifest SHA-256: {self.manifest_sha256}")
        lines.append(f"Experiment: {data['experiment_root']}")
        if data.get("experiment_root_changed"):
            lines.append(f"  changed from {self.outcome.previous_root}")
        if data.get("conversion"):
            lines.append(f"  named-axis copies of {data['conversion']['source_root']}")
        lines.extend(_folder_lines(data))
        lines.extend(_image_lines(data))
        lines.extend(_mask_lines(data))
        lines.extend(_outcome_lines(self.outcome))
        lines.extend(_issue_lines(self.issues))
        return "\n".join(lines) + "\n"


def build_report(
    pre: Preflight,
    output: Path,
    issues: Sequence[ValidationIssue],
    *,
    review: AxisReview | None = None,
    design: ExperimentDesign | None = None,
    image_type: str | None = None,
    outcome: RunOutcome | None = None,
) -> DiscoveryReport:
    """Build the report of one run from what it found and did.

    Parameters
    ----------
    pre
        The run's preflight (folders, headers, masks).
    output
        The output folder.
    issues
        Every issue of the run.
    review
        The axis review of the roles used, or None when there is none.
    design
        The experiment design, or None.
    image_type
        The image type, or None.
    outcome
        What the run did with the earlier results.

    Returns
    -------
    DiscoveryReport
        With ``status`` ``"stopped"``; the run sets it when it publishes.
    """
    outcome = outcome or RunOutcome()
    root = pre.discovery.root.as_posix()
    data = {
        "experiment_root": root,
        "experiment_root_changed": outcome.previous_root not in (None, root),
        "conversion": _conversion(pre),
        "output_folder": output.as_posix(),
        "masks_folder": _masks_folder(pre),
        "excluded": list(pre.excluded),
        "groups": _groups(pre),
        **_channels(pre, review),
        "images": _images(pre, review),
        "calibration": _calibration(pre),
        "design": _design(design),
        "image_type": image_type,
        "masks": _masks(pre),
    }
    ordered = tuple(sorted(issues, key=lambda issue: not issue.is_error))
    return DiscoveryReport(data, ordered, outcome)


def _conversion(pre: Preflight) -> dict | None:
    """Return the conversion record of a tree of named-axis copies, or None (design §5)."""
    if pre.conversion is None:
        return None
    return {
        "record": pre.conversion.record,
        "sha256": pre.conversion.sha256,
        "source_root": pre.conversion.source_root,
    }


def _issue_dict(issue: ValidationIssue) -> dict:
    """Return one issue as data, with its action."""
    return {
        "code": issue.code,
        "severity": issue.severity.value,
        "message": issue.message,
        "paths": list(issue.paths),
        "action": issue.action,
    }


def _masks_folder(pre: Preflight) -> str | None:
    """Return the masks folder, or None without one."""
    if pre.masks is None or pre.masks.folder is None:
        return None
    return pre.masks.folder.as_posix()


def _groups(pre: Preflight) -> list[dict]:
    """Return one entry per leaf folder: its labels, position identifiers and image count."""
    excluded = set(pre.excluded)
    groups = []
    for leaf in pre.discovery.leaves:
        kept = [image for image in leaf.images if image not in excluded]
        groups.append(
            {
                "folder": leaf.relative_path,
                "condition": leaf.condition,
                "biological_repeat": leaf.biological_repeat,
                "technical_repeat": leaf.technical_repeat,
                "images": len(kept),
                "positions": [position_id(image) for image in kept],
            }
        )
    return groups


def _channels(pre: Preflight, review: AxisReview | None) -> dict:
    """Return the channel count of the images (None when they differ) and their names."""
    counts = set()
    if review is not None:
        for meta in pre.metadata.files:
            mapping = review.file_mappings.get(meta.relative_path)
            if mapping is not None and meta.readable:
                roles = list(mapping.roles)
                counts.add(meta.shape[roles.index("C")] if "C" in roles else 1)
    names = next((f.channel_names for f in pre.metadata.files if f.channel_names), ())
    return {
        "channels": counts.pop() if len(counts) == 1 else None,
        "channel_names": [name or "" for name in names],
    }


def _images(pre: Preflight, review: AxisReview | None) -> dict[str, dict]:
    """Return each analysed image's axes, roles, ``axes_from`` and pixel sizes."""
    images = {}
    for meta in pre.metadata.files:
        mapping = None if review is None else review.file_mappings.get(meta.relative_path)
        images[meta.relative_path] = {
            "axes": meta.axes,
            "roles": None if mapping is None else mapping.roles_string,
            "axes_from": None if review is None else review.axes_from.get(meta.relative_path),
            "pixel_sizes": _sizes(pre.calibration.sizes.get(meta.relative_path, ())),
        }
    return images


def _sizes(sizes: Sequence[RecordedSize]) -> dict | str:
    """Return the sizes given or stated, by role; ``"uncalibrated"`` without X and Y.

    A ``"pixels"`` statement is shown with its source and no value: deliberately
    uncalibrated, as the user stated.
    """
    found = {
        size.role: {"value": size.value, "unit": size.unit, "source": size.source}
        for size in sizes
        if size.source is not None
    }
    return found if "X" in found and "Y" in found else "uncalibrated"


def _calibration(pre: Preflight) -> dict:
    """Return the stated calibration: its file's SHA-256 and statements, or none."""
    calibration = pre.calibration.calibration
    return {
        "file": None if pre.calibration.sha256 is None else "calibration.csv",
        "sha256": pre.calibration.sha256,
        "statements": [
            {"target": e.target, "axis": e.axis, "size": e.size, "unit": e.unit}
            for e in (calibration.entries if calibration is not None else ())
        ],
    }


def _design(design: ExperimentDesign | None) -> list[dict] | None:
    """Return the design's rows, or None without a design."""
    if design is None:
        return None
    return [
        {
            "condition": row.condition,
            "biological_repeat": row.biological_repeat,
            "repeat_group": row.repeat_group,
            "confirmed": row.confirmed,
            "control": row.control,
        }
        for row in design.rows
    ]


def _masks(pre: Preflight) -> dict:
    """Return, per mask type, the paired masks and the images without one."""
    if pre.masks is None:
        return {"types": {}, "unpaired": [], "ambiguous": []}
    analysed = [meta.relative_path for meta in pre.metadata.files]
    types = {}
    for mask_type in pre.masks.types:
        have = {mask.image for mask in pre.masks.files if mask.mask_type == mask_type}
        types[mask_type] = {
            "paired": len(have),
            "images_without": [image for image in analysed if image not in have],
        }
    by_code = {issue.code: list(issue.paths) for issue in pre.masks.issues}
    return {
        "types": types,
        "unpaired": by_code.get("mask_without_image", []),
        "ambiguous": by_code.get("ambiguous_mask", []),
    }


def _folder_lines(data: Mapping) -> list[str]:
    """Describe the conditions, repeats and positions."""
    groups = data["groups"]
    conditions = {group["condition"] for group in groups}
    repeats = {(group["condition"], group["biological_repeat"]) for group in groups}
    images = sum(group["images"] for group in groups)
    lines = [
        f"{plural(images, 'image')} in {plural(len(conditions), 'condition')}, "
        f"{plural(len(repeats), 'biological repeat')}, {plural(len(groups), 'folder')}"
        + (f"; {len(data['excluded'])} excluded" if data["excluded"] else "")
    ]
    lines.extend(
        f"  {group['condition']} / {group['biological_repeat']} / "
        f"{group['technical_repeat']}: {plural(group['images'], 'image')}"
        for group in groups
    )
    if data["design"] is not None:
        confirmed = sum(row["confirmed"] for row in data["design"])
        lines.append(f"Design: {confirmed} of {len(data['design'])} repeat rows confirmed")
    if data["image_type"] is not None:
        lines.append(f"Image type: {data['image_type']}")
    return lines


def _image_lines(data: Mapping) -> list[str]:
    """Describe the channels, axes and pixel sizes."""
    names = ", ".join(name or "unnamed" for name in data["channel_names"])
    lines = [f"Channels: {data['channels'] or 'not one number'}" + (f" ({names})" if names else "")]
    images = data["images"].values()
    sources = Counter(image["axes_from"] or "not checked" for image in images)
    lines.append(
        "Axes: " + ", ".join(f"{count} from {how}" for how, count in sorted(sources.items()))
    )
    kinds = Counter(_calibration_kind(image["pixel_sizes"]) for image in images)
    lines.append(
        f"Pixel sizes: {kinds[HEADER]} from the header, {kinds[STATED]} stated, "
        f"{kinds[PIXELS]} measured in pixels as stated, {kinds[None]} uncalibrated"
    )
    lines.extend(_axis_size_line(images, axis) for axis in ("Z", "T") if _with_axis(images, axis))
    statements = data["calibration"]["statements"]
    if data["calibration"]["file"] is not None:
        lines.append(f"  calibration.csv: {plural(len(statements), 'statement')}")
    return lines


def _with_axis(images: Iterable[Mapping], axis: str) -> list[Mapping]:
    """Return the images read with ``axis`` among their roles."""
    return [image for image in images if axis in (image["roles"] or "")]


def _axis_size_line(images: Iterable[Mapping], axis: str) -> str:
    """Count how the Z or T size of the images with that axis is known; the others have none."""
    images = list(images)
    having = _with_axis(images, axis)
    sources = Counter(
        image["pixel_sizes"].get(axis, {}).get("source")
        if isinstance(image["pixel_sizes"], Mapping)
        else None
        for image in having
    )
    name = "Z step" if axis == "Z" else "Time step"
    return (
        f"  {name}: {sources[HEADER]} from the header, {sources[STATED]} stated, "
        f"{sources[PIXELS]} in pixels, {sources[None]} unknown; "
        f"{len(images) - len(having)} without {axis} (no size recorded)"
    )


def _calibration_kind(sizes: Mapping | str) -> str | None:
    """Return how an image's pixel size (X by Y) is known: header, stated, pixels or None."""
    if sizes == "uncalibrated":
        return None
    sources = {sizes["X"]["source"], sizes["Y"]["source"]}
    if PIXELS in sources:
        return PIXELS
    return STATED if STATED in sources else HEADER


def _mask_lines(data: Mapping) -> list[str]:
    """Describe the mask types and the masks that pair with no image or several."""
    masks = data["masks"]
    if not masks["types"] and data["masks_folder"] is None:
        return ["Masks: none"]
    lines = [f"Masks: {plural(len(masks['types']), 'mask type')}"]
    for name, entry in sorted(masks["types"].items()):
        lines.append(
            f"  {name}: {plural(entry['paired'], 'mask')} paired; "
            f"{plural(len(entry['images_without']), 'image')} without one"
        )
    if masks["unpaired"]:
        lines.append(f"  {plural(len(masks['unpaired']), 'mask')} paired with no image")
    if masks["ambiguous"]:
        lines.append(f"  {plural(len(masks['ambiguous']), 'mask')} ambiguous")
    return lines


def _outcome_lines(outcome: RunOutcome) -> list[str]:
    """Describe what the run reused, recomputed and replaced, and why."""
    if outcome.step != "run":
        return []
    lines = [
        f"Mode: {outcome.mode}. Reused: {plural(len(outcome.reused), 'image')}; "
        f"recomputed: {plural(len(outcome.recomputed), 'image')}; "
        f"removed: {plural(len(outcome.removed), 'image')}"
    ]
    reasons = Counter(outcome.recomputed.values())
    lines.extend(f"  {count} recomputed: {reason}" for reason, count in reasons.most_common())
    lines.extend(f"  {name}: {what}" for name, what in sorted(outcome.replaced.items()))
    for change in outcome.changes:
        lines.append(
            f"  Changed {change.name}: {change.recorded} -> {change.given} ({change.effect})"
        )
    return lines


def _issue_lines(issues: Sequence[ValidationIssue]) -> list[str]:
    """List every issue with its action."""
    if not issues:
        return ["Checks: no problems found"]
    errors = sum(issue.is_error for issue in issues)
    lines = [f"Checks: {plural(errors, 'error')}, {plural(len(issues) - errors, 'warning')}"]
    for issue in issues:
        lines.append(f"  [{issue.severity.value}] {issue.code}: {issue.message}")
        lines.extend(f"    {path}" for path in issue.paths[:_SHOWN])
        if len(issue.paths) > _SHOWN:
            lines.append(f"    ... and {len(issue.paths) - _SHOWN} more")
        if issue.action:
            lines.append(f"    Action: {issue.action}")
    return lines
