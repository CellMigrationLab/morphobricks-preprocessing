"""Change an experiment design without editing the CSV: groups, controls, confirmations.

The notebook's pairing card applies a :class:`DesignEdit` to the design it shows (the
``design`` command and ``edit_design`` were removed in v0.5.1, PP-C): move folders to other repeat
groups, mark conditions as controls or not, and confirm repeat groups. Each change uses the
design's own rules (:class:`.design.ExperimentDesign`), and a repeat group is confirmed only
when its rows pass the run's validator (:func:`.design.design_issues`). Nothing is confirmed
by default: the confirmation stays an explicit act of the user. Anything unknown is refused
with nothing changed.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from .design import ExperimentDesign, design_issues

_UNCONFIRMED = "design_not_confirmed"
"""The one design issue a repeat group about to be confirmed may have."""


class DesignEditError(ValueError):
    """Raised when a change cannot be made to the design; the design is left as it is."""


@dataclass(frozen=True)
class DesignEdit:
    """The changes to make to an experiment design, applied in this order.

    Attributes
    ----------
    repeat_groups
        ``{(condition, biological_repeat): repeat group}``: move each folder into that
        group; both groups it leaves and joins are no longer confirmed.
    control, not_control
        Conditions to mark as controls, and to unmark.
    confirm
        Repeat groups to confirm (after the moves).
    confirm_all
        Confirm every repeat group not confirmed yet.
    unconfirm
        Repeat groups whose confirmation is taken back (after the moves, before the
        confirmations); the notebook's "Undo" (PP-10). Python only. It never confirms
        anything: a group in it cannot also be confirmed, and it
        cannot be combined with ``confirm_all``.
    """

    repeat_groups: Mapping[tuple[str, str], str] = field(default_factory=dict)
    control: tuple[str, ...] = ()
    not_control: tuple[str, ...] = ()
    confirm: tuple[str, ...] = ()
    confirm_all: bool = False
    unconfirm: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "repeat_groups", MappingProxyType(dict(self.repeat_groups)))
        for name in ("control", "not_control", "confirm", "unconfirm"):
            object.__setattr__(self, name, tuple(getattr(self, name)))


def parse_repeat_group(text: str) -> tuple[tuple[str, str], str]:
    """Read ``CONDITION/REPEAT=GROUP``, the command line's way to move a folder.

    Parameters
    ----------
    text
        The folder's condition and biological repeat (``single`` for none), ``=``, and the
        repeat group; the group is the text after the last ``=``.

    Returns
    -------
    tuple
        ``((condition, biological_repeat), group)``.

    Raises
    ------
    DesignEditError
        If the text is not in that form.
    """
    folder, equals, group = text.rpartition("=")
    condition, slash, repeat = folder.partition("/")
    if not equals or not slash or not condition or not repeat or "/" in repeat:
        raise DesignEditError(
            f"{text!r} is not CONDITION/REPEAT=GROUP (for example ctrl/rep1=day1; the "
            "biological repeat is single when the condition has no repeat folders)"
        )
    return (condition, repeat), group


def apply_design_edit(design: ExperimentDesign, edit: DesignEdit, on: str) -> ExperimentDesign:
    """Return the design with the changes made, refusing any that cannot be made.

    Parameters
    ----------
    design
        The design as read from ``experiment_design.csv``.
    edit
        The changes.
    on
        The date of the confirmations, ``YYYY-MM-DD``.

    Returns
    -------
    ExperimentDesign
        The changed design; a repeat group confirmed before keeps its date.

    Raises
    ------
    DesignEditError
        For an unknown folder, condition or repeat group, a condition both marked and
        unmarked, a repeat group to confirm whose rows break the design rules
        (``repeat_group_conflict``, ``invalid_experiment_design``), or a design whose values
        cannot be written. The message says which and why.
    """
    design = _moved(design, edit.repeat_groups)
    design = _controls(design, edit.control, edit.not_control)
    design = _unconfirmed(design, edit.unconfirm, edit.confirm, edit.confirm_all)
    groups = design.unconfirmed_groups if edit.confirm_all else edit.confirm
    _known("repeat group", groups, design.groups)
    for group in dict.fromkeys(groups):
        if not design.group_confirmed(group):
            _check_group(design, group)
            design = design.with_confirmed(group, True, on)
    invalid = [i for i in design_issues(design) if i.code == "invalid_experiment_design"]
    if invalid:
        raise DesignEditError(invalid[0].message)
    return design


def _moved(design: ExperimentDesign, moves: Mapping[tuple[str, str], str]) -> ExperimentDesign:
    """Move folders into other repeat groups, refusing folders the design does not have."""
    folders = {row.key for row in design.rows}
    unknown = [f"{c}/{r}" for c, r in moves if (c, r) not in folders]
    if unknown:
        known = ", ".join(f"{c}/{r}" for c, r in sorted(folders))
        raise DesignEditError(
            f"unknown folder {', '.join(unknown)}: the design's folders are {known}"
        )
    for key, group in moves.items():
        design = design.with_group(key, group)
    return design


def _controls(
    design: ExperimentDesign, control: tuple[str, ...], not_control: tuple[str, ...]
) -> ExperimentDesign:
    """Mark and unmark controls, refusing unknown conditions and contradictions."""
    _known("condition", (*control, *not_control), design.conditions)
    both = sorted(set(control) & set(not_control))
    if both:
        raise DesignEditError(f"{', '.join(both)} cannot be marked a control and not one")
    for condition in control:
        design = design.with_control(condition, True)
    for condition in not_control:
        design = design.with_control(condition, False)
    return design


def _unconfirmed(
    design: ExperimentDesign,
    unconfirm: tuple[str, ...],
    confirm: tuple[str, ...],
    confirm_all: bool,
) -> ExperimentDesign:
    """Take back confirmations, refusing unknown groups and a group both confirmed and not."""
    _known("repeat group", unconfirm, design.groups)
    both = sorted(set(unconfirm) & set(confirm)) if not confirm_all else sorted(set(unconfirm))
    if both:
        raise DesignEditError(f"{', '.join(both)} cannot be confirmed and unconfirmed at once")
    for group in dict.fromkeys(unconfirm):
        design = design.with_confirmed(group, False)
    return design


def _known(what: str, given: Iterable[str], known: tuple[str, ...]) -> None:
    """Refuse names the design does not have, listing those it has."""
    unknown = [name for name in dict.fromkeys(given) if name not in known]
    if unknown:
        raise DesignEditError(
            f"unknown {what} {', '.join(map(repr, unknown))}: the design's {what}s are "
            + (", ".join(known) or "none")
        )


def _check_group(design: ExperimentDesign, group: str) -> None:
    """Refuse to confirm a repeat group whose rows break the design rules."""
    members = ExperimentDesign(design.members(group))
    problems = [issue for issue in design_issues(members) if issue.code != _UNCONFIRMED]
    if problems:
        raise DesignEditError(
            f"repeat group {group!r} cannot be confirmed: "
            + " ".join(issue.message for issue in problems)
        )
