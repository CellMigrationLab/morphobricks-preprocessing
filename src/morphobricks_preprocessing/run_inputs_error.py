"""The errors of :class:`.run_inputs.RunInputs` (moved from ``run_inputs``, which re-exports them).

``RunInputsError`` is the general refusal: run preprocessing again. The Spec's analysis-layer
refusals (experiment-v1 section 8) are subclasses with their code, so a caller can match them:
:class:`DraftRefusedError` (``A_DRAFT_REFUSED``) and :class:`MaskUncheckedError`
(``A_MASK_UNCHECKED``).
"""

from __future__ import annotations


class RunInputsError(ValueError):
    """The run cannot be used as it is: run preprocessing again (the message says why)."""

    code: str | None = None
    """The Spec's code when the refusal has one (``A_*``), else None."""


class DraftRefusedError(RunInputsError):
    """A draft or an unconfirmed description was given: only the published file is read."""

    code = "A_DRAFT_REFUSED"


class MaskUncheckedError(RunInputsError):
    """A mask file whose representation the mask step has not checked yet was used."""

    code = "A_MASK_UNCHECKED"


class ChannelNotFoundError(RunInputsError):
    """A canonical channel ID is not one of the image's channels (never a default index)."""
