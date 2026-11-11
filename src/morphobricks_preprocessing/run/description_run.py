"""A run with a published ``morphobricks.toml`` (Stage 1 design section 3, "Run").

``run_experiment`` reads the published description under the output folder's lock, with the
strict reader, on the exact bytes it hashes. Every run needs one: without it the run is refused
(``A_DESCRIPTION_REQUIRED``, :mod:`..required_description`). The run:

- stops, with the Spec's own code and a plain message, when the file is not valid, its
  records file cannot be read, the units declare different image types
  (``R_HETEROGENEOUS_LAYOUT_NOT_ENABLED``; different channel lists are accepted from Stage 3,
  S3.3, and checked per unit) or the description contradicts the folder
  or the image headers (the physical ``R_*`` checks, :mod:`..physical_check`);
- records in its manifest the file's SHA-256, the section digests, which units' image type was
  checked against a header, the status of every declared mask file, and the publication
  records, copied unchanged (:mod:`..description_record`).

It reads no mask pixels: a mask file is ``checked`` only when the mask step recorded it
(:mod:`..mask_representation`), else ``not yet checked`` (experiment-v1 section 4.5a).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import astuple, dataclass, replace
from pathlib import Path

from ..channel_plan import declared_channel_ids, file_channel_ids
from ..description import Description, DescriptionError, description_from_bytes
from ..description_file import DESCRIPTION_FILENAME, read_file_bytes, sha256_hex
from ..description_layouts import declared_layouts, is_heterogeneous
from ..description_record import description_record, unit_records
from ..description_run_text import (
    coverage_issues,
    heterogeneous_issue,
    physical_issue,
    reader_issue,
)
from ..mask_representation import NOT_CHECKED, file_status, read_checked
from ..physical_check import HETEROGENEOUS_LAYOUT, PhysicalReport, check_physical
from ..required_description import description_required_issue
from ..unit_consistency import declared_type_issues
from ..validation import ValidationIssue, error
from .description_file import RecordsFileError, read_records
from .preflight import Preflight
from .steps import GLOBAL_CHANNEL_CODES, Checked


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
        ``(None, [])`` when there is no published file (the ``describe`` command drafts one
        then; every run refuses it, :func:`checked_description`); ``(None, errors)`` when it,
        or its records, cannot be used; else the description and no issue.
    """
    try:
        data = read_file_bytes(output / DESCRIPTION_FILENAME)
        if data is None:
            return None, []
        description = description_from_bytes(data)
        records = read_records(output)
    except DescriptionError as exc:
        return None, [reader_issue(item) for item in exc.issues]
    except RecordsFileError as exc:  # the Spec code and its E_RECORDS_* reasons, kept
        reasons = ", ".join(sorted(exc.reasons))
        message = f"The records file of the experiment description is refused ({reasons}): {exc}."
        return None, [error(exc.code, message, (), "Fix or remove the records file.")]
    except OSError as exc:
        message = f"The experiment description or its records file cannot be read: {exc}."
        return None, [error("description_unreadable", message, (), "Fix or remove the file.")]
    return RunDescription(description, sha256_hex(data), records), []


def check_description(
    found: RunDescription, pre: Preflight, roles: Mapping | None
) -> tuple[RunDescription, list[ValidationIssue]]:
    """Return the description with its physical report, and every refusal that applies.

    The layouts are checked first: with units of different image types the run stops with
    ``R_HETEROGENEOUS_LAYOUT_NOT_ENABLED`` and the physical checks do not run. Units with
    different channel lists of one image type are accepted (Stage 3, S3.3): each image is
    checked against its own unit (``R_CHANNEL_COUNT_MISMATCH``, ``R_UNIT_NOT_HOMOGENEOUS``,
    ``R_HEADER_CONTRADICTION``). The folder's
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


GLOBAL_UNIT_CODES = frozenset({"mixed_dtypes", "inconsistent_dimensions"})
"""The folder checks that compare pixel types and dimensions across the whole experiment.

A description declares each unit's ``image_type``; they are replaced by the same checks per
declared image type (:func:`..unit_consistency.declared_type_issues`; the axis review checks the
arrangement of dimensions). Units of different image types are then refused as a layout
(``R_HETEROGENEOUS_LAYOUT_NOT_ENABLED``, still refused), not by these."""


def without_global_channel_checks(issues: Iterable[ValidationIssue]) -> list[ValidationIssue]:
    """Return ``issues`` without the experiment-wide checks a description replaces.

    These are the channel checks (:data:`GLOBAL_CHANNEL_CODES`) and the pixel-type and
    dimension checks of every image of the experiment (``mixed_dtypes``,
    ``inconsistent_dimensions``), which :func:`check_description` makes per declared image
    type.
    """
    dropped = GLOBAL_CHANNEL_CODES | GLOBAL_UNIT_CODES
    return [issue for issue in issues if issue.code not in dropped]


def checked_description(
    output: Path,
    pre: Preflight,
    checked: Checked,
    loaded: tuple[RunDescription | None, list[ValidationIssue]],
) -> RunDescription | None:
    """Check the published description, add every refusal to ``checked.issues``, return it.

    ``loaded`` is :func:`load_description`'s answer, read under the output folder's lock.
    Returns None when there is none (``A_DESCRIPTION_REQUIRED``) or it cannot be used; the
    refusal is then in ``checked.issues``.
    """
    found, problems = loaded
    checked.issues.extend(problems)
    if found is None:
        if not problems:  # removed since the run started
            checked.issues.append(description_required_issue(output))
        return None
    checked.issues[:] = without_global_channel_checks(checked.issues)
    roles = None if checked.review is None else checked.review.file_mappings
    found, refused = check_description(found, pre, roles)
    checked.issues.extend(refused)
    checked.issues[:] = heterogeneous_first(checked.issues)
    return found


SECONDARY_CODES = frozenset({"inconsistent_mapping", "missing_axes", "ambiguous_missing_axes"})
"""The axis review's arrangement codes, whose advice is to export the images again."""

SECONDARY_ADVICE = (
    "Do not export the images again for this: it follows from the different layouts "
    f"({HETEROGENEOUS_LAYOUT}, listed first). This version runs one image type per experiment."
)


def heterogeneous_first(issues: Iterable[ValidationIssue]) -> list[ValidationIssue]:
    """Put the layout refusal first and make the axis review's arrangement issues secondary.

    With several layouts the arrangement of dimensions differs between units by design, so the
    axis review's arrangement issues (:data:`SECONDARY_CODES`) are a consequence, and
    exporting the images the same way would not help: their advice is replaced. Only the order
    and the advice change: no issue is added, removed or turned into a warning, so the run
    refuses exactly as before.
    """
    found = list(issues)
    layout = [i for i in found if i.code == HETEROGENEOUS_LAYOUT]
    if not layout:
        return found
    rest = [
        replace(i, action=SECONDARY_ADVICE) if i.code in SECONDARY_CODES else i
        for i in found
        if i.code != HETEROGENEOUS_LAYOUT
    ]
    return [*layout, *rest]


def described_checks(
    output: Path,
    pre: Preflight,
    checked: Checked,
    loaded: tuple[RunDescription | None, list[ValidationIssue]],
) -> tuple[Checked, RunDescription | None]:
    """Check the published description and give the run each image's channel IDs.

    :func:`checked_description`, then (with a description) :func:`with_channels` and, unless the
    image types are refused, the warning naming every partially represented repeat
    (``partial_channel_coverage``, :func:`..description_run_text.coverage_issues`).
    """
    described = checked_description(output, pre, checked, loaded)
    if described is None:
        return checked, None
    checked = with_channels(checked, described.description, pre)
    if HETEROGENEOUS_LAYOUT not in {issue.code for issue in checked.issues}:
        checked.issues.extend(coverage_issues(described.description))
    return checked, described


def with_channels(checked: Checked, description: Description, pre: Preflight) -> Checked:
    """Return ``checked`` with each image's channel IDs and the declared channel IDs.

    Each image's channels are its unit's confirmed list (Stage 3, S3.3); the issues list is
    the same object, so refusals added to it later are kept.
    """
    images = ((leaf.labels, image) for leaf in pre.discovery.leaves for image in leaf.images)
    return replace(
        checked,
        channels=file_channel_ids(description, images),
        channel_ids=declared_channel_ids(description),
    )


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
