"""The API surface the other bricks use (PP-1, contract §9.1 and §16): names and types pinned.

The consuming bricks read a run only through ``RunInputs``, its ``Fov``s and the request
resolver, and build their test runs with ``make_described_run`` and ``make_clean_run``. An
accidental rename, a removed method, a changed field type or a changed signature fails here; a
deliberate change updates this test, the contract and the CHANGELOG together.
"""

from __future__ import annotations

import dataclasses
import inspect

import pytest

import morphobricks_preprocessing as package
from morphobricks_preprocessing import (
    Capabilities,
    Fov,
    FovSelection,
    MaskRequest,
    MaskSelection,
    PlaneSelection,
    Provenance,
    Refusal,
    RepeatCoverage,
    Request,
    ResolvedRequest,
    RunInputs,
)
from morphobricks_preprocessing.run_inputs_request import COVERAGE, IMAGE_TYPES
from morphobricks_preprocessing.testing import (
    CleanRun,
    described_images,
    make_clean_run,
    make_described_run,
)

PATH = "str | os.PathLike"
RUN_INPUTS_METHODS = {
    "open": (
        f"(output_folder: '{PATH}', experiment_root: '{PATH} | None' = None, "
        f"masks_root: '{PATH} | None' = None) -> 'RunInputs'"
    ),
    "fovs": "(self) -> 'tuple[Fov, ...]'",
    "pixel_sizes": "(self, fov: 'Fov | str') -> 'dict[str, RecordedSize]'",
    "read_plane": (
        "(self, fov: 'Fov | str', z: 'int' = 0, t: 'int' = 0, *, "
        "memory_limit: 'int' = 1073741824) -> 'np.ndarray'"
    ),
    "read_image": "(self, fov: 'Fov | str') -> 'LazyImage'",
    "mask_types": "(self) -> 'tuple[str, ...]'",
    "read_mask": (
        "(self, fov: 'Fov | str', mask_type: 'str', z: 'int | None' = None, "
        "t: 'int | None' = None) -> 'np.ndarray'"
    ),
    "histograms": "(self) -> 'tuple[PlaneRecord, ...]'",
    "description": "(self) -> 'Description'",
    "section_digests": "(self) -> 'dict[str, str]'",
    "images_without_header_channel_names": "(self) -> 'dict[str, tuple[int, ...]]'",
    "channel_index": "(self, fov: 'object', channel_id: 'str') -> 'int'",
    "references": "(self) -> 'dict[str, str | None]'",
    "mask_semantics": "(self) -> 'dict[str, MaskSemantics]'",
    "mask_check_status": "(self, mask_id: 'str', fov: 'object') -> 'str'",
    "require_checked": "(self, recorded: 'Mapping', mask_type: 'str') -> 'None'",
    "resolve_request": "(self, request: 'Request') -> 'ResolvedRequest'",
}
RUN_INPUTS_PROPERTIES = {"files", "projection"}

FIELDS = {
    RunInputs: [
        ("output_folder", "Path"),
        ("experiment_root", "Path"),
        ("masks_root", "Path | None"),
        ("manifest", "RunManifest"),
        ("records", "tuple[PlaneRecord, ...]"),
    ],
    Fov: [
        ("key", "tuple[str, str, str, str]"),
        ("relative_path", "str"),
        ("plane_ids", "tuple[str, ...]"),
        ("axes", "str"),
        ("roles", "tuple[str | None, ...]"),
        ("axes_from", "str"),
        ("channel_names", "tuple[str | None, ...]"),
        ("dtype", "str"),
        ("significant_bits", "int | None"),
        ("z_count", "int"),
        ("t_count", "int"),
        ("c_count", "int"),
        ("repeat_group", "str | None"),
        ("condition_id", "str | None"),
        ("technical_repeat_id", "str | None"),
        ("channel_ids", "tuple[str, ...] | None"),
        ("image_type", "str | None"),
    ],
    Request: [
        ("channel_ids", "tuple[str, ...]"),
        ("masks", "tuple[MaskRequest, ...]"),
        ("planes", "str"),
        ("capabilities", "Capabilities"),
        ("device", "str"),
    ],
    MaskRequest: [("mask_id", "str"), ("role", "str"), ("binary_mask_policy", "str | None")],
    Capabilities: [
        ("rgb_support", "bool"),
        ("volume_support", "bool"),
        ("parent_labels_support", "bool"),
    ],
    ResolvedRequest: [
        ("selections", "tuple[FovSelection, ...]"),
        ("refusals", "tuple[Refusal, ...]"),
        ("coverage", "tuple[RepeatCoverage, ...]"),
        ("provenance", "Provenance"),
    ],
    FovSelection: [
        ("key", "tuple[str, str, str, str]"),
        ("relative_path", "str"),
        ("repeat_group", "str"),
        ("condition_id", "str"),
        ("technical_repeat_id", "str"),
        ("image_type", "str"),
        ("channel_ids", "tuple[str, ...]"),
        ("channel_indices", "tuple[int, ...]"),
        ("masks", "tuple[MaskSelection, ...]"),
        ("planes", "tuple[PlaneSelection, ...]"),
    ],
    MaskSelection: [
        ("mask_id", "str"),
        ("source_type", "str"),
        ("representation", "str"),
        ("role", "str"),
        ("check_status", "str"),
        ("conversion", "str | None"),
    ],
    PlaneSelection: [("t", "int"), ("z", "tuple[int, ...]")],
    Provenance: [
        ("resolver_version", "int"),
        ("description_sha256", "str"),
        ("section_digests", "tuple[tuple[str, str], ...]"),
        ("request", "Request"),
    ],
    Refusal: [
        ("code", "str"),
        ("subject", "str"),
        ("message", "str"),
        ("files", "tuple[str, ...]"),
    ],
    RepeatCoverage: [
        ("repeat_group", "str"),
        ("subject", "str"),
        ("status", "str"),
        ("present", "int"),
        ("total", "int"),
    ],
    CleanRun: [
        ("experiment", "Path"),
        ("output", "Path"),
        ("masks", "Path | None"),
        ("result", "RunResult"),
        ("description", "Description"),
    ],
}

HELPERS = {
    make_described_run: (
        f"(folder: '{PATH}', description: 'Description | bytes | str', *, "
        "masks: 'tuple[str, ...]' = (), mask_images: 'Iterable[str] | None' = None, "
        "mask_step: 'bool' = True) -> 'CleanRun'"
    ),
    make_clean_run: (
        f"(folder: '{PATH}', *, axes: 'str' = 'CYX', shape: 'tuple[int, ...]' = (2, 16, 20), "
        "mask_types: 'tuple[str, ...]' = (), pixel_size: 'float | None' = 0.5, "
        "design: 'bool' = False, seed: 'int' = 0, "
        "description: 'Description | bytes | str | None' = None) -> 'CleanRun'"
    ),
    described_images: "(description: 'Description') -> 'tuple[str, ...]'",
}


def test_run_inputs_has_exactly_the_public_methods_bricks_use():
    public = {name for name in dir(RunInputs) if not name.startswith("_")}
    fields = {f.name for f in dataclasses.fields(RunInputs)}
    assert public - fields == set(RUN_INPUTS_METHODS) | RUN_INPUTS_PROPERTIES
    for name in RUN_INPUTS_PROPERTIES:
        assert isinstance(inspect.getattr_static(RunInputs, name), property), name
    assert isinstance(inspect.getattr_static(RunInputs, "open"), classmethod)


@pytest.mark.parametrize("name", sorted(RUN_INPUTS_METHODS))
def test_run_inputs_method_signatures_are_pinned(name):
    assert str(inspect.signature(getattr(RunInputs, name))) == RUN_INPUTS_METHODS[name]


def test_the_run_wide_image_type_is_gone():
    # PP-1: the image type is each unit's declaration, on Fov and FovSelection only.
    assert not hasattr(RunInputs, "image_type")
    assert IMAGE_TYPES == ("channels", "rgb")


@pytest.mark.parametrize("cls", list(FIELDS), ids=lambda cls: cls.__name__)
def test_record_fields_and_types_are_pinned(cls):
    assert [(f.name, f.type) for f in dataclasses.fields(cls)] == FIELDS[cls]
    assert cls.__dataclass_params__.frozen


@pytest.mark.parametrize("helper", list(HELPERS), ids=lambda helper: helper.__name__)
def test_test_run_helper_signatures_are_pinned(helper):
    assert str(inspect.signature(helper)) == HELPERS[helper]


def test_the_resolver_names_are_exported_and_versioned():
    assert package.RESOLVER_VERSION == 2  # 2: parent-label masks (Spec ADR 0002)
    assert package.PARENT_LABELS_AS_AREA == "parent_labels_as_area"
    assert COVERAGE == ("full", "partial", "unavailable")
    for cls in [*FIELDS, Fov]:
        if cls is not CleanRun:
            assert getattr(package, cls.__name__) is cls, cls.__name__
    for name in (
        "ChannelNotFoundError",
        "DescriptionRequiredError",
        "MaskNotInstancesError",
        "MaskUncheckedError",
        "RgbNotSupportedError",
        "VolumeNotSupportedError",
        "RunInputsError",
    ):
        assert name in package.__all__, name
