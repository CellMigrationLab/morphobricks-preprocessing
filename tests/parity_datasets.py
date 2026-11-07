"""Legacy homogeneous datasets for the cross-version parity suite, and their descriptions.

Every dataset is a dict of image paths (relative to the experiment folder) to a seed, written
as random pixels so each plane's histogram is distinct. ``described`` gives the experiment
description a user would publish for it: IDs equal to the folder labels (the legacy case, Spec
v13), one control referenced by every other condition, the same layout everywhere.
"""

from __future__ import annotations

import numpy as np
import tifffile

from morphobricks_preprocessing.description import (
    Channel,
    Condition,
    Description,
    Member,
    Repeat,
    Unit,
)
from morphobricks_preprocessing.description_write import canonical_bytes

DATE = "2026-10-30"


def _write(path, data, axes, **kwargs):
    path.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(path, data, photometric=kwargs.pop("photometric", "minisblack"), **kwargs)


def _pixels(seed, shape):
    return np.random.default_rng(seed).integers(0, 255, shape, dtype=np.uint8)


def write_plain(root, images):
    """Write 2-D YX images."""
    for seed, rel in enumerate(images, start=1):
        _write(root / rel, _pixels(seed, (8, 8)), "YX", metadata={"axes": "YX"})


def write_ome(root, images):
    """Write two-channel OME-TIFF images whose headers name the channels."""
    for seed, rel in enumerate(images, start=1):
        meta = {"axes": "CYX", "Channel": {"Name": ["DAPI", "GFP"]}}
        _write(root / rel, _pixels(seed, (2, 8, 8)), "CYX", ome=True, metadata=meta)


def write_rgb(root, images):
    """Write RGB images (samples axis S)."""
    for seed, rel in enumerate(images, start=1):
        _write(root / rel, _pixels(seed, (8, 8, 3)), "YXS", photometric="rgb")


# name -> (images, writer, axes, image_type, channels per image, control condition or None)
DATASETS = {
    "one_repeat": (["A/rep1/a.tif", "A/rep1/b.tif"], write_plain, "YX", None, 1, None),
    "two_by_two": (
        [f"{c}/{r}/{n}.tif" for c in ("CTRL", "DrugA") for r in ("rep1", "rep2") for n in "ab"],
        write_plain,
        "YX",
        None,
        1,
        "CTRL",
    ),
    "technical_repeats": (
        [
            f"{c}/{r}/{t}/{n}.tif"
            for c in ("CTRL", "DrugA")
            for r in ("rep1", "rep2")
            for t in ("acq1", "acq2")
            for n in "ab"
        ],
        write_plain,
        "YX",
        None,
        1,
        "CTRL",
    ),
    "flat": (["a.tif", "b.tif", "c.tif"], write_plain, "YX", None, 1, None),
    "ome_names": (
        [f"{c}/rep{r}/a.ome.tif" for c in ("CTRL", "DrugA") for r in (1, 2)],
        write_ome,
        "CYX",
        None,
        2,
        "CTRL",
    ),
    "rgb": (
        [f"{c}/rep{r}/a.tif" for c in ("CTRL", "DrugA") for r in (1, 2)],
        write_rgb,
        "YXS",
        "rgb",
        3,
        "CTRL",
    ),
}


def _split(rel):
    """Return ``(condition, repeat, technical)`` folders of an image path, ``single`` if absent."""
    folders = rel.split("/")[:-1]
    return tuple(folders + ["single"] * (3 - len(folders)))


def described(images, channels, control, image_type, date=DATE) -> Description:
    """Return the description of a dataset: folder labels as IDs, one reference per condition."""
    layout: dict = {}
    for rel in images:
        condition, repeat, technical = _split(rel)
        members = layout.setdefault(repeat, {}).setdefault(condition, {})
        members[technical] = members.get(technical, 0) + 1
    names = ("R", "G", "B") if image_type == "rgb" else ("DAPI", "GFP")[:channels]
    names = names if channels > 1 else ("C0",)
    conditions = sorted({c for members in layout.values() for c in members})
    repeats = {
        repeat: Repeat(
            True,
            date,
            {
                condition: Member(
                    repeat,
                    sum(units.values()),
                    {
                        technical: Unit(
                            technical,
                            names,
                            image_type="rgb" if image_type == "rgb" else None,
                        )
                        for technical in sorted(units)
                    },
                )
                for condition, units in sorted(members.items())
            },
        )
        for repeat, members in sorted(layout.items())
    }
    return Description(
        {
            c: Condition(c, reference=control if control and c != control else None)
            for c in conditions
        },
        {n: Channel() for n in names},
        repeats,
        {
            "conditions": tuple(conditions),
            "channels": tuple(sorted(names)),
            "repeats": tuple(sorted(repeats)),
            "technical_repeats": tuple(
                sorted({t for m in layout.values() for u in m.values() for t in u})
            ),
        },
    )


def description_bytes(*args, **kwargs) -> bytes:
    """Return the canonical bytes of :func:`described` (it must be one the reader accepts)."""
    return canonical_bytes(described(*args, **kwargs))
