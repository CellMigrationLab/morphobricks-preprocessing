"""What the Cellpose batch asks of the record while a mask is being committed.

Both functions read the record under the type's write lock (called from ``write_mask``'s
``on_published``), with preprocessing's own reader; nothing here segments or writes.
"""

from __future__ import annotations

from pathlib import Path

from .segmentation_handoff import stray_backups
from .segmentation_record import (
    SegmentationError,
    SegmentedImage,
    read_record,
    record_path,
)


def check_newest(masks_root: str | Path, mask_type: str, batch_id: str, image: str) -> None:
    """Refuse an entry for ``image`` that would not be its newest (a later batch wrote it).

    ``SegmentationRecord.latest`` orders batches as recorded, and a batch keeps the place of its
    first write. Called under the type's write lock, where no other cooperating batch records a
    written mask.
    """
    if not record_path(masks_root, mask_type).exists():
        return
    batches = read_record(masks_root, mask_type).batches
    ids = [b.batch_id for b in batches]
    if batch_id not in ids:
        return  # appended: the newest
    later = [
        b.batch_id
        for b in batches[ids.index(batch_id) + 1 :]
        if any(e.image == image and e.status == "written" for e in b.images)
    ]
    if later:
        raise SegmentationError(
            f"The batch {later[-1]!r}, recorded after the batch {batch_id!r} started, wrote "
            f"{image}; an entry of this batch would not be the newest. Start a new batch."
        )


def is_recorded(
    masks_root: str | Path, mask_type: str, batch_id: str, entry: SegmentedImage
) -> bool:
    """Say whether the record on disk already has ``entry`` as its image's latest, from this batch.

    ``write_record`` can save the file and then fail (releasing its lock, an interrupt after the
    replace); the mask and the record then agree and the mask must not be rolled back. A record
    that cannot be read is not shown to agree.
    """
    try:
        latest = read_record(masks_root, mask_type).latest(entry.image)
    except (OSError, SegmentationError):
        return False
    return (
        latest is not None
        and latest[0].batch_id == batch_id
        and latest[1].mask_sha256 == entry.mask_sha256
    )


def replaced_earlier_mask(masks_root: str | Path, mask_type: str, before: frozenset[Path]) -> bool:
    """Say whether ``write_mask`` kept a copy of an earlier mask (a new backup).

    Read under the write lock, so it is exact; ``before`` is the strays seen before the write.
    """
    return bool(set(stray_backups(masks_root, mask_type)) - before)
