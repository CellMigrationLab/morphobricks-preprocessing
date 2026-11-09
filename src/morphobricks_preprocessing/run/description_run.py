"""A run with a published ``morphobricks.toml`` (Stage 1 design section 3, "Run").

``run_experiment`` reads the published description under the output folder's lock, with the
strict reader, on the exact bytes it hashes. Without one the run is the v0.4 run, and says so
(:data:`NO_DESCRIPTION`). With one the run:

- stops, with the Spec's own code and a plain message, when the file is not valid, its
  records file cannot be read, the units declare more than one channel layout
  (``R_HETEROGENEOUS_LAYOUT_NOT_ENABLED``, Stage 1) or the description contradicts the folder
  or the image headers (the physical ``R_*`` checks, :mod:`..physical_check`);
- records in its manifest the file's SHA-256, the section digests, which units' image type was
  checked against a header, the status of every declared mask file, and the publication
  records, copied unchanged (:mod:`..description_record`).

It reads no mask pixels: a mask file is ``checked`` only when the mask step recorded it
(:mod:`..mask_representation`), else ``not yet checked`` (experiment-v1 section 4.5a).
"""

from __future__ import annotations

import warnings
from collections.abc import Iterable, Mapping
from dataclasses import astuple, dataclass, replace
from pathlib import Path

from ..description import Description, DescriptionError, description_from_bytes
from ..description_file import DESCRIPTION_FILENAME, read_file_bytes, sha256_hex
from ..description_layouts import declared_layouts, is_heterogeneous
from ..description_record import description_record, unit_records
from ..description_run_text import heterogeneous_issue, physical_issue, reader_issue
from ..mask_representation import NOT_CHECKED, file_status, read_checked
from ..physical_check import PhysicalReport, check_physical
from ..unit_consistency import declared_type_issues
from ..validation import ValidationIssue, error
from .description_file import RecordsFileError, read_records
from .preflight import Preflight
from .steps import Checked

NO_DESCRIPTION = (
    "No experiment description (morphobricks.toml) was found in the output folder: this run "
    "uses the folder names and the experiment design as before. Describe the experiment in "
    "the notebook to state canonical channels, repeats and masks."
)


@dataclass(frozen=True)
class RunDescription:
    """The published description a run works with, as read."""

    description: Description
    sha256: str
    records: tuple[dict, ...]
    physical: PhysicalReport | None = None
    """The physical checks' report; None when they could not run (no confirmed axis roles)."""


def load_description(output: Path) -> tuple[RunDescription | None, list[ValidationIssue]]:
    """Read the output folder's published description and records, or say why not.

    Returns
    -------
    tuple
        ``(None, [])`` when there is no published file; ``(None, errors)`` when it, or its
        records, cannot be used; else the description and no issue.
    """
    try:
        data = read_file_bytes(output / DESCRIPTION_FILENAME)
        if data is None:
            return None, []
        description = description_from_bytes(data)
        records = read_records(output)
    except DescriptionError as exc:
        return None, [reader_issue(item) for item in exc.issues]
    except (RecordsFileError, OSError) as exc:
        message = f"The experiment description or its records file cannot be read: {exc}."
        return None, [error("description_unreadable", message, (), "Fix or remove the file.")]
    return RunDescription(description, sha256_hex(data), records), []


def check_description(
    found: RunDescription, pre: Preflight, roles: Mapping | None
) -> tuple[RunDescription, list[ValidationIssue]]:
    """Return the description with its physical report, and every refusal that applies.

    The layouts are checked first: with more than one layout the run stops with
    ``R_HETEROGENEOUS_LAYOUT_NOT_ENABLED`` and the physical checks do not run. The folder's
    pixel-type and dimension checks apply per declared image type
    (:func:`..unit_consistency.declared_type_issues`), so units of different image types
    never produce ``mixed_dtypes`` or ``inconsistent_dimensions`` between them. The physical
    checks need the confirmed axis roles; without them they are not run (the run stops on
    the missing roles anyway) and the report is None, never a pass.

    Parameters
    ----------
    found
        The description read.
    pre
        The run's preflight.
    roles
        The confirmed roles of each axis by relative path (``AxisReview.file_mappings``), or
        None when no axis roles were confirmed.
    """
    layouts = declared_layouts(found.description)
    units = declared_type_issues(found.description, pre)
    if is_heterogeneous(layouts):
        return found, [heterogeneous_issue(layouts), *units]
    if roles is None:
        return found, units
    masks = pre.masks
    report = check_physical(
        found.description,
        pre.discovery,
        pre.metadata.files,
        excluded=pre.excluded,
        roles={path: mapping.roles for path, mapping in roles.items()},
        mask_types=None if masks is None else masks.types,
    )
    return replace(found, physical=report), [*units, *(physical_issue(i) for i in report.issues)]


GLOBAL_CHANNEL_CODES = frozenset({"channel_names_differ", "different_channel_counts"})
"""The v0.4 checks that compare channels across the whole experiment.

A description states each unit's channels and the header names confirmed for each, so for a
described run they are replaced by the per-unit checks (``R_CHANNEL_COUNT_MISMATCH``,
``R_UNIT_NOT_HOMOGENEOUS``, ``R_HEADER_CONTRADICTION``; experiment-v1 section 4.3 and 8),
and by ``R_HETEROGENEOUS_LAYOUT_NOT_ENABLED`` for units with different channel lists."""


GLOBAL_UNIT_CODES = frozenset({"mixed_dtypes", "inconsistent_dimensions"})
"""The v0.4 folder checks that compare pixel types and dimensions across the whole experiment.

A description declares each unit's ``image_type``; they are replaced by the same checks per
declared image type (:func:`..unit_consistency.declared_type_issues`; the axis review checks the
arrangement of dimensions). Units of different image types are then refused as a layout
(``R_HETEROGENEOUS_LAYOUT_NOT_ENABLED``), not by these."""


def without_global_channel_checks(issues: Iterable[ValidationIssue]) -> list[ValidationIssue]:
    """Return ``issues`` without the experiment-wide checks a description replaces.

    These are the channel checks (:data:`GLOBAL_CHANNEL_CODES`) and the pixel-type and
    dimension checks of every image of the experiment (``mixed_dtypes``,
    ``inconsistent_dimensions``), which :func:`check_description` makes per declared image
    type. Described runs only.
    """
    dropped = GLOBAL_CHANNEL_CODES | GLOBAL_UNIT_CODES
    return [issue for issue in issues if issue.code not in dropped]


def checked_description(output: Path, pre: Preflight, checked: Checked) -> RunDescription | None:
    """Read the published description, add every refusal to ``checked.issues``, return it.

    Returns None without one (a v0.4 run: a warning says so) and when it cannot be used.
    """
    found, problems = load_description(output)
    checked.issues.extend(problems)
    if found is None:
        if not problems:
            warnings.warn(NO_DESCRIPTION, UserWarning, stacklevel=4)
        return None
    checked.issues[:] = without_global_channel_checks(checked.issues)
    roles = None if checked.review is None else checked.review.file_mappings
    found, refused = check_description(found, pre, roles)
    checked.issues.extend(refused)
    return found


def mask_records(
    description: Description, masks: Mapping | None, output: Path, images: int
) -> dict[str, dict]:
    """Return each declared mask with its files, their status and how complete it is.

    Parameters
    ----------
    description
        The published description.
    masks
        The manifest's ``masks`` entry (:func:`..mask_record.mask_manifest`), or None.
    output
        The output folder, whose ``mask_representation.json`` says what was checked.
    images
        The number of images of the run.
    """
    table = read_checked(output)
    recorded = (masks or {}).get("files", ())
    out: dict[str, dict] = {}
    for mask_id, mask in sorted(description.masks.items()):
        files = []
        for entry in (e for e in recorded if e["type"] == mask.source_type):
            state = file_status(table, mask_id, mask.source_type, mask.representation, entry)
            files.append({**entry, "status": state})
        files.sort(key=lambda item: item["path"])
        done = bool(files) and all(f["status"] != NOT_CHECKED for f in files)
        out[mask_id] = {
            "source_type": mask.source_type,
            "representation": mask.representation,
            "status": "checked" if done else NOT_CHECKED,
            "images_with_masks": len({f["image"] for f in files}),
            "images": images,
            "files": files,
        }
    return out


def unit_entries(found: RunDescription) -> list[dict[str, object]]:
    """Return the manifest's unit records: image type verification and unnamed header images."""
    report = found.physical
    if report is None:
        return unit_records(found.description, {})
    verified = {astuple(k): v for k, v in report.image_type_verified.items()}
    unnamed = {astuple(k): v for k, v in report.unnamed.items()}
    return unit_records(found.description, verified, unnamed)


def manifest_entry(
    found: RunDescription, masks: Mapping | None, output: Path, images: int
) -> dict[str, object]:
    """Return the manifest's ``experiment_description`` entry of a run."""
    return description_record(
        found.description,
        found.sha256,
        unit_entries(found),
        mask_records(found.description, masks, output, images),
        found.records,
    )
