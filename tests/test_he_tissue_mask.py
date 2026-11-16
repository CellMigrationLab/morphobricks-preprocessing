"""``RunInputs.he_tissue_mask`` on real runs (Spec ADR 0005 BM15; design he-tissue-mask §2, §3, §7).

The helper resolves the position from the run, refuses before any read what it cannot take,
writes one ``uint8`` region mask with its BM15 lineage line through BM12's publication, and
decides a retry with BM15's own comparator: a no-op only for two reusable lines equal in every
key but the output digest and the fractions. An unverified code identity writes a mask that is
never reused; freezing the rule needs a new output mask ID.

Code identities are set explicitly where a test needs them (``IDENTITY_PATCH``): a development
checkout is an editable install, which is never verified.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json

import numpy as np
import pytest
import tifffile

pytest.importorskip("skimage")

from morphobricks_normalization import stain  # noqa: E402
from morphobricks_normalization.optical_density import check_white, optical_density  # noqa: E402
from morphobricks_normalization.regions import check_region  # noqa: E402
from morphobricks_normalization.scalar_inputs import rgb_yxc  # noqa: E402

from code_identity_tree import broken_identity  # noqa: E402
from he_tissue_tree import (  # noqa: E402
    IDENTITY_PATCH,
    UNIT,
    channels_run,
    declared,
    description,
    identities,
    rgb_run,
    synthetic_he,
    tree,
    write_rgb,
)
from morphobricks_preprocessing import (  # noqa: E402
    MaskRequest,
    Request,
    RunInputs,
    RunInputsError,
    __version__,
    prefill_mapping,
)
from morphobricks_preprocessing import he_tissue_mask as helper  # noqa: E402
from morphobricks_preprocessing.he_tissue_rule import tissue_components  # noqa: E402
from morphobricks_preprocessing.lineage_schema import BM15_LINE_KEYS, rule_sha256  # noqa: E402
from morphobricks_preprocessing.mask_lineage import LINEAGE_FILENAME, read_lineage  # noqa: E402
from morphobricks_preprocessing.testing_described import publish_and_run  # noqa: E402

IMAGE = f"{UNIT}/a.tif"
OUT = f"HETissue/{IMAGE}"


@pytest.fixture
def verified(monkeypatch):
    monkeypatch.setattr(IDENTITY_PATCH, lambda: identities())


@pytest.fixture
def run(tmp_path):
    return rgb_run(tmp_path)


def no_reads(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("a pixel was read")

    monkeypatch.setattr(RunInputs, "read_plane", refuse)
    for cls in (tifffile.TiffFile, tifffile.TiffPage, tifffile.TiffFrame, tifffile.TiffPageSeries):
        monkeypatch.setattr(cls, "asarray", refuse)


def digest(pixels, axes):
    header = json.dumps(
        {"axes": axes, "dtype": pixels.dtype.str.replace("=", "<"), "shape": list(pixels.shape)},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(header.encode() + b"\n" + pixels.tobytes()).hexdigest()


def lineage(run):
    path = run.masks_root / "HETissue" / LINEAGE_FILENAME
    return path.read_text(encoding="utf-8") if path.exists() else None


# Writing --------------------------------------------------------------------------------------


def test_it_writes_the_region_and_the_exact_bm15_line(run, verified):
    result = run.he_tissue_mask(IMAGE, output_mask_id="HETissue")
    assert (result.path, result.mask_id, result.published, result.reusable) == (
        OUT,
        "HETissue",
        True,
        True,
    )
    rgb, _ = synthetic_he()
    pixels = np.ascontiguousarray(np.moveaxis(rgb, -1, 0))
    scalar = stain.to_scalar(pixels, axes="CYX", preset="he_eosin")
    od, floored = optical_density(rgb_yxc(pixels, "CYX"), check_white((255, 255, 255)))
    record = json.loads(lineage(run))
    expected = tissue_components(scalar.image, scalar.exclusion, od, record["rule"])
    written = tifffile.imread(run.masks_root / OUT)
    assert written.dtype == np.uint8 and np.array_equal(written, expected.keep.astype(np.uint8))
    assert tuple(record) == BM15_LINE_KEYS
    assert record["source"] == {
        "root": "experiment",
        "image": IMAGE,
        "axes": "CYX",
        "shape": [3, 96, 96],
        "dtype": "|u1",
        "content_sha256": digest(pixels, "CYX"),
    }
    assert record["output"] == {
        "root": "masks",
        "mask_id": "HETissue",
        "path": OUT,
        "axes": "YX",
        "shape": [96, 96],
        "dtype": "|u1",
        "content_sha256": digest(written, "YX"),
    }
    assert record["operation"] == "he_tissue_mask" and record["rule"]["status"] == "provisional"
    assert record["rule_sha256"] == rule_sha256(record["rule"])
    assert record["code"] == identities() and record["reusable"] is True
    import scipy
    import skimage

    assert (record["numpy_version"], record["scikit_image_version"], record["scipy_version"]) == (
        np.__version__,
        skimage.__version__,
        scipy.__version__,
    )
    assert record["image_type"] == "rgb"
    assert record["rgb_header_override"] == {"declared": False, "image": None}
    assert record["pixel_size"] is None
    assert record["fractions"] == {**expected.fractions(), "od_floored": floored}
    assert record["helper"] == "morphobricks_preprocessing.RunInputs.he_tissue_mask"
    assert record["helper_version"] == __version__
    assert result.line == lineage(run) and result.record == record
    (read,) = read_lineage(run.masks_root / "HETissue")
    assert read.record == record


def test_the_same_call_again_is_a_no_op(run, verified):
    first = run.he_tissue_mask(IMAGE, output_mask_id="HETissue")
    before = tree(run.masks_root)
    again = run.he_tissue_mask(IMAGE, output_mask_id="HETissue")
    assert tree(run.masks_root) == before
    assert not again.published and again.line == first.line == lineage(run)


def test_the_preview_writes_nothing_and_matches_the_written_file(run, verified):
    before = tree(run.masks_root)
    found = run.preview_he_tissue_mask(IMAGE)
    assert tree(run.masks_root) == before and found.provisional and found.reusable
    result = run.he_tissue_mask(IMAGE, output_mask_id="HETissue")
    written = tifffile.imread(run.masks_root / result.path)
    assert np.array_equal(written, found.components.keep.astype(np.uint8))
    assert dict(found.fractions) == dict(result.fractions)


def test_an_all_zero_mask_is_written_as_a_valid_empty_region(tmp_path, verified):
    white = np.full((48, 48, 3), 250, np.uint8)  # all background: 2304 px >= 1000
    run = rgb_run(tmp_path, {"a.tif": white})
    result = run.he_tissue_mask(IMAGE, output_mask_id="HETissue")
    assert result.fractions["kept"] == 0.0
    assert not tifffile.imread(run.masks_root / result.path).any()


# The FOV is the run's (B5) --------------------------------------------------------------------


def test_a_hand_built_fovs_attributes_are_ignored(run):
    (own,) = run.fovs()
    wrong = dataclasses.replace(
        own, image_type="channels", axes="ZYX", z_count=5, t_count=2, c_count=4, dtype="uint16"
    )
    found, expected = run.preview_he_tissue_mask(wrong), run.preview_he_tissue_mask(own)
    assert np.array_equal(found.components.keep, expected.components.keep)
    assert dict(found.fractions) == dict(expected.fractions)


def test_a_hand_built_rgb_fov_of_a_channels_position_is_refused(tmp_path, monkeypatch):
    run = channels_run(tmp_path)
    (own,) = run.fovs()
    no_reads(monkeypatch)
    with pytest.raises(ValueError, match="is declared channels; the H&E tissue mask takes a unit"):
        run.he_tissue_mask(dataclasses.replace(own, image_type="rgb"), output_mask_id="HETissue")


def test_the_lineage_copies_the_runs_per_image_override_record(tmp_path, verified):
    rgb = synthetic_he()[0]
    run = rgb_run(tmp_path, {"a.tif": rgb, "b.tif": rgb}, override=True, scanner={"a.tif"})
    used = run.he_tissue_mask(f"{UNIT}/a.tif", output_mask_id="HETissue").record
    unused = run.he_tissue_mask(f"{UNIT}/b.tif", output_mask_id="HETissue").record
    assert used["rgb_header_override"] == {
        "declared": True,
        "image": {"image": f"{UNIT}/a.tif", "override": "used", "header_photometric": "minisblack"},
    }
    assert unused["rgb_header_override"] == {
        "declared": True,
        "image": {
            "image": f"{UNIT}/b.tif",
            "override": "unused",
            "header_photometric": "rgb",
            "unused_reason": "header_already_rgb",
        },
    }
    recorded = run.manifest.experiment_description["units"][0]["rgb_header_override_images"]
    assert [used["rgb_header_override"]["image"], unused["rgb_header_override"]["image"]] == (
        recorded
    )


def test_a_recorded_pixel_size_is_copied_for_information_only(run, verified, monkeypatch):
    from morphobricks_preprocessing.recorded_sizes import RecordedSize

    sizes = {r: RecordedSize(r, 0.5, "micrometre", "stated") for r in ("X", "Y")}
    monkeypatch.setattr(RunInputs, "pixel_sizes", lambda self, fov: sizes)
    record = run.he_tissue_mask(IMAGE, output_mask_id="HETissue").record
    assert record["pixel_size"] == {
        r: {"value": 0.5, "unit": "micrometre", "source": "stated"} for r in ("X", "Y")
    }
    assert record["rule"]["components"][0]["cleanup"][0] == ["remove_pieces_smaller_than_px", 15]


# Refusals before any read (§2) ----------------------------------------------------------------


def _fov_refusal(field, value):
    def change(run, monkeypatch):
        fovs = tuple(dataclasses.replace(f, **{field: value}) for f in run.fovs())
        monkeypatch.setattr(RunInputs, "fovs", lambda self: fovs)

    return change


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (_fov_refusal("z_count", 2), "has 2 Z planes and 1 time points"),
        (_fov_refusal("t_count", 2), "has 1 Z planes and 2 time points"),
        (_fov_refusal("dtype", "uint16"), "has the pixel type uint16 and 3 channels"),
        (_fov_refusal("c_count", 4), "has the pixel type uint8 and 4 channels"),
    ],
)
def test_the_runs_record_of_the_position_is_refused_before_any_read(
    run, monkeypatch, change, message
):
    # A run records an RGB unit only as one 8-bit three-channel plane today, so the record is
    # changed here to show each check reads the run's record (never the caller's Fov).
    change(run, monkeypatch)
    before = tree(run.masks_root)
    no_reads(monkeypatch)
    with pytest.raises(ValueError, match=message):
        run.he_tissue_mask(IMAGE, output_mask_id="HETissue")
    assert tree(run.masks_root) == before


@pytest.mark.parametrize(
    ("output_id", "message"),
    [
        ("Nuclei", "is declared in the description already"),
        ("NUCLEI", "differs only in case from the declared mask ID 'Nuclei'"),
        ("nucleusmasks", "differs only in case from the declared source type 'NucleusMasks'"),
        ("NucleusMasks", "holds masks of the type 'NucleusMasks' that this helper did not write"),
        ("1Bad", "breaks the mask ID rules"),
        ("bad__id", "breaks the mask ID rules"),
    ],
)
def test_an_output_mask_id_is_refused_before_any_read(run, monkeypatch, output_id, message):
    before = tree(run.masks_root)
    no_reads(monkeypatch)
    with pytest.raises(ValueError, match=message):
        run.he_tissue_mask(IMAGE, output_mask_id=output_id)
    assert tree(run.masks_root) == before


def test_other_refusals_before_any_read(run, monkeypatch, tmp_path):
    before = tree(run.masks_root)
    no_reads(monkeypatch)
    with pytest.raises(RunInputsError, match="CTRL/rep1/z.tif is not an image of this run"):
        run.he_tissue_mask(f"{UNIT}/z.tif", output_mask_id="HETissue")
    with pytest.raises(ValueError, match="known rules are he_tissue_v1"):
        run.he_tissue_mask(IMAGE, output_mask_id="HETissue", rule="he_tissue_v2")
    monkeypatch.setitem(__import__("sys").modules, "skimage", None)  # a blocked import
    monkeypatch.setattr(helper, "hed_matrix", helper.hed_matrix.__wrapped__)
    with pytest.raises(ImportError, match=r'pip install "morphobricks-preprocessing\[he-mask\]"'):
        run.he_tissue_mask(IMAGE, output_mask_id="HETissue")
    with pytest.raises(ImportError, match="he-mask"):
        run.preview_he_tissue_mask(IMAGE)
    assert tree(run.masks_root) == before


def test_a_run_without_a_masks_folder_is_refused_before_any_read(tmp_path, monkeypatch):
    run = rgb_run(tmp_path, masks=False)
    no_reads(monkeypatch)
    with pytest.raises(ValueError, match="the run has no masks folder"):
        run.he_tissue_mask(IMAGE, output_mask_id="HETissue")


def test_a_foreign_file_and_an_existing_file_are_refused_before_any_read(run, monkeypatch):
    foreign = run.masks_root / "Other" / IMAGE
    foreign.parent.mkdir(parents=True)
    tifffile.imwrite(foreign, np.ones((96, 96), np.uint8), photometric="minisblack")
    run.he_tissue_mask(IMAGE, output_mask_id="HETissue")  # unverified: never reused
    before = tree(run.masks_root)
    no_reads(monkeypatch)
    with pytest.raises(ValueError, match="holds masks of the type 'Other' that this helper"):
        run.he_tissue_mask(IMAGE, output_mask_id="Other")
    with pytest.raises(ValueError, match=f"{OUT} exists .* unverified code identity"):
        run.he_tissue_mask(IMAGE, output_mask_id="HETissue")
    assert tree(run.masks_root) == before


# Code identity (B1) ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kind",
    ["editable", "record_missing", "hash_differs", "not_in_record", "no_hash", "source_tree"],
)
def test_an_unverified_identity_writes_a_mask_that_is_never_reused(
    run, monkeypatch, tmp_path, kind
):
    found = broken_identity(tmp_path / "dist", kind)
    assert found["verified"] is False and found["reason"]
    code = {**identities(), "preprocessing": found}
    monkeypatch.setattr(IDENTITY_PATCH, lambda: code)
    result = run.he_tissue_mask(IMAGE, output_mask_id="HETissue")
    assert result.published and result.reusable is False
    assert (run.masks_root / OUT).is_file()
    assert result.record["code"]["preprocessing"]["reason"] == found["reason"]
    before = tree(run.masks_root)
    with pytest.raises(ValueError, match="never reused"):
        run.he_tissue_mask(IMAGE, output_mask_id="HETissue")
    assert tree(run.masks_root) == before


def test_the_same_commit_with_another_record_is_another_identity(run, monkeypatch):
    monkeypatch.setattr(IDENTITY_PATCH, lambda: identities())
    run.he_tissue_mask(IMAGE, output_mask_id="HETissue")
    before = tree(run.masks_root)
    rebuilt = identities(record_sha256="f" * 64)  # same vcs_commit, other RECORD bytes
    assert rebuilt["preprocessing"]["vcs_commit"] == identities()["preprocessing"]["vcs_commit"]
    monkeypatch.setattr(IDENTITY_PATCH, lambda: rebuilt)
    with pytest.raises(ValueError, match=r"differs in: code\)"):
        run.he_tissue_mask(IMAGE, output_mask_id="HETissue")
    assert tree(run.masks_root) == before


@pytest.mark.parametrize(
    ("target", "value", "named"),
    [
        ("numpy_version", "0.0", "numpy_version"),
        ("scikit_image_version", "0.0", "scikit_image_version"),
        ("scipy_version", "0.0", "scipy_version"),
        ("hed", "e" * 64, "rule, rule_sha256"),
    ],
)
def test_another_library_version_or_hed_matrix_is_refused_on_retry(
    run, verified, monkeypatch, target, value, named
):
    run.he_tissue_mask(IMAGE, output_mask_id="HETissue")
    before = tree(run.masks_root)
    if target == "numpy_version":
        monkeypatch.setattr(helper.np, "__version__", value)
    elif target == "scipy_version":
        monkeypatch.setattr(helper.scipy, "__version__", value)
    elif target == "scikit_image_version":
        monkeypatch.setattr(helper.skimage_module(), "__version__", value)
    else:
        monkeypatch.setattr(helper, "hed_matrix", lambda: ("skimage.color.hed_from_rgb", value))
    with pytest.raises(ValueError, match=f"differs in: {named}"):
        run.he_tissue_mask(IMAGE, output_mask_id="HETissue")
    assert tree(run.masks_root) == before


def test_a_changed_image_is_refused_and_another_source_is_never_the_same(tmp_path, verified):
    run = rgb_run(tmp_path)
    first = run.he_tissue_mask(IMAGE, output_mask_id="HETissue")
    before = tree(run.masks_root)
    rgb = synthetic_he()[0]
    rgb[0, 0] = (1, 2, 3)
    write_rgb(run.experiment_root / IMAGE, rgb)
    with pytest.raises(RunInputsError, match="changed"):
        run.he_tissue_mask(IMAGE, output_mask_id="HETissue")
    assert tree(run.masks_root) == before
    # The comparator itself: equal pixels never stand in for the same source.
    other = json.loads(first.line)
    other["source"]["content_sha256"] = "0" * 64
    assert helper.same_computation(first.record, first.record)
    assert not helper.same_computation(first.record, other)
    assert helper._differences(first.record, other) == ["source"]
    results = json.loads(first.line)
    results["fractions"]["kept"] = 0.0
    results["output"]["content_sha256"] = "0" * 64
    assert helper.same_computation(first.record, results)  # results are not compared


# Freezing needs a new mask ID (review w2 N3) -----------------------------------------------------


def test_freezing_makes_a_new_mask_under_a_new_id_never_a_relabelled_one(
    run, verified, monkeypatch
):
    provisional = run.he_tissue_mask(IMAGE, output_mask_id="HETissue")
    before = tree(run.masks_root)
    monkeypatch.setitem(helper.RULES, "he_tissue_v1", "frozen")
    with pytest.raises(ValueError) as refused:
        run.he_tissue_mask(IMAGE, output_mask_id="HETissue")
    assert "differs in: rule, rule_sha256" in str(refused.value)
    assert "a frozen rule included) makes a new mask under a new output mask ID" in str(
        refused.value
    )
    assert tree(run.masks_root) == before  # the file, its digest and its line, byte for byte
    frozen = run.he_tissue_mask(IMAGE, output_mask_id="HETissueFrozen")
    assert frozen.published and frozen.record["rule"]["status"] == "frozen"
    assert frozen.record["rule_sha256"] != provisional.record["rule_sha256"]
    after = tree(run.masks_root)
    assert {k: v for k, v in after.items() if not k.startswith("HETissueFrozen/")} == before
    assert f"HETissueFrozen/{IMAGE}" in after


# Publication, rollback and the run ------------------------------------------------------------


def test_a_pending_line_is_rolled_back_by_the_next_call(run, verified):
    first = run.he_tissue_mask(IMAGE, output_mask_id="HETissue")
    (run.masks_root / OUT).unlink()  # a crash between the line and the rename
    assert read_lineage(run.masks_root / "HETissue")  # the pending line is still there
    again = run.he_tissue_mask(IMAGE, output_mask_id="HETissue")
    assert again.published and again.line == first.line
    assert lineage(run) == first.line  # one line, not two


def test_bm12_lines_still_read_and_the_run_still_opens_before_the_output_is_declared(run, verified):
    run.classes_to_binary(IMAGE, "Nuclei", output_prefix="Tissue")
    run.he_tissue_mask(IMAGE, output_mask_id="HETissue")
    (bm12,) = read_lineage(run.masks_root / "Tissue_class1")
    (bm15,) = read_lineage(run.masks_root / "HETissue")
    assert bm12.record["operation"] == "classes_to_binary"
    assert bm15.record["operation"] == "he_tissue_mask"
    again = RunInputs.open(run.output_folder)  # unrecorded.py: the outputs carry their lines
    assert not again.he_tissue_mask(IMAGE, output_mask_id="HETissue").published


@pytest.mark.parametrize("white", [False, True])
def test_declared_binary_it_is_checked_read_as_a_region_and_refused_as_objects(
    tmp_path, verified, white
):
    pixels = np.full((48, 48, 3), 250, np.uint8) if white else synthetic_he()[0]
    run = rgb_run(tmp_path, {"a.tif": pixels})
    written = tifffile.imread(
        run.masks_root / run.he_tissue_mask(IMAGE, output_mask_id="HETissue").path
    )
    publish_and_run(
        tmp_path,
        run.experiment_root,
        run.masks_root,
        prefill_mapping("YXS"),
        declared(description(1)),
    )  # fmt: skip  (the mask step runs: BM11 passes)
    again = RunInputs.open(run.output_folder)
    resolved = again.resolve_request(Request((), (MaskRequest("HETissue", "region"),)))
    (selection,) = resolved.require()
    region = again.read_mask(selection.relative_path, "HETissue")
    assert np.array_equal(region, written)
    check_region(region, written.shape, name="HETissue")  # valid; empty for the white image
    assert (region != 0).any() is not white
    refused = again.resolve_request(Request((), (MaskRequest("HETissue", "objects"),)))
    assert [r.code for r in refused.refusals] == ["A_MASK_NOT_INSTANCES"]


def test_a_non_editable_install_is_verified_and_reused_for_real(tmp_path):
    # No identity is patched here: the installed preprocessing and normalization are checked.
    # A development checkout (pip install -e) is never verified, so this runs where the package
    # is installed from a wheel (the cross-repo gate's environment).
    from importlib import metadata

    direct = json.loads(metadata.distribution("morphobricks-preprocessing").read_text(
        "direct_url.json") or "{}")  # fmt: skip
    if direct.get("dir_info", {}).get("editable"):
        pytest.skip("preprocessing is an editable install here: it is never verified")
    helper.code_identities.cache_clear()
    run = rgb_run(tmp_path)
    first = run.he_tissue_mask(IMAGE, output_mask_id="HETissue")
    code = first.record["code"]
    assert code["preprocessing"]["verified"], code["preprocessing"]["reason"]
    assert code["normalization"]["verified"], code["normalization"]["reason"]
    assert first.reusable and not run.he_tissue_mask(IMAGE, output_mask_id="HETissue").published
