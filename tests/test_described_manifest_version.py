"""A described run writes manifest version "4", so that older readers refuse it (Stage 2 Q9).

A v0.4.0 reader skips keys it does not know, so without a new version it would open a run with
an ``experiment_description`` and silently ignore it. The old reader here is the code of the
``v0.4.0`` tag (``released_code``): it refuses version "4" and reads the undescribed run.
"""

from __future__ import annotations

import json
import warnings

import pytest

from description_tree import MAPPING, build_experiment
from morphobricks_preprocessing import RunInputs, run_experiment
from morphobricks_preprocessing.description import description_from_bytes
from morphobricks_preprocessing.manifest import ManifestFormatError, read_manifest
from morphobricks_preprocessing.manifest_versions import check_version, version_for
from morphobricks_preprocessing.run import RunMode
from released_code import PRELUDE, extract, run_with
from spec_pin import fixtures

V01 = next(f for f in fixtures("valid") if f.name.startswith("v01_"))
DESCRIPTION = description_from_bytes(V01.data)

OLD_READER = (
    PRELUDE
    + """
from morphobricks_preprocessing.manifest import ManifestFormatError, read_manifest
try:
    manifest = read_manifest(Path(sys.argv[2]))
except ManifestFormatError as exc:
    print(json.dumps({"refused": str(exc)}))
else:
    print(json.dumps({"read": manifest.manifest_version}))
"""
)


def _run(tmp_path, *, described, verify_content=True):
    root, out = tmp_path / "exp", tmp_path / "out"
    out.mkdir(parents=True)
    build_experiment(root, DESCRIPTION)
    if described:
        (out / "morphobricks.toml").write_bytes(V01.data)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        mode = RunMode("restart", verify_content=verify_content)
        result = run_experiment(root, MAPPING, output_folder=out, mode=mode)
    assert result.written, result.issues
    return out


def _version(out):
    return json.loads((out / "run_manifest.json").read_text(encoding="utf-8"))["manifest_version"]


@pytest.mark.parametrize("verify_content", [True, False])
def test_a_described_run_writes_version_4_and_reads_back(tmp_path, verify_content):
    out = _run(tmp_path, described=True, verify_content=verify_content)
    assert _version(out) == "4"
    manifest = read_manifest(out / "run_manifest.json")
    assert manifest.manifest_version == "4"
    assert manifest.experiment_description is not None
    assert manifest.settings.verify_content is verify_content
    assert RunInputs.open(out).fovs()


@pytest.mark.parametrize(("verify_content", "version"), [(True, "3"), (False, "2")])
def test_an_undescribed_run_keeps_its_version(tmp_path, verify_content, version):
    out = _run(tmp_path, described=False, verify_content=verify_content)
    assert _version(out) == version
    assert "experiment_description" not in json.loads((out / "run_manifest.json").read_text())


@pytest.fixture(scope="module")
def released(tmp_path_factory):
    return extract("v0.4.0", tmp_path_factory.mktemp("released"))


def test_a_v0_4_0_reader_refuses_a_described_run_and_reads_an_undescribed_one(tmp_path, released):
    described = _run(tmp_path / "d", described=True)
    plain = _run(tmp_path / "u", described=False)
    refused = run_with(released, OLD_READER, described / "run_manifest.json")
    assert "Manifest version '4' is not supported" in refused["refused"]
    assert run_with(released, OLD_READER, plain / "run_manifest.json") == {"read": "3"}


def _mismatched(out, **change):
    path = out / "run_manifest.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    for key, value in change.items():
        if value is None:
            data.pop(key)
        else:
            data[key] = value
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_a_version_4_manifest_without_a_description_is_refused(tmp_path):
    path = _mismatched(_run(tmp_path, described=False), manifest_version="4")
    with pytest.raises(ManifestFormatError, match="version 4 manifest lacks experiment_desc"):
        read_manifest(path)
    with pytest.raises(ManifestFormatError):
        RunInputs.open(path.parent)


@pytest.mark.parametrize("version", ["1", "2", "3"])
def test_a_lower_version_with_a_description_is_refused(tmp_path, version):
    path = _mismatched(_run(tmp_path, described=True), manifest_version=version)
    with pytest.raises(ManifestFormatError, match=f"version {version} manifest carries"):
        read_manifest(path)
    with pytest.raises(ManifestFormatError):
        RunInputs.open(path.parent)


def test_version_4_needs_content_hashes_exactly_when_verify_content():
    assert version_for(False, True) == version_for(True, True) == "4"
    check_version("4", True, [("a.tif", "0" * 64)], True)
    check_version("4", False, [("a.tif", None)], True)
    with pytest.raises(ValueError, match="'a.tif'"):
        check_version("4", True, [("a.tif", None)], True)
    with pytest.raises(ValueError, match="'a.tif'"):
        check_version("4", False, [("a.tif", "0" * 64)], True)
    with pytest.raises(ValueError, match="writes version 4"):
        check_version("2", False, [], True)
