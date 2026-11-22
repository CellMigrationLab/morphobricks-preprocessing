"""Why a QC metric is blank, and which row combinations the table refuses (contract §19).

Every blank metric has a reason in the ``blank_reasons`` column, besides the row's
``qc_status``. The reader (and the writer) refuse the combinations no measurement makes: a
status with metrics it blanks, a blank metric without a reason, a value outside its range.
"""

import csv
import io

import numpy as np
import pytest
from morphobricks_normalization.histogram import compute_plane_histogram

from morphobricks_preprocessing.image_qc import METRICS, PlaneQc
from morphobricks_preprocessing.image_qc_table import (
    COLUMNS,
    ImageQcFormatError,
    QcRow,
    read_image_qc,
    table_text,
    write_image_qc,
)

IDENTITY = ("a", "a.tif", "0", "1", "c", "b", "t", "x/a.tif", "x/a.tif::z=0,t=1", "GFP")


def measure(pixels):
    qc = PlaneQc(pixels.shape, pixels.dtype)
    qc.add(0, 0, pixels)
    return qc.finish(compute_plane_histogram(pixels))


def reasons(pixels):
    return dict(measure(pixels).reasons)


def noisy(shape=(40, 48), dtype=np.uint16):
    return np.random.default_rng(0).integers(100, 1000, shape).astype(dtype)


def test_a_plane_with_every_metric_has_no_reason():
    found = measure(noisy())
    assert found.reasons == () and all(getattr(found, name) is not None for name in METRICS)


def test_a_constant_plane_has_no_otsu_split_and_no_correlation_variance():
    assert reasons(np.full((32, 32), 7, dtype=np.uint16)) == {
        "otsu_threshold": "constant_plane",
        "otsu_separation": "constant_plane",
        "foreground_fraction": "constant_plane",
        "correlation_1": "no_variance",
        "correlation_4": "no_variance",
        "correlation_16": "no_variance",
    }


def test_a_plane_not_wider_and_taller_than_the_lag_has_no_pairs_at_it():
    assert reasons(noisy((10, 10))) == {
        "correlation_16": "no_pairs",
        "local_focus_median": "plane_too_small",
        "local_focus_p10": "plane_too_small",
        "tile_mean_cv": "plane_too_small",
    }


def test_a_plane_under_three_rows_has_no_gradient_or_laplacian():
    found = reasons(noisy((2, 64)))
    assert found["tenengrad"] == found["laplacian_variance"] == "plane_too_small"
    assert found["correlation_4"] == "no_pairs" and "normalized_variance" not in found


def test_a_plane_with_a_mean_that_is_not_positive_has_no_ratio_metric():
    pixels = (noisy().astype(np.int32) - 5000).astype(np.int16)
    found = reasons(pixels)
    assert set(found) == {
        "normalized_variance",
        "tenengrad",
        "laplacian_variance",
        "local_focus_median",
        "local_focus_p10",
        "tile_mean_cv",
    }
    assert set(found.values()) == {"non_positive_mean"}


def test_non_finite_pixels_blank_every_pixel_metric_with_that_reason():
    pixels = noisy().astype(np.float32)
    pixels[3, 3] = np.nan
    found = reasons(pixels)
    assert len(found) == 9 and set(found.values()) == {"non_finite_pixels"}
    assert "entropy" not in found and "otsu_threshold" not in found


def test_a_plane_without_finite_pixels_has_every_metric_blank_for_that_reason():
    found = reasons(np.full((8, 8), np.nan, dtype=np.float32))
    assert list(found) == list(METRICS) and set(found.values()) == {"no_finite_pixels"}


def test_the_reasons_are_in_the_table_and_read_back(tmp_path):
    rows = [QcRow(IDENTITY, False, measure(np.full((32, 32), 7, dtype=np.uint16)))]
    path = write_image_qc(rows, tmp_path / "t.csv")
    cell = list(csv.DictReader(path.read_text(encoding="utf-8").splitlines()))[0]["blank_reasons"]
    assert cell.startswith("otsu_threshold=constant_plane;") and cell.endswith(
        "correlation_16=no_variance"
    )
    assert read_image_qc(path) == rows


# Refused combinations -----------------------------------------------------------------------------


def table_with(pixels, **cells):
    """Return the table text of one real row with the named cells replaced."""
    text = table_text([QcRow(IDENTITY, False, measure(pixels))])
    header, row = list(csv.reader(io.StringIO(text)))
    for name, value in cells.items():
        if name in COLUMNS:
            row[COLUMNS.index(name)] = value
    out = io.StringIO()
    csv.writer(out, lineterminator="\n").writerows([header, row])
    return out.getvalue()


def refused(tmp_path, text):
    (tmp_path / "t.csv").write_text(text, encoding="utf-8")
    with pytest.raises(ImageQcFormatError):
        read_image_qc(tmp_path / "t.csv")


def test_the_real_rows_used_below_are_accepted(tmp_path):
    (tmp_path / "t.csv").write_text(table_with(noisy()), encoding="utf-8")
    assert read_image_qc(tmp_path / "t.csv")[0].qc == measure(noisy())


@pytest.mark.parametrize(
    "cells",
    [
        {"qc_status": "no_finite_pixels"},  # every metric is still there
        {"qc_status": "non_finite_pixels"},  # the pixel metrics are still there
        {"qc_status": "measured", "entropy": ""},  # a measured plane has an entropy
        {"foreground_fraction": "1.5"},
        {"otsu_separation": "-0.1"},
        {"correlation_1": "1.2"},
        {"correlation_16": "-1.01"},
        {"entropy": "6.5"},
        {"tenengrad": "-0.5"},
        {"tile_mean_cv": "-1e-9"},
        {"otsu_threshold": "", "otsu_separation": ""},  # the Otsu metrics are blank together
        {"correlation_4": "", "blank_reasons": ""},  # a blank metric without a reason
        {"blank_reasons": "entropy=no_pairs"},  # a reason for a metric that has a value
        {"blank_reasons": "correlation_1=weather"},  # an unknown reason
        {"blank_reasons": "mean=no_pairs"},  # an unknown metric
        {"correlation_4": "", "blank_reasons": "correlation_4=non_finite_pixels"},
    ],
)
def test_the_reader_refuses_a_row_no_measurement_makes(tmp_path, cells):
    refused(tmp_path, table_with(noisy(), **cells))


def test_the_reader_refuses_a_non_finite_row_that_has_a_pixel_metric(tmp_path):
    pixels = noisy().astype(np.float32)
    pixels[0, 0] = np.nan
    text = table_with(pixels, normalized_variance="0.5", blank_reasons="")
    refused(tmp_path, text)


def test_the_reader_refuses_a_no_finite_row_that_has_a_metric(tmp_path):
    text = table_with(np.full((8, 8), np.nan, dtype=np.float32), entropy="1.0")
    refused(tmp_path, text)


def test_the_writer_refuses_what_the_reader_refuses(tmp_path):
    from dataclasses import replace

    bad = replace(measure(noisy()), status="no_finite_pixels")
    with pytest.raises(ValueError, match="no_finite_pixels"):
        write_image_qc([QcRow(IDENTITY, False, bad)], tmp_path / "t.csv")
