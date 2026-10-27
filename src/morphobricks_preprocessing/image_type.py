"""The image type: fluorescence or RGB (``docs/contract.md`` §13, follow-up P4).

Later bricks normalize the two types differently (normalization contract §5):
``fluorescence`` images channel by channel, ``rgb`` images with StarDist's
percentile method over the red, green and blue channels together. The type is
the user's choice. This module suggests it from the image headers (normalization
plan N34): ``rgb`` when every image is stored as RGB colour (TIFF photometric
RGB, three samples per pixel) and the confirmed axis roles make those samples the
channel axis, ``fluorescence`` otherwise. RGB needs exactly three channels of an
integer pixel type; the rule is the normalization brick's
:func:`~morphobricks_normalization.rgb.check_rgb`.

The histograms are the same for both types, so the type changes no row of
``plane_histograms.csv``; it is recorded in the run manifest (contract §9).
"""

from __future__ import annotations

from dataclasses import dataclass

from morphobricks_normalization.rgb import check_rgb

from .axes import AxisReview, channel_count
from .metadata import MetadataReport
from .validation import ValidationIssue, error

FLUORESCENCE = "fluorescence"
RGB = "rgb"
IMAGE_TYPES = (FLUORESCENCE, RGB)


@dataclass(frozen=True)
class ImageTypeReview:
    """What the headers and the confirmed axis roles say about the image type.

    Attributes
    ----------
    suggested
        ``"rgb"`` or ``"fluorescence"``, for the user to confirm.
    reason
        Why it is suggested, in plain language.
    channels_from_rgb_samples
        True when every analysed image is stored as RGB colour and the C role is
        on its samples axis ``S``.
    channel_count
        The number of channels of every analysed image; None when they differ or
        no image fits the axis roles.
    rgb_problem
        Why the type ``rgb`` cannot be chosen, in plain language; None when it can.
    """

    suggested: str
    reason: str
    channels_from_rgb_samples: bool = False
    channel_count: int | None = None
    rgb_problem: str | None = None

    @property
    def rgb_allowed(self) -> bool:
        """True if the images can be of type ``rgb``."""
        return self.rgb_problem is None


def check_image_type(image_type: str) -> str:
    """Return ``image_type`` if it is ``"fluorescence"`` or ``"rgb"``.

    Raises
    ------
    ValueError
        For any other value.
    """
    if image_type not in IMAGE_TYPES:
        raise ValueError(
            f"Image type {image_type!r} does not exist. Choose 'fluorescence' or 'rgb'."
        )
    return image_type


def review_image_type(metadata: MetadataReport, review: AxisReview) -> ImageTypeReview:
    """Suggest the image type and say whether RGB is possible, from the headers alone.

    Parameters
    ----------
    metadata
        The image headers.
    review
        The confirmed axis roles, resolved for every image.

    Returns
    -------
    ImageTypeReview
        ``rgb`` is suggested only when the images are RGB colour on the channel
        axis and can be of type ``rgb``.
    """
    headers = {f.relative_path: f for f in metadata.files}
    files = [(headers[rel], mapping) for rel, mapping in sorted(review.file_mappings.items())]
    from_samples = bool(files) and all(
        f.rgb_samples and mapping.roles[mapping.axes.index("S")] == "C" for f, mapping in files
    )
    counts = {channel_count(f, mapping) for f, mapping in files}
    count = counts.pop() if len(counts) == 1 else None
    problem = _rgb_problem(count, {f.dtype for f, _ in files})
    if from_samples and problem is None:
        return ImageTypeReview(
            RGB,
            "the images are stored as RGB colour (three colour samples per pixel), and these "
            "samples are their channels",
            True,
            count,
        )
    if from_samples:
        reason = "the images are stored as RGB colour, but cannot be of type RGB"
    elif any(f.rgb_samples for f, _ in files):
        reason = "not every image is stored as RGB colour on its channel axis"
    else:
        reason = "the images are not stored as RGB colour"
    return ImageTypeReview(FLUORESCENCE, reason, from_samples, count, problem)


def image_type_issues(image_type: str | None, review: ImageTypeReview) -> list[ValidationIssue]:
    """Return the error ``rgb_not_possible`` when ``rgb`` was chosen for images that cannot be.

    Images with different numbers of channels are reported by the axis review
    (``different_channel_counts``), so they give no issue here.
    """
    if image_type != RGB or review.rgb_allowed or review.channel_count is None:
        return []
    return [
        error(
            "rgb_not_possible",
            review.rgb_problem,
            action="Choose the image type fluorescence, or check the axis roles: the RGB "
            "samples axis must have the role C (channel).",
        )
    ]


def _rgb_problem(count: int | None, dtypes: set) -> str | None:
    if count is None or None in dtypes:
        return "The images do not have one number of channels and one pixel type."
    try:
        check_rgb(count, dtypes)
    except ValueError:
        names = " and ".join(sorted(dtype.name for dtype in dtypes))
        return (
            "RGB images need exactly three colour channels of a whole-number pixel type "
            f"(for example 8- or 16-bit), but these images have {count} "
            f"{'channel' if count == 1 else 'channels'} of type {names}."
        )
    return None
