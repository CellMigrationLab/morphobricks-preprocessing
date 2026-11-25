"""The conversion check reads its image paths once, whatever iterable they come in (#386)."""

import pytest

from morphobricks_preprocessing.conversion_check import conversion_problem
from morphobricks_preprocessing.manifest import read_manifest

pytestmark = pytest.mark.filterwarnings("ignore:No experiment description:UserWarning")


def test_a_generator_of_paths_is_checked_as_a_list(tmp_path):
    from test_run_projection import projected_run

    copies, out, _, _ = projected_run(tmp_path, method="max")
    manifest = read_manifest(out / "run_manifest.json")
    paths = [entry.relative_path for entry in manifest.files]
    args = (copies, manifest.conversion_record_sha256)
    assert conversion_problem(*args, paths, manifest.projection) is None
    assert conversion_problem(*args, (p for p in paths), manifest.projection) is None
    assert conversion_problem(*args, iter(paths), manifest.projection) is None
