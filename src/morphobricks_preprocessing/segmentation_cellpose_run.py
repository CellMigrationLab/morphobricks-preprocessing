"""The run, its channels and the closing words of the Cellpose notebook.

Split from :mod:`.segmentation_cellpose`, which re-exports them, to keep each file small.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path

from .run_inputs import RunInputs
from .segmentation_cellpose_model import CELLPOSE_VERSION
from .segmentation_record import SegmentationError, check_mask_type

MAX_CHANNELS = 3
"""Cellpose 4.2.1.1 reads at most three channels and drops the rest silently."""
NAMED_IMAGES = 5
"""How many images of a channel layout a refusal names before it counts the rest."""


def open_run(run_folder: str) -> RunInputs:
    """Open the run in ``run_folder`` (preprocessing's own checks; refusals are unchanged).

    Raises
    ------
    SegmentationError
        For a blank folder.
    RunInputsError
        For a run that cannot be opened (:meth:`.run_inputs.RunInputs.open`).
    """
    if not run_folder.strip():
        raise SegmentationError("Give the run's output folder (the one holding run_manifest.json).")
    return RunInputs.open(Path(run_folder.strip()))


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
        images, and asks for a tag), or for a number beyond the channels.
    """
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


def next_steps_lines(
    mask_types: str | Sequence[str],
    masks_root: str | os.PathLike,
    *,
    register_with: str | None = None,
) -> tuple[str, ...]:
    """Return what to do after the masks are made, as lines of text.

    ``mask_types`` is the mask type made, or the two of the output Both (#440): each is
    declared on its own. The notebook never pairs a cell with a nucleus; that relationship is
    computed downstream from the geometry (MorphoRelate, containment or overlap).

    ``register_with``: where the notebook itself registers the masks (the combined notebook:
    its cell 5, :mod:`.notebook_mask_register`), which then replaces the four steps done by
    hand in the Preprocessing notebook; None for those steps.

    Raises
    ------
    SegmentationError
        For a mask type preprocessing would refuse.
    """
    types = (mask_types,) if isinstance(mask_types, str) else tuple(mask_types)
    for mask_type in types:
        check_mask_type(mask_type)
    sections = "; ".join(
        f'[masks.{t}]  source_type = "{t}"  representation = "instances"' for t in types
    )
    named = "the new mask type" if len(types) == 1 else f"the {len(types)} new mask types"
    rerun = (
        f"preprocessing again with the masks folder {os.fspath(masks_root)} (the histograms "
        "are reused; the masks are read and checked)"
    )
    if register_with is not None:
        lines = [
            f"1. {register_with}: it declares {named} in the experiment description "
            f"({sections}), saves it, runs {rerun} and describes the masks.",
            "2. Then continue with MorphoFeature.",
        ]
    else:
        lines = [
            f"1. Declare {named} in the experiment description, in the preprocessing "
            f"notebook (step 4, Masks) or by adding to morphobricks.toml:  {sections}.",
            "2. Save the description.",
            f"3. Run {rerun}.",
            "4. Describe the masks (step 4 of the preprocessing notebook).",
            "5. Then continue with MorphoFeature.",
        ]
    if len(types) > 1:
        lines.append(
            f"The mask types {' and '.join(types)} are separate: this notebook never pairs a "
            "cell with a nucleus by label number. Relate them downstream from their geometry "
            "(MorphoRelate: containment or overlap)."
        )
    return tuple(lines)
