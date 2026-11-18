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


PI_STYLE = {
    # name: (seed, nuclei, red cells, lumens): dense tissue like PI-1 and PI-2, sparse like PI-3
    # and PI-4 (the presentation images themselves are not in this repository)
    "pi1": (1, 60, 6, 1),
    "pi2": (2, 50, 20, 2),
    "pi3": (3, 6, 2, 1),
    "pi4": (4, 4, 1, 3),
}


def pi_style(name: str, shape: tuple[int, int] = (160, 160)) -> np.ndarray:
    """Return a seeded H&E-like ``(Y, X, 3)`` image in the manner of PI-1 to PI-4.

    Noisy eosin stroma with paler fibre gaps, hematoxylin nuclei of varied size and shade, red
    cells, near-white lumens (some over 1000 px) and per-pixel noise, so pixels fall on both
    sides of every threshold. Not a model of real tissue: only inputs for equality tests.
    """
    seed, nuclei, red_cells, lumens = PI_STYLE[name]
    rng = np.random.default_rng(seed)
    rgb = np.empty((*shape, 3), np.float64)
    rgb[...] = PINK
    rows = (np.arange(shape[0]) + rng.integers(0, 7)) % 7 == 0
    rgb[rows] += (8, 50, 25)  # paler fibre gaps
    for count, colour, radii in (
        (lumens, (238, 236, 240), (14, 24)),
        (nuclei, NUCLEUS, (2, 6)),
        (red_cells, RED_CELL, (2, 4)),
    ):
        for _ in range(count):
            centre = tuple(int(rng.integers(0, n)) for n in shape)
            radius = int(rng.integers(*radii))
            shade = rng.normal(0, 12, 3)
            rgb[disc(shape, centre, radius)] = np.add(colour, shade)
    rgb += rng.normal(0, 9, rgb.shape)
    return np.clip(np.rint(rgb), 0, 255).astype(np.uint8)


def boundary_image() -> np.ndarray:
    """Return a ``(Y, X, 3)`` image of design §1's boundary colours and uniform random pixels."""
    colours = np.array(
        [
            (253, 101, 101), (253, 100, 100), (211, 84, 84), (211, 85, 85), (200, 79, 158),
            (200, 79, 159), (141, 50, 112), (141, 50, 113), (0, 0, 0), (3, 0, 0), (2, 0, 0),
            (255, 0, 0), (213, 213, 213), (212, 213, 213), (255, 255, 255), (255, 20, 147),
        ],
        np.uint8,
    )  # fmt: skip
    rgb = np.random.default_rng(5).integers(0, 256, (64, 64, 3), dtype=np.uint8)
    for index, colour in enumerate(colours):
        rgb[4 * (index // 4) : 4 * (index // 4) + 4, 4 * (index % 4) : 4 * (index % 4) + 4] = colour
    return rgb


# The 8-bit colours nearest each pixel threshold of he_tissue_v1, on each side (found by an
# exhaustive search of all 2**24 colours with normalization's E, H and OD; issue #345). Each is
# in no other component, so moving that one threshold past the colour flips the mask.
ON_THRESHOLD = {
    # name: (in the component, out of it), with the value compared in float64
    "nuclei H >= 0.05": ((176, 243, 254), (157, 51, 211)),  # H 0.0500000082 / 0.0499999970
    "red E >= 0.06": ((243, 94, 150), (242, 67, 68)),  # E 0.0600000918 / 0.0599999353
    "red H < 0.03": ((87, 11, 25), (136, 9, 103)),  # H 0.0299998578 / 0.0300001409
    "red OD_G-OD_R >= 0.40": ((211, 84, 155), (216, 86, 156)),  # 0.4000031692 / 0.3999553
    "red OD_B-OD_R >= 0.10": ((141, 0, 112), (248, 0, 197)),  # 0.1000010900 / 0.0999854547
    "background max OD < 0.08": ((213, 213, 213), (212, 212, 212)),  # 0.0781605770 / 0.0802043
}
"""No 8-bit colour has a max OD in (0.07816, 0.08020): the background threshold can move
anywhere in that gap without changing any mask (0.08 to 0.0801, say); the record's pinned values
(``test_he_tissue_rule.py``) catch such a change."""


def _disk(radius):
    yy, xx = np.mgrid[-radius : radius + 1, -radius : radius + 1]
    return xx * xx + yy * yy <= radius * radius


def _grown(core, radius):
    """Return ``core`` dilated by the disk of ``radius`` (plain loops: an opening keeps it)."""
    out = np.zeros_like(core)
    for y, x in zip(*np.nonzero(core), strict=True):
        for dy, dx in zip(*np.nonzero(_disk(radius)), strict=True):
            out[y + dy - radius, x + dx - radius] = True
    return out


def _region(shape, top, left, size):
    """Return a region of exactly ``size`` px (980 to 1008) that an opening by disk(2) keeps."""
    core = np.zeros(shape, bool)
    core[top + 2 : top + 29, left + 2 : left + 30] = True
    core[top + 1, left + 4 : left + 4 + size - int(_grown(core, 2).sum())] = True
    return _grown(core, 2)


def threshold_image():
    """Return a 100 x 220 image whose parts sit on every threshold of ``he_tissue_v1``.

    Not square, so a Y/X swap changes the mask's shape. On uniform pink stroma (kept):

    - for each pixel threshold, a 6 x 6 block of the colour just inside and one just outside
      (:data:`ON_THRESHOLD`; the background pair as regions of 1144 px that the opening by
      disk(2) keeps, over the 1000 px minimum);
    - one-pixel-wide nucleus and red-cell bars of 14 and 15 px (pieces under 15 px removed);
    - white regions of exactly 999 and 1000 px that the opening by disk(2) keeps (background
      pieces under 1000 px removed).

    Returns the image and its parts (boolean masks before clean-up), named
    ``"<threshold> in"``/``"<threshold> out"``, ``"nuclei 14"`` ... and ``"background 999"``.
    """
    shape = (100, 220)
    rgb = np.empty((*shape, 3), np.uint8)
    rgb[...] = PINK
    parts = {}

    def put(name, rows, cols):
        part = np.zeros(shape, bool)
        part[rows, cols] = True
        parts[name] = part

    for index, name in enumerate(ON_THRESHOLD):
        if name.startswith("background"):
            for side, left in (("in", 4), ("out", 46)):  # 1144 px each, kept by the opening
                core = np.zeros(shape, bool)
                core[36:66, left + 2 : left + 32] = True
                parts[f"{name} {side}"] = _grown(core, 2)
            continue
        for side, offset in (("in", 0), ("out", 12)):
            left = 4 + 24 * index + offset
            put(f"{name} {side}", slice(4, 10), slice(left, left + 6))
    for row, name in ((18, "nuclei"), (24, "red_cells")):
        put(f"{name} 14", row, slice(4, 18))
        put(f"{name} 15", row, slice(30, 45))
    for size, left in ((999, 96), (1000, 150)):
        parts[f"background {size}"] = _region(shape, 34, left, size)
    colours = {
        f"{n} {s}": c
        for n, pair in ON_THRESHOLD.items()
        for s, c in zip(("in", "out"), pair, strict=True)
    }
    colours.update({"nuclei 14": NUCLEUS, "nuclei 15": NUCLEUS, "red_cells 14": RED_CELL})
    colours.update({"red_cells 15": RED_CELL, "background 999": WHITE, "background 1000": WHITE})
    for name, part in parts.items():
        rgb[part] = colours[name]
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


def unverified() -> dict:
    """Return the identities of an editable checkout (never verified), set explicitly so a test
    gives the same result in an editable and a non-editable install."""
    return identities(
        verified=False, record_sha256=None, vcs_commit=None, reason="editable install"
    )


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
