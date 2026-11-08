"""Manifest versions: which run manifests a reader may open (contract §9).

A manifest's ``manifest_version`` says what its rows were checked against, so that a reader
that does not know a rule refuses the manifest rather than read it under a weaker one:

- ``"1"`` (before v0.4.0): read for reporting only; no row is reused, ``RunInputs`` refuses it;
- ``"2"`` (v0.4.0): the default identity rule, each source's fingerprint;
- ``"3"`` (v0.4.0rc5): a ``verify_content`` run (strict reuse, :mod:`.source_content`). It is
  version 2 with ``settings.verify_content`` true and every file's ``content_sha256``. The
  version, not only those keys, marks it: readers of v0.4.0rc4 and earlier ignore keys they
  do not know, and refuse a version they do not know (``ManifestFormatError``), so they never
  open a strict run as a fingerprint-only one (release review of rc5).

- ``"4"`` (v0.5.0): a run with an ``experiment_description`` (Stage 2 design question 9). It is
  version 2 or 3 (content verification as ``settings.verify_content`` says) plus the
  description. A v0.4.0 reader skips keys it does not know, so it would open a described run
  and name channels by header and pair by folder name; it refuses version ``"4"`` instead.

A default run still writes version 2 (or 3), byte for byte as before. The writer and the reader
apply the same rule (:func:`check_version`), so a written manifest always reads back.
"""

from __future__ import annotations

from collections.abc import Iterable

from .description_record import KEY as DESCRIPTION_KEY
from .manifest_fields import recorded_flag

MANIFEST_VERSION = "2"
"""The version of a default run's manifest (fingerprint identity)."""
STRICT_MANIFEST_VERSION = "3"
"""The version of a ``verify_content`` run's manifest (content SHA-256 identity)."""
DESCRIBED_MANIFEST_VERSION = "4"
"""The version of a described run's manifest (``experiment_description`` present)."""
LEGACY_MANIFEST_VERSION = "1"
"""Manifests written before v0.4.0: read for reporting, never trusted (no row is reused)."""
CONTENT_KEY = "content_sha256"
"""The key of a source's content SHA-256 in a manifest entry (``verify_content`` runs)."""
READ_VERSIONS = (
    LEGACY_MANIFEST_VERSION,
    MANIFEST_VERSION,
    STRICT_MANIFEST_VERSION,
    DESCRIBED_MANIFEST_VERSION,
)
"""The versions this package reads."""


class UnsupportedVersionError(ValueError):
    """Raised for a ``manifest_version`` this package does not read (a later version's)."""


def version_for(verify_content: bool, described: bool = False) -> str:
    """Return the manifest version a run with these settings writes.

    Parameters
    ----------
    verify_content
        Whether the run verifies each source's content (:attr:`.manifest.RunSettings`).
    described
        Whether the run carries an ``experiment_description``.

    Returns
    -------
    str
        :data:`DESCRIBED_MANIFEST_VERSION` for a described run, else
        :data:`STRICT_MANIFEST_VERSION` for a strict run, else :data:`MANIFEST_VERSION`.
    """
    if described:
        return DESCRIBED_MANIFEST_VERSION
    return STRICT_MANIFEST_VERSION if verify_content else MANIFEST_VERSION


def check_version(
    version: object,
    verify_content: bool,
    content: Iterable[tuple[str, str | None]],
    described: bool = False,
) -> None:
    """Refuse a manifest whose version does not match its content verification.

    Parameters
    ----------
    version
        The manifest's ``manifest_version``.
    verify_content
        Its ``settings.verify_content``.
    content
        ``(relative path, content_sha256 or None)`` of each file entry.
    described
        Whether the manifest carries an ``experiment_description``.

    Raises
    ------
    UnsupportedVersionError
        If the version is not one of :data:`READ_VERSIONS`.
    ValueError
        If a version 3 manifest does
        not verify content or lacks a file's ``content_sha256``; or if an older version
        records content verification (a strict run must be version 3, which older
        readers refuse); or if version 4 and the description do not go together (a described
        run must be version 4, so that older readers refuse it, and version 4 needs one); or
        if a version 4 manifest's content hashes do not match its ``verify_content``.
    """
    if version not in READ_VERSIONS:
        raise UnsupportedVersionError(
            f"Manifest version {version!r} is not supported (this version reads "
            f"{', '.join(repr(v) for v in READ_VERSIONS)})."
        )
    content = list(content)
    if (version == DESCRIBED_MANIFEST_VERSION) != described:
        raise ValueError(
            f"a version {version} manifest "
            + ("carries" if described else "lacks")
            + " experiment_description; a described run writes version "
            f"{DESCRIBED_MANIFEST_VERSION}, and version {DESCRIBED_MANIFEST_VERSION} needs one"
        )
    hashed = [path for path, sha in content if sha is not None]
    unhashed = [path for path, sha in content if sha is None]
    if version == DESCRIBED_MANIFEST_VERSION:
        stray = unhashed if verify_content else hashed
        if stray:
            raise ValueError(
                f"a version {version} manifest holds a content_sha256 in every entry exactly "
                f"when verify_content is true; the entry of {stray[0]!r} does not"
            )
    elif version == STRICT_MANIFEST_VERSION:
        if not verify_content:
            raise ValueError(f"a version {version} manifest must have verify_content true")
        if unhashed:
            raise ValueError(f"the entry of {unhashed[0]!r} has no content_sha256 (version 3)")
    elif verify_content or hashed:
        raise ValueError(
            f"a version {version} manifest records content verification "
            f"({'verify_content' if verify_content else hashed[0]!r}); a run that verifies "
            f"content writes version {STRICT_MANIFEST_VERSION}"
        )


def recorded_version(data: dict) -> str:
    """Return the version of manifest data, checked against its content verification.

    Parameters
    ----------
    data
        The manifest's JSON data.

    Returns
    -------
    str
        Its ``manifest_version``.

    Raises
    ------
    UnsupportedVersionError
        If the version is not one this package reads (checked first);
        :meth:`.manifest.RunManifest.from_dict` reports it as a ``ManifestFormatError``
        with the same message.
    KeyError, TypeError, ValueError, AttributeError
        If a key is missing or malformed, or :func:`check_version` refuses it;
        :meth:`.manifest.RunManifest.from_dict` reports each as a ``ManifestFormatError``.
    """
    if data["manifest_version"] not in READ_VERSIONS:  # before any other key is read
        check_version(data["manifest_version"], False, ())
    files = data["files"]
    check_version(
        data["manifest_version"],
        recorded_flag(data["settings"].get("verify_content", False)),
        [(entry.get("relative_path"), entry.get(CONTENT_KEY)) for entry in files],
        data.get(DESCRIPTION_KEY) is not None,
    )
    return data["manifest_version"]
