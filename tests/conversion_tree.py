"""Shared builders of the conversion-helper tests (Spec ADR 0005 step 2b, F11 and F12)."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import numpy as np
import tifffile

import morphobricks_preprocessing.mask_conversion as conversion
from described_tree import run_described
from description_tree import MAPPING, build_experiment
from morphobricks_preprocessing import (
    RunInputs,
    __version__,
    prefill_mapping,
)
from morphobricks_preprocessing.description import description_from_bytes
from morphobricks_preprocessing.discovery import discover_experiment
from morphobricks_preprocessing.mask_lineage import (
    LINEAGE_FILENAME,
)
from morphobricks_preprocessing.masks.files import _folder_masks
from morphobricks_preprocessing.run.mask_step import describe_masks, prepare_mask_step
from spec_pin import fixtures

VALID = {item.name.split("_")[0]: item for item in fixtures("valid")}
EXTRA = {
    "TumorCopy": ("tumor_copy", "binary"),
    "Classes": ("classes", "parent_labels"),
    "ClassObjects": ("class_objects", "instances"),
}


def blobs(*boxes, dtype=np.uint8, value=1):
    """An 8 x 8 array holding ``value`` in each ``(y0, y1, x0, x1)`` box."""
    yy, xx = np.mgrid[0:8, 0:8]
    inside = np.zeros((8, 8), bool)
    for y0, y1, x0, x1 in boxes:
        inside |= (yy >= y0) & (yy < y1) & (xx >= x0) & (xx < x1)
    return np.where(inside, value, 0).astype(dtype)


APART = blobs((1, 3, 1, 3), (5, 7, 5, 7))  # F02: two disconnected blobs
NUCLEI = blobs((1, 3, 1, 3), dtype=np.uint16, value=5) + blobs(
    (5, 7, 5, 7), dtype=np.uint16, value=9
)
CLASSES = np.zeros((8, 8), np.uint16)
CLASSES[0:3, :] = 1
CLASSES[5:8, 2:6] = 2


def write(path, pixels, axes):
    path.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(path, pixels, photometric="minisblack", metadata={"axes": axes})


def description(extra=(), declared=()):
    """The ``v08`` description with extra masks declared (before ``used_ids``)."""
    text = VALID["v08"].data.decode("utf-8")
    added = "".join(
        f'[masks.{mask_id}]\nsource_type = "{EXTRA[mask_id][0]}"\n'
        f'representation = "{EXTRA[mask_id][1]}"\n\n'
        for mask_id in extra
    )
    added += "".join(
        f'[masks.{mask_id}]\nsource_type = "{source}"\nrepresentation = "{rep}"\n\n'
        for mask_id, source, rep in declared
    )
    return text.replace("[used_ids]", added + "[used_ids]").encode("utf-8")


def run_with(
    tmp_path,
    tumor=APART,
    *,
    extra=None,
    declared=(),
    t=1,
    mask_step=True,
    masks=None,
    suffix_layout=False,
):
    """A described run with one image; ``extra`` maps extra mask IDs to their pixels."""
    extra = extra or {}
    data = description(tuple(extra), declared)
    root, out = tmp_path / "exp", tmp_path / "out"
    masks = masks or tmp_path / "masks"
    out.mkdir(parents=True)
    (out / "morphobricks.toml").write_bytes(data)
    (image,) = build_experiment(root, description_from_bytes(data), 1)
    mapping, axes = MAPPING, "YX"
    if t > 1:
        roles = "T"
        mapping = prefill_mapping(roles + "CYX")
        shape = (t,)
        pixels = np.random.default_rng(1).integers(0, 200, (*shape, 2, 8, 8), dtype=np.uint8)
        write(root / image, pixels, roles + "CYX")
        axes = roles + "YX"
        tumor = np.broadcast_to(tumor, (*shape, 8, 8)).copy() if tumor.ndim == 2 else tumor
    nuclei = np.broadcast_to(NUCLEI, (*tumor.shape[:-2], 8, 8)).copy()
    write(masks / "nuclei" / image, nuclei, axes)
    if suffix_layout:
        write(masks / image.replace(".tif", "_tumor_region.tif"), tumor, axes)
    else:
        write(masks / "tumor_region" / image, tumor, axes)
    for mask_id, pixels in extra.items():
        write(masks / EXTRA[mask_id][0] / image, pixels, "YX" if pixels.ndim == 2 else axes)
    result = run_described(root, mapping, output_folder=out, masks_folder=masks)
    assert result.written, [(i.code, i.message) for i in result.issues]
    if mask_step:
        describe_masks(prepare_mask_step(None, out))
    return RunInputs.open(out), image, masks


def tree(folder):
    """Every file under ``folder`` with its bytes, for "nothing was written" checks."""
    return {
        p.relative_to(folder).as_posix(): p.read_bytes() for p in folder.rglob("*") if p.is_file()
    }


def no_reads(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("a pixel was read")

    monkeypatch.setattr(conversion, "read_mask_plane", refuse)


def independent_digest(pixels, axes):
    header = (
        json.dumps(
            {
                "axes": axes,
                "dtype": pixels.dtype.newbyteorder("<").str,
                "shape": list(pixels.shape),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        + b"\n"
    )
    return hashlib.sha256(
        header + pixels.astype(pixels.dtype.newbyteorder("<")).tobytes()
    ).hexdigest()


def lines(masks, mask_id):
    path = masks / mask_id / LINEAGE_FILENAME
    return path.read_text(encoding="utf-8") if path.exists() else None


def expected_line(source, output, operation, class_value=None):
    return {
        "source": source,
        "output": output,
        "operation": operation,
        "connectivity": 8 if operation == "binary_to_instances" else None,
        "class_value": class_value,
        "helper": f"morphobricks_preprocessing.RunInputs.{operation}",
        "helper_version": __version__,
    }


def file(mask_id, path, axes, pixels):
    return {
        "root": "masks",
        "mask_id": mask_id,
        "path": path,
        "axes": axes,
        "shape": list(pixels.shape),
        "dtype": pixels.dtype.newbyteorder("<").str,
        "content_sha256": independent_digest(pixels, axes),
    }


def unchecked(image, mask_id="TumorRegion", folder="tumor_region"):
    return (
        f"The mask {mask_id!r} is not yet checked against its declared representation for 1 of 1 "
        "files. Run the masks step on this output folder first (A_MASK_UNCHECKED)."
    )


def fail_replace(monkeypatch, name):
    real = os.replace

    def replace(src, dst, *args, **kwargs):
        if Path(dst).name == name:
            raise OSError("injected")
        return real(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "replace", replace)


def discovered(run, masks):
    discovery = discover_experiment(run.experiment_root, run.output_folder)
    found, issues = _folder_masks(masks, discovery, {e.relative_path for e in run.files})
    return {(m.image, m.mask_type) for m in found}, issues
