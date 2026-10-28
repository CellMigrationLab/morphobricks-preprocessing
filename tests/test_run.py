import hashlib
import json
import os
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
import tifffile
from morphobricks_normalization.csv_io import write_plane_histograms

from morphobricks_preprocessing import (
    MANIFEST_FILENAME,
    OUTPUT_FILENAME,
    SINGLE,
    AxisMapping,
    PlaneReadError,
    PooledHistogram,
    Severity,
    group_records,
    plane_layout,
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
    original = run_module.iter_xyc_planes

    def spy(path, *args, **kwargs):
        paths.append(path)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(run_module, "iter_xyc_planes", spy)
    return paths


def codes(issues):
    return [issue.code for issue in issues]


# The experiment fixture has 3 images in control/bio_rep1 and 1 in each other
# folder, and bio_rep2 only in treated: tests about other problems leave these out.
BALANCE_CODES = ("unbalanced_images", "repeat_missing_conditions")


def other_codes(issues):
    return [code for code in codes(issues) if code not in BALANCE_CODES]


def issue(issues, code):
    (found,) = [i for i in issues if i.code == code]
    return found


def csv_bytes(result):
    return result.csv_path.read_bytes()


# First run ----------------------------------------------------------------------------


def test_the_run_checks_the_intensities_of_its_table(tmp_path, reads):
    from morphobricks_normalization.checks import IntensityCheckSettings

    from morphobricks_preprocessing import check_table

    root = tmp_path / "exp"
    rng = np.random.default_rng(5)
    for i, level in enumerate([100, 101, 99, 102, 98, 190]):
        image = np.clip(rng.normal(level, 8, (2, 16, 16)), 0, 254).astype(np.uint8)
        write_data(root / f"ctrl/rep1/img_{i}.tif", image, axes="ZYX")
    result = run_experiment(root, ZYX)
    assert result.written and result.intensity is not None
    _, from_table = check_table(result.csv_path)
    assert result.intensity.issues == from_table.issues
    assert {i.items for i in result.intensity.issues} == {("ctrl/rep1/img_5.tif",)}
    # The checks are warnings about the table, not issues of the run.
    assert "image_intensity_outlier" not in codes(result.issues)
    manifest = read_manifest(result.manifest_path)
    assert manifest.intensity_checks == IntensityCheckSettings()
    # New limits change the warnings and the manifest, and read no image again.
    reads.clear()
    loose = IntensityCheckSettings(outlier_limit=100)
    again = run_experiment(root, ZYX, intensity_settings=loose)
    assert reads == [] and again.intensity.issues == ()
    assert read_manifest(again.manifest_path).intensity_checks == loose


def test_a_failed_run_has_no_intensity_checks(experiment):
    result = run_experiment(experiment, prefill_mapping("YX"))
    assert not result.written and result.intensity is None


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
        f"{rel}::z={z},t=0" for rel in sorted(IMAGES) for z in (0, 1)
    ]
    by_path = {r.source_relative_path: r for r in records}
    short = by_path["treated/bio_rep1/img_01.tif"]
    assert (short.condition, short.biological_repeat, short.technical_repeat) == (
        "treated",
        "bio_rep1",
        SINGLE,
    )
    assert {r.z for r in records} == {0, 1}
    assert all(r.t == 0 and r.channel_count == 1 for r in records)


def test_table_matches_the_pixels(experiment):
    result = run_experiment(experiment, ZYX)
    records = read_plane_histograms(result.csv_path)
    pixels = {rel: tifffile.imread(experiment / rel) for rel in IMAGES}
    for record in records:
        plane = pixels[record.source_relative_path][record.z]
        assert record.channel_histogram(0).pixel_min == plane.min()
        assert record.channel_histogram(0).pixel_max == plane.max()
        assert record.channel_histogram(0).total_pixel_count == plane.size
        assert (
            record.channel_histogram(0).counts.sum()
            == record.channel_histogram(0).valid_pixel_count
        )
        assert record.channel_histogram(0).is_exact
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
    assert set(manifest.versions) == {
        "morphobricks-preprocessing",
        "morphobricks-normalization",
        "numpy",
        "tifffile",
        "python",
    }
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


# Positions (cross-brick decision D02) -------------------------------------------------


def test_every_row_has_its_position_id(experiment):
    result = run_experiment(experiment, ZYX)
    records = read_plane_histograms(result.csv_path)
    assert {r.position_id for r in records} == {"img_01", "img_02"}
    # The same file name in two technical repeats gives two distinct positions.
    keys = {
        (r.condition, r.biological_repeat, r.technical_repeat, r.position_id)
        for r in records
        if r.image_file == "img_01.tif"
    }
    assert keys == {
        ("control", "bio_rep1", "tech_rep1", "img_01"),
        ("control", "bio_rep1", "tech_rep2", "img_01"),
        ("treated", "bio_rep1", SINGLE, "img_01"),
        ("treated", "bio_rep2", "tech_rep1", "img_01"),
    }
    # Position key plus plane coordinates identifies every row.
    rows = [
        (r.condition, r.biological_repeat, r.technical_repeat, r.position_id, r.z, r.t)
        for r in records
    ]
    assert len(set(rows)) == len(rows) == 2 * len(IMAGES)
    manifest = read_manifest(result.manifest_path)
    assert {f.relative_path: f.position_id for f in manifest.files} == {
        rel: rel.rsplit("/", 1)[-1].removesuffix(".tif") for rel in IMAGES
    }
    data = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert data["manifest_version"] == "1" and data["schema_version"] == "4"
    assert all("position_id" in f for f in data["files"])


def test_folder_named_single_runs_as_a_one_member_level(tmp_path):
    # <condition>/single/<images>: the repeat level exists with one member.
    root = tmp_path / "exp"
    for seed, rel in enumerate(["ctrl/single/a.tif", "ctrl/single/b.tif", "drug/single/a.tif"]):
        write_data(root / rel, stack(seed), "ZYX")
    assert preflight(root).ok
    records = read_plane_histograms(run_experiment(root, ZYX).csv_path)
    assert {
        (r.condition, r.biological_repeat, r.technical_repeat, r.position_id) for r in records
    } == {
        ("ctrl", SINGLE, SINGLE, "a"),
        ("ctrl", SINGLE, SINGLE, "b"),
        ("drug", SINGLE, SINGLE, "a"),
    }
    assert {r.source_relative_path for r in records} == {
        "ctrl/single/a.tif",
        "ctrl/single/b.tif",
        "drug/single/a.tif",
    }


def test_position_ids_that_are_text_stay_text(tmp_path):
    root = tmp_path / "exp"
    for seed, rel in enumerate(["ctrl/001.tif", "ctrl/NA.tif", "drug/TRUE.tif"]):
        write_data(root / rel, stack(seed), "ZYX")
    result = run_experiment(root, ZYX)
    records = read_plane_histograms(result.csv_path)
    assert sorted({r.position_id for r in records}) == ["001", "NA", "TRUE"]


def test_images_with_the_same_position_id_are_refused(experiment):
    write_data(experiment / "control/bio_rep1/tech_rep1/img_01.ome.tif", stack(9), "ZYX")
    result = run_experiment(experiment, ZYX)
    assert not result.written and not result.output_folder.exists()
    assert issue(result.issues, "duplicate_position_id").paths == (
        "control/bio_rep1/tech_rep1/img_01.ome.tif",
        "control/bio_rep1/tech_rep1/img_01.tif",
    )
    assert not preflight(experiment).ok


def test_table_without_position_ids_is_filled_without_reading(experiment, reads):
    first = run_experiment(experiment, ZYX)
    table = csv_bytes(first)
    # A table and manifest written before position identifiers were recorded.
    blank = [replace(r, position_id=None) for r in read_plane_histograms(first.csv_path)]
    write_plane_histograms(blank, first.csv_path)
    data = json.loads(first.manifest_path.read_text(encoding="utf-8"))
    for f in data["files"]:
        del f["position_id"]
    data["csv_sha256"] = hashlib.sha256(first.csv_path.read_bytes()).hexdigest()
    first.manifest_path.write_text(json.dumps(data), encoding="utf-8")
    reads.clear()
    result = run_experiment(experiment, ZYX)
    assert result.written and result.rebuild_reason is None
    assert result.unchanged == tuple(sorted(IMAGES)) and reads == []
    assert csv_bytes(result) == table
    assert all(f.position_id for f in read_manifest(result.manifest_path).files)


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
        same = np.array_equal(
            record.channel_histogram(0).counts, before[plane_id].channel_histogram(0).counts
        )
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
    assert {r.channel_histogram(0).bin_count for r in read_plane_histograms(result.csv_path)} == {
        100
    }


def test_changed_axis_mapping_rebuilds(experiment):
    run_experiment(experiment, ZYX)
    result = run_experiment(experiment, AxisMapping("ZYX", "TYX"))
    assert result.rebuild_reason == "the axis mapping changed"
    records = read_plane_histograms(result.csv_path)
    assert {r.t for r in records} == {0, 1} and {r.z for r in records} == {0}


def test_table_of_an_earlier_format_version_is_recomputed(experiment, reads):
    first = run_experiment(experiment, ZYX)
    manifest = json.loads(first.manifest_path.read_text(encoding="utf-8"))
    manifest["schema_version"] = "1"
    first.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    reads.clear()
    result = run_experiment(experiment, ZYX)
    assert result.rebuild_reason == "the earlier table uses a different format version"
    assert sorted(reads.relative(experiment)) == sorted(IMAGES)
    assert csv_bytes(result) == csv_bytes(first)


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


@pytest.mark.parametrize("failure", ["csv", "hash", "manifest", "backup"])
def test_preparing_outputs_preserves_earlier_files_on_failure(experiment, monkeypatch, failure):
    first = run_experiment(experiment, ZYX)
    before = {path.name: path.read_bytes() for path in (first.csv_path, first.manifest_path)}
    touch(experiment / IMAGES[0], stack(99))
    original_hash = run_module.file_sha256

    def broken_csv(*args, **kwargs):
        raise OSError("simulated CSV failure")

    def broken_hash(path):
        if Path(path).parent != first.output_folder:
            raise OSError("simulated hash failure")
        return original_hash(path)

    def broken_manifest(*args, **kwargs):
        raise OSError("simulated manifest failure")

    def broken_backup(*args, **kwargs):
        raise OSError("simulated backup failure")

    with monkeypatch.context() as patch:
        if failure == "csv":
            patch.setattr(run_module, "write_plane_histograms", broken_csv)
        elif failure == "hash":
            patch.setattr(run_module, "file_sha256", broken_hash)
        elif failure == "manifest":
            patch.setattr(run_module, "write_manifest", broken_manifest)
        else:
            patch.setattr(run_module.shutil, "copy2", broken_backup)
        with pytest.raises(OSError, match="simulated"):
            run_experiment(experiment, ZYX)
    assert {path.name: path.read_bytes() for path in first.output_folder.iterdir()} == before
    retried = run_experiment(experiment, ZYX)
    assert retried.written and retried.changed == (IMAGES[0],)


@pytest.mark.parametrize("previous", ["none", "both", "csv", "manifest"])
@pytest.mark.parametrize("failed_name", [OUTPUT_FILENAME, MANIFEST_FILENAME])
def test_publishing_outputs_restores_the_previous_state(
    experiment, monkeypatch, previous, failed_name
):
    first = run_experiment(experiment, ZYX)
    for path, keep in ((first.csv_path, "csv"), (first.manifest_path, "manifest")):
        if previous not in ("both", keep):
            path.unlink()
    unrelated = first.output_folder / "notes.txt"
    unrelated.write_text("keep this file", encoding="utf-8")
    before = {path.name: path.read_bytes() for path in first.output_folder.iterdir()}
    touch(experiment / IMAGES[0], stack(99))
    original_replace = os.replace
    failed = False

    def broken_replace(source, destination):
        nonlocal failed
        destination = Path(destination)
        if destination == first.output_folder / failed_name and not failed:
            failed = True
            raise PermissionError("simulated publication failure")
        return original_replace(source, destination)

    with monkeypatch.context() as patch:
        patch.setattr(run_module.os, "replace", broken_replace)
        with pytest.raises(PermissionError, match="simulated publication failure"):
            run_experiment(experiment, ZYX)
    assert failed
    assert {path.name: path.read_bytes() for path in first.output_folder.iterdir()} == before
    retried = run_experiment(experiment, ZYX)
    assert retried.written
    assert read_manifest(retried.manifest_path).csv_sha256 == run_module.file_sha256(
        retried.csv_path
    )
    assert unrelated.read_text(encoding="utf-8") == "keep this file"


def test_interrupted_publication_restores_earlier_output(experiment, monkeypatch):
    first = run_experiment(experiment, ZYX)
    before = {path.name: path.read_bytes() for path in first.output_folder.iterdir()}
    touch(experiment / IMAGES[0], stack(99))
    original_replace = os.replace

    def interrupted(source, destination):
        if Path(destination) == first.manifest_path:
            raise KeyboardInterrupt
        return original_replace(source, destination)

    monkeypatch.setattr(run_module.os, "replace", interrupted)
    with pytest.raises(KeyboardInterrupt):
        run_experiment(experiment, ZYX)
    assert {path.name: path.read_bytes() for path in first.output_folder.iterdir()} == before


def test_failed_rollback_retains_backups_and_reports_their_location(experiment, monkeypatch):
    first = run_experiment(experiment, ZYX)
    before = {path.name: path.read_bytes() for path in first.output_folder.iterdir()}
    touch(experiment / IMAGES[0], stack(99))
    original_replace = os.replace

    def broken_replace(source, destination):
        if Path(destination) == first.manifest_path or Path(source).parent.name == "previous":
            raise PermissionError("simulated locked output")
        return original_replace(source, destination)

    monkeypatch.setattr(run_module.os, "replace", broken_replace)
    with pytest.raises(RuntimeError, match="could not be fully restored") as excinfo:
        run_experiment(experiment, ZYX)
    (staged,) = first.output_folder.glob(".run-*")
    backup = staged / "previous"
    assert str(backup) in str(excinfo.value)
    assert {path.name: path.read_bytes() for path in backup.iterdir()} == before


def test_cleanup_failure_does_not_report_completed_output_as_failed(
    experiment, monkeypatch, caplog
):
    def broken_cleanup(path):
        raise PermissionError("simulated temporary folder in use")

    monkeypatch.setattr(run_module.shutil, "rmtree", broken_cleanup)
    result = run_experiment(experiment, ZYX)
    assert result.written
    assert read_manifest(result.manifest_path).csv_sha256 == run_module.file_sha256(result.csv_path)
    assert "Cannot remove temporary output folder" in caplog.text


def test_output_inside_experiment_is_refused(experiment):
    result = run_experiment(experiment, ZYX, output_folder=experiment / "results")
    assert "output_inside_experiment" in codes(result.issues)
    assert not (experiment / "results").exists()


def test_unconfirmed_mapping_errors_block(experiment):
    result = run_experiment(experiment, AxisMapping("ZYX", "-YX"))
    assert other_codes(result.issues) == ["unassigned_axis"] and not result.written


def test_invalid_bin_count_blocks(experiment):
    result = run_experiment(experiment, ZYX, bin_count=0)
    found = issue(result.issues, "invalid_bin_count")
    assert "between 1 and 256" in found.message and not result.written


def test_plane_over_memory_limit_blocks(experiment, reads):
    result = run_experiment(experiment, ZYX, memory_limit=1000)
    assert other_codes(result.issues) == ["plane_over_memory_limit"]
    assert not result.written and reads == []


def test_planes_over_the_memory_limit_are_read_in_blocks(experiment, tmp_path, reads):
    whole = run_experiment(experiment, ZYX, output_folder=tmp_path / "whole")
    limit = plane_layout(experiment / IMAGES[0], ZYX, memory_limit=1).block_memory_estimate
    blocks = run_experiment(experiment, ZYX, output_folder=tmp_path / "blocks", memory_limit=limit)
    assert blocks.written and other_codes(blocks.issues) == ["planes_read_in_blocks"]
    found = issue(blocks.issues, "planes_read_in_blocks")
    assert found.severity is Severity.WARNING and found.paths == tuple(sorted(IMAGES))
    assert csv_bytes(blocks) == csv_bytes(whole)
    assert all(f.read_in_blocks for f in read_manifest(blocks.manifest_path).files)
    assert not any(f.read_in_blocks for f in read_manifest(whole.manifest_path).files)
    # Changing only the limit reads nothing again; the manifest records the new way.
    reads.clear()
    again = run_experiment(experiment, ZYX, output_folder=tmp_path / "blocks")
    assert again.read == () and reads == [] and csv_bytes(again) == csv_bytes(whole)
    assert not any(f.read_in_blocks for f in read_manifest(again.manifest_path).files)


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
    write_data(root / "a.tif", stack(0, n_z=2)[None].repeat(2, axis=0), axes="TZYX")
    write_data(root / "b.tif", stack(1, n_z=2), axes="ZYX")
    mapping = prefill_mapping("TZYX")
    blocked = run_experiment(root, mapping)
    assert codes(blocked.issues) == ["missing_axes", "different_plane_counts"]
    result = run_experiment(root, mapping, missing_as_singleton=True)
    assert result.written
    rows = [r for r in read_plane_histograms(result.csv_path) if r.source_relative_path == "b.tif"]
    assert [(r.z, r.t) for r in rows] == [(0, 0), (1, 0)]
    assert read_manifest(result.manifest_path).file("b.tif").singleton_roles == ("T",)


def test_missing_channel_axis_next_to_several_channels_is_an_error(tmp_path, reads):
    # A file without a C axis has one channel; the others have two (N43).
    root = tmp_path / "exp"
    write_data(root / "a.tif", stack(0, n_z=2)[:, None].repeat(2, axis=1), axes="ZCYX")
    write_data(root / "b.tif", stack(1, n_z=2), axes="ZYX")
    write_data(root / "c.tif", stack(2, n_z=2), axes="ZYX")
    pre = preflight(root)
    assert issue(pre.issues, "different_channel_counts").paths == ("a.tif",)
    result = run_experiment(root, prefill_mapping("ZCYX"), missing_as_singleton=True)
    found = issue(result.issues, "different_channel_counts")
    assert found.severity is Severity.ERROR and found.paths == ("a.tif",)
    assert "1 channel (2 images), 2 channels (1 image)" in found.message
    assert "separate experiment" in found.action
    assert not result.written and reads == []


def test_one_row_per_xyc_plane_with_every_channel(tmp_path):
    root = tmp_path / "exp"
    data = np.stack([stack(seed, n_z=3) for seed in range(2)], axis=1)  # Z, C, Y, X
    write_data(root / "a.tif", data, axes="ZCYX")
    result = run_experiment(root, prefill_mapping("ZCYX"))
    assert result.written and result.plane_count == 3
    rows = read_plane_histograms(result.csv_path)
    assert [r.plane_id for r in rows] == [f"a.tif::z={z},t=0" for z in range(3)]
    for row in rows:
        assert row.channel_count == 2
        for c in range(2):
            plane = data[row.z, c]
            hist = row.channel_histogram(c)
            assert (hist.pixel_min, hist.pixel_max) == (plane.min(), plane.max())
            np.testing.assert_array_equal(
                hist.counts, np.bincount(plane.ravel() - plane.min(), minlength=256)[:256]
            )
    assert read_manifest(result.manifest_path).file("a.tif").plane_count == 3


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
    assert (
        first.channel_histogram(0).total_pixel_count - first.channel_histogram(0).valid_pixel_count
        == 4
    )


def test_empty_plane_is_a_warning(tmp_path):
    root = tmp_path / "exp"
    data = stack(0, dtype="float64")
    data[1] = np.nan
    write_data(root / "a.tif", data, "ZYX")
    result = run_experiment(root, ZYX)
    assert result.written
    assert issue(result.issues, "no_finite_pixels").paths == ("a.tif",)
    rows = read_plane_histograms(result.csv_path)
    assert rows[1].channel_histogram(0).is_empty and rows[1].channel_histogram(0).pixel_min is None


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
    assert [r.channel_histogram(0).pixel_max for r in rows] == [data[0].max(), data[1].max()]


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
    assert result.ok and other_codes(result.issues) == ["excluded_images"]


# Progress and preview -----------------------------------------------------------------


def test_progress_reports_each_file_read(experiment):
    calls = []
    run_experiment(experiment, ZYX, progress=lambda *args: calls.append(args))
    total = len(IMAGES)
    assert calls == [(i, total, rel) for i, rel in enumerate(sorted(IMAGES))] + [(total, total, "")]
    calls.clear()
    run_experiment(experiment, ZYX, progress=lambda *args: calls.append(args))
    assert calls == [(0, 0, "")]


def test_sample_planes_one_per_axis_arrangement(tmp_path):
    root = tmp_path / "exp"
    write_data(root / "a.tif", stack(0, n_z=3)[:, None].repeat(2, axis=1), axes="ZCYX")
    write_data(root / "b.tif", stack(1, n_z=3)[:, None].repeat(2, axis=1), axes="ZCYX")
    write_data(root / "c.tif", stack(2, n_z=3), axes="ZYX")
    write_file(root / "d.tif", b"garbage")
    pre = preflight(root)
    samples = run_module.sample_planes(pre, prefill_mapping("ZCYX"))
    assert [(s.relative_path, s.axes) for s in samples] == [("a.tif", "ZCYX"), ("c.tif", "ZYX")]
    assert (samples[0].plane.z, samples[0].plane.c) == (1, 0)
    assert (samples[1].plane.z, samples[1].plane.c) == (1, 0)  # missing C read as length 1
    np.testing.assert_array_equal(samples[1].plane.pixels, tifffile.imread(root / "c.tif")[1])


def test_sample_planes_report_problems(tmp_path):
    root = tmp_path / "exp"
    write_data(root / "a.tif", stack(0), axes="ZYX")
    write_data(root / "b.tif", stack(1), axes="CYX")
    pre = preflight(root)
    misfit, fits = sorted(run_module.sample_planes(pre, ZYX), key=lambda s: s.plane is not None)
    assert misfit.relative_path == "b.tif" and "does not fit" in misfit.error
    too_big = run_module.sample_planes(pre, ZYX, memory_limit=100)
    assert "limit" in too_big[0].error and too_big[0].plane is None
