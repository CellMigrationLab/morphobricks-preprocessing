"""``observe_folders``: the image count of each unit of a folder walk, nothing else."""

from morphobricks_preprocessing.description_reconcile import ObservedUnit, UnitFolders
from morphobricks_preprocessing.discovery import discover_experiment
from morphobricks_preprocessing.reconcile_observe import observe_folders
from tiff_tree import write_tiff


def test_each_unit_has_its_image_count_and_no_channel_evidence(tmp_path):
    for name in ("a.tif", "b.tif", "c.tif"):
        write_tiff(tmp_path / "CTRL" / "rep1" / name)
    write_tiff(tmp_path / "DrugA" / "rep1" / "acq1" / "a.tif")
    seen = observe_folders(discover_experiment(tmp_path))
    assert seen == {
        UnitFolders("CTRL", "rep1", "single"): ObservedUnit(3),
        UnitFolders("DrugA", "rep1", "acq1"): ObservedUnit(1),
    }
    assert all(unit.channel_count is None and unit.header_names is None for unit in seen.values())


def test_excluded_images_are_not_counted_and_an_emptied_folder_is_not_a_unit(tmp_path):
    for name in ("a.tif", "b.tif"):
        write_tiff(tmp_path / "CTRL" / "rep1" / name)
    write_tiff(tmp_path / "DrugA" / "rep1" / "a.tif")
    seen = observe_folders(
        discover_experiment(tmp_path), excluded={"CTRL/rep1/a.tif", "DrugA/rep1/a.tif"}
    )
    assert seen == {UnitFolders("CTRL", "rep1", "single"): ObservedUnit(1)}


def test_the_headers_read_give_each_unit_its_channel_evidence(tmp_path):
    # Regression (batch 34b BLOCKER 4): only image counts were observed, so a header rewritten
    # under the same files was never a channel change.
    from channel_images import write_channels
    from morphobricks_preprocessing.metadata import inspect_image

    kinds = ["blobs", "filaments"]
    write_channels(tmp_path / "CTRL" / "rep1" / "a.tif", ["DAPI", "Actin"], kinds)
    write_channels(tmp_path / "CTRL" / "rep1" / "b.tif", ["DAPI", "Actin"], kinds, seed=2)
    write_channels(tmp_path / "DrugA" / "rep1" / "a.tif", ["DAPI", "Actin"], kinds)
    write_channels(tmp_path / "DrugA" / "rep1" / "b.tif", ["Actin", "DAPI"], kinds, seed=2)
    write_tiff(tmp_path / "Plain" / "rep1" / "a.tif")
    discovery = discover_experiment(tmp_path)
    metas = [inspect_image(tmp_path / rel, rel) for rel in discovery.images]
    seen = observe_folders(discovery, metadata=metas)
    assert seen[UnitFolders("CTRL", "rep1", "single")] == ObservedUnit(
        2, 2, ("DAPI", "Actin"), rgb=False
    )
    assert not seen[UnitFolders("DrugA", "rep1", "single")].uniform  # its headers disagree
    plain = seen[UnitFolders("Plain", "rep1", "single")]
    assert plain.header_names is None and plain.uniform
    assert observe_folders(discovery) == {k: ObservedUnit(v.image_count) for k, v in seen.items()}


def test_a_colour_header_flag_changed_under_the_same_files_is_a_channel_change(tmp_path):
    # Regression (review of #255): the RGB flag was not observed, so an RGB unit rewritten as a
    # plain three-channel image (same files, count and channel count) reconciled as unchanged
    # while the run's checks refuse IMAGE_TYPE_CONTRADICTS_HEADER.
    import numpy as np
    import tifffile

    from morphobricks_preprocessing.description import description_from_bytes
    from morphobricks_preprocessing.description_reconcile import CHANNELS_CHANGED, reconcile
    from morphobricks_preprocessing.metadata import inspect_image
    from spec_pin import fixtures

    rgb = next(f for f in fixtures("valid") if f.name.startswith("v28_"))
    published = description_from_bytes(rgb.data)
    for condition in ("CTRL", "DrugA"):
        for repeat in ("rep1", "rep2"):
            for i in range(8):
                path = tmp_path / condition / repeat / f"{i}.tif"
                path.parent.mkdir(parents=True, exist_ok=True)
                tifffile.imwrite(path, np.zeros((8, 8, 3), np.uint8), photometric="rgb")

    def seen():
        discovery = discover_experiment(tmp_path)
        metas = [inspect_image(tmp_path / rel, rel) for rel in discovery.images]
        return observe_folders(discovery, metadata=metas)

    assert reconcile(published, seen()).reopened == {}
    for i in range(8):
        plain = np.zeros((3, 8, 8), np.uint8)
        path = tmp_path / "CTRL" / "rep1" / f"{i}.tif"
        tifffile.imwrite(path, plain, photometric="minisblack", metadata={"axes": "CYX"})
    observed = seen()
    assert observed[UnitFolders("CTRL", "rep1", "single")].rgb is False
    assert observed[UnitFolders("CTRL", "rep2", "single")].rgb is True
    assert reconcile(published, observed).reopened == {"rep1": (CHANNELS_CHANGED,)}
