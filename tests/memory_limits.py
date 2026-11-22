"""Memory limits for tests that read a large plane in blocks beside a small plane read whole."""

from morphobricks_preprocessing.image_qc import qc_memory

__all__ = ["block_limit", "qc_memory"]


def block_limit(small_estimate: int, small_shape: tuple[int, int], cols: int) -> int:
    """Return the limit that fits the small plane whole and one row of ``cols`` pixels in blocks.

    The small plane's whole estimate, with the QC working memory of its (Y, X) shape swapped
    for that of the one-row block (the histogram and pixel terms of both are those of the same
    number of pixels), so the large plane is read in blocks of one row and not refused.
    """
    return small_estimate - qc_memory(*small_shape, False) + qc_memory(1, cols, False)
