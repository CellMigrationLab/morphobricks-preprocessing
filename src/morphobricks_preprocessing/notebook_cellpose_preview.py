"""The Cellpose notebook's example result, as the ZeroCostDL4Mic Cellpose 2D notebook shows it.

ZeroCostDL4Mic (``Cellpose_2D_ZeroCostDL4Mic.ipynb``, section 6.1) segments one chosen image and
draws ``cellpose.plot.show_segmentation(fig, image, masks, flows[0], channels=...)``: the
input, the predicted outlines, the predicted masks and the predicted flows (cell pose). The
preview here does the same with Cellpose's own function (4.2.1.1 has that signature unchanged),
after one display panel per chosen channel labelled with its channel ID and role (the display
contrast only: the stack and the masks are never changed). Nothing is written.

A preview holds a list of :class:`MaskResult`: one now, a cell mask or a nuclei mask; the
*Both* output (#440) adds a second result and the figures follow, with no change here.

The figures are shown as PNG pictures in widgets (:func:`preview_images`), never displayed into
an output area: the preview's box then holds the last Preview's result only, in every frontend
(owner, 26 November 2026: in Colab, nuclei figures of the first Preview stayed on screen after
later ones).
"""

from __future__ import annotations

import importlib
import io
from dataclasses import dataclass

import ipywidgets as widgets
import numpy as np
from matplotlib.figure import Figure

from .preview_render import display_panel


@dataclass(frozen=True)
class MaskResult:
    """One mask of a preview: its name, the labels and Cellpose's flows.

    Attributes
    ----------
    name
        What the mask is, e.g. ``"Cell mask"``.
    stack
        The ``(C, Y, X)`` stack Cellpose got for it (the chosen channels, in Cellpose's order).
    channels
        ``(channel ID, role)`` for each plane of ``stack``.
    labels
        The 2D label image (0 is background).
    flows
        Cellpose's flows (``flows[0]`` is the RGB flow image), or None.
    """

    name: str
    stack: np.ndarray
    channels: tuple[tuple[str, str], ...]
    labels: np.ndarray
    flows: object

    @property
    def objects(self) -> int:
        """The number of objects (distinct labels above 0)."""
        return int(np.unique(self.labels[self.labels > 0]).size)


def channel_figure(result: MaskResult) -> Figure:
    """Return one display panel per channel, titled with its channel ID and role."""
    figure = Figure(figsize=(4 * len(result.channels), 4))
    axes = figure.subplots(1, len(result.channels), squeeze=False)[0]
    for axis, plane, (channel, role) in zip(axes, result.stack, result.channels, strict=True):
        axis.imshow(display_panel(np.asarray(plane)).pixels, cmap="gray", vmin=0, vmax=255)
        axis.set_title(f"{channel} ({role}, display contrast)")
        axis.axis("off")
    figure.tight_layout()
    return figure


def segmentation_figure(result: MaskResult) -> Figure:
    """Return Cellpose's own four panels: input, outlines, masks and flows (cell pose).

    ``channels`` is what ZeroCostDL4Mic passes: ``[0, 0]`` (grey) for one channel, ``[1, 2]``
    (the cell channel red, the nuclear channel green) for two.

    Raises
    ------
    ValueError
        When Cellpose returned no flows to draw.
    """
    if result.flows is None or len(result.flows) == 0:
        raise ValueError("Cellpose returned no flows for this image: the preview cannot draw them.")
    plot = importlib.import_module("cellpose.plot")
    figure = Figure(figsize=(16, 4.5))
    channels = [0, 0] if len(result.channels) == 1 else [1, 2]
    stack, flow = np.asarray(result.stack), result.flows[0]
    plot.show_segmentation(figure, stack, result.labels, flow, channels)
    figure.suptitle(f"{result.name}: {result.objects} objects")
    figure.tight_layout()
    return figure


def preview_figures(results: list[MaskResult]) -> list[Figure]:
    """Return the preview's figures: the channels, then Cellpose's panels, for each result."""
    return [figure for r in results for figure in (channel_figure(r), segmentation_figure(r))]


def figure_image(figure: Figure) -> widgets.Image:
    """Return ``figure`` as a PNG picture widget, at most 99% of its box wide.."""
    buffer = io.BytesIO()
    figure.savefig(buffer, format="png", bbox_inches="tight")
    layout = widgets.Layout(max_width="99%", height="auto")
    return widgets.Image(value=buffer.getvalue(), format="png", layout=layout)


def preview_images(results: list[MaskResult]) -> list[widgets.Image]:
    """Return the figures of :func:`preview_figures` as picture widgets, in the same order."""
    return [figure_image(figure) for figure in preview_figures(results)]
