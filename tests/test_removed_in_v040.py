"""What v0.4.0 removes (spec revision 4, §8 PP-2), and what still reads.

Removed: ``convert_tiffs`` with ``zarr_export`` (OME-Zarr copies in the output folder), the
deprecated stand-ins of the code moved to the normalization brick, and
``check_declared_axes``/``declared_axes_transposed``, which read a Y/X swap transposed.
"""

from __future__ import annotations

import importlib
import json

import numpy as np
import pytest

import morphobricks_preprocessing as pp
from morphobricks_preprocessing import prefill_mapping, read_manifest, run_experiment
from morphobricks_preprocessing._io import free_space
from morphobricks_preprocessing.cli import main
from tiff_tree import write_data

REMOVED_NAMES = [
    "check_declared_axes",
    "declared_axes_transposed",
    "ConversionSettings",
    "ConversionEstimate",
    "OmeZarrWriter",
    "estimate_conversion",
    "ome_zarr_relative_path",
    # The deprecated stand-ins of morphobricks_normalization's names.
    "PlaneRecord",
    "compute_plane_histogram",
    "PooledHistogram",
    "read_plane_histograms",
    "min_max",
    "SINGLE",
]


@pytest.mark.parametrize("name", REMOVED_NAMES)
def test_removed_names_are_gone(name):
    assert not hasattr(pp, name)
    assert name not in pp.__all__


@pytest.mark.parametrize(
    "module",
    [
        "zarr_export",
        "_moved",
        "csv_io",
        "dtypes",
        "histogram",
        "normalization",
        "pooling",
        "records",
    ],
)
def test_removed_modules_are_gone(module):
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(f"morphobricks_preprocessing.{module}")


def test_the_run_takes_no_conversion(tmp_path):
    write_data(tmp_path / "exp/a.tif", np.zeros((2, 6, 8), np.uint8), "ZYX")
    with pytest.raises(TypeError, match="convert_tiffs"):
        run_experiment(tmp_path / "exp", prefill_mapping("ZYX"), convert_tiffs=True)
    with pytest.raises(TypeError, match="conversion"):
        run_experiment(tmp_path / "exp", prefill_mapping("ZYX"), conversion=None)


def test_the_cli_takes_no_conversion(tmp_path):
    with pytest.raises(SystemExit) as stopped:
        main(["run", str(tmp_path), "--axes", "ZYX", "--convert-tiff"])
    assert stopped.value.code == 2


def test_a_v03_manifest_with_conversion_keys_is_still_read(tmp_path):
    root = tmp_path / "exp"
    write_data(root / "a.tif", np.zeros((2, 6, 8), np.uint8), "ZYX")
    result = run_experiment(root, prefill_mapping("ZYX"))
    data = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    data["settings"]["convert_tiffs"] = True
    data["settings"]["conversion"] = {"chunk_size": 1024}
    data["files"][0]["ome_zarr_path"] = "ome_zarr/a.ome.zarr"
    result.manifest_path.write_text(json.dumps(data), encoding="utf-8")
    manifest = read_manifest(result.manifest_path)
    assert manifest.file("a.tif") is not None
    assert "convert_tiffs" not in json.dumps(manifest.to_dict())
    # The rows of the earlier run are kept: they were the TIFF's own.
    again = run_experiment(root, prefill_mapping("ZYX"))
    assert again.unchanged == ("a.tif",) and again.read == ()


def test_free_space_is_kept_for_the_disk_check(tmp_path):
    assert free_space(tmp_path / "not" / "yet") > 0
