import numpy as np
import pytest

from morphobricks_preprocessing import NgffError, ngff_label_names, open_ngff, open_ngff_labels
from zarr_tree import write_bioformats2raw, write_labels, write_ngff

pytest.importorskip("zarr")

VERSIONS = ["0.4", "0.5"]


def labels_of(shape, value):
    data = np.zeros(shape, dtype=np.uint16)
    data[..., 1:3, 1:3] = value
    return data


@pytest.mark.parametrize("version", VERSIONS)
def test_label_images_are_listed_and_opened_by_name(tmp_path, version):
    image = np.zeros((2, 4, 5), dtype=np.uint8)
    path = write_ngff(tmp_path / "a.ome.zarr", image, "zyx", version=version)
    cells, nuclei = labels_of((2, 4, 5), 1), labels_of((1, 4, 5), 2)
    write_labels(path, {"cells": cells, "nuclei": nuclei}, "zyx", version=version)

    assert ngff_label_names(path) == ("cells", "nuclei")
    opened = open_ngff_labels(path, "nuclei")
    assert (opened.axes, opened.shape, opened.dataset_path) == ("ZYX", (1, 4, 5), "labels/nuclei/0")
    np.testing.assert_array_equal(np.asarray(opened.array[...]), nuclei)
    # The image itself is opened as before.
    assert open_ngff(path).shape == (2, 4, 5)


def test_a_single_label_image_may_be_opened_without_a_name(tmp_path):
    path = write_ngff(tmp_path / "a.ome.zarr", np.zeros((4, 5), dtype=np.uint8), "yx")
    write_labels(path, {"cells": labels_of((4, 5), 3)}, "yx")
    assert open_ngff_labels(path).dataset_path == "labels/cells/0"


def test_label_name_errors(tmp_path):
    path = write_ngff(tmp_path / "a.ome.zarr", np.zeros((4, 5), dtype=np.uint8), "yx")
    assert ngff_label_names(path) == ()
    with pytest.raises(NgffError, match="no label images"):
        open_ngff_labels(path)

    write_labels(path, {"cells": labels_of((4, 5), 1), "nuclei": labels_of((4, 5), 2)}, "yx")
    with pytest.raises(NgffError, match="several label images \\(cells, nuclei\\)"):
        open_ngff_labels(path)
    with pytest.raises(NgffError, match="no label image 'membranes'"):
        open_ngff_labels(path, "membranes")


def test_labels_of_the_first_image_in_a_bioformats2raw_folder(tmp_path):
    path = write_bioformats2raw(tmp_path / "a.ome.zarr", [np.zeros((2, 4, 5), dtype=np.uint8)])
    write_labels(path / "0", {"cells": labels_of((2, 4, 5), 1)}, "zyx")
    assert ngff_label_names(path) == ("cells",)
    assert open_ngff_labels(path, "cells").dataset_path == "0/labels/cells/0"
