"""Find the masks of every image and check them from their headers (``docs/contract.md`` §15).

Masks (segmentations: label images of cells, nuclei, ...) are kept in a masks folder outside
the experiment, one subfolder per mask type, each mirroring the experiment's folders::

    <masks folder>/<mask type>/<condition>/<biological repeat>/<technical repeat>/<image>.tif

A mask belongs to the image with the same position (same leaf folder, same ``position_id``,
contract §1). The type may also follow the image name instead of being a folder::

    <masks folder>/<condition>/<biological repeat>/<technical repeat>/<image>_<mask type>.tif

and the two layouts may be mixed. Its file may be a TIFF (``.tif``, ``.tiff``, any case, with
an optional ``.ome``). The label images of an OME-Zarr image (its OME-NGFF ``labels`` group)
are masks too, of the type named by the label image, and are always used when present
(design v0.4 §2).

Any number of mask types is accepted; each is named by its folder, by the suffix after the
image name, or by its label image, and two
names may not differ only in case. Only headers are read here, never pixels: a mask has the
image's Y and X sizes and integer or boolean pixels. Its other dimensions (length-1 axes
aside; Y and X are kept whatever their length, so a mask one pixel high is a 2D ``YX`` mask)
must be labelled depth (Z) or time (T) in its own metadata, or stated once per mask type
(``--mask-axes Cells=TYX``; ``Cells=YX`` for one 2D mask shared by every plane), never
guessed. Masks are never converted or reordered (design v0.4 §3), and every
mask of one image has the same depth slices and time points. Whether those are the image's
planes needs the confirmed axis roles: :func:`mask_plane_issues`, run by
:func:`.run.run_experiment` (issue #36). Reading the pixels and relating the masks to each
other is :mod:`.mask_scan`.
"""

from __future__ import annotations

from .checks import mask_plane_issues
from .files import find_masks
from .labels import LARGEST_LABEL, check_labels
from .model import FOLDER, OME_ZARR, PLANE_AXES, MaskFile, MaskInventory
from .read import read_mask, read_mask_file
from .stated import mask_axes_option

__all__ = [
    "FOLDER",
    "LARGEST_LABEL",
    "OME_ZARR",
    "PLANE_AXES",
    "MaskFile",
    "MaskInventory",
    "check_labels",
    "find_masks",
    "mask_axes_option",
    "mask_plane_issues",
    "read_mask",
    "read_mask_file",
]
