"""The H&E tissue mask of an image array in memory, with no run (contract §15.4; roadmap K3).

``he_tissue_mask_array(rgb)`` computes the fixed rule ``he_tissue_v1`` (**provisional**) on one
``uint8`` ``(Y, X, 3)`` R, G, B array and returns the boolean mask (``True`` kept) with a small
immutable record of what computed it. It is for standalone users (a script, another brick's
folder command) who have pixels but no MorphoBricks run. The computation is the run route's own
(:func:`.he_tissue_mask.rule_on_plane`, called with the array moved to ``(3, Y, X)``), never a
copy, so the two masks are bitwise equal for the same pixels.

**Not a run artifact (Spec ADR 0005 BM15).** BM15 governs a derived region mask *written in a
run*: a file in the masks folder with its ``mask_lineage.jsonl`` line, declared by the user and
checked by the masks step. This function writes nothing, reads no run and makes no lineage line,
so nothing it returns can be declared, checked or reused as a run's mask. Its record carries the
parts of a BM15 line that describe the computation (the rule ID, version and ``status``, the
complete rule record and its ``rule_sha256``, BM15's code identities with ``verified`` as found,
the library versions and the ``fractions``), so a caller can record the provenance. The parts
that describe a run's source and output (``source``, ``output``, ``image_type``,
``rgb_header_override``, ``pixel_size``) have no meaning here and are absent. For MorphoBricks
runs the run route (``RunInputs.he_tissue_mask``, with lineage) remains the route.

**Refused before any computation**, with nothing converted and no axes guessed: an input that is
not a plain ``numpy.ndarray`` (``TypeError``), a pixel type other than ``uint8``, a shape other
than ``(Y, X, 3)`` with ``Y, X >= 1`` (``ValueError``), an unknown rule (``ValueError``), and a
missing scikit-image (``ImportError`` naming the ``he-mask`` extra). The rule takes nothing else
from the caller: it has no pixel size (sizes are pixels), its white is fixed at 255 and it has no
per-call thresholds, exactly as the run route. This module imports no scikit-image,
normalization ``stain`` module or helper module: they are imported only when the function is
called past the input checks.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np

HE_TISSUE_V1 = "he_tissue_v1"
"""The default rule, as in ``RunInputs.he_tissue_mask`` (provisional; :mod:`.he_tissue_rule`)."""
IN_MEMORY = "<in-memory array>"
"""The label of the plane in the shared computation: an array, not an image of a run."""
ARRAY_HELPER = "morphobricks_preprocessing.he_tissue_mask_array"
"""The function's qualified name, recorded as ``helper``."""
RECORD_KEYS = (
    "rule_id",
    "operation",
    "rule",
    "rule_sha256",
    "code",
    "code_verified",
    "numpy_version",
    "scikit_image_version",
    "scipy_version",
    "fractions",
    "helper",
    "helper_version",
)
"""The keys of :meth:`TissueMaskArrayRecord.to_dict`, in this order."""


@dataclass(frozen=True)
class TissueMaskArrayRecord:
    """What computed one in-memory H&E tissue mask: provenance, not a lineage line.

    Attributes
    ----------
    rule_id
        The rule asked for (``"he_tissue_v1"``).
    operation
        The rule's operation (``"he_tissue_mask"``), as in the run route's lineage.
    rule_text
        The complete canonical rule record as JSON, keys in the record's order: the record the
        run route writes in its lineage line (read it with :attr:`rule`).
    rule_sha256
        The SHA-256 of the record's canonical JSON, as the run route records it.
    code
        BM15's code identities of ``preprocessing`` and ``normalization`` (read-only), each with
        ``verified`` and ``reason`` as found; never assumed verified.
    code_verified
        True only when both identities are verified (the run route's ``reusable``).
    numpy_version, scikit_image_version, scipy_version
        Each package's ``__version__`` as imported by this process.
    fractions
        As the run route's lineage ``fractions``: the kept share, each excluded component's
        share of all pixels (``nuclei``, ``red_cells``, ``background``) and ``od_floored``.
    helper, helper_version
        ``morphobricks_preprocessing.he_tissue_mask_array`` and the package version.
    """

    rule_id: str
    operation: str
    rule_text: str
    rule_sha256: str
    code: Mapping[str, Mapping[str, object]]
    code_verified: bool
    numpy_version: str
    scikit_image_version: str
    scipy_version: str
    fractions: Mapping[str, float]
    helper: str
    helper_version: str

    @property
    def rule(self) -> dict:
        """The canonical rule record as a new dict (its keys in the record's order)."""
        return json.loads(self.rule_text)

    @property
    def rule_version(self) -> int:
        """The rule record's ``version`` (``1`` for ``he_tissue_v1``)."""
        return self.rule["version"]

    @property
    def status(self) -> str:
        """The rule record's ``status``: ``"provisional"`` for ``he_tissue_v1``."""
        return self.rule["status"]

    @property
    def provisional(self) -> bool:
        """True while the rule's status is ``"provisional"``."""
        return self.status == "provisional"

    def to_dict(self) -> dict[str, Any]:
        """Return the record as plain JSON-ready values, keys in :data:`RECORD_KEYS` order."""
        values = {
            **{key: getattr(self, key) for key in RECORD_KEYS},
            "rule": self.rule,
            "code": {name: dict(identity) for name, identity in self.code.items()},
            "fractions": dict(self.fractions),
        }
        return {key: values[key] for key in RECORD_KEYS}


@dataclass(frozen=True)
class TissueMaskArray:
    """An in-memory H&E tissue mask and its record.

    Attributes
    ----------
    mask
        Boolean ``(Y, X)``: ``True`` where the rule keeps the pixel. A region (Spec ADR 0005
        BM3), never an object set: never pass it as cell-mode labels.
    record
        What computed it (:class:`TissueMaskArrayRecord`).
    """

    mask: np.ndarray
    record: TissueMaskArrayRecord


def he_tissue_mask_array(rgb: np.ndarray, *, rule: str = HE_TISSUE_V1) -> TissueMaskArray:
    """Compute the H&E tissue mask of one ``uint8`` ``(Y, X, 3)`` R, G, B array in memory.

    The rule ``he_tissue_v1`` is **provisional** and fixed: it leaves out nuclei, red blood
    cells and empty space, sizes in pixels, colour maths from normalization. The computation is
    ``RunInputs.he_tissue_mask``'s own, so the mask is bitwise equal to the run route's for the
    same pixels. Nothing is written and no run is read; for MorphoBricks runs use the run route,
    which writes the mask with its lineage.

    Parameters
    ----------
    rgb
        A plain ``numpy.ndarray`` of pixel type ``uint8`` and shape ``(Y, X, 3)``, the last axis
        R, G, B. Nothing is converted and no axis is guessed.
    rule
        The rule ID; ``"he_tissue_v1"`` is the only one.

    Returns
    -------
    TissueMaskArray
        The boolean ``(Y, X)`` mask (``True`` kept) and its :class:`TissueMaskArrayRecord`.

    Raises
    ------
    TypeError
        When ``rgb`` is not a plain ``numpy.ndarray`` (a subclass, a list, ``None``).
    ValueError
        A pixel type other than ``uint8``, a shape other than ``(Y, X, 3)`` with ``Y, X >= 1``,
        or an unknown rule; all before any computation.
    ImportError
        scikit-image is missing: ``pip install "morphobricks-preprocessing[he-mask]"``.
    """
    check_rgb_array(rgb)
    return _computed(rgb, rule)


def check_rgb_array(rgb: object) -> None:
    """Refuse an input that is not a plain ``uint8`` ``(Y, X, 3)`` array (nothing is computed).

    Raises
    ------
    TypeError
        When ``rgb`` is not a plain ``numpy.ndarray``.
    ValueError
        For another pixel type, or a shape other than ``(Y, X, 3)`` with ``Y, X >= 1``.
    """
    if type(rgb) is not np.ndarray:
        raise TypeError(
            f"The H&E tissue mask takes a plain numpy.ndarray; got {type(rgb).__name__}. Pass "
            "the image's pixels as a uint8 (Y, X, 3) R, G, B array"
        )
    if rgb.dtype != np.uint8:
        raise ValueError(
            f"The H&E tissue mask takes 8-bit RGB (uint8); this array is {rgb.dtype}. Nothing "
            "is converted: pass the image's own uint8 pixels"
        )
    if rgb.ndim != 3 or rgb.shape[2] != 3 or rgb.shape[0] < 1 or rgb.shape[1] < 1:
        raise ValueError(
            f"The H&E tissue mask takes one (Y, X, 3) R, G, B plane; this array has the shape "
            f"{tuple(rgb.shape)}. No axis is guessed: move the colour axis last and pass one "
            "2-D plane"
        )


def _computed(rgb: np.ndarray, rule: str) -> TissueMaskArray:
    """Compute the rule on a checked array through the run route's own computation.

    The helper module, scikit-image and SciPy are imported here, after the input checks. The
    array is moved to the run route's ``(3, Y, X)`` and given to
    :func:`.he_tissue_mask.rule_on_plane`; the record takes the run route's rule record, digest,
    code identities, library versions and fractions. An unknown rule (``ValueError``) and a
    missing scikit-image (``ImportError``) are refused by :func:`.he_tissue_mask.current_rule`
    before any computation.
    """
    import scipy

    from . import __version__
    from .he_tissue_mask import current_rule, rule_on_plane, skimage_module
    from .he_tissue_rule import OPERATION
    from .lineage_schema import CODE_KEYS, rule_sha256

    record = current_rule(rule)
    pixels = np.ascontiguousarray(np.moveaxis(rgb, -1, 0))
    computed = rule_on_plane(pixels, record, image=IN_MEMORY)
    code = computed.code
    found = TissueMaskArrayRecord(
        rule_id=rule,
        operation=OPERATION,
        rule_text=json.dumps(record),
        rule_sha256=rule_sha256(record),
        code=code,
        code_verified=all(code[key]["verified"] for key in CODE_KEYS),
        numpy_version=np.__version__,
        scikit_image_version=skimage_module().__version__,
        scipy_version=scipy.__version__,
        fractions=computed.fractions,
        helper=ARRAY_HELPER,
        helper_version=__version__,
    )
    return TissueMaskArray(mask=computed.components.keep, record=found)
