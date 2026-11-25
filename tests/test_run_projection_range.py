"""The manifest's projection slice range is checked when read (#445)."""

import pytest

from morphobricks_preprocessing.manifest import ManifestFormatError, read_manifest
from test_run_projection import edit, projected_run

pytestmark = pytest.mark.filterwarnings("ignore:No experiment description:UserWarning")


@pytest.mark.parametrize(
    "bounds",
    [[0.5, 2], [True, 2], [1, True], [3, 1], [2, 2], [-1, 2], ["a", "b"], [1, 2.0], [1, 99]],
)
def test_a_slice_range_that_is_not_two_ordered_slice_numbers_is_refused(tmp_path, bounds):
    _, out, _, _ = projected_run(tmp_path, method="mean", z_range=(1, 4))
    edit(out / "run_manifest.json", lambda d: d["projection"].__setitem__("z_range", bounds))
    with pytest.raises(ManifestFormatError, match="z_range"):
        read_manifest(out / "run_manifest.json")


def test_a_range_other_than_the_images_is_refused(tmp_path):
    _, out, _, _ = projected_run(tmp_path, method="mean", z_range=(1, 4))
    edit(out / "run_manifest.json", lambda d: d["projection"].__setitem__("z_range", [0, 4]))
    with pytest.raises(ManifestFormatError, match="z_range"):
        read_manifest(out / "run_manifest.json")


def test_every_slice_means_each_image_s_whole_stack(tmp_path):
    _, out, _, _ = projected_run(tmp_path, method="max")
    assert read_manifest(out / "run_manifest.json").projection["z_range"] is None

    def partial(d):
        d["projection"]["images"][0]["z_range"] = [1, 2]

    edit(out / "run_manifest.json", partial)
    with pytest.raises(ManifestFormatError, match="z_range"):
        read_manifest(out / "run_manifest.json")


def test_a_written_range_reads_as_before(tmp_path):
    _, out, _, _ = projected_run(tmp_path, method="mean", z_range=(1, 4))
    assert read_manifest(out / "run_manifest.json").projection["z_range"] == [1, 4]


def _image_field(key, value):
    return lambda d: d["projection"]["images"][0].__setitem__(key, value)


@pytest.mark.parametrize(
    "change",
    [
        _image_field("output_dtype", "uint16"),
        _image_field("output_dtype", "float32"),
        _image_field("rounding", "none"),
        _image_field("dtype_rule", "max uint16->uint16"),
        _image_field("dtype_rule", "mean uint16->float64"),
        _image_field("source_dtype", "uint8"),
        _image_field("source_dtype", "int64"),
        _image_field("source_dtype", "not a type"),
        _image_field("significant_bits_dropped", "edited"),
        lambda d: d["projection"].__setitem__("method", "max"),
    ],
)
def test_an_image_s_dtype_metadata_other_than_the_table_s_is_refused(tmp_path, change):
    """Each image's types, rule, rounding and significant-bits reason must be the dtype
    table's for the method and its slices (#445 review), as a v0.8.0 run writes them."""
    _, out, _, _ = projected_run(tmp_path, method="mean", z_range=(1, 4))
    image = read_manifest(out / "run_manifest.json").projection["images"][0]
    assert (image["output_dtype"], image["rounding"]) == ("float64", "float64, one division")
    edit(out / "run_manifest.json", change)
    with pytest.raises(ManifestFormatError, match="dtype|rounding|significant|method"):
        read_manifest(out / "run_manifest.json")
