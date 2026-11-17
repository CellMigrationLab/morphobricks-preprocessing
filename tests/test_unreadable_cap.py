"""The ``unreadable_planes`` message names at most 20 failed planes per image (Spec ADR 0003 HV3).

Owner decision of 17 November 2026: the message names each image's first 20 failed planes, in
the order of today (images sorted, then planes in reading order), followed by ``and N more in
<image>``; each image is capped on its own. The run report keeps the whole list
(``unreadable_planes`` of ``discovery_report.json`` and the text report), byte-identical for
any number of workers, and the notebook's report view shows it, folded per image (#334). The
whole table still fails; nothing else changes.
"""

import html
import json
import re

import numpy as np
import pytest
import tifffile

from described_tree import run_described
from morphobricks_preprocessing import RunMode, prefill_mapping
from morphobricks_preprocessing.discovery_report import REPORT_FILENAME, REPORT_TEXT_FILENAME
from morphobricks_preprocessing.report_view import report_html
from test_run_failed_channel import corrupt_page

TCYX = prefill_mapping("TCYX")
A, B = "ctrl/rep1/a.tif", "ctrl/rep1/b.tif"


def write_frames(path, frames, seed):
    """Write a T=``frames``, C=2 OME-TIFF whose DNA plane fails in every frame.

    One zlib-compressed strip per page (page = t * 2 + c); the DNA pages (c=0) are corrupted.
    """
    rng = np.random.default_rng(seed)
    data = rng.integers(0, 255, (frames, 2, 16, 16), dtype=np.uint8)
    path.parent.mkdir(parents=True, exist_ok=True)
    metadata = {"axes": "TCYX", "Channel": {"Name": ["DNA", "GFP"]}}
    tifffile.imwrite(
        path, data, ome=True, photometric="minisblack", metadata=metadata, compression="zlib"
    )
    for t in range(frames):
        corrupt_page(path, 2 * t)


def plane(rel, t):
    return f"{rel}, plane t={t}, c=0 (channel DNA): Page {2 * t} of {rel} cannot be decoded"


def run(root, out, workers=1):
    result = run_described(root, TCYX, output_folder=out, mode=RunMode(workers=workers))
    assert not result.written
    (found,) = [issue for issue in result.issues if issue.is_error]
    assert found.code == "unreadable_planes"
    report = json.loads((out / REPORT_FILENAME).read_text(encoding="utf-8"))
    return found, report


def test_25_failures_name_20_and_5_more_and_the_report_lists_all(tmp_path):
    root = tmp_path / "exp"
    write_frames(root / A, 25, 1)
    found, report = run(root, tmp_path / "out")
    assert found.paths == (A,)
    assert found.message.count("plane t=") == 20
    for t in range(20):
        assert plane(A, t) in found.message
    assert "t=20," not in found.message
    # The report lists all 25, in reading order; its issue carries the capped message.
    listed = report["unreadable_planes"][A]
    assert len(listed) == 25
    assert f"What failed: {'; '.join(listed[:20])}; and 5 more in {A}." in found.message
    assert found.message.endswith("The run report lists every failed plane.")
    assert all(text.startswith(plane(A, t)) for t, text in enumerate(listed))
    assert [i["message"] for i in report["issues"] if i["code"] == found.code] == [found.message]
    text = (tmp_path / "out" / REPORT_TEXT_FILENAME).read_text(encoding="utf-8")
    assert f"Unreadable: 25 failures in {A}" in text
    assert all(f"    {entry}" in text for entry in listed)


def test_exactly_20_failures_have_no_suffix(tmp_path):
    root = tmp_path / "exp"
    write_frames(root / A, 20, 1)
    found, report = run(root, tmp_path / "out")
    assert found.message.count("plane t=") == 20
    assert "more in" not in found.message
    assert "run report lists" not in found.message
    listed = report["unreadable_planes"][A]
    assert len(listed) == 20
    assert found.message.endswith(f"What failed: {'; '.join(listed)}.")


def test_two_images_are_capped_independently(tmp_path):
    root = tmp_path / "exp"
    write_frames(root / A, 25, 1)
    write_frames(root / B, 22, 2)
    found, report = run(root, tmp_path / "out")
    assert found.paths == (A, B)
    assert found.message.count("plane t=") == 40
    full = report["unreadable_planes"]
    assert list(full) == [A, B]  # images in sorted order
    assert {rel: len(texts) for rel, texts in full.items()} == {A: 25, B: 22}
    # Each image followed by its own count, in the order of before.
    expected = (
        f"What failed: {'; '.join(full[A][:20])}; and 5 more in {A}; "
        f"{'; '.join(full[B][:20])}; and 2 more in {B}."
    )
    assert expected in found.message


@pytest.mark.parametrize("workers", [2, 3])
def test_the_message_and_the_report_list_are_identical_for_any_number_of_workers(tmp_path, workers):
    root = tmp_path / "exp"
    write_frames(root / A, 25, 1)
    write_frames(root / B, 22, 2)
    serial, serial_report = run(root, tmp_path / "w1")
    found, report = run(root, tmp_path / f"w{workers}", workers)
    assert found == serial
    dump = json.dumps(report["unreadable_planes"], indent=2, ensure_ascii=False).encode()
    assert (
        dump
        == json.dumps(serial_report["unreadable_planes"], indent=2, ensure_ascii=False).encode()
    )
    assert report["issues"] == serial_report["issues"]


def _words(page):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", page))).strip()


def test_the_notebook_report_view_lists_every_failed_plane_folded_per_image(tmp_path):
    # Issue #334: the capped message points to the run report, so its view shows the whole list.
    root = tmp_path / "exp"
    write_frames(root / A, 25, 1)
    write_frames(root / B, 22, 2)
    _, report = run(root, tmp_path / "out")
    page = report_html(report)
    assert ">Unreadable planes</div>" in page
    words = _words(page)
    assert "47 failed planes in 2 images; each image lists every failed plane." in words
    # One fold per image, closed, in the report's order, each with every failed plane.
    folds = re.findall(r"<details class='mb-about'><summary>([^<]*)</summary>", page)
    assert f"{A} (25 failed planes)" in folds and f"{B} (22 failed planes)" in folds
    assert folds.index(f"{A} (25 failed planes)") < folds.index(f"{B} (22 failed planes)")
    assert "<details open" not in page
    for rel in (A, B):
        for entry in report["unreadable_planes"][rel]:
            assert f"<div>{html.escape(entry)}</div>" in page
    assert page.index(">Unreadable planes</div>") < page.index(">Checks</div>")


def test_the_report_view_has_no_unreadable_planes_part_without_failures():
    assert "Unreadable planes" not in report_html({"unreadable_planes": {}})
    assert "Unreadable planes" not in report_html({})
