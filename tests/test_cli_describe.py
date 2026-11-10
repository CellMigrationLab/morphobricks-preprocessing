"""The command line's ``describe`` (Stage 1, PR 17): print the draft, publish nothing unasked.

- ``describe`` prints the proposals, the open questions and what needs the user's confirmation,
  and writes nothing (no output folder, no draft, no ``morphobricks.toml``).
- ``--publish`` refuses a draft with an open question (exit status 1, every missing item
  listed); it never confirms an ID, a repeat or a date for the user.
- For a published description that matches the folders there is nothing to publish; for one
  the folders no longer match, what needs the user's answer is listed and the file is untouched.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from description_tree import build_experiment
from morphobricks_preprocessing.cli import main
from morphobricks_preprocessing.description import description_from_bytes
from morphobricks_preprocessing.description_file import sha256_hex
from morphobricks_preprocessing.design import DesignRow, DesignTable, write_experiment_design
from morphobricks_preprocessing.discovery import discover_experiment
from morphobricks_preprocessing.draft import draft_from_discovery
from morphobricks_preprocessing.run.description_file import publish_description
from spec_pin import fixtures
from tiff_tree import make_tree

TOML = "morphobricks.toml"
DESIGN = "experiment_design.csv"


def row(condition, repeat, *, control=False, on="2026-10-30", confirmed=True):
    return DesignRow(
        condition,
        repeat,
        repeat,
        control=control,
        image_count=2,
        confirmed=confirmed,
        confirmed_on=on if confirmed else "",
    )


def tree(root, *paths):
    make_tree(root, [f"{p}/{name}.tif" for p in paths for name in ("a", "b")])
    return root


def listing(folder):
    """Every path under ``folder`` with its bytes, to prove that nothing was written."""
    return {p: p.read_bytes() for p in sorted(folder.rglob("*")) if p.is_file()}


@pytest.fixture
def experiment(tmp_path):
    return tree(tmp_path / "exp", "CTRL/rep1", "CTRL/rep2", "DrugA/rep1", "DrugA/rep2")


def test_describe_prints_the_proposals_and_the_open_questions_and_writes_nothing(
    experiment, tmp_path, capsys
):
    output = tmp_path / "out"
    before = listing(tmp_path)
    assert main(["describe", str(experiment), "--output", str(output)]) == 0
    out = capsys.readouterr().out
    assert "Drawn from the folders alone. Nothing here is confirmed" in out
    assert "CTRL: proposed ID 'CTRL'" in out
    assert "rep1: proposed ID 'rep1'; not confirmed" in out
    assert "CTRL / rep1: 2 images" in out
    assert "open questions (answered by you, in the notebook)" in out
    assert "confirm that the folders of repeat rep1 were prepared and acquired" in out
    assert "choose the ID of condition CTRL" in out
    assert listing(tmp_path) == before and not output.exists()


def test_a_folder_that_cannot_be_an_id_has_no_proposal_and_is_not_repaired(tmp_path, capsys):
    root = tree(tmp_path / "exp", "control/rep1", "DrugA/rep1")
    assert main(["describe", str(root)]) == 0
    out = capsys.readouterr().out
    assert "control: no ID can be proposed" in out and "you name it" in out
    assert "DrugA: proposed ID 'DrugA'" in out


def test_describe_reads_the_legacy_design_without_confirming_anything(experiment, tmp_path, capsys):
    output = tmp_path / "out"
    rows = [
        row("CTRL", "rep1", control=True),
        row("CTRL", "rep2", control=True),
        row("DrugA", "rep1", control=True, confirmed=False, on=""),
        row("DrugA", "rep2", control=True, confirmed=False, on=""),
    ]
    output.mkdir()
    write_experiment_design(DesignTable(tuple(rows)), output / DESIGN)
    before = listing(tmp_path)
    assert main(["describe", str(experiment), "--output", str(output)]) == 0
    out = capsys.readouterr().out
    assert "legacy experiment_design.csv" in out
    assert "DrugA -> CTRL" not in out  # two controls: nothing is guessed
    assert "marked as a control, as is another one" in out
    assert "rep1: proposed ID 'rep1'; not confirmed" in out
    assert listing(tmp_path) == before


def test_publish_refuses_a_draft_with_open_questions_and_lists_them(experiment, tmp_path, capsys):
    output = tmp_path / "out"
    before = listing(tmp_path)
    assert main(["describe", str(experiment), "--output", str(output), "--publish"]) == 1
    out = capsys.readouterr().out
    assert "Not published. What is missing:" in out
    missing = out.split("Not published. What is missing:")[1]
    assert missing.count("  - choose the ID of") == 5  # 2 conditions, single, 2 repeats
    assert "  - confirm that the folders of repeat rep2" in out
    assert not (output / TOML).exists() and not output.exists()
    assert listing(tmp_path) == before


def test_publish_never_confirms_for_the_user(experiment, tmp_path, capsys):
    """A fully confirmed legacy design still leaves the IDs to the user: nothing is adopted."""
    output = tmp_path / "out"
    output.mkdir()
    rows = [row(c, r) for c in ("CTRL", "DrugA") for r in ("rep1", "rep2")]
    write_experiment_design(DesignTable(tuple(rows)), output / DESIGN)
    before = listing(tmp_path)
    assert main(["describe", str(experiment), "--output", str(output), "--publish"]) == 1
    out = capsys.readouterr().out
    assert "choose the ID of condition CTRL" in out
    assert "confirmed on 2026-10-30" in out  # the user's own earlier confirmation, as given
    assert listing(tmp_path) == before  # no design rewritten, no description, no draft


def test_a_complete_draft_is_still_not_publishable_from_the_command_line(tmp_path):
    """Every ID and repeat confirmed leaves only what the draft does not hold."""
    from morphobricks_preprocessing.cli_describe import NOT_IN_DRAFT, draft_blockers

    discovery = discover_experiment(tree(tmp_path / "exp", "CTRL/rep1", "DrugA/rep1"))
    draft = draft_from_discovery(discovery)
    for kind, key in [
        ("conditions", "CTRL"),
        ("conditions", "DrugA"),
        ("technical_repeats", "single"),
        ("repeats", "rep1"),
    ]:
        draft = draft.confirm_id(kind, key, key)
    assert [q.code for q in draft.open_questions()] == ["repeat_not_confirmed"]
    assert any("same circumstances" in item for item in draft_blockers(draft))
    done = replace(
        draft,
        repeats={"rep1": replace(draft.repeats["rep1"], confirmed=True, confirmed_on="2026-11-01")},
    )
    assert done.publishable
    assert draft_blockers(done) == [NOT_IN_DRAFT]


def test_a_legacy_design_that_cannot_be_read_is_refused_and_left_alone(
    experiment, tmp_path, capsys
):
    output = tmp_path / "out"
    output.mkdir()
    (output / DESIGN).write_text("not,a,design\n1,2,3\n", encoding="utf-8")
    before = listing(tmp_path)
    assert main(["describe", str(experiment), "--output", str(output)]) == 1
    assert "cannot be drafted" in capsys.readouterr().out
    assert listing(tmp_path) == before


def test_a_missing_experiment_folder_is_an_error(tmp_path, capsys):
    assert main(["describe", str(tmp_path / "nowhere")]) == 1
    assert "does not exist or is not a folder" in capsys.readouterr().out


# --- a published description -------------------------------------------------------------


V01 = next(f for f in fixtures("valid") if f.name.startswith("v01_"))


def published(tmp_path):
    """Publish a description whose counts match the folders written for it (2 images a unit)."""
    description = description_from_bytes(V01.data)
    repeats = {}
    for key, repeat in description.repeats.items():
        members = {
            cid: replace(m, image_count=2 * len(m.technical_repeats))
            for cid, m in repeat.members.items()
        }
        repeats[key] = replace(repeat, members=members)
    description = replace(description, repeats=repeats)
    root, output = tmp_path / "exp", tmp_path / "out"
    output.mkdir()
    build_experiment(root, description)
    publish_description(output, description, base_sha256="absent")
    return root, output


def test_a_published_description_that_matches_the_folders_has_nothing_to_publish(tmp_path, capsys):
    root, output = published(tmp_path)
    before = listing(tmp_path)
    assert main(["describe", str(root), "--output", str(output), "--publish"]) == 0
    out = capsys.readouterr().out
    assert "No change." in out and "the channels the image headers state are compared" in out
    assert "Nothing to publish" in out
    assert listing(tmp_path) == before


def test_a_new_folder_beside_a_published_description_needs_the_users_answer(tmp_path, capsys):
    root, output = published(tmp_path)
    make_tree(root, ["Extra/day9/a.tif"])
    sha = sha256_hex((output / TOML).read_bytes())
    before = listing(tmp_path)
    assert main(["describe", str(root), "--output", str(output)]) == 0
    assert "new, no ID yet: Extra / day9 / single" in capsys.readouterr().out
    assert main(["describe", str(root), "--output", str(output), "--publish"]) == 1
    out = capsys.readouterr().out
    assert "Extra / day9 / single is new: say which repeat" in out
    assert sha256_hex((output / TOML).read_bytes()) == sha
    assert listing(tmp_path) == before


def test_a_description_that_cannot_be_read_is_refused(tmp_path, capsys):
    root, output = published(tmp_path)
    (output / TOML).write_text("schema = 'nope'\n", encoding="utf-8")
    assert main(["describe", str(root), "--output", str(output)]) == 1
    assert "cannot be used" in capsys.readouterr().out
