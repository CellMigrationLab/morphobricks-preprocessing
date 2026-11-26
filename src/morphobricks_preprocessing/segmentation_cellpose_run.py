"""The run, its channels and the closing words of the Cellpose notebook.

Split from :mod:`.segmentation_cellpose`, which re-exports them, to keep each file small.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path

from ._io import file_sha256
from .content_memo import ContentMemo
from .manifest import MANIFEST_FILENAME
from .open_checks import Checking, quiet
from .run_inputs import RunInputs, open_checked
from .segmentation_cellpose_model import CELLPOSE_VERSION
from .segmentation_record import SegmentationError, check_mask_type

MAX_CHANNELS = 3
"""Cellpose 4.2.1.1 reads at most three channels and drops the rest silently."""
NAMED_IMAGES = 5
"""How many images of a channel layout a refusal names before it counts the rest."""
NOT_THE_SNAPSHOT = (
    "run_manifest_sha256 {given} is not the SHA-256 of the run manifest the run was read from "
    "({pinned}): give the run's own (RunInputs.manifest_sha256), or none."
)
"""The refusal of a job given a run manifest SHA-256 other than its run's snapshot's (ar B2)."""
UNPINNED = (
    "The run was not opened with RunInputs.open, so the SHA-256 of the manifest it was read "
    "from is unknown: open the run with RunInputs.open. Nothing is segmented or recorded "
    "against a run manifest that is not known."
)
"""The refusal of a job whose run has no ``manifest_sha256`` (built by hand)."""
REPUBLISHED = (
    "The run was published again since it was opened (its run_manifest.json changed): press "
    "Open the run again. Nothing is segmented or recorded against a run other than the one "
    "opened."
)
"""The refusal of a preview, a batch or a record whose run manifest is no longer the opened one."""


def open_run(
    run_folder: str, *, checking: Checking = quiet, memo: ContentMemo | None = None
) -> RunInputs:
    """Open the run in ``run_folder`` (preprocessing's own checks; refusals are unchanged).

    ``checking`` is told each phase of the checks (:func:`.run_inputs.open_checked`); it
    changes nothing they check or decide. ``memo``: the content SHA-256s one notebook press
    already took (:mod:`.content_memo`).

    Raises
    ------
    SegmentationError
        For a blank folder.
    RunInputsError
        For a run that cannot be opened (:meth:`.run_inputs.RunInputs.open`).
    """
    if not run_folder.strip():
        raise SegmentationError("Give the run's output folder (the one holding run_manifest.json).")
    return open_checked(Path(run_folder.strip()), checking=checking, memo=memo)


def open_pinned(run_folder: str, *, checking: Checking = quiet) -> tuple[RunInputs, str | None]:
    """Open the run (:func:`open_run`, with ``checking``) and return it with its manifest SHA-256.

    The SHA-256 is the snapshot's (:attr:`.run_inputs.RunInputs.manifest_sha256`: of the bytes
    the manifest was parsed from, review ar B2); a run published again before this returns is
    refused (:data:`REPUBLISHED`). Every preview, batch and record then checks the manifest
    against that SHA-256 (:func:`check_manifest`), and the record names it; the file is never
    hashed again for the record (review aq, B2).

    Raises
    ------
    SegmentationError
        As :func:`open_run`, or :data:`REPUBLISHED` when a run was published meanwhile.
    RunInputsError
        For a run that cannot be opened.
    """
    inputs = open_run(run_folder, checking=checking)
    check_manifest(inputs, inputs.manifest_sha256)
    return inputs, inputs.manifest_sha256


def manifest_sha256(run: RunInputs | Path) -> str | None:
    """Return the SHA-256 of the run's manifest as it is on disk now, or None when unreadable."""
    folder = run.output_folder if isinstance(run, RunInputs) else run
    try:
        return file_sha256(folder / MANIFEST_FILENAME)
    except OSError:
        return None


def check_manifest(inputs: RunInputs, pinned: str | None) -> None:
    """Refuse when the run's manifest on disk is not the one ``pinned`` when it was opened.

    Raises
    ------
    SegmentationError
        :data:`REPUBLISHED`.
    """
    if pinned is None or manifest_sha256(inputs) != pinned:
        raise SegmentationError(REPUBLISHED)


def pinned_manifest(inputs: RunInputs, given: str | None) -> str:
    """Return the SHA-256 of the manifest ``inputs`` was read from, the one a job records.

    It is the snapshot's (:attr:`.run_inputs.RunInputs.manifest_sha256`), never the file's now
    (review ar, B2): a SHA-256 ``given`` must be that one.

    Raises
    ------
    SegmentationError
        :data:`UNPINNED` for inputs without one; :data:`NOT_THE_SNAPSHOT` for another given.
    """
    pinned = inputs.manifest_sha256
    if pinned is None:
        raise SegmentationError(UNPINNED)
    if given is not None and given != pinned:
        raise SegmentationError(NOT_THE_SNAPSHOT.format(given=given, pinned=pinned))
    return pinned


def channel_coverage(inputs: RunInputs) -> dict[str, tuple[int, int]]:
    """Return ``{channel ID: (images that have it, images)}``, in the order first met."""
    fovs = inputs.fovs()
    order = dict.fromkeys(c for f in fovs for c in (f.channel_ids or ()))
    return {c: (sum(c in (f.channel_ids or ()) for f in fovs), len(fovs)) for c in order}


def non_2d_images(inputs: RunInputs) -> tuple[str, ...]:
    """Return the images with more than one Z plane or time point (refused: 2D only for now)."""
    return tuple(f.relative_path for f in inputs.fovs() if f.z_count > 1 or f.t_count > 1)


def check_channel_count(channel_ids: Sequence[str]) -> None:
    """Refuse more channels than Cellpose reads (it would silently use the first three)."""
    if len(channel_ids) > MAX_CHANNELS:
        raise SegmentationError(
            f"Cellpose {CELLPOSE_VERSION} reads at most {MAX_CHANNELS} channels and would "
            f"silently drop the others by position; {len(channel_ids)} were chosen "
            f"({', '.join(channel_ids)}). Choose {MAX_CHANNELS} or fewer."
        )


def channel_layouts(inputs: RunInputs) -> dict[tuple[str, ...] | None, tuple[str, ...]]:
    """Return the run's images by channel layout: the channel IDs at C = 1, 2, ..., in order.

    The layouts are ordered by the number of images, most first (then as first met); None is
    the layout of an image whose channel IDs are not recorded.
    """
    groups: dict[tuple[str, ...] | None, list[str]] = {}
    for fov in inputs.fovs():
        groups.setdefault(fov.channel_ids, []).append(fov.relative_path)
    ordered = sorted(groups.items(), key=lambda item: -len(item[1]))  # stable: first met
    return {layout: tuple(images) for layout, images in ordered}


def channel_number_from_text(text: str) -> int | None:
    """Return the channel number typed (1-based), or None for a blank field.

    Raises
    ------
    SegmentationError
        For text that is not a whole number of 1 or more.
    """
    if not text.strip():
        return None
    try:
        number = int(text.strip())
    except ValueError:
        number = 0
    if number < 1:
        raise SegmentationError(
            f"{text.strip()!r} is not a channel number: type a whole number from 1 (the first "
            "channel), or leave it blank, or choose the channel by its tag."
        )
    return number


def channel_by_number(inputs: RunInputs, number: int) -> str:
    """Return the channel ID at channel number ``number`` (C position, 1-based) of every image.

    A number is allowed only when **every image of the run has the same channel IDs at the
    same C positions** (one :func:`channel_layouts` entry); it is then translated once, to
    that ID, which is resolved by ID as any tag is. It is never resolved image by image.

    Raises
    ------
    SegmentationError
        When the images' channel layouts differ (the refusal names each layout and its
        images, and asks for a tag), or for a number below 1 or beyond the channels.
    """
    if number < 1:  # 0 or -1 would index from the end: never a channel (review aq, FILE 2)
        raise SegmentationError(
            f"Channel number {number} is not a channel number: channel numbers start at 1 (the "
            "first channel). Choose a number of 1 or more, or choose the channel by its tag."
        )
    layouts = channel_layouts(inputs)
    if len(layouts) != 1 or None in layouts:
        raise SegmentationError(_layouts_differ(number, layouts))
    (ids,) = layouts
    if number > len(ids):
        raise SegmentationError(
            f"Channel number {number} is not a channel of this run: its images have "
            f"{len(ids)} channels ({numbered_layout(ids)}). Choose a number from 1 to {len(ids)}, "
            "or choose the channel by its tag."
        )
    return ids[number - 1]


def numbered_layout(ids: tuple[str, ...] | None) -> str:
    """Return a layout as ``1 = DNA, 2 = TUB``."""
    if ids is None:
        return "channel IDs not recorded"
    return ", ".join(f"{n} = {c}" for n, c in enumerate(ids, start=1))


def _layouts_differ(number: int, layouts: dict[tuple[str, ...] | None, tuple[str, ...]]) -> str:
    """Return the refusal of a channel number in a run whose images' channels differ."""
    lines = [
        f"Channel number {number} cannot be used: the images of this run do not all have the "
        "same channels at the same positions, so a number could name a different stain in "
        "different images. Choose the channel by its tag (channel ID) instead. The layouts:"
    ]
    for ids, images in layouts.items():
        named = ", ".join(images[:NAMED_IMAGES])
        more = len(images) - NAMED_IMAGES
        lines.append(
            f"- {numbered_layout(ids)}: {len(images)} image(s): {named}"
            + (f" and {more} more" if more > 0 else "")
        )
    return "\n".join(lines)


def next_step_line(
    mask_types: str | Sequence[str],
    masks_root: str | os.PathLike,
    *,
    register_with: str | None = None,
) -> str:
    """Return the one line said once the masks are made: how they reach preprocessing.

    ``mask_types`` is the mask type made, or the two of the output Both (#440): each is
    declared on its own (the notebook never pairs a cell with a nucleus). ``register_with``:
    where the notebook itself registers the masks (the combined notebook's cell 5,
    :mod:`.notebook_mask_register`); None for the declaration done by hand.

    Raises
    ------
    SegmentationError
        For a mask type preprocessing would refuse.
    """
    types = (mask_types,) if isinstance(mask_types, str) else tuple(mask_types)
    for mask_type in types:
        check_mask_type(mask_type)
    sections = ", ".join(f"[masks.{t}]" for t in types)
    if register_with is not None:
        return f"Next: {register_with} to declare {sections} and describe the masks."
    return (
        f'Next: declare {sections} (representation = "instances") in the experiment '
        f"description, then run preprocessing again with the masks folder "
        f"{os.fspath(masks_root)}."
    )
