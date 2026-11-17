"""The table put together from the staged rows, and the staging in the workers (D7).

Each worker stages its image's rows with the table's header, and the calling thread copies
them into ``plane_histograms.csv`` (:mod:`morphobricks_preprocessing.run.table`). The bytes
must be those of the normalization brick's writer on the same rows, whatever is staged or
not (kept rows of an earlier table, rows staged with other channel IDs, a staged file changed
since), with the writer's refusals; a failure while staging stops the run as before.
"""

import hashlib
import logging

import numpy as np
import pytest
from morphobricks_normalization.csv_io import (
    read_plane_histogram_table,
    write_plane_histograms,
)
from morphobricks_normalization.histogram import compute_plane_histogram
from morphobricks_normalization.records import PlaneRecord

from described_tree import run_described
from morphobricks_preprocessing import prefill_mapping
from morphobricks_preprocessing import run as run_module
from morphobricks_preprocessing.run import RunMode
from morphobricks_preprocessing.run.staging import STAGING_FOLDER, stage_file
from morphobricks_preprocessing.run.table import write_table
from tiff_tree import write_data

TYX = prefill_mapping("TYX")
IDS = ("DNA", "GFP", "RFP")  # RFP is in no row: its cells are blank in every row
FILES = ("ctrl/r1/a.tif", "ctrl/r1/b.tif", "drug/r1/a.tif")


def records_of(rel, seed, planes=3, channels=("DNA", "GFP")):
    """``planes`` rows (t = 0, 1, ...) of ``rel``: uint8 histograms of seeded pixels."""
    rng = np.random.default_rng(seed)
    return [
        PlaneRecord(
            rel,
            *rel.split("/")[:2],
            "single",
            histograms=tuple(
                compute_plane_histogram(rng.integers(0, 200, (16, 16), dtype=np.uint8))
                for _ in channels
            ),
            z=None,
            t=t,
            position_id=rel,
            channels=channels,
        )
        for t in range(planes)
    ]


@pytest.fixture
def rows():
    """Every file's rows, in table order."""
    return {rel: records_of(rel, seed) for seed, rel in enumerate(FILES)}


def stage(output, rel, records, ids=IDS):
    return stage_file(output, {"file": rel}, (records, []), ids)


def expected(tmp_path, records):
    """The bytes the normalization brick's writer gives these rows."""
    path = write_plane_histograms(records, tmp_path / "writer.csv", channel_ids=IDS)
    return path.read_bytes()


def table(tmp_path, records, staged):
    folder = tmp_path / "generation"
    folder.mkdir(exist_ok=True)
    path, sha256 = write_table(records, folder / "plane_histograms.csv", IDS, staged)
    data = path.read_bytes()
    assert sha256 == hashlib.sha256(data).hexdigest()
    assert sorted(p.name for p in folder.iterdir()) == ["plane_histograms.csv"]  # no leftover
    return data


@pytest.mark.parametrize(
    "staged_files",
    [FILES, (), FILES[1:2], FILES[::2]],
    ids=["all staged", "none staged", "middle staged", "first and last staged"],
)
def test_the_table_is_the_writers_bytes_whatever_is_staged(tmp_path, rows, staged_files):
    """Staged rows are copied, the others (kept rows) written by the writer: the same bytes."""
    output = tmp_path / "out"
    output.mkdir()
    staged = {rel: stage(output, rel, rows[rel]) for rel in staged_files}
    records = [record for rel in FILES for record in rows[rel]]
    assert table(tmp_path, records, staged) == expected(tmp_path, records)


def test_a_staged_file_is_the_tables_header_and_its_rows(tmp_path, rows):
    output = tmp_path / "out"
    output.mkdir()
    found = stage(output, FILES[0], rows[FILES[0]])
    assert found.path.parent == output / STAGING_FOLDER
    assert found.plane_ids == tuple(record.plane_id for record in rows[FILES[0]])
    assert found.sha256 == hashlib.sha256(found.path.read_bytes()).hexdigest()
    assert read_plane_histogram_table(found.path).channel_ids == IDS


def _changed(found):
    data = found.path.read_bytes()
    found.path.write_bytes(data[:-3] + (b"9" if data[-3:-2] != b"9" else b"8") + data[-2:])


@pytest.mark.parametrize(
    "spoil",
    ["changed", "removed", "other channel ids", "other row order"],
)
def test_a_staged_file_not_as_staged_is_written_again_never_copied(tmp_path, rows, spoil, caplog):
    output = tmp_path / "out"
    output.mkdir()
    staged = {rel: stage(output, rel, rows[rel]) for rel in FILES}
    found = staged[FILES[1]]
    if spoil == "changed":
        _changed(found)
    elif spoil == "removed":
        found.path.unlink()
    elif spoil == "other channel ids":
        staged[FILES[1]] = stage(output, FILES[1], rows[FILES[1]], ("DNA", "GFP"))
    else:
        reordered = rows[FILES[1]][::-1]
        staged[FILES[1]] = stage(output, FILES[1], reordered)
    records = [record for rel in FILES for record in rows[rel]]
    with caplog.at_level(logging.WARNING, logger="morphobricks_preprocessing.run.table"):
        assert table(tmp_path, records, staged) == expected(tmp_path, records)
    if spoil in ("changed", "removed", "other channel ids"):
        assert f"The staged rows of {FILES[1]} are not the ones staged" in caplog.text


def _refusal(call):
    with pytest.raises(ValueError) as raised:
        call()
    return str(raised.value)


@pytest.mark.parametrize(
    "case", ["no rows", "a plane twice", "a channel outside the ids", "two bin counts", "no ids"]
)
def test_the_writers_refusals_are_made_before_anything_is_written(tmp_path, rows, case):
    records = [record for rel in FILES for record in rows[rel]]
    ids = IDS
    if case == "no rows":
        records = []
    elif case == "a plane twice":
        records = [*records, records[-1]]
    elif case == "a channel outside the ids":
        ids = ("DNA",)
    elif case == "two bin counts":
        wide = compute_plane_histogram(np.arange(256, dtype=np.uint16).reshape(16, 16))
        other = PlaneRecord(
            "z/r1/w.tif",
            "z",
            "r1",
            "single",
            histograms=(wide,),
            z=None,
            t=0,
            position_id="w",
            channels=("DNA",),
        )
        records = [*records, other]
    else:
        ids = ()
    folder = tmp_path / "generation"
    folder.mkdir()
    ours = _refusal(lambda: write_table(records, folder / "plane_histograms.csv", ids, {}))
    writer = _refusal(
        lambda: write_plane_histograms(records, tmp_path / "writer.csv", channel_ids=ids)
    )
    assert ours == writer
    assert list(folder.iterdir()) == []


# A run: kept, resumed and read rows give the writer's bytes ------------------------------------


def write_images(root, names, start=0):
    for seed, rel in enumerate(names, start=start):
        pixels = np.random.default_rng(seed).integers(0, 255, (3, 8, 8), dtype=np.uint8)
        write_data(root / rel, pixels, "TYX")


def assert_writers_bytes(result):
    """The published table is the writer's bytes on its own rows read back."""
    data = result.csv_path.read_bytes()
    found = read_plane_histogram_table(result.csv_path)
    again = result.csv_path.parent / "again.csv"
    write_plane_histograms(found.records, again, channel_ids=found.channel_ids)
    try:
        assert again.read_bytes() == data
    finally:
        again.unlink()


@pytest.mark.parametrize("workers", [1, 2, 4, "auto"])
def test_a_run_with_kept_and_new_images_publishes_the_writers_bytes(tmp_path, workers):
    root, out = tmp_path / "exp", tmp_path / "out"
    write_images(root, ["ctrl/r1/b.tif", "drug/r1/b.tif"])
    first = run_described(root, TYX, output_folder=out, mode=RunMode(workers=workers))
    assert first.written
    write_images(root, ["ctrl/r1/a.tif", "ctrl/r1/c.tif", "drug/r1/c.tif"], start=10)
    second = run_described(root, TYX, output_folder=out, mode=RunMode(workers=workers))
    assert second.written and len(second.read) == 3 and len(second.unchanged) == 2
    assert_writers_bytes(second)
    fresh = run_described(root, TYX, output_folder=tmp_path / "fresh")
    assert fresh.csv_path.read_bytes() == second.csv_path.read_bytes()


@pytest.mark.parametrize("workers", [1, 3])
def test_a_resumed_run_publishes_the_writers_bytes(tmp_path, monkeypatch, workers):
    root, out = tmp_path / "exp", tmp_path / "out"
    names = ["ctrl/r1/a.tif", "ctrl/r1/b.tif", "drug/r1/a.tif", "drug/r1/b.tif"]
    write_images(root, names)
    read = run_module.resume._read_file

    def last_fails(path, rel, *args):
        if rel == names[-1]:
            raise RuntimeError("stopped")
        return read(path, rel, *args)

    with monkeypatch.context() as patch:
        patch.setattr(run_module.resume, "_read_file", last_fails)
        with pytest.raises(RuntimeError, match="stopped"):
            run_described(root, TYX, output_folder=out, mode=RunMode(workers=workers))
    resumed = run_described(root, TYX, output_folder=out, mode=RunMode(workers=workers))
    assert resumed.written and resumed.read == (names[-1],) and len(resumed.resumed) == 3
    assert_writers_bytes(resumed)
    fresh = run_described(root, TYX, output_folder=tmp_path / "fresh")
    assert fresh.csv_path.read_bytes() == resumed.csv_path.read_bytes()


@pytest.mark.parametrize("workers", [1, 3])
def test_a_failure_while_staging_stops_the_run_as_in_the_calling_thread(
    tmp_path, monkeypatch, workers
):
    root, out = tmp_path / "exp", tmp_path / "out"
    write_images(root, FILES)
    stage_now = run_module.resume.stage_file

    def failing(output, key, rows, *args):
        if rows[0][0].source_relative_path == FILES[1]:
            raise OSError("simulated: the disk is full")
        return stage_now(output, key, rows, *args)

    monkeypatch.setattr(run_module.resume, "stage_file", failing)
    with pytest.raises(OSError, match="simulated: the disk is full") as raised:
        run_described(root, TYX, output_folder=out, mode=RunMode(workers=workers))
    assert not getattr(raised.value, "__notes__", None)  # as before: not a read failure
    assert not (out / "plane_histograms.csv").exists()
