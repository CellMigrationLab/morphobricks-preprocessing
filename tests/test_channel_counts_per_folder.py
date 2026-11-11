"""Channel counts are compared inside each folder only (Stage 3 S3.3, owner, 11 November 2026)."""

from morphobricks_preprocessing.metadata import check_metadata
from test_metadata import codes, meta


def test_channel_counts_are_compared_inside_each_folder_only():
    # Stage 3 S3.3 (owner decision of 11 November 2026): acquisitions may have other channels.
    files = [
        meta("ctrl/rep1/a.tif", (3, 8, 8), axes="CYX"),
        meta("ctrl/rep2/a.tif", (4, 8, 8), axes="CYX"),
        meta("ctrl/rep2/b.tif", (4, 8, 8), axes="CYX"),
    ]
    assert "different_channel_counts" not in codes(check_metadata(files))
    files.append(meta("ctrl/rep2/c.tif", (3, 8, 8), axes="CYX"))
    (found,) = [i for i in check_metadata(files) if i.code == "different_channel_counts"]
    assert found.paths == ("ctrl/rep2/c.tif",) and "folder ctrl/rep2" in found.message
