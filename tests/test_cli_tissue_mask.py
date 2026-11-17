"""The command line's ``tissue-mask`` (Spec ADR 0005 BM15; design §7 F4): end to end.

Arguments, exit codes, the files and lines written, a no-op retry, an unverified identity's
note, a refusal's message and exit code, and the extra named when scikit-image is missing.
"""

from __future__ import annotations

import json
import sys

import pytest

from he_tissue_tree import IDENTITY_PATCH, UNIT, identities, rgb_run, tree
from morphobricks_preprocessing.cli import build_parser, main
from morphobricks_preprocessing.mask_lineage import LINEAGE_FILENAME

IMAGE = f"{UNIT}/a.tif"


def test_the_help_names_the_command_and_its_options(capsys):
    with pytest.raises(SystemExit) as stopped:
        main(["--help"])
    assert stopped.value.code == 0
    assert "tissue-mask" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        main(["tissue-mask", "--help"])
    text = capsys.readouterr().out
    for part in ("--output FOLDER", "--to NEW_ID", "--image PATH", "--rule RULE", "he_tissue_v1"):
        assert part in text
    assert "tissue-mask" in build_parser().format_help()


def test_missing_arguments_exit_2(capsys):
    with pytest.raises(SystemExit) as stopped:
        main(["tissue-mask", "--output", "x"])
    assert stopped.value.code == 2


def test_it_writes_every_rgb_image_and_names_the_next_steps(tmp_path, capsys, monkeypatch):
    pytest.importorskip("skimage")
    monkeypatch.setattr(IDENTITY_PATCH, lambda: identities())
    run = rgb_run(tmp_path)
    args = ["tissue-mask", "--output", str(run.output_folder), "--to", "HETissue"]
    assert main(args) == 0
    printed = capsys.readouterr().out
    assert "Fixed rule he_tissue_v1 (provisional)" in printed
    assert f"{IMAGE}: HETissue/{IMAGE} (written), kept 80.4 %" in printed
    assert "declare the mask HETissue (source type HETissue) as binary" in printed
    assert "run preprocessing and the masks step again" in printed
    assert "never pass it as cell-mode labels" in printed
    line = json.loads((run.masks_root / "HETissue" / LINEAGE_FILENAME).read_text())
    assert line["operation"] == "he_tissue_mask" and line["reusable"] is True
    before = tree(run.masks_root)
    assert main([*args, "--image", IMAGE]) == 0  # the same call: a no-op
    assert "(already written)" in capsys.readouterr().out
    assert tree(run.masks_root) == before


def test_an_unverified_identity_is_said_and_its_retry_refused(tmp_path, capsys, monkeypatch):
    pytest.importorskip("skimage")
    code = identities(verified=False, reason="editable install")
    monkeypatch.setattr(IDENTITY_PATCH, lambda: code)
    run = rgb_run(tmp_path)
    args = ["tissue-mask", "--output", str(run.output_folder), "--to", "HETissue"]
    assert main(args) == 0
    assert "this mask will not be reused on retry" in capsys.readouterr().out
    assert main(args) == 1
    assert "never reused" in capsys.readouterr().out


def test_a_refusal_is_printed_and_exits_1_writing_nothing(tmp_path, capsys):
    run = rgb_run(tmp_path)
    before = tree(run.masks_root)
    args = ["tissue-mask", "--output", str(run.output_folder), "--to", "Nuclei"]
    assert main(args) == 1
    printed = capsys.readouterr().out
    assert printed.splitlines()[-1].startswith(f"{IMAGE}: no tissue mask written. The output")
    assert "is declared in the description already" in printed
    assert main(["tissue-mask", "--output", str(tmp_path / "none"), "--to", "X"]) == 1
    assert "The run cannot be used" in capsys.readouterr().out
    assert tree(run.masks_root) == before


def test_without_scikit_image_it_names_the_extra_and_writes_nothing(tmp_path, capsys, monkeypatch):
    from morphobricks_preprocessing import he_tissue_mask as helper

    run = rgb_run(tmp_path)
    before = tree(run.masks_root)
    monkeypatch.setitem(sys.modules, "skimage", None)
    monkeypatch.setattr(helper, "hed_matrix", helper.hed_matrix.__wrapped__)
    args = ["tissue-mask", "--output", str(run.output_folder), "--to", "HETissue"]
    assert main(args) == 1
    assert 'pip install "morphobricks-preprocessing[he-mask]"' in capsys.readouterr().out
    assert tree(run.masks_root) == before
