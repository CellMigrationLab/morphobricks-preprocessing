"""How many images a run reads at once: ``workers`` and ``max_ram`` (issue #107).

A run reads each image (one FOV: all its planes and channels) in one worker thread, and at
most ``RunMode(workers=...)`` images at once; everything else stays in the calling thread
(:func:`.resume.new_rows`). ``workers=1``, the default, reads one image after the other in
the calling thread, as before. ``workers="auto"`` takes, for the images that are read:

- the CPU cap: the number of images to read, at most the CPUs this process may use
  (:func:`available_cpus`);
- with ``max_ram``, the RAM cap: ``(max_ram - PARENT_RESERVE) // largest``, where ``largest``
  is the largest image's working-memory estimate (:func:`image_memory`, from the header's
  :func:`..planes.plane_layout`, never the size on disk);

and reads with the smaller. It is deliberately conservative: every image is counted as the
largest one. An image whose estimate alone is over what ``max_ram`` leaves is refused with
``image_over_max_ram`` before any image is read: the limit is never exceeded silently.

The number of workers is operational: it is in no row key, staging key, manifest or table,
so the same images give byte-identical outputs with any value.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ..axes import AxisMapping
from ..manifest import RunSettings
from ..planes import PlaneReadError, format_bytes, plane_layout
from ..validation import ValidationIssue, error, plural
from .modes import AUTO

logger = logging.getLogger(__name__)

PARENT_RESERVE = 256 * 2**20
"""Bytes of ``max_ram`` kept for the run itself (its rows, staging and checks), not workers."""
OVER_MAX_RAM = "image_over_max_ram"


@dataclass(frozen=True)
class Workers:
    """How many images a run may read at once (from its :class:`.modes.RunMode`).

    Attributes
    ----------
    count
        A whole number of workers, at least 1, or ``"auto"``.
    max_ram
        With ``"auto"``, the bytes of memory the run may use, or None for no RAM cap.
    """

    count: int | Literal["auto"] = 1
    max_ram: int | None = None


def available_cpus() -> int:
    """Return the number of CPUs this process may run on (at least 1)."""
    if hasattr(os, "process_cpu_count"):  # Python 3.13
        return os.process_cpu_count() or 1
    if hasattr(os, "sched_getaffinity"):
        return len(os.sched_getaffinity(0)) or 1
    return os.cpu_count() or 1


def image_memory(path: Path, mapping: AxisMapping, settings: RunSettings) -> int:
    """Return the working memory of reading one image, from its header (not its file size).

    The working memory of one plane as the run reads it (:func:`..planes.plane_layout` with
    the run's memory limit: the whole plane, or one block when its planes are read in
    blocks), plus the bin counts of every row of the image, kept until the run has them.

    Raises
    ------
    PlaneReadError
        If the header cannot be read plane by plane.
    """
    layout = plane_layout(
        path,
        mapping,
        bin_count=settings.bin_count,
        memory_limit=settings.memory_limit,
        histogram_settings=settings.histogram,
    )
    plane = layout.block_memory_estimate if layout.in_blocks else layout.memory_estimate
    return plane + layout.plane_count * 8 * settings.bin_count


@dataclass(frozen=True)
class WorkerPlan:
    """The workers a run reads with, or why it does not read.

    Attributes
    ----------
    count
        The number of worker threads (1: in the calling thread, one image after another).
    refused
        ``image_over_max_ram`` when an image cannot be read within ``max_ram``, else None.
    """

    count: int
    refused: ValidationIssue | None = None


def plan_workers(
    workers: Workers,
    root: Path,
    to_read: list[str],
    mappings: Mapping[str, AxisMapping],
    settings: RunSettings,
) -> WorkerPlan:
    """Return how many images to read at once, for the images ``to_read``.

    Parameters
    ----------
    workers
        The run's :class:`Workers`.
    root
        The experiment folder.
    to_read
        The images whose pixels the run reads (resumed ones left out).
    mappings
        Each image's axis mapping.
    settings
        The run's settings (memory limit, bins, histogram range).

    Returns
    -------
    WorkerPlan
        A whole number given is used as it is. ``"auto"``: the smaller of the CPU and RAM caps
        (module docstring). An image whose header cannot be read plane by plane is left out of
        the estimate: its read opens the same header first and fails before reading a pixel.
    """
    if workers.count != AUTO:
        return WorkerPlan(workers.count)
    count = max(1, min(len(to_read), available_cpus()))
    if workers.max_ram is None or not to_read:
        return WorkerPlan(count)
    estimates = {}
    for rel in to_read:
        try:
            estimates[rel] = image_memory(root / rel, mappings[rel], settings)
        except PlaneReadError:
            logger.debug("No memory estimate for %s", rel, exc_info=True)
    if not estimates:
        return WorkerPlan(count)
    largest = max(estimates.values())
    budget = workers.max_ram - PARENT_RESERVE
    if largest > budget:
        over = sorted(rel for rel, needed in estimates.items() if needed > budget)
        return WorkerPlan(0, over_max_ram(over, largest, workers.max_ram))
    return WorkerPlan(min(count, budget // largest))


def over_max_ram(paths: list[str], largest: int, max_ram: int) -> ValidationIssue:
    """Return the error naming the images that cannot be read within ``max_ram``."""
    return error(
        OVER_MAX_RAM,
        f"{plural(len(paths), 'image')} need more working memory to read than max_ram allows: "
        f"up to {format_bytes(largest)}, with {format_bytes(PARENT_RESERVE)} of the "
        f"{format_bytes(max_ram)} kept for the run itself (the estimate is from the image "
        "header: one plane, or one block when the memory limit has its planes read in blocks, "
        "and the image's rows). No image was read.",
        paths,
        "Raise max_ram if this computer has enough memory, or lower the memory limit so that "
        "the planes are read in smaller blocks.",
    )
