import json
import os
from pathlib import Path

import numpy as np
import pytest
import tifffile

from morphobricks_preprocessing import (
    MANIFEST_FILENAME,
    OUTPUT_FILENAME,
    SINGLE,
    AxisMapping,
    PlaneReadError,
    PooledHistogram,
    Severity,
    group_records,
    prefill_mapping,
    preflight,
    read_manifest,
    read_plane_histograms,
    run_experiment,
)
from morphobricks_preprocessing import planes as planes_module
from morphobricks_preprocessing import run as run_module
from simulation import simulate_plane
from tiff_tree import write_data, write_file

ZYX = prefill_mapping("ZYX")
IMAGES = [
    "control/bio_rep1/tech_rep1/img_01.tif",
    "control/bio_rep1/tech_rep1/img_02.tif",
    "control/bio_rep1/tech_rep2/img_01.tif",
    "treated/bio_rep1/img_01.tif",
    "treated/bio_rep2/tech_rep1/img_01.tif",
]


def stack(seed, dtype="uint8", n_z=2, shape=(24, 24), brightness=1.0):
    """Simulated z-stack. 8-bit by default: 256 bins keep the tables small and the tests fast."""
    rng = np.random.default_rng(seed)
    if dtype == "uint8":
        planes = [simulate_plane(rng, np.uint16, shape, brightness) // 8 for _ in range(n_z)]
        return np.clip(np.stack(planes), 0, 255).astype(np.uint8)
    return np.stack([simulate_plane(rng, dtype, shape, brightness) for _ in range(n_z)])


def touch(path, data):
    """Rewrite ``path`` with ``data`` and make sure its modification time changes."""
    before = os.stat(path).st_mtime_ns
    write_data(path, data, axes="ZYX")
    stat = os.stat(path)
    os.utime(path, ns=(stat.st_atime_ns, max(stat.st_mtime_ns, before) + 10**9))


@pytest.fixture
def experiment(tmp_path):
    root = tmp_path / "exp"
    for seed, rel in enumerate(IMAGES):
        write_data(root / rel, stack(seed, brightness=1 + 0.5 * rel.startswith("treated")), "ZYX")
    return root


class Reads(list):
    def relative(self, root):
        return [Path(p).relative_to(Path(root).resolve()).as_posix() for p in self]


@pytest.fixture
def reads(monkeypatch):
    """Record which files have their pixels read."""
    paths = Reads()
    original = run_module.iter_planes

    def spy(path, *args, **kwargs):
        paths.append(path)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(run_module, "iter_planes", spy)
    return paths


def codes(issues):
    return [issue.code for issue in issues]


def issue(issues, code):
    (found,) = [i for i in issues if i.code == code]
    return found


def csv_bytes(result):
    return result.csv_path.read_bytes()


# First run ----------------------------------------------------------------------------


def test_first_run_writes_table_and_manifest(experiment):
    result = run_experiment(experiment, ZYX)
    assert result.written and result.ok
    assert result.output_folder == experiment.parent / "exp_MorphoBricks_Preprocessing"
    assert result.csv_path.name == OUTPUT_FILENAME
    assert result.manifest_path.name == MANIFEST_FILENAME
    assert result.new == tuple(sorted(IMAGES)) and result.read == tuple(sorted(IMAGES))
    assert result.rebuild_reason == "there is no earlier run in the output folder"
    assert result.plane_count == 2 * len(IMAGES)
    assert not any((experiment / name).exists() for name in (OUTPUT_FILENAME, MANIFEST_FILENAME))


def test_labels_and_coordinates_in_the_table(experiment):
    result = run_experiment(experiment, ZYX)
    records = read_plane_histograms(result.csv_path)
    assert [r.plane_id for r in records] == [
        f"{rel}::z={z}" for rel in sorted(IMAGES) for z in (0, 1)
    ]
    by_path = {r.source_relative_path: r for r in records}
    short = by_path["treated/bio_rep1/img_01.tif"]
    assert (short.condition, short.biological_repeat, short.technical_repeat) == (
        "treated",
        "bio_rep1",
        SINGLE,
    )
    assert {r.z for r in records} == {0, 1}
    assert all(r.c is None and r.t is None for r in records)


def test_table_matches_the_pixels(experiment):
    result = run_experiment(experiment, ZYX)
    records = read_plane_histograms(result.csv_path)
    pixels = {rel: tifffile.imread(experiment / rel) for rel in IMAGES}
    for record in records:
        plane = pixels[record.source_relative_path][record.z]
        assert record.histogram.pixel_min == plane.min()
        assert record.histogram.pixel_max == plane.max()
        assert record.histogram.total_pixel_count == plane.size
        assert record.histogram.counts.sum() == record.histogram.valid_pixel_count
        assert record.histogram.is_exact
    # Pooled counts reproduce the pooled-pixel statistics exactly at every level.
    for level in ("condition", "biological_repeat", "technical_repeat", "image"):
        for members in group_records(records, level).values():
            values = np.concatenate(
                [pixels[r.source_relative_path][r.z].ravel() for r in members]
            ).astype(np.float64)
            pooled = PooledHistogram(members)
            assert pooled.mean == pytest.approx(values.mean(), rel=1e-12)
            assert pooled.std == pytest.approx(values.std(), rel=1e-9)
            assert pooled.percentile(99) == np.percentile(values, 99, method="inverted_cdf")


def test_manifest_records_labels_mapping_settings_and_files(experiment):
    result = run_experiment(experiment, ZYX, bin_count=100, memory_limit=2**28)
    manifest = read_manifest(result.manifest_path)
    assert manifest.dtype == "uint8"
    assert manifest.mapping == ZYX
    assert manifest.settings.bin_count == 100 and manifest.settings.memory_limit == 2**28
    assert manifest.leaves["treated/bio_rep1"] == ("treated", "bio_rep1", SINGLE)
    assert [f.relative_path for f in manifest.files] == sorted(IMAGES)
    entry = manifest.file(IMAGES[0])
    stat = os.stat(experiment / IMAGES[0])
    assert (entry.size, entry.mtime_ns, entry.plane_count) == (stat.st_size, stat.st_mtime_ns, 2)
    assert set(manifest.versions) == {"morphobricks-preprocessing", "numpy", "tifffile", "python"}
    assert manifest.written_at.endswith("+00:00")


def test_manifest_records_channel_names_and_significant_bits(tmp_path):
    root = tmp_path / "exp"
    root.mkdir()
    for i, names in enumerate([["DAPI", "GFP"], ["DAPI", "RFP"]]):
        tifffile.imwrite(
            root / f"img_{i}.ome.tif",
            stack(i, n_z=2),
            ome=True,
            photometric="minisblack",
            metadata={"axes": "CYX", "Channel": {"Name": names}, "SignificantBits": 12},
        )
    result = run_experiment(root, prefill_mapping("CYX"))
    assert result.written
    found = issue(result.issues, "channel_names_differ")
    assert found.severity is Severity.WARNING
    manifest = read_manifest(result.manifest_path)
    assert manifest.file("img_1.ome.tif").channel_names == ("DAPI", "RFP")
    assert manifest.file("img_0.ome.tif").significant_bits == 12


def test_custom_output_folder(experiment, tmp_path):
    result = run_experiment(experiment, ZYX, output_folder=tmp_path / "out" / "nested")
    assert result.written and result.csv_path == tmp_path / "out" / "nested" / OUTPUT_FILENAME


# Reruns (plan §7) ---------------------------------------------------------------------


def test_rerun_is_idempotent_and_reads_nothing(experiment, reads):
    first = run_experiment(experiment, ZYX)
    table = csv_bytes(first)
    reads.clear()
    second = run_experiment(experiment, ZYX)
    assert second.written and second.rebuild_reason is None
    assert second.unchanged == tuple(sorted(IMAGES))
    assert second.new == second.changed == second.removed == second.read == ()
    assert reads == []
    assert csv_bytes(second) == table


def test_only_changed_file_is_read(experiment, reads):
    first = run_experiment(experiment, ZYX)
    before = {r.plane_id: r for r in read_plane_histograms(first.csv_path)}
    changed = IMAGES[1]
    touch(experiment / changed, stack(99) // 2)
    reads.clear()

    result = run_experiment(experiment, ZYX)
    assert result.changed == (changed,) and result.read == (changed,)
    assert reads.relative(experiment) == [changed]
    after = {r.plane_id: r for r in read_plane_histograms(result.csv_path)}
    assert after.keys() == before.keys()
    for plane_id, record in after.items():
        same = np.array_equal(record.histogram.counts, before[plane_id].histogram.counts)
        assert same == (not plane_id.startswith(changed))


def test_new_file_is_added(experiment, reads):
    run_experiment(experiment, ZYX)
    added = "control/bio_rep1/tech_rep2/img_02.tif"
    write_data(experiment / added, stack(50), "ZYX")
    reads.clear()
    result = run_experiment(experiment, ZYX)
    assert result.new == (added,) and reads.relative(experiment) == [added]
    assert result.plane_count == 2 * (len(IMAGES) + 1)
    assert added in {r.source_relative_path for r in read_plane_histograms(result.csv_path)}


def test_deleted_file_rows_are_removed(experiment, reads):
    run_experiment(experiment, ZYX)
    (experiment / IMAGES[0]).unlink()
    reads.clear()
    result = run_experiment(experiment, ZYX)
    assert result.removed == (IMAGES[0],) and result.read == () and reads == []
    paths = {r.source_relative_path for r in read_plane_histograms(result.csv_path)}
    assert IMAGES[0] not in paths and len(paths) == len(IMAGES) - 1
    assert read_manifest(result.manifest_path).file(IMAGES[0]) is None


def test_renamed_file_is_a_delete_plus_an_add(experiment, reads):
    run_experiment(experiment, ZYX)
    old = experiment / IMAGES[0]
    old.rename(old.with_name("renamed.tif"))
    renamed = IMAGES[0].rsplit("/", 1)[0] + "/renamed.tif"
    reads.clear()
    result = run_experiment(experiment, ZYX)
    assert result.removed == (IMAGES[0],) and result.new == (renamed,)
    assert reads.relative(experiment) == [renamed]


def test_force_rebuild_reads_everything(experiment, reads):
    first = run_experiment(experiment, ZYX)
    reads.clear()
    result = run_experiment(experiment, ZYX, force_rebuild=True)
    assert result.rebuild_reason == "a full rebuild was requested"
    assert result.unchanged == tuple(sorted(IMAGES)) and result.read == tuple(sorted(IMAGES))
    assert sorted(reads.relative(experiment)) == sorted(IMAGES)
    assert csv_bytes(result) == csv_bytes(first)


def test_changed_bin_count_rebuilds(experiment, reads):
    run_experiment(experiment, ZYX)
    reads.clear()
    result = run_experiment(experiment, ZYX, bin_count=100)
    assert result.rebuild_reason == "the bin count changed from 256 to 100"
    assert len(reads) == len(IMAGES)
    assert {r.histogram.bin_count for r in read_plane_histograms(result.csv_path)} == {100}


def test_changed_axis_mapping_rebuilds(experiment):
    run_experiment(experiment, ZYX)
    result = run_experiment(experiment, AxisMapping("ZYX", "TYX"))
    assert result.rebuild_reason == "the axis mapping changed"
    records = read_plane_histograms(result.csv_path)
    assert {r.t for r in records} == {0, 1} and {r.z for r in records} == {None}


def test_memory_limit_change_keeps_rows(experiment, reads):
    run_experiment(experiment, ZYX)
    reads.clear()
    result = run_experiment(experiment, ZYX, memory_limit=2**29)
    assert result.rebuild_reason is None and reads == []


def test_edited_table_is_rebuilt(experiment):
    first = run_experiment(experiment, ZYX)
    original = csv_bytes(first)
    first.csv_path.write_bytes(original.replace(b"control", b"CONTROL"))
    result = run_experiment(experiment, ZYX)
    assert result.rebuild_reason == (
        "the histogram table was changed or replaced after the earlier run"
    )
    assert csv_bytes(result) == original


def test_missing_table_is_rebuilt(experiment):
    first = run_experiment(experiment, ZYX)
    first.csv_path.unlink()
    result = run_experiment(experiment, ZYX)
    assert result.written and "changed or replaced" in result.rebuild_reason


def test_unreadable_manifest_is_rebuilt(experiment):
    first = run_experiment(experiment, ZYX)
    first.manifest_path.write_text("{not json", encoding="utf-8")
    result = run_experiment(experiment, ZYX)
    assert result.rebuild_reason == "the earlier run manifest could not be read"
    assert result.new == tuple(sorted(IMAGES))
    assert read_manifest(result.manifest_path).files


def test_table_with_missing_rows_rereads_the_file(experiment, reads):
    first = run_experiment(experiment, ZYX)
    manifest = read_manifest(first.manifest_path)
    # Make the manifest claim more planes than the table holds for one file.
    data = manifest.to_dict()
    data["files"][0]["plane_count"] = 3
    first.manifest_path.write_text(json.dumps(data), encoding="utf-8")
    reads.clear()
    result = run_experiment(experiment, ZYX)
    assert result.changed == (sorted(IMAGES)[0],)
    assert reads.relative(experiment) == [sorted(IMAGES)[0]]


# Blocking errors ----------------------------------------------------------------------


def test_errors_write_nothing_and_keep_earlier_output(experiment):
    first = run_experiment(experiment, ZYX)
    table, manifest = csv_bytes(first), first.manifest_path.read_bytes()
    write_file(experiment / "control/bio_rep1/tech_rep1/bad.tif", b"garbage")
    result = run_experiment(experiment, ZYX)
    assert not result.written and not result.ok
    assert issue(result.issues, "unreadable_tiff").paths == ("control/bio_rep1/tech_rep1/bad.tif",)
    assert csv_bytes(first) == table and first.manifest_path.read_bytes() == manifest


def test_first_run_with_errors_creates_no_output(experiment):
    write_file(experiment / "control/bio_rep1/tech_rep1/bad.tif", b"garbage")
    result = run_experiment(experiment, ZYX)
    assert not result.written
    assert not result.output_folder.exists()


def test_output_inside_experiment_is_refused(experiment):
    result = run_experiment(experiment, ZYX, output_folder=experiment / "results")
    assert "output_inside_experiment" in codes(result.issues)
    assert not (experiment / "results").exists()


def test_unconfirmed_mapping_errors_block(experiment):
    result = run_experiment(experiment, AxisMapping("ZYX", "-YX"))
    assert codes(result.issues) == ["unassigned_axis"] and not result.written


def test_invalid_bin_count_blocks(experiment):
    result = run_experiment(experiment, ZYX, bin_count=0)
    found = issue(result.issues, "invalid_bin_count")
    assert "between 1 and 256" in found.message and not result.written


def test_plane_over_memory_limit_blocks(experiment, reads):
    result = run_experiment(experiment, ZYX, memory_limit=1000)
    assert codes(result.issues) == ["plane_over_memory_limit"]
    assert not result.written and reads == []


def test_pixel_data_that_cannot_be_decoded_blocks(experiment, monkeypatch):
    original = planes_module._read_page

    def broken(series, index, layout, path):
        if str(path).endswith("img_02.tif"):
            raise PlaneReadError("Page 0 cannot be decoded")
        return original(series, index, layout, path)

    monkeypatch.setattr(planes_module, "_read_page", broken)
    result = run_experiment(experiment, ZYX)
    found = issue(result.issues, "unreadable_planes")
    assert found.paths == ("control/bio_rep1/tech_rep1/img_02.tif",)
    assert "exclude" in found.action
    assert not result.written and not result.output_folder.exists()


def test_missing_axes_need_confirmation(tmp_path):
    root = tmp_path / "exp"
    write_data(root / "a.tif", stack(0, n_z=2)[:, None].repeat(2, axis=1), axes="ZCYX")
    write_data(root / "b.tif", stack(1, n_z=2), axes="ZYX")
    mapping = prefill_mapping("ZCYX")
    blocked = run_experiment(root, mapping)
    assert codes(blocked.issues) == ["missing_axes"]
    result = run_experiment(root, mapping, missing_as_singleton=True)
    assert result.written
    rows = [r for r in read_plane_histograms(result.csv_path) if r.source_relative_path == "b.tif"]
    assert [(r.z, r.c) for r in rows] == [(0, 0), (1, 0)]
    assert read_manifest(result.manifest_path).file("b.tif").singleton_roles == ("C",)


# Exclusions (open question 6) ---------------------------------------------------------


def test_excluding_a_corrupt_file_lets_the_run_continue(experiment):
    bad = "control/bio_rep1/tech_rep1/bad.tif"
    write_file(experiment / bad, b"garbage")
    result = run_experiment(experiment, ZYX, exclude=[bad.replace("/", "\\")])
    assert result.written and result.excluded == (bad,)
    found = issue(result.issues, "excluded_images")
    assert found.severity is Severity.WARNING and found.paths == (bad,)
    assert read_manifest(result.manifest_path).settings.exclude == (bad,)


def test_excluding_an_analysed_file_removes_its_rows(experiment, reads):
    run_experiment(experiment, ZYX)
    reads.clear()
    result = run_experiment(experiment, ZYX, exclude=[IMAGES[2]])
    assert result.removed == (IMAGES[2],) and reads == []
    assert IMAGES[2] not in {r.source_relative_path for r in read_plane_histograms(result.csv_path)}


def test_unknown_excluded_path_is_a_warning(experiment):
    result = run_experiment(experiment, ZYX, exclude=["./control/nope.tif"])
    assert result.written
    assert issue(result.issues, "excluded_path_not_found").paths == ("control/nope.tif",)


def test_excluding_everything_is_an_error(experiment):
    result = run_experiment(experiment, ZYX, exclude=IMAGES)
    assert "no_images" in codes(result.issues) and not result.written


# Pixel values -------------------------------------------------------------------------


def test_non_finite_pixels_are_a_warning(tmp_path):
    root = tmp_path / "exp"
    data = stack(0, dtype="float32")
    data[0, 0, :4] = [np.nan, np.inf, -np.inf, np.nan]
    write_data(root / "a.tif", data, "ZYX")
    result = run_experiment(root, ZYX)
    assert result.written
    found = issue(result.issues, "non_finite_pixels")
    assert "4 pixels" in found.message and found.paths == ("a.tif",)
    first = read_plane_histograms(result.csv_path)[0]
    assert first.histogram.total_pixel_count - first.histogram.valid_pixel_count == 4


def test_empty_plane_is_a_warning(tmp_path):
    root = tmp_path / "exp"
    data = stack(0, dtype="float64")
    data[1] = np.nan
    write_data(root / "a.tif", data, "ZYX")
    result = run_experiment(root, ZYX)
    assert result.written
    assert issue(result.issues, "no_finite_pixels").paths == ("a.tif",)
    rows = read_plane_histograms(result.csv_path)
    assert rows[1].histogram.is_empty and rows[1].histogram.pixel_min is None


def test_no_usable_planes_is_an_error(tmp_path):
    root = tmp_path / "exp"
    write_data(root / "a.tif", np.full((2, 8, 8), np.nan, np.float32), "ZYX")
    result = run_experiment(root, ZYX)
    assert codes(result.issues)[0] == "no_usable_planes" and not result.written


@pytest.mark.parametrize(
    "dtype", ["int8", "int16", "int32", "int64", "uint8", "uint16", "uint32", "uint64"]
)
def test_every_integer_dtype_runs(tmp_path, dtype):
    root = tmp_path / "exp"
    info = np.iinfo(dtype)
    data = np.random.default_rng(0).integers(info.min, info.max, (2, 8, 8), dtype=dtype)
    write_data(root / "a.tif", data, "ZYX")
    result = run_experiment(root, ZYX)
    assert result.written
    rows = read_plane_histograms(result.csv_path)
    assert [r.histogram.pixel_max for r in rows] == [data[0].max(), data[1].max()]


# Preflight ----------------------------------------------------------------------------


def test_preflight_suggests_a_mapping(experiment):
    result = preflight(experiment)
    assert result.ok
    assert result.suggested_mapping == ZYX
    assert result.discovery.images == tuple(sorted(IMAGES))


def test_preflight_leaves_excluded_files_out(experiment):
    write_file(experiment / "control/bio_rep1/tech_rep1/bad.tif", b"garbage")
    assert "unreadable_tiff" in codes(preflight(experiment).issues)
    result = preflight(experiment, exclude=["control/bio_rep1/tech_rep1/bad.tif"])
    assert result.ok and codes(result.issues) == ["excluded_images"]
