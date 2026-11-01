from pathlib import Path

import numpy as np
import pytest

from morphobricks_preprocessing import (
    FLUORESCENCE,
    RGB,
    check_image_type,
    inspect_tiff,
    prefill_mapping,
    preflight,
    read_manifest,
    review_axes,
    review_image_type,
    run_experiment,
)
from morphobricks_preprocessing import run as run_module
from tiff_tree import write_data


def rgb_image(path, shape=(2, 8, 8, 3), dtype=np.uint8, **kwargs):
    """An RGB colour image: photometric RGB, three samples per pixel (ZYXS)."""
    rng = np.random.default_rng(len(str(path)))
    data = rng.integers(0, 200, shape).astype(dtype)
    kwargs.setdefault("photometric", "rgb")
    return write_data(path, data, axes="ZYXS" if len(shape) == 4 else None, **kwargs)


def rgb_experiment(root):
    for rel in ("ctrl/rep1/a.tif", "ctrl/rep1/b.tif", "drug/rep1/a.tif"):
        rgb_image(root / rel)
    return root


def fluorescence_experiment(root, channels=2, dtype=np.uint8):
    rng = np.random.default_rng(0)
    for rel in ("ctrl/rep1/a.tif", "drug/rep1/a.tif"):
        data = rng.integers(0, 200, (2, channels, 8, 8)).astype(dtype)
        write_data(root / rel, data, axes="ZCYX")
    return root


def type_review(root, roles=None, **kwargs):
    pre = preflight(root)
    mapping = pre.suggested_mapping if roles is None else roles
    return review_image_type(pre.metadata, review_axes(pre.metadata, mapping, **kwargs))


def codes(issues):
    return [issue.code for issue in issues]


# Header -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("data_shape", "kwargs", "expected"),
    [
        pytest.param((8, 8, 3), {"photometric": "rgb"}, True, id="rgb"),
        pytest.param(
            (3, 8, 8), {"photometric": "rgb", "planarconfig": "separate"}, True, id="planar"
        ),
        pytest.param((8, 8, 3), {"planarconfig": "contig"}, False, id="three-grey-samples"),
        pytest.param(
            (8, 8, 4), {"photometric": "rgb", "extrasamples": ["unassalpha"]}, False, id="rgba"
        ),
        pytest.param((3, 8, 8), {}, False, id="grey-stack"),
    ],
)
def test_rgb_samples_come_from_the_photometric_interpretation(
    tmp_path, data_shape, kwargs, expected
):
    path = write_data(tmp_path / "a.tif", np.zeros(data_shape, np.uint8), **kwargs)
    assert inspect_tiff(path).rgb_samples is expected


def test_ome_zarr_images_have_no_rgb_samples(tmp_path):
    pytest.importorskip("zarr")
    import zarr_tree

    path = tmp_path / "a.ome.zarr"
    zarr_tree.write_ngff(path, np.zeros((3, 8, 8), np.uint8), "cyx")
    from morphobricks_preprocessing import inspect_ome_zarr

    assert inspect_ome_zarr(path).rgb_samples is False


# Suggestion -------------------------------------------------------------------------------


def test_rgb_is_suggested_for_rgb_samples_on_the_channel_axis(tmp_path):
    review = type_review(rgb_experiment(tmp_path / "exp"))
    assert review.suggested == RGB and review.channels_from_rgb_samples
    assert review.rgb_allowed and review.channel_count == 3
    assert "stored as RGB colour" in review.reason


def test_planar_rgb_is_suggested_too(tmp_path):
    root = tmp_path / "exp"
    for rel in ("a.tif", "b.tif"):
        rgb_image(root / rel, shape=(3, 8, 8), planarconfig="separate")
    review = type_review(root)
    assert review.suggested == RGB and review.channels_from_rgb_samples


def test_fluorescence_is_suggested_without_rgb_samples(tmp_path):
    # Three integer channels could be RGB, but nothing in the metadata says so.
    review = type_review(fluorescence_experiment(tmp_path / "exp", channels=3))
    assert review.suggested == FLUORESCENCE and not review.channels_from_rgb_samples
    assert review.rgb_allowed
    assert review.reason == "the images are not stored as RGB colour"


@pytest.mark.parametrize(
    ("channels", "dtype", "text"),
    [
        (2, np.uint8, "these images have 2 channels of type uint8"),
        (1, np.uint16, "these images have 1 channel of type uint16"),
        (3, np.float32, "these images have 3 channels of type float32"),
    ],
)
def test_rgb_needs_three_integer_channels(tmp_path, channels, dtype, text):
    review = type_review(fluorescence_experiment(tmp_path / "exp", channels, dtype))
    assert review.suggested == FLUORESCENCE and not review.rgb_allowed
    assert text in review.rgb_problem


def test_float_rgb_images_are_not_suggested_rgb(tmp_path):
    root = tmp_path / "exp"
    for rel in ("a.tif", "b.tif"):
        rgb_image(root / rel, dtype=np.float32)
    review = type_review(root)
    assert review.channels_from_rgb_samples and not review.rgb_allowed
    assert review.suggested == FLUORESCENCE
    assert review.reason == "the images are stored as RGB colour, but cannot be of type RGB"
    assert "3 channels of type float32" in review.rgb_problem


def test_mixed_rgb_and_grey_images_are_not_suggested_rgb(tmp_path):
    root = tmp_path / "exp"
    rgb_image(root / "a.tif", shape=(8, 8, 3))
    write_data(root / "b.tif", np.zeros((8, 8, 3), np.uint8), planarconfig="contig")
    review = type_review(root)
    assert review.suggested == FLUORESCENCE and review.rgb_allowed
    assert review.reason == "not every image is stored as RGB colour on its channel axis"


def test_check_image_type():
    assert check_image_type("rgb") == "rgb"
    with pytest.raises(ValueError, match="Choose 'fluorescence' or 'rgb'"):
        check_image_type("brightfield")


# Run and manifest -------------------------------------------------------------------------


@pytest.fixture
def reads(monkeypatch):
    paths = []
    original = run_module.iter_xyc_planes

    def spy(path, *args, **kwargs):
        paths.append(Path(path).name)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(run_module, "iter_xyc_planes", spy)
    return paths


def test_the_manifest_records_the_image_type(tmp_path):
    root = rgb_experiment(tmp_path / "exp")
    result = run_experiment(root, prefill_mapping("ZYXS"), image_type="rgb")
    assert result.written and result.image_type == "rgb"
    manifest = read_manifest(result.manifest_path)
    assert manifest.image_type == "rgb" and manifest.channels_from_rgb_samples is True


def test_without_an_image_type_none_is_recorded(tmp_path):
    # The suggestion is never applied without the user's confirmation.
    root = rgb_experiment(tmp_path / "exp")
    result = run_experiment(root, prefill_mapping("ZYXS"))
    manifest = read_manifest(result.manifest_path)
    assert manifest.image_type is None and manifest.channels_from_rgb_samples is True


def test_changing_the_image_type_recomputes_nothing(tmp_path, reads):
    root = rgb_experiment(tmp_path / "exp")
    first = run_experiment(root, prefill_mapping("ZYXS"), image_type="rgb")
    table = first.csv_path.read_bytes()
    reads.clear()
    second = run_experiment(root, prefill_mapping("ZYXS"), image_type="fluorescence")
    assert second.written and reads == [] and second.rebuild_reason is None
    assert second.csv_path.read_bytes() == table
    assert read_manifest(second.manifest_path).image_type == "fluorescence"


def test_rgb_is_refused_for_images_that_are_not_rgb(tmp_path):
    root = fluorescence_experiment(tmp_path / "exp", channels=2)
    result = run_experiment(root, prefill_mapping("ZCYX"), image_type="rgb")
    assert not result.written and "rgb_not_possible" in codes(result.issues)
    (issue,) = [i for i in result.issues if i.code == "rgb_not_possible"]
    assert "2 channels of type uint8" in issue.message and "fluorescence" in issue.action
    assert sorted(p.name for p in result.output_folder.iterdir()) == [
        "discovery_report.json",
        "discovery_report.txt",
    ]


def test_rgb_for_three_grey_channels_is_allowed(tmp_path):
    root = fluorescence_experiment(tmp_path / "exp", channels=3)
    result = run_experiment(root, prefill_mapping("ZCYX"), image_type="rgb")
    assert result.written
    manifest = read_manifest(result.manifest_path)
    assert manifest.image_type == "rgb" and manifest.channels_from_rgb_samples is False


def test_unknown_image_type_is_a_programming_error(tmp_path):
    root = fluorescence_experiment(tmp_path / "exp")
    with pytest.raises(ValueError, match="does not exist"):
        run_experiment(root, prefill_mapping("ZCYX"), image_type="RGB")
