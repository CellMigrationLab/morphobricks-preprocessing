"""What a TIFF header states about colour: photometric interpretation and interleaved samples.

Moved from ``metadata.py`` (``_rgb_samples``) and extended for the RGB header override
(Spec experiment-v1 4.4b, amendment RH1): the override is accepted only for an image with exactly
three samples per pixel on an interleaved samples axis, whatever its photometric
interpretation says. Everything here is read from the header of the series' first page; no
pixel is decoded.
"""

from __future__ import annotations

import tifffile


def colour_header(series: tifffile.TiffPageSeries) -> dict[str, object]:
    """Return the colour fields of :class:`.metadata.TiffMetadata` for a ``tifffile`` series.

    Parameters
    ----------
    series
        The first image series of the file.

    Returns
    -------
    dict
        ``rgb_samples`` (photometric RGB with three samples per pixel on the ``S`` axis),
        ``photometric`` (the photometric interpretation in lower case as ``tifffile`` names it,
        for example ``"minisblack"`` or ``"rgb"``; None when the tag holds a value
        ``tifffile`` does not name) and ``interleaved_samples`` (the samples per pixel when
        they are stored interleaved: TIFF contiguous planar configuration, the ``S`` axis of
        the series; 0 when the image has no interleaved samples axis).
    """
    keyframe = series.keyframe
    samples = int(keyframe.samplesperpixel)
    interleaved = (
        samples > 1
        and keyframe.planarconfig == tifffile.PLANARCONFIG.CONTIG
        and "S" in series.axes
        and series.shape[series.axes.index("S")] == samples
    )
    try:
        photometric: str | None = tifffile.PHOTOMETRIC(keyframe.photometric).name.lower()
    except ValueError:
        photometric = None
    return {
        "rgb_samples": (
            "S" in series.axes and keyframe.photometric == tifffile.PHOTOMETRIC.RGB and samples == 3
        ),
        "photometric": photometric,
        "interleaved_samples": samples if interleaved else 0,
    }
