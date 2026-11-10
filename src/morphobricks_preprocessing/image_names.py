"""An image's position identifier, from its name (contract §1, MorphoBricks D02, D85).

Moved from ``discovery.py`` (PP-5), so that the mask-name screen can use it; the names of
TIFF images and of system files (``is_tiff_name``, ``is_system_name``) are here too.
"""

from __future__ import annotations

# Image name endings removed to give the position identifier, longest first, so
# that "img_01.ome.tif" and "img_01.ome.zarr" both give "img_01".
IMAGE_SUFFIXES = (".ome.tiff", ".ome.tif", ".ome.zarr", ".tiff", ".tif", ".zarr")


# The extensions position_id removes: the images this brick reads, then the other formats
# MorphoBricks bricks read (CellPatcher: PNG, JPEG, BMP, GIF, NumPy), so every brick names a
# position by this one function (MorphoBricks decision D85, 2026-10-29). Naming only: the
# images discovery accepts are still IMAGE_SUFFIXES.
NAME_SUFFIXES = (*IMAGE_SUFFIXES, ".png", ".jpeg", ".jpg", ".bmp", ".gif", ".npy")


def position_id(image: str) -> str:
    """Return the position identifier of an image: its name without the extension.

    Every image is exactly one position, because files holding several
    positions, scenes or tiles are refused, so the file name identifies the
    position within its technical repeat. The full position (field of view) key
    is ``condition``, ``biological_repeat``, ``technical_repeat`` and this
    identifier (contract §1). The extension is ``.tif``, ``.tiff`` or ``.zarr``
    (any case), together with a preceding ``.ome``, or one of the other image
    formats MorphoBricks bricks read (``.png``, ``.jpg``, ``.jpeg``, ``.bmp``,
    ``.gif``, ``.npy``; :data:`NAME_SUFFIXES`), so every brick names a position
    with this one function (MorphoBricks D85).

    Parameters
    ----------
    image
        The image's path relative to the experiment root, or its name.

    Returns
    -------
    str
        For example ``"img_01"`` for ``"ctrl/rep1/img_01.ome.tif"``.

    Raises
    ------
    ValueError
        If the name is only an extension (``".tif"``, ``".ome.zarr"``): it has
        no name to identify a position by. The longest extension decides, so
        ``".ome.tif"`` is refused rather than read as ``".ome"``.
    """
    name = image.replace("\\", "/").rsplit("/", 1)[-1]
    lowered = name.lower()
    for suffix in NAME_SUFFIXES:
        if lowered.endswith(suffix):
            if len(name) == len(suffix):
                raise ValueError(
                    f"{image!r} is only an extension: it has no name to identify a position by"
                )
            return name[: -len(suffix)]
    return name


TIFF_SUFFIXES = (".tif", ".tiff")

# Files and folders created by operating systems and tools, not by users. They
# are skipped without a warning so that copying an experiment from a Mac or
# opening it in Jupyter does not flood the preflight with messages.
SYSTEM_NAMES = frozenset({"thumbs.db", "desktop.ini", "__macosx"})


def is_system_name(name: str) -> bool:
    """Return True for system files and folders that are skipped silently.

    These are dot-files and dot-folders (``.DS_Store``, ``._image.tif``,
    ``.ipynb_checkpoints``), ``Thumbs.db``, ``desktop.ini`` and ``__MACOSX``.
    """
    return name.startswith(".") or name.lower() in SYSTEM_NAMES


def is_tiff_name(name: str) -> bool:
    """Return True if ``name`` has a TIFF extension (``.tif`` or ``.tiff``, any case)."""
    return name.lower().endswith(TIFF_SUFFIXES)
