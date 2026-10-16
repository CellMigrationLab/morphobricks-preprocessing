"""Simulate a MorphoBricks-style experiment of fluorescence-like uint16 TIFFs.

Layout: <root>/<condition>/<bio_rep>/<tech_rep>/img_XX.tif
2 conditions x 4 biological repeats x 2 technical repeats x 5 images = 80 images.

Variability is hierarchical, as in real experiments:
  condition  -> overall signal level and cell density
  bio repeat -> multiplicative brightness factor (default sigma 0.20, log scale)
  tech repeat-> multiplicative brightness factor (default sigma 0.08, log scale)
  image      -> number of cells, per-cell brightness, illumination falloff
Optionally, larger batch effects and random image sizes (--variable-size).
Pixels = camera offset + background + cells, with Poisson (shot) and Gaussian
(read) noise, stored as uint16. With --dtype float64 the same images are
stored as continuous background-subtracted values (no camera offset, no
rounding, background removed), so background pixels scatter around 0 and
can be negative, as after a typical float processing step.
"""

import argparse
import json
from pathlib import Path

import numpy as np
import tifffile

SHAPE = (512, 512)
CONDITIONS = {
    # name: (cell signal scale, mean number of cells per image)
    "control": (1.0, 25),
    "treated": (1.6, 40),
}
N_BIO, N_TECH, N_IMG = 4, 2, 5
CAMERA_OFFSET = 100.0
BACKGROUND = 60.0
READ_NOISE = 3.0
BASE_CELL_PEAK = 900.0


def illumination(rng, shape):
    """Radial vignetting: centre = 1, corners darker by a random amount."""
    yy, xx = np.mgrid[0 : shape[0], 0 : shape[1]]
    cy, cx = (shape[0] - 1) / 2, (shape[1] - 1) / 2
    r2 = ((yy - cy) ** 2 + (xx - cx) ** 2) / (cy**2 + cx**2)
    falloff = rng.uniform(0.15, 0.45)
    return 1.0 - falloff * r2


def cells_layer(rng, n_cells, peak_scale, shape):
    img = np.zeros(shape, dtype=np.float64)
    yy, xx = np.mgrid[0 : shape[0], 0 : shape[1]]
    for _ in range(n_cells):
        cy, cx = rng.uniform(0, shape[0]), rng.uniform(0, shape[1])
        ry, rx = rng.uniform(7, 20, size=2)
        theta = rng.uniform(0, np.pi)
        c, s = np.cos(theta), np.sin(theta)
        dy, dx = yy - cy, xx - cx
        u = (c * dx + s * dy) / rx
        v = (-s * dx + c * dy) / ry
        peak = peak_scale * rng.lognormal(0.0, 0.45)
        # Soft-edged cell body plus a brighter nucleus-like core
        body = np.exp(-((u**2 + v**2) ** 2) * 1.5)
        core = 0.8 * np.exp(-(u**2 + v**2) / 0.12)
        img += peak * (body + core)
    return img


def simulate(
    root: Path, seed: int, bio_sigma=0.20, tech_sigma=0.08, variable_size=False, dtype="uint16"
):
    rng = np.random.default_rng(seed)
    truth = []
    for cond, (cond_scale, mean_cells) in CONDITIONS.items():
        for b in range(1, N_BIO + 1):
            bio_factor = rng.lognormal(0.0, bio_sigma)
            bio_density = rng.lognormal(0.0, 0.25)
            for t in range(1, N_TECH + 1):
                tech_factor = rng.lognormal(0.0, tech_sigma)
                folder = root / cond / f"bio_rep{b}" / f"tech_rep{t}"
                folder.mkdir(parents=True, exist_ok=True)
                for i in range(1, N_IMG + 1):
                    shape = (
                        tuple(int(v) for v in rng.integers(256, 769, size=2))
                        if variable_size
                        else SHAPE
                    )
                    area = shape[0] * shape[1] / (SHAPE[0] * SHAPE[1])
                    n_cells = int(rng.poisson(mean_cells * bio_density * area))
                    peak = BASE_CELL_PEAK * cond_scale * bio_factor * tech_factor
                    illum = illumination(rng, shape)
                    expected = (BACKGROUND + cells_layer(rng, n_cells, peak, shape)) * illum
                    photons = rng.poisson(expected).astype(np.float64)
                    read_noise = rng.normal(0, READ_NOISE, shape)
                    if dtype == "float64":
                        pixels = photons + read_noise - BACKGROUND * illum
                    else:
                        pixels = CAMERA_OFFSET + photons + read_noise
                        pixels = np.clip(np.rint(pixels), 0, 65535).astype(np.uint16)
                    path = folder / f"img_{i:02d}.tif"
                    tifffile.imwrite(path, pixels, metadata={"axes": "YX"})
                    truth.append(
                        {
                            "file": path.relative_to(root).as_posix(),
                            "shape": list(shape),
                            "n_cells": n_cells,
                            "bio_factor": round(bio_factor, 4),
                            "tech_factor": round(tech_factor, 4),
                        }
                    )
    (root.parent / f"{root.name}_parameters.json").write_text(
        json.dumps(
            {
                "seed": seed,
                "bio_sigma": bio_sigma,
                "tech_sigma": tech_sigma,
                "variable_size": variable_size,
                "dtype": dtype,
                "images": truth,
            },
            indent=2,
        )
    )
    return len(truth)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="simulated_experiment")
    ap.add_argument("--seed", type=int, default=20261016)
    ap.add_argument("--bio-sigma", type=float, default=0.20)
    ap.add_argument("--tech-sigma", type=float, default=0.08)
    ap.add_argument(
        "--variable-size",
        action="store_true",
        help="random image sizes between 256 and 768 px per side",
    )
    ap.add_argument("--dtype", choices=["uint16", "float64"], default="uint16")
    args = ap.parse_args()
    n = simulate(
        Path(args.root), args.seed, args.bio_sigma, args.tech_sigma, args.variable_size, args.dtype
    )
    print(f"Wrote {n} images under {args.root}")
