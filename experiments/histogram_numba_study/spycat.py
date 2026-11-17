"""Categorise py-spy raw stacks: spycat.py FILE..."""

import re
import sys
from collections import Counter

CATS = [  # (category, regex on a frame); the deepest matching frame wins
    (
        "decode/read (zlib, tifffile, zarr, file read)",
        r"zlib|decode|_read_segment|_read_tiff_box|zarr|tifffile|read_bytes|readinto",
    ),
    ("hashing (file/content sha256)", r"sha256|hashlib|_digest|fingerprint"),
    (
        "per-pixel binning (np.bincount, table add)",
        r"_small_integer_histogram|_integer_bins|_float_bins",
    ),
    (
        "per-bin Python loops (bin starts/limits, representatives, error)",
        r"_integer_bin_starts|integer_bin_limits|_integer_bin_limits|integer_representatives|max_representative_error|_counts_from_per_value|min_max",
    ),
    ("tail moments / range check", r"tail|_range_check|_RadixSelection|exact_sums|counted_sums"),
    ("CSV writing", r"write_plane_histograms|csv_io|writerow|_row|channel_columns|table_header"),
    ("pooling/summaries", r"pooling|summaries"),
]
for path in sys.argv[1:]:
    c = Counter()
    total = 0
    for line in open(path):
        stack, _, n = line.rstrip().rpartition(" ")
        n = int(n)
        total += n
        frames = stack.split(";")
        cat = "other (imports, discovery, staging, misc)"
        for f in reversed(frames):
            hit = next((name for name, rx in CATS if re.search(rx, f)), None)
            if hit:
                cat = hit
                break
        c[cat] += n
    print(f"== {path}: {total} samples")
    for k, v in c.most_common():
        print(f"  {v / total * 100:5.1f}%  {v:6d}  {k}")
