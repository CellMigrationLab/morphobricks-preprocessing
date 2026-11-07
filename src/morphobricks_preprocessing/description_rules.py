"""The one table of the experiment-description rules (experiment-v1 sections 3, 4 and 6).

Codes, field tables, the forbidden names and the fixed enumerations live here and nowhere
else in the reader (conformance plan section 1: no second copy of the rules).
``tests/test_spec_rules_table.py`` compares every table with the vendored normative text;
a rule that changes in the Spec changes here in the same pull request, with its fixtures.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


class Code(StrEnum):
    """Every error and warning code of experiment-v1 section 6.

    ``R_*`` and ``A_*`` are not the reader's: preprocessing's publication and the analysis
    layer own them.
    """

    E_FILE_ENCODING = "E_FILE_ENCODING"
    E_TOML_SYNTAX = "E_TOML_SYNTAX"
    E_DUPLICATE_KEY = "E_DUPLICATE_KEY"
    E_SCHEMA_ID = "E_SCHEMA_ID"
    E_SCHEMA_VERSION_MISSING = "E_SCHEMA_VERSION_MISSING"
    E_SCHEMA_VERSION_UNSUPPORTED = "E_SCHEMA_VERSION_UNSUPPORTED"
    E_TYPE = "E_TYPE"
    E_UNKNOWN_FIELD = "E_UNKNOWN_FIELD"
    E_FORBIDDEN_FIELD = "E_FORBIDDEN_FIELD"
    E_MISSING_FIELD = "E_MISSING_FIELD"
    E_EMPTY = "E_EMPTY"
    E_TEXT_EMPTY = "E_TEXT_EMPTY"
    E_ENUM = "E_ENUM"
    E_DATE_FORMAT = "E_DATE_FORMAT"
    E_VALUE_RANGE = "E_VALUE_RANGE"
    E_REPEAT_UNCONFIRMED = "E_REPEAT_UNCONFIRMED"
    E_ID_TEXT = "E_ID_TEXT"
    E_ID_SYNTAX = "E_ID_SYNTAX"
    E_ID_RESERVED = "E_ID_RESERVED"
    E_ID_NOT_NFC = "E_ID_NOT_NFC"
    E_ID_CASE_COLLISION = "E_ID_CASE_COLLISION"
    E_SINGLE_MISUSE = "E_SINGLE_MISUSE"
    E_UNKNOWN_CONDITION = "E_UNKNOWN_CONDITION"
    E_UNKNOWN_CHANNEL = "E_UNKNOWN_CHANNEL"
    E_DUPLICATE_CHANNEL_IN_UNIT = "E_DUPLICATE_CHANNEL_IN_UNIT"
    E_DUPLICATE_VALUE = "E_DUPLICATE_VALUE"
    E_RGB_UNIT_CHANNELS = "E_RGB_UNIT_CHANNELS"
    E_RGB_TOKEN_MISUSE = "E_RGB_TOKEN_MISUSE"
    E_RGB_ALIAS = "E_RGB_ALIAS"
    E_DISPLAY_NAME_COLLISION = "E_DISPLAY_NAME_COLLISION"
    E_USED_ID_MISSING = "E_USED_ID_MISSING"
    E_SOURCE_FOLDER_REUSED = "E_SOURCE_FOLDER_REUSED"
    E_SOURCE_FOLDER_CONFLICT = "E_SOURCE_FOLDER_CONFLICT"
    E_CONDITION_WITHOUT_MEMBERS = "E_CONDITION_WITHOUT_MEMBERS"
    E_SOURCE_REPEAT_REUSED = "E_SOURCE_REPEAT_REUSED"
    E_REPEAT_ID_SHADOWS_SOURCE = "E_REPEAT_ID_SHADOWS_SOURCE"
    E_MASK_SOURCE_TYPE_REUSED = "E_MASK_SOURCE_TYPE_REUSED"
    E_REFERENCE_UNKNOWN = "E_REFERENCE_UNKNOWN"
    E_REFERENCE_SELF = "E_REFERENCE_SELF"
    E_REFERENCE_CYCLE = "E_REFERENCE_CYCLE"
    W_UNUSED_CHANNEL = "W_UNUSED_CHANNEL"
    W_REPEAT_MISSING_CONDITIONS = "W_REPEAT_MISSING_CONDITIONS"
    W_REFERENCE_UNPAIRED = "W_REFERENCE_UNPAIRED"


@dataclass(frozen=True)
class TableSpec:
    """The closed set of fields of one table, in the order of the Spec's table.

    ``required`` names the ones that must be present.
    """

    fields: tuple[str, ...]
    required: frozenset[str]


SCHEMA_ID = "morphobricks-experiment"
SUPPORTED_VERSIONS = frozenset({1})
"""The schema versions this reader implements: a constant, checked against nothing fetched
(experiment-v1 section 7)."""

TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)*")
"""The Token rule (section 3.2): the regular expression a channel or mask ID must match whole."""
OBJECT_SET_PART = "in"
"""A mask ID with this ``_``-separated, case-folded part is reserved (the object-set rule)."""
RGB_TOKENS = ("R", "G", "B")
"""The fixed channel IDs of an RGB unit, in order (section 4.4b)."""

REPRESENTATIONS = ("instances", "binary")
IMAGE_TYPES = ("rgb",)

TOP = TableSpec(
    ("schema", "schema_version", "conditions", "channels", "repeats", "masks", "used_ids"),
    frozenset({"schema", "schema_version", "conditions", "channels", "repeats", "used_ids"}),
)
CONDITION = TableSpec(
    ("source_folder", "display_name", "reference", "target", "treatment_key"),
    frozenset({"source_folder"}),
)
CHANNEL = TableSpec(("display_name", "header_names"), frozenset())
REPEAT = TableSpec(
    ("display_name", "confirmed", "confirmed_on", "members"),
    frozenset({"confirmed", "confirmed_on", "members"}),
)
MEMBER = TableSpec(
    ("source_repeat", "image_count", "technical_repeats"),
    frozenset({"source_repeat", "image_count", "technical_repeats"}),
)
UNIT = TableSpec(
    ("source_folder", "display_name", "channels", "image_type"),
    frozenset({"source_folder", "channels"}),
)
MASK = TableSpec(
    ("source_type", "representation", "source_channels", "display_name"),
    frozenset({"source_type", "representation"}),
)
USED_IDS = TableSpec(
    ("conditions", "channels", "repeats", "technical_repeats"),
    frozenset({"conditions", "channels", "repeats", "technical_repeats"}),
)

FORBIDDEN = frozenset(
    {
        "axes", "shared", "plane_scope", "z", "projection",
        "pixel_size", "calibration",
        "fovs", "files", "paths", "overrides", "exclude", "fingerprints",
        "bricks", "routes", "features", "normalization", "statistics", "contrasts",
        "binary_mask_policy", "mask_conversion",
        "control", "roles", "experiment",
        "source_index", "channel_count",
        "retired_repeats", "replaced_by",
        "labchronicle", "provenance", "source_database",
    }
)  # fmt: skip
"""Field names and ID keys refused as ``E_FORBIDDEN_FIELD``, in any table, compared exactly
and case-sensitively (experiment-v1 section 4.7)."""

RESERVED = frozenset(
    {
        "whole", "cell", "nucleus", "cytoplasm", "ring", "expanded_nucleus",
        "condition", "biological_repeat", "repeat", "technical_repeat", "position_id",
        "image_file", "image_path", "mask_file", "patch_file", "patch_id", "label_id",
        "z", "z0", "z1", "c", "t",
    }
)  # fmt: skip
"""The one reserved list of channel IDs and mask IDs, compared case-folded (section 3.2)."""
