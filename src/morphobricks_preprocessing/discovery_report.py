"""The discovery report: what the one discovery-and-validation step found (contract §18).

Every run (and ``check``) is one step: discover every image and mask, validate them, then
compute the histograms (owner decision of 1 November 2026). The report says what was
discovered and every check made, shown in the notebook and the CLI and written to
``<output>/discovery_report.json``:

- conditions, biological and technical repeats and positions, with image counts;
- channels and axes (the experiment mapping, each image's ``axes_from``);
- mask types, with paired, unpaired and ambiguous masks, and images without masks;
- every warning and error, under the check family that made it (:data:`CHECK_FAMILIES`),
  with the scope of each family in this run: which checks ran on every image, and which
  results were reused for unchanged images in ``restart`` mode.

No check is added, dropped or weakened here: the report only lists the issues the checks
gave, with their own severities.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from .check_families import CHECK_FAMILIES, family_of, family_scope
from .validation import ValidationIssue, plural

DISCOVERY_REPORT_FILENAME = "discovery_report.json"
DISCOVERY_REPORT_TEXT = "discovery_report.txt"
"""The readable text version, written next to the JSON report."""
DISCOVERY_REPORT_VERSION = "1"

FULL = "full"
"""Mode: erase the brick's earlier results in the output folder and process everything."""

RESTART = "restart"
"""Mode (default): reuse the results of unchanged images; process new or changed ones."""

MODES = (RESTART, FULL)


@dataclass(frozen=True)
class DiscoveryReport:
    """What one discovery-and-validation step found and checked.

    Attributes
    ----------
    experiment_root, output_folder, masks_folder
        The folders, as absolute paths (``masks_folder`` None without masks).
    mode
        ``"restart"`` or ``"full"`` (``"check"`` for the CLI ``check``, which runs no
        histograms).
    groups
        One entry per leaf folder: its labels and number of images (excluded ones left out).
    images
        Number of analysed images; ``excluded`` lists the excluded ones.
    channels, channel_names, axes
        The channel count of the images and their names (None for unnamed), and the
        experiment mapping (``{"axes", "roles"}``, None when not confirmed);
        ``axes_from`` counts the images whose roles come from their header and those
        confirmed by the user, and ``axes_by_image`` gives each image's axes, roles and
        how they were established.
    masks
        Per mask type: ``paired`` (number of masks paired with an image) and
        ``images_without`` (images that have no mask of the type); ``unpaired`` and
        ``ambiguous`` list the masks that pair with no image or with several.
    reused, recomputed, erased
        Images whose rows were kept, images read in this run, and the brick's earlier
        output files replaced (``full`` mode).
    issues
        Every warning and error, errors first.
    status
        ``"stopped"`` (errors: nothing but the report written), ``"written"`` (the table
        and manifest published with it) or ``"checked"`` (``check``: the report only).
    manifest_sha256
        The SHA-256 of the manifest the report describes, for a written run.
    """

    experiment_root: str
    output_folder: str
    masks_folder: str | None = None
    mode: str = RESTART
    groups: tuple[dict, ...] = ()
    images: int = 0
    excluded: tuple[str, ...] = ()
    channels: int | None = None
    channel_names: tuple[str | None, ...] = ()
    axes: dict | None = None
    axes_from: Mapping[str, int] = field(default_factory=dict)
    axes_by_image: Mapping[str, dict] = field(default_factory=dict)
    masks: Mapping[str, dict] = field(default_factory=dict)
    unpaired: tuple[str, ...] = ()
    ambiguous: tuple[str, ...] = ()
    reused: tuple[str, ...] = ()
    recomputed: tuple[str, ...] = ()
    erased: tuple[str, ...] = ()
    versions: Mapping[str, str] = field(default_factory=dict)
    issues: tuple[ValidationIssue, ...] = ()
    status: str = "stopped"
    manifest_sha256: str | None = None

    @property
    def checks(self) -> list[dict]:
        """Every check family with its scope in this run and the issues it gave."""
        families = []
        by_family: dict[str, list[ValidationIssue]] = {}
        for issue in self.issues:
            by_family.setdefault(family_of(issue.code), []).append(issue)
        for name in (*CHECK_FAMILIES, "other"):
            found = by_family.get(name, [])
            if name == "other" and not found:
                continue
            families.append(
                {
                    "family": name,
                    "scope": self._scope(name),
                    "errors": sum(i.is_error for i in found),
                    "warnings": sum(not i.is_error for i in found),
                    "codes": sorted({i.code for i in found}),
                }
            )
        return families

    def _scope(self, family: str) -> str:
        """Say what a check family covered in this run.

        Parameters
        ----------
        family
            The family's name.

        Returns
        -------
        str
            Its scope in plain words.
        """
        return family_scope(family, self.mode, len(self.recomputed), len(self.reused))

    def to_dict(self) -> dict:
        """Return the report as JSON-compatible data."""
        return {
            "report_version": DISCOVERY_REPORT_VERSION,
            "status": self.status,
            "manifest_sha256": self.manifest_sha256,
            "experiment_root": self.experiment_root,
            "output_folder": self.output_folder,
            "masks_folder": self.masks_folder,
            "mode": self.mode,
            "images": self.images,
            "excluded": list(self.excluded),
            "groups": [dict(g) for g in self.groups],
            "channels": self.channels,
            "channel_names": list(self.channel_names),
            "axis_mapping": self.axes,
            "axes_from": dict(self.axes_from),
            "axes_by_image": {k: dict(v) for k, v in self.axes_by_image.items()},
            "masks": {k: dict(v) for k, v in self.masks.items()},
            "unpaired_masks": list(self.unpaired),
            "ambiguous_masks": list(self.ambiguous),
            "reused": list(self.reused),
            "recomputed": list(self.recomputed),
            "erased": list(self.erased),
            "checks": self.checks,
            "issues": [
                {
                    "code": i.code,
                    "severity": i.severity.value,
                    "family": family_of(i.code),
                    "message": i.message,
                    "paths": list(i.paths),
                    "action": i.action,
                }
                for i in self.issues
            ],
            "versions": dict(self.versions),
        }

    def text(self) -> str:
        """Describe the report in plain text (ASCII-safe for consoles)."""
        lines = [f"Discovery report ({self.mode}, {self.status})"]
        conditions = Counter(g["condition"] for g in self.groups)
        repeats = {(g["condition"], g["biological_repeat"]) for g in self.groups}
        lines.append(
            f"  {plural(self.images, 'image')} in {plural(len(conditions), 'condition')}, "
            f"{plural(len(repeats), 'biological repeat')}, "
            f"{plural(len(self.groups), 'technical-repeat folder')}"
            + (f"; {len(self.excluded)} excluded" if self.excluded else "")
        )
        for g in self.groups:
            lines.append(
                f"    {g['condition']} / {g['biological_repeat']} / {g['technical_repeat']}: "
                f"{plural(g['images'], 'image')}"
            )
        if self.axes is not None:
            roles = "".join(r or "-" for r in self.axes["roles"])
            names = ", ".join(n or "unnamed" for n in self.channel_names)
            lines.append(
                f"  Axes {self.axes['axes']} read as {roles}; channels: {self.channels}"
                + (f" ({names})" if self.channel_names else "")
            )
            if self.axes_from.get("confirmed"):
                lines.append(
                    f"    {plural(self.axes_from['confirmed'], 'image')} with roles confirmed "
                    "by you (their metadata does not name the axes)"
                )
        elif self.channels is not None:
            lines.append(f"  Channels: {self.channels}")
        if self.masks_folder is not None or self.masks:
            lines.append(f"  Masks: {plural(len(self.masks), 'mask type')}")
            for name, entry in sorted(self.masks.items()):
                lines.append(
                    f"    {name}: {plural(entry['paired'], 'mask')} paired; "
                    f"{plural(len(entry['images_without']), 'image')} without one"
                )
            if self.unpaired:
                lines.append(f"    {plural(len(self.unpaired), 'mask')} paired with no image")
            if self.ambiguous:
                lines.append(f"    {plural(len(self.ambiguous), 'mask')} ambiguous")
        if self.mode != "check":
            lines.append(
                f"  Images reused: {len(self.reused)}; recomputed: {len(self.recomputed)}; "
                f"earlier files erased: {len(self.erased)}"
            )
            lines.extend(f"    recomputed: {path}" for path in self.recomputed[:20])
            if len(self.recomputed) > 20:
                lines.append(f"    ... and {len(self.recomputed) - 20} more")
            lines.extend(f"    erased: {path}" for path in self.erased)
        lines.append("  Checks:")
        for family in self.checks:
            found = (
                f"{plural(family['errors'], 'error')}, {plural(family['warnings'], 'warning')}"
                if family["codes"]
                else "no problems"
            )
            lines.append(f"    {family['family']}: {found} ({family['scope']})")
            if family["codes"]:
                lines.append(f"      {', '.join(family['codes'])}")
        return "\n".join(lines)


def build_discovery_report(
    pre,
    *,
    output_folder: str | os.PathLike,
    issues: Iterable[ValidationIssue],
    review=None,
    mode: str = RESTART,
    reused: Sequence[str] = (),
    recomputed: Sequence[str] = (),
    erased: Sequence[str] = (),
    versions: Mapping[str, str] | None = None,
) -> DiscoveryReport:
    """Build the report of one discovery-and-validation step from its parts.

    Parameters
    ----------
    pre
        The step's :class:`.run.Preflight` (folders, headers, masks).
    output_folder
        The output folder.
    issues
        Every issue of the step, as reported to the user.
    review
        The :class:`.axes.AxisReview` of the confirmed mapping, or None (``check``).
    mode
        ``"restart"``, ``"full"`` or ``"check"``.
    reused, recomputed, erased
        Images whose rows were kept, images read, and earlier output files erased.
    versions
        Package versions, as in the run manifest.
    """
    from .axes import channel_count

    excluded = set(pre.excluded)
    groups = []
    for leaf in pre.discovery.leaves:
        kept = [image for image in leaf.images if image not in excluded]
        condition, biological, technical = leaf.labels
        groups.append(
            {
                "folder": leaf.relative_path,
                "condition": condition,
                "biological_repeat": biological,
                "technical_repeat": technical,
                "images": len(kept),
                "positions": sorted({image.rsplit("/", 1)[-1] for image in kept}),
            }
        )
    readable = [f for f in pre.metadata.files if f.readable]
    channels = None
    if review is not None:
        counts = {
            channel_count(f, review.file_mappings[f.relative_path])
            for f in readable
            if f.relative_path in review.file_mappings
        }
        channels = counts.pop() if len(counts) == 1 else None
    names = next((f.channel_names for f in readable if f.channel_names), ())
    by_image = {}
    if review is not None:
        for f in readable:
            mapping = review.file_mappings.get(f.relative_path)
            if mapping is not None:
                by_image[f.relative_path] = {
                    "axes": f.axes,
                    "roles": mapping.roles_string,
                    "axes_from": review.axes_from.get(f.relative_path, ""),
                }
    masks: dict[str, dict] = {}
    unpaired: tuple[str, ...] = ()
    ambiguous: tuple[str, ...] = ()
    if pre.masks is not None:
        analysed = [f.relative_path for f in pre.metadata.files]
        for mask_type in pre.masks.types:
            have = {m.image for m in pre.masks.files if m.mask_type == mask_type}
            masks[mask_type] = {
                "paired": len(have),
                "images_without": sorted(set(analysed) - have),
            }
        for issue in pre.masks.issues:
            if issue.code == "mask_without_image":
                unpaired = issue.paths
            elif issue.code == "ambiguous_mask":
                ambiguous = issue.paths
    issues = tuple(sorted(issues, key=lambda issue: not issue.is_error))
    return DiscoveryReport(
        experiment_root=pre.discovery.root.as_posix(),
        output_folder=Path(output_folder).as_posix(),
        masks_folder=(
            None if pre.masks is None or pre.masks.folder is None else pre.masks.folder.as_posix()
        ),
        mode=mode,
        groups=tuple(groups),
        images=len(pre.metadata.files),
        excluded=tuple(pre.excluded),
        channels=channels,
        channel_names=tuple(names),
        axes_by_image=by_image,
        axes=(
            None
            if review is None
            else {"axes": review.mapping.axes, "roles": list(review.mapping.roles)}
        ),
        axes_from=dict(Counter(review.axes_from.values())) if review is not None else {},
        masks=masks,
        unpaired=tuple(unpaired),
        ambiguous=tuple(ambiguous),
        reused=tuple(reused),
        recomputed=tuple(recomputed),
        erased=tuple(erased),
        versions=dict(versions or {}),
        issues=issues,
    )


def write_discovery_report(report: DiscoveryReport, path: str | os.PathLike) -> Path:
    """Write the report as JSON, and its text version beside it (``discovery_report.txt``).

    Each file is replaced only once writing it succeeded.

    Parameters
    ----------
    report
        The report.
    path
        The JSON file.

    Returns
    -------
    Path
        The JSON file.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_text(path, json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n")
    _write_text(path.with_name(DISCOVERY_REPORT_TEXT), report.text() + "\n")
    return path


def _write_text(path: Path, text: str) -> None:
    """Write UTF-8 text with LF line endings, atomically (temporary file, then replace).

    Parameters
    ----------
    path
        The file.
    text
        Its content.
    """
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", newline="\n", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
