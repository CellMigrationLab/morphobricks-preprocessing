"""The sources a run must be of, when it is made for masks segmented from a known generation.

v0.9.1 (external review bc of #582, B2): **Register the masks** in the combined notebook runs
preprocessing again so that the run records the masks cell 4 wrote. Those masks were segmented
from one exact generation of the images: the run cell 4 opened. A run that saw an image change
during the press (its content hashed again, its rows recomputed) used to publish the new image
with the old masks, since each run only checks that it is consistent with itself.

:class:`ExpectedSources` carries that generation, from the manifest cell 4 opened, into the run
(``RunMode(expected=...)``), and :func:`expected_issues` refuses a run whose images are not it:

- the same experiment folder and exactly the same analysed images;
- each image's fingerprint (size, modification time; an OME-Zarr store's inventory) as
  recorded there;
- each image's content SHA-256 where the segmented generation recorded one, so a run of
  ``verify_content`` generation must verify content too (a run that does not is refused, never
  silently weaker).

The run checks this before it reads any pixel (from the headers' fingerprints) and again on the
entries it would publish, inside its lock; its own check before the commit then binds those
entries to the files. A generation recorded without content SHA-256s is bound by fingerprint
only: a rewrite of the same size with its time set back is not seen there, as in any run of
such a generation (a SHA-256 never recorded cannot be made up later).

Error ``source_not_segmented``: nothing is published, the earlier generation stays as it is.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from ..fingerprint import Fingerprint
from ..manifest import FileEntry
from ..validation import ValidationIssue, error, plural

NOT_SEGMENTED = "source_not_segmented"
"""The error of a run whose images are not the generation its masks were segmented from."""
SHOWN = 3
"""How many images the message names; the issue lists all of them."""


@dataclass(frozen=True)
class ExpectedSources:
    """The images a run must be of: the generation masks were segmented from.

    Attributes
    ----------
    experiment_root
        The experiment folder, resolved.
    files
        ``{relative path: (fingerprint, content SHA-256 or None)}`` of every analysed image.
    verify_content
        True when that generation recorded content SHA-256s: the run must verify content.
    """

    experiment_root: Path
    files: Mapping[str, tuple[Fingerprint, str | None]] = field(default_factory=dict)
    verify_content: bool = False

    @classmethod
    def of_run(cls, inputs: object) -> ExpectedSources:
        """Return the generation of an opened run (:class:`.run_inputs.RunInputs`)."""
        manifest = inputs.manifest
        files = {
            entry.relative_path: (Fingerprint.recorded(entry), entry.content_sha256)
            for entry in manifest.files
        }
        return cls(
            Path(inputs.experiment_root).resolve(),
            MappingProxyType(files),
            bool(manifest.settings.verify_content),
        )


def expected_issues(
    expected: ExpectedSources,
    root: Path,
    fingerprints: Mapping[str, Fingerprint | None],
    *,
    verify_content: bool,
    entries: Mapping[str, FileEntry] | None = None,
) -> list[ValidationIssue]:
    """Return the error naming what is not the expected generation; [] when all of it is.

    Parameters
    ----------
    expected
        The generation the run must be of.
    root
        The run's experiment folder.
    fingerprints
        ``{relative path: fingerprint}`` of every image the run analyses (its headers', or
        its entries' when ``entries`` is given); None for one not bound to its file.
    verify_content
        Whether the run verifies content.
    entries
        The entries the run would publish, with their content SHA-256s; None before they exist.

    Returns
    -------
    list of ValidationIssue
        One ``source_not_segmented`` error, or nothing.
    """
    reasons: list[str] = []
    paths: set[str] = set()
    if Path(root).resolve() != expected.experiment_root:
        reasons.append(f"the experiment folder is {Path(root).resolve()}")
        paths.update(expected.files)
    if expected.verify_content and not verify_content:
        reasons.append("content verification is off, but the masks' images were verified")
    added = sorted(set(fingerprints) - set(expected.files))
    gone = sorted(set(expected.files) - set(fingerprints))
    if added:
        reasons.append(f"{plural(len(added), 'image')} added ({_named(added)})")
    if gone:
        reasons.append(f"{plural(len(gone), 'image')} missing ({_named(gone)})")
    changed = sorted(
        rel
        for rel, found in fingerprints.items()
        if rel in expected.files
        and (found is None or found != expected.files[rel][0] or _content(entries, rel, expected))
    )
    if changed:
        reasons.append(f"{plural(len(changed), 'image')} changed ({_named(changed)})")
    paths.update(added, gone, changed)
    if not reasons:
        return []
    return [
        error(
            NOT_SEGMENTED,
            "The images are not those the masks were segmented from: "
            + "; ".join(reasons)
            + ". Nothing was published: masks are never recorded for images other than the "
            "ones they were made from.",
            sorted(paths),
            "Segment the images as they are now (in the combined notebook: cell 4, Open the run "
            "again, then Segment the whole experiment again), then register the masks again.",
        )
    ]


def _content(entries: Mapping[str, FileEntry] | None, rel: str, expected: ExpectedSources) -> bool:
    """Return True when ``rel``'s entry has another content SHA-256 than the one expected."""
    sha = expected.files[rel][1]
    if entries is None or sha is None:
        return False
    entry = entries.get(rel)
    return entry is None or entry.content_sha256 != sha


def _named(paths: list[str]) -> str:
    """Return the first images of ``paths``, joined, with "…" when there are more."""
    return ", ".join(paths[:SHOWN]) + (", …" if len(paths) > SHOWN else "")


__all__ = ["NOT_SEGMENTED", "ExpectedSources", "expected_issues"]
