"""Histogram simplification experiment for float64 images.

Follows the MorphoBricks plan rule for float planes: each image gets its own
bin domain from its finite minimum and maximum, split into bin_count
equal-width bins (numpy convention: bins are [a, b) except the last, which
includes the maximum). One row per image goes to a CSV with the plan's fields.

Statistics are then computed from the CSV only. Because every image has its
own bin edges, groups are pooled by treating each bin's midpoint as a
representative value weighted by its count (nothing is averaged). Results are
compared with the pooled reference: all pixels of the group combined and the
statistic computed directly.

Error bound check: every pixel is represented by a midpoint at most half a
bin width away, so every pooled percentile can be off by at most the largest
half bin width among the group's images. That bound is reported per group.

Background-subtracted float values sit around zero, so differences are
reported in intensity units and as a percentage of the pooled p1-p99 span
(a relative difference to a value near zero would be meaningless).
"""

import argparse
import csv
from pathlib import Path

import numpy as np
from compare_statistics import PCTS, STATS
from histogram_experiment import (
    LABEL_COLS,
    LEVELS,
    META_COLS,
    group_key,
    load_images,
    pooled_reference,
)


def write_csv(images, path: Path, n_bins):
    bin_cols = [f"histogram_bin_{i:05d}" for i in range(n_bins)]
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(META_COLS + bin_cols)
        for im in images:
            px = im["pixels"]
            finite = px[np.isfinite(px)]
            lo, hi = float(finite.min()), float(finite.max())
            counts, _ = np.histogram(finite, bins=n_bins, range=(lo, hi))
            assert counts.sum() == finite.size
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
                    "float64",
                    64,
                    repr(lo),
                    repr(hi),
                    px.size,
                    finite.size,
                    repr(lo),
                    repr(hi),
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
                    "binning_min": float(row[col["binning_min"]]),
                    "binning_max": float(row[col["binning_max"]]),
                    "bin_count": int(row[col["bin_count"]]),
                    "valid": int(row[col["valid_pixel_count"]]),
                    "counts": np.array(row[first_bin:], dtype=np.int64),
                }
            )
    return rows


def half_width(row):
    return (row["binning_max"] - row["binning_min"]) / row["bin_count"] / 2


def midpoints(row):
    edges = np.linspace(row["binning_min"], row["binning_max"], row["bin_count"] + 1)
    return (edges[:-1] + edges[1:]) / 2


def stats_from_rows(rows):
    reps = np.concatenate([midpoints(r) for r in rows])
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


def compare(rows, ref, n_bins, out_dir: Path):
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
            bound = max(half_width(m) for m in members)
            for s in STATS:
                diff = h[s] - p[s]
                per_stat[s].append((diff, 100 * diff / span, abs(diff) / bound))
                detail.append(
                    [
                        n_bins,
                        level,
                        "/".join(k),
                        s,
                        f"{p[s]:.6f}",
                        f"{h[s]:.6f}",
                        f"{diff:.6f}",
                        f"{100 * diff / span:.5f}",
                        f"{bound:.6f}",
                    ]
                )
        for s in STATS:
            a = np.array(per_stat[s])
            summary.append(
                {
                    "bin_count": n_bins,
                    "level": level,
                    "n_groups": len(a),
                    "statistic": s,
                    "mean_abs_difference": np.abs(a[:, 0]).mean(),
                    "max_abs_difference": np.abs(a[:, 0]).max(),
                    "max_abs_difference_pct_of_p1_p99_span": np.abs(a[:, 1]).max(),
                    "max_difference_over_half_bin_bound": a[:, 2].max(),
                }
            )
    with open(out_dir / f"comparison_float64_{n_bins}bins.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "bin_count",
                "level",
                "group",
                "statistic",
                "pooled_direct",
                "from_histogram_csv",
                "histogram_minus_pooled",
                "difference_pct_of_p1_p99_span",
                "half_bin_width_bound",
            ]
        )
        w.writerows(detail)
    return summary


def main(root: Path, out: Path, bin_counts):
    out.mkdir(parents=True, exist_ok=True)
    images = load_images(root)
    ref = pooled_reference(images)
    summary = []
    for n in bin_counts:
        path = out / f"plane_histograms_float64_{n}bins.csv"
        write_csv(images, path, n)
        rows = read_csv(path)
        summary += compare(rows, ref, n, out)
        hw = [half_width(r) for r in rows]
        print(
            f"{n:>6} bins  {path.stat().st_size / 1e6:5.1f} MB  bin width per image: "
            f"{2 * min(hw):.3f} to {2 * max(hw):.3f}"
        )
    with open(out / "float64_binning_summary.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(summary[0]))
        w.writeheader()
        for s in summary:
            w.writerow({k: (f"{v:.6f}" if isinstance(v, float) else v) for k, v in s.items()})


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="simulated_experiment_float64")
    ap.add_argument("--out", default="results_histograms_float64")
    ap.add_argument("--bins", type=int, nargs="+", default=[32768, 8192, 1024])
    a = ap.parse_args()
    main(Path(a.root), Path(a.out), a.bins)
