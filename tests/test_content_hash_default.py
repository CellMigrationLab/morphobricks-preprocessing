"""Content hashing of the sources is on by default; an opt-out is recorded (#159).

Without it an image edited in place under the same name, size and modification time is
reused as unchanged. A run hashes unless it opts out (``RunMode(verify_content=False)``,
``--no-verify-content``, the notebook's box); the manifest records the choice either way.
"""

import json

import pytest

from described_tree import main_described, run_described
from morphobricks_preprocessing import RunMode, read_manifest
from strict_tree import IMAGES, TYX, rewrite_keeping_metadata, write_experiment

OFF = RunMode("restart", verify_content=False)


@pytest.fixture
def experiment(tmp_path):
    return write_experiment(tmp_path)


def settings_of(out):
    return json.loads((out / "run_manifest.json").read_text(encoding="utf-8"))


def test_a_run_given_no_mode_hashes_every_source_and_records_it(experiment):
    root, out = experiment
    assert run_described(root, TYX, output_folder=out).written
    data = settings_of(out)
    assert data["manifest_version"] == "5" and data["settings"]["verify_content"] is True
    assert all(entry["content_sha256"] for entry in data["files"])
    assert read_manifest(out / "run_manifest.json").settings.verify_content


def test_a_default_run_reads_an_image_rewritten_with_its_metadata_kept(experiment):
    root, out = experiment
    assert run_described(root, TYX, output_folder=out).written
    rewrite_keeping_metadata(root / IMAGES[0])
    again = run_described(root, output_folder=out)
    assert again.written and again.read == (IMAGES[0],) and again.changed == (IMAGES[0],)


def test_an_opt_out_is_recorded_as_false_and_the_rewrite_is_not_seen(experiment):
    root, out = experiment
    assert run_described(root, TYX, output_folder=out, mode=OFF).written
    data = settings_of(out)
    assert data["manifest_version"] == "5" and data["settings"]["verify_content"] is False
    assert not any("content_sha256" in entry for entry in data["files"])
    rewrite_keeping_metadata(root / IMAGES[0])
    again = run_described(root, output_folder=out)  # the earlier run's choice is kept
    assert again.read == () and IMAGES[0] in again.unchanged
    assert settings_of(out)["settings"]["verify_content"] is False


def test_the_cli_hashes_by_default_and_no_verify_content_opts_out(experiment):
    root, out = experiment
    off_out = out.with_name("out_off")
    assert main_described(["run", str(root), "--output", str(out), "--axes", "TYX"]) == 0
    assert settings_of(out)["settings"]["verify_content"] is True
    args = ["run", str(root), "--output", str(off_out), "--axes", "TYX", "--no-verify-content"]
    assert main_described(args) == 0
    assert settings_of(off_out)["settings"]["verify_content"] is False
