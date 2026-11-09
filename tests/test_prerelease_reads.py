"""Pre-release follow-ups to #254 and #255: pictures bound before and after the read, and
per-unit channel counts from every image of the unit.

- A channel strip, the native crop of a replicate's picture and the *Images found* picture are
  drawn only when the file is, before **and** after the read, the revision the folder check
  read (as the masks and the axis preview already are): a file rewritten during the read is
  never shown as the image checked.
- A unit's channel count, on which *Check and save* gates the save, comes from every image of
  the unit: images that disagree (or one that cannot be read) leave it unknown
  (``unit_channel_count_unknown``), never the first image's count.
"""

import os

import numpy as np
import pytest
import tifffile

from channel_images import large, write_channels
from morphobricks_preprocessing import header_preview as hp
from morphobricks_preprocessing import preview
from morphobricks_preprocessing.axes import AxisMapping, AxisReview
from morphobricks_preprocessing.channel_labels import UnitLayout
from morphobricks_preprocessing.fingerprint import Fingerprint
from morphobricks_preprocessing.metadata import inspect_image
from morphobricks_preprocessing.preview import FILE_CHANGED, PreviewRefused, StripOptions

KINDS = ["blobs", "filaments", "cells"]


def _later(path, data, axes):
    """Rewrite ``path`` with other pixels and a later modification time."""
    before = path.stat().st_mtime_ns
    tifffile.imwrite(path, data, metadata={"axes": axes}, photometric="minisblack")
    os.utime(path, ns=(before + 5 * 10**9, before + 5 * 10**9))


def _rewriting(real, path, data, axes):
    """Return ``real`` (a plane reader) that rewrites ``path`` once it has read."""

    def read_then_rewrite(*args, **kwargs):
        plane = real(*args, **kwargs)
        _later(path, data, axes)
        return plane

    return read_then_rewrite


# --- item 5: before and after the read ----------------------------------------------------------


def test_a_channel_strip_of_a_file_changed_during_the_read_is_refused(tmp_path, monkeypatch):
    path = tmp_path / "cyx.tif"
    data = np.stack([large(s, 64) for s in (1, 2)])
    tifffile.imwrite(path, data, metadata={"axes": "CYX"}, photometric="minisblack")
    mapping = AxisMapping("CYX", "CYX")
    review = AxisReview(mapping, {"cyx.tif": mapping}, ())
    seen = Fingerprint.of(path)
    other = np.stack([large(s, 64) for s in (3, 4)])
    monkeypatch.setattr(preview, "read_plane", _rewriting(preview.read_plane, path, other, "CYX"))
    with pytest.raises(PreviewRefused) as refused:
        preview.channel_strip(path, "cyx.tif", review, StripOptions(expected=seen))
    assert refused.value.code == FILE_CHANGED
    assert "changed since the folder check read it" in str(refused.value)


def test_a_plane_read_across_a_rewrite_is_not_kept_in_the_cache(tmp_path, monkeypatch):
    # Review of #257: the refused strip had already cached its torn planes under the fingerprint
    # taken before the read, so a file put back with that size and time (a restore that keeps
    # times) would have been shown from them without a read.
    path = tmp_path / "cyx.tif"
    data = np.stack([large(s, 64) for s in (1, 2)])
    tifffile.imwrite(path, data, metadata={"axes": "CYX"}, photometric="minisblack")
    mapping = AxisMapping("CYX", "CYX")
    review = AxisReview(mapping, {"cyx.tif": mapping}, ())
    seen, cache = Fingerprint.of(path), preview.PreviewCache()
    real = preview.read_plane
    other = np.stack([large(s, 64) for s in (3, 4)])
    monkeypatch.setattr(preview, "read_plane", _rewriting(real, path, other, "CYX"))
    with pytest.raises(PreviewRefused):
        preview.channel_strip(path, "cyx.tif", review, StripOptions(expected=seen, cache=cache))
    assert len(cache) == 0  # nothing read across the rewrite is kept
    monkeypatch.setattr(preview, "read_plane", real)
    now = Fingerprint.of(path)
    preview.channel_strip(path, "cyx.tif", review, StripOptions(expected=now, cache=cache))
    assert len(cache) == 2  # the control: a clean read is kept


def test_the_native_crop_of_a_file_changed_during_its_read_is_not_shown(tmp_path, monkeypatch):
    pytest.importorskip("matplotlib")
    from morphobricks_preprocessing import notebook_channel_thumbs as nct

    path = tmp_path / "big.tif"
    tifffile.imwrite(path, large(1, 1024), metadata={"axes": "YX"}, photometric="minisblack")
    mapping = AxisMapping("YX", "YX")
    review = AxisReview(mapping, {"big.tif": mapping}, ())
    layout = UnitLayout(("ctrl", "rep1", "single"), "big.tif", 1, images=1)

    def thumbs():
        return nct.Thumbnails(tmp_path, review, 2**30, {"big.tif": Fingerprint.of(path)})

    assert thumbs().tile(layout, False).crop_png is not None  # the control: a crop is drawn
    bound = thumbs()
    monkeypatch.setattr(nct, "read_plane", _rewriting(nct.read_plane, path, large(2, 1024), "YX"))
    found = bound.tile(layout, False)
    assert found.crop_png is None
    assert "changed since the folder check read it" in found.crop_text


def test_the_images_found_picture_of_a_file_changed_during_its_read_is_not_shown(
    tmp_path, monkeypatch
):
    path = tmp_path / "big.tif"
    tifffile.imwrite(path, large(1, 512), metadata={"axes": "YX"}, photometric="minisblack")
    meta, bound = inspect_image(path, "big.tif"), {"big.tif": Fingerprint.of(path)}
    assert hp.header_preview(tmp_path, meta, bound=bound).pictures is not None  # the control
    monkeypatch.setattr(hp, "read_plane", _rewriting(hp.read_plane, path, large(2, 512), "YX"))
    found = hp.header_preview(tmp_path, meta, bound=bound)
    assert found.pictures is None
    assert "changed since the folder check read it" in found.problem


# --- item 6: the channel count of every image -----------------------------------------------------


def _unit_with_images(tmp_path, second):
    from morphobricks_preprocessing import preflight
    from morphobricks_preprocessing.axis_review import review_axes

    root = tmp_path / "exp"
    write_channels(root / "ctrl/rep1/a.tif", ["DAPI", "Actin", "Ch3"], KINDS, seed=1)
    write_channels(root / "ctrl/rep1/b.tif", second, KINDS[: len(second)], seed=2)
    write_channels(root / "drug/rep1/a.tif", ["DAPI", "Actin", "Ch3"], KINDS, seed=3)
    pre = preflight(root, tmp_path / "out")
    return pre, review_axes(pre.metadata, pre.suggested_mapping)


def test_a_unit_whose_images_disagree_in_channel_count_has_no_count(tmp_path):
    from morphobricks_preprocessing.channel_units import (
        unit_channel_counts,
        unit_images,
        unit_layouts,
    )
    from morphobricks_preprocessing.draft import draft_from_discovery
    from morphobricks_preprocessing.draft_answers import (
        ChannelAnswer,
        DraftAnswers,
        UnitAnswer,
        missing_confirmations,
    )

    pre, review = _unit_with_images(tmp_path, ["DAPI", "Actin"])  # a.tif has 3, b.tif 2
    keys = unit_images(pre.discovery, ()).keys()
    layouts = {u.key: u for u in unit_layouts(keys, pre.discovery, (), pre.metadata, review)}
    mixed, other = layouts[("ctrl", "rep1", "single")], layouts[("drug", "rep1", "single")]
    assert mixed.channels == 3 and not mixed.counts_agree  # the first image alone says 3
    assert other.counts_agree
    counts = unit_channel_counts(layouts.values())
    assert counts == {("drug", "rep1", "single"): 3}  # never the first image's 3
    answers = DraftAnswers(
        "absent",
        "x",
        channels={t: ChannelAnswer() for t in ("DNA", "ACTIN", "PHASE")},
        units={k: UnitAnswer(("DNA", "ACTIN", "PHASE"), "channels") for k in layouts},
    )
    draft = draft_from_discovery(pre.discovery, ())
    open_ = missing_confirmations(draft, answers, channel_counts=counts)
    assert ("unit_channel_count_unknown", "ctrl / rep1 / single") in {
        (q.code, q.subject) for q in open_
    }


def test_a_unit_whose_images_agree_in_count_but_not_in_names_keeps_its_count(tmp_path):
    from morphobricks_preprocessing.channel_units import (
        unit_channel_counts,
        unit_images,
        unit_layouts,
    )

    pre, review = _unit_with_images(tmp_path, ["Actin", "DAPI", "Ch3"])
    keys = unit_images(pre.discovery, ()).keys()
    layouts = unit_layouts(keys, pre.discovery, (), pre.metadata, review)
    mixed = next(u for u in layouts if u.key == ("ctrl", "rep1", "single"))
    assert not mixed.uniform and mixed.counts_agree  # names differ, the count does not
    assert unit_channel_counts(layouts)[("ctrl", "rep1", "single")] == 3
