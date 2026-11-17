"""The opt-in H&E tissue mask helper (Spec ADR 0005 BM15; design ``docs/design/he-tissue-mask.md``).

``RunInputs.he_tissue_mask(fov, output_mask_id=...)`` writes one ``uint8`` region mask of one RGB
position: ``1`` where the fixed rule (:mod:`.he_tissue_rule`, ``he_tissue_v1``, **provisional**)
keeps the pixel, ``0`` where it leaves out nuclei, red blood cells or empty space.
``RunInputs.preview_he_tissue_mask(fov)`` computes the same in memory and writes nothing. Both
are reached through :class:`.run_inputs_tissue.TissueMaskInputs`, which imports this module only
when called: scikit-image, normalization's ``stain`` module and this module stay out of a core
import.

**The FOV is the run's.** Only the image path is taken from the argument; the position is the
:class:`.run_inputs.Fov` of ``RunInputs.fovs()`` with that path, and every check and lineage
value use it. A caller's ``image_type``, axes, counts or dtype are ignored. Before any pixel is
read, the helper refuses (``ValueError`` naming the position) an image that is not the run's, a
unit not declared ``rgb``, more than one Z plane or time point, a pixel type other than
``uint8`` or a C count other than 3, a run without a masks folder, an unknown rule, a missing
scikit-image (``ImportError`` naming the ``he-mask`` extra), and an output mask ID that breaks
experiment-v1 3.2, is declared, differs only in case from a declared mask ID or source type,
names a type folder holding files this helper did not write, or names an existing file whose
lineage line is not this same computation as far as it can be told without pixels.

**Colour maths: normalization's, never reimplemented.** ``E`` and ``H`` are
``stain.to_scalar(rgb, axes="CYX", preset="he_eosin")``'s ``image`` and ``exclusion``; the
per-channel optical density is ``optical_density(rgb_yxc(rgb, "CYX"), check_white((255, 255,
255)))``, unpacked as ``(od, od_floored)``. The HED matrix is only looked up by the preset's own
name for it, to hash it into the rule record.

**Output and publication.** The file goes to ``<masks folder>/<output mask ID>/<the image's
folders>/<position ID>.tif`` with its line of ``mask_lineage.jsonl`` (the BM15 schema,
:mod:`.lineage_schema`), published by :func:`.mask_lineage.publish_output`. An all-zero mask is
written as computed (a valid empty region). The mask is a **region**: declare it
``representation = "binary"``, run preprocessing and the masks step again, and request it as a
region; in memory, never pass it as cell-mode labels (it would be one object, label 1).

**Retry (BM15's own comparator).** An existing output is the same computation, and the call is
a no-op returning it, only when both lines are ``reusable`` (both code identities verified),
they are equal in every key except ``output.content_sha256`` and ``fractions``, and the file
still has its digest. Anything else is refused, never overwritten: another source, code
identity, library version or rule record (a provisional and a frozen record included), and
every unverified line. Freezing a rule therefore needs a **new output mask ID**.
"""

from __future__ import annotations

import functools
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType, ModuleType
from typing import TYPE_CHECKING, Any

import numpy as np
import scipy
from morphobricks_normalization import stain
from morphobricks_normalization.optical_density import check_white, optical_density
from morphobricks_normalization.scalar_inputs import rgb_yxc

from .code_identity import code_identity
from .he_tissue_rule import (
    HE_TISSUE_V1,
    OPERATION,
    PROVISIONAL,
    RULES,
    TissueComponents,
    rule_record,
    tissue_components,
)
from .image_names import position_id
from .lineage_schema import BM15_LINE_KEYS, CODE_KEYS, EXPERIMENT_ROOT, rule_sha256
from .mask_conversion import _check_case_collision, _check_output_id, _digest_of
from .mask_lineage import (
    LineageLine,
    content_sha256,
    file_record,
    little_endian,
    publish_output,
    published_lines,
    roll_back_pending,
)
from .run_inputs_error import RunInputsError

if TYPE_CHECKING:
    from .run_inputs import Fov, RunInputs

INSTALL_HINT = 'pip install "morphobricks-preprocessing[he-mask]"'
"""The command that installs what the helper needs (scikit-image, through normalization)."""
HELPER = "morphobricks_preprocessing.RunInputs.he_tissue_mask"
PRESET = "he_eosin"
PRESET_VERSION = 1
WHITE = (255, 255, 255)
SOURCE_AXES = "CYX"
OUTPUT_AXES = "YX"
DISTRIBUTIONS = {"preprocessing": "morphobricks-preprocessing", "normalization": (
    "morphobricks-normalization"
)}  # fmt: skip
REGION_ONLY = (
    "Use this mask as a region (declare it binary); in memory, never pass it as cell-mode labels."
)
_PRE_READ_IGNORED = {"source": ("shape", "content_sha256"), "output": ("shape", "content_sha256")}
"""What only the pixels decide: compared after the read, by :func:`same_computation`."""


@dataclass(frozen=True)
class TissueMaskResult:
    """What one call of ``RunInputs.he_tissue_mask`` wrote or found already written.

    Attributes
    ----------
    image
        The position's image, relative to the experiment folder.
    mask_id
        The output mask ID (declare it ``representation = "binary"``).
    path
        The output file, relative to the masks folder, with ``/``.
    line
        Its line of ``mask_lineage.jsonl``, as stored (with the newline).
    published
        True when this call wrote it; False for an idempotent retry.
    reusable
        False when a code identity is unverified: the mask is written but never reused.
    fractions
        The kept share, each component's share of all pixels, and ``od_floored``.
    """

    image: str
    mask_id: str
    path: str
    line: str
    published: bool
    reusable: bool
    fractions: Mapping[str, float]

    @property
    def record(self) -> dict:
        """The lineage line as a new dict (its keys in the stored order)."""
        return json.loads(self.line)


@dataclass(frozen=True)
class TissueMaskPreview:
    """The tissue mask of one position computed in memory (nothing written).

    Attributes
    ----------
    image
        The position's image, relative to the experiment folder.
    rgb
        The plane read, ``uint8`` ``(3, Y, X)`` R, G, B.
    E, H
        normalization's eosin and hematoxylin maps (``to_scalar(..., preset="he_eosin")``).
    od, od_floored
        normalization's optical density ``(Y, X, 3)`` and its floored fraction.
    components
        The excluded components after clean-up and ``keep``.
    fractions
        As :attr:`TissueMaskResult.fractions`, from the full-resolution mask.
    rule
        The rule's canonical record (``status`` ``"provisional"`` for ``he_tissue_v1``).
    code
        The two code identities; ``reusable`` is True only when both are verified.
    """

    image: str
    rgb: np.ndarray
    E: np.ndarray
    H: np.ndarray
    od: np.ndarray
    od_floored: float
    components: TissueComponents
    fractions: Mapping[str, float]
    rule: Mapping[str, Any]
    code: Mapping[str, Mapping[str, object]]

    @property
    def reusable(self) -> bool:
        """True when both code identities are verified (the mask would be reused on retry)."""
        return all(self.code[name]["verified"] for name in CODE_KEYS)

    @property
    def provisional(self) -> bool:
        """True while the rule's status is ``"provisional"``."""
        return self.rule["status"] == PROVISIONAL


@dataclass(frozen=True)
class _Computed:
    """One position read and computed: everything a lineage record needs."""

    fov: Fov
    pixels: np.ndarray
    preview: TissueMaskPreview


def skimage_module() -> ModuleType:
    """Return scikit-image, or refuse naming the extra that installs it.

    Raises
    ------
    ImportError
        Naming ``pip install "morphobricks-preprocessing[he-mask]"``.
    """
    try:
        import skimage
        import skimage.color
    except ImportError as exc:
        raise ImportError(
            f"The H&E tissue mask needs scikit-image, which is not installed. Install it with "
            f"{INSTALL_HINT}"
        ) from exc
    return skimage


@functools.cache
def hed_matrix() -> tuple[str, str]:
    """Return the HED matrix's qualified name and SHA-256 (float64, little-endian, C order).

    The matrix is looked up only by the name normalization's ``he_eosin`` preset gives it.
    """
    name = stain.PRESETS[PRESET].matrix
    matrix = getattr(skimage_module().color, name)
    data = np.ascontiguousarray(matrix, dtype="<f8").tobytes()
    return f"skimage.color.{name}", hashlib.sha256(data).hexdigest()


@functools.cache
def code_identities() -> Mapping[str, Mapping[str, object]]:
    """Return the code identities of preprocessing and normalization, checked once per process.

    It is called after every module the helper uses is imported (this module's imports).
    """
    return MappingProxyType(
        {key: MappingProxyType(code_identity(name)) for key, name in DISTRIBUTIONS.items()}
    )


def current_rule(rule: str) -> dict:
    """Return a known rule's canonical record, refusing an unknown rule before scikit-image.

    Raises
    ------
    ValueError
        For an unknown rule ID (listing the known ones), or a normalization whose ``he_eosin``
        preset is not the version the rule was defined on.
    ImportError
        When scikit-image is missing (:func:`skimage_module`).
    """
    if rule not in RULES:
        raise ValueError(
            f"The rule {rule!r} is not known; the known rules are {', '.join(sorted(RULES))}"
        )
    skimage_module()  # refused here, before the output checks and any read
    found = stain.PRESETS[PRESET].version
    if found != PRESET_VERSION:
        raise ValueError(
            f"The installed normalization's {PRESET} preset is version {found}; {rule} is "
            f"defined on version {PRESET_VERSION}"
        )
    source, digest = hed_matrix()
    return rule_record(rule, hed_source=source, hed_sha256=digest)


def resolved_fov(run: RunInputs, fov: object) -> Fov:
    """Return the run's own position for an image path, refusing one this helper cannot take.

    Only the path is taken from ``fov`` (a ``Fov``'s ``relative_path``, or the text); its other
    attributes are ignored. Nothing is read.

    Raises
    ------
    RunInputsError
        An image that is not the run's.
    ValueError
        A unit not declared ``rgb``, a Z or T stack, a pixel type other than ``uint8``, or a C
        count other than 3.
    """
    relative = fov if isinstance(fov, str) else getattr(fov, "relative_path", None)
    if not isinstance(relative, str):
        raise ValueError("The position must be a Fov of the run or its image path (text)")
    found = {item.relative_path: item for item in run.fovs()}.get(relative)
    if found is None:
        raise RunInputsError(f"{relative} is not an image of this run.")
    if found.image_type != "rgb":
        raise ValueError(
            f"{relative} is declared {found.image_type}; the H&E tissue mask takes a unit declared "
            'image_type = "rgb"'
        )
    if found.z_count > 1 or found.t_count > 1:
        raise ValueError(
            f"{relative} has {found.z_count} Z planes and {found.t_count} time points; the H&E "
            "tissue mask takes one 2-D plane"
        )
    if found.dtype != "uint8" or found.c_count != 3:
        raise ValueError(
            f"{relative} has the pixel type {found.dtype} and {found.c_count} channels; the H&E "
            "tissue mask takes 8-bit RGB (uint8, three channels R, G, B)"
        )
    return found


def preview(run: RunInputs, fov: object, *, rule: str = HE_TISSUE_V1) -> TissueMaskPreview:
    """Compute one position's tissue mask in memory (``RunInputs.preview_he_tissue_mask``)."""
    found = resolved_fov(run, fov)
    record = current_rule(rule)
    return _compute(run, found, record).preview


def he_tissue_mask(
    run: RunInputs, fov: object, *, output_mask_id: str, rule: str = HE_TISSUE_V1
) -> TissueMaskResult:
    """Write one position's tissue mask with its lineage line (``RunInputs.he_tissue_mask``).

    Raises
    ------
    ValueError
        Every refusal of the module docstring, before any pixel is read, and a collision found
        once the pixels are known (nothing is written then).
    ImportError
        When scikit-image is missing.
    RunInputsError
        An image that is not the run's, or that changed since the run or while it was read.
    """
    found = resolved_fov(run, fov)
    if run.masks_root is None:
        raise ValueError(
            f"{found.relative_path}: the run has no masks folder; the H&E tissue mask is written "
            "there only. Run preprocessing with a masks folder first"
        )
    record = current_rule(rule)
    relative = _output_target(run, found.relative_path, output_mask_id)
    planned = _planned(run, found, relative, output_mask_id, record)
    existing = _existing_line(run, relative, planned, before_read=True)
    computed = _compute(run, found, record)
    line = _lineage_record(planned, computed)
    if existing is not None:
        if same_computation(existing.record, line) and _digest_of(
            run.masks_root / relative, existing.record
        ):
            return _result(found.relative_path, existing.text, published=False)
        raise _collision(relative, _differences(existing.record, line))
    output = computed.preview.components.keep.astype(np.uint8)
    roll_back_pending(run.masks_root, relative)
    publish_output(run.masks_root, relative, output, line)
    text = next(
        item.text
        for item in published_lines(run.masks_root, run.masks_root / output_mask_id)
        if item.output_path == relative
    )
    return _result(found.relative_path, text, published=True)


def same_computation(existing: Mapping[str, Any], wanted: Mapping[str, Any]) -> bool:
    """Return True when two BM15 lines record the same computation (BM15's own comparator).

    Both must be ``reusable``, and equal in every key except ``output.content_sha256`` and
    ``fractions``; the caller checks the existing file's digest.
    """
    return not _differences(existing, wanted)


def _differences(
    existing: Mapping[str, Any], wanted: Mapping[str, Any], ignored: Mapping = MappingProxyType({})
) -> list[str]:
    """Return what keeps two lines from being the same computation (empty when nothing)."""
    found = []
    if existing.get("reusable") is not True or wanted.get("reusable") is not True:
        found.append("an unverified code identity (never reused)")
    skipped = {"output": ("content_sha256",), **ignored}
    for key in BM15_LINE_KEYS:
        if key == "fractions" or key == "reusable":
            continue
        left, right = existing.get(key), wanted.get(key)
        if key in skipped and isinstance(left, dict) and isinstance(right, dict):
            left = {k: v for k, v in left.items() if k not in skipped[key]}
            right = {k: v for k, v in right.items() if k not in skipped[key]}
        if left != right:
            found.append(key)
    return found


def _collision(relative: str, differences: list[str]) -> ValueError:
    """Return the refusal of an existing output that is not this computation's."""
    text = (
        f"{relative} exists in the masks folder and is not this computation's output (it "
        f"differs in: {', '.join(differences)}); nothing is overwritten. Choose another output "
        "mask ID"
    )
    if "rule" in differences or "rule_sha256" in differences:
        text += (
            ": a rule with another record (a frozen rule included) makes a new mask under a new "
            "output mask ID, declared binary, with the masks step run again"
        )
    return ValueError(text)


def _output_target(run: RunInputs, image: str, output_id: str) -> str:
    """Return the output's path after refusing an invalid or colliding output mask ID."""
    _check_output_id(output_id, "output mask ID")
    declared = run.description().masks
    if output_id in declared:
        raise ValueError(
            f"The output mask ID {output_id!r} is declared in the description already; choose "
            "another ID: an output never replaces a declared mask"
        )
    _check_case_collision(output_id, declared)
    lines = published_lines(run.masks_root, run.masks_root / output_id)
    written = {line.output_path for line in lines if line.record["operation"] == OPERATION}
    foreign = [path for path in run._type_files(output_id) if path not in written]
    if foreign:
        raise ValueError(
            f"The masks folder holds masks of the type {output_id!r} that this helper did not "
            f"write ({', '.join(foreign)}); choose another output mask ID: nothing is overwritten"
        )
    parent, _, name = image.rpartition("/")
    return "/".join(part for part in (output_id, parent, position_id(name) + ".tif") if part)


def _existing_line(
    run: RunInputs, relative: str, planned: Mapping[str, Any], *, before_read: bool
) -> LineageLine | None:
    """Return the published line of an existing output, refusing one that cannot be reused.

    Before the read only what is known without pixels is compared (the digests and shapes are
    compared after it).
    """
    target = run.masks_root / relative
    if not target.exists():
        return None
    folder = run.masks_root / relative.split("/", 1)[0]
    lines = [
        line
        for line in published_lines(run.masks_root, folder)
        if line.output_path == relative and line.record["operation"] == OPERATION
    ]
    if not lines:
        raise _collision(relative, ["no lineage line of this helper names it"])
    ignored = _PRE_READ_IGNORED if before_read else {}
    differences = _differences(lines[-1].record, planned, ignored)
    if differences:
        raise _collision(relative, differences)
    return lines[-1]


def _compute(run: RunInputs, found: Fov, record: Mapping[str, Any]) -> _Computed:
    """Read the plane through the run and compute the rule on it."""
    skimage_module()
    pixels = run.read_plane(found.relative_path)
    if not run._unchanged(run._entry(found.relative_path)):
        raise RunInputsError(
            f"{found.relative_path} changed while it was read; nothing is written."
        )
    scalar = stain.to_scalar(pixels, axes=SOURCE_AXES, preset=PRESET)
    od, od_floored = _optical_density(pixels)
    components = tissue_components(scalar.image, scalar.exclusion, od, record)
    fractions = {**components.fractions(), "od_floored": od_floored}
    result = TissueMaskPreview(
        image=found.relative_path,
        rgb=pixels,
        E=scalar.image,
        H=scalar.exclusion,
        od=od,
        od_floored=od_floored,
        components=components,
        fractions=MappingProxyType(fractions),
        rule=MappingProxyType(record),
        code=code_identities(),
    )
    return _Computed(found, pixels, result)


def _optical_density(pixels: np.ndarray) -> tuple[np.ndarray, float]:
    """Return normalization's ``(od, floored_fraction)``, refusing any other result (a bug).

    Raises
    ------
    TypeError
        When the result is not a 2-tuple of a float64 ``(Y, X, 3)`` array and a float.
    """
    rgb = rgb_yxc(pixels, SOURCE_AXES)
    result = optical_density(rgb, check_white(WHITE))
    if not isinstance(result, tuple) or len(result) != 2:
        raise TypeError("optical_density() did not return the pair (od, floored_fraction)")
    od, floored = result
    if (
        not isinstance(od, np.ndarray)
        or od.dtype != np.float64
        or od.shape != rgb.shape
        or not isinstance(floored, float)
    ):
        raise TypeError("optical_density() did not return a float64 (Y, X, 3) array and a float")
    return od, floored


def _override(run: RunInputs, found: Fov) -> dict[str, object]:
    """Return the unit's declared RGB header override and this image's recorded item."""
    recorded = run.manifest.experiment_description or {}
    for unit in recorded.get("units", ()):
        key = (unit.get("repeat"), unit.get("condition"), unit.get("technical_repeat"))
        if key == (found.repeat_group, found.condition_id, found.technical_repeat_id):
            items = unit.get("rgb_header_override_images", ())
            image = next((i for i in items if i.get("image") == found.relative_path), None)
            return {"declared": unit.get("rgb_header_override") is True, "image": image}
    raise RunInputsError(f"The run records no unit for {found.relative_path}. Run it again.")


def _pixel_size(run: RunInputs, image: str) -> dict | None:
    """Return the recorded X and Y sizes (information only), or None when neither has one."""
    sizes = run.pixel_sizes(image)
    found = {role: sizes.get(role) for role in ("X", "Y")}
    if all(size is None or size.value is None for size in found.values()):
        return None
    return {
        role: None if size is None else {k: v for k, v in size.to_dict().items() if k != "role"}
        for role, size in found.items()
    }


def _planned(
    run: RunInputs, found: Fov, relative: str, output_id: str, record: Mapping[str, Any]
) -> dict:
    """Return this call's lineage record without what only the pixels give (pre-read check)."""
    from . import __version__

    code = code_identities()
    values = {
        "source": {"root": EXPERIMENT_ROOT, "image": found.relative_path, "axes": SOURCE_AXES},
        "output": {"root": "masks", "mask_id": output_id, "path": relative, "axes": OUTPUT_AXES},
        "operation": OPERATION,
        "rule": record,
        "rule_sha256": rule_sha256(record),
        "code": {key: dict(code[key]) for key in CODE_KEYS},
        "reusable": all(code[key]["verified"] for key in CODE_KEYS),
        "numpy_version": np.__version__,
        "scikit_image_version": skimage_module().__version__,
        "scipy_version": scipy.__version__,
        "image_type": found.image_type,
        "rgb_header_override": _override(run, found),
        "pixel_size": _pixel_size(run, found.relative_path),
        "fractions": None,
        "helper": HELPER,
        "helper_version": __version__,
    }
    for side in ("source", "output"):
        values[side]["dtype"] = little_endian(np.uint8)
    return values


def _lineage_record(planned: Mapping[str, Any], computed: _Computed) -> dict:
    """Return the complete lineage record of one output, its keys in the BM15 order."""
    pixels = computed.pixels
    keep = computed.preview.components.keep.astype(np.uint8)
    output = planned["output"]
    found = {
        **planned,
        "source": {
            "root": EXPERIMENT_ROOT,
            "image": computed.preview.image,
            "axes": SOURCE_AXES,
            "shape": [int(n) for n in pixels.shape],
            "dtype": little_endian(pixels.dtype),
            "content_sha256": content_sha256(pixels, axes=SOURCE_AXES),
        },
        "output": file_record(output["mask_id"], output["path"], OUTPUT_AXES, keep),
        "fractions": dict(computed.preview.fractions),
    }
    return {key: found[key] for key in BM15_LINE_KEYS}


def _result(image: str, text: str, *, published: bool) -> TissueMaskResult:
    """Return the result of one call from its lineage line as stored."""
    record = json.loads(text)
    return TissueMaskResult(
        image=image,
        mask_id=record["output"]["mask_id"],
        path=record["output"]["path"],
        line=text,
        published=published,
        reusable=record["reusable"],
        fractions=MappingProxyType(record["fractions"]),
    )
