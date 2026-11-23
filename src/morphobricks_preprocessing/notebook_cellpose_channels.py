"""The channel step of the Cellpose notebook: the output's masks and their channels by ID.

Split from :mod:`.notebook_cellpose` (which re-exports it) to keep each file small. The
choice is only shown here; the channels are resolved by
:func:`.segmentation_handoff.resolve_segmentation`, the outputs by
:func:`.segmentation_cellpose.output_inputs`.
"""

from __future__ import annotations

import html
from dataclasses import dataclass

import ipywidgets as widgets

from . import segmentation_cellpose as cp
from .notebook_style import status
from .run_inputs import RunInputs, RunInputsError
from .segmentation_handoff import SegmentationPlan, resolve_segmentation
from .segmentation_record import SegmentationError

NONE = "(none)"
OUTPUT_NAMES = {
    cp.CELL_OUTPUT: "Cell mask",
    cp.NUCLEI_OUTPUT: "Nuclei mask",
    cp.BOTH_OUTPUT: "Both",
}
"""The outputs offered, as the notebook names them; Both makes the other two (#440)."""
REFUSALS = (SegmentationError, RunInputsError)
"""What the hand-off and the run refuse with; their messages are shown unchanged."""


def _verbatim(text: str) -> str:
    """Return ``text`` as an HTML block that keeps its line breaks and shows it unchanged."""
    return f"<pre style='white-space:pre-wrap;margin:0'>{html.escape(text)}</pre>"


def _card(kind: str, title: str, lines: tuple[str, ...] = (), *, raw: bool = False) -> widgets.HTML:
    """Return a status card as a widget, with the notebook's look."""
    return widgets.HTML(f"<div class='mb-root'>{status(kind, title, lines, raw=raw)}</div>")


@dataclass(frozen=True)
class MaskTarget:
    """One mask an output makes: what it is, the channels Cellpose reads, and their plan.

    Attributes
    ----------
    output
        ``cells`` or ``nuclei`` (the output Both makes one of each).
    channel_ids, roles
        The channel IDs in Cellpose's order and the role of each.
    plan
        The channels resolved by ID for the whole run (``resolve_segmentation``).
    """

    output: str
    channel_ids: tuple[str, ...]
    roles: tuple[str, ...]
    plan: SegmentationPlan


class ChannelPicker:
    """A cell channel and a nuclear channel, both optional, by channel ID, for one output.

    Owner, 23 November 2026: a **cell mask** reads the cell channel, then the nuclear channel
    if one is chosen; a **nuclei mask** reads the nuclear channel only
    (:func:`.segmentation_cellpose.input_channels`, which refuses a choice that does not fit).
    The roles only name and order the channels; they are resolved by ID as before. Nothing is
    chosen for the user: both start blank.

    **Both** (#440) makes a cell mask and a nuclei mask from the same two channels, each
    resolved on its own (:func:`.segmentation_cellpose.output_inputs`); it needs both.

    The options are the channel IDs of the run; one that not every image has says so, and
    choosing it shows the resolver's refusal (``A_SCOPE_INCOMPLETE``) unchanged under the
    dropdowns. :attr:`targets` holds each mask to make, resolved, or nothing while the choice is
    refused; :attr:`plan`, :attr:`channel_ids` and :attr:`channel_roles` are those of the first
    (the cell mask of Both).
    """

    def __init__(self, inputs: RunInputs, output: str = cp.CELL_OUTPUT) -> None:
        """Build the dropdowns for ``inputs`` and the ``output`` and resolve the choice."""
        self.inputs, self.output = inputs, output
        coverage = cp.channel_coverage(inputs)
        options = [
            (NONE, ""),
            *(
                (c if have == total else f"{c} (in {have} of {total} images)", c)
                for c, (have, total) in coverage.items()
            ),
        ]
        style = {"description_width": "11em"}
        self.cell = widgets.Dropdown(
            options=options, value="", description="Cell channel", style=style
        )
        self.nuclear = widgets.Dropdown(
            options=options, value="", description="Nuclear channel", style=style
        )
        self.dropdowns = [self.cell, self.nuclear]
        self.message = widgets.HTML()
        self.plan: SegmentationPlan | None = None
        self.targets: tuple[MaskTarget, ...] = ()
        self.refusal = ""
        self.channel_ids: tuple[str, ...] = ()
        self.channel_roles: tuple[str, ...] = ()
        for dropdown in self.dropdowns:
            dropdown.observe(self._changed, names="value")
        self._changed()

    def _changed(self, _change: object = None) -> None:
        """Resolve the choice; show what was resolved, or the refusal verbatim."""
        try:
            masks = cp.output_inputs(self.output, self.cell.value, self.nuclear.value)
            for _, ids, _ in masks:
                cp.check_channel_count(ids)
            self.targets = tuple(
                MaskTarget(o, ids, roles, resolve_segmentation(self.inputs, ids))
                for o, ids, roles in masks
            )
            first = self.targets[0]
            self.plan, self.channel_ids, self.channel_roles = (
                first.plan,
                first.channel_ids,
                first.roles,
            )
            self.refusal = ""
            note = _card("ok", "; ".join(map(_described, self.targets)))
        except REFUSALS as exc:
            self.plan, self.refusal, self.targets = None, str(exc), ()
            self.channel_ids = self.channel_roles = ()
            note = _card(
                "error", "These channels cannot be used", (_verbatim(self.refusal),), raw=True
            )
        self.message.value = note.value

    def current_plan(self) -> SegmentationPlan:
        """Resolve the dropdowns as they are now and return the plan, or refuse (once).

        The dropdowns may have changed since the model was loaded; every step that segments
        calls this first, so a refused choice stops the cell with the resolver's message
        before anything starts.

        Raises
        ------
        SegmentationError
            With the resolver's message, when the choice is refused.
        """
        return self.current_targets()[0].plan

    def current_targets(self) -> tuple[MaskTarget, ...]:
        """Resolve the dropdowns as they are now and return every mask to make, or refuse.

        Raises
        ------
        SegmentationError
            With the resolver's message, when the choice is refused.
        """
        self._changed()
        if not self.targets:
            raise SegmentationError(self.refusal)
        return self.targets

    def box(self) -> widgets.VBox:
        """Return the dropdowns and the message under them."""
        help_line = widgets.HTML(
            "<div class='mb-help'>Choose by channel ID. The <b>cell channel</b> (cytoplasm or "
            "cell border) and the <b>nuclear channel</b> are both optional, but at least one is "
            "needed. A cell mask reads the cell channel, then the nuclear channel if one is "
            "chosen; a nuclei mask reads the nuclear channel only; Both needs the two and makes "
            "each mask on its own.</div>"
        )
        return widgets.VBox([*self.dropdowns, help_line, self.message])


def _described(target: MaskTarget) -> str:
    """Return what Cellpose reads for ``target``: ``Cell mask from TUB (cell), DNA (nuclear)``."""
    named = ", ".join(f"{c} ({r})" for c, r in zip(target.channel_ids, target.roles, strict=True))
    return f"{OUTPUT_NAMES[target.output]} from {named}"
