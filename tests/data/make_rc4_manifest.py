"""Write the v0.4.0rc4 manifest fixture of ``test_manifest_position_id.py`` (audit A-m5).

Run once, from the repository root, with the code of v0.4.0rc4 first on the path and the
repository's dev dependencies (zarr included) installed:

    git archive v0.4.0rc4 src | tar -x -C ../rc4
    PYTHONPATH=../rc4/src python tests/data/make_rc4_manifest.py

It writes ``rc4_run_manifest.json`` next to this script: the ``run_manifest.json`` that
v0.4.0rc4's ``run_experiment`` wrote for a small experiment whose image names end in every
way a position identifier is cut from (``.tif``, ``.TIFF``, ``.ome.tif``, a name with dots,
``.ome.zarr``). The fixture is committed, so the test reads what that release wrote rather
than what the current writer writes.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import numpy as np

HERE = Path(__file__).parent


def main() -> None:
    """Run v0.4.0rc4 on the experiment and keep its manifest."""
    sys.path.insert(0, str(HERE.parent))
    import morphobricks_preprocessing as package
    from tiff_tree import write_data
    from zarr_tree import write_ngff

    assert package.__version__ == "0.4.0rc4", package.__file__
    pixels = np.arange(64, dtype=np.uint8).reshape(8, 8)
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder) / "exp"
        for name in ("a.tif", "B.TIFF", "img.01.ome.tif", "c d.tif"):
            write_data(root / "ctrl/rep1" / name, pixels, "YX")
        write_data(root / "treated/rep1/a.tif", pixels, "YX")
        write_ngff(root / "treated/rep1/z.ome.zarr", pixels, "yx")
        result = package.run_experiment(root, package.prefill_mapping("YX"))
        data = json.loads(Path(result.manifest_path).read_text(encoding="utf-8"))
    data["experiment_root"] = "/data/exp"  # the temporary folder is not kept
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    (HERE / "rc4_run_manifest.json").write_text(text, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
