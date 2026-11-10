"""The rows of ``experiment_design.csv``: repeat groups, their confirmation, and controls.

A repeat group is one biological repeat across all conditions (normalization plan N30); each
row is one biological-repeat folder of a condition, with its group, whether the condition is a
control (recorded only, N36), its counts and the confirmation that the group's images are
comparable (N35). The file is an **output only** (Spec ADR 0002 AR10 and AR11 (3)): the run
derives it from the published description (:mod:`.design_derive`) and writes it with
:func:`write_experiment_design`; no brick reads it back as an input to grouping, and the
notebook's pairing is an answer of the draft (:mod:`.notebook_design`). :class:`DesignTable`
holds rows and answers questions about them; it has no editing methods.

:func:`reconcile_design` and :func:`design_issues` are the run's check of the derived rows
against the folders found; :func:`parse_design_bytes` is the strict parser of a file
:func:`write_experiment_design` wrote. No brick reads the file to propose or prefill a draft
(experiment-v1 section 7); the parser is for tests and for checking a written file.
The lenient reader, the default pairing, the editor model and the strict reader for other
bricks (``read_confirmed_design``) were removed in v0.5.1 (SWEEP-PP). Nothing here reads images.
"""

from __future__ import annotations

import csv
import io
import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path

from morphobricks_normalization.records import SINGLE

from ._io import write_text_atomic
from .dataset import BiologicalRepeat
from .design_values import (
    DATE_FORMAT,
    DesignFormatError,
    check_header,
    is_date,
    parse_bool,
    parse_count,
)
from .discovery import IdentityError, check_identity_case, check_identity_text
from .validation import ValidationIssue, error, plural

DESIGN_FILENAME = "experiment_design.csv"
DESIGN_SCHEMA_VERSION = "1"
DESIGN_COLUMNS = (
    "condition",
    "biological_repeat",
    "repeat_group",
    "control",
    "image_count",
    "technical_repeat_count",
    "confirmed",
    "confirmed_on",
    "schema_version",
)


@dataclass(frozen=True)
class DesignRow:
    """One biological-repeat folder of a condition, and the answers about it.

    Attributes
    ----------
    condition, biological_repeat
        Folder labels, as in ``plane_histograms.csv`` (``single`` when absent).
    repeat_group
        Label of the repeat group the folder belongs to.
    control
        True when the condition is marked as a control.
    image_count, technical_repeat_count
        Images and technical-repeat folders when the group was confirmed (the
        current counts while it is not).
    confirmed
        True once the repeat group is confirmed.
    confirmed_on
        Date of the confirmation (ISO 8601), or ``""``.
    """

    condition: str
    biological_repeat: str
    repeat_group: str
    control: bool = False
    image_count: int = 0
    technical_repeat_count: int = 0
    confirmed: bool = False
    confirmed_on: str = ""

    @property
    def key(self) -> tuple[str, str]:
        """``(condition, biological_repeat)``, the folder's labels."""
        return (self.condition, self.biological_repeat)


@dataclass(frozen=True)
class DesignTable:
    """The rows of an ``experiment_design.csv``, sorted, and what they say.

    Read-only: there is no editing method (the notebook's pairing is a draft answer).

    Attributes
    ----------
    rows
        One row per biological-repeat folder, sorted by condition and repeat.
    """

    rows: tuple[DesignRow, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "rows", tuple(sorted(self.rows, key=lambda r: r.key)))

    @property
    def conditions(self) -> tuple[str, ...]:
        """Condition labels, sorted."""
        return tuple(sorted({row.condition for row in self.rows}))

    @property
    def groups(self) -> tuple[str, ...]:
        """Repeat-group labels, sorted."""
        return tuple(sorted({row.repeat_group for row in self.rows}))

    @property
    def controls(self) -> tuple[str, ...]:
        """Conditions marked as controls, sorted."""
        return tuple(sorted({row.condition for row in self.rows if row.control}))

    @property
    def single_repeat(self) -> bool:
        """True when no condition has biological-repeat folders: one group for everything."""
        return bool(self.rows) and all(row.biological_repeat == SINGLE for row in self.rows)

    @property
    def confirmed(self) -> bool:
        """True when every repeat group is confirmed and none holds two folders of a condition."""
        return bool(self.rows) and not self.unconfirmed_groups and not self.conflicts

    @property
    def unconfirmed_groups(self) -> tuple[str, ...]:
        """Repeat groups not confirmed yet."""
        return tuple(g for g in self.groups if not self.group_confirmed(g))

    @property
    def conflicts(self) -> dict[str, tuple[str, ...]]:
        """Repeat groups with more than one folder of a condition, and those conditions.

        A repeat group is one biological repeat across conditions, so it can
        have only one folder per condition; such a group cannot be confirmed.
        """
        found: dict[str, tuple[str, ...]] = {}
        for group in self.groups:
            conditions = [row.condition for row in self.members(group)]
            twice = sorted({c for c in conditions if conditions.count(c) > 1})
            if twice:
                found[group] = tuple(twice)
        return found

    def members(self, group: str) -> tuple[DesignRow, ...]:
        """Return the rows of a repeat group."""
        return tuple(row for row in self.rows if row.repeat_group == group)

    def row(self, key: tuple[str, str]) -> DesignRow | None:
        """Return the row of a ``(condition, biological_repeat)`` folder, or None."""
        return next((row for row in self.rows if row.key == key), None)

    def group_confirmed(self, group: str) -> bool:
        """Return True if every folder of the group is confirmed."""
        members = self.members(group)
        return bool(members) and all(row.confirmed for row in members)

    def confirmed_on(self, group: str) -> str:
        """Return when a repeat group was confirmed (the latest date of its rows), or ``""``."""
        return max((row.confirmed_on for row in self.members(group)), default="")

    def missing_conditions(self, group: str) -> tuple[str, ...]:
        """Return the conditions in which a repeat group has no folder."""
        present = {row.condition for row in self.members(group)}
        return tuple(c for c in self.conditions if c not in present)

    def repeat_groups(self) -> dict[tuple[str, str], str]:
        """Return ``{(condition, biological_repeat): repeat_group}``.

        This is the pairing the normalization brick takes for its
        ``biological_repeat`` scope.
        """
        return {row.key: row.repeat_group for row in self.rows}


def reconcile_design(design: DesignTable, repeats: Iterable[BiologicalRepeat]) -> DesignTable:
    """Bring derived rows up to date with the folders found now (the run's check).

    Folders of ``design`` keep their repeat group, control mark and confirmation; added or
    removed images within a folder keep the confirmation. A folder found now but not in
    ``design`` joins the group named after it (and the control mark of its condition)
    unconfirmed, so that group is not confirmed (:func:`design_issues`). Folders no longer
    found are dropped.

    Parameters
    ----------
    design
        The rows derived from the published description.
    repeats
        The biological-repeat folders found now (:func:`.dataset.biological_repeats`).
    """
    controls = set(design.controls)
    rows, added = [], set()
    for repeat in repeats:
        row = design.row(repeat.key)
        if row is None:
            added.add(repeat.key)
            row = replace(
                _new_row(repeat, repeat.biological_repeat), control=repeat.condition in controls
            )
        elif not row.confirmed:
            # Unanswered rows show the counts as they are now.
            row = replace(
                row,
                image_count=repeat.image_count,
                technical_repeat_count=repeat.technical_repeat_count,
            )
        rows.append(row)
    reopened = {row.repeat_group for row in rows if row.key in added}
    return DesignTable(
        tuple(
            replace(row, confirmed=False, confirmed_on="") if row.repeat_group in reopened else row
            for row in rows
        )
    )


def design_issues(design: DesignTable) -> list[ValidationIssue]:
    """Check that a design can be used: every repeat group confirmed, one folder per condition.

    Returns
    -------
    list of ValidationIssue
        ``invalid_experiment_design`` for a repeat-group name that is not an identity
        (decision D-e: NA-like, empty, or with outer whitespace) or a ``confirmed_on`` that
        is not empty or a date in :data:`DATE_FORMAT`; ``repeat_group_conflict`` for a
        group with two folders of one condition; ``design_not_confirmed`` for groups not
        confirmed yet. All are errors.
    """
    issues = []
    invalid = _design_problems(design)
    if invalid:
        issues.append(
            error(
                "invalid_experiment_design",
                "The experiment design has values that cannot be used: "
                + "; ".join(f"{_folder(row)}: {problem}" for row, problem in invalid)
                + ".",
                sorted({_folder(row) for row, _ in invalid}),
                "Give every repeat group a name (not NA, nan, none, null, n/a or empty, "
                "without spaces around it, spelled the same way in every row) and every "
                f"confirmation date as {DATE_FORMAT}.",
            )
        )
    if design.conflicts:
        described = "; ".join(
            f"{group} has more than one folder of {', '.join(conditions)}"
            for group, conditions in design.conflicts.items()
        )
        issues.append(
            error(
                "repeat_group_conflict",
                "A repeat group is one biological repeat across the conditions, so it can "
                f"hold only one folder of each condition: {described}.",
                [_folder(row) for g in design.conflicts for row in design.members(g)],
                "Move one of the folders into another replicate on the notebook's Replicates "
                "page, then save the experiment description again (Check and save).",
            )
        )
    unconfirmed = [g for g in design.unconfirmed_groups if g not in design.conflicts]
    if unconfirmed:
        what = (
            "The biological repeat of this experiment is"
            if design.single_repeat
            else f"{plural(len(unconfirmed), 'repeat group')} ({', '.join(unconfirmed)}) "
            f"{'is' if len(unconfirmed) == 1 else 'are'}"
        )
        issues.append(
            error(
                "design_not_confirmed",
                f"{what} not confirmed yet. Images are only processed once it is confirmed "
                "that the biological repeats were prepared and acquired under the same "
                "circumstances, so that they are comparable.",
                [_folder(row) for g in unconfirmed for row in design.members(g)],
                "Describe these folders: confirm their replicates in the notebook's describe "
                "steps and save the experiment description again (Check and save).",
            )
        )
    return issues


def write_experiment_design(design: DesignTable, path: str | os.PathLike) -> Path:
    """Write ``experiment_design.csv``, replacing the file only once writing succeeded.

    The file is UTF-8 with LF line endings; the same design always gives the
    same bytes. Every ``confirmed_on`` it writes is empty or a date in
    :data:`DATE_FORMAT`, and every repeat group a valid name.

    Raises
    ------
    DesignFormatError
        If a row has a repeat-group name or confirmation date the parser refuses
        (:func:`design_issues`, ``invalid_experiment_design``); nothing is written.
    """
    path = Path(path)
    invalid = _design_problems(design)
    if invalid:
        raise DesignFormatError(
            "The experiment design cannot be written: "
            + "; ".join(f"{_folder(row)}: {problem}" for row, problem in invalid)
            + "."
        )
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(DESIGN_COLUMNS)
    for row in design.rows:
        writer.writerow(
            [
                row.condition,
                row.biological_repeat,
                row.repeat_group,
                _bool_text(row.control),
                row.image_count,
                row.technical_repeat_count,
                _bool_text(row.confirmed),
                row.confirmed_on,
                DESIGN_SCHEMA_VERSION,
            ]
        )
    return write_text_atomic(path, buffer.getvalue(), parents=True)


def parse_design_bytes(data: bytes, name: str = DESIGN_FILENAME) -> DesignTable:
    """Parse a design as :func:`write_experiment_design` writes it; confirmation not required.

    Strict (experiment-v1 section 7): UTF-8 without a byte-order mark, exactly
    :data:`DESIGN_COLUMNS` in that order, one value per column, no outer whitespace, ``true`` or
    ``false``, whole counts, ISO dates, identities as :func:`.discovery.check_identity_text`
    takes them and no case collision.
    Nothing in this package reads an ``experiment_design.csv`` back as an input.

    Raises
    ------
    DesignFormatError
        Naming the line and column of the first value refused.
    """
    if data.startswith(b"\xef\xbb\xbf"):
        raise DesignFormatError(
            f"{name} starts with a byte-order mark: it was saved by another program, not "
            "written by the preprocessing brick."
        )
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DesignFormatError(f"{name} is not UTF-8 text: {exc}") from None
    try:
        lines = list(csv.reader(io.StringIO(text, newline=""), strict=True))
    except csv.Error as exc:
        raise DesignFormatError(f"{name} is not a well-formed CSV file: {exc}") from None
    check_header(lines[0] if lines else (), name)
    if not lines or tuple(lines[0]) != DESIGN_COLUMNS:
        found = ", ".join(lines[0]) if lines else "nothing"
        raise DesignFormatError(
            f"{name} must have the columns {', '.join(DESIGN_COLUMNS)} in this order, not {found}."
        )
    rows: list[DesignRow] = []
    numbers: list[int] = []
    for number, values in enumerate(lines[1:], start=2):
        where = f"{name}, line {number}"
        if len(values) != len(DESIGN_COLUMNS):
            raise DesignFormatError(
                f"{where} has {plural(len(values), 'value')} for "
                f"{plural(len(DESIGN_COLUMNS), 'column')}."
            )
        record = dict(zip(DESIGN_COLUMNS, values, strict=True))
        _check_strict_values(record, where)
        rows.append(_parse_row(record, where))
        numbers.append(number)
    if not rows:
        raise DesignFormatError(f"{name} lists no folder.")
    _check_rows(rows, name)
    try:
        check_identity_case((row.condition, row.biological_repeat, SINGLE) for row in rows)
    except IdentityError as exc:
        raise DesignFormatError(f"{name}: {exc}") from None
    _check_group_case(zip(numbers, rows, strict=True), name)
    return DesignTable(tuple(rows))


def _check_rows(rows: list[DesignRow], name: str) -> None:
    """Refuse a folder listed twice, or rows of one condition disagreeing on ``control``."""
    keys = [row.key for row in rows]
    twice = sorted({key for key in keys if keys.count(key) > 1})
    if twice:
        raise DesignFormatError(
            f"{name} lists a folder more than once: " + "; ".join(f"{c} / {b}" for c, b in twice)
        )
    for condition in sorted({row.condition for row in rows}):
        if len({row.control for row in rows if row.condition == condition}) > 1:
            raise DesignFormatError(
                f"{name}: control must be the same in every row of condition {condition}."
            )


def _check_strict_values(record: Mapping[str, str], where: str) -> None:
    for column, value in record.items():
        if value != value.strip():
            raise DesignFormatError(f"{where}: {column} {value!r} starts or ends with whitespace.")
    for column in ("condition", "biological_repeat"):
        try:
            check_identity_text(record[column], column)
        except IdentityError as exc:
            raise DesignFormatError(f"{where}: {exc}.") from None
    for column in ("control", "confirmed"):
        if record[column] not in ("true", "false"):
            raise DesignFormatError(
                f"{where}: {column} must be true or false, not {record[column]!r}."
            )
    for column in ("image_count", "technical_repeat_count"):
        value = record[column]
        if not (value.isascii() and value.isdigit() and str(int(value)) == value):
            raise DesignFormatError(
                f"{where}: {column} must be a whole number of at least 0, not {value!r}."
            )


def _new_row(repeat: BiologicalRepeat, group: str) -> DesignRow:
    return DesignRow(
        repeat.condition,
        repeat.biological_repeat,
        group,
        image_count=repeat.image_count,
        technical_repeat_count=repeat.technical_repeat_count,
    )


def _parse_row(record: Mapping[str, str | None], where: str) -> DesignRow:
    def text(column: str) -> str:
        return (record.get(column) or "").strip()

    if text("schema_version") != DESIGN_SCHEMA_VERSION:
        raise DesignFormatError(
            f"{where}: schema_version {text('schema_version')!r} is not supported "
            f"(this version reads {DESIGN_SCHEMA_VERSION!r})."
        )
    condition, repeat = text("condition"), text("biological_repeat")
    if not condition or not repeat:
        raise DesignFormatError(f"{where}: condition and biological_repeat must not be empty.")
    group = record.get("repeat_group") or ""
    try:
        check_identity_text(group, "repeat_group")
    except IdentityError as exc:
        raise DesignFormatError(
            f"{where}, column repeat_group: {exc}. Give the repeat group a name, for example "
            f"the folder name {repeat!r}."
        ) from None
    on = text("confirmed_on")
    if on and not is_date(on):
        raise DesignFormatError(
            f"{where}, column confirmed_on: {on!r} is not a date in the form {DATE_FORMAT} "
            "(for example 2026-10-26)."
        )
    return DesignRow(
        condition=condition,
        biological_repeat=repeat,
        repeat_group=group,
        control=parse_bool(text("control"), "control", where),
        image_count=parse_count(text("image_count"), "image_count", where),
        technical_repeat_count=parse_count(
            text("technical_repeat_count"), "technical_repeat_count", where
        ),
        confirmed=parse_bool(text("confirmed"), "confirmed", where),
        confirmed_on=on,
    )


def _design_problems(design: DesignTable) -> list[tuple[DesignRow, str]]:
    """Return the rows whose repeat group or date the parser refuses, with the reason.

    Each row's own values (:func:`_row_problems`), and a repeat group spelled differently
    from an earlier row's only in case (decision D-e: one spelling per name).
    """
    problems = [(row, problem) for row in design.rows for problem in _row_problems(row)]
    spellings: dict[str, str] = {}
    for row in design.rows:
        first = spellings.setdefault(row.repeat_group.casefold(), row.repeat_group)
        if first != row.repeat_group:
            problems.append(
                (row, f"repeat_group {row.repeat_group!r} differs from {first!r} only in case")
            )
    return problems


def _check_group_case(numbered: Iterable[tuple[int, DesignRow]], name: str) -> None:
    """Refuse repeat groups whose names differ only in case (decision D-e), naming lines."""
    spellings: dict[str, tuple[int, str]] = {}
    for number, row in numbered:
        first_line, first = spellings.setdefault(
            row.repeat_group.casefold(), (number, row.repeat_group)
        )
        if first != row.repeat_group:
            raise DesignFormatError(
                f"{name}, line {number}, column repeat_group: {row.repeat_group!r} differs "
                f"only in case from {first!r} on line {first_line}. Spell a repeat group the "
                "same way in every row."
            )


def _row_problems(row: DesignRow) -> list[str]:
    problems = []
    try:
        check_identity_text(row.repeat_group, "repeat_group")
    except IdentityError as exc:
        problems.append(str(exc))
    if row.confirmed_on and not is_date(row.confirmed_on):
        problems.append(
            f"confirmed_on {row.confirmed_on!r} is not a date in the form {DATE_FORMAT}"
        )
    return problems


def _bool_text(value: bool) -> str:
    return "true" if value else "false"


def _folder(row: DesignRow) -> str:
    parts = [p for p in (row.condition, row.biological_repeat) if p != SINGLE]
    return "/".join(parts) or "."
