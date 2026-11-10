"""The run manifest version: one version is written and read (contract §9, ADR 0002 AR11).

A manifest's ``manifest_version`` says what its rows were checked against, so that a reader
that does not know a rule refuses the manifest rather than read it under a weaker one. Before
the first pipeline release there is no backward compatibility (Spec ADR 0002 AR10; owner ruling
16 of 10 November 2026): this package writes and reads version ``"4"`` only, a described run's
manifest (``experiment_description`` present). Content verification is
``settings.verify_content``, with every file's ``content_sha256`` exactly when it is true.

A manifest of any other version (``"1"`` before v0.4.0, ``"2"`` and ``"3"`` of v0.4.0 and the
undescribed runs of v0.5.0, or a later one) is refused with
:class:`UnsupportedVersionError`, which names the fix: run preprocessing again. Nothing is
converted. The writer and the reader apply the same rule (:func:`check_version`), so a written
manifest always reads back.
"""

from __future__ import annotations

from collections.abc import Iterable

from .description_record import KEY as DESCRIPTION_KEY
from .manifest_fields import recorded_flag

MANIFEST_VERSION = "4"
"""The version of every run manifest this package writes and reads (a described run)."""
CONTENT_KEY = "content_sha256"
"""The key of a source's content SHA-256 in a manifest entry (``verify_content`` runs)."""
READ_VERSIONS = (MANIFEST_VERSION,)
"""The versions this package reads: the current one only (owner ruling 16)."""
RERUN = "Run preprocessing again on the experiment (after describing it) to write a current one."
"""The fix an old manifest's refusal names."""


class UnsupportedVersionError(ValueError):
    """Raised for a ``manifest_version`` this package does not read: re-run preprocessing."""


def check_version(
    version: object,
    verify_content: bool,
    content: Iterable[tuple[str, str | None]],
    described: bool = True,
) -> None:
    """Refuse a manifest that is not a current one, or whose content hashes do not match.

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
        If the version is not :data:`MANIFEST_VERSION` (an older run, or a later package's):
        the message says to run preprocessing again.
    ValueError
        If the manifest carries no ``experiment_description`` (every run is described), or if
        its entries hold a ``content_sha256`` other than exactly when ``verify_content`` is true.
    """
    if version not in READ_VERSIONS:
        raise UnsupportedVersionError(
            f"Manifest version {version!r} is not supported: this version of preprocessing "
            f"reads version {MANIFEST_VERSION!r} only (older runs are not converted). {RERUN}"
        )
    if not described:
        raise ValueError(
            f"a version {version} manifest carries the run's experiment_description; this one "
            "lacks it"
        )
    content = list(content)
    stray = [path for path, sha in content if (sha is None) == bool(verify_content)]
    if stray:
        raise ValueError(
            f"a version {version} manifest holds a content_sha256 in every entry exactly "
            f"when verify_content is true; the entry of {stray[0]!r} does not"
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
        If the version is not the current one (checked first, before any other key is read);
        :meth:`.manifest.RunManifest.from_dict` reports it as a ``ManifestFormatError`` with
        the same message.
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
