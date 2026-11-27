"""When cell 5 keeps cell 3's confirmed axes: nothing changed since its folder check (v0.9.1).

Register the masks needed two presses: the first always checked the folders again and stopped
for the axes. Now the axes are kept, and the masks paired alone, only when the folders and the
image headers are unchanged (by fingerprint); anything else is named, in words.
"""

import os
from pathlib import Path
from types import SimpleNamespace

from morphobricks_preprocessing.fingerprint import current_fingerprint
from morphobricks_preprocessing.register_checks import changes_since_check


def checked(tmp_path, names=("a.tif", "b.tif")):
    root = tmp_path / "exp"
    root.mkdir()
    for name in names:
        (root / name).write_bytes(name.encode())
    pre = SimpleNamespace(
        discovery=SimpleNamespace(root=str(root)),
        header_fingerprints={name: current_fingerprint(root / name) for name in names},
    )
    app = SimpleNamespace(preflight=pre, confirmed_mapping=object())
    entries = [SimpleNamespace(relative_path=name) for name in names]
    inputs = SimpleNamespace(experiment_root=root, manifest=SimpleNamespace(files=entries))
    return app, inputs, root


def test_nothing_changed_keeps_the_axes(tmp_path):
    app, inputs, _ = checked(tmp_path)
    assert changes_since_check(app, inputs) == []


def test_each_change_is_named(tmp_path):
    app, inputs, root = checked(tmp_path)
    stat = (root / "a.tif").stat()
    os.utime(root / "a.tif", ns=(stat.st_atime_ns, stat.st_mtime_ns + 10**9))
    assert changes_since_check(app, inputs) == [
        "1 image changed since cell 3 checked the folder (a.tif)"
    ]
    app.confirmed_mapping = None
    assert changes_since_check(app, inputs) == ["the axes are not confirmed in cell 3"]
    app.preflight = None
    assert changes_since_check(app, inputs) == ["cell 3 holds no folder check"]


def test_another_experiment_or_other_images_are_changes(tmp_path):
    app, inputs, root = checked(tmp_path)
    inputs.experiment_root = Path(tmp_path / "elsewhere")
    assert changes_since_check(app, inputs)[0].startswith("cell 3 checked another experiment")
    inputs.experiment_root = root
    inputs.manifest.files = inputs.manifest.files[:1]
    assert changes_since_check(app, inputs) == [
        "the images cell 3 checked are not the images of the run"
    ]


def test_a_header_not_bound_to_its_file_is_a_change(tmp_path):
    app, inputs, _ = checked(tmp_path)
    app.preflight.header_fingerprints["b.tif"] = None  # the file moved while its header was read
    assert "1 image changed" in changes_since_check(app, inputs)[0]
