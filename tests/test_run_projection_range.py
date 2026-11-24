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
