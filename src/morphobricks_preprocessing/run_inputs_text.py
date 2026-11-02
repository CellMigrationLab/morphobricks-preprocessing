"""The texts :class:`.run_inputs.RunInputs` refuses a run with (moved from :mod:`.run_inputs`).

Each says what changed or is missing since the run, and what to do. Nothing here reads or
writes files.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .validation import plural

if TYPE_CHECKING:
    from .unrecorded import UnrecordedFiles
    from .zarr_input import NgffError


def _changed_text(paths: list[str], what: str = "image") -> str:
    """Say which files changed or went missing since the run."""
    where = "experiment_root=" if what == "image" else "masks_root="
    return (
        f"{len(paths)} {what}{'' if len(paths) == 1 else 's'} changed or missing since the "
        f"run: {_shown(paths)}. Run preprocessing again on the experiment as it is (or, if the "
        f"{what}s were only moved, give {where} where they are now)."
    )


def _shown(paths: tuple[str, ...] | list[str]) -> str:
    """Name up to five paths, sorted."""
    return ", ".join(sorted(paths)[:5]) + (" and more" if len(paths) > 5 else "")


def _without_axes_text(paths: list[str]) -> str:
    """Say which masks the run recorded without axes, and what to do."""
    return (
        f"The run recorded {plural(len(paths), 'mask')} without axes: "
        f"{_shown(paths)}. A run before v0.4.0 wrote such a record for a mask whose header "
        "puts X before Y; masks are never transposed. Save each such mask again with its "
        "last two axes swapped (numpy.swapaxes(mask, -1, -2)), then run preprocessing again."
    )


def _unrecorded_text(files: UnrecordedFiles) -> str:
    """Name the images and masks the run did not record, and what to do."""
    parts = [
        f"{plural(len(found), what)} ({_shown(found)})"
        for what, found in (("image", files.images), ("mask", files.masks))
        if found
    ]
    masks = (
        " with its masks folder (--masks <masks folder>; the label images of OME-Zarr images "
        "are added)"
        if files.masks
        else ""
    )
    exclude = ", or exclude the images (--exclude)" if files.images else ""
    return (
        f"The experiment holds {' and '.join(parts)} that the preprocessing run did not "
        "record, for example added since the run; they would be left out. Run preprocessing "
        f"again on the experiment as it is{masks} (morphobricks-preprocessing run --output "
        f"<output folder>){exclude}."
    )


_ZARR_MISSING = (
    "The run holds OME-Zarr images, and the zarr package is not installed, so their label "
    "images cannot be checked against the run (none is ever skipped). Install it with: "
    'pip install "morphobricks-preprocessing[zarr]"'
)


def _labels_unreadable_text(exc: NgffError) -> str:
    """Say which OME-Zarr image's labels group cannot be read, and what to do."""
    return (
        f"The run cannot be checked: {exc}. Its label images may have changed since the "
        "run; check the image's labels group, then run preprocessing again on the "
        "experiment as it is."
    )
