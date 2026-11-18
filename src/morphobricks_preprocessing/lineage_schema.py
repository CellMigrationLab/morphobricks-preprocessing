"""The two schemas of a ``mask_lineage.jsonl`` line, chosen by ``operation`` (Spec ADR 0005).

A line is parsed by :func:`parse_line`, which refuses a key repeated in one JSON object at any
depth (``json.loads`` would keep the last one silently). Its ``operation`` then selects the
schema: ``binary_to_instances`` and ``classes_to_binary`` select BM12's (checked by
:func:`.mask_lineage.read_lineage`, keys :data:`.mask_lineage.LINE_KEYS`), a rule operation of
:data:`BM15_OPERATIONS` selects BM15's (:func:`check_bm15`, keys :data:`BM15_LINE_KEYS`). A line
that matches neither is refused, and so are BM12's keys under a BM15 operation and the reverse.
A ``root`` of ``"experiment"`` is valid only in a BM15 ``source``; every other ``root`` is
``"masks"``.

:func:`rule_sha256` is the SHA-256 of a rule record's canonical JSON (sorted keys, compact
separators, UTF-8), the digest BM15 lines carry and readers recompute.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

LINEAGE_ROOT = "masks"
"""The ``root`` of every output and of BM12 sources: the run's standalone masks folder."""
EXPERIMENT_ROOT = "experiment"
"""The ``root`` of a BM15 source: the experiment folder (Spec ADR 0005 choice 15, amended)."""
FILE_KEYS = ("root", "mask_id", "path", "axes", "shape", "dtype", "content_sha256")
"""The keys of a BM12 ``source`` and of every ``output``, in order."""
BM12_OPERATIONS = ("binary_to_instances", "classes_to_binary")
BM15_OPERATIONS = ("he_tissue_mask",)
"""The operations of the derived region masks (Spec ADR 0005 BM15), one per rule family."""
BM15_LINE_KEYS = (
    "source",
    "output",
    "operation",
    "rule",
    "rule_sha256",
    "code",
    "reusable",
    "numpy_version",
    "scikit_image_version",
    "scipy_version",
    "image_type",
    "rgb_header_override",
    "pixel_size",
    "fractions",
    "helper",
    "helper_version",
)
BM15_SOURCE_KEYS = ("root", "image", "axes", "shape", "dtype", "content_sha256")
CODE_KEYS = ("preprocessing", "normalization")
IDENTITY_KEYS = ("distribution", "version", "verified", "record_sha256", "vcs_commit", "reason")
"""The keys of one code identity (Spec ADR 0005 BM15 "Code identity"), in order."""
FRACTION_KEYS = ("kept", "nuclei", "red_cells", "background", "od_floored")
OVERRIDE_KEYS = ("declared", "image")


class DuplicateKeyError(ValueError):
    """A JSON object of a lineage line repeats a key."""


def parse_line(text: str) -> object:
    """Return a lineage line's JSON value, refusing a key repeated in any object.

    Raises
    ------
    DuplicateKeyError
        When one object holds a key twice (at any depth); it names the key.
    ValueError
        When the text is not JSON.
    """
    return json.loads(text, object_pairs_hook=_unique_pairs)


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict:
    """Build one JSON object, refusing a repeated key instead of keeping the last value."""
    found: dict = {}
    for key, value in pairs:
        if key in found:
            raise DuplicateKeyError(f"the key {key!r} appears twice in one object")
        found[key] = value
    return found


def canonical_rule_text(rule: Mapping[str, object]) -> str:
    """Return a rule record's canonical JSON: keys sorted, no spaces (what is hashed)."""
    return json.dumps(rule, sort_keys=True, separators=(",", ":"))


def rule_sha256(rule: Mapping[str, object]) -> str:
    """Return the lower-case hex SHA-256 of a rule record's canonical JSON (UTF-8)."""
    return hashlib.sha256(canonical_rule_text(rule).encode("utf-8")).hexdigest()


def check_bm15(record: Mapping[str, object], where: str) -> None:
    """Refuse a record that is not a complete BM15 lineage line.

    Parameters
    ----------
    record
        The parsed line; its ``operation`` is one of :data:`BM15_OPERATIONS`.
    where
        The file and line number, as the messages name them.

    Raises
    ------
    ValueError
        For other keys or another key order (BM12's ``connectivity`` or ``class_value``
        included), a ``source`` that is not an experiment image's record, an ``output`` that is
        not a masks-folder file record, a ``rule_sha256`` that is not the digest of ``rule``, or
        a ``code``, ``reusable``, ``rgb_header_override`` or ``fractions`` of another form.
    """
    if tuple(record) != BM15_LINE_KEYS:
        raise ValueError(f"{where} is not a mask lineage record")
    source, output = record["source"], record["output"]
    if not isinstance(source, dict) or tuple(source) != BM15_SOURCE_KEYS:
        raise ValueError(f"{where} has no complete source record")
    if source["root"] != EXPERIMENT_ROOT:
        raise ValueError(
            f"{where} has the source root {source['root']!r}; the source of a derived region "
            f"mask is an image of the experiment folder ({EXPERIMENT_ROOT!r})"
        )
    if not isinstance(output, dict) or tuple(output) != FILE_KEYS:
        raise ValueError(f"{where} has no complete output record")
    if output["root"] != LINEAGE_ROOT:
        raise ValueError(
            f"{where} has the output root {output['root']!r}; a lineage path is relative to the "
            f"masks folder ({LINEAGE_ROOT!r}) only"
        )
    rule = record["rule"]
    if not isinstance(rule, dict) or record["rule_sha256"] != rule_sha256(rule):
        raise ValueError(f"{where} has a rule_sha256 that is not the digest of its rule record")
    _check_code(record["code"], where)
    _check_rest(record, where)


def _check_code(code: object, where: str) -> None:
    """Refuse a ``code`` that is not two complete code identities."""
    if not isinstance(code, dict) or tuple(code) != CODE_KEYS:
        raise ValueError(f"{where} has no complete code record")
    for name in CODE_KEYS:
        item = code[name]
        if not isinstance(item, dict) or tuple(item) != IDENTITY_KEYS:
            raise ValueError(f"{where} has no complete code identity for {name}")
        if not isinstance(item["verified"], bool):
            raise ValueError(f"{where} has a code identity for {name} without verified status")


def _check_rest(record: Mapping[str, object], where: str) -> None:
    """Refuse ``reusable``, ``rgb_header_override`` or ``fractions`` of another form."""
    verified = all(record["code"][name]["verified"] for name in CODE_KEYS)
    if not isinstance(record["reusable"], bool) or record["reusable"] != verified:
        raise ValueError(f"{where} has reusable {record['reusable']!r} for its code identities")
    override = record["rgb_header_override"]
    if not isinstance(override, dict) or tuple(override) != OVERRIDE_KEYS:
        raise ValueError(f"{where} has no complete rgb_header_override record")
    fractions = record["fractions"]
    if not isinstance(fractions, dict) or tuple(fractions) != FRACTION_KEYS:
        raise ValueError(f"{where} has no complete fractions record")
