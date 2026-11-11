"""OME-Zarr copies: a recorded conversion, asked on the first screen (owner, 11 November 2026).

``convert_experiment(store="ome-zarr")`` writes every image that is read as an OME-Zarr image
(OME-NGFF 0.4), plane for plane under the confirmed roles, with the conversion record; the
notebook offers it on the Axes step once the roles are confirmed, when the option of the first
screen is ticked. The originals are only read; records without OME-Zarr copies are unchanged.
"""

import hashlib
import html
import json
import os
import re

import numpy as np
import pytest
import tifffile

pytest.importorskip("zarr")

from morphobricks_preprocessing.axes import prefill_mapping  # noqa: E402
from morphobricks_preprocessing.axis_review import review_axes  # noqa: E402
from morphobricks_preprocessing.conversion import convert_experiment  # noqa: E402
from morphobricks_preprocessing.conversion_record import (  # noqa: E402
    RECORD_FILENAME,
    CopyEntry,
    read_record,
)
from morphobricks_preprocessing.ome_zarr_copy import zarr_copy_name  # noqa: E402
from morphobricks_preprocessing.planes import iter_planes  # noqa: E402
from morphobricks_preprocessing.projection import Projection  # noqa: E402
from morphobricks_preprocessing.run import preflight  # noqa: E402
from test_notebook_check_save import FOLDERS, answer_all  # noqa: E402
from test_notebook_projection import NAMES, digests, write_stacks  # noqa: E402


def _text(text):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text))).strip()


def read_copy(dest, rel):
    """Return the copy's array as the brick reads it, in the copy's axes order."""
    pre = preflight(dest, dest.parent / "check_out")
    assert not [i for i in pre.issues if i.is_error], pre.issues
    meta = next(f for f in pre.metadata.files if f.relative_path == rel)
    mapping = review_axes(pre.metadata, pre.suggested_mapping).file_mappings[rel]
    out = np.zeros(meta.shape, meta.dtype)
    roles = [r.lower() for r in meta.axes[:-2]]
    for plane in iter_planes(dest / rel, mapping):
        out[tuple(plane.coordinates[r] for r in roles)] = plane.pixels
    return meta, out


def test_a_copy_keeps_the_folder_and_the_position_id():
    assert zarr_copy_name("ctrl/rep1/fov_01.tif") == "ctrl/rep1/fov_01.ome.zarr"
    assert zarr_copy_name("fov.ome.tiff") == "fov.ome.zarr"


def test_every_image_is_written_plane_for_plane_with_its_metadata_and_recorded(tmp_path):
    root, dest = tmp_path / "exp", tmp_path / "copies"
    data = write_stacks(root, "TCYX", (2, 3, 16, 20))
    before = digests(root)
    result = convert_experiment(root, prefill_mapping("TCYX"), dest, store="ome-zarr")
    assert result.written and not result.issues
    assert list(result.converted) == [f"{f}/a.ome.zarr" for f in FOLDERS]  # all of them
    assert digests(root) == before
    meta, copy = read_copy(dest, "ctrl/rep1/a.ome.zarr")
    assert meta.axes == "TCYX" and np.array_equal(copy, data["ctrl/rep1/a.tif"])
    assert meta.channel_names == tuple(NAMES)
    assert (meta.physical_sizes.x, meta.physical_sizes.x_unit) == (0.3, "micrometer")
    attrs = json.loads((dest / "ctrl/rep1/a.ome.zarr/.zattrs").read_text(encoding="utf-8"))
    assert attrs["multiscales"][0]["version"] == "0.4"
    # The time step is not in the header: a placeholder scale, named as unknown, read as none.
    assert attrs["multiscales"][0]["metadata"] == {"unknown_scale": ["t"]}
    assert meta.physical_sizes.t is None
    record, sha256 = read_record(dest)
    entry = record.entry("ctrl/rep1/a.ome.zarr")
    assert (
        entry.source_relative_path == "ctrl/rep1/a.tif" and entry.source_path == "ctrl/rep1/a.tif"
    )
    assert entry.action == "converted" and entry.copy_axes == "TCYX"
    assert entry.significant_bits is None and entry.copy_sha256
    assert result.record_sha256 == sha256
    # The same conversion again writes nothing and keeps the record.
    again = convert_experiment(root, prefill_mapping("TCYX"), dest, store="ome-zarr")
    assert again.written and len(again.reused) == len(FOLDERS) and again.record_sha256 == sha256


def test_the_same_copy_is_the_same_bytes(tmp_path):
    root = tmp_path / "exp"
    write_stacks(root, "CYX", (3, 16, 16))

    def tree(dest):
        convert_experiment(root, prefill_mapping("CYX"), dest, store="ome-zarr")
        store = dest / "ctrl/rep1/a.ome.zarr"
        return {
            p.relative_to(store).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(store.rglob("*"))
            if p.is_file()
        }

    first = tree(tmp_path / "one")
    assert first == tree(tmp_path / "two") and "0/0/0/0" in first and ".zarray" not in first
    assert "0/.zarray" in first


def test_a_projection_can_be_written_as_ome_zarr(tmp_path):
    root, dest = tmp_path / "exp", tmp_path / "copies"
    data = write_stacks(root, "ZCYX", (4, 3, 16, 16))
    result = convert_experiment(
        root, prefill_mapping("ZCYX"), dest, projection=Projection("max"), store="ome-zarr"
    )
    assert result.written and len(result.projected) == len(FOLDERS)
    meta, copy = read_copy(dest, "drug/rep2/a.ome.zarr")
    assert meta.axes == "CYX" and np.array_equal(copy, data["drug/rep2/a.tif"].max(axis=0))
    entry = read_record(dest)[0].entry("drug/rep2/a.ome.zarr")
    assert entry.projection.source_relative_path == "drug/rep2/a.tif"
    attrs = json.loads((dest / "drug/rep2/a.ome.zarr/.zattrs").read_text(encoding="utf-8"))
    assert "max" in attrs["morphobricks"]["description"]


def test_a_changed_source_is_named_by_the_check_of_the_copies(tmp_path):
    root, dest = tmp_path / "exp", tmp_path / "copies"
    write_stacks(root, "CYX", (3, 16, 16))
    convert_experiment(root, prefill_mapping("CYX"), dest, store="ome-zarr")
    clean = preflight(dest, tmp_path / "out")
    assert clean.conversion is not None and not clean.conversion.issues
    path = root / "ctrl/rep1/a.tif"
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10**10))
    after = preflight(dest, tmp_path / "out")
    assert [i.code for i in after.conversion.issues] == ["conversion_source_changed"]
    assert "ctrl/rep1/a.tif" in str(after.conversion.issues[0].paths)  # the source's own path


def test_an_unknown_store_is_refused_before_anything_is_read(tmp_path):
    root, dest = tmp_path / "exp", tmp_path / "copies"
    for folder, name in (("ctrl/rep1", "a.tif"), ("ctrl/rep1", "b.tif"), ("drug/rep1", "a.tif")):
        path = root / folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        tifffile.imwrite(path, np.zeros((2, 8, 8), np.uint8), ome=True, metadata={"axes": "CYX"})
    with pytest.raises(ValueError, match="store must be one of"):
        convert_experiment(root, prefill_mapping("CYX"), dest, store="zarr")
    assert not dest.exists()
    assert convert_experiment(root, prefill_mapping("CYX"), dest, store="ome-zarr").written


def test_two_images_that_would_share_a_copy_name_stop_the_plan():
    from morphobricks_preprocessing.conversion_plan import _stored
    from morphobricks_preprocessing.copy_axes import CopyLayout
    from morphobricks_preprocessing.fingerprint import Fingerprint
    from morphobricks_preprocessing.planned_copy import PlannedCopy

    def planned(rel):
        entry = CopyEntry(rel, "converted", None, Fingerprint(1, 1), copy_axes="YX")
        return PlannedCopy(entry, CopyLayout("YX", (4, 4)))

    kept, issues = _stored((planned("c/r/a.tif"), planned("c/r/A.tiff")), [], True)
    assert kept == () and [i.code for i in issues] == ["copy_names_collide"]
    assert set(issues[0].paths) == {"c/r/a.tif", "c/r/A.tiff"}  # the sources, by their own names
    kept, issues = _stored((planned("c/r/a.tif"), planned("c/r/b.tif")), [], True)
    assert [k.relative_path for k in kept] == ["c/r/a.ome.zarr", "c/r/b.ome.zarr"] and not issues


def test_records_without_ome_zarr_copies_are_written_as_before(tmp_path):
    root, dest = tmp_path / "exp", tmp_path / "copies"
    write_stacks(root, "ZCYX", (4, 3, 16, 16))
    convert_experiment(root, prefill_mapping("ZCYX"), dest, projection=Projection("max"))
    text = (dest / RECORD_FILENAME).read_text(encoding="utf-8")
    assert "source_relative_path" in text  # the projection's own, as before
    record = read_record(dest)[0]
    assert all(e.source_relative_path is None for e in record.files)
    assert all("source_relative_path" not in e.to_dict() for e in record.files)
    entry = record.files[0]
    assert CopyEntry.from_dict(entry.to_dict()) == entry


# --- the notebook --------------------------------------------------------------------------------

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")

from morphobricks_preprocessing import notebook_style as ui  # noqa: E402
from morphobricks_preprocessing.notebook_ui import PreprocessingNotebook  # noqa: E402
from morphobricks_preprocessing.notebook_zarr_copy import OPTION_LABEL  # noqa: E402


def started(tmp_path, axes, shape, *, option=True):
    root, out = tmp_path / "exp", tmp_path / "out"
    data = write_stacks(root, axes, shape)
    nb = PreprocessingNotebook(str(root), output_folder=str(out))
    nb.steps()
    nb.zarr_option.value = option
    nb.scan_button.click()
    nb.check_button.click()
    return nb, root, out, data


def test_the_option_is_on_the_first_screen_unticked_and_writes_nothing(tmp_path):
    nb, root, out, _ = started(tmp_path, "CYX", (3, 16, 16), option=False)
    assert nb.zarr_option.description == OPTION_LABEL and not nb.zarr_option.value
    assert nb.zarr_option_row in nb.folders_card.children
    assert not ui.is_visible(nb.zarr_card)
    nb.quick_confirm.click()
    assert not ui.is_visible(nb.zarr_card) and nb._write_zarr_copy() is None
    nb.zarr_option.value = True  # ticked later: offered at once, the roles being confirmed
    assert ui.is_visible(nb.zarr_card) and not nb.zarr_button.disabled
    assert not out.joinpath("ome_zarr").exists()


def test_the_copy_waits_for_the_confirmed_axes_and_says_so(tmp_path):
    nb, root, out, _ = started(tmp_path, "CYX", (3, 16, 16))
    assert ui.is_visible(nb.zarr_card) and nb.zarr_button.disabled
    assert "the axis roles are not confirmed" in _text(nb.zarr_blocker.value)
    assert nb._write_zarr_copy() is None and not out.joinpath("ome_zarr").exists()
    nb.quick_confirm.click()
    assert not nb.zarr_button.disabled and nb.zarr_blocker.value == ""
    assert str(out / "ome_zarr") in nb.zarr_where.value


def test_the_copy_is_written_recorded_and_used_by_the_later_steps(tmp_path):
    nb, root, out, data = started(tmp_path, "TCYX", (2, 3, 16, 16))
    before = digests(root)
    nb.quick_confirm.click()
    result = nb._write_zarr_copy()
    copies = out / "ome_zarr"
    assert result.written and len(result.converted) == len(FOLDERS)
    assert nb.folder.value == str(copies) and nb.output.value == str(out)
    assert nb.preflight.discovery.root == copies and nb.confirmed_mapping is None
    assert all(f.is_ome_zarr for f in nb.preflight.metadata.files)
    assert "recorded copies of" in _text(nb.copy_note.value)
    assert root.resolve().as_posix() in nb.copy_note.value  # as the record spells it
    assert "OME-Zarr already" in _text(nb.zarr_where.value)  # nothing more to write
    assert nb.scan_viewer.view is not None  # the viewer reads the copies
    nb.quick_confirm.click()
    answer_all(nb)
    assert nb.save_experiment()
    run = nb.run()
    assert run.written and run.plane_count == len(FOLDERS) * 2
    assert digests(root) == before
    record = read_record(copies)[0]
    assert record.source_root == root.resolve().as_posix()
    assert {e.source_path for e in record.files} == set(data)


def test_ticked_with_z_stacks_the_projection_is_written_as_ome_zarr(tmp_path):
    nb, root, out, data = started(tmp_path, "ZCYX", (4, 3, 16, 16))
    assert ui.is_visible(nb.projection_card) and not ui.is_visible(nb.zarr_card)
    nb.projection_method.value = "max"
    nb.preview_checked.button.click()
    assert "as OME-Zarr" in _text(nb.projection_where.value)
    result = nb._write_projection()
    copies = out / "projected_max_zarr"
    assert result.written and result.destination == copies
    assert (copies / "ctrl/rep1/a.ome.zarr/.zattrs").is_file()
    assert nb.folder.value == str(copies) and "Z" not in nb.mapping.roles
    nb.quick_confirm.click()
    answer_all(nb)
    assert nb.save_experiment() and nb.run().written
