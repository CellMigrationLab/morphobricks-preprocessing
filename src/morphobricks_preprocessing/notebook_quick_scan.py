"""The quick scan of the shipped notebook's first step: look before every header is read.

Owner decision (10 November 2026): the first step is *choose the folders*, then a **quick
scan** that only walks the folders (the same walk as the folder check: labels and image counts,
no image header is read) and opens **one** image chosen at random, so the user sees that this is
the right experiment before waiting for every header. Only then the explicit click **Looks
right: read all image headers** starts the folder check (the "deep scan"), with its progress.

The random image is for looking only. Its path and the seed that chose it are shown; it is drawn
from its own header by the rules of the folder pictures (:mod:`.header_preview`: only when the
header names Y and X, bound to the file as read). When that header names a channel axis, every
channel is drawn side by side across the page, each with its own contrast and its ``c=`` index,
up to :data:`.header_preview.CHANNELS_SHOWN`; with more, the line under them says "6 of N
channels shown" (owner, 10 November 2026). Nothing of the quick scan is kept: no label,
axis, header or picture of it feeds the check, the draft or the run. Changing a folder makes the
scan stale, and the check is not offered until the folders are scanned again.

The page is one column (owner, same day): the picture row, then the conditions found in a short
table, with every folder one click away.
"""

from __future__ import annotations

import html
import random
import secrets
from pathlib import Path

import ipywidgets as widgets

from . import notebook_style as ui
from .discovery import DiscoveryResult, discover_experiment
from .fingerprint import current_fingerprint
from .header_preview import (
    HEADER_NOTE,
    READ_FAILURES,
    HeaderChannels,
    HeaderPreview,
    PreviewUnavailable,
    header_channels,
    header_preview,
    read_failure,
)
from .metadata import inspect_image
from .notebook_progress import Wait
from .notebook_render import blocked_line, issues_html
from .notebook_thumb_tile import ThumbTile
from .run.recorded import normalise_relative_path
from .validation import plural

SCAN_LABEL, DEEP_LABEL = " Scan the folders", " Looks right: read all image headers"
SEED_RANGE = 1_000_000
EXAMPLE_SIDE = 190
"""The largest side of the quick scan's picture, in pixels."""


def tree_html(discovery: DiscoveryResult, excluded: frozenset[str]) -> str:
    """Return the folders found as a table: labels and image count per folder (HTML)."""
    rows = [("Condition", "Biological repeat", "Technical repeat", "Images")]
    for leaf in discovery.leaves:
        count = sum(image not in excluded for image in leaf.images)
        rows.append((*leaf.labels, f"{count:,}"))
    return ui.table(rows, numeric_from=3)


def scan_summary(discovery: DiscoveryResult, excluded: frozenset[str]) -> str:
    """Return one line of counts: images, folders, conditions and biological repeats."""
    leaves = discovery.leaves
    images = sum(image not in excluded for image in discovery.images)
    conditions = {leaf.condition for leaf in leaves}
    repeats = {(leaf.condition, leaf.biological_repeat) for leaf in leaves}
    parts = [
        plural(images, "image"),
        plural(len(leaves), "folder"),
        plural(len(conditions), "condition"),
        plural(len(repeats), "biological repeat"),
    ]
    left_out = len(discovery.images) - images
    if left_out:
        parts.append(f"{plural(left_out, 'image')} excluded")
    return " · ".join(parts)


def conditions_html(discovery: DiscoveryResult, excluded: frozenset[str]) -> str:
    """Return the folders found in short: one row per condition, every folder on demand (HTML)."""
    rows = [("Condition", "Biological repeats", "Folders", "Images")]
    by_condition: dict[str, list] = {}
    for leaf in discovery.leaves:
        by_condition.setdefault(leaf.condition, []).append(leaf)
    for condition, leaves in by_condition.items():
        repeats = list(dict.fromkeys(leaf.biological_repeat for leaf in leaves))
        count = sum(image not in excluded for leaf in leaves for image in leaf.images)
        rows.append((condition, ", ".join(repeats), f"{len(leaves):,}", f"{count:,}"))
    every = (
        f"<details class='mb-about'><summary>All {plural(len(discovery.leaves), 'folder')}"
        f"</summary>{tree_html(discovery, excluded)}</details>"
    )
    return ui.table(rows, numeric_from=2) + every


def random_image(images: list[str], seed: int) -> str | None:
    """Return the image the seed chooses among ``images`` (sorted first), None without one."""
    return random.Random(seed).choice(sorted(images)) if images else None


def example_preview(root: Path, image: str) -> HeaderChannels | HeaderPreview:
    """Return the pictures of one image from its own header, or why there is none.

    The header is read here, for these pictures only, and the file is fingerprinted before and
    after, so they are of the file as that header describes it. A header that names a channel
    axis gives one picture per channel (:func:`.header_preview.header_channels`); any other
    gives the one picture of the folder check (:func:`.header_preview.header_preview`).
    """
    path = root / image
    try:
        before = current_fingerprint(path)
        meta = inspect_image(path, image)
        after = current_fingerprint(path)
    except (OSError, ValueError) as exc:
        return HeaderPreview(image, problem=read_failure(exc, root))
    same = before is not None and before == after
    if same:
        try:
            channels = header_channels(path, meta, EXAMPLE_SIDE)
        except PreviewUnavailable:
            channels = None  # no channel axis named, or over the caps: the one picture says why
        except READ_FAILURES as exc:
            return HeaderPreview(image, problem=read_failure(exc, root))
        if channels is not None and current_fingerprint(path) == before:
            return channels
    return header_preview(root, meta, EXAMPLE_SIDE, bound={image: before if same else None})


def channel_tiles(found: HeaderChannels) -> list[widgets.Widget]:
    """Return the row of channel pictures (``c=`` labels) and the line under it."""
    tiles = []
    for tile in found.tiles:
        label = f"<b>c={tile.c}</b>" + (f" · {html.escape(tile.note)}" if tile.note else "")
        image = widgets.Image(
            value=tile.png, format="png", layout=widgets.Layout(border="1px solid #CBD5E1")
        )
        image.add_class("mb-grey")
        box = widgets.VBox(
            [widgets.HTML(f"<div class='mb-help'>{label}</div>"), image],
            layout=widgets.Layout(flex="1 1 0", min_width="0", max_width=f"{EXAMPLE_SIDE}px"),
        )
        box.add_class("mb-channel")
        tiles.append(box)
    shown = (
        f"all {found.total} channels"
        if len(found.tiles) == found.total
        else f"<b>{len(found.tiles)} of {found.total} channels shown</b>"
    )
    row = widgets.HBox(tiles, layout=widgets.Layout(width="99%"))
    row.add_class("mb-channel-row")
    note = f"{shown}, each with its own contrast; {html.escape(found.text)}; {HEADER_NOTE}"
    return [row, widgets.HTML(f"<div class='mb-help'>{note}</div>")]


class _QuickScan:
    """The quick scan card of the stepper's first step, and the gate of the folder check."""

    def _build_quick_scan(self) -> None:
        """Build the scan button, its results and the line saying what blocks the check."""
        self._scanned: tuple | None = None
        self._scan: DiscoveryResult | None = None
        self.scan_seed: int | None = None
        self.scan_image: str | None = None
        self.scan_button = ui.button(
            SCAN_LABEL,
            style="primary",
            icon="search",
            tooltip="List the folders and show one image; no image header is read",
            main=True,
        )
        self.scan_status = widgets.HTML()
        self.scan_tree = widgets.HTML()
        self.scan_example = widgets.VBox()
        self.scan_another = ui.button(
            " Show another image", icon="random", tooltip="Choose another image at random"
        )
        self.scan_box = widgets.VBox(  # one column: the pictures, then the folders
            [self.scan_example, self.scan_another, self.scan_tree],
            layout=widgets.Layout(width="99%"),
        )
        self.scan_box.add_class("mb-scan")
        ui.set_visible(self.scan_box, False)
        self.check_blocker = widgets.HTML()
        self.scan_button.on_click(lambda _: self._quick_scan())
        self.scan_another.on_click(lambda _: self._show_scan_example(None))
        self._check_label = (DEEP_LABEL, "check")
        self.check_button.description, self.check_button.icon = self._check_label
        self.check_button.button_style = "success"
        self.check_button.tooltip = "Read every image header and check the experiment"
        for box in (self.folder, self.output, self.masks_folder, self.exclude):
            box.observe(lambda _: self._refresh_scan_gate(), names="value")
        self._refresh_scan_gate()

    def _scan_inputs(self) -> tuple:
        """Return the folders and exclusions as typed now (what a scan is a scan of)."""
        return (
            ui.clean_path(self.folder.value),
            self._output_folder(),
            ui.clean_path(self.masks_folder.value),
            tuple(self._excluded()),
        )

    def _scan_current(self) -> bool:
        """Return whether the folders shown were scanned as they are typed now."""
        return self._scanned is not None and self._scanned == self._scan_inputs()

    def _refresh_scan_gate(self) -> None:
        """Offer the folder check only for folders scanned as typed; say what blocks it."""
        current = self._scan_current()
        if self._scanned is not None and not current:
            ui.set_visible(self.scan_box, False)  # the tree and picture are of other folders
            self.scan_status.value = ui.status(
                "info", "The folders changed.", ["Scan the folders again."]
            )
        usable = current and self._scan is not None and bool(self._scan.images)
        self.check_button.disabled = not usable
        if usable:
            reasons: list[str] = []
        elif not ui.clean_path(self.folder.value):
            reasons = ["no experiment folder is given"]
        elif not current:
            reasons = ["the folders are not scanned as they are now: click Scan the folders"]
        else:
            reasons = ["the scan found no image to read"]
        self.check_blocker.value = blocked_line(reasons)

    def _quick_scan(self) -> DiscoveryResult | None:
        """Walk the folders (no header is read), show the tree and one random image.

        Returns
        -------
        DiscoveryResult or None
            What the walk found; None when no experiment folder is given.
        """
        folder = ui.clean_path(self.folder.value)
        if not folder:
            self.scan_status.value = ui.status(
                "warn", "Enter the experiment folder first.", ["Paste its path or use Browse…."]
            )
            return None
        self._busy(self.scan_button, "Scanning…")
        wait = Wait(self.scan_status)
        wait.start("Listing the folders")
        seen = [0]

        def listed(path: str) -> None:
            seen[0] += 1
            wait.step(seen[0], 0, path)

        try:
            with self.log.attached():
                found = discover_experiment(folder, self._output_folder(), progress=listed)
        finally:
            self._idle(self.scan_button, SCAN_LABEL, "search")
        self._scan, self._scanned = found, self._scan_inputs()
        wait.phase("Opening one image")
        self._show_scan_example(None, status=False)
        excluded = frozenset(normalise_relative_path(path) for path in self._excluded())
        self.scan_tree.value = conditions_html(found, excluded)
        errors = [issue for issue in found.issues if issue.is_error]
        self.scan_status.value = ui.status(
            "error" if errors else "ok" if found.images else "warn",
            f"Found {scan_summary(found, excluded)} (listed in {wait.took()}). No image header "
            "was read yet: if this is your experiment, read the headers below.",
        ) + (issues_html(errors) if errors else "")
        ui.set_visible(self.scan_box, bool(found.leaves))
        self._refresh_scan_gate()
        self._show_log()
        return found

    def _show_scan_example(self, seed: int | None, *, status: bool = True) -> None:
        """Show one image chosen at random by ``seed`` (a new seed when None), display only."""
        found = self._scan
        if found is None:
            return
        excluded = {normalise_relative_path(path) for path in self._excluded()}
        images = [image for image in found.images if image not in excluded]
        self.scan_seed = secrets.randbelow(SEED_RANGE) if seed is None else seed
        self.scan_image = random_image(images, self.scan_seed)
        ui.set_visible(self.scan_another, len(images) > 1)
        if self.scan_image is None:
            self.scan_example.children = []
            return
        if status:
            self._busy(self.scan_another, "Opening…")
        try:
            preview = example_preview(Path(found.root), self.scan_image)
        finally:
            if status:
                self._idle(self.scan_another, " Show another image", "random")
        chosen = widgets.HTML(
            f"<div class='mb-help'>One image chosen at random (seed {self.scan_seed}), for "
            f"looking only: <b>{html.escape(self.scan_image)}</b></div>"
        )
        if isinstance(preview, HeaderChannels):
            self.scan_example.children = [chosen, *channel_tiles(preview)]
            return
        tile = ThumbTile(
            preview.pictures,
            caption=f"{preview.text}; {HEADER_NOTE}" if preview.pictures is not None else "",
            problem=preview.problem,
            colour=preview.colour,
            width=f"{EXAMPLE_SIDE}px",
        )
        self.scan_example.children = [chosen, tile.widget]


__all__ = [
    "DEEP_LABEL",
    "SCAN_LABEL",
    "_QuickScan",
    "channel_tiles",
    "conditions_html",
    "example_preview",
    "random_image",
    "scan_summary",
    "tree_html",
]
