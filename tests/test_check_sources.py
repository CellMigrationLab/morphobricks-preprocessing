"""check_sources: the per-image checks other bricks make against a run (MorphoBricks D86, A2)."""

import os
from dataclasses import replace

import numpy as np
import pytest

from morphobricks_preprocessing import (
    MANIFEST_FILENAME,
    check_sources,
    prefill_mapping,
    read_manifest,
    run_experiment,
)
from tiff_tree import write_data

IMAGES = ("ctrl/rep1/a.tif", "ctrl/rep1/b.tif", "drug/rep1/a.tif")


def stack(seed):
    return np.random.default_rng(seed).integers(0, 200, (3, 8, 8), dtype=np.uint16)


@pytest.fixture
def run(tmp_path):
    root = tmp_path / "exp"
    for seed, rel in enumerate(IMAGES):
        write_data(root / rel, stack(seed), "ZYX")
    result = run_experiment(root, prefill_mapping("ZYX"), output_folder=tmp_path / "out")
    assert result.written, result.issues
    return root, read_manifest(tmp_path / "out" / MANIFEST_FILENAME)


def codes(issues):
    return {issue.code: issue.paths for issue in issues}


def identity(path):
    condition, repeat, image = path.split("/")
    return (condition, repeat, "single", image.removesuffix(".tif"))


def test_unchanged_images_pass(run):
    root, manifest = run
    assert (
        check_sources(
            manifest,
            root,
            IMAGES,
            image_axes=dict.fromkeys(IMAGES, "ZYX"),
            identities={path: identity(path) for path in IMAGES},
        )
        == []
    )


def test_images_not_analysed_by_the_run(run):
    root, manifest = run
    write_data(root / "drug/rep1/c.tif", stack(9), "ZYX")
    issues = check_sources(manifest, root, [*IMAGES, "drug/rep1/c.tif"])
    assert codes(issues) == {"images_not_in_manifest": ("drug/rep1/c.tif",)}
    assert all(issue.is_error for issue in issues)


def test_changed_and_missing_images(run):
    root, manifest = run
    write_data(root / "ctrl/rep1/a.tif", stack(5)[:2], "ZYX")  # rewritten, another size
    (root / "drug/rep1/a.tif").unlink()
    found = codes(check_sources(manifest, root, IMAGES))
    assert found == {
        "images_changed": ("ctrl/rep1/a.tif",),
        "images_missing": ("drug/rep1/a.tif",),
    }


def test_same_size_with_a_new_time_is_changed(run):
    root, manifest = run
    path = root / "ctrl/rep1/b.tif"
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10**9))
    assert codes(check_sources(manifest, root, IMAGES)) == {"images_changed": ("ctrl/rep1/b.tif",)}


def test_a_rewritten_chunk_of_an_ome_zarr_image_is_a_change(tmp_path):
    # The report's MF-3 example: the folder's own time does not change when a chunk is
    # rewritten; the source fingerprint (its files' size and newest time) does.
    pytest.importorskip("zarr")
    from zarr_tree import write_ngff

    root = tmp_path / "exp"
    image = write_ngff(root / "ctrl" / "a.ome.zarr", stack(1), "zyx")
    result = run_experiment(root, prefill_mapping("ZYX"), output_folder=tmp_path / "out")
    assert result.written, result.issues
    manifest = read_manifest(tmp_path / "out" / MANIFEST_FILENAME)
    assert check_sources(manifest, root, ["ctrl/a.ome.zarr"]) == []
    folder_time = image.stat().st_mtime_ns
    chunk = max((p for p in image.rglob("*") if p.is_file()), key=lambda p: p.stat().st_size)
    stat = chunk.stat()
    os.utime(chunk, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10**9))
    assert image.stat().st_mtime_ns == folder_time
    assert codes(check_sources(manifest, root, ["ctrl/a.ome.zarr"])) == {
        "images_changed": ("ctrl/a.ome.zarr",)
    }


def test_labels_and_position_must_be_the_runs(run):
    root, manifest = run
    given = {path: identity(path) for path in IMAGES}
    given["ctrl/rep1/b.tif"] = ("ctrl", "rep2", "single", "b")
    issues = check_sources(manifest, root, IMAGES, identities=given)
    assert codes(issues) == {"identity_differs": ("ctrl/rep1/b.tif",)}
    assert "('ctrl', 'rep2', 'single', 'b')" in issues[0].message


def test_a_manifest_without_positions_cannot_confirm_them(run):
    root, manifest = run
    old = replace(manifest, files=tuple(replace(f, position_id=None) for f in manifest.files))
    issues = check_sources(old, root, IMAGES, identities={p: identity(p) for p in IMAGES})
    assert codes(issues) == {"position_id_not_recorded": tuple(sorted(IMAGES))}
    assert check_sources(old, root, IMAGES) == []  # nothing to compare without identities


def test_a_recorded_position_that_is_not_the_name_is_refused(run):
    root, manifest = run
    files = tuple(
        replace(f, position_id="x") if f.relative_path == "ctrl/rep1/a.tif" else f
        for f in manifest.files
    )
    found = codes(check_sources(replace(manifest, files=files), root, IMAGES))
    assert found == {"identity_differs": ("ctrl/rep1/a.tif",)}


@pytest.mark.parametrize("roles_recorded", [True, False])
def test_axes_must_have_the_confirmed_roles(run, roles_recorded):
    # CellPatcher's check_images rule; manifests written before per-file roles fall back on
    # the run's mapping resolved for the image's header.
    root, manifest = run
    if not roles_recorded:
        manifest = replace(manifest, files=tuple(replace(f, roles=None) for f in manifest.files))
    axes = dict.fromkeys(IMAGES, "zyx")
    assert check_sources(manifest, root, IMAGES, image_axes=axes) == []
    axes["drug/rep1/a.tif"] = "TYX"
    axes["ctrl/rep1/b.tif"] = "YX"
    issues = check_sources(manifest, root, IMAGES, image_axes=axes)
    assert codes(issues) == {"axis_mapping_mismatch": ("ctrl/rep1/b.tif", "drug/rep1/a.tif")}
    assert "drug/rep1/a.tif: given TYX, preprocessing ZYX" in issues[0].message


def test_an_axis_without_a_role_fits_any_letter(run):
    root, manifest = run
    files = tuple(replace(f, roles=(None, "Y", "X")) for f in manifest.files)
    axes = dict.fromkeys(IMAGES, "TYX")
    assert check_sources(replace(manifest, files=files), root, IMAGES, image_axes=axes) == []


def test_every_path_needs_its_axes_and_identity(run):
    root, manifest = run
    with pytest.raises(ValueError, match="image_axes has no entry for drug/rep1/a.tif"):
        check_sources(manifest, root, IMAGES, image_axes=dict.fromkeys(IMAGES[:2], "ZYX"))
    with pytest.raises(ValueError, match="identities has no entry"):
        check_sources(manifest, root, IMAGES, identities={})
