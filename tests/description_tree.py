"""Build the experiment folder and masks of a description, for the run-integration tests.

One image per unit by default, ``CYX`` with as many channels as the unit declares and no
header names (so no confirmed alias is needed); a unit named ``single`` has no folder level.
Masks go under ``<masks>/<source type>/<image path>`` as 2-D label images.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import tifffile

from morphobricks_preprocessing import prefill_mapping
from morphobricks_preprocessing.testing_described import unit_folder


def write_image(path, channels, seed=1):
    """Write a small ``CYX`` image whose header names no channel."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.random.default_rng(seed).integers(0, 200, (channels, 8, 8), dtype=np.uint8)
    tifffile.imwrite(path, data, photometric="minisblack", metadata={"axes": "CYX"})


def build_experiment(root, description, images=2):
    """Write ``images`` images per declared unit; return their relative paths, sorted."""
    found = []
    for repeat_id, repeat in description.repeats.items():
        for condition_id, member in repeat.members.items():
            for unit in member.technical_repeats.values():
                folder = unit_folder(description, repeat_id, condition_id, unit)
                for index in range(images):
                    rel = folder / f"img{index}.tif"
                    write_image(Path(root) / rel, len(unit.channels), seed=len(found) + 1)
                    found.append(rel.as_posix())
    return sorted(found)


def write_mask(masks_root, mask_type, image, values):
    """Write the 2-D mask of ``image`` (a relative path) holding the given values."""
    path = Path(masks_root) / mask_type / image
    path.parent.mkdir(parents=True, exist_ok=True)
    pixels = np.resize(np.array(values, dtype=np.uint8), (8, 8))
    tifffile.imwrite(path, pixels, photometric="minisblack", metadata={"axes": "YX"})
    return path


MAPPING = prefill_mapping("CYX")


def two_unnamed_units(v12: bytes) -> bytes:
    """Return ``v12`` with a second unit, DrugA/rep1, of the same unnamed DNA, ACTIN layout.

    Conformance P06 asks for two unnamed, identical-shape units; ``v12`` declares one. The
    second is added as the file format writes it, and DrugA is listed in ``used_ids``.
    """
    text = v12.decode("utf-8")
    condition = '[conditions.DrugA]\nsource_folder = "DrugA"\nreference = "CTRL"\n\n'
    member = (
        '[repeats.rep1.members.DrugA]\nsource_repeat = "rep1"\nimage_count = 4\n\n'
        '[repeats.rep1.members.DrugA.technical_repeats.single]\nsource_folder = "single"\n'
        'channels = ["DNA", "ACTIN"]\n\n'
    )
    for old, new in (
        ("[channels.DNA]", condition + "[channels.DNA]"),
        ("[used_ids]", member + "[used_ids]"),
        ('conditions = ["CTRL"]', 'conditions = ["CTRL", "DrugA"]'),
    ):
        assert text.count(old) == 1, old
        text = text.replace(old, new)
    return text.encode("utf-8")


def write_fluorophores(path, *, swapped, side=8):
    """Write an unnamed ``CYX`` image: a nucleus-like disc then actin-like stripes.

    With ``swapped`` the two planes are stored the other way round, as when the fluorophores
    of a unit were physically exchanged. The header names no channel either way.
    """
    yy, xx = np.mgrid[0:side, 0:side]
    nuclei = ((yy - side // 2) ** 2 + (xx - side // 2) ** 2 < 4).astype(np.uint8) * 200
    actin = (xx % 2 == 0).astype(np.uint8) * 60
    planes = [actin, nuclei] if swapped else [nuclei, actin]
    path.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(path, np.stack(planes), photometric="minisblack", metadata={"axes": "CYX"})
    return path
