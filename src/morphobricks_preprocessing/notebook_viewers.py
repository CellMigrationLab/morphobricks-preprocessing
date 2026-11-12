"""Where the shipped notebook's steps show images: one viewer each, all the same component.

Owner (11 November 2026): every step that shows images uses the one viewer
(:class:`.notebook_viewer.ImageViewer`) with its narrowing dropdowns, instead of a list of
pictures per folder. This module only says, per step, which roles an image is read with and
where they come from:

- **Images**: the roles the image's own header names, for looking only ("from the file header,
  axes not confirmed yet"); a header that does not name Y and X, or leaves a dimension with
  several planes unnamed, gives a sentence instead of a picture (no axis is guessed). Before
  the headers are read, the one header is read for the picture and the file is fingerprinted
  before and after; after, the image is drawn only as the folder check read it.
- **Axes**: the roles being edited (the example), then the confirmed ones.
- **Channels** and **Check and save**: the confirmed roles only, each channel with its header
  name and the tag chosen for it; a folder shown as colour is one colour picture.

The stacked pages other bricks embed (``folder_step``, ``axis_step``) keep their own pictures.
"""

from __future__ import annotations

import html
from pathlib import Path

import ipywidgets as widgets

from . import notebook_style as ui
from .axes import AxisMapping, file_mapping_for, prefill_mapping
from .channel_labels import UnitLayout
from .fingerprint import Fingerprint, current_fingerprint
from .header_preview import HEADER_NOTE, NO_YX
from .image_tree import ImageEntry, entries_of
from .metadata import TiffMetadata, inspect_image, named_axes
from .notebook_channels_view import chip, quoted
from .notebook_viewer import ImageViewer, ViewSource
from .run.recorded import normalise_relative_path

NOT_BOUND = "it changed while its header was read: scan or check the folder again"
EDITED_NOTE = "read with the roles above, not confirmed yet"
CONFIRMED_NOTE = "read with the confirmed axis roles"
VIEW_HELP = (
    "Choose any image: the lists narrow each other. Each picture is stretched on its own (0.5th "
    "to 99.5th percentile, display only), so brightness cannot be compared between pictures."
)


def header_roles(meta: TiffMetadata) -> tuple[AxisMapping | None, str]:
    """Return the roles an image's own header names, or None and why no picture is drawn.

    Only a header that names Y and X, and every dimension with more than one plane, gives
    roles: nothing is guessed from the shape.
    """
    if not meta.readable:
        return None, f"the header cannot be read: {meta.error or 'unknown reason'}"
    named = named_axes(meta)
    if "Y" not in named or "X" not in named:
        return None, f"the header does not name the Y and X axes: {NO_YX}"
    mapping = prefill_mapping(meta.axes)
    open_axes = [
        f"{axis} ({length})"
        for axis, role, length in zip(meta.axes, mapping.roles, meta.shape, strict=True)
        if role is None and length > 1
    ]
    if open_axes:
        return None, (
            f"the header does not say what the dimension {', '.join(open_axes)} is: {NO_YX}"
        )
    return mapping, ""


def is_colour(meta: TiffMetadata) -> bool:
    """Return True when the header says the image is a colour (RGB) picture."""
    return bool(meta.rgb_samples) and "S" in meta.axes and meta.shape[meta.axes.index("S")] == 3


class _Viewers:
    """The viewers of the stepper's steps, and how each reads an image."""

    def _build_viewers(self) -> None:
        """Build the four viewers (hidden until they have images)."""
        self._scan_headers: dict[str, tuple[TiffMetadata, Fingerprint | None]] = {}
        self.scan_viewer = ImageViewer(self._header_source)
        self.axes_viewer = ImageViewer(self._axes_source)
        self.channels_viewer = ImageViewer(self._confirmed_source)
        self.check_viewer = ImageViewer(self._confirmed_source)
        self.axes_viewer.on_draw(self._axes_drawn)

    # Entries -------------------------------------------------------------------------------

    def _viewer_entries(self) -> tuple[ImageEntry, ...]:
        """Return the checked folder's images to analyse, with their labels."""
        pre = self.preflight
        if pre is None:
            return ()
        excluded = {normalise_relative_path(path) for path in self._excluded()}
        return entries_of(pre.discovery.leaves, excluded)

    def _refresh_confirmed_viewers(self) -> None:
        """List the images in the Channels and Check and save viewers, or empty them."""
        if getattr(self, "channels_viewer", None) is None:
            return
        entries = self._viewer_entries() if self._channels_key is not None else ()
        for viewer in (self.channels_viewer, self.check_viewer):
            if viewer.entries != entries:
                viewer.set_entries(entries)
            else:
                viewer.refresh()

    # Sources -------------------------------------------------------------------------------

    def _header_of(self, image: str) -> tuple[Path, TiffMetadata | None, Fingerprint | None]:
        """Return an image's path, header and the fingerprint bound to that header.

        After the folder check these are the check's; before it (the quick scan) the header is
        read here, once per scan, with the file fingerprinted before and after.
        """
        pre = self.preflight
        if pre is not None and self._checked_folder == self._scan_folder():
            root = Path(pre.discovery.root)
            meta = next((f for f in pre.metadata.files if f.relative_path == image), None)
            return root / image, meta, pre.header_fingerprints.get(image)
        root = Path(self._scan.root)
        if image not in self._scan_headers:
            try:
                before = current_fingerprint(root / image)
                meta = inspect_image(root / image, image)
                same = before is not None and before == current_fingerprint(root / image)
            except (OSError, ValueError) as exc:
                meta, before, same = TiffMetadata(image, False, error=str(exc)), None, False
            self._scan_headers[image] = (meta, before if same else None)
        return (root / image, *self._scan_headers[image])

    def _scan_folder(self) -> str:
        return ui.clean_path(self.folder.value)

    def _header_source(self, image: str) -> ViewSource:
        """Read an image by the roles its own header names (Images: for looking only)."""
        path, meta, bound = self._header_of(image)
        if meta is None:
            return ViewSource(path, problem="it is not among the images checked")
        mapping, why = self._scan_roles_for(meta)
        if mapping is None:
            return ViewSource(path, problem=why)
        if bound is None:
            return ViewSource(path, problem=NOT_BOUND)
        return ViewSource(
            path,
            mapping,
            tuple(meta.shape),
            note=self._scan_note(meta),
            expected=bound,
            colour=is_colour(meta),
            memory_limit=self._memory_limit(),
        )

    def _scan_roles_for(self, meta: TiffMetadata) -> tuple[AxisMapping | None, str]:
        """Return the roles the Images viewer reads an image with (its header's)."""
        return header_roles(meta)

    def _scan_note(self, meta: TiffMetadata) -> str:
        return f"axes {meta.axes} {HEADER_NOTE}"

    def _axes_source(self, image: str) -> ViewSource:
        """Read an image by the roles being edited on Axes (the example)."""
        pre = self.preflight
        path = Path(pre.discovery.root) / image
        header = next((f for f in pre.metadata.files if f.relative_path == image), None)
        try:
            mapping = self._current_mapping() if self.role_dropdowns else self.mapping
        except ValueError:
            mapping = None
        if mapping is None:
            return ViewSource(path, problem="these roles cannot be used together")
        fitted = None
        if header is not None and header.readable and mapping is not None:
            fitted = file_mapping_for(header, mapping)
        if fitted is None:
            return ViewSource(path, problem="this image does not fit these axis roles")
        bound = pre.header_fingerprints.get(image)
        if bound is None:
            return ViewSource(path, problem=NOT_BOUND)
        confirmed = self.confirmed_mapping is not None
        return ViewSource(
            path,
            fitted,
            tuple(header.shape),
            note=CONFIRMED_NOTE if confirmed else EDITED_NOTE,
            expected=bound,
            memory_limit=self._memory_limit(),
        )

    def _confirmed_source(self, image: str) -> ViewSource:
        """Read an image by the confirmed roles, its channels named and tagged."""
        pre, thumbs = self.preflight, self._channel_thumbs
        path = Path(pre.discovery.root) / image
        review = None if thumbs is None else thumbs.review
        mapping = None if review is None else review.file_mappings.get(image)
        if mapping is None or image in review.axis_error_files:
            return ViewSource(path, problem="the axis roles are not confirmed for this image")
        bound = pre.header_fingerprints.get(image)
        header = next((f for f in pre.metadata.files if f.relative_path == image), None)
        if bound is None or header is None:
            return ViewSource(path, problem=NOT_BOUND)
        layout = self._layout_of(image)
        colour = layout is not None and self.channel_types.shown(layout) == "rgb"
        return ViewSource(
            path,
            mapping,
            tuple(header.shape),
            note=CONFIRMED_NOTE,
            expected=bound,
            colour=colour,
            labels=() if colour else self._channel_captions(header, layout),
            memory_limit=self._memory_limit(),
        )

    def _layout_of(self, image: str) -> UnitLayout | None:
        """Return the imaging unit (folder) of an image as the Channels step lists it."""
        try:
            key = self.preflight.discovery.labels_for(image)
        except KeyError:
            return None
        return next((u for u in self._channel_layouts if tuple(u.key) == tuple(key)), None)

    def _channel_captions(self, header: TiffMetadata, layout: UnitLayout | None) -> tuple[str, ...]:
        """Return, per channel, the image's own header name and the tag of its folder."""
        tags: tuple = ()
        if layout is not None and layout.usable and self.channel_grid.layouts:
            tags = self.channel_grid.effective(layout)[0]
        labels = list(self.channel_labels.labels)
        count = max(len(header.channel_names), len(tags))
        captions = []
        for index in range(count):
            name = header.channel_names[index] if index < len(header.channel_names) else None
            tag = tags[index] if index < len(tags) else None
            parts = [quoted(name)] if name else []
            if tag:
                parts.append(chip(tag, labels) if tag in labels else html.escape(tag))
            captions.append(" ".join(parts))
        return tuple(captions)

    # Axes: the example is the viewer -------------------------------------------------------

    def _axes_drawn(self) -> None:
        """Record which roles the example now shows (None when it shows no picture)."""
        self._preview_drawn = self.mapping if self.axes_viewer.view is not None else None

    def _fill_preview_files(self) -> None:
        """List the images that fit the roles in the example's viewer."""
        viewer = getattr(self, "axes_viewer", None)
        if viewer is None:
            super()._fill_preview_files()
            return
        fitting = {
            f.relative_path
            for f in self.preflight.metadata.files
            if f.readable and file_mapping_for(f, self.mapping) is not None
        }
        entries = tuple(e for e in self._viewer_entries() if e.image in fitting)
        viewer.set_entries(entries, draw=False)

    def _configure_sliders(self, reset: bool) -> None:
        if getattr(self, "axes_viewer", None) is None:
            super()._configure_sliders(reset)

    def _draw_preview(self) -> None:
        """Draw the example: the viewer's image, read with the roles shown."""
        viewer = getattr(self, "axes_viewer", None)
        if viewer is None:
            super()._draw_preview()
            return
        self._preview_drawn = None
        self.preview_caption.value = ""
        if viewer.entries:
            viewer.refresh()
        else:
            self.preview_caption.value = ui.status("warn", "No image fits these axis roles.")
        self._show_log()

    def _preview_of(self, mapping: AxisMapping | None) -> bool:
        """Return whether the example now shows an image read with exactly these roles."""
        viewer = getattr(self, "axes_viewer", None)
        if viewer is None:
            return super()._preview_of(mapping)
        drawn = self._preview_drawn
        return (
            mapping is not None
            and drawn is not None
            and (drawn.axes, drawn.roles) == (mapping.axes, mapping.roles)
            and viewer.view is not None
        )

    def _cards_show_pictures(self) -> bool:
        """Return False where the step's viewer shows the images instead of the folder cards."""
        return getattr(self, "check_viewer", None) is None

    def _member_picture(self, key: tuple[str, str], detail: widgets.VBox) -> widgets.Widget:
        """Return no picture of a folder where the step's viewer shows any of its images."""
        if self._cards_show_pictures():
            return super()._member_picture(key, detail)
        return widgets.HTML("")

    # Images --------------------------------------------------------------------------------

    def _show_checked_viewer(self) -> None:
        """Show, on Images, the viewer over every image the folder check read."""
        self.scan_viewer.set_entries(self._viewer_entries(), self.scan_viewer.image)
        self.scan_example.children = [ui.help_text(VIEW_HELP), self.scan_viewer.widget]
        ui.set_visible(self.scan_tree, False)
        ui.set_visible(self.scan_box, bool(self.scan_viewer.entries))


__all__ = [
    "CONFIRMED_NOTE",
    "EDITED_NOTE",
    "NOT_BOUND",
    "VIEW_HELP",
    "_Viewers",
    "header_roles",
    "is_colour",
]
