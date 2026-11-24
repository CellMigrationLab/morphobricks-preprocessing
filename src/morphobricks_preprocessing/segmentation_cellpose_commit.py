"""What the Cellpose batch asks of the record while a mask is being committed.

The checks read the record under the type's write lock (called from ``write_mask``'s
``on_published``), with preprocessing's own reader; nothing here segments or writes.
:func:`after_commit` (moved from :mod:`.segmentation_cellpose` to keep it small) says how a
batch stops after a failure that came once a mask was kept with its record entry.
"""

from __future__ import annotations

from pathlib import Path

from .segmentation_handoff import stray_backups
from .segmentation_record import (
    SegmentationBatch,
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


def is_recorded(masks_root: str | Path, mask_type: str, expected: SegmentationBatch) -> bool:
    """Say whether the record on disk is what writing ``expected`` would have left.

    ``expected`` is the batch passed to ``write_record``, its last image the entry being
    committed. ``write_record`` can save the file and then fail (releasing its lock, an
    interrupt after the replace); the mask and the record then agree and the mask must not be
    rolled back. That needs the whole of it: the record's batch of this ID has the provenance
    of ``expected`` (the record's own ``same_provenance``: a refused reuse of the ID with
    another model or settings is not shown to agree) and the image's latest written entry is
    exactly this one (image, status, SHA-256), and the batch's whole image list is the one
    written (a reuse of the ID that leaves out earlier images is refused by ``write_record``
    and must not read as success). A record that cannot be read is not shown to agree.
    """
    entry = expected.images[-1]
    try:
        latest = read_record(masks_root, mask_type).latest(entry.image)
    except (OSError, SegmentationError):
        return False
    if latest is None:
        return False
    batch, found = latest
    return (
        batch.batch_id == expected.batch_id
        and batch.same_provenance(expected)
        and batch.images == expected.images
        and (found.image, found.status, found.mask_sha256)
        == (entry.image, entry.status, entry.mask_sha256)
    )


def replaced_earlier_mask(masks_root: str | Path, mask_type: str, before: frozenset[Path]) -> bool:
    """Say whether ``write_mask`` kept a copy of an earlier mask (a new backup).

    Read under the write lock, so it is exact; ``before`` is the strays seen before the write.
    """
    return bool(set(stray_backups(masks_root, mask_type)) - before)


def after_commit(
    entries: list[SegmentedImage], entry: SegmentedImage, exc: BaseException
) -> tuple[SegmentedImage, str]:
    """Stop the batch after a failure that came once ``entry`` was recorded and its mask kept.

    An error is reported in the table and stops the batch. An interrupt (``KeyboardInterrupt``)
    is raised again, with the image in ``entries`` first so the final record keeps its entry.
    """
    if isinstance(exc, Exception):
        return entry, f"{type(exc).__name__}: {exc}"
    entries.append(entry)
    raise exc
