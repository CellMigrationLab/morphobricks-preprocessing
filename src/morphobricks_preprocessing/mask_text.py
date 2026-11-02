"""Plain-language descriptions of the masks found and of the mask step's report (§15).

The command line's ``check`` and ``masks`` print these texts and the notebook shows the
tables (moved from :mod:`.report`), so both tell the user the same thing: what each mask
type looks like, how the types relate, and the hierarchy, as suggestions to check. Nothing
here reads masks or writes files.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from .mask_scan import DEFAULT_THRESHOLDS, MaskReport, PairRelation
from .masks import MaskInventory
from .validation import plural

_MASK_KINDS = {
    "instances": "one object per label",
    "grouped": "each label in several pieces (for example organelles grouped by cell)",
    "one_value": "one value per image (a binary mask or a single object)",
    "empty": "no objects",
}
_RELATIONS = {
    "inside": "inside",
    "grouped_by": "grouped by the labels of",
    "same_objects": "the same objects as",
}


def mask_summary(masks: MaskInventory) -> str:
    """Summarise the masks found: each mask type and how many images have it."""
    if not masks.types:
        return "Masks: none found."
    lines = [
        f"Masks: {plural(len(masks.types), 'mask type')} for {plural(len(masks.images), 'image')}:"
    ]
    for name in masks.types:
        found = [f for f in masks.files if f.mask_type == name]
        sources = " and ".join(sorted({f.source for f in found}))
        lines.append(f"  {name}: {plural(len(found), 'image')} ({sources})")
    return "\n".join(lines)


def mask_report_summary(report: MaskReport) -> str:
    """Describe the mask types, their relationships and the hierarchy in plain words.

    Every statement is a suggestion from the masks' pixels; the text ends by asking the
    user to name the masks and check the relationships.
    """
    lines = [f"Mask types ({plural(report.images, 'image')} scanned):"]
    for name, summary in sorted(report.types.items()):
        lines.append(
            f"  {name}: {_MASK_KINDS[summary.kind]}; {plural(summary.objects, 'object')}, "
            f"median {summary.median_pieces_per_label:g} piece(s) per label"
        )
    relationships = [pair for pair in report.relationships if pair.relation != "same_objects"]
    if relationships:
        lines += ["", "Relationships found:"]
        lines += [_relationship_line(pair) for pair in relationships]
    if report.same_objects:
        lines += ["", "Masks that look like two segmentations of the same objects:"]
        lines += [f"  {first} and {second}" for first, second in report.same_objects]
    lines += ["", "Hierarchy (direct parents only):"]
    lines += _hierarchy_lines(report.hierarchy)
    lines += ["", f"Thresholds: {mask_thresholds_used(report)}"]
    lines += [
        "",
        "These are suggestions from the masks' pixels: name each mask, then check the "
        "relationships before using them.",
    ]
    return "\n".join(lines)


def mask_thresholds_used(report: MaskReport) -> str:
    """Name the thresholds the report was made with: "the defaults", or those changed."""
    changed = [
        f"{name} {value:g}"
        for name, value in report.thresholds.items()
        if value != DEFAULT_THRESHOLDS[name]
    ]
    return ", ".join(changed) + "; the others the defaults" if changed else "the defaults"


def mask_type_rows(report: MaskReport) -> list[list[str]]:
    """Return the mask types as table rows, header first: kind, images, objects, pieces."""
    rows = [["Mask type", "Kind (suggested)", "Images", "Objects", "Median pieces per label"]]
    for name, summary in sorted(report.types.items()):
        kind = _MASK_KINDS[summary.kind]
        numbers = (summary.images, summary.objects)
        rows.append(
            [name, kind, *(f"{n:,}" for n in numbers), f"{summary.median_pieces_per_label:g}"]
        )
    return rows


def mask_relationship_rows(report: MaskReport) -> list[list[str]]:
    """Return the relationships found as table rows, header first, with their evidence.

    Two masks of the same objects are left out, as in :func:`mask_report_summary`: they are
    :attr:`.mask_scan.MaskReport.same_objects`.
    """
    rows = [
        [
            "Mask",
            "Relationship (suggested)",
            "Other mask",
            "Objects inside",
            "Other mask's objects with 0/1/2/3+",
            "Outside the other mask",
            "Split between two of its objects",
        ]
    ]
    for pair in report.relationships:
        if pair.relation == "same_objects":
            continue
        e = pair.evidence
        relation = _RELATIONS[pair.relation] + ("" if pair.direct else " (through another mask)")
        counts = (
            e["parents_with_0_children"],
            e["parents_with_1_child"],
            e["parents_with_2_children"],
            e["parents_with_3_or_more_children"],
        )
        rows.append(
            [
                pair.child,
                relation,
                pair.parent,
                f"{e['inside']:,} of {e['children']:,}",
                "/".join(f"{n:,}" for n in counts),
                f"{e['outside_parents']:,}",
                f"{e['split_between_parents']:,}",
            ]
        )
    return rows


def mask_hierarchy_lines(report: MaskReport) -> list[str]:
    """Return the hierarchy of the mask types (direct parents only) as an indented tree."""
    return _hierarchy_lines(report.hierarchy)


def _relationship_line(pair: PairRelation) -> str:
    """One relationship with its evidence, e.g. how many parents have 0, 1, 2, 3+ children."""
    e = pair.evidence
    text = (
        f"  {pair.child} {_RELATIONS[pair.relation]} {pair.parent}"
        f"{'' if pair.direct else ' (through another mask)'}: "
        f"{e['inside']} of {plural(e['children'], 'object')} inside; "
        f"{pair.parent} with 0/1/2/3+ {pair.child}: {e['parents_with_0_children']}/"
        f"{e['parents_with_1_child']}/{e['parents_with_2_children']}/"
        f"{e['parents_with_3_or_more_children']}"
    )
    extra = []
    if e["outside_parents"]:
        extra.append(f"{e['outside_parents']} outside every {pair.parent}")
    if e["split_between_parents"]:
        extra.append(f"{e['split_between_parents']} split between {pair.parent}")
    return text + (f"; {', '.join(extra)}" if extra else "")


def _hierarchy_lines(hierarchy: Mapping[str, Sequence[str]]) -> list[str]:
    """Return the mask types as an indented tree; a type with two parents shows twice."""
    children: dict[str, list[str]] = {name: [] for name in hierarchy}
    for name, parents in hierarchy.items():
        for parent in parents:
            children[parent].append(name)
    lines: list[str] = []

    def show(name: str, depth: int) -> None:
        lines.append(f"{'    ' * (depth + 1)}{name}")
        for child in sorted(children[name]):
            show(child, depth + 1)

    for name in sorted(n for n, parents in hierarchy.items() if not parents):
        show(name, 0)
    return lines
