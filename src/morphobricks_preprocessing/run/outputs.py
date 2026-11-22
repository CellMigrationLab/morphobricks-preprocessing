"""What a run writes into its publication folder: the tables and the manifest (contract §9).

:func:`write_generation` writes the histogram table, the image QC table, the experiment design
and the manifest, and returns the manifest's SHA-256; :func:`replaced_files` names the earlier
outputs the run replaces or removes. Moved from :mod:`.pipeline`.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from morphobricks_normalization.csv_io import OUTPUT_FILENAME, write_plane_histograms

from ..channel_identity import ChannelIdentityReport
from ..design import DESIGN_FILENAME, write_experiment_design
from ..discovery_report import REPORT_FILENAME, REPORT_TEXT_FILENAME
from ..image_qc_record import IMAGE_QC_FILENAME, image_qc_entry
from ..image_qc_table import write_image_qc
from ..intensity import IntensityReport
from ..manifest import (
    MANIFEST_FILENAME,
    RunManifest,
    RunSettings,
    file_sha256,
    package_versions,
    write_manifest,
)
from ..manifest_versions import MANIFEST_VERSION
from ..mask_scan import MASK_REPORT_FILENAME
from .description_run import manifest_entry
from .modes import FULL
from .preflight import Preflight
from .recorded import usable
from .state import RunState
from .steps import Rows


def write_generation(
    run: RunState,
    rows: Rows,
    settings: RunSettings,
    records: list,
    checks: tuple[dict | None, IntensityReport, ChannelIdentityReport],
    staged: Path,
) -> str:
    """Write the table, design and manifest into the publication folder.

    ``checks`` are the masks entry, the intensity and the channel checks. Returns the manifest's
    SHA-256 (the report, which names it, is written once the images are checked again).
    """
    pre, design, ids = run.pre, run.checked.design, run.checked.channel_ids
    masks, intensity, identity = checks
    described = run.description
    per_file = Counter(record.source_relative_path for record in records)
    csv_path = write_plane_histograms(records, staged / OUTPUT_FILENAME, channel_ids=ids)
    qc_sha256 = file_sha256(write_image_qc(rows.qc, staged / IMAGE_QC_FILENAME))
    design_sha256 = None
    if design is not None:
        design_sha256 = file_sha256(write_experiment_design(design, staged / DESIGN_FILENAME))
    manifest = RunManifest(
        experiment_root=pre.discovery.root.as_posix(),
        dtype=pre.metadata.dtype.name,
        settings=settings,
        mapping=run.checked.review.mapping,
        files=tuple(
            replace(entry, plane_count=per_file[rel]) for rel, entry in sorted(rows.entries.items())
        ),
        csv_sha256=file_sha256(csv_path),
        versions=package_versions(),
        written_at=datetime.now(UTC).isoformat(timespec="seconds"),
        experiment_design_sha256=design_sha256,
        masks=masks,
        image_type=run.inputs.image_type,
        channels_from_rgb_samples=run.checked.type_review.channels_from_rgb_samples,
        intensity_checks=intensity.settings,
        channel_identity=identity.settings,
        narrow_ranges=tuple(rows.narrow),
        calibration_sha256=pre.calibration.sha256,
        conversion_record_sha256=None if pre.conversion is None else pre.conversion.sha256,
        projection=_projection(pre),
        experiment_description=manifest_entry(described, masks, run.output, len(rows.entries)),
        lab_links=None if run.lab_links is None else run.lab_links.to_dict(),
        image_qc=image_qc_entry(qc_sha256),
        manifest_version=MANIFEST_VERSION,
    )
    manifest = _kept_time(manifest, run.previous[0])
    return file_sha256(write_manifest(manifest, staged / MANIFEST_FILENAME))


def _projection(pre: Preflight) -> dict | None:
    """Return the manifest's ``projection`` entry for projected copies, else None."""
    found = None if pre.conversion is None else pre.conversion.projection
    return None if found is None else found.to_dict()


def _kept_time(manifest: RunManifest, earlier: RunManifest | None) -> RunManifest:
    """Return the manifest with the earlier run's ``written_at`` when nothing else changed.

    Later bricks pin the manifest's SHA-256 (CellPatcher, MorphoFeature): a rerun that
    changes nothing must leave its bytes as they were (cross-brick test F10). The report
    still records the rerun.
    """
    if earlier is None:
        return manifest
    same = replace(manifest, written_at=earlier.written_at)
    return same if same.to_dict() == earlier.to_dict() else manifest


def replaced_files(run: RunState, masks: dict | None) -> dict[str, str]:
    """Return the earlier output files the run replaces or removes, and why.

    ``mask_relationships.json`` is removed when the masks changed since the earlier run
    (or nothing usable records them), and in ``full`` mode, which recomputes every check.
    """
    names = [
        OUTPUT_FILENAME,
        IMAGE_QC_FILENAME,
        MANIFEST_FILENAME,
        REPORT_FILENAME,
        REPORT_TEXT_FILENAME,
    ]
    if run.checked.design is not None:
        names.append(DESIGN_FILENAME)
    replaced = {name: "replaced" for name in names if name in run.found}
    if MASK_REPORT_FILENAME in run.found:
        before = usable(run.previous[0])
        if run.mode == FULL:
            replaced[MASK_REPORT_FILENAME] = "removed: a full run recomputes every check"
        elif before is None or before.masks != masks:
            replaced[MASK_REPORT_FILENAME] = "removed: the masks changed since it was written"
    return replaced
