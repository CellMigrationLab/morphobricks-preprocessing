"""Compare pooled vs merged ("mean of means") statistics across the hierarchy.

For every image: mean, median, std and percentiles.
For every technical repeat, biological repeat and condition, two methods:
  pooled : put all pixels of the group together, then compute the statistic
  merged : compute the statistic per member, then average the member values
           (images -> technical repeat -> biological repeat -> condition)

As a check of the MorphoBricks plan, pooled statistics are also recomputed
from summed per-image 65,536-bin histograms (they should match exactly for
uint16), and std is also combined exactly from per-member (n, mean, variance).

Percentiles use the inverse-CDF definition: the smallest pixel value v such
that at least q% of pixels are <= v (numpy method="inverted_cdf"). The median
is the 50th percentile under the same definition.

Differences are reported relative to the pooled value and, because raw values
include the camera offset, also as a percentage of the pooled p1-p99 span
(the scale a percentile normalization would use).
"""

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

import numpy as np
import tifffile

PCTS = [1, 5, 25, 75, 95, 99, 99.9]
STATS = ["mean", "median", "std"] + [f"p{p:g}" for p in PCTS]
LEVELS = ["technical_repeat", "biological_repeat", "condition"]


def direct_stats(px):
    px = px.astype(np.float64, copy=False)
    q = np.percentile(px, [50] + PCTS, method="inverted_cdf")
    out = {"n": px.size, "mean": px.mean(), "median": q[0], "std": px.std()}
    out.update({f"p{p:g}": v for p, v in zip(PCTS, q[1:])})
    return out


def hist_stats(hist):
    """Statistics from a full-resolution integer histogram (value = bin index)."""
    values = np.arange(hist.size, dtype=np.float64)
    n = hist.sum()
    mean = (hist * values).sum() / n
    std = np.sqrt((hist * (values - mean) ** 2).sum() / n)
    cdf = np.cumsum(hist)

    def pct(q):
        return float(np.searchsorted(cdf, np.ceil(q / 100 * n), side="left"))

    out = {"n": int(n), "mean": mean, "median": pct(50), "std": std}
    out.update({f"p{p:g}": pct(p) for p in PCTS})
    return out


def exact_std_merge(members):
    """Combine (n, mean, std) of members into the exact pooled std."""
    n = sum(m["n"] for m in members)
    mean = sum(m["n"] * m["mean"] for m in members) / n
    ss = sum(m["n"] * (m["std"] ** 2 + (m["mean"] - mean) ** 2) for m in members)
    return np.sqrt(ss / n)


def main(root: Path, out: Path):
    out.mkdir(parents=True, exist_ok=True)
    files = sorted(root.rglob("*.tif"))
    images = []
    for f in files:
        cond, bio, tech, _ = f.relative_to(root).parts
        px = tifffile.imread(f).ravel()
        images.append(
            {
                "file": f.relative_to(root).as_posix(),
                "condition": cond,
                "biological_repeat": bio,
                "technical_repeat": tech,
                "pixels": px,
                "hist": np.bincount(px, minlength=65536).astype(np.int64),
                "stats": direct_stats(px),
            }
        )

    # ---- per-image table
    with open(out / "per_image_stats.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(
            ["image_file", "condition", "biological_repeat", "technical_repeat", "n_pixels"] + STATS
        )
        for im in images:
            s = im["stats"]
            w.writerow(
                [
                    im["file"],
                    im["condition"],
                    im["biological_repeat"],
                    im["technical_repeat"],
                    s["n"],
                ]
                + [f"{s[k]:.4f}" for k in STATS]
            )

    # ---- group keys for each level (labels are scoped to their parents)
    def key(im, level):
        k = (im["condition"],)
        if level in ("biological_repeat", "technical_repeat"):
            k += (im["biological_repeat"],)
        if level == "technical_repeat":
            k += (im["technical_repeat"],)
        return k

    # members of a group at each level are the groups one level below
    child_level = {
        "technical_repeat": None,
        "biological_repeat": "technical_repeat",
        "condition": "biological_repeat",
    }
    groups = {}  # (level, key) -> dict(pooled, merged, hist_pooled, std_exact_merge)
    summary = {lvl: defaultdict(list) for lvl in LEVELS}
    max_hist_mismatch = 0.0

    for level in LEVELS:
        members = defaultdict(list)
        for im in images:
            members[key(im, level)].append(im)
        rows = []
        for k, ims in sorted(members.items()):
            pooled = direct_stats(np.concatenate([im["pixels"] for im in ims]))
            hpool = hist_stats(sum(im["hist"] for im in ims))
            max_hist_mismatch = max(
                max_hist_mismatch, max(abs(pooled[s] - hpool[s]) for s in STATS)
            )
            if child_level[level] is None:
                parts = [im["stats"] for im in ims]
            else:
                child_keys = sorted({key(im, child_level[level]) for im in ims})
                parts = [groups[(child_level[level], ck)]["merged"] for ck in child_keys]
            merged = {s: float(np.mean([p[s] for p in parts])) for s in STATS}
            merged["n"] = sum(p["n"] for p in parts)
            std_exact = exact_std_merge(
                parts
                if child_level[level] is None
                else [groups[(child_level[level], ck)]["pooled"] for ck in child_keys]
            )
            groups[(level, k)] = {"pooled": pooled, "merged": merged}
            for s in STATS:
                diff = merged[s] - pooled[s]
                rel = 100 * diff / pooled[s]
                span = pooled["p99"] - pooled["p1"]
                rng_rel = 100 * diff / span
                summary[level][s].append((rel, rng_rel))
                rows.append(
                    list(k)
                    + [
                        len(ims),
                        s,
                        f"{pooled[s]:.4f}",
                        f"{merged[s]:.4f}",
                        f"{diff:.4f}",
                        f"{rel:.3f}",
                        f"{rng_rel:.3f}",
                        f"{hpool[s]:.4f}",
                        f"{std_exact:.4f}" if s == "std" else "",
                    ]
                )
        label_cols = ["condition", "biological_repeat", "technical_repeat"][: len(k)]
        with open(out / f"group_stats_{level}.csv", "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(
                label_cols
                + [
                    "n_images",
                    "statistic",
                    "pooled",
                    "merged",
                    "merged_minus_pooled",
                    "relative_difference_pct",
                    "difference_pct_of_p1_p99_range",
                    "pooled_from_histograms",
                    "std_exact_merge",
                ]
            )
            w.writerows(rows)

    # ---- summary across groups at each level
    result = {"max_hist_vs_direct_abs_diff": max_hist_mismatch, "levels": {}}
    with open(out / "information_loss_summary.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "level",
                "n_groups",
                "statistic",
                "mean_relative_difference_pct",
                "mean_abs_relative_difference_pct",
                "min_relative_difference_pct",
                "max_relative_difference_pct",
                "mean_abs_difference_pct_of_p1_p99_range",
                "max_abs_difference_pct_of_p1_p99_range",
            ]
        )
        for level in LEVELS:
            result["levels"][level] = {}
            for s in STATS:
                a = np.array(summary[level][s])
                r, g = a[:, 0], np.abs(a[:, 1])
                row = {
                    "mean": r.mean(),
                    "mean_abs": np.abs(r).mean(),
                    "min": r.min(),
                    "max": r.max(),
                    "n_groups": r.size,
                    "range_mean_abs": g.mean(),
                    "range_max_abs": g.max(),
                }
                result["levels"][level][s] = row
                w.writerow(
                    [level, r.size, s]
                    + [
                        f"{row[x]:.3f}"
                        for x in (
                            "mean",
                            "mean_abs",
                            "min",
                            "max",
                            "range_mean_abs",
                            "range_max_abs",
                        )
                    ]
                )
    (out / "information_loss_summary.json").write_text(json.dumps(result, indent=2, default=float))
    print(f"Images: {len(images)}  |  histogram vs direct max abs diff: {max_hist_mismatch}")
    for level in LEVELS:
        print(f"\n{level}")
        for s in STATS:
            r = result["levels"][level][s]
            print(
                f"  {s:>6}: mean {r['mean']:+7.2f}%  range [{r['min']:+7.2f}, {r['max']:+7.2f}]%"
                f"  | of p1-p99 span: mean {r['range_mean_abs']:5.2f}%  max {r['range_max_abs']:5.2f}%"
            )


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="simulated_experiment")
    ap.add_argument("--out", default="results")
    a = ap.parse_args()
    main(Path(a.root), Path(a.out))
