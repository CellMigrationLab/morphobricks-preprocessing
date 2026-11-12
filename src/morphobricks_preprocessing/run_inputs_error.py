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
"""

from __future__ import annotations


class RunInputsError(ValueError):
    """The run cannot be used as it is: run preprocessing again (the message says why)."""

    code: str | None = None
    """The Spec's code when the refusal has one (``A_*``), else None."""


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
