"""The lab-tool links input (PP-2, Spec experiment-v1 §9.3): the file, its checks and the CLI.

The fixed keys only, each of its type and never empty, nothing trimmed or filled in; a key
given twice is refused; repeat codes are checked against the description's canonical repeat
IDs and need the user's confirmation; the manifest reads back what the run wrote. The run side
(the links change no output, digest or cache key) is ``test_spec_lab_links.py``.
"""

from __future__ import annotations

import json
import pickle

import pytest

from description_tree import MAPPING, build_experiment
from morphobricks_preprocessing.cli import main
from morphobricks_preprocessing.description import description_from_bytes
from morphobricks_preprocessing.lab_links import (
    MISSING_REPEAT,
    REPEAT_TWICE,
    UNCONFIRMED,
    UNKNOWN_REPEAT,
    LabLinks,
    LabLinksError,
    parse_lab_links,
    recorded_lab_links,
    repeat_code_issues,
)
from morphobricks_preprocessing.lab_links_file import read_lab_links
from morphobricks_preprocessing.manifest import ManifestFormatError, RunManifest, read_manifest
from morphobricks_preprocessing.run import RunMode, run_experiment
from spec_pin import fixtures

V03 = next(item for item in fixtures("valid") if item.name.startswith("v03_")).data
COMMIT = "0123456789abcdef0123456789abcdef01234567"
GOOD = {
    "repository_url": "https://example.org/lab/database",
    "commit": COMMIT,
    "experiment_path": "experiments/2026-11-06",
    "repeat_codes": {"R002": "Day2", "R001": "Day1"},
}


def _bytes(data):
    return json.dumps(data).encode("utf-8")


def _with(**changes):
    data = dict(GOOD)
    for key, value in changes.items():
        if value is _with:
            data.pop(key)
        else:
            data[key] = value
    return data


def test_the_fixed_keys_are_read_and_written_verbatim():
    links = parse_lab_links(_bytes(GOOD))
    assert links.repository_url == GOOD["repository_url"]
    assert links.commit == COMMIT and links.experiment_path == GOOD["experiment_path"]
    assert dict(links.repeat_codes) == GOOD["repeat_codes"]
    assert not links.repeat_codes_confirmed  # never read from the file
    assert links.to_dict() == {**GOOD, "repeat_codes": {"R001": "Day1", "R002": "Day2"}}
    assert list(links.to_dict()["repeat_codes"]) == ["R001", "R002"]
    plain = parse_lab_links(_bytes(_with(repeat_codes=_with)))
    assert plain.repeat_codes is None and "repeat_codes" not in plain.to_dict()
    assert parse_lab_links(_bytes(GOOD), confirmed=True).repeat_codes_confirmed


def test_a_sha256_commit_and_an_ssh_remote_are_accepted():
    data = _with(commit="ab" * 32, repository_url="git@example.org:lab/database.git")
    assert parse_lab_links(_bytes(data)).commit == "ab" * 32


@pytest.mark.parametrize(
    ("data", "found"),
    [
        (_with(labchronicle="x"), "unknown key 'labchronicle'"),
        (_with(repeat_codes_confirmed=True), "unknown key 'repeat_codes_confirmed'"),
        (_with(commit=_with), "the key commit is missing"),
        (_with(commit=_with, experiment_path=_with), "the keys commit, experiment_path are"),
        (_with(repository_url=1), "repository_url must be text, found int"),
        (_with(repository_url=None), "repository_url must be text, found NoneType"),
        (_with(experiment_path=["a"]), "experiment_path must be text, found list"),
        (_with(commit=True), "commit must be text, found bool"),
        (_with(repository_url=""), "repository_url is empty"),
        (_with(experiment_path=" experiments/a"), "has spaces around it"),
        (_with(experiment_path="a\tb"), "control characters"),
        (_with(commit=COMMIT[:7]), "not a full commit SHA"),
        (_with(commit=COMMIT.upper()), "not a full commit SHA"),
        (_with(commit=COMMIT + "0"), "not a full commit SHA"),
        (_with(repeat_codes=None), "repeat_codes is null"),
        (_with(repeat_codes={}), "repeat_codes is empty"),
        (_with(repeat_codes=["R001", "Day1"]), "repeat_codes must map"),
        (_with(repeat_codes={"R001": 1}), "the repeat ID of code 'R001' must be text"),
        (_with(repeat_codes={"R001": ""}), "the repeat ID of code 'R001' is empty"),
        (_with(repeat_codes={"": "Day1"}), "a repeat code is empty"),
        (_with(repeat_codes={"R001 ": "Day1"}), "spaces around it"),
        ([GOOD], "one JSON object"),
        ("links", "one JSON object"),
    ],
)
def test_anything_but_the_fixed_keys_with_text_values_is_refused(data, found):
    with pytest.raises(LabLinksError) as caught:
        parse_lab_links(_bytes(data))
    assert found in str(caught.value)


@pytest.mark.parametrize(
    ("text", "found"),
    [
        (b'{"commit": "a", "commit": "b"}', "the key 'commit' is given twice"),
        (
            b'{"repository_url": "u", "commit": "' + COMMIT.encode() + b'", '
            b'"experiment_path": "p", "repeat_codes": {"R001": "Day1", "R001": "Day2"}}',
            "the key 'R001' is given twice",
        ),
        (b"{not json", "not valid JSON"),
        (b"\xff\xfe", "not UTF-8"),
    ],
)
def test_a_key_given_twice_or_a_file_that_is_not_json_is_refused(text, found):
    with pytest.raises(LabLinksError) as caught:
        parse_lab_links(text)
    assert found in str(caught.value)


def test_the_file_reader_names_the_file(tmp_path):
    path = tmp_path / "links.json"
    with pytest.raises(LabLinksError, match="cannot be read"):
        read_lab_links(path)
    path.write_bytes(_bytes(_with(commit="abc")))
    with pytest.raises(LabLinksError) as caught:
        read_lab_links(path)
    assert f"{path} is refused: commit 'abc'" in str(caught.value)  # a Windows path is no regex
    path.write_bytes(_bytes(GOOD))
    assert read_lab_links(path, confirmed=True).repeat_codes_confirmed


def _codes(codes, confirmed=True):
    return LabLinks("u", COMMIT, "p", codes, confirmed)


V03_DESCRIPTION = description_from_bytes(V03)


def test_confirmed_codes_naming_every_repeat_once_pass():
    assert repeat_code_issues(_codes({"R001": "Day1", "R002": "Day2"}), V03_DESCRIPTION) == []
    assert repeat_code_issues(LabLinks("u", COMMIT, "p"), V03_DESCRIPTION) == []
    assert repeat_code_issues(None, V03_DESCRIPTION) == []
    assert repeat_code_issues(_codes({"R001": "x"}), None) == []  # the run is refused anyway


def test_every_refusal_of_the_codes_is_reported_together():
    links = _codes({"R001": "Day1", "R002": "Day1", "R003": "rep2"}, confirmed=False)
    issues = repeat_code_issues(links, V03_DESCRIPTION)
    assert [issue.code for issue in issues] == [
        UNCONFIRMED,
        UNKNOWN_REPEAT,
        REPEAT_TWICE,
        MISSING_REPEAT,
    ]
    assert all(issue.is_error for issue in issues)
    unconfirmed, unknown, twice, missing = issues
    assert unconfirmed.paths == ("R001 -> Day1", "R002 -> Day1", "R003 -> rep2")
    assert "equal names are not evidence" in unconfirmed.message
    assert "--confirm-repeat-codes" in unconfirmed.action
    assert unknown.paths == ("rep2",)  # a source folder of v03, not a canonical repeat ID
    assert twice.paths == ("R001 -> Day1", "R002 -> Day1")
    assert missing.paths == ("Day2",)


def test_codes_equal_to_the_repeat_ids_still_need_the_confirmation():
    issues = repeat_code_issues(_codes({"Day1": "Day1", "Day2": "Day2"}, False), V03_DESCRIPTION)
    assert [issue.code for issue in issues] == [UNCONFIRMED]


def test_the_manifest_entry_is_read_strictly():
    assert recorded_lab_links(None) is None
    assert recorded_lab_links(dict(GOOD)) == LabLinks.from_data(GOOD).to_dict()
    with pytest.raises(LabLinksError):
        recorded_lab_links({**GOOD, "extra": 1})


def test_a_manifest_reads_back_its_links_and_refuses_a_malformed_entry(tmp_path):
    root, out = tmp_path / "exp", tmp_path / "out"
    out.mkdir()
    (out / "morphobricks.toml").write_bytes(V03)
    build_experiment(root, V03_DESCRIPTION, 1)
    links = parse_lab_links(_bytes(GOOD), confirmed=True)
    result = run_experiment(root, MAPPING, output_folder=out, mode=RunMode(lab_links=links))
    assert result.written, [i.message for i in result.issues]
    manifest = read_manifest(out / "run_manifest.json")
    assert manifest.lab_links == links.to_dict()
    data = manifest.to_dict()
    assert RunManifest.from_dict(data) == manifest
    for bad in ({**GOOD, "commit": "short"}, {**GOOD, "extra": 1}, "links"):
        with pytest.raises(ManifestFormatError):
            RunManifest.from_dict({**data, "lab_links": bad})


def test_run_mode_refuses_links_of_another_type(tmp_path):
    with pytest.raises(ValueError, match="lab_links must be LabLinks or None; found dict"):
        run_experiment(tmp_path, output_folder=tmp_path / "out", mode=RunMode(lab_links=GOOD))


def test_links_pickle():
    links = parse_lab_links(_bytes(GOOD), confirmed=True)
    assert pickle.loads(pickle.dumps(links)) == links


# The command line.


@pytest.fixture
def described(tmp_path):
    root, out = tmp_path / "exp", tmp_path / "out"
    out.mkdir()
    (out / "morphobricks.toml").write_bytes(V03)
    build_experiment(root, V03_DESCRIPTION, 1)
    return root, out


def _run(root, out, *extra):
    return main(["run", str(root), "--output", str(out), *extra])


def test_cli_records_confirmed_links_and_prints_them(described, tmp_path, capsys):
    root, out = described
    path = tmp_path / "links.json"
    path.write_bytes(_bytes(GOOD))
    assert _run(root, out, "--lab-links", str(path), "--confirm-repeat-codes") == 0
    printed = capsys.readouterr().out
    assert "Lab-tool links (recorded in the run manifest only" in printed
    assert f"  commit: {COMMIT}" in printed
    assert "  repeat codes (confirmed):\n    R001 -> Day1\n    R002 -> Day2" in printed
    manifest = json.loads((out / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["lab_links"] == parse_lab_links(_bytes(GOOD)).to_dict()


def test_cli_refuses_unconfirmed_codes_and_names_the_option(described, tmp_path, capsys):
    root, out = described
    path = tmp_path / "links.json"
    path.write_bytes(_bytes(GOOD))
    assert _run(root, out, "--lab-links", str(path)) == 1
    printed = capsys.readouterr().out
    assert "repeat codes (not confirmed)" in printed
    assert "the mapping is not confirmed" in printed and "--confirm-repeat-codes" in printed
    assert not (out / "run_manifest.json").exists()


def test_cli_without_codes_needs_no_confirmation(described, tmp_path):
    root, out = described
    path = tmp_path / "links.json"
    path.write_bytes(_bytes(_with(repeat_codes=_with)))
    assert _run(root, out, "--lab-links", str(path)) == 0
    manifest = json.loads((out / "run_manifest.json").read_text(encoding="utf-8"))
    assert "repeat_codes" not in manifest["lab_links"]


@pytest.mark.parametrize(
    ("content", "extra", "found"),
    [
        (_with(source_database="x"), (), "unknown key 'source_database'"),
        (_with(commit=_with), (), "the key commit is missing"),
        (None, (), "cannot be read"),
        (_with(repeat_codes=_with), ("--confirm-repeat-codes",), "has no repeat_codes"),
    ],
)
def test_cli_refuses_a_bad_file_before_any_image_is_read(
    described, tmp_path, capsys, content, extra, found
):
    root, out = described
    path = tmp_path / "links.json"
    if content is not None:
        path.write_bytes(_bytes(content))
    assert _run(root, out, "--lab-links", str(path), *extra) == 1
    printed = capsys.readouterr().out
    assert found in printed and "repository_url, commit and experiment_path" in printed
    assert sorted(p.name for p in out.iterdir()) == ["morphobricks.toml"]  # nothing written


def test_cli_refuses_the_confirmation_without_links(described, capsys):
    root, out = described
    assert _run(root, out, "--confirm-repeat-codes") == 1
    assert "--confirm-repeat-codes is given without --lab-links" in capsys.readouterr().out
    assert sorted(p.name for p in out.iterdir()) == ["morphobricks.toml"]


@pytest.mark.parametrize(
    ("url", "found"),
    [
        ("https://user:secret@example.org/lab/database", "a password before '@'"),
        ("https://user:@example.org/lab/database", "a password before '@'"),
        ("ssh://git:secret@example.org/lab/database.git", "a password before '@'"),
        ("user:secret@example.org:lab/database.git", "a password before '@'"),
        ("https://ghp_0123456789abcdef@example.org/lab/database", "a user name before '@'"),
        ("HTTP://user@example.org/lab/database", "a user name before '@' in a http URL"),
        ("https://example.org/lab/database?token=secret", "a query string or a fragment"),
        ("https://example.org/lab/database?", "a query string or a fragment"),
        ("https://example.org/lab/database#main", "a query string or a fragment"),
        ("https://[::1/lab/database", "not a URL that can be read"),
        (" https://user:secret@example.org/db", "a password before '@'"),  # not echoed
    ],
)
def test_a_repository_url_with_a_credential_query_or_fragment_is_refused(url, found):
    # Coordinator decision (PR #264): the manifest records the URL verbatim, so a credential
    # must never reach it; the refusal does not repeat the secret either.
    with pytest.raises(LabLinksError) as caught:
        parse_lab_links(_bytes(_with(repository_url=url)))
    assert found in str(caught.value)
    assert "secret" not in str(caught.value) and "ghp_" not in str(caught.value)
    with pytest.raises(LabLinksError):
        LabLinks(url, COMMIT, "experiments/2026-11-06")


@pytest.mark.parametrize(
    "url",
    [
        "https://example.org/lab/database",
        "https://example.org:8443/lab/database.git",
        "git@example.org:lab/database.git",
        "ssh://git@example.org/lab/database.git",
        "git+ssh://git@example.org/lab/database.git",
        "ssh://example.org:2222/lab/database.git",
        "/srv/lab/database",
        "https://example.org/lab/data@2026",
        "C:\\lab\\data@2026",
    ],
)
def test_a_plain_repository_url_or_ssh_remote_is_accepted_verbatim(url):
    assert parse_lab_links(_bytes(_with(repository_url=url))).repository_url == url


def test_cli_refuses_a_url_with_a_credential_before_anything_is_written(
    described, tmp_path, capsys
):
    root, out = described
    path = tmp_path / "links.json"
    path.write_bytes(_bytes(_with(repository_url="https://u:secret@example.org/db")))
    assert _run(root, out, "--lab-links", str(path), "--confirm-repeat-codes") == 1
    printed = capsys.readouterr().out
    assert "is refused" in printed and "a password before '@'" in printed
    assert "secret" not in printed
    assert sorted(p.name for p in out.iterdir()) == ["morphobricks.toml"]  # nothing written
