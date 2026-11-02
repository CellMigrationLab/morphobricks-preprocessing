"""Choices and fixed settings shared by the notebook's steps."""

from __future__ import annotations

import ipywidgets as widgets

from .image_type import FLUORESCENCE, RGB

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

PYRAMID_OPTIONS = [
    ("Automatic (recommended)", "auto"),
    ("Full resolution only", "none"),
    ("Choose the number of smaller copies", "fixed"),
]
_MAX_PLOTTED_GROUPS = 12
# Short names of the intensity checks, for the tiles and the table's first column.
_CHECK_LABELS = {
    "image_intensity_outlier": "Image outlier",
    "repeat_intensity_shift": "Shifted repeat",
    "saturated_image": "Saturated",
    "no_intensity_spread": "No spread",
}
_PACKAGE_LOGGER = "morphobricks_preprocessing"
_WIDE = {"width": "99%"}
_PAIRING_COLUMNS = widgets.Layout(
    grid_template_columns="minmax(120px, 1fr) minmax(120px, 1fr) minmax(160px, 2fr)",
    grid_gap="4px 12px",
    width="99%",
)
