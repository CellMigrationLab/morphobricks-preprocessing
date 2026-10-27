"""Describe every mask type and how the masks relate to each other (``docs/contract.md`` §15).

The optional mask step: it reads the masks found by :func:`.masks.find_masks`, one image at a
time, and summarises each mask type and every ordered pair of types over the whole
experiment, with the evidence (counts) behind each conclusion:

- **Kind of mask:** ``instances`` (each label is one object, in at most two pieces: a median
  over all objects), ``grouped`` (each label is spread over more than two pieces, as in a
  per-cell mask of mitochondria whose pixels carry their cell's label), ``one_value`` (every
  image's mask has a single foreground value: a binary mask or one object, which only the user
  can tell apart) or ``empty``.
- **Relationship of a child type to a parent type:**

  - ``same_objects``: one-to-one matches at intersection over union >= 0.5 for at least 90% of
    the objects of both types (two segmentations of the same objects);
  - ``grouped_by``: the child is ``grouped``, at least 99% of its pixels carry the label of the
    parent object under them, and it is inside as below;
  - ``inside``: at least 90% of the child objects have at least half of their pixels in one
    parent object, which is larger than the child for at least 90% of them;
  - ``none`` otherwise.

  Several children per parent are counted (parents with 0, 1, 2 and 3 or more children), as
  are children outside every parent and children split between parents.
- **Hierarchy:** the direct parents of each type: a parent is left out when it is the parent
  of another parent (nucleoli inside nuclei inside cells: the nucleoli's direct parent is the
  nuclei). A type may have several direct parents (cells inside an organoid and inside a
  region of the slide).

The numbers above are the default thresholds (:data:`DEFAULT_THRESHOLDS`); each can be changed
(``scan_masks(..., thresholds=...)``, or the ``masks`` command's options), and the values used
are written in the report.

Nothing is decided for the user: the report is a suggestion with its evidence; the user names
the masks and confirms or corrects the relationships. Pieces are counted with 4-connectivity
(``scipy.ndimage.label``).
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

import numpy as np
from scipy import ndimage

from .manifest import package_versions
from .masks import MaskInventory, read_mask

logger = logging.getLogger(__name__)

MASK_REPORT_FILENAME = "mask_relationships.json"
"""Name of the report written to the output folder."""

MASK_REPORT_VERSION = "1"
"""Version of the report's format."""

DEFAULT_THRESHOLDS = MappingProxyType(
    {
        "inside_pixel_share": 0.5,
        "inside_object_share": 0.9,
        "parent_larger_share": 0.9,
        "same_objects_iou": 0.5,
        "same_objects_share": 0.9,
        "grouped_label_share": 0.99,
        "grouped_median_pieces": 2.0,
    }
)
"""Default thresholds of the kinds and relationships (contract §15.1); each may be changed
(:func:`mask_thresholds`), and the values used are written in every report."""

_SHARES = frozenset(DEFAULT_THRESHOLDS) - {"grouped_median_pieces"}

_COUNTS = (
    "children",
    "parents",
    "inside",
    "inside_parent_larger",
    "split_between_parents",
    "outside_parents",
    "parents_with_0_children",
    "parents_with_1_child",
    "parents_with_2_children",
    "parents_with_3_or_more_children",
    "child_pixels",
    "child_pixels_with_parent_label",
    "children_matched_one_to_one",
    "parents_matched_one_to_one",
)


@dataclass(frozen=True)
class MaskTypeSummary:
    """One mask type over the experiment.

    Attributes
    ----------
    name
        The mask type.
    kind
        ``instances``, ``grouped``, ``one_value`` or ``empty`` (module docstring).
    images
        Images with a mask of this type.
    objects
        Distinct labels, summed over the images.
    median_pieces_per_label
        Median number of 4-connected pieces per label over all objects; 0 when empty.
    """

    name: str
    kind: str
    images: int
    objects: int
    median_pieces_per_label: float


@dataclass(frozen=True)
class PairRelation:
    """How one mask type (the child) sits relative to another (the parent).

    Attributes
    ----------
    child, parent
        The two mask types.
    relation
        ``inside``, ``grouped_by``, ``same_objects`` or ``none`` (module docstring).
    direct
        True for a relationship kept in the hierarchy (not implied by another parent).
    evidence
        The counts behind the relation, summed over the images with both masks.
    """

    child: str
    parent: str
    relation: str
    direct: bool
    evidence: Mapping[str, int]


@dataclass(frozen=True)
class MaskReport:
    """The kinds of the mask types and their relationships, with the evidence.

    Attributes
    ----------
    images
        Images scanned (those with at least one mask).
    types
        One summary per mask type, by name.
    pairs
        Every ordered pair of types seen together in at least one image, sorted.
    thresholds
        The thresholds the kinds and relations were decided with.
    """

    images: int
    types: Mapping[str, MaskTypeSummary] = field(default_factory=lambda: MappingProxyType({}))
    pairs: tuple[PairRelation, ...] = ()
    thresholds: Mapping[str, float] = field(default_factory=lambda: DEFAULT_THRESHOLDS)

    @property
    def relationships(self) -> tuple[PairRelation, ...]:
        """The pairs with a relation other than ``none``."""
        return tuple(pair for pair in self.pairs if pair.relation != "none")

    @property
    def hierarchy(self) -> Mapping[str, tuple[str, ...]]:
        """Each mask type's direct parents (empty for a type without a parent)."""
        parents = {name: [] for name in self.types}
        for pair in self.pairs:
            if pair.direct:
                parents[pair.child].append(pair.parent)
        return MappingProxyType({name: tuple(sorted(found)) for name, found in parents.items()})

    @property
    def same_objects(self) -> tuple[tuple[str, str], ...]:
        """Pairs of types that segment the same objects, each pair once."""
        found = {
            tuple(sorted((pair.child, pair.parent)))
            for pair in self.pairs
            if pair.relation == "same_objects"
        }
        return tuple(sorted(found))

    def to_dict(self) -> dict:
        """Return the report as JSON-compatible data (contract §15)."""
        return {
            "report_version": MASK_REPORT_VERSION,
            "thresholds": dict(self.thresholds),
            "images": self.images,
            "masks": {
                name: {
                    "kind": summary.kind,
                    "images": summary.images,
                    "objects": summary.objects,
                    "median_pieces_per_label": summary.median_pieces_per_label,
                }
                for name, summary in sorted(self.types.items())
            },
            "hierarchy": {name: list(parents) for name, parents in self.hierarchy.items()},
            "same_objects": [list(pair) for pair in self.same_objects],
            "relationships": [_pair_dict(pair) for pair in self.relationships],
            "pairs": [_pair_dict(pair) for pair in self.pairs],
            "versions": package_versions(),
        }


def mask_thresholds(changes: Mapping[str, float] | None = None) -> Mapping[str, float]:
    """Return the default thresholds with some replaced, after checking them.

    Parameters
    ----------
    changes
        New values by name (:data:`DEFAULT_THRESHOLDS`); the others keep their default.

    Raises
    ------
    ValueError
        For an unknown name, a share or intersection over union outside (0, 1], or a median
        number of pieces below 1.
    """
    thresholds = dict(DEFAULT_THRESHOLDS)
    for name, value in (changes or {}).items():
        if name not in thresholds:
            known = ", ".join(DEFAULT_THRESHOLDS)
            raise ValueError(f"Unknown mask threshold {name!r}; the thresholds are {known}.")
        value = float(value)
        if name in _SHARES and not 0 < value <= 1:
            raise ValueError(f"The mask threshold {name} must be above 0 and at most 1.")
        if name not in _SHARES and not value >= 1:
            raise ValueError(f"The mask threshold {name} must be at least 1.")
        thresholds[name] = value
    return MappingProxyType(thresholds)


def piece_counts(mask: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Return the labels of a 2D mask and the number of 4-connected pieces of each.

    Two pixels are in one piece when they carry the same label and touch along an edge.

    Returns
    -------
    tuple of numpy.ndarray
        The sorted nonzero labels, and the number of pieces of each.
    """
    values, dense = np.unique(mask, return_inverse=True)
    dense = dense.reshape(mask.shape)
    if values.size and values[0] == 0:
        values = values[1:]
    else:
        dense = dense + 1  # no background: every value is a label
    counts = np.zeros(values.size, dtype=np.int64)
    for index, box in enumerate(ndimage.find_objects(dense)):
        if box is not None:
            counts[index] = ndimage.label(dense[box] == index + 1)[1]
    return values, counts


def pair_counts(
    child: np.ndarray, parent: np.ndarray, *, thresholds: Mapping[str, float] | None = None
) -> dict[str, int]:
    """Return the counts relating the objects of one mask (child) to another (parent).

    Both are 2D label images of one position; label 0 is background. ``thresholds`` changes
    the default ``inside_pixel_share`` and ``same_objects_iou`` (:func:`mask_thresholds`).
    """
    limits = mask_thresholds(thresholds)
    counts = dict.fromkeys(_COUNTS, 0)
    on = child != 0
    child_labels, child_of = np.unique(child[on], return_inverse=True)
    parent_labels, parent_sizes = np.unique(parent[parent != 0], return_counts=True)
    counts["children"], counts["parents"] = child_labels.size, parent_labels.size
    if child_labels.size == 0:
        counts["parents_with_0_children"] = parent_labels.size
        return counts
    child_sizes = np.bincount(child_of, minlength=child_labels.size)
    under = parent[on]
    counts["child_pixels"] = int(on.sum())
    counts["child_pixels_with_parent_label"] = int(np.count_nonzero(child[on] == under))
    covered = under != 0
    slots = parent_labels.size
    pairs, shared = np.unique(
        child_of[covered] * slots + np.searchsorted(parent_labels, under[covered]),
        return_counts=True,
    )
    kid, host = pairs // slots, pairs % slots
    if kid.size == 0:  # no child pixel lies on a parent
        counts["outside_parents"] = child_labels.size
        counts["parents_with_0_children"] = slots
        return counts
    union = child_sizes[kid] + parent_sizes[host] - shared
    matched = shared / union >= limits["same_objects_iou"]
    counts["children_matched_one_to_one"] = int(np.unique(kid[matched]).size)
    counts["parents_matched_one_to_one"] = int(np.unique(host[matched]).size)
    best = np.lexsort((-shared, kid))
    lead = np.r_[True, kid[best][1:] != kid[best][:-1]]
    best_kid, best_host, best_shared = kid[best][lead], host[best][lead], shared[best][lead]
    inside = best_shared / child_sizes[best_kid] >= limits["inside_pixel_share"]
    counts["inside"] = int(inside.sum())
    counts["inside_parent_larger"] = int(
        np.count_nonzero(parent_sizes[best_host[inside]] > child_sizes[best_kid[inside]])
    )
    counts["split_between_parents"] = int(np.count_nonzero(~inside))
    counts["outside_parents"] = child_labels.size - best_kid.size
    per_parent = np.bincount(best_host[inside], minlength=slots)
    counts["parents_with_0_children"] = int(np.count_nonzero(per_parent == 0))
    counts["parents_with_1_child"] = int(np.count_nonzero(per_parent == 1))
    counts["parents_with_2_children"] = int(np.count_nonzero(per_parent == 2))
    counts["parents_with_3_or_more_children"] = int(np.count_nonzero(per_parent >= 3))
    return counts


def scan_masks(
    inventory: MaskInventory,
    experiment_root: str | os.PathLike,
    *,
    progress: Callable[[int, int, str], None] | None = None,
    thresholds: Mapping[str, float] | None = None,
) -> MaskReport:
    """Read every image's masks and describe the mask types and their relationships.

    Parameters
    ----------
    inventory
        The masks found by :func:`.masks.find_masks`; it should have no errors.
    experiment_root
        The experiment folder (for OME-Zarr label images).
    progress
        Called as ``progress(done, total, image)`` before each image is read.
    thresholds
        Thresholds to change from their defaults, by name (:func:`mask_thresholds`).

    Returns
    -------
    MaskReport

    Raises
    ------
    ValueError
        For an invalid threshold, before any mask is read.
    """
    limits = mask_thresholds(thresholds)
    root = Path(experiment_root).resolve()
    images = inventory.images
    pieces: dict[str, list[np.ndarray]] = {name: [] for name in inventory.types}
    single: dict[str, list[bool]] = {name: [] for name in inventory.types}
    totals: dict[tuple[str, str], dict[str, int]] = {}
    for done, image in enumerate(images):
        if progress is not None:
            progress(done, len(images), image)
        masks = {
            name: read_mask(mask, inventory.folder, root)
            for name, mask in inventory.for_image(image).items()
        }
        for name, pixels in masks.items():
            labels, counts = piece_counts(pixels)
            pieces[name].append(counts)
            single[name].append(labels.size <= 1)
        for child in masks:
            for parent in masks:
                if child != parent:
                    found = pair_counts(masks[child], masks[parent], thresholds=limits)
                    total = totals.setdefault((child, parent), dict.fromkeys(_COUNTS, 0))
                    for key, value in found.items():
                        total[key] += value
    if progress is not None:
        progress(len(images), len(images), "")
    types = {name: _summary(name, pieces[name], single[name], limits) for name in inventory.types}
    return MaskReport(
        images=len(images),
        types=MappingProxyType(types),
        pairs=_relations(types, totals, limits),
        thresholds=limits,
    )


def write_mask_report(report: MaskReport, path: str | os.PathLike) -> Path:
    """Write the report as UTF-8 JSON (two-space indent, LF), atomically; return the path."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n"
    handle, temporary = tempfile.mkstemp(prefix=".mask_report-", dir=target.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)
    return target


def _summary(
    name: str, pieces: list[np.ndarray], single: list[bool], limits: Mapping[str, float]
) -> MaskTypeSummary:
    """Return a mask type's summary from its pieces per label in every image."""
    every = np.concatenate(pieces) if pieces else np.zeros(0, dtype=np.int64)
    median = float(np.median(every)) if every.size else 0.0
    if every.size == 0:
        kind = "empty"
    elif all(single):
        kind = "one_value"
    elif median > limits["grouped_median_pieces"]:
        kind = "grouped"
    else:
        kind = "instances"
    return MaskTypeSummary(name, kind, len(pieces), int(every.size), median)


def _relation(
    child: MaskTypeSummary, counts: Mapping[str, int], limits: Mapping[str, float]
) -> str:
    """Return the relation of a child type to a parent type from their counts."""
    children, parents = counts["children"], counts["parents"]
    if not children or not parents:
        return "none"
    share = limits["same_objects_share"]
    if (
        counts["children_matched_one_to_one"] >= share * children
        and counts["parents_matched_one_to_one"] >= share * parents
    ):
        return "same_objects"
    inside = counts["inside"] >= limits["inside_object_share"] * children
    if (
        child.kind == "grouped"
        and inside
        and counts["child_pixels_with_parent_label"]
        >= limits["grouped_label_share"] * counts["child_pixels"]
    ):
        return "grouped_by"
    larger = counts["inside_parent_larger"] >= limits["parent_larger_share"] * counts["inside"]
    return "inside" if inside and larger else "none"


def _relations(
    types: Mapping[str, MaskTypeSummary],
    totals: Mapping[tuple[str, str], Mapping[str, int]],
    limits: Mapping[str, float],
) -> tuple[PairRelation, ...]:
    """Return every pair's relation, marking the direct ones (module docstring)."""
    found = {
        (child, parent): _relation(types[child], counts, limits)
        for (child, parent), counts in totals.items()
    }
    nested = {pair for pair, relation in found.items() if relation in ("inside", "grouped_by")}
    pairs = []
    for (child, parent), relation in sorted(found.items()):
        between = any((child, other) in nested and (other, parent) in nested for other in types)
        direct = (child, parent) in nested and not between
        evidence = MappingProxyType(dict(totals[(child, parent)]))
        pairs.append(PairRelation(child, parent, relation, direct, evidence))
    return tuple(pairs)


def _pair_dict(pair: PairRelation) -> dict:
    return {
        "child": pair.child,
        "parent": pair.parent,
        "relation": pair.relation,
        "direct": pair.direct,
        "evidence": dict(pair.evidence),
    }
