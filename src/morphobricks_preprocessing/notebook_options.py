"""Choices and fixed settings shared by the notebook's steps."""

from __future__ import annotations

import math

import ipywidgets as widgets

from .image_type import FLUORESCENCE, RGB
from .run.modes import AUTO, FULL, RESTART

ROLE_OPTIONS = [
    ("Z - depth (focal planes)", "Z"),
    ("C - channel (stain or colour)", "C"),
    ("T - time (frames)", "T"),
    ("Y - height (image rows)", "Y"),
    ("X - width (image columns)", "X"),
    ("No role (only for length 1)", "-"),
]

IMAGE_TYPE_OPTIONS = [
    ("Fluorescence - each channel is a separate stain or marker", FLUORESCENCE),
    ("RGB - colour images (red, green, blue), e.g. brightfield or histology stains", RGB),
]
IMAGE_TYPE_NAMES = {FLUORESCENCE: "Fluorescence", RGB: "RGB"}

LEVEL_OPTIONS = [
    ("Condition", "condition"),
    ("Biological repeat", "biological_repeat"),
    ("Technical repeat", "technical_repeat"),
    ("Image", "image"),
]

# Problems a user can get past by leaving the affected images out (open question 6).
EXCLUDABLE_CODES = ("unreadable_tiff", "multiple_series", "unsupported_dtype")

_SKIPPED_CODES = ("non_tiff_file", "empty_folder")

# "What will happen" is a short summary, so its lists of images fold early.
_PLAN_PATHS = 5

# Step 3's histogram range (follow-up P6, normalization contract §1.4).
HISTOGRAM_RANGE_OPTIONS = [
    ("Minimum to maximum of each plane (recommended)", "minmax"),
    ("Robust: between two quantiles, the pixels outside counted separately", "robust"),
]

# Step 3's mode (design v0.4 §1, PP-7): what each reuses, and what both keep.
MODE_OPTIONS = [
    (
        "Restart (recommended): reuse the rows of unchanged images, read new or changed ones",
        RESTART,
    ),
    ("Full: read every image again and recompute every row and check", FULL),
]
MODE_HELP = (
    "Both modes keep your confirmations (labels, design, axis roles, image type, stated pixel "
    "sizes) and write the table only when the whole run succeeds: a run that stops leaves the "
    "earlier results as they are. Full takes longer; use it to start from scratch."
)
VERIFY_CONTENT_HELP = (
    "An image counts as unchanged when its size and modification time are (for OME-Zarr, "
    "those of every file). A file rewritten with the same size and its time set back would "
    "not be seen. Verifying the content also compares each image's SHA-256 before its earlier "
    "rows are reused: every image is read once more. Turning it on or off reads every image "
    "again."
)

# Step 3's worker threads (issue #107). The notebook defaults to Auto under a memory cap of half
# the memory available when it starts (owner, 16 November 2026); the CLI's --workers stays 1.
WORKER_OPTIONS = [
    ("1: one image after another", 1),
    ("2 images at once", 2),
    ("4 images at once", 4),
    ("8 images at once", 8),
    ("Auto: as many as this computer's CPUs and the memory cap allow", AUTO),
]
MIN_DEFAULT_CAP_GIB = 0.5
"""Below this default cap the notebook starts serial: too little memory to share."""


def default_max_ram_gib(available: int | None) -> float | None:
    """Return the default memory cap, in GiB: half of ``available`` bytes, or None.

    Rounded down to 0.1 GiB. None when the available memory is unknown or the half is below
    :data:`MIN_DEFAULT_CAP_GIB`: the notebook then starts with one image at a time.
    """
    if available is None:
        return None
    cap = int(available / 2 / 2**30 * 10) / 10
    return cap if cap >= MIN_DEFAULT_CAP_GIB else None


def cap_help(available: int | None, cap: float | None, physical: bool = False) -> str:
    """Return the line saying where the default memory cap comes from.

    ``physical``: the figure is the computer's physical memory, not what was available
    (:func:`.run.workers.memory_is_physical`, macOS), and the line says so.
    """
    if cap is None:
        what = "physical memory" if physical else "memory this computer has available"
        return (
            f"The {what} could not be read (or is too small to share), so the notebook starts "
            "with one image at a time."
        )
    if physical:
        return (
            f"Default: {cap:g} GiB, half of the {available / 2**30:.1f} GiB of physical memory "
            "this computer has (other programs' use is not counted here). Change it if other "
            "programs need more or less."
        )
    return (
        f"Default: {cap:g} GiB, half of the {available / 2**30:.1f} GiB this computer had "
        "available when the notebook started. Change it if other programs need more or less."
    )


def serial_note(cap: float, need: int) -> str:
    """Return the note saying why the notebook starts this dataset with one image at a time.

    ``cap`` is the default memory cap in GiB; ``need`` the bytes the largest image needs
    (:func:`.run.workers.max_ram_needed`), rounded up to 0.1 GiB so that it reads as larger.
    """
    largest = math.ceil(need / 2**30 * 10) / 10
    return (
        f"Images read at once: 1, because the default memory cap ({cap:g} GiB) is smaller than "
        f"the largest image ({largest:.1f} GiB); outputs are identical for any number."
    )


WORKERS_HELP = (
    "Each image is read whole in its own thread. The outputs are byte-identical whatever the "
    "number. The speed-up is modest: most of the histogram work holds Python's global "
    "interpreter lock (GIL), so the threads mostly take turns. More images at once also needs "
    "more memory: each holds its own plane (or block) in memory."
)
MAX_RAM_HELP = (
    "With Auto only: the memory the run may use, in GiB (0: no cap). Auto then reads only as "
    "many images at once as the largest one's estimated working memory fits; an image that "
    "alone needs more stops the run before any pixel is read."
)

_MAX_PLOTTED_GROUPS = 12
# Short names of the intensity checks, for the tiles and the table's first column.
_CHECK_LABELS = {
    "image_intensity_outlier": "Image outlier",
    "repeat_intensity_shift": "Shifted repeat",
    "saturated_image": "Saturated",
    "no_intensity_spread": "No spread",
    "channel_without_finite_pixels": "Not checked",
}
_PACKAGE_LOGGER = "morphobricks_preprocessing"
_WIDE = {"width": "99%"}
_PAIRING_COLUMNS = widgets.Layout(
    grid_template_columns="minmax(120px, 1fr) minmax(120px, 1fr) minmax(160px, 2fr)",
    grid_gap="4px 12px",
    width="99%",
)
