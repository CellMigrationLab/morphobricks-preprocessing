"""A channel that cannot be measured fails the whole table, naming the plane (Spec ADR 0003 HV3).

The run stops with ``unreadable_planes``; its message and the report name every failed plane by
its T, Z and C index, the channel ID of the description and the cause (contract §6).
"""

import json

import numpy as np
import pytest
import tifffile

from described_tree import run_described
from morphobricks_preprocessing import PlaneReadError, RunMode, planes, prefill_mapping
from morphobricks_preprocessing.discovery_report import REPORT_FILENAME
from morphobricks_preprocessing.run.staging import STAGING_FOLDER
from run_files import only_the_report

TCYX = prefill_mapping("TCYX")
GOOD, BAD = "ctrl/rep1/a.tif", "ctrl/rep1/b.tif"
NAMES = ["DNA", "GFP"]


def write_two_channels(path, seed):
    """Write a T=2, C=2 OME-TIFF, one zlib-compressed strip per page (page = t * 2 + c)."""
    rng = np.random.default_rng(seed)
    data = rng.integers(0, 255, (2, 2, 16, 16), dtype=np.uint8)
    path.parent.mkdir(parents=True, exist_ok=True)
    metadata = {"axes": "TCYX", "Channel": {"Name": NAMES}}
    tifffile.imwrite(
        path, data, ome=True, photometric="minisblack", metadata=metadata, compression="zlib"
    )
    return path


def corrupt_page(path, index):
    """Overwrite the compressed bytes of page ``index``: only that page fails to decode."""
    with tifffile.TiffFile(path) as tif:
        page = tif.pages[index]
        offset, count = page.dataoffsets[0], page.databytecounts[0]
    with open(path, "r+b") as handle:
        handle.seek(offset)
        handle.write(b"\xff" * count)


@pytest.fixture
def experiment(tmp_path):
    root = tmp_path / "exp"
    write_two_channels(root / GOOD, 1)
    corrupt_page(write_two_channels(root / BAD, 2), 3)  # t=1, c=1: GFP
    return root


@pytest.mark.parametrize("workers", [1, 2])
def test_a_channel_that_cannot_be_measured_names_its_plane_and_fails_the_table(
    tmp_path, experiment, workers
):
    out = tmp_path / "out"
    result = run_described(experiment, TCYX, output_folder=out, mode=RunMode(workers=workers))
    assert not result.written
    (found,) = [issue for issue in result.issues if issue.is_error]
    assert found.code == "unreadable_planes" and found.paths == (BAD,)
    # The plane (T, Z, C index), the channel ID of the description and the cause; only it.
    assert f"{BAD}, plane t=1, c=1 (channel GFP): Page 3 of {BAD} cannot be decoded" in (
        found.message
    )
    assert found.message.count("plane t=") == 1 and "DNA" not in found.message
    assert str(experiment) not in found.message  # the experiment's relative paths only
    # The report says the same; no table, manifest or design is written.
    report = json.loads((out / REPORT_FILENAME).read_text(encoding="utf-8"))
    assert [issue["message"] for issue in report["issues"] if issue["code"] == found.code] == [
        found.message
    ]
    assert only_the_report(out, staged=True)
    # Nothing else changes: the readable image is read and staged as before; the failed one
    # is neither (the next run reads it again).
    assert result.read == (GOOD,)
    assert len(list((out / STAGING_FOLDER).glob("*.json"))) == 1


def test_every_failed_plane_is_named(tmp_path, experiment):
    corrupt_page(experiment / BAD, 0)  # t=0, c=0: DNA, too
    result = run_described(experiment, TCYX, output_folder=tmp_path / "out")
    (found,) = [issue for issue in result.issues if issue.is_error]
    # Worker threads name the same planes, in the same words.
    other = run_described(experiment, TCYX, output_folder=tmp_path / "w", mode=RunMode(workers=3))
    assert [issue for issue in other.issues if issue.is_error] == [found]
    assert f"{BAD}, plane t=0, c=0 (channel DNA): Page 0 of {BAD}" in found.message
    assert f"{BAD}, plane t=1, c=1 (channel GFP): Page 3 of {BAD}" in found.message
    assert found.message.count("plane t=") == 2
    assert found.message.index("t=0, c=0") < found.message.index("t=1, c=1")


def test_a_page_that_fails_to_decode_leaves_no_stale_page(tmp_path, monkeypatch):
    # The run measures on after a failed channel: a later read of the last page decoded
    # must decode it again, not use a page the failure let go.
    data = np.arange(3 * 4 * 5, dtype=np.uint8).reshape(3, 4, 5)
    path = tmp_path / "a.tif"
    tifffile.imwrite(path, data, metadata={"axes": "ZYX"}, photometric="minisblack")
    mapping = prefill_mapping("ZYX")
    read_page = planes._read_page

    def failing(series, index, layout, where):
        if index == 1:
            raise PlaneReadError("simulated")
        return read_page(series, index, layout, where)

    monkeypatch.setattr(planes, "_read_page", failing)
    with tifffile.TiffFile(path) as tif:
        layout = planes.plane_layout(path, mapping)
        read = planes._tiff_reader(tif.series[0], layout, mapping, path)
        assert np.array_equal(read({0: 0}).pixels, data[0])
        with pytest.raises(PlaneReadError):
            read({0: 1})
        assert np.array_equal(read({0: 0}).pixels, data[0])
