"""``RunInputs.manifest_sha256`` through equality, ``replace()`` and pickle (#497).

The field is the SHA-256 of the manifest bytes a run was opened from (review ar, B2). It is not
part of equality (``compare=False``: two snapshots holding the same data are equal whichever
manifest bytes they were parsed from) and a ``RunInputs`` has no hash at all (its manifest holds
dicts), digest or not. ``dataclasses.replace()`` and a pickle round trip carry it. A
``RunInputs`` without it, such as one pickled before the field existed, is refused by
``SegmentationJob`` as ``UNPINNED``, never given a digest. This test only reads the
segmentation code; it does not change it.
"""

import dataclasses
import hashlib
import pickle

import pytest

from morphobricks_preprocessing import segmentation_cellpose_run as run_module
from morphobricks_preprocessing.manifest import MANIFEST_FILENAME
from morphobricks_preprocessing.run_inputs import RunInputs
from morphobricks_preprocessing.segmentation_record import SegmentationError
from morphobricks_preprocessing.testing import make_clean_run
from test_segmentation_cellpose_direct import make_job, setup  # noqa: F401 (fixture)

OTHER = "f" * 64


@pytest.fixture
def opened(tmp_path):
    run = make_clean_run(tmp_path, mask_types=("Cells",))
    return RunInputs.open(run.output), run


def test_the_digest_is_the_sha256_of_the_manifest_bytes(opened):
    inputs, run = opened
    expected = hashlib.sha256((run.output / MANIFEST_FILENAME).read_bytes()).hexdigest()
    assert inputs.manifest_sha256 == expected


def test_equality_ignores_the_digest_and_there_is_no_hash(opened):
    inputs, _ = opened
    other = dataclasses.replace(inputs, manifest_sha256=OTHER)
    unpinned = dataclasses.replace(inputs, manifest_sha256=None)
    assert inputs == other == unpinned
    assert (other.manifest_sha256, unpinned.manifest_sha256) == (OTHER, None)
    for item in (inputs, other, unpinned):  # the digest does not make it hashable, nor its lack
        with pytest.raises(TypeError, match="unhashable"):
            hash(item)


def test_replace_carries_the_digest_unless_it_is_given(opened):
    inputs, run = opened
    assert dataclasses.replace(inputs, masks_root=None).manifest_sha256 == inputs.manifest_sha256
    assert dataclasses.replace(inputs, manifest_sha256=OTHER).manifest_sha256 == OTHER
    assert dataclasses.replace(inputs).manifest_sha256 == inputs.manifest_sha256


def test_a_pickle_round_trip_carries_the_digest(opened):
    inputs, _ = opened
    copy = pickle.loads(pickle.dumps(inputs))
    assert copy.manifest_sha256 == inputs.manifest_sha256 is not None
    assert "manifest_sha256" in vars(copy)
    assert copy.manifest == inputs.manifest and copy.output_folder == inputs.output_folder


def old_pickle_of(inputs):
    """Return ``inputs`` as a pickle made before the field existed would restore it."""
    state = {k: v for k, v in vars(inputs).items() if k != "manifest_sha256"}
    old = RunInputs.__new__(RunInputs)
    old.__dict__.update(state)
    assert "manifest_sha256" not in vars(old)
    return pickle.loads(pickle.dumps(old))


def test_a_pickle_without_the_field_reads_none_and_is_refused_by_a_job(setup):  # noqa: F811
    old = old_pickle_of(setup.run.inputs)
    assert old.manifest_sha256 is None and old.manifest == setup.run.inputs.manifest
    with pytest.raises(SegmentationError) as refused:
        make_job(old)
    assert str(refused.value) == run_module.UNPINNED
    with pytest.raises(SegmentationError) as given:  # a digest given does not pin it either
        make_job(old, run_manifest_sha256=setup.run.inputs.manifest_sha256)
    assert str(given.value) == run_module.UNPINNED
    assert old.manifest_sha256 is None  # never given one
