"""MaskInventory lookups are indexed once, not scanned per image (issue #70)."""

import pytest


class _CountingFiles(tuple):
    """A tuple of masks that counts how often it is iterated."""

    iterations = 0

    def __iter__(self):
        type(self).iterations += 1
        return super().__iter__()


@pytest.mark.parametrize("n_images", [100, 1_000, 10_000])
def test_inventory_lookup_does_not_traverse_the_files(n_images):
    from morphobricks_preprocessing.masks.model import FOLDER, MaskFile, MaskInventory

    kinds = ("Cells", "Nuclei")
    files = _CountingFiles(
        MaskFile(f"img{i:05d}.tif", kind, f"{kind}/img{i:05d}.tif", FOLDER, (4, 4))
        for i in range(n_images)
        for kind in kinds
    )
    found = MaskInventory(folder=None, types=kinds, files=files)
    _CountingFiles.iterations = 0  # building may index once; lookups never traverse
    assert found.images[0] == "img00000.tif" and len(found.images) == n_images
    for i in range(n_images):
        by_type = found.for_image(f"img{i:05d}.tif")
        assert list(by_type) == list(kinds)
        assert by_type["Nuclei"].path == f"Nuclei/img{i:05d}.tif"
    assert found.for_image("missing.tif") == {}
    assert _CountingFiles.iterations == 0


def test_inventory_lookup_is_read_only():
    from morphobricks_preprocessing.masks.model import FOLDER, MaskFile, MaskInventory

    mask = MaskFile("a.tif", "Cells", "Cells/a.tif", FOLDER)
    found = MaskInventory(folder=None, types=("Cells",), files=(mask,))
    with pytest.raises(TypeError):
        found.for_image("a.tif")["Nuclei"] = mask  # type: ignore[index]
    with pytest.raises(TypeError):
        found.for_image("missing.tif")["Nuclei"] = mask  # type: ignore[index]
