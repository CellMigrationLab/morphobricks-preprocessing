# Evaluation of `he_tissue_v1` on the four presentation images (PI-1 to PI-4)

- **Status:** evaluation of acceptance criterion 1 (and the proof-of-concept column of criterion
  2) of [he-tissue-mask.md §1](he-tissue-mask.md#1-the-rule-he_tissue_v1). **The rule is not
  frozen**: it stays `"provisional"` until one report meets every criterion and the owner accepts
  it. Criterion 3 (three public scanner sets, two vendors) is **not run here** and remains.
- **Bar (fixed in the design before this evaluation, not moved):** per image, Dice
  `2 |R ∩ V| / (|R| + |V|) >= 0.90` and discordant `|R xor V| / |R| <= 0.05`, on the full
  plane, unrounded; `|R| = 0` fails.
- **Run (16 November 2026):** the I1 helper, end to end, on a described run of the four images
  (`RunInputs.he_tissue_mask`, output `HETissue`), at the I1 branch; NumPy 2.5.5, SciPy 1.18.1,
  scikit-image 0.26.0, normalization v0.7.0 (`c6a9f9d`). `V` is each written file (`1` = kept),
  and equals the in-memory preview. The pin later moved to v0.7.1 (`fcfaeac`), whose modules
  used by the rule are byte-identical to v0.7.0's (design, implementation notes), so `V` is
  unchanged.
- **`R`:** the proof of concept's `scripts/tissue_mask.py`, run unchanged on the same decoded
  pixels, with `E` and `H` from normalization's `to_scalar(rgb, axes="YXC", preset="he_eosin")`,
  as its `run_all.py` passed them (through fiberfeatures `prepare_rgb_input`). Its thresholds are
  the ones recorded in the presentation `results/run_record.json` (checked equal to the script's
  `THRESHOLDS`). This `R` reproduces the presentation's saved masks
  (`results/masks/tissue_mask_<n>.png`) **bit for bit** on all four images.
- **Images:** the owner's four WebP files, identified by name and SHA-256; each SHA-256 was
  recomputed and equals the design's table. The reference is the decoded pixels of exactly these
  bytes (Pillow, `convert("RGB")`), 1500 × 1500 × 3, `uint8`. The images are not in this
  repository.

## Results

| ID | File | SHA-256 (checked) | Dice | Discordant | Meets the bar |
| --- | --- | --- | --- | --- | --- |
| PI-1 | `1.webp` | `3a074fb8…f5fcd8e7`[^sha] | 0.99777 | 0.44 % | **pass** |
| PI-2 | `2.webp` | `5725d058…c0a45f69` | 0.99815 | 0.37 % | **pass** |
| PI-3 | `3.webp` | `518640ca…a7adef59` | 0.99992 | 0.015 % | **pass** |
| PI-4 | `4.webp` | `dda02603…45ef07d2` | 0.99970 | 0.060 % | **pass** |

[^sha]: Full digests are in the design's table; each file's SHA-256 was compared in full.

Shares of all pixels (the components can overlap; `R` has no `od_floored`):

| ID | Rule | kept | nuclei | red cells | background | `od_floored` |
| --- | --- | --- | --- | --- | --- | --- |
| PI-1 | `R` | 69.392 % | 25.664 % | 0.438 % | 4.581 % | |
| PI-1 | `V` | 69.130 % | 25.900 % | 0.440 % | 4.605 % | 0.069 % |
| PI-2 | `R` | 70.030 % | 23.148 % | 1.220 % | 5.680 % | |
| PI-2 | `V` | 69.918 % | 23.324 % | 1.228 % | 5.608 % | 0.112 % |
| PI-3 | `R` | 98.758 % | 0.748 % | 0.060 % | 0.436 % | |
| PI-3 | `V` | 98.758 % | 0.753 % | 0.062 % | 0.428 % | 0.002 % |
| PI-4 | `R` | 98.917 % | 0.571 % | 0.005 % | 0.508 % | |
| PI-4 | `V` | 98.968 % | 0.575 % | 0.005 % | 0.453 % | 0.001 % |

Kept by one rule only (share of all pixels): PI-1 `R` only 0.285 %, `V` only 0.024 %; PI-2
0.186 % and 0.074 %; PI-3 0.0075 % and 0.0074 %; PI-4 0.0039 % and 0.055 %.

### No overlay figures

By the owner's decision of 17 November 2026 no figure of these images' masks is kept in this
repository: the report keeps only the numbers above and the images' SHA-256. The masks can be
regenerated from the images (identified by SHA-256) with the helper and the proof of concept.

## Why the two rules disagree (explained, not tuned)

Nothing was changed per image or after seeing these numbers. Each disagreement was traced by
recomputing one component with one difference between the rules removed at a time:

- **Nuclei (most of the disagreement on PI-1 and PI-2):** the proof of concept removes small
  pieces with scikit-image's `remove_small_objects` default, which is **4-connected**;
  `he_tissue_v1` uses **8-connected** pieces (ADR 0005 BM1). Pieces of nucleus touching only
  diagonally are therefore one piece in `V` and survive the 15 px limit more often, so `V`
  excludes slightly more (nuclei +0.24 and +0.18 points). Replacing the connectivity alone by
  4-connected makes the two nuclei components **identical** on all four images; the change of
  `H > 0.05` to `H >= 0.05` changes no pixel of them.
- **Background (most of the rest, and PI-4):** the pixel rules differ in their optical density:
  the proof of concept uses `-log10((I + 1) / 256)` on float32, `he_tissue_v1` normalization's
  `-log10(max(I, 1) / 255)` in float64, so pixels with every channel near 212 to 214 fall on
  different sides of 0.08 (0.15 % to 0.49 % of pixels before the clean-up). After the clean-up
  the background components differ on 0.007 % to 0.078 % of pixels; the remainder after aligning
  the pixel rule (up to 0.054 %, PI-1 and PI-4) comes from the clean-up: 4- versus 8-connected
  pieces at the 1000 px limit and, much less, the opening at the image border (SciPy's default
  erodes from outside the image, border value 0; `he_tissue_v1` clips the disk by the border).
- **Red cells:** saturation and 8-bit differences against OD ratios; under 0.009 % of pixels on
  every image.
- **`od_floored`:** pixels with a channel at 0 are 0.069 % (PI-1) and 0.112 % (PI-2) of the plane;
  the rule uses their floored OD as it is (design §1).

## Criterion 2's table: what the proof of concept decides

`V` gives exactly the components §1 specifies for every row (tested in
`tests/test_he_tissue_rule.py`). `R`'s pixel rules (before its clean-up) decide differently in
three rows:

| RGB | `he_tissue_v1` (nuclei, red cells, background) | proof of concept | Differs because |
| --- | --- | --- | --- |
| (200, 79, 159) | none | red cell | `OD_B - OD_R = 0.0996 < 0.10`; the PoC's `R - B = 41 > 40` |
| (3, 0, 0) | red cell | none | the PoC's `R - G = 3 <= 80` fails; the OD ratios with the floor pass |
| (212, 213, 213) | none | background | OD(212) = 0.0802 with `max(I, 1)/255`; the PoC's `(212 + 1)/256` gives 0.0798 |

The other rows of the table ((253, 101, 101) vs (253, 100, 100); (211, 84, 84) vs (211, 85, 85);
(200, 79, 158); (141, 50, 112) vs (141, 50, 113); (0, 0, 0); (2, 0, 0); (255, 0, 0); (213, 213,
213); pure white; saturated eosin pink (255, 20, 147)) are decided alike. In the first two pairs
the colour terms flip exactly at the boundary, but `E` is below 0.06 for all four pixels, so no
row of those pairs is a red cell under either rule.

## What remains before freezing

- **Criterion 3, not run:** the three public scanner sets (CMU-1 and Zenodo 4550300, Aperio;
  `Hamamatsu-2.ndpi`, Hamamatsu) need downloads and the tile selection rule of §1 (seeded, from
  tiles whose proof-of-concept tissue fraction is at least 0.25, at least four tiles per set),
  with the SHA-256 of each file checked against the design.
- The owner's review of this report and of criterion 3's; freezing then gives a new rule record
  (`status` `"frozen"`) and so new masks under new output mask IDs.
