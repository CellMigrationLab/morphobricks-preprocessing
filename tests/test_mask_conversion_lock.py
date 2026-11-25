"""A mask conversion takes the type's write lock, as the Cellpose writer does (#382)."""

from __future__ import annotations

import pytest

from conversion_tree import blobs, lines, run_with, tree
from morphobricks_preprocessing import mask_conversion
from morphobricks_preprocessing.segmentation_record import SegmentationError

LOCK = ".segmentation_write.lock"


def test_a_held_type_lock_refuses_the_conversion_naming_it(tmp_path):
    run, image, masks = run_with(tmp_path)
    lock = masks / "TumorObjects" / LOCK
    lock.parent.mkdir()
    lock.write_text("{}", encoding="utf-8")  # another writer holds it
    before = tree(masks)
    with pytest.raises(SegmentationError, match=r"\.segmentation_write\.lock exists"):
        run.binary_to_instances(image, "TumorRegion", output_mask_id="TumorObjects")
    assert tree(masks) == before  # nothing written, the other writer's lock kept


def test_a_conversion_during_another_into_the_same_type_is_refused(tmp_path, monkeypatch):
    run, image, masks = run_with(tmp_path)
    publish = mask_conversion.publish_output
    seen = []

    def interleaved(*args, **kwargs):
        if not seen:  # a second writer starts while the first publishes
            seen.append(True)
            with pytest.raises(SegmentationError, match="is being written by"):
                run.binary_to_instances(image, "TumorRegion", output_mask_id="TumorObjects")
        return publish(*args, **kwargs)

    monkeypatch.setattr(mask_conversion, "publish_output", interleaved)
    result = run.binary_to_instances(image, "TumorRegion", output_mask_id="TumorObjects")
    assert seen and result.outputs[0].published
    assert len(lines(masks, "TumorObjects").splitlines()) == 1  # one line, the first writer's
    assert not (masks / "TumorObjects" / LOCK).exists()  # released at the end
    again = run.binary_to_instances(image, "TumorRegion", output_mask_id="TumorObjects")
    assert not again.outputs[0].published  # the retry finds the published conversion


def test_classes_to_binary_holds_each_output_type_lock(tmp_path):
    classes = blobs((1, 3, 1, 3), value=1) + blobs((5, 7, 5, 7), value=2)
    run, image, masks = run_with(tmp_path, extra={"Classes": classes})
    lock = masks / "Tissue_class2" / LOCK
    lock.parent.mkdir()
    lock.write_text("{}", encoding="utf-8")
    before = tree(masks)
    with pytest.raises(SegmentationError, match="Tissue_class2"):
        run.classes_to_binary(image, "Classes", output_prefix="Tissue")
    assert tree(masks) == before  # not even the first class's output
