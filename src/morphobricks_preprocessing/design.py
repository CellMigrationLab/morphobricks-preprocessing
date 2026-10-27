"""The experiment design: repeat groups, their confirmation, and control conditions.

A repeat group is one biological repeat across all conditions (normalization
plan N30). By default, biological-repeat folders with the same name are one
group; the user may move a folder into another group when a repeat was named
differently in some condition. Before processing, the user confirms for each
group that its images were prepared and acquired under the same circumstances
(N35); without biological-repeat folders the whole experiment is one group
(N31). Conditions may be marked as controls, for the record only (N36).

The answers are kept in ``experiment_design.csv`` in the output folder
(``docs/contract.md`` §12), so a rerun with the same folders does not ask
again. Later bricks read the pairing with :func:`read_experiment_design`.
Nothing here reads images.
"""

from __future__ import annotations

import csv
import io
import os
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path

from morphobricks_normalization.records import SINGLE

from .dataset import BiologicalRepeat
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

_TRUE = frozenset({"true", "yes", "1"})
_FALSE = frozenset({"false", "no", "0", ""})


class DesignFormatError(ValueError):
    """Raised when a file is not an experiment design this version can read."""


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
class DesignChanges:
    """How the folders differ from an earlier experiment design.

    Attributes
    ----------
    added
        Biological-repeat folders not in the earlier design.
    removed
        Folders of the earlier design no longer found.
    reopened
        Repeat groups that were confirmed but are asked again, because a folder
        was added to them.
    """

    added: tuple[tuple[str, str], ...] = ()
    removed: tuple[tuple[str, str], ...] = ()
    reopened: tuple[str, ...] = ()

    @property
    def any(self) -> bool:
        """True if the folders changed since the earlier design."""
        return bool(self.added or self.removed)


@dataclass(frozen=True)
class ExperimentDesign:
    """The pairing of biological repeats, their confirmation and the controls.

    Every method returns a new design; a design is never changed in place.

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

    def with_group(self, key: tuple[str, str], group: str) -> ExperimentDesign:
        """Move a folder into another repeat group.

        Both groups changed, so neither stays confirmed.
        """
        old = self.row(key)
        if old is None:
            raise KeyError(f"{key!r} is not a folder of this design.")
        if old.repeat_group == group:
            return self
        touched = {old.repeat_group, group}
        rows = []
        for row in self.rows:
            if row.key == key:
                row = replace(row, repeat_group=group)
            if row.repeat_group in touched or row.key == key:
                row = replace(row, confirmed=False, confirmed_on="")
            rows.append(row)
        return ExperimentDesign(tuple(rows))

    def with_confirmed(
        self,
        group: str,
        confirmed: bool,
        on: str = "",
        repeats: Iterable[BiologicalRepeat] = (),
    ) -> ExperimentDesign:
        """Confirm a repeat group, or take the confirmation back.

        Parameters
        ----------
        group
            The repeat group.
        confirmed
            The answer.
        on
            Date of the confirmation (ISO 8601).
        repeats
            The folders as found now; confirming records their counts.
        """
        counts = {r.key: (r.image_count, r.technical_repeat_count) for r in repeats}
        rows = []
        for row in self.rows:
            if row.repeat_group == group:
                images, technical = counts.get(
                    row.key, (row.image_count, row.technical_repeat_count)
                )
                row = replace(
                    row,
                    confirmed=confirmed,
                    confirmed_on=on if confirmed else "",
                    image_count=images,
                    technical_repeat_count=technical,
                )
            rows.append(row)
        return ExperimentDesign(tuple(rows))

    def with_control(self, condition: str, control: bool) -> ExperimentDesign:
        """Mark a condition as a control, or not."""
        return ExperimentDesign(
            tuple(
                replace(row, control=control) if row.condition == condition else row
                for row in self.rows
            )
        )

    def dated(self, on: str) -> ExperimentDesign:
        """Fill the date of confirmed rows that have none (e.g. confirmed by hand in the file)."""
        return ExperimentDesign(
            tuple(
                replace(row, confirmed_on=on) if row.confirmed and not row.confirmed_on else row
                for row in self.rows
            )
        )


def default_design(repeats: Iterable[BiologicalRepeat]) -> ExperimentDesign:
    """Build the design before any answer: repeats with the same folder name are one group.

    Nothing is confirmed and no condition is a control.
    """
    return ExperimentDesign(tuple(_new_row(repeat, repeat.biological_repeat) for repeat in repeats))


def reconcile_design(
    previous: ExperimentDesign | None, repeats: Iterable[BiologicalRepeat]
) -> tuple[ExperimentDesign, DesignChanges]:
    """Bring an earlier design up to date with the folders found now.

    Folders of the earlier design keep their repeat group, control mark and
    confirmation; added or removed images within a folder keep the
    confirmation. A new folder joins the group named after it (and the
    control mark of its condition), and that group is asked again. Folders no
    longer found are dropped.

    Parameters
    ----------
    previous
        The earlier design, or None.
    repeats
        The biological-repeat folders found now
        (:func:`.dataset.biological_repeats`).

    Returns
    -------
    tuple of ExperimentDesign and DesignChanges
    """
    repeats = list(repeats)
    if previous is None:
        return default_design(repeats), DesignChanges()
    controls = set(previous.controls)
    current = {repeat.key for repeat in repeats}
    rows, added = [], []
    for repeat in repeats:
        row = previous.row(repeat.key)
        if row is None:
            added.append(repeat.key)
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
    reopened = sorted({row.repeat_group for row in rows if row.key in added})
    was_confirmed = [g for g in reopened if previous.group_confirmed(g)]
    rows = [
        replace(row, confirmed=False, confirmed_on="") if row.repeat_group in reopened else row
        for row in rows
    ]
    removed = sorted(row.key for row in previous.rows if row.key not in current)
    changes = DesignChanges(tuple(sorted(added)), tuple(removed), tuple(was_confirmed))
    return ExperimentDesign(tuple(rows)), changes


def design_issues(design: ExperimentDesign) -> list[ValidationIssue]:
    """Check that a design can be used: every repeat group confirmed, one folder per condition.

    Returns
    -------
    list of ValidationIssue
        ``repeat_group_conflict`` for a group with two folders of one
        condition and ``design_not_confirmed`` for groups not confirmed yet;
        both are errors.
    """
    issues = []
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
                "Move one of the folders into another repeat group in the experiment design "
                "(step 1, or the repeat_group column of experiment_design.csv).",
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
                "Confirm the biological repeats in step 1 of the notebook, or set confirmed "
                "to true for every row of these groups in experiment_design.csv.",
            )
        )
    return issues


def read_experiment_design(path: str | os.PathLike) -> ExperimentDesign:
    """Read ``experiment_design.csv``.

    Labels are read as text. ``control`` and ``confirmed`` accept ``true`` or
    ``false`` in any case (also ``yes``/``no``, ``1``/``0``; empty is false), so
    a file saved by a spreadsheet program still reads. An empty
    ``repeat_group`` means the group named after the folder.

    Raises
    ------
    DesignFormatError
        If the file cannot be read, a column is missing, a value is not valid,
        a folder is listed twice, the rows of a condition disagree about
        ``control``, or the schema version is not 1.
    """
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        raise DesignFormatError(f"{path} cannot be read: {exc}") from exc
    reader = csv.DictReader(io.StringIO(text))
    missing = [c for c in DESIGN_COLUMNS if c not in (reader.fieldnames or ())]
    if missing:
        raise DesignFormatError(
            f"{path.name} is not an experiment design: the column(s) {', '.join(missing)} "
            "are missing."
        )
    rows: list[DesignRow] = []
    for number, record in enumerate(reader, start=2):
        if not any((value or "").strip() for value in record.values() if isinstance(value, str)):
            continue
        rows.append(_parse_row(record, f"{path.name}, line {number}"))
    keys = [row.key for row in rows]
    twice = sorted({key for key in keys if keys.count(key) > 1})
    if twice:
        raise DesignFormatError(
            f"{path.name} lists a folder more than once: "
            + "; ".join(f"{c} / {b}" for c, b in twice)
        )
    for condition in {row.condition for row in rows}:
        if len({row.control for row in rows if row.condition == condition}) > 1:
            raise DesignFormatError(
                f"{path.name}: control must be the same in every row of condition {condition}."
            )
    return ExperimentDesign(tuple(rows))


def write_experiment_design(design: ExperimentDesign, path: str | os.PathLike) -> Path:
    """Write ``experiment_design.csv``, replacing the file only once writing succeeded.

    The file is UTF-8 with LF line endings; the same design always gives the
    same bytes.
    """
    path = Path(path)
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
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", newline="", encoding="utf-8") as fh:
            fh.write(buffer.getvalue())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return path


def load_design(
    output_folder: str | os.PathLike, repeats: Iterable[BiologicalRepeat]
) -> tuple[ExperimentDesign, DesignChanges, str | None]:
    """Read the design in an output folder, if any, and bring it up to date with the folders.

    Returns
    -------
    tuple
        The design (the default one when there is no readable file), how the
        folders changed since it was written, and why an existing file could
        not be read (None when it could, or when there is none).
    """
    path = Path(output_folder) / DESIGN_FILENAME
    previous, problem = None, None
    if path.exists():
        try:
            previous = read_experiment_design(path)
        except DesignFormatError as exc:
            problem = str(exc)
    design, changes = reconcile_design(previous, repeats)
    return design, changes, problem


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
    return DesignRow(
        condition=condition,
        biological_repeat=repeat,
        repeat_group=text("repeat_group") or repeat,
        control=_bool(text("control"), "control", where),
        image_count=_count(text("image_count"), "image_count", where),
        technical_repeat_count=_count(
            text("technical_repeat_count"), "technical_repeat_count", where
        ),
        confirmed=_bool(text("confirmed"), "confirmed", where),
        confirmed_on=text("confirmed_on"),
    )


def _bool(value: str, column: str, where: str) -> bool:
    lowered = value.lower()
    if lowered in _TRUE:
        return True
    if lowered in _FALSE:
        return False
    raise DesignFormatError(f"{where}: {column} must be true or false, not {value!r}.")


def _count(value: str, column: str, where: str) -> int:
    if not value:
        return 0
    try:
        number = int(value)
    except ValueError:
        raise DesignFormatError(
            f"{where}: {column} must be a whole number, not {value!r}."
        ) from None
    if number < 0:
        raise DesignFormatError(f"{where}: {column} must not be negative.")
    return number


def _bool_text(value: bool) -> str:
    return "true" if value else "false"


def _folder(row: DesignRow) -> str:
    parts = [p for p in (row.condition, row.biological_repeat) if p != SINGLE]
    return "/".join(parts) or "."
