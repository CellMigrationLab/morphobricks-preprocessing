"""Histogram simplification experiment for uint16 images.

1. For each bin count, write one histogram row per image to a CSV that follows
   the MorphoBricks plan contract (labels, extrema, counts, binning domain,
   then histogram_bin_00000 ...).
2. Read the CSV back and compute mean, median, std and percentiles per image,
   technical repeat, biological repeat and condition, using only the CSV.
3. Compare with the pooled reference: all pixels of the group combined and the
   statistic computed directly on them.

Binning convention (integer data)
  An integer value v is treated as occupying [v, v+1). The domain
  [binning_min, binning_max + 1) is split into bin_count equal-width bins;
  value v goes to bin floor((v - binning_min) * bin_count / W), with
  W = binning_max + 1 - binning_min (exact integer arithmetic).
  The representative value of bin i is the midpoint of the integers the bin
  actually contains: offsets a = ceil(i*W/n) .. b = ceil((i+1)*W/n) - 1, so
  representative = binning_min + (a + b) / 2. Whenever a bin holds a single
  integer (bin_count >= W) the representative is that integer and the
  histogram is exact.

Domains
  full     : binning_min = 0, binning_max = 65535 (dtype range; the plan's rule)
  observed : binning_min/max = the image's own pixel_min/pixel_max

Statistics from a histogram use the representative values weighted by their
counts: weighted mean and std, and inverse-CDF percentiles (the smallest
representative value with cumulative count >= q% of pixels). Groups pool the
counts of their member rows; nothing is averaged.
"""

import argparse
import csv
import time
from pathlib import Path

import numpy as np
import tifffile
from compare_statistics import PCTS, STATS, direct_stats

LEVELS = ["image", "technical_repeat", "biological_repeat", "condition"]
LABEL_COLS = ["condition", "biological_repeat", "technical_repeat"]
META_COLS = [
    "position_id",
    "image_file",
    "z",
    "c",
    "t",
    "condition",
    "biological_repeat",
    "technical_repeat",
    "source_relative_path",
    "plane_id",
    "dtype",
    "bit_depth",
    "pixel_min",
    "pixel_max",
    "total_pixel_count",
    "valid_pixel_count",
    "binning_min",
    "binning_max",
    "bin_count",
]


def load_images(root: Path):
    images = []
    for f in sorted(root.rglob("*.tif")):
        rel = f.relative_to(root).as_posix()
        cond, bio, tech, name = rel.split("/")
        images.append(
            {
                "rel": rel,
                "name": name,
                "condition": cond,
                "biological_repeat": bio,
                "technical_repeat": tech,
                "pixels": tifffile.imread(f).ravel(),
            }
        )
    return images


def histogram(px, n_bins, lo, hi):
    width = hi + 1 - lo
    idx = ((px.astype(np.int64) - lo) * n_bins) // width
    return np.bincount(idx, minlength=n_bins)


def write_bin_definition(path: Path, n_bins, lo=0, hi=65535):
    """One row per bin: the whole-number pixel values it covers (inclusive)."""
    width = hi + 1 - lo
    i = np.arange(n_bins + 1, dtype=np.int64)
    starts = -((-i * width) // n_bins)
    first, last = starts[:-1], starts[1:] - 1
    # cross-check against the assignment rule used for the histograms
    values = np.arange(lo, hi + 1, dtype=np.int64)
    idx = ((values - lo) * n_bins) // width
    assert np.array_equal(first + lo, values[np.searchsorted(idx, np.arange(n_bins))])
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["bin_column", "first_value", "last_value", "n_values", "representative_value"])
        for k in range(n_bins):
            w.writerow(
                [
                    f"histogram_bin_{k:05d}",
                    lo + first[k],
                    lo + last[k],
                    last[k] - first[k] + 1,
                    f"{lo + (first[k] + last[k]) / 2:g}",
                ]
            )
    widths = last - first + 1
    return {int(v): int((widths == v).sum()) for v in np.unique(widths)}


def write_csv(images, path: Path, n_bins, domain):
    bin_cols = [f"histogram_bin_{i:05d}" for i in range(n_bins)]
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(META_COLS + bin_cols)
        for im in images:
            px = im["pixels"]
            pmin, pmax = int(px.min()), int(px.max())
            lo, hi = (0, 65535) if domain == "full" else (pmin, pmax)
            counts = histogram(px, n_bins, lo, hi)
            assert counts.sum() == px.size
            w.writerow(
                [
                    "",
                    im["name"],
                    "",
                    "",
                    "",
                    im["condition"],
                    im["biological_repeat"],
                    im["technical_repeat"],
                    im["rel"],
                    im["rel"],
                    "uint16",
                    16,
                    pmin,
                    pmax,
                    px.size,
                    px.size,
                    lo,
                    hi,
                    n_bins,
                ]
                + counts.tolist()
            )


def read_csv(path: Path):
    rows = []
    with open(path, newline="") as fh:
        r = csv.reader(fh)
        header = next(r)
        first_bin = header.index("histogram_bin_00000")
        col = {name: i for i, name in enumerate(header[:first_bin])}
        for row in r:
            rows.append(
                {
                    **{k: row[col[k]] for k in LABEL_COLS + ["source_relative_path"]},
                    "binning_min": int(row[col["binning_min"]]),
                    "binning_max": int(row[col["binning_max"]]),
                    "bin_count": int(row[col["bin_count"]]),
                    "valid": int(row[col["valid_pixel_count"]]),
                    "counts": np.array(row[first_bin:], dtype=np.int64),
                }
            )
    return rows


def representatives(row):
    n, lo = row["bin_count"], row["binning_min"]
    width = row["binning_max"] + 1 - lo
    i = np.arange(n + 1, dtype=np.int64)
    starts = -((-i * width) // n)  # ceil(i * W / n), exact integer arithmetic
    a, b = starts[:-1], starts[1:] - 1
    return lo + (a + b) / 2  # bins holding no integer always have count 0


def stats_from_rows(rows):
    reps = np.concatenate([representatives(r) for r in rows])
    counts = np.concatenate([r["counts"] for r in rows]).astype(np.float64)
    keep = counts > 0
    reps, counts = reps[keep], counts[keep]
    order = np.argsort(reps, kind="stable")
    reps, counts = reps[order], counts[order]
    n = counts.sum()
    assert n == sum(r["valid"] for r in rows)
    mean = (counts * reps).sum() / n
    std = np.sqrt((counts * (reps - mean) ** 2).sum() / n)
    cdf = np.cumsum(counts)

    def pct(q):
        return float(reps[np.searchsorted(cdf, np.ceil(q / 100 * n), side="left")])

    out = {"mean": mean, "median": pct(50), "std": std}
    out.update({f"p{p:g}": pct(p) for p in PCTS})
    return out


def group_key(item, level):
    if level == "image":
        return (
            item["condition"],
            item["biological_repeat"],
            item["technical_repeat"],
            item.get("source_relative_path", item.get("rel")),
        )
    depth = {"technical_repeat": 3, "biological_repeat": 2, "condition": 1}[level]
    return tuple(item[c] for c in LABEL_COLS[:depth])


def pooled_reference(images):
    ref = {}
    for level in LEVELS:
        groups = {}
        for im in images:
            groups.setdefault(group_key(im, level), []).append(im["pixels"])
        for k, pxs in groups.items():
            ref[(level, k)] = direct_stats(np.concatenate(pxs))
    return ref


def compare(rows, ref, n_bins, domain, out_dir: Path):
    detail, summary = [], []
    for level in LEVELS:
        groups = {}
        for r in rows:
            groups.setdefault(group_key(r, level), []).append(r)
        per_stat = {s: [] for s in STATS}
        for k, members in sorted(groups.items()):
            h = stats_from_rows(members)
            p = ref[(level, k)]
            span = p["p99"] - p["p1"]
            for s in STATS:
                diff = h[s] - p[s]
                rel = 100 * diff / p[s]
                per_stat[s].append((diff, rel, 100 * diff / span))
                detail.append(
                    [
                        domain,
                        n_bins,
                        level,
                        "/".join(k),
                        s,
                        f"{p[s]:.4f}",
                        f"{h[s]:.4f}",
                        f"{diff:.4f}",
                        f"{rel:.4f}",
                        f"{100 * diff / span:.4f}",
                    ]
                )
        for s in STATS:
            a = np.array(per_stat[s])
            summary.append(
                {
                    "domain": domain,
                    "bin_count": n_bins,
                    "level": level,
                    "n_groups": len(a),
                    "statistic": s,
                    "max_abs_difference": np.abs(a[:, 0]).max(),
                    "mean_abs_relative_difference_pct": np.abs(a[:, 1]).mean(),
                    "max_abs_relative_difference_pct": np.abs(a[:, 1]).max(),
                    "max_abs_difference_pct_of_p1_p99_span": np.abs(a[:, 2]).max(),
                }
            )
    with open(out_dir / f"comparison_{domain}_{n_bins}bins.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "domain",
                "bin_count",
                "level",
                "group",
                "statistic",
                "pooled_direct",
                "from_histogram_csv",
                "histogram_minus_pooled",
                "relative_difference_pct",
                "difference_pct_of_p1_p99_span",
            ]
        )
        w.writerows(detail)
    return summary


def main(root: Path, out: Path, bin_counts, domains):
    out.mkdir(parents=True, exist_ok=True)
    images = load_images(root)
    ref = pooled_reference(images)
    summary, files = [], []
    for domain in domains:
        for n in bin_counts:
            name = (
                f"plane_histograms_{n}bins.csv"
                if domain == "full"
                else f"plane_histograms_{n}bins_observed_range.csv"
            )
            path = out / name
            t0 = time.perf_counter()
            write_csv(images, path, n, domain)
            t1 = time.perf_counter()
            if domain == "full":
                widths = write_bin_definition(out / f"bin_definition_{n}bins.csv", n)
                print(f"         {n:>6} bins: values per bin -> number of bins {widths}")
            rows = read_csv(path)
            t2 = time.perf_counter()
            summary += compare(rows, ref, n, domain, out)
            files.append([domain, n, name, path.stat().st_size, f"{t1 - t0:.2f}", f"{t2 - t1:.2f}"])
            print(
                f"{domain:>8} {n:>6} bins  {path.stat().st_size / 1e6:6.1f} MB  "
                f"write {t1 - t0:5.1f}s  read {t2 - t1:5.1f}s"
            )
    with open(out / "histogram_binning_summary.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(summary[0]))
        w.writeheader()
        for s in summary:
            w.writerow({k: (f"{v:.4f}" if isinstance(v, float) else v) for k, v in s.items()})
    with open(out / "csv_files.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["domain", "bin_count", "file", "size_bytes", "write_seconds", "read_seconds"])
        w.writerows(files)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="simulated_experiment")
    ap.add_argument("--out", default="results_histograms")
    ap.add_argument("--bins", type=int, nargs="+", default=[65536, 30000, 10000, 1000])
    ap.add_argument(
        "--domains", nargs="+", default=["full", "observed"], choices=["full", "observed"]
    )
    a = ap.parse_args()
    main(Path(a.root), Path(a.out), a.bins, a.domains)
