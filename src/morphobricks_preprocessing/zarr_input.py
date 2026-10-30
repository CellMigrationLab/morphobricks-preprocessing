"""Open OME-Zarr images (OME-NGFF 0.4 and 0.5) given as input.

An OME-Zarr image is a folder whose name ends in ``.zarr`` (usually
``.ome.zarr``). It is found by folder discovery like a TIFF file and analysed
the same way: only the full-resolution level 0 of a single image is read, one
2D plane at a time (``docs/contract.md`` §1 and §2).

The axes come from the image's OME-NGFF metadata, translated into the same
one-letter codes ``tifffile`` uses: ``T`` for a time axis, ``C`` for a channel
axis, and ``Z``, ``Y`` and ``X`` for space axes of those names. Any other axis
becomes ``Q`` (no known role), so it is never guessed.

A store that holds several images (several ``multiscales`` entries, a
``bioformats2raw`` folder with more than one series, a plate or a well with
several fields) is described by its first image and reports how many images it
holds, so that the preflight can block it like a multi-series TIFF.

The physical size of a pixel along each axis, with its unit, comes from the
OME-NGFF scale of level 0 (:attr:`NgffImage.physical_sizes`).

The label images of an image (segmentations in its OME-NGFF ``labels`` group,
such as ``cells`` or ``nuclei``) can be listed with :func:`ngff_label_names`
and opened with :func:`open_ngff_labels`, for later bricks that use them as
masks; this brick does not analyse them.

Needs the ``zarr`` extra. OME-NGFF 0.4 (Zarr format 2) can be read with zarr 2.18
or 3.x; OME-NGFF 0.5 (Zarr format 3) needs zarr 3, which needs Python 3.11 or newer.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .physical_sizes import PhysicalSizes, ngff_physical_sizes

logger = logging.getLogger(__name__)

ZARR_SUFFIX = ".zarr"


class ZarrUnavailableError(ImportError):
    """Raised when OME-Zarr is needed but the ``zarr`` package is not installed."""


class NgffError(ValueError):
    """Raised when a folder is not an OME-Zarr image this brick can read."""


def read_channel_names(raw: Iterable[Any]) -> tuple[tuple[str | None, ...], bool]:
    """Return channel names as read from metadata, and whether any was trimmed.

    Leading and trailing whitespace is removed from every name, for every image (TIFF,
    OME-Zarr, and this brick's own copies), so ``"DAPI "`` and ``"DAPI"`` are one name.
    Whitespace inside a name is kept: names are otherwise compared exactly. A name that
    is empty, only whitespace or not text is unnamed (None). The names are empty when
    none is given. The flag is recorded in the run manifest (``channel_names_trimmed``).
    """
    names: list[str | None] = []
    trimmed = False
    for value in raw:
        if not isinstance(value, str):
            names.append(None)
            continue
        name = value.strip()
        trimmed = trimmed or name != value
        names.append(name or None)
    return (tuple(names) if any(names) else ()), trimmed


def is_zarr_name(name: str) -> bool:
    """Return True if a folder name marks a Zarr store (``.zarr``, any case)."""
    return name.lower().endswith(ZARR_SUFFIX)


def is_zarr_store(path: str | os.PathLike) -> bool:
    """Return True if ``path`` is a folder named like a Zarr store."""
    path = Path(path)
    return is_zarr_name(path.name) and path.is_dir()


def zarr_available() -> bool:
    """Return True if the ``zarr`` package can be imported."""
    try:
        _zarr()
    except ZarrUnavailableError:
        return False
    return True


def canonical_dtype(dtype: np.dtype) -> np.dtype:
    """Return the standard NumPy dtype with the same name, e.g. ``int64`` for ``longlong``.

    On Linux and macOS, NumPy has two 64-bit integer types (``long`` and ``long
    long``) that compare equal but are different classes; tifffile may return
    either, and zarr 3 only recognises the standard one.
    """
    return np.dtype(np.dtype(dtype).name)


@dataclass(frozen=True)
class NgffImage:
    """The full-resolution array of the first image in an OME-Zarr store.

    Attributes
    ----------
    array
        The level-0 zarr array (not read).
    axes
        One letter per dimension, as ``tifffile`` would label it (e.g. ``"TCZYX"``).
    version
        The OME-NGFF version from the metadata, or ``""`` if it is not stated.
    image_count
        Number of images in the store; more than one blocks processing.
    channel_names
        Channel labels from the ``omero`` metadata, without leading or trailing
        whitespace (:func:`read_channel_names`); empty when not given.
    channel_names_trimmed
        True when leading or trailing whitespace was removed from a label.
    dataset_path
        Path of the level-0 array inside the store, e.g. ``"0"`` or ``"0/0"``.
    physical_sizes
        Level-0 size of one step along X, Y, Z and T with each axis's unit, from
        the OME-NGFF ``coordinateTransformations`` scale and axis units
        (:func:`.physical_sizes.ngff_physical_sizes`), in the form
        :func:`.physical_sizes.read_physical_sizes` gives for TIFF files; None
        where the metadata does not give them.
    """

    array: Any
    axes: str
    version: str
    image_count: int
    channel_names: tuple[str | None, ...] = ()
    dataset_path: str = ""
    channel_names_trimmed: bool = False
    physical_sizes: PhysicalSizes = PhysicalSizes()

    @property
    def shape(self) -> tuple[int, ...]:
        """Shape of the level-0 array."""
        return tuple(int(n) for n in self.array.shape)

    @property
    def dtype(self) -> np.dtype:
        """Pixel type of the level-0 array, in native byte order."""
        return canonical_dtype(np.dtype(self.array.dtype).newbyteorder("="))

    @property
    def chunks(self) -> tuple[int, ...]:
        """Chunk shape of the level-0 array: the unit that is decoded at once."""
        return tuple(int(n) for n in self.array.chunks)


def open_ngff(path: str | os.PathLike) -> NgffImage:
    """Open the first image of an OME-Zarr store without reading its pixels.

    Raises
    ------
    ZarrUnavailableError
        If the ``zarr`` package is not installed.
    NgffError
        If the folder cannot be opened or has no usable OME-NGFF metadata.
    """
    prefix, group, meta, image_count = _image_group(path)
    return _level0(group, meta, prefix, image_count)


def ngff_label_names(path: str | os.PathLike) -> tuple[str, ...]:
    """Return the names of the label images of the first image in an OME-Zarr store.

    They are listed in the ``labels`` group next to the image (OME-NGFF 0.4 and
    0.5), for example ``("cells", "nuclei")``. Empty when the image has no
    ``labels`` group.

    Raises
    ------
    ZarrUnavailableError
        If the ``zarr`` package is not installed.
    NgffError
        If the store cannot be opened or its ``labels`` metadata is malformed.
    """
    _, group, _, _ = _image_group(path)
    labels = _labels_group(group)
    if labels is None:
        return ()
    names = _ome_attrs(labels).get("labels")
    if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
        raise NgffError("its labels group does not list its label images")
    return tuple(names)


def open_ngff_labels(path: str | os.PathLike, name: str | None = None) -> NgffImage:
    """Open level 0 of a label image of an OME-Zarr store without reading its pixels.

    Parameters
    ----------
    path
        The OME-Zarr image.
    name
        The label image, one of :func:`ngff_label_names`. May be None when the
        image has exactly one.

    Returns
    -------
    NgffImage
        The label image's level-0 array and axes; ``dataset_path`` is its path
        inside the store, e.g. ``"labels/cells/0"``.

    Raises
    ------
    ZarrUnavailableError
        If the ``zarr`` package is not installed.
    NgffError
        If the image has no label images, ``name`` is not one of them, or
        ``name`` is None and there are several.
    """
    prefix, group, _, _ = _image_group(path)
    names = ngff_label_names(path)
    if not names:
        raise NgffError("it has no label images (no labels group)")
    if name is None:
        if len(names) > 1:
            raise NgffError(f"it has several label images ({', '.join(names)}); choose one by name")
        name = names[0]
    if name not in names:
        raise NgffError(f"it has no label image {name!r}; its label images are {', '.join(names)}")
    labels = _labels_group(group)
    try:
        label_group = labels[name]
    except KeyError as exc:
        raise NgffError(f"the label image {name!r} listed in its metadata is missing") from exc
    return _level0(label_group, _ome_attrs(label_group), f"{prefix}labels/{name}/", 1)


def _image_group(path: str | os.PathLike) -> tuple[str, Any, dict, int]:
    """Open a store and descend to its first image: ``(prefix, group, meta, image_count)``."""
    zarr = _zarr()
    path = Path(path)
    if (path / "zarr.json").exists() and _zarr_major() < 3:
        raise NgffError(
            "it is stored in Zarr format 3 (OME-NGFF 0.5), which needs zarr 3 "
            "(Python 3.11 or newer)"
        )
    try:
        group = zarr.open_group(str(path), mode="r")
    except Exception as exc:  # zarr raises many types for damaged or foreign folders
        raise NgffError(
            f"it cannot be opened as a Zarr group ({type(exc).__name__}: {exc})"
        ) from exc

    prefix = ""
    meta = _ome_attrs(group)
    image_count = 1
    if "plate" in meta:
        wells = meta["plate"].get("wells") or []
        if not wells:
            raise NgffError("the plate has no wells")
        prefix, group, meta, fields = _descend(group, wells[0]["path"])
        image_count = max(2, len(wells) * max(fields, 1))
    if "well" in meta:
        images = meta["well"].get("images") or []
        if not images:
            raise NgffError("the well has no images")
        sub, group, meta, _ = _descend(group, images[0]["path"])
        prefix = f"{prefix}{sub}"
        image_count = max(image_count, len(images))
    if "bioformats2raw.layout" in meta:
        series = sorted((k for k in group.group_keys() if k.isdigit()), key=int)
        if not series:
            raise NgffError("the bioformats2raw folder contains no image")
        sub, group, meta, _ = _descend(group, series[0])
        prefix = f"{prefix}{sub}"
        image_count = max(image_count, len(series))
    return prefix, group, meta, image_count


def _level0(group, meta: dict, prefix: str, image_count: int) -> NgffImage:
    """Describe level 0 of the multiscales image in ``group``."""
    multiscales = meta.get("multiscales")
    if not multiscales:
        raise NgffError("it has no OME-NGFF multiscales metadata")
    image_count = max(image_count, len(multiscales))
    first = multiscales[0]
    datasets = first.get("datasets") or []
    if not datasets:
        raise NgffError("its multiscales metadata lists no datasets")
    dataset_path = str(datasets[0]["path"])
    try:
        array = group[dataset_path]
    except KeyError as exc:
        raise NgffError(f"the level-0 array {dataset_path!r} is missing") from exc
    axes = ngff_axes(first.get("axes"), len(array.shape))
    if len(axes) != len(array.shape):
        raise NgffError(f"its metadata names {len(axes)} axes but the array has {len(array.shape)}")
    omero = meta.get("omero") if isinstance(meta.get("omero"), dict) else {}
    raw_labels = [
        channel.get("label") if isinstance(channel, dict) else None
        for channel in omero.get("channels") or []
    ]
    for index, label in enumerate(raw_labels):
        # A label that is not text is malformed metadata: refuse rather than read the
        # channel as unnamed, which would hide it from the channel-name check (#37).
        if label is not None and not isinstance(label, str):
            raise NgffError(f"the label of channel {index} in its omero metadata is not text")
    labels, trimmed = read_channel_names(raw_labels)
    return NgffImage(
        array=array,
        axes=axes,
        version=str(meta.get("version") or first.get("version") or ""),
        image_count=image_count,
        channel_names=labels,
        channel_names_trimmed=trimmed,
        dataset_path=f"{prefix}{dataset_path}",
        physical_sizes=_physical_sizes(first, axes, dataset_path),
    )


def _physical_sizes(multiscale: dict, axes: str, where: str) -> PhysicalSizes:
    """Return the image's physical sizes; empty when its scale metadata cannot be used.

    The sizes are for information only, so metadata that cannot be understood
    never makes the image unreadable.
    """
    try:
        return ngff_physical_sizes(multiscale, axes)
    except Exception:  # sizes are optional; never block an image over them
        logger.debug("Cannot read the physical sizes of %s", where, exc_info=True)
        return PhysicalSizes()


def _labels_group(group):
    """Return the ``labels`` group next to an image, or None when it has none."""
    try:
        labels = group["labels"]
    except KeyError:
        return None
    return labels if hasattr(labels, "group_keys") else None


def ngff_axes(axes: Any, ndim: int) -> str:
    """Translate OME-NGFF axis metadata into one-letter codes.

    ``axes`` is the ``multiscales[0]["axes"]`` entry: a list of objects with
    ``name`` and ``type`` (OME-NGFF 0.4 and 0.5) or of names (0.3). Without axes
    metadata (OME-NGFF 0.1 and 0.2) the order is fixed to ``t, c, z, y, x``.
    """
    if not axes:
        if ndim != 5:
            raise NgffError("it does not say what its axes are")
        return "TCZYX"
    letters = []
    for axis in axes:
        name, kind = (
            (axis.get("name"), axis.get("type")) if isinstance(axis, dict) else (axis, None)
        )
        name = str(name or "").lower()
        kind = str(kind or "").lower()
        if kind == "time" or (not kind and name == "t"):
            letters.append("T")
        elif kind == "channel" or (not kind and name == "c"):
            letters.append("C")
        elif kind in ("space", "") and name in ("z", "y", "x"):
            letters.append(name.upper())
        else:
            letters.append("Q")
    return "".join(letters)


def read_selection(array: Any, selection: tuple) -> np.ndarray:
    """Read part of a zarr array as a NumPy array in native byte order."""
    data = np.asarray(array[selection])
    return data.astype(canonical_dtype(data.dtype.newbyteorder("=")), copy=False)


# zarr 2 / 3 ---------------------------------------------------------------------------


def _zarr():
    try:
        import zarr
    except ImportError as exc:
        raise ZarrUnavailableError(
            "OME-Zarr needs the zarr package. Install it with: "
            'pip install "morphobricks-preprocessing[zarr]"'
        ) from exc
    return zarr


def _zarr_major() -> int:
    return int(_zarr().__version__.split(".")[0])


def _ome_attrs(group) -> dict:
    """Return the OME metadata of a group (nested under ``"ome"`` in OME-NGFF 0.5)."""
    attrs = dict(group.attrs)
    ome = attrs.get("ome")
    return dict(ome) if isinstance(ome, dict) else attrs


def _descend(group, path: str):
    try:
        child = group[path]
    except KeyError as exc:
        raise NgffError(f"the image {path!r} listed in its metadata is missing") from exc
    meta = _ome_attrs(child)
    fields = len((meta.get("well") or {}).get("images") or [])
    return f"{path}/", child, meta, fields
