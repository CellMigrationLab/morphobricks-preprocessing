"""The RGB header override: Spec amendment RH1 (experiment-v1 4.4b), conformance scenario P36.

Scanner files store interleaved RGB under a header that does not say RGB (TIFF photometric
``MINISBLACK``, ``SamplesPerPixel`` 3, contiguous). A unit declared ``rgb`` may declare
``rgb_header_override = true``; the contradiction is then accepted only for exactly three
interleaved samples per pixel, and the manifest records the unit as declared RGB with the header
overridden.

The Spec fixtures of RH1 (``v40``, ``i140``, ``i141``, Spec commit 2cbebf8) are not vendored yet:
the pin moves only to a merged Spec commit. The descriptions here are equivalent to them:
``v40`` and ``i141`` are built from the vendored ``v28`` with exactly the lines the Spec adds
(checked below against the vendored ``v28``), ``i140`` is copied without its comment lines.
"""

from __future__ import annotations

import json

import numpy as np
import pytest
import tifffile

from morphobricks_preprocessing import Capabilities, Request, RunInputs
from morphobricks_preprocessing.axes import prefill_mapping
from morphobricks_preprocessing.channel_answers import with_type
from morphobricks_preprocessing.channel_labels import UnitLayout
from morphobricks_preprocessing.description import description_from_bytes
from morphobricks_preprocessing.description_read import read_description_bytes
from morphobricks_preprocessing.description_reconcile import UnitFolders, reconcile
from morphobricks_preprocessing.description_rules import Code
from morphobricks_preprocessing.description_run_text import physical_issue
from morphobricks_preprocessing.description_write import canonical_bytes
from morphobricks_preprocessing.discovery import discover_experiment
from morphobricks_preprocessing.draft import draft_from_discovery
from morphobricks_preprocessing.draft_answers import description_from_draft
from morphobricks_preprocessing.draft_format import answers_from_bytes, answers_to_bytes
from morphobricks_preprocessing.metadata import inspect_images, inspect_tiff
from morphobricks_preprocessing.physical_check import (
    IMAGE_TYPE_CONTRADICTS_HEADER,
    RGB_OVERRIDE_NOT_THREE_SAMPLES,
    UnitKey,
    check_physical,
)
from morphobricks_preprocessing.published_answers import published_answers
from morphobricks_preprocessing.reconcile_observe import observe_folders
from morphobricks_preprocessing.run import run_experiment
from morphobricks_preprocessing.testing import make_described_run
from morphobricks_preprocessing.testing_described import (
    PUBLISHED_NAME,
    publish_and_run,
    write_described_tree,
)
from spec_pin import fixtures

V28 = next(f for f in fixtures("valid") if f.name.startswith("v28_")).data.decode("utf-8")
CTRL_UNIT = "[repeats.{rep}.members.CTRL.technical_repeats.single]"
RGB_LINE = 'image_type = "rgb"\n'
CTRL = [UnitKey("CTRL", "rep1", "single"), UnitKey("CTRL", "rep2", "single")]
DRUG = [UnitKey("DrugA", "rep1", "single"), UnitKey("DrugA", "rep2", "single")]
SHAPE = (6, 7, 3)


def _with_override(values: dict[str, str]) -> str:
    """Return v28's body with ``rgb_header_override = <value>`` after the CTRL units' type."""
    body = "".join(line for line in V28.splitlines(keepends=True) if not line.startswith("#"))
    for rep, value in values.items():
        head = CTRL_UNIT.format(rep=rep)
        start = body.index(head)
        at = body.index(RGB_LINE, start) + len(RGB_LINE)
        body = body[:at] + f"rgb_header_override = {value}\n" + body[at:]
    return body


V40 = _with_override({"rep1": "true", "rep2": "true"}).encode("utf-8")
I141 = _with_override({"rep1": '"true"'}).encode("utf-8")
I140 = b"""schema = "morphobricks-experiment"
schema_version = 1

[conditions.CTRL]
source_folder = "CTRL"
display_name = "Control"

[conditions.DrugA]
source_folder = "DrugA"
display_name = "Drug A"
reference = "CTRL"

[channels.DAPI]
display_name = "DAPI"

[repeats.rep1]
confirmed = true
confirmed_on = "2026-11-06"

[repeats.rep1.members.CTRL]
source_repeat = "rep1"
image_count = 8

[repeats.rep1.members.CTRL.technical_repeats.single]
source_folder = "single"
channels = ["DAPI"]
rgb_header_override = true

[repeats.rep1.members.DrugA]
source_repeat = "rep1"
image_count = 8

[repeats.rep1.members.DrugA.technical_repeats.single]
source_folder = "single"
channels = ["DAPI"]

[used_ids]
conditions = ["CTRL", "DrugA"]
channels = ["DAPI"]
repeats = ["rep1"]
technical_repeats = ["single"]
"""


def scanner_tiff(path, data=None, **kwargs):
    """Write interleaved RGB under a MINISBLACK header, as the scanner files of RH1 are."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if data is None:
        data = np.arange(np.prod(SHAPE), dtype=np.uint8).reshape(SHAPE)
    options = {"photometric": "minisblack", "planarconfig": "contig", **kwargs}
    tifffile.imwrite(path, data, **options)
    return path


def single_sample(path):
    return scanner_tiff(path, np.zeros((6, 7), np.uint8))


def rgb_tiff(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(path, np.zeros(SHAPE, np.uint8), photometric="rgb")
    return path


def build(root, *, ctrl=scanner_tiff, images=2):
    """CTRL images by ``ctrl`` (scanner files by default), DrugA images with an RGB header."""
    for condition in ("CTRL", "DrugA"):
        for rep in ("rep1", "rep2"):
            for index in range(images):
                path = root / condition / rep / f"FOV{index:03d}.tif"
                (ctrl if condition == "CTRL" else rgb_tiff)(path)
    return root


def check(root, data):
    discovery = discover_experiment(root)
    files = inspect_images(root, discovery.images).files
    return check_physical(description_from_bytes(data), discovery, files)


@pytest.fixture
def no_pixel_reads(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("pixel data was read")

    for cls in (tifffile.TiffFile, tifffile.TiffPage, tifffile.TiffFrame, tifffile.TiffPageSeries):
        monkeypatch.setattr(cls, "asarray", fail)


# The descriptions -----------------------------------------------------------------------------


def test_the_inline_descriptions_are_the_spec_fixtures_of_rh1():
    v28 = description_from_bytes(V28.encode("utf-8"))
    v40 = description_from_bytes(V40)
    assert V40.decode("utf-8").count("rgb_header_override = true") == 2
    for rep, repeat in v40.repeats.items():
        assert repeat.members["CTRL"].technical_repeats["single"].rgb_header_override is True
        assert repeat.members["DrugA"].technical_repeats["single"].rgb_header_override is None
        assert repeat.members["DrugA"] == v28.repeats[rep].members["DrugA"]


def test_v40_is_valid_and_the_key_survives_the_canonical_writer():
    report = read_description_bytes(V40)
    assert report.ok and not report.warnings
    description = description_from_bytes(V40)
    written = canonical_bytes(description)
    assert written.count(b"rgb_header_override = true\n") == 2
    assert description_from_bytes(written) == description


def test_i141_a_string_is_never_read_as_a_boolean():
    report = read_description_bytes(I141)
    assert (report.phase, report.codes) == (3, {Code.E_TYPE})
    (issue,) = report.errors
    assert issue.path.endswith("CTRL.technical_repeats.single.rgb_header_override")
    assert issue.value == "true"


@pytest.mark.parametrize("value", ["true", "false"])
def test_i140_the_key_without_image_type_rgb(value):
    data = I140.replace(b"rgb_header_override = true", f"rgb_header_override = {value}".encode())
    report = read_description_bytes(data)
    assert (report.phase, report.codes) == (5, {Code.E_RGB_OVERRIDE_WITHOUT_RGB})
    assert report.errors[0].path.endswith(".rgb_header_override")


def test_false_on_an_rgb_unit_is_valid_and_kept_as_written():
    data = _with_override({"rep1": "false"}).encode("utf-8")
    description = description_from_bytes(data)
    unit = description.repeats["rep1"].members["CTRL"].technical_repeats["single"]
    assert unit.rgb_header_override is False
    assert b"rgb_header_override = false" in canonical_bytes(description)


# The headers ----------------------------------------------------------------------------------


def test_a_scanner_file_is_minisblack_with_three_interleaved_samples(tmp_path, no_pixel_reads):
    path = scanner_tiff(tmp_path / "a.tif")
    with tifffile.TiffFile(path) as tif:
        page = tif.pages[0]
        assert page.photometric == tifffile.PHOTOMETRIC.MINISBLACK
        assert page.samplesperpixel == 3
        assert page.planarconfig == tifffile.PLANARCONFIG.CONTIG
        assert len(tif.pages) == 1
    meta = inspect_tiff(path)
    assert (meta.photometric, meta.interleaved_samples, meta.rgb_samples) == (
        "minisblack",
        3,
        False,
    )
    assert meta.axes == "YXS"
    rgb = inspect_tiff(rgb_tiff(tmp_path / "b.tif"))
    assert (rgb.photometric, rgb.interleaved_samples, rgb.rgb_samples) == ("rgb", 3, True)
    plain = inspect_tiff(scanner_tiff(tmp_path / "c.tif", np.zeros((6, 7), np.uint8)))
    assert (plain.photometric, plain.interleaved_samples) == ("minisblack", 0)


# P36 ------------------------------------------------------------------------------------------


def test_p36a_the_override_accepts_three_interleaved_samples(tmp_path, no_pixel_reads):
    report = check(build(tmp_path / "exp"), V40)
    assert report.ok, report.issues
    assert dict(report.image_type_overridden) == dict.fromkeys(CTRL, "minisblack")
    assert {key: report.image_type_verified[key] for key in CTRL + DRUG} == {
        **dict.fromkeys(CTRL, False),  # overridden: not header-verified
        **dict.fromkeys(DRUG, True),
    }
    assert report.layouts[CTRL[0]].channels == 3


def test_p36b_without_the_key_the_contradiction_is_refused_and_names_it(tmp_path):
    report = check(build(tmp_path / "exp"), V28.encode("utf-8"))
    assert report.codes == {IMAGE_TYPE_CONTRADICTS_HEADER}
    assert {issue.unit for issue in report.issues} == set(CTRL)
    for issue in report.issues:
        assert "rgb_header_override" in issue.detail and "minisblack" in issue.detail
        assert "rgb_header_override = true" in physical_issue(issue).message
    assert not report.image_type_overridden


def test_p36b_a_contradiction_without_three_samples_does_not_name_the_key(tmp_path):
    report = check(build(tmp_path / "exp", ctrl=single_sample), V28.encode("utf-8"))
    assert report.codes == {IMAGE_TYPE_CONTRADICTS_HEADER}
    assert all("rgb_header_override" not in issue.detail for issue in report.issues)


def test_p36c_one_image_with_one_sample_per_pixel(tmp_path, no_pixel_reads):
    root = build(tmp_path / "exp")
    odd = scanner_tiff(root / "CTRL/rep1/FOV001.tif", np.zeros((6, 7), np.uint8))
    report = check(root, V40)
    assert report.codes == {RGB_OVERRIDE_NOT_THREE_SAMPLES}
    (issue,) = report.issues
    assert (issue.unit, issue.path) == (CTRL[0], odd.relative_to(root).as_posix())
    assert "no interleaved samples" in issue.detail
    assert set(report.image_type_overridden) == {CTRL[1]}  # the refused unit records nothing


@pytest.mark.parametrize(
    ("data", "options", "shown"),
    [
        (np.zeros((3, 6, 7), np.uint8), {"planarconfig": "separate"}, "no interleaved"),
        (np.zeros((6, 7, 4), np.uint8), {"extrasamples": ["unspecified"] * 3}, "4 interleaved"),
        (np.zeros((6, 7, 2), np.uint8), {"extrasamples": ["unspecified"]}, "2 interleaved"),
    ],
)
def test_p36c_other_sample_layouts_are_not_three_interleaved_samples(
    tmp_path, data, options, shown
):
    root = build(tmp_path / "exp", ctrl=lambda path: scanner_tiff(path, data, **options))
    report = check(root, V40)
    assert report.codes == {RGB_OVERRIDE_NOT_THREE_SAMPLES}
    assert {issue.unit for issue in report.issues} == set(CTRL)
    assert all(shown in issue.detail for issue in report.issues)


def test_p36d_a_header_that_already_says_rgb_is_header_verified(tmp_path):
    report = check(build(tmp_path / "exp", ctrl=rgb_tiff), V40)
    assert report.ok
    assert all(report.image_type_verified[key] for key in CTRL + DRUG)
    assert not report.image_type_overridden


def test_the_reverse_direction_is_unchanged(tmp_path):
    # A unit not declared rgb whose header says RGB: the override does not exist for it.
    data = V28.replace(RGB_LINE, "", 1).replace('channels = ["R", "G", "B"]', 'channels = ["X"]', 1)
    data = data.replace("[channels.B]", "[channels.X]\n\n[channels.B]").encode("utf-8")
    data = data.replace(b'channels = ["B", "G", "R"]', b'channels = ["B", "G", "R", "X"]')
    report = check(build(tmp_path / "exp", ctrl=rgb_tiff), data)
    assert IMAGE_TYPE_CONTRADICTS_HEADER in report.codes


# The run --------------------------------------------------------------------------------------


@pytest.fixture
def overridden_run(tmp_path):
    return make_described_run(tmp_path / "run", V40)


def test_the_run_records_the_override_and_keeps_the_other_records(overridden_run):
    manifest = json.loads((overridden_run.output / "run_manifest.json").read_text("utf-8"))
    units = manifest["experiment_description"]["units"]
    ctrl = [u for u in units if u["condition"] == "CTRL"]
    drug = [u for u in units if u["condition"] == "DrugA"]
    assert len(ctrl) == len(drug) == 2
    for unit in ctrl:
        assert unit["image_type_header_verified"] is False
        assert unit["image_type_header_overridden"] is True
        assert unit["header_photometric"] == "minisblack"
    for unit in drug:
        assert set(unit) == {
            "repeat",
            "condition",
            "technical_repeat",
            "channels",
            "image_type_header_verified",
        }
        assert unit["image_type_header_verified"] is True


def test_run_inputs_give_samples_0_1_2_as_r_g_b_in_stored_order(overridden_run):
    inputs = RunInputs.open(overridden_run.output)
    ctrl = [fov for fov in inputs.fovs() if fov.relative_path.startswith("CTRL/")]
    assert len(ctrl) == 16
    for fov in ctrl:
        assert (fov.channel_ids, fov.image_type, fov.c_count) == (("R", "G", "B"), "rgb", 3)
        stored = tifffile.imread(overridden_run.experiment / fov.relative_path)
        assert stored.shape[-1] == 3
        np.testing.assert_array_equal(inputs.read_plane(fov), np.moveaxis(stored, -1, 0))
    resolved = inputs.resolve_request(
        Request(("R", "G", "B"), capabilities=Capabilities(rgb_support=True))
    )
    assert not resolved.refused
    assert {s.channel_indices for s in resolved.selections} == {(0, 1, 2)}


def test_a_run_without_the_key_is_refused_and_the_message_names_it(tmp_path):
    root = tmp_path / "run"
    write_described_tree(root / "experiment", None, description_from_bytes(V40))
    output = root / "output"
    output.mkdir()
    (output / PUBLISHED_NAME).write_bytes(V28.encode("utf-8"))
    result = run_experiment(root / "experiment", prefill_mapping("YXS"), output_folder=output)
    assert not result.written
    refused = [i for i in result.issues if i.code == IMAGE_TYPE_CONTRADICTS_HEADER]
    assert len(refused) == 16
    assert all("rgb_header_override = true" in issue.message for issue in refused)
    published = publish_and_run(root, root / "experiment", None, prefill_mapping("YXS"), V40)
    assert published.result.written  # the same images with the key


# Reconciliation and the notebook's draft -------------------------------------------------------


def test_an_accepted_override_is_not_a_changed_unit(tmp_path):
    root = build(tmp_path / "exp", images=8)
    discovery = discover_experiment(root)
    seen = observe_folders(discovery, metadata=inspect_images(root, discovery.images).files)
    folders = [UnitFolders(*vars(key).values()) for key in CTRL]
    assert all(seen[key].three_samples and seen[key].rgb is False for key in folders)
    assert reconcile(description_from_bytes(V40), seen).reopened == {}
    assert set(reconcile(description_from_bytes(V28.encode("utf-8")), seen).reopened) == {
        "rep1",
        "rep2",
    }


def test_the_draft_keeps_the_key_when_the_description_is_saved_again(tmp_path):
    published = description_from_bytes(V40)
    root = tmp_path / "exp"
    write_described_tree(root, None, published)
    draft = draft_from_discovery(discover_experiment(root))
    answers = published_answers(published, "0" * 64)
    assert answers_from_bytes(answers_to_bytes(answers)) == answers
    again = description_from_draft(draft, answers_from_bytes(answers_to_bytes(answers)))
    assert again == published
    assert canonical_bytes(again) == canonical_bytes(published)


def test_the_draft_refuses_a_key_that_is_not_a_boolean():
    answers = published_answers(description_from_bytes(V40), "0" * 64)
    text = answers_to_bytes(answers).replace(
        b'"rgb_header_override": true', b'"rgb_header_override": "true"'
    )
    with pytest.raises(ValueError, match="true or false"):
        answers_from_bytes(text)


def test_changing_the_unit_to_separate_channels_drops_the_key():
    answers = published_answers(description_from_bytes(V40), "0" * 64)
    key = ("CTRL", "rep1", "single")
    layout = UnitLayout(key, "CTRL/rep1/FOV000.tif", 3, (None, None, None))
    assert answers.units[key].rgb_header_override is True
    changed = with_type(answers, layout, "channels")
    assert changed.units[key].rgb_header_override is None
    assert with_type(answers, layout, "rgb").units[key].rgb_header_override is True
