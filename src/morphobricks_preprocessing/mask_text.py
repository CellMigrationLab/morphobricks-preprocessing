"""Plain-language descriptions of the masks found and of the mask step's report (§15).

The command line's ``check`` and ``masks`` print these texts and the notebook shows the
tables (moved from :mod:`.report`), so both tell the user the same thing: what each mask
type looks like, how the types relate, and the hierarchy, as suggestions to check. Nothing
here reads masks or writes files.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from .mask_scan import (
    DEFAULT_THRESHOLDS,
    MaskReport,
    PairRelation,
    hierarchy_cycles,
)
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
    ambiguous = mask_ambiguous_lines(report)
    if ambiguous:
        lines += ["", "Objects given no parent (equally in two or more objects of the other mask):"]
        lines += [f"  {line}" for line in ambiguous]
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


NOT_DESCRIBED = "not described yet"
"""What the declared-kind column says for a mask type the description does not declare."""


def _mask_cell(name: str, ids: Mapping[str, str] | None) -> str:
    """Return the mask's name: its ID, with the folder name only when that differs."""
    mask_id = None if ids is None else ids.get(name)
    return name if mask_id is None else mask_id if mask_id == name else f"{mask_id} ({name})"


def mask_type_rows(
    report: MaskReport,
    ids: Mapping[str, str] | None = None,
    declared: Mapping[str, str] | None = None,
) -> list[list[str]]:
    """Return the mask types as table rows, header first: kind, images, objects, pieces.

    The first column, *Mask*, is the mask's ID when ``ids`` (mask type -> mask ID, from
    *Your masks* or the published description) gives one, followed by the folder name in
    brackets only when the two differ; it is one column, not a type and an ID that repeat
    each other. With ``declared`` (mask type -> ``objects`` or ``regions``, what the user
    answered) a *Declared kind* column states that answer beside the suggested kind, which
    stays a suggestion; a type the user has not answered says :data:`NOT_DESCRIBED`.
    """
    shown = declared is not None
    header = ["Mask", *(["Declared kind"] if shown else []), "Kind (suggested)"]
    rows = [[*header, "Images", "Objects", "Median pieces per label"]]
    for name, summary in sorted(report.types.items()):
        answer = [declared.get(name, NOT_DESCRIBED)] if declared is not None else []
        rows.append(
            [_mask_cell(name, ids), *answer, _MASK_KINDS[summary.kind]]
            + [f"{summary.images:,}", f"{summary.objects:,}"]
            + [f"{summary.median_pieces_per_label:g}"]
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
            "Equally in two or more of its objects (no parent)",
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
                f"{e['ambiguous_parent']:,}",
            ]
        )
    return rows


def mask_ambiguous_lines(report: MaskReport) -> list[str]:
    """Return one line per pair of types with objects given no parent because of a tie.

    Such a child lies equally in two or more parent objects, with a share that would make
    it inside (``ambiguous_parent``, contract §15.1): it is never given one of them, whatever
    the relation of the pair, so every pair is listed, ``none`` included.
    """
    return [
        f"{pair.child} in {pair.parent}: {plural(pair.evidence['ambiguous_parent'], 'object')}"
        f" of {pair.evidence['children']:,} equally in two or more {pair.parent}"
        for pair in report.pairs
        if pair.evidence.get("ambiguous_parent")
    ]


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
    if e["ambiguous_parent"]:
        extra.append(
            f"{e['ambiguous_parent']} equally in two or more {pair.parent} (no parent given)"
        )
    return text + (f"; {', '.join(extra)}" if extra else "")


def _hierarchy_lines(hierarchy: Mapping[str, Sequence[str]]) -> list[str]:
    """Return the mask types as an indented tree; a type with two parents shows twice.

    Every type is listed. Types whose direct parents form a cycle (issue #105) have no
    parent-child hierarchy: a named warning ``mask_hierarchy_cycle`` comes first, with
    every type and link of the cycle, and the tree shows them together as one
    ``[cycle: ...]`` entry (the strongly connected components), never guessing a direction.
    """
    cycles = hierarchy_cycles(hierarchy)
    group = {name: (name,) for name in hierarchy}
    group.update({parent: (parent,) for parents in hierarchy.values() for parent in parents})
    group.update({name: cycle for cycle in cycles for name in cycle})
    children: dict[tuple[str, ...], set[tuple[str, ...]]] = {g: set() for g in group.values()}
    has_parent: set[tuple[str, ...]] = set()
    for name, parents in hierarchy.items():
        for parent in parents:
            if group[parent] != group[name]:
                children[group[parent]].add(group[name])
                has_parent.add(group[name])
    lines = [_cycle_warning(cycle, hierarchy) for cycle in cycles]

    def show(node: tuple[str, ...], depth: int) -> None:
        label = f"[cycle: {', '.join(node)}]" if node in cycles else node[0]
        lines.append(f"{'    ' * (depth + 1)}{label}")
        for child in sorted(children[node]):
            show(child, depth + 1)

    for node in sorted(set(group.values()) - has_parent):
        show(node, 0)
    return lines


def _cycle_warning(cycle: tuple[str, ...], hierarchy: Mapping[str, Sequence[str]]) -> str:
    """Name a cycle's types and links, and say no hierarchy can be inferred for them."""
    links = [
        f"{name} in {parent}"
        for name in cycle
        for parent in hierarchy.get(name, ())
        if parent in cycle
    ]
    return (
        f"  Warning (mask_hierarchy_cycle): the direct parents of {', '.join(cycle)} form a "
        f"cycle ({'; '.join(links)}), so no parent-child hierarchy can be inferred for "
        "them; they are shown together below. Check these masks."
    )
