"""Reading the published description as a consumer (experiment-v1 sections 10 and 12).

A consumer reads only the published ``morphobricks.toml``. A draft (``morphobricks.draft.json``,
a reconciliation proposal, anything not named ``morphobricks.toml``) and a file with an
unconfirmed repeat are refused with ``A_DRAFT_REFUSED``; nothing is read from them.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from .description import Description, DescriptionError, description_from_bytes
from .description_file import DESCRIPTION_FILENAME, read_file_bytes, sha256_hex
from .description_rules import Code
from .run_inputs_error import DraftRefusedError, RunInputsError


@lru_cache(maxsize=8)
def _parsed(data: bytes) -> Description:
    """Return the model of valid bytes (cached by content: a read is then a read and a hash)."""
    return description_from_bytes(data)


def read_published_description(path: str | os.PathLike) -> tuple[Description, str]:
    """Read the published description at ``path``; return it and the SHA-256 of its bytes.

    Raises
    ------
    DraftRefusedError
        ``A_DRAFT_REFUSED``: the file is not named ``morphobricks.toml`` (a draft or proposal)
        or holds a repeat that is not confirmed.
    RunInputsError
        When the file is missing, a symbolic link, or not a valid description.
    """
    path = Path(path)
    if path.name != DESCRIPTION_FILENAME:
        raise DraftRefusedError(
            f"{path.name} is not the published {DESCRIPTION_FILENAME}: a draft or proposal "
            "is never read (A_DRAFT_REFUSED). Publish the description first."
        )
    try:
        data = read_file_bytes(path)
        if data is None:
            raise RunInputsError(f"{path} is gone: the run recorded a description.")
        return _parsed(data), sha256_hex(data)
    except DescriptionError as exc:
        codes = sorted({issue.code.value for issue in exc.issues})
        if any(issue.code is Code.E_REPEAT_UNCONFIRMED for issue in exc.issues):
            raise DraftRefusedError(
                f"{path.name} has a repeat that is not confirmed, so it is a draft "
                "(A_DRAFT_REFUSED). Confirm and publish it first."
            ) from exc
        raise RunInputsError(
            f"{path.name} is not a valid experiment description ({', '.join(codes)})."
        ) from exc
    except OSError as exc:
        raise RunInputsError(f"{path.name} cannot be read: {exc}") from exc
