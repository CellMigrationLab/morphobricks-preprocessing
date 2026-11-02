"""Images and masks in the experiment that a run did not record (design v0.4 §6, PP-8b).

A brick reads files only through a clean run (:class:`.run_inputs.RunInputs`) and never
discovers them itself, so an image added after the run would be left out without a word.
:func:`unrecorded_files` lists them with preprocessing's own rules, without reading a pixel:

- images: what :func:`.discovery.discover_experiment` would analyse, less the images the run
  recorded and those it excluded. Files discovery ignores or refuses (non-image files,
  system files, images outside a leaf folder or too deep) are not listed: a run would not
  analyse them either;
- masks: the masks folder's files paired with a recorded image as
  :func:`.masks.find_masks` pairs them (masks of excluded images and files that pair with
  no image are left out, as the run leaves them out), and the label images of the recorded
  OME-Zarr images, less the masks the run recorded.
"""

from __future__ import annotations

import logging
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path

from .discovery import DiscoveryResult, discover_experiment
from .manifest import RunManifest
from .masks import FOLDER
from .masks.files import _folder_masks
from .zarr_input import NgffError, ZarrUnavailableError, is_zarr_name, ngff_label_names

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class UnrecordedFiles:
    """Files of the experiment that a run neither recorded nor excluded.

    Attributes
    ----------
    images
        Images, relative to the experiment root, sorted.
    masks
        Masks: masks-folder files relative to the masks folder, label images as
        ``<image>/labels/<name>`` (``<image>/labels`` for a labels group that cannot be
        read), sorted.
    """

    images: tuple[str, ...] = ()
    masks: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        """Return True when any file is unrecorded."""
        return bool(self.images or self.masks)


def unrecorded_files(
    manifest: RunManifest,
    experiment_root: Path,
    masks_root: Path | None,
    output_folder: Path | None = None,
) -> UnrecordedFiles:
    """List the images and masks a run did not record and did not exclude.

    Folders are listed and OME-Zarr metadata read; no pixel and no TIFF header is read.

    Parameters
    ----------
    manifest
        The run's manifest.
    experiment_root
        Where the experiment is now.
    masks_root
        Where the masks folder is now, or None (only label images are then checked).
    output_folder
        The run's output folder, never scanned as part of the experiment.

    Returns
    -------
    UnrecordedFiles
        What the run did not record; empty (false) when it recorded everything.
    """
    recorded = {entry.relative_path for entry in manifest.files}
    discovery = discover_experiment(experiment_root, output_folder)
    images = set(discovery.images) - recorded - set(manifest.settings.exclude)
    recorded_masks = {m["path"] for m in (manifest.masks or {}).get("files", ())}
    masks = _folder_mask_paths(masks_root, discovery, recorded) | _label_paths(
        experiment_root, recorded
    )
    return UnrecordedFiles(tuple(sorted(images)), tuple(sorted(masks - recorded_masks)))


def _folder_mask_paths(
    masks_root: Path | None, discovery: DiscoveryResult, recorded: Collection[str]
) -> set[str]:
    """Return the masks-folder files that pair with a recorded image, by the run's rules."""
    if masks_root is None or not masks_root.is_dir():
        return set()
    found, _ = _folder_masks(masks_root, discovery, recorded)
    return {mask.path for mask in found if mask.source == FOLDER}


def _label_paths(experiment_root: Path, recorded: Collection[str]) -> set[str]:
    """Return the label images of the recorded OME-Zarr images, as their mask paths."""
    paths = set()
    for image in recorded:
        if not is_zarr_name(image):
            continue
        try:
            names = ngff_label_names(experiment_root / image)
        except ZarrUnavailableError as exc:  # the image itself cannot be read either
            logger.info("No label images listed for %s: %s", image, exc)
            continue
        except NgffError:
            paths.add(f"{image}/labels")
            continue
        paths.update(f"{image}/labels/{name}" for name in names)
    return paths
