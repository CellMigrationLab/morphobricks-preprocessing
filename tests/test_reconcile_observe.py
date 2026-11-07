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
