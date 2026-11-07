"""A strict run's manifest is version 3, which earlier readers refuse (release review of rc5).

With version 2 and two extra keys, v0.4.0rc4's reader ignored ``settings.verify_content``
and ``content_sha256`` and opened a strict run as a fingerprint-only one. Version 3 is
refused there; this is shown by running rc4's own code on a strict run. A default run's
manifest stays version 2, byte for byte what the code before the change wrote.
"""

import json
import shutil
from dataclasses import replace

import pytest

from morphobricks_preprocessing import (
    ManifestFormatError,
    RunInputs,
    RunMode,
    read_manifest,
    run_experiment,
    write_manifest,
)
from morphobricks_preprocessing.manifest import RunManifest
from morphobricks_preprocessing.manifest_versions import (
    MANIFEST_VERSION,
    STRICT_MANIFEST_VERSION,
    check_version,
    version_for,
)
from released_code import BEFORE_STRICT_VERSION, PRELUDE, RC4, extract, run_with
from strict_tree import IMAGES, STRICT, TYX, write_experiment

RC4_READS = (
    PRELUDE
    + """
out, root = Path(sys.argv[2]), Path(sys.argv[3])
found = {}
try:
    package.read_manifest(out / "run_manifest.json")
    found["read_manifest"] = "opened"
except package.ManifestFormatError as exc:
    found["read_manifest"] = "ManifestFormatError: " + str(exc)
try:
    package.RunInputs.open(out)
    found["run_inputs"] = "opened"
except Exception as exc:
    found["run_inputs"] = type(exc).__name__ + ": " + str(exc)
result = package.run_experiment(root, package.prefill_mapping("TYX"), output_folder=out)
found["run"] = {
    "written": result.written,
    "read": list(result.read),
    "unchanged": list(result.unchanged),
    "rebuild_reason": result.rebuild_reason,
}
print(json.dumps(found))
"""
)

DEFAULT_RUN = (
    PRELUDE
    + """
root, out = Path(sys.argv[2]), Path(sys.argv[3])
result = package.run_experiment(root, package.prefill_mapping("TYX"), output_folder=out)
print(json.dumps({"written": result.written}))
"""
)


@pytest.fixture
def experiment(tmp_path):
    return write_experiment(tmp_path)


def test_a_strict_run_writes_version_3_and_a_default_run_version_2(experiment):
    root, out = experiment
    assert run_experiment(root, TYX, output_folder=out, mode=STRICT).written
    data = json.loads((out / "run_manifest.json").read_text(encoding="utf-8"))
    assert data["manifest_version"] == STRICT_MANIFEST_VERSION == "3"
    assert data["settings"]["verify_content"] is True
    assert all(entry["content_sha256"] for entry in data["files"])
    # The current reader reads it, and RunInputs opens it (and checks each SHA-256).
    assert read_manifest(out / "run_manifest.json").manifest_version == "3"
    assert RunInputs.open(out).manifest.manifest_version == "3"
    # Back to a default run: every row is recomputed, and the manifest is version 2 again.
    off = run_experiment(root, output_folder=out, mode=RunMode("restart", verify_content=False))
    assert off.written and off.read == tuple(IMAGES)
    assert off.rebuild_reason == "content verification (verify_content) was turned off"
    data = json.loads((out / "run_manifest.json").read_text(encoding="utf-8"))
    assert data["manifest_version"] == MANIFEST_VERSION == "2"
    assert data["settings"]["verify_content"] is False  # the opt-out is recorded (#159)
    assert not any("content_sha256" in entry for entry in data["files"])


def test_a_strict_restart_reuses_the_rows_of_a_version_3_run(experiment):
    root, out = experiment
    assert run_experiment(root, TYX, output_folder=out, mode=STRICT).written
    again = run_experiment(root, output_folder=out)
    assert again.written and again.read == () and again.unchanged == tuple(IMAGES)
    assert read_manifest(out / "run_manifest.json").manifest_version == "3"


def test_v0_4_0rc4_refuses_a_strict_manifest(experiment, tmp_path):
    root, out = experiment
    assert run_experiment(root, TYX, output_folder=out, mode=STRICT).written
    copy = tmp_path / "rc4-out"
    shutil.copytree(out, copy)
    src = extract(RC4, tmp_path / "rc4")
    found = run_with(src, RC4_READS, copy, root)
    refusal = "ManifestFormatError: Manifest version '3' is not supported"
    assert found["read_manifest"].startswith(refusal)
    assert found["run_inputs"].startswith(refusal)
    # rc4's run cannot read the earlier run, so it reuses none of its rows: it reads every
    # image again and writes a run of its own, not a strict run taken as a default one.
    assert found["run"] == {
        "written": True,
        "read": IMAGES,
        "unchanged": [],
        "rebuild_reason": "the earlier run manifest could not be read",
    }


def test_v0_4_0rc4_opened_a_strict_run_written_as_version_2(experiment, tmp_path):
    # The defect itself: rc5 before this change wrote a strict run as version 2, and rc4
    # opened it as a fingerprint-only run (it reused the rows without hashing anything).
    root, out = experiment
    assert run_experiment(root, TYX, output_folder=out, mode=STRICT).written
    data = json.loads((out / "run_manifest.json").read_text(encoding="utf-8"))
    data["manifest_version"] = "2"
    (out / "run_manifest.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    src = extract(RC4, tmp_path / "rc4")
    found = run_with(src, RC4_READS, out, root)
    assert found["read_manifest"] == "opened"
    assert found["run"]["unchanged"] == IMAGES and found["run"]["read"] == []


def test_a_default_manifest_is_byte_identical_to_the_one_before_version_3(experiment, tmp_path):
    root, out = experiment
    off = RunMode("restart", verify_content=False)
    assert run_experiment(root, TYX, output_folder=out, mode=off).written
    src = extract(BEFORE_STRICT_VERSION, tmp_path / "before")
    before_out = tmp_path / "before-out"
    assert run_with(src, DEFAULT_RUN, root, before_out) == {"written": True}
    now = (out / "run_manifest.json").read_bytes()
    before = (before_out / "run_manifest.json").read_bytes()
    # Only the time of writing and the versions may differ (the extracted code reports the
    # installed package's version); everything else is the same bytes.
    data, earlier = json.loads(now), json.loads(before)
    # The opt-out is the one new key (#159); everything else is as before.
    assert data["settings"].pop("verify_content") is False
    now = (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    assert data.keys() == earlier.keys()
    earlier["written_at"], earlier["versions"] = data["written_at"], data["versions"]
    text = json.dumps(earlier, indent=2, ensure_ascii=False) + "\n"
    assert json.dumps(data, indent=2, ensure_ascii=False) + "\n" == now.decode("utf-8")
    assert text.encode("utf-8") == now
    assert (out / "plane_histograms.csv").read_bytes() == (
        before_out / "plane_histograms.csv"
    ).read_bytes()


def _strict_data(out):
    return json.loads((out / "run_manifest.json").read_text(encoding="utf-8"))


def test_the_reader_refuses_a_version_that_does_not_match_its_content_check(experiment):
    root, out = experiment
    assert run_experiment(root, TYX, output_folder=out, mode=STRICT).written
    path = out / "run_manifest.json"
    cases = []
    data = _strict_data(out)
    data["manifest_version"] = "2"  # a strict run written as version 2 (unreleased rc5)
    cases.append((data, "version 2 manifest records content verification"))
    data = _strict_data(out)
    del data["settings"]["verify_content"]
    cases.append((data, "must have verify_content true"))
    data = _strict_data(out)
    data["files"][1]["content_sha256"] = None
    cases.append((data, "has no content_sha256"))
    data = _strict_data(out)
    data["manifest_version"] = "2"
    del data["settings"]["verify_content"]  # a content_sha256 left in a version 2 entry
    cases.append((data, "version 2 manifest records content verification"))
    data = _strict_data(out)
    data["manifest_version"] = "4"
    cases.append((data, "(?i)manifest version '4' is not supported"))
    for data, message in cases:
        path.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(ManifestFormatError, match=message):
            read_manifest(path)


def test_the_writer_refuses_what_the_reader_refuses(experiment, tmp_path):
    root, out = experiment
    assert run_experiment(root, TYX, output_folder=out, mode=STRICT).written
    strict = read_manifest(out / "run_manifest.json")
    with pytest.raises(ValueError, match="records content verification"):
        write_manifest(replace(strict, manifest_version="2"), tmp_path / "m.json")
    unhashed = replace(strict, files=(replace(strict.files[0], content_sha256=None),))
    with pytest.raises(ValueError, match="has no content_sha256"):
        write_manifest(unhashed, tmp_path / "m.json")
    assert not (tmp_path / "m.json").exists()
    assert isinstance(strict, RunManifest)


def test_version_for_and_check_version():
    assert (version_for(False), version_for(True)) == ("2", "3")
    check_version("1", False, [("a.tif", None)])
    check_version("3", True, [("a.tif", "0" * 64)])
    with pytest.raises(ValueError, match="'99' is not supported"):
        check_version("99", False, [])
    with pytest.raises(ValueError, match="'a.tif'"):
        check_version("1", False, [("a.tif", "0" * 64)])


@pytest.mark.parametrize("files", [[], "not a list"])
def test_an_unknown_later_version_is_plainly_not_supported(experiment, files):
    # Review of #146: a later version was reported as "incomplete or malformed"; whatever
    # else it holds, it is refused as a version this package does not read.
    root, out = experiment
    assert run_experiment(root, TYX, output_folder=out, mode=STRICT).written
    data = _strict_data(out)
    data["manifest_version"] = "4"
    data["files"] = files
    path = out / "run_manifest.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ManifestFormatError) as refused:
        read_manifest(path)
    assert str(refused.value) == (
        "Manifest version '4' is not supported (this version reads '1', '2', '3')."
    )
