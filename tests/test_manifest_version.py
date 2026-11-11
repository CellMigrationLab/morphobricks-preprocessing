"""One run manifest version is written and read: "5" (owner ruling 16, Spec ADR 0002 AR11).

Every run is described, so its manifest carries ``experiment_description``; content verification
is ``settings.verify_content`` with a ``content_sha256`` in every entry exactly when it is true.
An older manifest ("1" before v0.4.0, "2" and "3" of the undescribed runs, "4" of v0.5.1 with
its schema version 4 table) is refused with "run preprocessing again", never converted; a later
one too. A refused manifest is replaced by the
next run, which reads every image again.
"""

import json
from dataclasses import replace

import pytest

from described_tree import run_described
from morphobricks_preprocessing import RunInputs, RunMode, read_manifest, write_manifest
from morphobricks_preprocessing.manifest import ManifestFormatError
from morphobricks_preprocessing.manifest_versions import MANIFEST_VERSION, check_version
from strict_tree import IMAGES, STRICT, TYX, write_experiment


@pytest.fixture
def experiment(tmp_path):
    return write_experiment(tmp_path)


def _data(out):
    return json.loads((out / "run_manifest.json").read_text(encoding="utf-8"))


def _write(out, data):
    (out / "run_manifest.json").write_text(json.dumps(data), encoding="utf-8")


@pytest.mark.parametrize("verify_content", [True, False])
def test_every_run_writes_version_4_with_its_description(experiment, verify_content):
    root, out = experiment
    mode = RunMode("restart", verify_content=verify_content)
    assert run_described(root, TYX, output_folder=out, mode=mode).written
    data = _data(out)
    assert data["manifest_version"] == MANIFEST_VERSION == "5"
    assert data["experiment_description"]["sha256"]
    assert data["settings"]["verify_content"] is verify_content
    assert all(bool(e.get("content_sha256")) is verify_content for e in data["files"])
    assert RunInputs.open(out).manifest.manifest_version == "5"


def test_turning_content_verification_off_recomputes_every_row(experiment):
    root, out = experiment
    assert run_described(root, TYX, output_folder=out, mode=STRICT).written
    again = run_described(root, output_folder=out)
    assert again.written and again.read == () and again.unchanged == tuple(IMAGES)
    off = run_described(root, output_folder=out, mode=RunMode("restart", verify_content=False))
    assert off.written and off.read == tuple(IMAGES)
    assert off.rebuild_reason == "content verification (verify_content) was turned off"
    assert _data(out)["manifest_version"] == "5"


@pytest.mark.parametrize("version", ["1", "2", "3", "4", "6"])
def test_any_other_version_is_refused_naming_the_fix(experiment, version):
    root, out = experiment
    assert run_described(root, TYX, output_folder=out).written
    data = _data(out)
    data["manifest_version"] = version
    _write(out, data)
    with pytest.raises(ManifestFormatError, match=f"'{version}' is not supported") as refused:
        read_manifest(out / "run_manifest.json")
    assert "Run preprocessing again" in str(refused.value)
    with pytest.raises(ManifestFormatError, match="Run preprocessing again"):
        RunInputs.open(out)


def test_a_refused_older_manifest_is_replaced_by_the_next_run(experiment):
    root, out = experiment
    assert run_described(root, TYX, output_folder=out).written
    data = _data(out)
    data["manifest_version"] = "2"
    del data["experiment_description"]
    _write(out, data)
    again = run_described(root, TYX, output_folder=out)
    assert again.written and again.read == tuple(IMAGES)  # nothing reused from it
    assert _data(out)["manifest_version"] == "5"


def test_a_manifest_without_its_description_is_refused(experiment):
    root, out = experiment
    assert run_described(root, TYX, output_folder=out).written
    data = _data(out)
    del data["experiment_description"]
    _write(out, data)
    with pytest.raises(ManifestFormatError, match="lacks it"):
        read_manifest(out / "run_manifest.json")


def test_the_content_hashes_must_match_verify_content(experiment, tmp_path):
    root, out = experiment
    assert run_described(root, TYX, output_folder=out, mode=STRICT).written
    data = _data(out)
    data["files"][1]["content_sha256"] = None
    _write(out, data)
    with pytest.raises(ManifestFormatError, match=repr(IMAGES[1])):
        read_manifest(out / "run_manifest.json")
    assert run_described(root, TYX, output_folder=out, mode=STRICT).written
    strict = read_manifest(out / "run_manifest.json")
    unhashed = replace(strict, files=(replace(strict.files[0], content_sha256=None),))
    with pytest.raises(ValueError, match="exactly when verify_content is true"):
        write_manifest(unhashed, tmp_path / "m.json")
    assert not (tmp_path / "m.json").exists()


def test_check_version():
    check_version("5", True, [("a.tif", "0" * 64)])
    check_version("5", False, [("a.tif", None)])
    with pytest.raises(ValueError, match="'a.tif'"):
        check_version("5", False, [("a.tif", "0" * 64)])
    with pytest.raises(ValueError, match="lacks it"):
        check_version("5", False, [], described=False)
    with pytest.raises(ValueError, match="'4' is not supported"):  # v0.5.1, table version 4
        check_version("4", True, [("a.tif", "0" * 64)])
    with pytest.raises(ValueError, match="'3' is not supported"):
        check_version("3", True, [("a.tif", "0" * 64)])
