"""*Images found*: the labels table with a picture of each folder's first image (PR 17c).

Each folder of the labels table gets the picture of its first image by path, drawn from the
image header alone (:mod:`.header_preview`): only when the header names Y and X, every other
axis max-projected and named, colour when the header says RGB, and a crop at native pixels when
the thumbnail loses detail (:class:`.notebook_thumb_tile.ThumbTile`). It is display only, so a
user sees at once that the folder is the wrong one; the caption says "from the file header, axes
not confirmed yet". A picture that cannot be drawn says why in its cell and never stops the
check.
"""

from __future__ import annotations

import html
from collections.abc import Callable, Mapping
from pathlib import Path

import ipywidgets as widgets

from . import notebook_style as ui
from .discovery import LeafFolder
from .fingerprint import Fingerprint
from .header_preview import HEADER_NOTE, HeaderPreview, header_preview
from .metadata import TiffMetadata
from .notebook_render import label_cells
from .notebook_thumb_tile import ThumbTile, show_detail
from .preview import DETAIL_SIDE
from .run import Preflight
from .validation import plural

LABELS = "minmax(0,1fr) minmax(0,1fr) minmax(0,1fr) 56px minmax(0,1.3fr)"
COLUMNS = "minmax(0,1fr) 340px"
HEADERS = ("Condition", "Biological repeat", "Technical repeat", "Images", "Folder")
DRAWN_AT_ONCE = 12
"""Folders whose pictures the check draws; the others wait for one click (a big experiment)."""
NOT_DRAWN = "picture not drawn yet: press Draw the pictures below the table"


def first_headers(pre: Preflight) -> dict[str, TiffMetadata | None]:
    """Return the header of each folder's first image to analyse, by path (None without one)."""
    headers = {m.relative_path: m for m in pre.metadata.files}
    return {
        leaf.relative_path: next((headers[i] for i in leaf.images if i in headers), None)
        for leaf in pre.discovery.leaves
    }


def _caption(found: HeaderPreview) -> str:
    return f"{found.image}: {found.text}; {HEADER_NOTE}"


def _cells(cells: list[str] | tuple[str, ...]) -> str:
    """Return a folder's label cells as one HTML grid (one widget per row, not five)."""
    inner = "".join(f"<div style='overflow-wrap:anywhere'>{c}</div>" for c in cells)
    return (
        f"<div style='display:grid;grid-template-columns:{LABELS};gap:4px 10px;"
        f"align-items:center'>{inner}</div>"
    )


def _waiting_row(leaf: LeafFolder) -> str:
    """Return a folder's row before its picture is drawn, as HTML laid out like the others."""
    return (
        f"<div style='display:grid;grid-template-columns:{COLUMNS};gap:4px 10px;"
        "align-items:center;border-bottom:1px solid #E2E8F0;padding:4px 6px'>"
        f"{_cells(label_cells(leaf))}<span class='mb-help'>{NOT_DRAWN}</span></div>"
    )


class LabelPreviews:
    """The labels table with one header picture per folder, and the enlarged view under it.

    The check draws the pictures of the first :data:`DRAWN_AT_ONCE` folders; on a bigger
    experiment the others are drawn by **Draw the other pictures**, so the check stays quick.
    """

    def __init__(self) -> None:
        self.rows = widgets.VBox(layout=widgets.Layout(max_height="960px", overflow="auto"))
        self.detail = widgets.VBox()
        self.more = ui.button(" Draw the other pictures", icon="image")
        self.more.on_click(lambda _: self.draw_all())
        ui.set_visible(self.more, False)
        self.found: dict[str, HeaderPreview] = {}
        self.tiles: dict[str, ThumbTile] = {}
        self._slots: dict[str, widgets.Box] = {}
        self._waiting: list[LeafFolder] = []
        self._leaves: dict[str, LeafFolder] = {}
        self._root: Path | None = None
        self._headers: Mapping[str, TiffMetadata | None] = {}
        self._bound: Mapping[str, Fingerprint | None] = {}
        self.widget = widgets.VBox([self.rows, self.more, self.detail])

    def clear(self) -> None:
        """Forget the pictures (another folder is checked)."""
        self.rows.children, self.detail.children = [], []
        self.found, self.tiles, self._slots, self._leaves = {}, {}, {}, {}
        self._waiting = []
        ui.set_visible(self.more, False)

    def show(self, pre: Preflight, progress: Callable[[int, int, str], None] | None = None) -> None:
        """Draw the table of a new check: labels, image count, folder and picture per folder.

        ``progress(done, total, folder)`` is called before each picture is drawn.
        """
        self.clear()
        self._root, self._headers = Path(pre.discovery.root), first_headers(pre)
        self._bound = pre.header_fingerprints  # drawn only as the check read them
        header = widgets.HTML(_cells([f"<b>{h}</b>" for h in HEADERS]))
        top = widgets.GridBox([header, widgets.HTML("<b>Preview</b>")], layout=self._grid())
        leaves = pre.discovery.leaves
        rows = [self._row(leaf) for leaf in leaves[:DRAWN_AT_ONCE]]
        for done, leaf in enumerate(leaves[:DRAWN_AT_ONCE]):
            if progress is not None:
                progress(done, min(len(leaves), DRAWN_AT_ONCE), leaf.relative_path)
            self.draw(leaf.relative_path)
        # The other folders are one HTML block until drawn: a widget per row costs time.
        self._waiting = list(leaves[DRAWN_AT_ONCE:])
        rest = "".join(_waiting_row(leaf) for leaf in self._waiting)
        block = widgets.HTML(rest, layout=widgets.Layout(flex="0 0 auto"))
        self.rows.children = [top, *rows, *([block] if rest else [])]
        others = plural(len(self._waiting), "folder")
        self.more.description = f" Draw the pictures of the other {others}"
        ui.set_visible(self.more, bool(self._waiting))

    @staticmethod
    def _grid() -> widgets.Layout:
        return widgets.Layout(
            grid_template_columns=COLUMNS,
            grid_gap="4px 10px",
            align_items="center",
            border_bottom="1px solid #E2E8F0",
            padding="4px 6px",
            width="99%",
            flex="0 0 auto",  # a row keeps its height in the scrolling table
        )

    def _row(self, leaf: LeafFolder) -> widgets.Widget:
        """Build one folder's row: its labels, image count and folder; the picture comes later."""
        slot = widgets.Box()
        self._slots[leaf.relative_path], self._leaves[leaf.relative_path] = slot, leaf
        return widgets.GridBox([widgets.HTML(_cells(label_cells(leaf))), slot], layout=self._grid())

    def draw(self, folder: str) -> None:
        """Draw one folder's picture into its row (read once)."""
        if folder in self.tiles:
            return
        found = header_preview(self._root, self._headers[folder], bound=self._bound)
        self.found[folder] = found
        leaf = self._leaves[folder]
        tile = ThumbTile(
            found.pictures,
            caption=_caption(found) if found.pictures is not None else "",
            problem=found.problem,
            colour=found.colour,
            enlarge=None if found.pictures is None else lambda f=leaf: self.enlarge(f),
        )
        self.tiles[folder] = tile
        self._slots[folder].children = [tile.widget]

    def draw_all(self) -> None:
        """Draw the pictures not drawn yet (the user's click on a big experiment)."""
        if not self._waiting:
            return
        rows = [self._row(leaf) for leaf in self._waiting]
        for leaf in self._waiting:
            self.draw(leaf.relative_path)
        self.rows.children = [*self.rows.children[:-1], *rows]
        self._waiting = []
        ui.set_visible(self.more, False)

    def enlarge(self, leaf: LeafFolder) -> None:
        """Show one folder's picture at the detail size under the table (read again)."""
        found = header_preview(
            self._root, self._headers[leaf.relative_path], DETAIL_SIDE, bound=self._bound
        )
        folder = "experiment folder" if leaf.relative_path == "." else leaf.relative_path
        title = f"<b>{html.escape(folder)}</b> · {html.escape(_caption(found))}"
        show_detail(self.detail, title, found.pictures, found.problem)
        ui.set_visible(self.detail, True)


__all__ = ["DRAWN_AT_ONCE", "LabelPreviews", "first_headers"]
