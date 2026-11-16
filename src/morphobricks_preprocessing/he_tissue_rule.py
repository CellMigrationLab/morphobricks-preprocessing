"""The fixed H&E tissue rules and their clean-up (Spec ADR 0005 BM15; design he-tissue-mask §1).

A rule is one immutable, canonical record (:func:`rule_record`): its input, the colour maths it
takes from normalization (named, never reimplemented here), its excluded components in order,
each with its pixel comparisons and its clean-up in order, the connectivity, the disk, how the
components combine, and its ``status``. :func:`tissue_components` computes a rule **from its
record**, so the record written to the lineage is exactly what ran.

``he_tissue_v1`` (status ``"provisional"`` until its acceptance report is accepted) excludes:

- nuclei: ``H >= 0.05``; pieces under 15 px removed, then dilated by a disk of radius 2;
- red blood cells: ``E >= 0.06``, ``H < 0.03``, ``OD_G - OD_R >= 0.40`` and
  ``OD_B - OD_R >= 0.10``; pieces under 15 px removed, then dilated by a disk of radius 1;
- background: ``max(OD_R, OD_G, OD_B) < 0.08``; opened by a disk of radius 2, then pieces under
  1000 px removed (only lumens, fat and tears; not the gaps between fibres).

``keep = not (nuclei or red_cells or background)``. ``E`` and ``H`` are normalization's
``to_scalar(..., preset="he_eosin")`` ``image`` and ``exclusion`` (float32), ``OD`` its
``optical_density`` (float64, ``(Y, X, 3)``). Every comparison is made in float64 on the values
as normalization returns them, as written (``>=`` or ``<``), with no rounding and no tolerance;
the threshold is never rounded to float32. Sizes are pixels: nothing reads a pixel size.

The clean-up uses SciPy only. Pieces are 8-connected; "pieces under N px" removes each piece of
**fewer than** N pixels (a piece of exactly N is kept). A disk of radius ``r`` is
``x*x + y*y <= r*r``. The structuring element is clipped by the image border, never padded or
wrapped: a dilation adds nothing from outside the image, and an erosion (in the opening) takes
only the pixels inside the image into account, so a region touching the border is not eaten
from the border side.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np
from scipy import ndimage

PROVISIONAL = "provisional"
FROZEN = "frozen"
STATUSES = (PROVISIONAL, FROZEN)
OPERATION = "he_tissue_mask"
"""The lineage ``operation`` of every ``he_tissue`` rule (Spec ADR 0005 BM15)."""
HE_TISSUE_V1 = "he_tissue_v1"
CONNECTIVITY = 8
DISK = "x*x + y*y <= r*r"
COMPONENT_NAMES = ("nuclei", "red_cells", "background")
QUANTITIES = ("E", "H", "OD_G-OD_R", "OD_B-OD_R", "max(OD_R,OD_G,OD_B)")
OPERATORS = (">=", "<")
CLEANUPS = ("remove_pieces_smaller_than_px", "dilate_disk_radius_px", "open_disk_radius_px")

_V1 = {
    "id": "he_tissue",
    "version": 1,
    "status": PROVISIONAL,
    "input": {"image_type": "rgb", "dtype": "uint8", "white": [255, 255, 255]},
    "colour": {
        "stain_preset": "he_eosin",
        "stain_preset_version": 1,
        "hed_matrix": {"source": None, "sha256": None},
        "E": "to_scalar(...).image (separate_stains component 1)",
        "H": "to_scalar(...).exclusion (separate_stains component 0)",
        "optical_density": {
            "function": "morphobricks_normalization.optical_density.optical_density",
            "floor": 1,
            "white": [255, 255, 255],
        },
    },
    "connectivity": CONNECTIVITY,
    "disk": DISK,
    "components": [
        {
            "name": "nuclei",
            "pixel": [["H", ">=", 0.05]],
            "cleanup": [["remove_pieces_smaller_than_px", 15], ["dilate_disk_radius_px", 2]],
        },
        {
            "name": "red_cells",
            "pixel": [
                ["E", ">=", 0.06],
                ["H", "<", 0.03],
                ["OD_G-OD_R", ">=", 0.40],
                ["OD_B-OD_R", ">=", 0.10],
            ],
            "cleanup": [["remove_pieces_smaller_than_px", 15], ["dilate_disk_radius_px", 1]],
        },
        {
            "name": "background",
            "pixel": [["max(OD_R,OD_G,OD_B)", "<", 0.08]],
            "cleanup": [["open_disk_radius_px", 2], ["remove_pieces_smaller_than_px", 1000]],
        },
    ],
    "combine": "keep = not (nuclei or red_cells or background)",
}

RULES: Mapping[str, str] = {HE_TISSUE_V1: PROVISIONAL}
"""The known rule IDs and the ``status`` each is offered with (v1 is provisional)."""


def rule_record(rule_id: str, *, hed_source: str, hed_sha256: str) -> dict:
    """Return the canonical record of a known rule, with the HED matrix's source and digest.

    Parameters
    ----------
    rule_id
        A key of :data:`RULES`.
    hed_source
        The qualified name of the matrix normalization's ``he_eosin`` preset deconvolves with
        (``"skimage.color.hed_from_rgb"``).
    hed_sha256
        The SHA-256 of that matrix as imported (float64, little-endian, C order).

    Raises
    ------
    ValueError
        For an unknown rule ID (the message lists the known ones) or status.
    """
    if rule_id not in RULES:
        known = ", ".join(sorted(RULES))
        raise ValueError(f"The rule {rule_id!r} is not known; the known rules are {known}")
    if RULES[rule_id] not in STATUSES:
        raise ValueError(f"The rule {rule_id!r} has the unknown status {RULES[rule_id]!r}")
    record = copy.deepcopy(_V1)
    record["status"] = RULES[rule_id]
    record["colour"]["hed_matrix"] = {"source": hed_source, "sha256": hed_sha256}
    return record


@dataclass(frozen=True)
class TissueComponents:
    """The excluded components of one plane after their clean-up, and the pixels kept.

    Every array is boolean ``(Y, X)``; ``True`` means "in the component" (excluded), and in
    ``keep`` "kept". The components may overlap.
    """

    nuclei: np.ndarray
    red_cells: np.ndarray
    background: np.ndarray
    keep: np.ndarray

    def fractions(self) -> dict[str, float]:
        """Return the share of all pixels kept and in each component (full resolution)."""
        return {
            "kept": float(np.mean(self.keep)),
            "nuclei": float(np.mean(self.nuclei)),
            "red_cells": float(np.mean(self.red_cells)),
            "background": float(np.mean(self.background)),
        }


def disk(radius: int) -> np.ndarray:
    """Return the boolean disk ``x*x + y*y <= r*r`` of side ``2 r + 1``."""
    yy, xx = np.mgrid[-radius : radius + 1, -radius : radius + 1]
    return xx * xx + yy * yy <= radius * radius


def remove_pieces_smaller_than(mask: np.ndarray, size: int) -> np.ndarray:
    """Return ``mask`` without its 8-connected pieces of fewer than ``size`` pixels."""
    labels, count = ndimage.label(mask, structure=np.ones((3, 3), bool))
    if count == 0:
        return np.zeros(mask.shape, bool)
    sizes = np.bincount(labels.ravel(), minlength=count + 1)
    kept = sizes >= size
    kept[0] = False
    return kept[labels]


def dilate(mask: np.ndarray, radius: int) -> np.ndarray:
    """Return ``mask`` dilated by the disk of ``radius``; nothing comes from outside the image."""
    return ndimage.binary_dilation(mask, structure=disk(radius), border_value=0)


def open_disk(mask: np.ndarray, radius: int) -> np.ndarray:
    """Return ``mask`` opened by the disk of ``radius``, the disk clipped by the image border.

    The erosion takes only the pixels inside the image into account (outside counts as in the
    mask, which a minimum ignores), the dilation adds nothing from outside the image.
    """
    eroded = ndimage.binary_erosion(mask, structure=disk(radius), border_value=1)
    return ndimage.binary_dilation(eroded, structure=disk(radius), border_value=0)


def quantities(E: np.ndarray, H: np.ndarray, od: np.ndarray) -> dict[str, np.ndarray]:
    """Return each quantity a rule compares, in float64, from normalization's arrays.

    ``E`` and ``H`` are widened exactly (float32 to float64); the OD differences are computed in
    float64 from ``od[..., 1] - od[..., 0]`` and ``od[..., 2] - od[..., 0]``.
    """
    return {
        "E": E.astype(np.float64),
        "H": H.astype(np.float64),
        "OD_G-OD_R": od[..., 1] - od[..., 0],
        "OD_B-OD_R": od[..., 2] - od[..., 0],
        "max(OD_R,OD_G,OD_B)": od.max(axis=-1),
    }


def pixel_components(
    E: np.ndarray,
    H: np.ndarray,
    od: np.ndarray,
    rule: Mapping[str, object],
) -> dict[str, np.ndarray]:
    """Return each component's pixel rule (every comparison joined by "and"), before clean-up.

    Raises
    ------
    ValueError
        When the arrays do not have the shapes and dtypes normalization returns, or the record
        names a quantity or operator this module does not know.
    """
    _check_arrays(E, H, od)
    values = quantities(E, H, od)
    found = {}
    for component in rule["components"]:
        inside = np.ones(E.shape, bool)
        for quantity, operator, threshold in component["pixel"]:
            if quantity not in QUANTITIES or operator not in OPERATORS:
                raise ValueError(f"unknown comparison {quantity} {operator} in the rule record")
            limit = np.float64(threshold)  # a float64 scalar keeps the comparison in float64
            inside &= values[quantity] >= limit if operator == ">=" else values[quantity] < limit
        found[component["name"]] = inside
    return found


def clean_up(mask: np.ndarray, steps: list) -> np.ndarray:
    """Apply a component's clean-up steps to its pixel mask, in their order.

    Raises
    ------
    ValueError
        For a step this module does not know.
    """
    for name, value in steps:
        if name == "remove_pieces_smaller_than_px":
            mask = remove_pieces_smaller_than(mask, int(value))
        elif name == "dilate_disk_radius_px":
            mask = dilate(mask, int(value))
        elif name == "open_disk_radius_px":
            mask = open_disk(mask, int(value))
        else:
            raise ValueError(f"unknown clean-up step {name!r} in the rule record")
    return mask


def tissue_components(
    E: np.ndarray,
    H: np.ndarray,
    od: np.ndarray,
    rule: Mapping[str, object],
) -> TissueComponents:
    """Return the components of one plane after clean-up, and ``keep``, from a rule record.

    Parameters
    ----------
    E, H
        normalization's eosin and hematoxylin maps, float32 ``(Y, X)``.
    od
        normalization's optical density, float64 ``(Y, X, 3)``.
    rule
        The rule's canonical record (:func:`rule_record`).

    Returns
    -------
    TissueComponents
        Boolean ``(Y, X)`` arrays.

    Raises
    ------
    ValueError
        As :func:`pixel_components` and :func:`clean_up`, or a record whose components are not
        :data:`COMPONENT_NAMES` in that order.
    """
    names = tuple(component["name"] for component in rule["components"])
    if names != COMPONENT_NAMES:
        raise ValueError(f"the rule record's components are {names}, not {COMPONENT_NAMES}")
    pixels = pixel_components(E, H, od, rule)
    cleaned = {
        component["name"]: clean_up(pixels[component["name"]], component["cleanup"])
        for component in rule["components"]
    }
    keep = ~(cleaned["nuclei"] | cleaned["red_cells"] | cleaned["background"])
    return TissueComponents(cleaned["nuclei"], cleaned["red_cells"], cleaned["background"], keep)


def _check_arrays(E: np.ndarray, H: np.ndarray, od: np.ndarray) -> None:
    """Refuse arrays that are not normalization's ``E``, ``H`` and ``OD`` of one plane."""
    if E.dtype != np.float32 or H.dtype != np.float32 or E.ndim != 2 or H.shape != E.shape:
        raise ValueError("E and H must be float32 (Y, X) maps of the same shape")
    if od.dtype != np.float64 or od.shape != (*E.shape, 3):
        raise ValueError("od must be a float64 (Y, X, 3) array of the plane of E and H")
