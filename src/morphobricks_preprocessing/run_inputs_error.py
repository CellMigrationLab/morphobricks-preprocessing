"""The errors of :class:`.run_inputs.RunInputs` (moved from ``run_inputs``, which re-exports them).

``RunInputsError`` is the general refusal: run preprocessing again. The Spec's analysis-layer
refusals (experiment-v1 section 8, ADR 0002) are subclasses with their code, so a caller can
match them: :class:`DescriptionRequiredError` (``A_DESCRIPTION_REQUIRED``),
:class:`DraftRefusedError` (``A_DRAFT_REFUSED``), :class:`MaskUncheckedError`
(``A_MASK_UNCHECKED``), :class:`ChannelNotFoundError` (``A_SCOPE_INCOMPLETE``),
:class:`MaskNotInstancesError` (``A_MASK_NOT_INSTANCES``) and :class:`RgbNotSupportedError`
(``A_RGB_NOT_SUPPORTED``). Preprocessing raises the first four of these itself; the last two
are for the bricks to raise (contract section 16, Stage 2 design PR 2), so that no brick keeps
its own copy of the code.

Two run-folder refusals carry a runtime code outside experiment-v1 section 8, named after the
``RUN_FOLDER_*`` family that Spec ADR 0007 (proposed, MorphoBricks-Spec PR #48) gives the folder
states ``RunInputs.open`` refuses: :class:`RunStoppedError` (``RUN_FOLDER_STOPPED``: the folder
holds a run that preprocessing stopped, with that run's own error issues; issue #322) and
:class:`RunReportUnreadableError` (``RUN_FOLDER_REPORT_UNREADABLE``: the stopped run's report
cannot be read, or is of a version this preprocessing does not read).

A masks folder given explicitly as ``masks_root`` that is not an existing, listable folder is
refused with the codes the run itself uses for a masks folder (issue #487):
:class:`MasksFolderNotFoundError` (``masks_folder_not_found``) and
:class:`MasksFolderUnreadableError` (``masks_folder_unreadable``).

A manifest whose recorded description section digests are not those of the published
description the run read is refused with :class:`SectionDigestMismatchError`
(``manifest_section_digest_mismatch``, issue #490); one whose unit records are not the
published description's units with :class:`UnitRecordMismatchError`
(``manifest_unit_record_mismatch``, issue #491).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .validation import ValidationIssue


class RunInputsError(ValueError):
    """The run cannot be used as it is: run preprocessing again (the message says why)."""

    code: str | None = None
    """The refusal's code when it has one (the Spec's ``A_*``, a runtime code), else None."""


class DescriptionRequiredError(RunInputsError):
    """The run's output folder holds no published description (every run needs one).

    Spec ADR 0002 AR10/AR11: there is no undescribed run; the message names the fix (the
    notebook's describe steps, or the command line's ``describe``).
    """

    code = "A_DESCRIPTION_REQUIRED"


class DraftRefusedError(RunInputsError):
    """A draft or an unconfirmed description was given: only the published file is read."""

    code = "A_DRAFT_REFUSED"


class MaskUncheckedError(RunInputsError):
    """A mask file whose representation the mask step has not checked yet was used."""

    code = "A_MASK_UNCHECKED"


class ChannelNotFoundError(RunInputsError):
    """A canonical channel ID is not one of the image's channels (never a default index).

    One rule for "no image has the channel" and "some images lack it" (owner answer 2): both
    are ``A_SCOPE_INCOMPLETE``, and the message names the images.
    """

    code = "A_SCOPE_INCOMPLETE"


class MaskNotInstancesError(RunInputsError):
    """A mask whose representation is not ``instances`` was used as an object set.

    For the bricks to raise: a mask that is not ``instances`` (for example ``binary``) has no
    object identity, so it is refused unless the request asks for a recorded conversion. A
    ``parent_labels`` mask is refused always: it has no conversion (experiment-v1 section 4.5b).
    """

    code = "A_MASK_NOT_INSTANCES"


class RgbNotSupportedError(RunInputsError):
    """The run's units are ``rgb`` and the receiving brick does not declare RGB support.

    For the bricks to raise: the request is refused, never split or converted to grey.
    """

    code = "A_RGB_NOT_SUPPORTED"


class MasksFolderNotFoundError(RunInputsError):
    """The masks folder given as ``masks_root`` does not exist or is not a folder (#487).

    An explicitly requested masks folder is never read as "no masks": the message names the
    path given and what to give instead. The code is the run's own ``masks_folder_not_found``.
    """

    code = "masks_folder_not_found"


class MasksFolderUnreadableError(RunInputsError):
    """The masks folder given as ``masks_root`` cannot be listed (#487).

    A folder that cannot be listed would hold no masks to the check of unrecorded files, so it
    is refused, never read as empty; the message names the folder and the reason.
    """

    code = "masks_folder_unreadable"


class SectionDigestMismatchError(RunInputsError):
    """A recorded section digest is not the one of the published description (#490).

    ``RunInputs.open`` recomputes the digests of the published description the run read (its
    SHA-256 already checked) and refuses a manifest recording another one for any section; the
    message names each such section. The manifest is never corrected.
    """

    code = "manifest_section_digest_mismatch"


class UnitRecordMismatchError(RunInputsError):
    """A recorded unit is not a unit of the published description (#491).

    Each record of ``experiment_description.units`` names a declared unit (repeat, condition,
    technical repeat), once, with its channels and, for a unit declared ``rgb``, its declared
    ``rgb_header_override``; the message names the unit record and the field. Nothing is
    filled in or skipped.
    """

    code = "manifest_unit_record_mismatch"


class RunStoppedError(RunInputsError):
    """The output folder holds a run that preprocessing stopped: it published nothing.

    Raised by :meth:`.run_inputs.RunInputs.open` when the folder has no ``run_manifest.json`` and
    its run report says the run stopped (issue #322). A brick passes the stopped run's refusal on
    unchanged (Spec ADR 0005 propagation rule), so it never reads the report itself.

    Attributes
    ----------
    issues
        The stopped run's error issues, in the report's order, each with its ``code``,
        ``message``, ``paths`` (in the report's order) and ``action`` (the suggested action)
        exactly as the report records them.
    """

    code = "RUN_FOLDER_STOPPED"

    def __init__(self, message: str, issues: tuple[ValidationIssue, ...]) -> None:
        super().__init__(message)
        self.issues = issues

    def __reduce__(self) -> tuple:
        """Pickle with the issues, so a worker process can raise it to its parent."""
        return (type(self), (str(self), self.issues))


class RunReportUnreadableError(RunInputsError):
    """The run report of a folder without a manifest cannot be read: nothing is guessed.

    The report is not valid UTF-8 JSON of the expected shape, or is of a report version this
    preprocessing does not read (issue #322).

    Attributes
    ----------
    path
        The report file.
    """

    code = "RUN_FOLDER_REPORT_UNREADABLE"

    def __init__(self, message: str, path: Path) -> None:
        super().__init__(message)
        self.path = path

    def __reduce__(self) -> tuple:
        """Pickle with the path, so a worker process can raise it to its parent."""
        return (type(self), (str(self), self.path))
