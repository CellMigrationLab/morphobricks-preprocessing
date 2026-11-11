"""The description's meaning for :class:`.run_inputs.RunInputs` (Stage 1 design section 3).

Every run has a published description and records its SHA-256 in the manifest
(``experiment_description``, :mod:`.description_record`). :class:`SemanticInputs` is mixed into
``RunInputs`` and gives the consumer the canonical identities and semantics, read from the
published file after checking it is the file the run read:

- canonical channel IDs per position and ``channel_index(fov, id)``: by ID, never by C index
  alone, and an absent channel is an error, never a default;
- ``repeat_group``, ``condition_id`` and ``technical_repeat_id`` beside the unchanged FOV key
  and the unit's declared ``image_type`` (:mod:`.fov_identity`), the condition reference
  graph and the masks' semantics;
- refusals: ``A_DRAFT_REFUSED`` for a draft or an unconfirmed file, ``A_MASK_UNCHECKED`` for a
  mask file whose representation the mask step has not checked.

There is no run without a description (Spec ADR 0002 AR10): :class:`.run_inputs.RunInputs`
refuses an output folder without one (``A_DESCRIPTION_REQUIRED``).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

from .description import Description
from .description_consume import read_published_description
from .description_file import DESCRIPTION_FILENAME
from .description_unnamed import FIELD as UNNAMED_FIELD
from .fov_identity import FovIdentity, fov_identity
from .manifest import FileEntry, RunManifest
from .mask_representation import CHECKED, file_status, read_checked
from .run_inputs_error import (
    ChannelNotFoundError,
    DescriptionRequiredError,
    MaskUncheckedError,
    RunInputsError,
)

if TYPE_CHECKING:
    from .run_inputs import Fov


@dataclass(frozen=True)
class MaskSemantics:
    """What the user confirmed about a declared mask (experiment-v1 section 4.5)."""

    source_type: str
    representation: str
    source_channels: tuple[str, ...] | None = None


def check_description(output: Path, manifest: RunManifest) -> Description:
    """Return the published description a run recorded, checked against its SHA-256.

    Raises
    ------
    DescriptionRequiredError
        ``A_DESCRIPTION_REQUIRED``: the manifest records no description.
    DraftRefusedError
        ``A_DRAFT_REFUSED``: the published file holds an unconfirmed repeat.
    RunInputsError
        When the file is gone, not valid, or not the file the run read (changed since).
    """
    recorded = manifest.experiment_description
    if recorded is None:
        raise DescriptionRequiredError(
            "The run records no experiment description (A_DESCRIPTION_REQUIRED). Describe the "
            "experiment and run preprocessing again."
        )
    description, sha256 = read_published_description(output / DESCRIPTION_FILENAME)
    if sha256 != recorded["sha256"]:
        raise RunInputsError(
            f"The experiment description {DESCRIPTION_FILENAME} is not the one the run read "
            "(changed or republished since). Run preprocessing again."
        )
    return description


def with_identity(fov: Fov, entry: FileEntry, description: Description) -> Fov:
    """Return the :class:`.run_inputs.Fov` with its canonical identity added.

    Raises
    ------
    RunInputsError
        When the entry's folders name no unit of the description, the unit declares another
        number of channels than the image has, or other channel IDs than the run recorded.
    """
    identity = _identity(entry.labels, description)
    if len(identity.channel_ids) != fov.c_count:
        raise RunInputsError(
            f"{entry.relative_path} has {fov.c_count} channels but its unit declares "
            f"{len(identity.channel_ids)} ({', '.join(identity.channel_ids)}). "
            "Run preprocessing again."
        )
    if entry.channel_ids and tuple(entry.channel_ids) != identity.channel_ids:
        raise RunInputsError(
            f"{entry.relative_path} was measured with the channels "
            f"{', '.join(entry.channel_ids)}, but its unit now declares "
            f"{', '.join(identity.channel_ids)}. Run preprocessing again."
        )
    return replace(
        fov,
        repeat_group=identity.repeat_group,
        condition_id=identity.condition_id,
        technical_repeat_id=identity.technical_repeat_id,
        channel_ids=identity.channel_ids,
        image_type=identity.image_type,
    )


def _identity(labels: tuple[str, str, str], description: Description) -> FovIdentity:
    """Return a position's canonical identity, refusing labels the description lacks."""
    try:
        return fov_identity(description, labels)
    except LookupError as exc:
        raise RunInputsError(
            f"The run's image folders do not match its description: {exc}. Run preprocessing again."
        ) from exc


class SemanticInputs:
    """Methods of ``RunInputs`` that need the published description."""

    output_folder: Path
    manifest: RunManifest

    def description(self) -> Description:
        """Return the published description the run read.

        The file is read again and must still be the one the run recorded; only the
        published ``morphobricks.toml`` is ever read, never a draft.

        Raises
        ------
        DraftRefusedError
            ``A_DRAFT_REFUSED``: the published file now holds an unconfirmed repeat.
        RunInputsError
            When the file is gone, invalid, or changed since the run.
        """
        return check_description(self.output_folder, self.manifest)

    def section_digests(self) -> dict[str, str]:
        """Return the description's section digests as the run's manifest recorded them.

        The SHA-256 (hex) of the ``conditions``, ``channels``, ``repeats`` and ``masks``
        sections (:func:`.description_write.section_digests`), so a brick can key a cache or a
        memo on the section it depends on. Read from the manifest only: the published file is
        not read again (``description()`` does that).

        Returns
        -------
        dict[str, str]
            A new dict.
        """
        return dict(self.manifest.experiment_description["section_digests"])

    def images_without_header_channel_names(self) -> dict[str, tuple[int, ...]]:
        """Return the images the run passed without header channel names, as it recorded them.

        Each image's relative path maps to its zero-based C positions whose header gives no
        name (all of them when the header names none). Empty when every header names all its
        channels. Read from the manifest only.
        """
        recorded = self.manifest.experiment_description
        return {
            item["image"]: tuple(item["channel_positions"])
            for unit in recorded["units"]
            for item in unit.get(UNNAMED_FIELD, ())
        }

    def channel_index(self, fov: object, channel_id: str) -> int:
        """Return the C index of a canonical channel in one position's image.

        Parameters
        ----------
        fov
            A :class:`.run_inputs.Fov` or the image's relative path.
        channel_id
            The canonical channel ID.

        Raises
        ------
        ChannelNotFoundError
            ``A_SCOPE_INCOMPLETE``: the image does not have that channel (nothing is
            defaulted); the message names the image.
        RunInputsError
            When the image is not one of the run's.
        """
        ids = getattr(fov, "channel_ids", None)
        if ids is None:
            ids = self._channel_ids(fov)
        if channel_id not in ids:
            raise ChannelNotFoundError(
                f"The channel {channel_id!r} is not in the image "
                f"{getattr(fov, 'relative_path', fov)}; its channels are {', '.join(ids)} "
                "(A_SCOPE_INCOMPLETE)."
            )
        return ids.index(channel_id)

    def _channel_ids(self, fov: object) -> tuple[str, ...]:
        description = self.description()
        relative = getattr(fov, "relative_path", fov)
        entry = self.manifest.file(str(relative))
        if entry is None:
            raise RunInputsError(f"{relative} is not an image of this run.")
        return _identity(entry.labels, description).channel_ids

    def references(self) -> dict[str, str | None]:
        """Return the condition reference graph: condition ID to the ID it references.

        The value is None for a condition that references none.
        """
        description = self.description()
        return {key: value.reference for key, value in sorted(description.conditions.items())}

    def mask_semantics(self) -> dict[str, MaskSemantics]:
        """Return the declared masks by ID with their source type, representation and channels."""
        description = self.description()
        return {
            key: MaskSemantics(m.source_type, m.representation, m.source_channels)
            for key, m in sorted(description.masks.items())
        }

    def mask_check_status(self, mask_id: str, fov: object) -> str:
        """Return ``checked`` or ``not yet checked`` for one mask of one image.

        It is ``checked`` only when the mask step recorded this file with the fingerprint the
        run recorded, for the representation and source type the description declares now.

        Raises
        ------
        RunInputsError
            For an undeclared mask ID, or an image with no recorded file of that mask.
        """
        mask = self.description().masks.get(mask_id)
        if mask is None:
            raise RunInputsError(f"{mask_id!r} is not a mask of this run's description.")
        relative = str(getattr(fov, "relative_path", fov))
        recorded = self._mask_entry(relative, mask.source_type)
        table = read_checked(self.output_folder)
        return file_status(table, mask_id, mask.source_type, mask.representation, recorded)

    def _mask_entry(self, image: str, mask_type: str) -> Mapping:
        """Return the recorded mask of an image and type."""
        for recorded in (self.manifest.masks or {}).get("files", ()):
            if recorded["image"] == image and recorded["type"] == mask_type:
                return recorded
        raise RunInputsError(f"The run recorded no {mask_type} mask for {image}.")

    def require_checked(self, recorded: Mapping, mask_type: str) -> None:
        """Refuse a mask file the mask step has not checked (``A_MASK_UNCHECKED``).

        Only a mask type the description declares is refused.
        """
        description = self.description()
        table = read_checked(self.output_folder)
        for mask_id, mask in description.masks.items():
            if mask.source_type != mask_type:
                continue
            state = file_status(table, mask_id, mask_type, mask.representation, recorded)
            if state != CHECKED:
                raise MaskUncheckedError(
                    f"The mask {mask_id!r} ({recorded['path']}) is not yet checked against "
                    f"its declared representation ({mask.representation}). Run the masks "
                    "step on this output folder first (A_MASK_UNCHECKED)."
                )
