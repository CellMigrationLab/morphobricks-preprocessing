"""The run's images in the Cellpose notebook's first step, with the preprocessing image viewer.

Once **Open the run** has opened a run, its images are shown with the one image viewer of the
Preprocessing app (:class:`.notebook_viewer.ImageViewer`: condition, repeat and image dropdowns,
every channel side by side) and its one reader (:func:`.image_view.read_view`). Nothing is
guessed: each image is read with the axes and roles **the run recorded** for it, bound to the
fingerprint the run recorded (a file changed since the run is not drawn, and says so), each
channel captioned with the channel ID the run recorded at that position, and drawn as one
colour picture only for a unit the description declares ``rgb``. Display contrast only;
nothing is written.
"""

from __future__ import annotations

import html
from collections.abc import Callable
from pathlib import Path

from .axes import AxisMapping
from .fingerprint import Fingerprint
from .image_tree import ImageEntry
from .metadata import inspect_image
from .notebook_viewer import ImageViewer, ViewSource
from .run_inputs import RunInputs

NOTE = "axes and channels as the run recorded them; display contrast only"


def run_entries(inputs: RunInputs | None) -> tuple[ImageEntry, ...]:
    """Return the run's images with the labels of their folders, sorted by path."""
    if inputs is None:
        return ()
    return tuple(ImageEntry(*entry.labels, entry.relative_path) for entry in inputs.files)


def run_source(inputs: Callable[[], RunInputs | None]) -> Callable[[str], ViewSource]:
    """Return the viewer's source: how to read an image of the run ``inputs()`` holds now."""

    def source(image: str) -> ViewSource:
        run = inputs()
        if run is None:
            return ViewSource(Path(image), problem="no run is open")
        path = run.experiment_root / image
        entry = run.manifest.file(image)
        if entry is None or not entry.roles:
            return ViewSource(path, problem="the run recorded no axes for this image")
        try:
            header = inspect_image(path, image)
        except (OSError, ValueError) as exc:
            return ViewSource(path, problem=str(exc))
        if not header.readable or header.axes != entry.axes:
            return ViewSource(path, problem="its header no longer has the axes the run recorded")
        fov = next((f for f in run.fovs() if f.relative_path == image), None)
        colour = fov is not None and fov.image_type == "rgb"
        labels = () if colour else tuple(html.escape(c) for c in entry.channel_ids)
        return ViewSource(
            path,
            AxisMapping(entry.axes, tuple(entry.roles), entry.singleton_roles),
            tuple(header.shape),
            note=NOTE,
            expected=Fingerprint.recorded(entry),
            colour=colour,
            labels=labels,
        )

    return source


def run_viewer(inputs: Callable[[], RunInputs | None]) -> ImageViewer:
    """Return a viewer of the run ``inputs()`` holds (empty and hidden until one is open)."""
    return ImageViewer(run_source(inputs))


__all__ = ["NOTE", "run_entries", "run_source", "run_viewer"]
