"""Plan the projected copies of an experiment (issue #176): every refusal, before any write.

:func:`plan_projection` takes the headers and the confirmed mappings that
:func:`.conversion_plan.plan_conversion` already settled and decides each image's projected
copy (:mod:`.projection_layout`, :mod:`.projection`). It reads no pixels. An image the
projection cannot be exact for, a slice range that does not fit, OME-Zarr images, a plane
that does not fit the memory limit and an experiment with no depth axis at all are refused
together, and then nothing is planned and nothing is written.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

from .axes import AxisMapping
from .axis_check import header_axis_issue
from .conversion_record import PROJECTED, CopyEntry
from .fingerprint import Fingerprint
from .metadata import TiffMetadata
from .ome_tiff_copy import copy_nbytes
from .planes import format_bytes
from .planned_copy import PlannedCopy
from .projection import Projection, ProjectionRefused, projection_dtype
from .projection_issues import (
    inexact_issue,
    nothing_to_project_issue,
    over_memory_issue,
    pixel_type_issue,
    z_range_issue,
    zarr_issue,
)
from .projection_layout import ProjectedLayout, projected_layout
from .projection_record import NAN_POLICY, ProjectionEntry
from .validation import ValidationIssue
from .zarr_input import is_zarr_store


def plan_projection(
    root: Path,
    headers: Mapping[str, TiffMetadata],
    mappings: Mapping[str, AxisMapping],
    sources: Mapping[str, Fingerprint],
    projection: Projection,
    *,
    roles: str,
    memory_limit: int,
) -> tuple[dict[str, PlannedCopy], list[ValidationIssue]]:
    """Plan the projected copy of every image in ``headers``, or say why none can be made.

    Parameters
    ----------
    root
        The experiment folder.
    headers
        The header of each image to project, by relative path (excluded images are not in it).
    mappings
        Each image's own confirmed mapping.
    sources
        Each image's fingerprint taken with its header.
    projection
        The method and slice range.
    roles
        The experiment's confirmed roles, for the message when no image has a Z axis.
    memory_limit
        The working-memory limit per projection, in bytes: one input plane plus the
        running plane (and the output plane when it is of another type).

    Returns
    -------
    tuple
        The planned copies by relative path (empty when there is an error), and the errors.
    """
    zarrs = sorted(rel for rel in headers if is_zarr_store(root / rel))
    if zarrs:
        return {}, [zarr_issue(zarrs)]
    layouts = {rel: projected_layout(meta, mappings[rel]) for rel, meta in headers.items()}
    if not any(found.z_axis is not None for found in layouts.values()):
        return {}, [nothing_to_project_issue(roles)]
    issues = _problems(headers, layouts, projection, memory_limit)
    if issues:
        return {}, issues
    return {
        rel: _planned(headers[rel], mappings[rel], sources[rel], layouts[rel], projection)
        for rel in sorted(headers)
    }, []


def _problems(
    headers: Mapping[str, TiffMetadata],
    layouts: Mapping[str, ProjectedLayout],
    projection: Projection,
    memory_limit: int,
) -> list[ValidationIssue]:
    """Return every refusal: a range that does not fit, a type or sum that is not exact, memory."""
    outside = {
        rel: found.z_size
        for rel, found in layouts.items()
        if projection.z_range is not None and projection.z_range[1] > found.z_size
    }
    if outside:
        return [z_range_issue(projection, outside)]
    refused: dict[tuple[str, str], tuple[ProjectionRefused, list[str]]] = {}
    heavy: list[tuple[str, int]] = []
    for rel in sorted(headers):
        meta, found = headers[rel], layouts[rel]
        start, stop = projection.resolve(found.z_size)
        try:
            rule = projection_dtype(meta.dtype, projection.method, stop - start)
        except ProjectionRefused as exc:
            refused.setdefault((exc.code, str(exc)), (exc, []))[1].append(rel)
            continue
        needed = rule.working_nbytes(found.layout.plane_shape)
        if needed > memory_limit:
            heavy.append((rel, needed))
    issues = [
        pixel_type_issue(paths, exc)
        if code == "cannot_convert_pixel_type"
        else inexact_issue(paths, exc)
        for (code, _), (exc, paths) in sorted(refused.items())
    ]
    if heavy:
        rel, needed = max(heavy, key=lambda item: item[1])
        issues.append(
            over_memory_issue(rel, format_bytes(needed), format_bytes(memory_limit), len(heavy))
        )
    return issues


def _planned(
    meta: TiffMetadata,
    mapping: AxisMapping,
    source: Fingerprint,
    found: ProjectedLayout,
    projection: Projection,
) -> PlannedCopy:
    """Plan the projected copy of one image; its types and sums are already checked."""
    start, stop = projection.resolve(found.z_size)
    rule = projection_dtype(meta.dtype, projection.method, stop - start)
    layout = found.layout
    bits = meta.significant_bits if rule.significant_bits_dropped is None else None
    dropped = None if meta.significant_bits is None else rule.significant_bits_dropped
    layout = replace(layout, significant_bits=bits)
    details = ProjectionEntry(
        source_relative_path=meta.relative_path,
        source_content_sha256=None,
        source_z_axis=found.z_axis,
        source_z_size=found.z_size,
        z_range=(start, stop),
        method=projection.method,
        source_dtype=meta.dtype.name,
        output_dtype=rule.output_dtype.name,
        dtype_rule=rule.dtype_rule,
        rounding=rule.rounding,
        nan_policy=NAN_POLICY,
        dropped_sizes={"Z": found.dropped_z},
        significant_bits_dropped=dropped,
    )
    entry = CopyEntry(
        relative_path=meta.relative_path,
        action=PROJECTED,
        reason=header_axis_issue(meta, mapping),
        source=source,
        source_axes=meta.axes,
        roles=mapping.roles,
        copy_axes=layout.axes,
        pixel_sizes=layout.sizes,
        unknown_sizes=layout.unknown,
        channel_names=layout.channel_names,
        significant_bits=bits,
        projection=details,
    )
    nbytes = copy_nbytes(layout, rule.output_dtype)
    return PlannedCopy(entry, layout, mapping, nbytes, meta.dtype)
