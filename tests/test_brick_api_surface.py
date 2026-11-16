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
    MaskConversion,
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
    "plane_shape": "(self, fov: 'Fov | str') -> 'tuple[int, int]'",
    "read_window": (
        "(self, fov: 'Fov | str', *, t: 'int', z: 'int', "
        "bounds: 'tuple[int, int, int, int]', channels: 'Sequence[str]', level: 'int' = 0, "
        "memory_limit: 'int' = 1073741824) -> 'np.ndarray'"
    ),
    "window_reader": (
        "(self, fov: 'Fov | str', *, cache_bytes: 'int', "
        "memory_limit: 'int' = 1073741824) -> 'RunWindowReader'"
    ),
    "mask_types": "(self) -> 'tuple[str, ...]'",
    "read_mask": (
        "(self, fov: 'Fov | str', mask_type: 'str', z: 'int | None' = None, "
        "t: 'int | None' = None) -> 'np.ndarray'"
    ),
    "read_mask_window": (
        "(self, fov: 'Fov | str', mask_type: 'str', *, t: 'int', z: 'int', "
        "bounds: 'tuple[int, int, int, int]', level: 'int' = 0, "
        "memory_limit: 'int' = 1073741824) -> 'np.ndarray'"
    ),
    "mask_window_plan": (
        "(self, fov: 'Fov | str', mask_type: 'str', *, "
        "bounds: 'tuple[int, int, int, int]', level: 'int' = 0, "
        "memory_limit: 'int' = 1073741824) -> 'WindowPlan'"
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
    "read_objects": (
        "(self, selection: 'FovSelection', mask_id: 'str', t: 'int', z: 'int') -> 'np.ndarray'"
    ),
    "binary_to_instances": (
        "(self, fov: 'Fov | str', mask_id: 'str', *, output_mask_id: 'str') "
        "-> 'MaskConversionResult'"
    ),
    "classes_to_binary": (
        "(self, fov: 'Fov | str', mask_id: 'str', *, output_prefix: 'str') "
        "-> 'MaskConversionResult'"
    ),
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
        ("mask_conversions", "tuple[MaskConversion, ...]"),
    ],
    MaskConversion: [
        ("mask_id", "str"),
        ("conversion", "str"),
        ("connectivity", "int | None"),
        ("labeller", "str | None"),
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
    assert package.RESOLVER_VERSION == 3  # 3: the conversion record (Spec ADR 0005)
    assert package.PARENT_LABELS_AS_AREA == "parent_labels_as_area"
    assert COVERAGE == ("full", "partial", "unavailable")
    for cls in [*FIELDS, Fov]:
        if cls is not CleanRun:
            assert getattr(package, cls.__name__) is cls, cls.__name__
    for name in (
        "ChannelNotFoundError",
        "DescriptionRequiredError",
        "MaskNotInstancesError",
        "MaskContradictsRepresentationError",
        "MaskUncheckedError",
        "RgbNotSupportedError",
        "VolumeNotSupportedError",
        "RunInputsError",
    ):
        assert name in package.__all__, name


ADR_0005_SIGNATURES = {
    "morphobricks_preprocessing.masks.binary.label_binary": (
        "(mask: 'np.ndarray', *, name: 'str' = 'the mask') -> 'np.ndarray'"
    ),
    "morphobricks_preprocessing.masks.check_object_set": (
        "(value: 'object', *, name: 'str') -> 'None'"
    ),
    "morphobricks_preprocessing.mask_rules.refuse_not_instances": (
        "(subject: 'str', *, declared: 'str | None' = None, value: 'object' = None) "
        "-> 'MaskNotInstancesError'"
    ),
    "morphobricks_preprocessing.mask_rules.refuse_binary_values": (
        "(path: 'str', *, values: 'Iterable[int] | None' = None, dtype: 'object' = None) "
        "-> 'MaskContradictsRepresentationError'"
    ),
    "morphobricks_preprocessing.mask_rules.check_binary_values": (
        "(values: 'Iterable[int]', *, dtype: 'object', path: 'str') -> 'None'"
    ),
    "morphobricks_preprocessing.mask_conversion.check_class_image": (
        "(pixels: 'np.ndarray', *, mask_id: 'str', path: 'str') -> 'None'"
    ),
    "morphobricks_preprocessing.mask_lineage.content_sha256": (
        "(pixels: 'np.ndarray', *, axes: 'str') -> 'str'"
    ),
    "morphobricks_preprocessing.run_inputs_request.volume_refusals": (
        "(request: 'Request', z_counts: 'Mapping[str, int]') -> 'tuple[Refusal, ...]'"
    ),
}


@pytest.mark.parametrize("dotted", sorted(ADR_0005_SIGNATURES))
def test_the_adr_0005_helpers_are_pinned(dotted):
    # Spec ADR 0005 step 2: the names other bricks import, with their signatures.
    import importlib

    module, name = dotted.rsplit(".", 1)
    function = getattr(importlib.import_module(module), name)
    assert str(inspect.signature(function)) == ADR_0005_SIGNATURES[dotted]


def test_the_adr_0005_names_are_exported():
    from morphobricks_preprocessing import masks
    from morphobricks_preprocessing.masks import binary

    assert package.label_binary is binary.label_binary and "label_binary" in package.__all__
    assert package.check_object_set is masks.check_object_set
    assert "check_object_set" in masks.__all__
    assert binary.CONNECTIVITY == 8
    assert binary.STRUCTURE.shape == (3, 3) and binary.STRUCTURE.all()
    assert issubclass(package.MaskContradictsRepresentationError, ValueError)
    assert package.MaskContradictsRepresentationError.code == "R_MASK_CONTRADICTS_REPRESENTATION"


def test_the_conversion_results_are_pinned():
    # Spec ADR 0005 step 2b (BM12): what the conversion helpers return.
    assert package.MaskConversionResult.__module__ == "morphobricks_preprocessing.mask_conversion"
    fields = [(f.name, f.type) for f in dataclasses.fields(package.MaskConversionResult)]
    assert fields == [
        ("operation", "str"),
        ("image", "str"),
        ("mask_id", "str"),
        ("source_path", "str"),
        ("representation", "str"),
        ("outputs", "tuple[ConvertedMask, ...]"),
        ("note", "str"),
    ]
    fields = [(f.name, f.type) for f in dataclasses.fields(package.ConvertedMask)]
    assert fields == [
        ("mask_id", "str"),
        ("path", "str"),
        ("class_value", "int | None"),
        ("line", "str"),
        ("published", "bool"),
    ]
    assert "MaskConversionResult" in package.__all__ and "ConvertedMask" in package.__all__
