"""The arguments of :func:`.pipeline.run_experiment`, checked before a run starts.

Moved from :mod:`.pipeline` (issue #107, to keep it within the size rules).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from ..image_type import check_image_type
from .modes import run_mode


def check_arguments(
    mode: object,
    image_type: str | None,
    missing_as_singleton: object,
) -> None:
    """Refuse arguments that are not allowed, before anything is read or written.

    Raises
    ------
    ValueError
        For an unknown mode (:func:`.modes.run_mode`, with its ``workers`` and ``max_ram``)
        or image type, or a ``missing_as_singleton`` that is not True or False.
    """
    run_mode(mode)
    if image_type is not None:
        check_image_type(image_type)
    # The manifest records it as a JSON boolean (contract §9).
    if missing_as_singleton is not None and not isinstance(missing_as_singleton, bool):
        raise ValueError(
            f"missing_as_singleton must be True or False, found {missing_as_singleton!r}"
        )


def masks_argument(
    masks_folder: str | os.PathLike | Literal[False] | None,
) -> Path | Literal[False] | None:
    """Return the masks folder resolved; False and None stay as they are."""
    if masks_folder is None or masks_folder is False:
        return masks_folder
    return Path(masks_folder).resolve()
