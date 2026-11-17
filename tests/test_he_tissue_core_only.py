"""A core-only install: the H&E tissue mask stays opt-in and inert (design §7, review w F4).

In a fresh interpreter whose import of scikit-image is blocked (as without the ``he-mask``
extra), importing the package, opening a run, resolving a request and the help of every command
succeed and leave scikit-image, normalization's ``stain`` module and the helper's module out of
``sys.modules``; the helper, the preview and ``tissue-mask`` each fail naming
``pip install "morphobricks-preprocessing[he-mask]"`` and write nothing.
"""

from __future__ import annotations

import json
import subprocess
import sys

from he_tissue_tree import UNIT, rgb_run, tree

SCRIPT = r"""
import importlib.abc, json, sys

class Blocked(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name == "skimage" or name.startswith("skimage."):
            raise ImportError(f"No module named {name!r} (blocked: core-only install)")
        return None

sys.meta_path.insert(0, Blocked())
sys.modules.pop("skimage", None)
import morphobricks_preprocessing
from morphobricks_preprocessing import MaskRequest, Request, RunInputs
from morphobricks_preprocessing.cli import main

out, image = sys.argv[1], sys.argv[2]
found = {"helps": {}}
for command in ([], ["check"], ["run"], ["masks"], ["calibrate"], ["describe"], ["convert"],
                ["convert-mask"], ["tissue-mask"]):
    try:
        main([*command, "--help"])
    except SystemExit as stop:
        found["helps"][" ".join(command) or "-"] = stop.code
run = RunInputs.open(out)
run.resolve_request(Request((), (MaskRequest("Nuclei"),)))
watched = (
    "skimage", "morphobricks_normalization.stain", "morphobricks_preprocessing.he_tissue_mask"
)
found["loaded"] = [name for name in watched if name in sys.modules]
for name, call in (
    ("helper", lambda: run.he_tissue_mask(image, output_mask_id="HETissue")),
    ("preview", lambda: run.preview_he_tissue_mask(image)),
):
    try:
        call()
        found[name] = "no refusal"
    except ImportError as exc:
        found[name] = str(exc)
found["cli"] = main(["tissue-mask", "--output", out, "--to", "HETissue"])
print("RESULT" + json.dumps(found))
"""
EXTRA = 'pip install "morphobricks-preprocessing[he-mask]"'


def test_a_core_only_install_is_inert_and_names_the_extra(tmp_path):
    run = rgb_run(tmp_path)
    before = tree(run.masks_root)
    done = subprocess.run(
        [sys.executable, "-c", SCRIPT, str(run.output_folder), f"{UNIT}/a.tif"],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    line = next(text for text in done.stdout.splitlines() if text.startswith("RESULT"))
    found = json.loads(line.removeprefix("RESULT"))
    assert set(found["helps"].values()) == {0} and len(found["helps"]) == 9
    assert found["loaded"] == []
    assert EXTRA in found["helper"] and EXTRA in found["preview"]
    assert found["cli"] == 1 and EXTRA in done.stdout
    assert tree(run.masks_root) == before
