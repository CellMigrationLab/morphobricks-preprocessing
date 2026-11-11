"""The texts a run with a published description refuses with (plain words, no reader jargon).

Each function returns a :class:`.validation.ValidationIssue` whose ``code`` is the Spec's own
code (experiment-v1 section 8), so a script can match it, and whose message and action say in
plain words what is wrong and what to do. Presentation only: which codes apply is decided by
the reader, the physical checks and the layouts, never here.
"""

from __future__ import annotations

from .channel_plan import channel_coverage, partial_repeats
from .description import Description
from .description_layouts import (
    DeclaredLayout,
    heterogeneous_message,
    heterogeneous_units,
)
from .description_report import DescriptionIssue
from .description_rules import Code
from .physical_check import (
    CHANNEL_COUNT_MISMATCH,
    HEADER_CONTRADICTION,
    HETEROGENEOUS_LAYOUT,
    IMAGE_TYPE_CONTRADICTS_HEADER,
    MASK_CONTRADICTS_REPRESENTATION,
    MASK_TYPE_NOT_DECLARED,
    UNIT_NOT_DECLARED,
    UNIT_NOT_FOUND,
    UNIT_NOT_HOMOGENEOUS,
    PhysicalIssue,
    UnitKey,
)
from .validation import ValidationIssue, error, warning

FIX = (
    "Open the experiment description in the notebook, correct it, publish it again, and run again."
)
_ACTIONS = {
    UNIT_NOT_FOUND: "Remove that acquisition from the description or restore its folder.",
    UNIT_NOT_DECLARED: "Add the acquisition to the description, or exclude its images.",
    MASK_TYPE_NOT_DECLARED: "Declare the mask type in the description, or remove its files.",
}


def unit_text(key: UnitKey | None) -> str:
    """Name a unit by its folders: condition, biological repeat and acquisition."""
    if key is None:
        return "the experiment"
    return (
        f"condition folder {key.condition!r}, repeat folder {key.biological_repeat!r}, "
        f"acquisition folder {key.technical_repeat!r}"
    )


def _what(issue: PhysicalIssue) -> str:
    """Say in plain words what one physical refusal found."""
    where = unit_text(issue.unit)
    if issue.code == UNIT_NOT_FOUND:
        return f"The description lists {where}, but the experiment has no images there."
    if issue.code == UNIT_NOT_DECLARED:
        return f"The experiment has images in {where}, which the description does not list."
    if issue.code == MASK_TYPE_NOT_DECLARED:
        return f"The masks folder has masks of type {issue.detail!r} that are not declared."
    reasons = {
        UNIT_NOT_HOMOGENEOUS: "Images of one acquisition differ in their layout",
        CHANNEL_COUNT_MISMATCH: "An image has another number of channels than described",
        HEADER_CONTRADICTION: "An image header names a channel the description does not",
        IMAGE_TYPE_CONTRADICTS_HEADER: "The image type contradicts the image header",
    }
    return f"{reasons[issue.code]} in {where}: {issue.detail}."


def physical_issue(issue: PhysicalIssue) -> ValidationIssue:
    """Return one physical check refusal as an error naming its code, unit and image."""
    paths = () if issue.path is None else (issue.path,)
    return error(issue.code, _what(issue), paths, _ACTIONS.get(issue.code, FIX))


def reader_issue(issue: DescriptionIssue) -> ValidationIssue:
    """Return one reader (or file) refusal as an error with its code, table path and value."""
    code = issue.code.value
    shown = "" if issue.value is None else f" (value {issue.value!r})"
    message = (
        f"The experiment description morphobricks.toml cannot be used: {code} at "
        f"{issue.path}{shown}."
    )
    return error(code, message, (), FIX)


_WARNINGS = {
    Code.W_UNUSED_CHANNEL: "The label {key} is declared, but no folder's channels use it.",
    Code.W_REPEAT_MISSING_CONDITIONS: "Replicate {key} has no folder of {value}.",
    Code.W_REFERENCE_UNPAIRED: (
        "{key} and {value}, the condition it is compared against, share no replicate."
    ),
}


def reader_warning(issue: DescriptionIssue) -> ValidationIssue:
    """Return one warning of the strict reader (valid, never blocking) in plain words."""
    code = issue.code.value
    key = issue.path.partition(".")[2].strip('"')
    value = issue.value
    shown = ", ".join(map(str, value)) if isinstance(value, list | tuple) else str(value)
    text = _WARNINGS.get(issue.code, "{code} at {path}.")
    return warning(code, text.format(key=key, value=shown, code=code, path=issue.path))


def heterogeneous_issue(layouts: tuple[DeclaredLayout, ...]) -> ValidationIssue:
    """Return the refusal of an experiment whose units declare different image types."""
    action = (
        "Process the RGB and the separate-channel units as separate experiments. "
        "Units: " + "; ".join(heterogeneous_units(layouts)) + "."
    )
    return error(HETEROGENEOUS_LAYOUT, heterogeneous_message(layouts), (), action)


PARTIAL_COVERAGE = "partial_channel_coverage"
"""The run's warning naming each biological repeat that has a channel in only some acquisitions."""


def coverage_issues(description: Description) -> list[ValidationIssue]:
    """Return the warning listing every partially represented repeat and channel, or nothing.

    Never a refusal: the table holds what was acquired (an absent channel is blank, never zero)
    and the coverage is recorded in the manifest. A brick asked to compare or normalize such a
    channel across the repeat refuses (``A_SCOPE_INCOMPLETE``) unless the user selects and
    records the acquisitions that have it.
    """
    partial = partial_repeats(channel_coverage(description))
    if not partial:
        return []
    return [
        warning(
            PARTIAL_COVERAGE,
            "Some biological repeats have a channel in only some of their acquisitions (partial "
            "presence). Their images are measured as acquired: the missing channel is blank in "
            "the histogram table, never zero.",
            partial,
            "No action needed for this run. Later analyses that pool such a channel over a "
            "whole repeat refuse it (A_SCOPE_INCOMPLETE) unless you choose to use only the "
            "acquisitions that have it; that choice is recorded.",
        )
    ]


def representation_issue(
    mask_id: str, representation: str, path: str, values: int
) -> ValidationIssue:
    """Return the mask step's refusal of a mask whose pixels contradict its representation."""
    message = (
        f"Mask {mask_id!r} is declared {representation!r}, but its file holds {values} "
        "different values. A binary mask has at most two."
    )
    action = "Declare the mask as instances in the description if each value is an object."
    return error(MASK_CONTRADICTS_REPRESENTATION, message, (path,), action)
