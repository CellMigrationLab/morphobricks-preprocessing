"""Builders of the H&E tissue mask tests (Spec ADR 0005 BM15; design he-tissue-mask §7).

:func:`synthetic_he` is the 96 x 96 test image of design §7, made from known colours; its parts
are returned with it so tests check every component pixel by pixel. :func:`rgb_run` writes a
described run of RGB images (one unit declared ``rgb``), publishes the description and runs
preprocessing and the masks step, so the helper is called on a real run.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import tifffile

from morphobricks_preprocessing import RunInputs, prefill_mapping
from morphobricks_preprocessing.testing_described import publish_and_run

PINK = (240, 150, 200)  # eosin-stained stroma: kept (H < 0.05, not red-cell, max OD >= 0.08)
NUCLEUS = (60, 40, 120)  # hematoxylin: H = 0.155
RED_CELL = (200, 30, 40)  # E = 0.094, H = 0, OD_G - OD_R = 0.82, OD_B - OD_R = 0.70
WHITE = (240, 240, 240)  # max OD = 0.026: background colour
UNIT = "CTRL/rep1"
IDENTITY_PATCH = "morphobricks_preprocessing.he_tissue_mask.code_identities"


def description(
    images: int, *, override: bool | None = None, channels: bool = False, masks: bool = True
) -> bytes:
    """Return a one-unit description of ``images`` images (``rgb``, or ``channels``).

    With ``masks`` it declares the ``Nuclei`` instances mask every image has; the placeholder
    ``{declared}`` is where :func:`declared` adds the tissue mask.
    """
    line = "" if override is None else f"rgb_header_override = {str(override).lower()}\n"
    ids = ("DNA", "ACTIN", "MITO") if channels else ("R", "G", "B")
    kind = "" if channels else 'image_type = "rgb"\n'
    mask = (
        '[masks.Nuclei]\nsource_type = "NucleusMasks"\nrepresentation = "instances"\n'
        if masks
        else ""
    )
    listed = ", ".join(f'"{i}"' for i in ids)
    used = ", ".join(f'"{i}"' for i in sorted(ids))
    declared_channels = "".join(f"[channels.{i}]\n\n" for i in ids)
    text = f"""schema = "morphobricks-experiment"
schema_version = 1

[conditions.CTRL]
source_folder = "CTRL"

{declared_channels}[repeats.rep1]
confirmed = true
confirmed_on = "2026-11-06"

[repeats.rep1.members.CTRL]
source_repeat = "rep1"
image_count = {images}

[repeats.rep1.members.CTRL.technical_repeats.single]
source_folder = "single"
channels = [{listed}]
{kind}{line}
{mask}{{declared}}
[used_ids]
conditions = ["CTRL"]
channels = [{used}]
repeats = ["rep1"]
technical_repeats = ["single"]
"""
    return text.encode("utf-8")


def declared(data: bytes, mask_id: str = "HETissue") -> bytes:
    """Return the description with the tissue mask declared ``binary`` (design §3)."""
    block = (
        f'\n[masks.{mask_id}]\nsource_type = "{mask_id}"\nrepresentation = "binary"\n'
        'source_channels = ["R", "G", "B"]\n'
        f'display_name = "H&E tissue (he_tissue_v1)"\n'
    )
    return data.replace(b"{declared}", block.encode("utf-8"))


def undeclared(data: bytes) -> bytes:
    """Return the description with no tissue mask declared."""
    return data.replace(b"{declared}", b"")


def disc(shape, centre, radius):
    yy, xx = np.mgrid[: shape[0], : shape[1]]
    return (yy - centre[0]) ** 2 + (xx - centre[1]) ** 2 <= radius * radius


def synthetic_he():
    """Return the 96 x 96 image of design §7 and its parts (boolean masks before clean-up).

    An eosin-pink field; a hematoxylin disc (nucleus, radius 5); a saturated red disc (red cell,
    radius 4); a 40 x 40 near-white block (1600 px: background); a 30 x 30 near-white block
    (900 px: kept, under the 1000 px limit); a 3 x 3 hematoxylin speck (9 px: kept).
    """
    shape = (96, 96)
    parts = {
        "nucleus": disc(shape, (14, 14), 5),
        "red_cell": disc(shape, (14, 40), 4),
        "speck": np.zeros(shape, bool),
        "big_white": np.zeros(shape, bool),
        "small_white": np.zeros(shape, bool),
    }
    parts["speck"][30:33, 70:73] = True
    parts["big_white"][50:90, 4:44] = True
    parts["small_white"][55:85, 56:86] = True
    rgb = np.empty((*shape, 3), np.uint8)
    rgb[...] = PINK
    for name, colour in (
        ("nucleus", NUCLEUS),
        ("speck", NUCLEUS),
        ("red_cell", RED_CELL),
        ("big_white", WHITE),
        ("small_white", WHITE),
    ):
        rgb[parts[name]] = colour
    return rgb, parts


def write_rgb(path: Path, rgb: np.ndarray, *, scanner: bool = False) -> None:
    """Write ``(Y, X, 3)`` RGB as a TIFF (``scanner``: three samples under MINISBLACK, RH1)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    photometric = "minisblack" if scanner else "rgb"
    tifffile.imwrite(path, rgb, photometric=photometric, planarconfig="contig")


def rgb_run(
    tmp_path: Path,
    images: dict[str, np.ndarray] | None = None,
    *,
    override: bool | None = None,
    scanner: set[str] = frozenset(),
    data: bytes | None = None,
    masks: bool = True,
) -> RunInputs:
    """Write RGB images in ``CTRL/rep1``, publish the description, run; return the opened run.

    ``images`` maps a file name to its ``(Y, X, 3)`` pixels (default: one synthetic H&E image,
    ``a.tif``). Every image gets a ``Nuclei`` instances mask unless ``masks`` is False (then the
    run has no masks folder).
    """
    images = images or {"a.tif": synthetic_he()[0]}
    experiment = tmp_path / "experiment"
    masks_root = tmp_path / "masks" if masks else None
    for name, rgb in images.items():
        write_rgb(experiment / UNIT / name, rgb, scanner=name in scanner)
        if masks_root is not None:
            labels = np.zeros(rgb.shape[:2], np.uint8)
            labels[1:3, 1:3] = 1
            target = masks_root / "NucleusMasks" / UNIT / name
            target.parent.mkdir(parents=True, exist_ok=True)
            tifffile.imwrite(target, labels, photometric="minisblack", metadata={"axes": "YX"})
    data = data or description(len(images), override=override, masks=masks)
    publish_and_run(tmp_path, experiment, masks_root, prefill_mapping("YXS"), undeclared(data))
    return RunInputs.open(tmp_path / "output")


def verified(name: str) -> dict:
    """Return a verified code identity (for the tests that need a reusable line)."""
    return {
        "distribution": name,
        "version": "1.0",
        "verified": True,
        "record_sha256": "0" * 64,
        "vcs_commit": "1" * 40,
        "reason": None,
    }


def identities(**changes) -> dict:
    """Return two verified code identities, with ``changes`` applied to preprocessing's."""
    return {
        "preprocessing": {**verified("morphobricks-preprocessing"), **changes},
        "normalization": verified("morphobricks-normalization"),
    }


def tree(folder: Path) -> dict:
    """Every file under ``folder`` with its bytes, for "nothing was written" checks."""
    return {
        p.relative_to(folder).as_posix(): p.read_bytes() for p in folder.rglob("*") if p.is_file()
    }


def channels_run(tmp_path: Path) -> RunInputs:
    """A run of one three-channel fluorescence image (a unit not declared rgb), with masks."""
    experiment, masks_root = tmp_path / "experiment", tmp_path / "masks"
    pixels = np.moveaxis(synthetic_he()[0], -1, 0).copy()
    (experiment / UNIT).mkdir(parents=True)
    tifffile.imwrite(
        experiment / UNIT / "a.tif", pixels, photometric="minisblack", metadata={"axes": "CYX"}
    )
    labels = np.zeros(pixels.shape[1:], np.uint8)
    labels[1:3, 1:3] = 1
    (masks_root / "NucleusMasks" / UNIT).mkdir(parents=True)
    tifffile.imwrite(
        masks_root / "NucleusMasks" / UNIT / "a.tif",
        labels,
        photometric="minisblack",
        metadata={"axes": "YX"},
    )
    data = undeclared(description(1, channels=True))
    publish_and_run(tmp_path, experiment, masks_root, prefill_mapping("CYX"), data)
    return RunInputs.open(tmp_path / "output")
