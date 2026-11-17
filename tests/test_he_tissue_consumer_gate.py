"""Gate P3: the downstream consumer's guarantees for a tissue mask (design §9 P3; review w B3).

Run with the consumer installed: fiberfeatures v0.7.0 (merged at ``a7f57c5``; the gate is
complete once that release is tagged). Each test makes a real tissue mask with the helper,
declares it ``binary``, runs preprocessing and the masks step again, and asks fiberfeatures:

- (a) an all-zero region gives ``valid_empty`` with reason ``empty_region``: no value is 0
  (the image's values are undefined), and the other image is measured (the run goes on);
- (b) CellPatcher cell mode with the region and no ``objects`` mask is refused before any read,
  naming the region: a binary region is never taken as cells.

Without fiberfeatures these tests skip with that reason; a skipped gate test is not a pass (the
PR lists the skips).
"""

from __future__ import annotations

import warnings
from importlib import metadata

import numpy as np
import pytest

pytest.importorskip("skimage")
pytest.importorskip("fiberfeatures", reason="gate P3 needs fiberfeatures v0.7.0")
FIBERFEATURES = metadata.version("fiberfeatures")
if tuple(int(p) for p in FIBERFEATURES.split(".")[:2]) < (0, 7):
    pytest.skip("gate P3 needs fiberfeatures v0.7.0 (a7f57c5)", allow_module_level=True)

from he_tissue_tree import (  # noqa: E402
    IDENTITY_PATCH,
    UNIT,
    declared,
    description,
    identities,
    rgb_run,
    synthetic_he,
)
from morphobricks_preprocessing import MaskRequest, RunInputs, prefill_mapping  # noqa: E402
from morphobricks_preprocessing.testing_described import publish_and_run  # noqa: E402

WHITE = np.full((96, 96, 3), 250, np.uint8)


@pytest.fixture
def declared_run(tmp_path, monkeypatch):
    """Two RGB images with a tissue mask declared binary: a.tif all background, b.tif H&E."""
    monkeypatch.setattr(IDENTITY_PATCH, lambda: identities())
    run = rgb_run(tmp_path, {"a.tif": WHITE, "b.tif": synthetic_he()[0]})
    for image in (f"{UNIT}/a.tif", f"{UNIT}/b.tif"):
        run.he_tissue_mask(image, output_mask_id="HETissue")
    publish_and_run(
        tmp_path, run.experiment_root, run.masks_root, prefill_mapping("YXS"),
        declared(description(2)),
    )  # fmt: skip
    return run.output_folder


def _config():
    from fiberfeatures import FiberFeaturesConfig, RGBInputConfig

    return FiberFeaturesConfig(
        scales=(1.0,), allow_scipy_fallback=True, rgb_input=RGBInputConfig("he_eosin")
    )


def test_a_an_all_zero_tissue_mask_is_an_empty_region_never_zero(declared_run):
    from fiberfeatures import analyze_metadata, build_metadata_from_run

    meta = build_metadata_from_run(declared_run, rgb=True)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        features, _, status = analyze_metadata(
            meta, "ecm", _config(), preprocessing_run=declared_run, measure_patches=False,
            masks=(MaskRequest("HETissue", "region"),), return_status=True,
        )  # fmt: skip
    rows = features.set_index("source_relative_path")
    empty, measured = rows.loc[f"{UNIT}/a.tif"], rows.loc[f"{UNIT}/b.tif"]
    assert empty["roi_area_px"] == 0
    assert np.isnan(empty["ridge_support_mean"]) and np.isnan(empty["nematic_order"])
    assert measured["roi_area_px"] > 0 and np.isfinite(measured["ridge_support_mean"])
    by_image = status.set_index("source_relative_path")
    assert by_image.loc[f"{UNIT}/a.tif", ["status", "status_reason"]].tolist() == [
        "valid_empty",
        "empty_region",
    ]
    assert by_image.loc[f"{UNIT}/b.tif", "status"] == "processed"


def test_b_cell_mode_never_takes_the_tissue_region_as_cells(declared_run, monkeypatch):
    from cellpatcher import CellPatcherConfig
    from fiberfeatures import analyze_metadata, build_metadata_from_run

    meta = build_metadata_from_run(declared_run, rgb=True)

    def refuse(*args, **kwargs):
        raise AssertionError("a pixel was read before the refusal")

    for name in ("read_plane", "read_mask", "read_mask_window", "read_objects"):
        monkeypatch.setattr(RunInputs, name, refuse)
    with pytest.raises(ValueError) as refused:
        analyze_metadata(
            meta, "ecm", _config(), patch_config=CellPatcherConfig(mode="cell"),
            preprocessing_run=declared_run, masks=(MaskRequest("HETissue", "region"),),
        )  # fmt: skip
    message = str(refused.value)
    assert "cell mode needs an objects mask" in message
    assert "'HETissue' is never taken as cells" in message
