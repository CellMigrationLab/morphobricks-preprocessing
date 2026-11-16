# Design: an opt-in H&E tissue mask helper

- **Status:** design (roadmap item I0, merged #312). **I1 implements it** with the rule
  `he_tissue_v1` **provisional** (not frozen); see [Implementation notes (I1)](#implementation-notes-i1)
  and the PI-1..PI-4 evaluation [he-tissue-mask-eval-pi.md](he-tissue-mask-eval-pi.md).
- **Owner answers (16 November 2026):** questions 1, 2, 3, 6, 8 and 9 accepted as recommended
  ([§8](#8-owner-decisions-and-open-questions)); questions 4, 5 and 7 decided after review w
  (§8). The Spec amendment of §6 is MorphoBricks-Spec #45; I1 waits for it to merge and for the
  other prerequisites of [§9](#9-prerequisites-for-i1).
- **Owner decision (16 November 2026):** an opt-in preprocessing helper, "make an H&E tissue mask",
  that leaves out nuclei, red blood cells and empty space. It works like the ADR 0005 conversion
  helpers, writes a region mask with its lineage, is declared by the user as a mask, is offered in
  the notebook, and reuses normalization's colour maths without copying it.
- **Read at:** preprocessing `69dc006` (main, after `ea0d7c7` = #308); MorphoBricks-Spec `bf68d30`;
  morphobricks-normalization `v0.7.0` = `c6a9f9d`; fiberfeatures `v0.6.2` = `752b1dd`. Line
  numbers below are from these commits unless another commit is named. Re-read for review w
  (16 November 2026): preprocessing main `37c583e` (v0.6.1: the per-image RH1 record),
  fiberfeatures main `36819a3`, CellPatcher main `0a08987`.

## Why

fiberfeatures measures fibre texture on the eosin map of an H&E image. The normalization preset
`he_eosin` recommends an upstream mask that removes nuclei, red cells and empty space
(`stain_presets.py:141`, `:149-157`, hint `exclude_nuclei_rbc_empty`). fiberfeatures builds no mask.
It only warns when none is given (`fiberfeatures rgb_run.py:117-124`, `docs/histology.md:129-141`).
Today no MorphoBricks brick makes this mask. The presentation proof of concept had to analyse whole
images, and nuclei and duct epithelium then scored as fibres.

## 1. The rule: `he_tissue_v1`

One fixed, named, versioned rule. The helper computes three excluded components on one RGB plane.
The mask keeps every pixel that is in none of them.

**Colour maths: called from normalization, never reimplemented.** The helper makes two calls per
image:

| What | normalization call (v0.7.0) | Units |
| --- | --- | --- |
| Eosin `E` and hematoxylin `H` | `result = stain.to_scalar(rgb, axes="CYX", preset="he_eosin")`; `E = result.image`, `H = result.exclusion` (`stain.py:335-412`; components 1 and 0 of `separate_stains(rgb, hed_from_rgb)`, `stain.py:168-173`) | `separate_stains` units, float32: `(ln(max(I/255, 1e-6)) / ln(1e-6)) @ hed_from_rgb`, clipped at 0, which is the log10 OD with a `1e-6` floor, divided by 6, then deconvolved (normalization `contract.md` §3.4). **Not** raw log10 OD, and not derived from the `OD` array below. |
| Per-channel optical density `OD_R, OD_G, OD_B` | `od, od_floored = optical_density.optical_density(scalar_inputs.rgb_yxc(rgb, "CYX"), optical_density.check_white((255, 255, 255)))` (`optical_density.py:251-260`, `:127-157`; `scalar_inputs.py:31-57`) | `od`: float64 `(Y, X, 3)`, `-log10(max(I, 1) / 255)`. `od_floored`: a Python `float`, the share of pixels with at least one channel at 0 (raised to 1) |

**`optical_density()` returns a pair.** In normalization v0.7.0 its signature is
`optical_density(rgb, white) -> tuple[np.ndarray, float]` (`optical_density.py:251`). The helper
unpacks it explicitly into `od` and `od_floored`, never indexes the tuple, and refuses (an
internal `TypeError`, a bug, never a silent fallback) a result that is not a 2-tuple of a float64
`(Y, X, 3)` array and a float. `OD_R, OD_G, OD_B` are `od[..., 0]`, `od[..., 1]`, `od[..., 2]`.
`od_floored` is recorded in the lineage `fractions` (§3) and shown on the preview. The E/H units
and the OD units differ (base, floor, scale and clipping), so no threshold is ever moved from one
to the other.

These are the same eosin and hematoxylin maps that fiberfeatures measures with `he_eosin`, so
the mask and the measured map agree. The white is fixed at 255 for every image, as
`separate_stains` already assumes. The helper does not estimate a white per image.

**Components** (`True` = excluded). The proof of concept's `scripts/tissue_mask.py` and its
recorded thresholds (presentation `results/run_record.json`) were the starting point only.
`he_tissue_v1` is defined **natively** on normalization's quantities (`E`, `H` and `OD` above). It
is a different rule: **no equivalence with the proof of concept is claimed**, for any component
(owner decision on Q4, 16 November 2026). The proof of concept's raw 8-bit RGB differences and
saturation are not reproduced.

| Component | Pixel rule (`he_tissue_v1`) | Clean-up (pixels) |
| --- | --- | --- |
| Nuclei | `H >= 0.05` | remove pieces under 15 px; then dilate by a disk of radius 2 |
| Red blood cells | `E >= 0.06` and `H < 0.03` and `OD_G - OD_R >= 0.40` and `OD_B - OD_R >= 0.10` | remove pieces under 15 px; then dilate by a disk of radius 1 |
| Background / empty space | `max(OD_R, OD_G, OD_B) < 0.08` (near-white in every channel) | opening with a disk of radius 2; then remove pieces under 1000 px (keep only lumens, fat, tears; not the gaps between fibres) |

`keep = not (nuclei or red_cells or background)`.

- **Exact comparisons.** Each comparison is applied as written (`>=` or `<`) to the values as
  normalization returns them (`E`, `H` float32; `OD` float64; the differences
  `od[..., 1] - od[..., 0]` and `od[..., 2] - od[..., 0]` computed in float64), with no rounding
  and no tolerance. A value exactly at a threshold is in the component for `>=` and out of it for
  `<`.
- **The red-cell colour terms in 8-bit values.** With `I' = max(I, 1)`, `OD_G - OD_R >= 0.40` is
  `log10(R' / G') >= 0.40` (`R' >= 2.5119 G'`) and `OD_B - OD_R >= 0.10` is
  `log10(R' / B') >= 0.10` (`R' >= 1.2589 B'`): ratios, not differences. No 8-bit pair lies on
  either boundary: the closest are `(R, G) = (211, 84)`, `3.2e-6` above 0.40, and
  `(R, B) = (141, 112)`, `1.1e-6` above 0.10, far above float64 rounding, so strictness and
  rounding cannot flip an 8-bit pixel. Examples: at `R = 253` the green limit is `G <= 100`
  (`G = 101` gives `0.3988`, not red-cell); at `R = 200` the blue limit is `B <= 158`; at
  `R = 100` it is `B <= 79`.
- **OD flooring.** `optical_density()` raises a channel at 0 to 1 before the log
  (`OD = log10(255) = 2.4065`) and returns the share of such pixels (`od_floored`, recorded,
  §3). The rule uses the floored values as they are and adds no special case: a pixel with
  `R = 0` has the largest `OD_R`, so both red-cell colour differences are `<= 0` and it is never
  red-cell; a pixel with `G = 0` or `B = 0` is treated as 1 there, so `(3, 0, 0)` meets both
  colour terms (`log10 3 = 0.477`) and `(2, 0, 0)` does not (`0.301`); the `E` and `H` terms
  then decide, on `separate_stains`' own floor (`1e-6`, not 1). Background is
  `max(OD) < 0.08`, which is every channel `>= 213` (`OD(213) = 0.0782`, `OD(212) = 0.0802`); a
  floored channel is never background.
- **Clean-up sizes.** "Remove pieces under N px" removes each 8-connected piece of **fewer than
  N** pixels; a piece of exactly N pixels is kept.
- **The numbers are provisional** (rule record `status` `"provisional"`, the card says
  "provisional rule") until a report meets the written acceptance criteria below and the owner
  accepts it. Freezing sets `status` to `"frozen"` (a new rule record, and so a new mask under a
  new output mask ID; see [the canonical rule record](#the-canonical-rule-record)); changing any
  number instead gives `he_tissue_v2`.
- Clean-up is done with SciPy (`scipy.ndimage`), which is already a core dependency
  (`pyproject.toml`). Pieces are 8-connected, as everywhere else (ADR 0005 BM1). A disk of
  radius `r` is `x² + y² <= r²`. The clean-up is part of the rule, not colour maths. It belongs
  to preprocessing.
- **All sizes are in pixels.** No pixel size is read, guessed or used. The image's recorded
  pixel size, when the run has one (`RunInputs.pixel_sizes`, `run_inputs.py:291-299`), is copied
  into the lineage for information only. The sizes were set on the proof-of-concept images,
  whose magnification was not recorded. The card says that sizes are in pixels (owner decision,
  16 November 2026).

**Acceptance criteria before freezing `he_tissue_v1`** (owner decisions on Q4 and on the
thresholds, 16 November 2026). The thresholds below are fixed now, before any evaluation is run,
and are not changed after results are seen (Spec BM15 "Acceptance before freezing"; review w2
N2). One report, reviewed by the owner, shows all of the following, and the rule is accepted only
when every numbered criterion passes.

**The per-image bar.** For an image or tile, let `R` be the pixels the proof-of-concept rule keeps
(its `scripts/tissue_mask.py` with the thresholds recorded in the presentation
`results/run_record.json`, run unchanged on the same pixels) and `V` the pixels `he_tissue_v1`
keeps. The image meets the bar when **both**:

- **Dice ≥ 0.90:** `2 |R ∩ V| / (|R| + |V|) >= 0.90`;
- **discordant ≤ 5 % of tissue pixels:** `|R xor V| / |R| <= 0.05`, where the tissue pixels are
  `R`, the proof-of-concept kept pixels, and the discordant pixels are those kept by exactly one
  of the two rules.

Both are computed on the full plane, without rounding before the comparison; an image with
`|R| = 0` fails. No other tolerance applies.

1. **The four presentation images each meet the per-image bar.** For each image: the
   proof-of-concept and `he_tissue_v1` masks side by side and as a difference overlay; per
   component and for `keep`, both shares; Dice and the discordant fraction; pass or fail.
   Disagreements are explained, never tuned away per image. The images are identified by file
   name and the SHA-256 of the file's bytes (owner decision, 16 November 2026: these are the four
   WebP files the owner shared for the presentation; WebP is lossy, so the reference is the
   decoded pixels of exactly these bytes). The owner holds the images; they are not published:

   | ID | File name (presentation folder) | SHA-256 |
   | --- | --- | --- |
   | PI-1 | `1.webp` | `3a074fb849fa8901e1381d479f69427339ab4ac6e42217b0cc450c48f5fcd8e7` |
   | PI-2 | `2.webp` | `5725d0584925126faa2c1e70d62e2450815df4a02a65cca7a9f7e219c0a45f69` |
   | PI-3 | `3.webp` | `518640cae5220da0a4372ed34df6b13c5d47912a7a696d6969a45886a7adef59` |
   | PI-4 | `4.webp` | `dda0260384b6d590d3403be6ed436af030faf27cae38c56fec56d68f45ef07d2` |

   The report recomputes each SHA-256 and refuses an image whose bytes differ.
2. **Hard RGB boundary cases.** A synthetic table, each pixel with its expected component and
   checked by a test: both sides of each colour boundary (`(253, 101, 101)` vs `(253, 100, 100)`;
   `(211, 84, 84)` vs `(211, 85, 85)`; `(200, 79, 158)` vs `(200, 79, 159)`; `(141, 50, 112)` vs
   `(141, 50, 113)`), the floored cases above (`(0, 0, 0)`, `(3, 0, 0)`, `(2, 0, 0)`, `(255, 0,
   0)`), the background edge (`(213, 213, 213)` vs `(212, 213, 213)`), pure white and saturated
   eosin pink; and for each, what the proof of concept decides, where it differs. **Pass:**
   every listed case gives exactly the component that §1's comparisons specify for it; one
   mismatch fails the criterion.
3. **At least two scanner vendors (owner decision, 16 November 2026: "another public
   vendor").** Besides the presentation images, three public scanner sets, two of them from
   Aperio and one from a different vendor, Hamamatsu, with fixed tiles chosen and listed (slide,
   level, x, y, width, height) before the rule is run, overlays and per-component shares per
   tile, and every failure mode named (for example faint eosin, pen marks, folds). **At least
   four tiles per set** (owner decision, 16 November 2026), chosen before evaluation by a written
   rule: tiles drawn uniformly at random, with a seed recorded in the report, from the tiles of a
   fixed level whose proof-of-concept tissue fraction is at least 0.25, so no tile is picked by
   looking at `V`. **Pass:** in
   each set, every selected tile meets the per-image bar above, with the proof-of-concept rule
   run on the same tile as `R`. Acquisition
   provenance, as the sources state it (review w2 N1; Spec BM15 "Acceptance before freezing"):

   | Set | Vendor, model | Stain | Source | Licence |
   | --- | --- | --- | --- | --- |
   | A. `CMU-1.svs` | Aperio (model not stated by the source) | not stated in OpenSlide's index | OpenSlide test data, `https://openslide.cs.cmu.edu/download/openslide-testdata/Aperio/CMU-1.svs`, SHA-256 `00a3d54482cd707abf254fe69dccc8d06b8ff757a1663f1290c23418c480eb30` | CC0-1.0 (OpenSlide `index.json`) |
   | B. Zenodo 4550300 bright-field TMA cores | Aperio CS2 Digital Pathology Scanner (Leica Biosystems), 40x, per the source paper (Keikhosravi et al. 2020, *Commun. Biol.* 3:414, doi 10.1038/s42003-020-01151-5, "Imaging systems"); not in the record's metadata | H&E (source paper) | `https://zenodo.org/records/4550300` | CC-BY-4.0 (Zenodo record) |
   | C. `Hamamatsu-2.ndpi` | Hamamatsu NanoZoomer S20MD (file header `Product=C16300-21MDEU`), 2026 | H&E (OpenSlide `index.json`) | OpenSlide test data, `https://openslide.cs.cmu.edu/download/openslide-testdata/Hamamatsu/Hamamatsu-2.ndpi`, SHA-256 `76ae0bc685c177bf008ad9d8a5aa7af85df4f257bb328c654825b5d9a7868396`, credit Hamamatsu | CC0-1.0 (OpenSlide `index.json`) |

   A and B count as one vendor (Aperio); C is the second vendor. The report checks each file's
   SHA-256 against this table before use. If set C becomes unavailable, its replacement is
   another public H&E slide from a vendor other than Aperio, with the same provenance columns,
   chosen before the rule is run.

Until that report is accepted the numbers above are named provisional everywhere: in the rule
record, the lineage, the card and the documentation.

**Fixed defaults, not user thresholds (owner decision, 16 November 2026).** v1 offers one preset, `he_tissue_v1`, and
no per-call overrides. Changing any number gives a new preset ID (`he_tissue_v2`) and never
changes v1. The complete rule record (below) and its SHA-256 are written to the lineage. Nothing is
tuned per image, and nothing is tuned silently. The preview and the "% kept" figure are how the
user judges whether the rule suits their slides.

### The canonical rule record

The rule is defined once, as an immutable record in the helper's module, and written whole into
every lineage line with its SHA-256 (Spec BM15 "The rule record"; owner decision on Q7). Nothing
in it is read from the image, the run or the caller. The one value taken at import is the HED
matrix's hash: the helper hashes the matrix normalization passes to `separate_stains`, looked up
by the preset's own name for it (`PRESETS["he_eosin"].matrix` = `"hed_from_rgb"`,
`stain_presets.py:153`; `stain.py:168-170`) as float64, little-endian, C order, and a
scikit-image whose matrix differs gives another record and so never matches on retry.

```json
{
  "id": "he_tissue", "version": 1, "status": "provisional",
  "input": {"image_type": "rgb", "dtype": "uint8", "white": [255, 255, 255]},
  "colour": {
    "stain_preset": "he_eosin", "stain_preset_version": 1,
    "hed_matrix": {"source": "skimage.color.hed_from_rgb", "sha256": "<64 hex>"},
    "E": "to_scalar(...).image (separate_stains component 1)",
    "H": "to_scalar(...).exclusion (separate_stains component 0)",
    "optical_density": {"function": "morphobricks_normalization.optical_density.optical_density", "floor": 1, "white": [255, 255, 255]}
  },
  "connectivity": 8,
  "disk": "x*x + y*y <= r*r",
  "components": [
    {"name": "nuclei", "pixel": [["H", ">=", 0.05]],
     "cleanup": [["remove_pieces_smaller_than_px", 15], ["dilate_disk_radius_px", 2]]},
    {"name": "red_cells", "pixel": [["E", ">=", 0.06], ["H", "<", 0.03], ["OD_G-OD_R", ">=", 0.40], ["OD_B-OD_R", ">=", 0.10]],
     "cleanup": [["remove_pieces_smaller_than_px", 15], ["dilate_disk_radius_px", 1]]},
    {"name": "background", "pixel": [["max(OD_R,OD_G,OD_B)", "<", 0.08]],
     "cleanup": [["open_disk_radius_px", 2], ["remove_pieces_smaller_than_px", 1000]]}
  ],
  "combine": "keep = not (nuclei or red_cells or background)"
}
```

The order of `components`, of each `pixel` list (all comparisons joined by "and") and of each
`cleanup` list is the order of computation. `status` is `"provisional"` until the acceptance
report of [Q4](#8-owner-decisions-and-open-questions) is accepted; freezing changes it to
`"frozen"`, which is a different record, so no provisional mask is ever reused as a frozen one.

**Freezing makes a new mask, never a relabelled one** (Spec BM15 "Freezing a rule makes a new
mask"; review w2 N3). `status` is in the hashed rule record, so a user who wants the frozen rule
on an image that already has a provisional mask: (1) runs the helper with the frozen rule under a
**new output mask ID** (for example `HETissueFrozen`), (2) declares that new output
`representation = "binary"`, and (3) runs the mask step again before any brick reads it. The
provisional mask file and its lineage line stay as they are: never overwritten, renamed,
re-declared as frozen or edited in place. A call with the frozen rule under the provisional
mask's ID is a collision and is refused by the retry comparator (§3); the refusal message names
the new-ID route. A test in the I1 PR covers this (§7).

## 2. Inputs and refusals

API, a mixin on `RunInputs` like `ConversionInputs` (`mask_conversion.py:238-251`):

```python
RunInputs.he_tissue_mask(fov, *, output_mask_id, rule="he_tissue_v1") -> TissueMaskResult
RunInputs.preview_he_tissue_mask(fov, *, rule="he_tissue_v1") -> TissueMaskPreview  # writes nothing
```

The input is a typed context, not a path: an opened clean run plus one position, given as a
`Fov` or its image path.

**The FOV is resolved from the opened run before any read (Spec BM15).** The helper takes only
the image path from its argument (`fov.relative_path`, or the string) and looks the position up
in the run's own authoritative description: the `Fov` of `RunInputs.fovs()` with that path,
which carries the published description's `image_type` (`run_inputs.py:276-284` at `37c583e`).
It never uses `RunInputs._fov(entry)` for this: that one is built without the description, so its
`image_type` is `None` (`run_inputs.py:379-382` at `37c583e`). Every check below and every
lineage value use the resolved record only. An `image_type`, `axes`, `z_count`, `t_count`,
`c_count` or `dtype` on a caller's `Fov` (a `Fov` "built by hand" may carry anything,
`run_inputs.py:146-150`) is ignored: never trusted, never compared, never used to fill a gap.
RH1 is copied per image from the run's description record: the unit's declared
`rgb_header_override` and, when declared, that image's item of `rgb_header_override_images`
(`override`, `unused_reason`, `header_photometric`; `description_record.py:27-30`, `:104-123` at
`37c583e`).

Before any pixel is read, the helper refuses each of the following with a `ValueError` naming the
position and the reason:

| Refused | Why |
| --- | --- |
| An image path that is not an image of the run | The FOV is resolved from the run only (`RunInputs._entry`, `run_inputs.py:384-390` at `37c583e`) |
| A position whose unit is not declared `image_type = "rgb"` (`Fov.image_type`, `run_inputs.py:146-150`): "`{image}` is declared channels; the H&E tissue mask takes a unit declared image_type = "rgb"" | RGB is declared, never inferred (experiment-v1 §4.4b). RH1 is honoured as recorded: a unit declared `rgb` with `rgb_header_override = true` that the run accepted is valid input. The helper never reads the header itself and never decides the override again. The unit's declared key and that image's recorded override item are copied into the lineage unchanged. |
| More than one Z plane; more than one time point (the run's `z_count`, `t_count`) | One 2-D plane only, as ADR 0005 BM5 does for conversions |
| A pixel type other than `uint8`, or a C count other than 3 (the run's `dtype`, `c_count`) | `to_scalar` accepts 8-bit sRGB only (`scalar_inputs.py:52-56`). The refusal comes first, with the helper's own message. |
| A run without a masks folder (`masks_root` is None) | Outputs only go to the masks folder (ADR 0005 §"Outputs") |
| An unknown rule ID | Lists the known rule IDs |
| scikit-image is missing | `ImportError` naming the extra `pip install "morphobricks-preprocessing[he-mask]"`. That extra pulls in `morphobricks-normalization[histology]`. Core import stays inert: scikit-image, `stain` and the helper's module are imported inside the call, never at package or CLI import. |
| An output mask ID that breaks experiment-v1 §3.2, is already declared, differs only in case from a declared mask ID or source type, names a type folder that holds files this helper did not write, or names an existing file that is not this same computation | Reuses `_check_output_id`, `_check_case_collision` and the foreign-file and existing-file checks (`mask_conversion.py:389-409`, `:532-566`, `:493-513`); the case-collision refusal is #308's fix `03fa90e` |

The helper reads the plane with `RunInputs.read_plane(fov)`. That returns `(3, Y, X)` R, G, B and
refuses an image that changed since the run (`run_inputs.py:301-335`). The helper then checks
the image fingerprint again after the read, as `_read_source` does (`mask_conversion.py:441-446`).

## 3. Output

- **Representation:** one `uint8` `(Y, X)` file holding `1` = tissue kept and `0` = excluded. It
  is declared `binary` and used as a **region**: the region is `mask != 0` (ADR 0005 BM3,
  `regions.check_region`, `regions.py:86-130`). An all-zero result is written as computed. It is
  a valid `binary` file (BM11). Downstream it must give `empty_region`, never 0: that is a
  consumer guarantee this helper cannot give itself, so it is gate **P3** of
  [§9](#9-prerequisites-for-i1). The card warns when the kept share is 0 %.
- **Layout:** the same as the conversion helpers (`mask_conversion.py:20-24`, `:408-409`):
  `<masks folder>/<output mask ID>/<the image's folders>/<position ID>.tif`, plus one
  `mask_lineage.jsonl` per type folder. Example: image `CTRL/rep1/slide1.tif` gives
  `HETissue/CTRL/rep1/slide1.tif`. The source image is never touched.
- **Publication:** `mask_lineage.publish_output` is reused unchanged. It writes a temporary file,
  re-reads the digest, writes the line, then renames. A crash leaves a pending line, which is
  rolled back on retry (`mask_lineage.py:12-20`, `:199-215`, `:232-282`). Mask discovery already
  skips the lineage file and the temporary files (`mask_lineage.py:69-71`, `masks/files.py:197`).
  `ConversionInputs._publish` is **not** reused: it builds a BM12 `ConvertedMask` from
  `record["class_value"]` (`mask_conversion.py:473-491`, `:489`), a key a BM15 line does not
  have, and decides reuse with BM12's `_published_before` / `_same_conversion`. The helper calls
  `roll_back_pending`, `publish_output` and `published_lines` directly, with its own result
  builder (`TissueMaskResult`: output ID, path, the line's text, `published`, `reusable`,
  `fractions`) and BM15's own comparator below.
- **Idempotent retry, with BM15's own comparator** (Spec BM15 "Retry comparator"; it is not
  BM12's `_same_conversion`, `mask_conversion.py:579-589`). An existing output is the same
  computation, and the call is a no-op that returns it, only when both lines have `reusable`
  `true`, they are equal in every key except `output.content_sha256` and `fractions`, and the
  existing file still has the recorded digest. Any difference is refused, never overwritten. That
  includes equal pixels from another source, another code identity, another NumPy, scikit-image
  or SciPy version, another rule record (provisional against frozen included), and any line with
  `reusable` `false`.
- **Code identity (Spec BM15, owner decision on Q7; review w2 B1).** For
  `morphobricks-preprocessing` and `morphobricks-normalization` the helper records
  `{"distribution", "version", "verified", "record_sha256", "vcs_commit", "reason"}` (keys exactly
  these, in this order, all always present). `record_sha256`, the installed-artifact digest, is
  **always** recorded for a verified identity: the lower-case hex SHA-256 of the exact bytes of
  the installed `<name>-<version>.dist-info/RECORD` (read as bytes, not parsed first;
  `importlib.metadata.distribution(name).read_text` is not used because it decodes). `vcs_commit`
  is PEP 610 `direct_url.json` `vcs_info.commit_id` when present, else `null`, and is extra
  provenance only. Once per process, after the helper has imported every module it uses from
  both distributions, the identity is verified only when: the install is not editable (PEP 610
  `dir_info.editable`); `RECORD` is present and readable; every file `RECORD` lists with a hash
  still has it (`distribution(name).files`, each `PackagePath.hash` checked); every `.py` and
  extension-module file (`.so`, `.pyd`) on disk under the distribution's import packages, outside
  `__pycache__`, is in `RECORD` with a hash; and every `sys.modules` entry of those packages has a
  `__file__` whose `os.path.realpath` is that of a hashed `RECORD` entry
  (`distribution(name).locate_file(path)`), so a source tree or another installation earlier on
  `sys.path` is never taken for this one. Otherwise `verified` is `false` and `reason` names the
  first failed check (for example `"editable install"`, `"RECORD missing"`, `"hash differs:
  <file>"`, `"not in RECORD: <file>"`, `"no hash in RECORD: <file>"`, `"module not from this
  installation: <module>"`). Then the mask is still written, `reusable` is `false`, the result and
  the card say "this mask will not be reused on retry", and a retry is refused as a collision.
  The retry comparator compares `code` in every key, so an equal `vcs_commit` with another
  `record_sha256` is another identity and is refused. Reinstalling the same wheel can change
  `RECORD` (installer files, `direct_url.json`); that refuses reuse, which is the safe side.
- **Declaring it** (experiment-v1 §4.5; no new key):

  ```toml
  [masks.HETissue]
  source_type = "HETissue"
  representation = "binary"
  source_channels = ["R", "G", "B"]   # provenance only
  display_name = "H&E tissue (he_tissue_v1)"
  ```

  Then run preprocessing and the mask step again. Every brick can now request it as a region,
  for example fiberfeatures `MaskRequest("HETissue", "region")` (`run_request.py:165`), which also
  stops the `he_eosin` warning. A mask with files for only some positions is valid (§4.5).
- **Lineage line** (one per output, keys in this order, exactly as Spec BM15):

  | Key | Value |
  | --- | --- |
  | `source` | `{"root": "experiment", "image": <image path relative to the experiment folder>, "axes": "CYX", "shape": [3, Y, X], "dtype": "\|u1", "content_sha256": <the ADR 0005 canonical digest of the pixels read, mask_lineage.content_sha256>}` |
  | `output` | the ADR 0005 `FILE_KEYS` object (`root` `"masks"`, `mask_id`, `path`, `axes` `"YX"`, `shape`, `dtype` `"\|u1"`, `content_sha256`) |
  | `operation` | `"he_tissue_mask"` |
  | `rule` | the canonical rule record ([§1](#the-canonical-rule-record)) |
  | `rule_sha256` | SHA-256 of `json.dumps(rule, sort_keys=True, separators=(",", ":")).encode("utf-8")` |
  | `code` | `{"preprocessing": <code identity>, "normalization": <code identity>}` |
  | `reusable` | `true` when both code identities are verified |
  | `numpy_version`, `scikit_image_version`, `scipy_version` | as imported |
  | `image_type` | `"rgb"`, from the resolved FOV |
  | `rgb_header_override` | `{"declared": true or false, "image": <that image's recorded item, or null>}` |
  | `pixel_size` | the recorded X/Y size and unit, or `null` (information only) |
  | `fractions` | `kept`, `nuclei`, `red_cells`, `background` of all pixels (the components can overlap), and `od_floored` (normalization's `floored_fraction`) |
  | `helper`, `helper_version` | `morphobricks_preprocessing.RunInputs.he_tissue_mask`, `__version__` |

  The current reader accepts only the BM12 keys. It refuses any other key order, and any `root`
  other than `"masks"` (`mask_lineage.py:44-53`, `:151-161`). The BM12 reader is extended to
  validate this second schema when `operation` is `"he_tissue_mask"`. This matters before the
  user declares the output: `unrecorded.py:108` uses the lineage to keep undeclared helper outputs
  out of the "unrecorded files" refusal, and an unreadable line there counts for nothing
  (`mask_lineage.py:189-192`).

## 4. What it is not

- It is not a trained segmentation and not a nucleus segmentation for object features. The
  nucleus component is a stain threshold, used only to leave pixels out.
- It is never an object set. It is declared `binary`, so a request for it as objects is
  `A_MASK_NOT_INSTANCES` unless the user asks for an explicit conversion (experiment-v1 §4.5c).
  No consumer may take cells from it when no objects mask is given (gate **P3**, §9). In memory
  this cannot be enforced by the file: the output is `uint8` 0/1, and an in-memory object set is
  any plain integer array (ADR 0005 BM4), so a caller who reads the file and passes the array as
  labels gets one object, label 1 (§9, P3 finding).
- It never runs by default. Nothing is computed unless the user calls the helper or clicks the
  card. A run, the mask step and every brick behave as before when the helper is not used.
- It changes no normalization number and no image. It writes a new mask file, nothing else.

## 5. Notebook card

**Make an H&E tissue mask (optional)** is a card beside **Convert a mask**, under **Describe
the masks** (`notebook_mask_step.py:106-113`). It is shown once the output folder holds a run with
at least one unit declared `rgb`.

1. **Choose:** a list of RGB positions, grouped by unit, with "all of this unit". Positions that
   cannot be used are listed greyed, each with its reason (Z or T stack, not 8-bit).
2. **Preview:** one chosen image, computed in memory with `preview_he_tissue_mask`. The H&E image
   is shown with each excluded component tinted (nuclei blue, red cells orange, background grey,
   the proof of concept's colours). Below it are the kept share and each component's share
   ("kept 69 % · nuclei 25.7 % · red cells 0.4 % · empty 4.6 %"). A note says "Sizes are in
   pixels. Fixed rule he_tissue_v1 (provisional), not a trained segmentation" while the rule
   record's `status` is `"provisional"`, and "this mask will not be reused on retry" when a code
   identity is unverified (§3). The display may be downsampled;
   the numbers come from the full-resolution mask.
3. **New mask ID:** typed by the user, never proposed (as **Convert a mask** does).
4. **Write:** writes every chosen position, and only on this click. It then shows the files
   written, the retries that changed nothing, and the next steps (declare the mask, run again,
   then **Describe the masks**), as `conversion_next_steps` does (`mask_conversion.py:193-207`).
5. **Disabled actions name their reason**, as `_update_button` does
   (`notebook_mask_conversion.py:152-163`). The reasons are: no run in the output folder, no unit
   declared rgb, no masks folder, no position chosen, the mask ID is missing, invalid, declared or
   case-colliding, and scikit-image is not installed (the message names the extra). A refusal
   during the write is shown as a red card naming the position.

## 6. Spec impact

**An amendment is needed.** ADR 0005 BM12 covers the mask-to-mask conversion only. It names
exactly two helpers and two operations, says that the lineage keys are "exactly these", and says
that `root` is `"masks"` only, with readers refusing anything else (ADR 0005 lines 503-519 and
choice 15, lines 692-693). A tissue mask's source is an image under the experiment folder, and it
carries a rule and thresholds. experiment-v1 needs no change: the output is declared with existing
§4.5 keys.

The normative text is ADR 0005 "Derived region masks (BM15)" in MorphoBricks-Spec #45 (it
replaced the draft that stood here, so the two cannot drift). In short:

- **Decision row BM15:** an opt-in preprocessing helper computes a `binary` region from one
  declared image with a named, versioned, fixed rule whose colour maths is normalization's.
- **The FOV is the run's:** resolved by image path from the opened run before any read; a
  caller's `image_type`, axes, counts and dtype are ignored; RH1 is copied per image.
- **Lineage:** its own schema in the same `mask_lineage.jsonl`, chosen by `operation`; the rule's
  complete canonical record and its SHA-256; verified code identities of preprocessing and
  normalization; NumPy, scikit-image and SciPy versions; `reusable`.
- **Retry:** BM15's own comparator; an unverified code identity is never reused.
- **Choice 15, amended:** a BM15 `source` has the `root` `"experiment"`.
- **Amendments:** none to experiment-v1, the fixtures, the goldens or `tools/audit/refval.py`.

## 7. Tests (I1)

- **Synthetic H&E:** a 96×96 `uint8` RGB image built from known OD vectors. It has an
  eosin-pink field, a hematoxylin disc (nucleus), a saturated red disc (red cell), a 40×40
  near-white block (1600 px: background) and a 30×30 one (900 px: kept, under the 1000 px
  limit), plus a 3×3 hematoxylin speck (under 15 px: kept). The expected components, the
  dilation radii and the opening are checked pixel by pixel, as is the written file.
- **Exact reuse:** the helper's `E`, `H` and `OD` (exposed on `TissueMaskPreview`) are
  `np.array_equal` to calling `stain.to_scalar(..., preset="he_eosin")` (`.image`, `.exclusion`)
  and `optical_density.optical_density(...)` directly. For the OD the test unpacks
  `od, floored = optical_density(...)` and compares **only the array** `od` with
  `np.array_equal`; `floored` is compared on its own with the recorded `fractions.od_floored`
  (`==`, a float from the same call). A test image with a channel at 0 checks `od_floored > 0`
  and that the floored pixels have OD `log10(255)`. A source check shows the module contains no
  `log10`, `log(`, `separate_stains` or `rgb2hed`; the HED matrix is looked up only by the name
  the preset gives (`PRESETS["he_eosin"].matrix`, `stain_presets.py:153`) and only to hash it for
  the rule record.
- **Morphology boundaries (review w F5):** two nucleus pieces touching only diagonally are one
  8-connected piece (kept or removed together); a piece touching the image border is measured and
  dilated as clipped by the border, never padded or wrapped; pieces of exactly 14, 15 and 16 px
  (nuclei, red cells) and 999, 1000 and 1001 px (background, after the opening) check "fewer than
  N is removed, exactly N is kept"; the dilation disks of radius 1 and 2 and the opening disk of
  radius 2 match `x*x + y*y <= r*r` pixel by pixel; the clean-up order is checked by a case whose
  result differs if two steps are swapped.
- **Red-cell boundaries (B4):** every pixel of the §1 boundary table gives its expected component,
  including the floored cases and the background edge `(213, 213, 213)` / `(212, 213, 213)`.
- **Refusals:** each row of §2 (channels unit, Z = 2, T = 2, `uint16`, C = 4, no masks folder,
  unknown rule, missing scikit-image via a blocked import, each output-ID case incl. case
  collision with a declared ID and with a source type, foreign file, existing file, an image path
  not in the run), each checked to write nothing and to read no pixel.
- **The FOV from the run (B5):** a hand-built `Fov` with the right path but `image_type =
  "channels"`, wrong axes, counts or dtype gives exactly the result of the run's own `Fov` (the
  attributes are ignored); a hand-built `rgb` `Fov` for a position the run declares `channels` is
  refused; the lineage `rgb_header_override` equals the run's per-image record for a used, an
  unused (`header_already_rgb`) and an undeclared override.
- **Code identity (B1, review w2 B1/Q7):** a verified install records `verified` `true`, its
  `record_sha256` (checked against `hashlib.sha256` of the `RECORD` bytes) and `vcs_commit` when
  `direct_url.json` has one, and `reusable` `true`. Each of these records `verified` `false` with
  its reason and `reusable` `false`, the file is still written, and a retry is refused (never a
  no-op): an editable install; a missing `RECORD`; a file whose hash differs; a `.py` file added
  to the installed package but absent from `RECORD`; a `.py` file listed in `RECORD` without a
  hash; and a source tree placed earlier on `sys.path`, so an imported module's `__file__` is not
  in the installation. **Same commit, different build:** two non-editable installs, each
  matching its own `RECORD` and recording the same `vcs_commit`, whose `RECORD` bytes differ (for
  example a wheel rebuilt with one more module); the mask written under the first install is
  refused, never reused, by a retry under the second, and the refusal names `code`. The rule record's `rule_sha256` is recomputed from the line;
  a changed HED matrix hash, rule `status`, NumPy, scikit-image or SciPy version is refused on
  retry. **Freezing (review w2 N3):** with a provisional mask published, a call with the frozen
  record under the same mask ID is refused and leaves the file, its digest and its lineage line
  byte for byte unchanged; the same call under a new mask ID writes a second file and a second
  line, and the provisional ones are unchanged.
- **Lineage round trip:** write, re-read the line with the extended reader, check every key and
  the digests; a retry is a no-op (`published` False); a changed rule record, image or library
  version is refused; a pending line (crash between line and rename) is rolled back; BM12 lines
  in other folders still read; `RunInputs.open` still succeeds before the output is declared
  (`unrecorded.py:108`).
- **BM12 reader after the dispatch (review w F1):** with the reader now selecting the schema by
  `operation`, BM12 lines are still refused for: a missing key, an extra key (a BM15 key such as
  `rule` included), keys out of order, a duplicate key in one line, a `root` other than
  `"masks"` (`"experiment"` included) in `source` or `output`, an unknown `operation`, and a
  BM15 line carrying BM12's `connectivity` or `class_value`. All but one are the same refusal
  as on `main`, and the existing BM12 tests pass unchanged. The exception is duplicates: today
  `json.loads` keeps the last of two equal keys, so a line whose last key (`helper_version`) is
  repeated is read silently with its second value (`mask_lineage.py:148-151`). The extended
  reader parses with an `object_pairs_hook` that refuses a duplicate key at any depth, in BM12
  and BM15 lines alike (ADR 0005: "keys exactly these"); this is a new refusal, tested on its
  own.
- **Declared and used:** declare the output `binary`, run the mask step (it passes BM11), read it
  with `check_region`, and refuse it as objects with `A_MASK_NOT_INSTANCES`.
- **Untouched runs and a core-only install (review w F4):** in a subprocess with a core-only
  install (no `he-mask` extra, scikit-image absent, enforced by an import blocker),
  `import morphobricks_preprocessing`, `RunInputs.open` and the CLI's `--help` and every other
  command succeed and leave `skimage`, `morphobricks_normalization.stain` and the helper's module
  out of `sys.modules`; the helper, the preview and `tissue-mask` each fail with the `ImportError`
  naming `pip install "morphobricks-preprocessing[he-mask]"` and write nothing. The `tissue-mask`
  CLI path is tested end to end (arguments, exit codes, the files and line written, a no-op
  retry, a refusal's message and exit code). The existing run and golden tests pass byte for
  byte unchanged. A run that never calls the helper writes the same files as `main`.
- **Notebook:** the card's disabled reasons, the preview writing nothing, and the write happening
  only on click.

## 8. Owner decisions and open questions

**Decided by the owner on 16 November 2026 (accepted as recommended):**

- **Fixed rule (former question 1).** `he_tissue_v1` is fixed and versioned, with no per-call
  overrides. A user rule set, if ever added, gets its own recorded ID.
- **White reference (former question 2).** The white is fixed at 255, the same as `he_eosin`. A
  declared-white variant would be a new rule ID.
- **Sizes (former question 3).** Clean-up sizes are in pixels in v1 and recorded as pixels.
- **Optional dependency (former question 6).** A new extra `he-mask` pulls in
  `morphobricks-normalization[histology]`. Core preprocessing stays without scikit-image, and the
  card and the command line name the extra when it is missing.
- **Command line (former question 8).** A `tissue-mask` command calls the same functions, like
  `convert-mask`.
- **One file only (former question 9).** The helper writes one tissue mask file. Nuclei, red
  cells and background are shown in the preview only.

**Decided after review w (16 November 2026):**

- **Q4 (decided 16 November 2026, after review w).** The red-cell rule is defined natively in OD
  terms, with explicit strictness and OD flooring (§1), and no equivalence with the proof of
  concept is claimed. Written acceptance criteria come first (§1: the four presentation images,
  hard RGB boundary cases, at least two scanner vendors: CMU-1 and Zenodo 4550300, both
  Aperio, and OpenSlide `Hamamatsu-2.ndpi`, Hamamatsu; review w2 N1). The
  numbers stay provisional, and are named so, until a report meets them.
- **Q5 (decided 16 November 2026).** `optical_density.optical_density`, `check_white` and
  `scalar_inputs.rgb_yxc` are documented as stable normalization APIs, in a documentation-only
  normalization PR, as a prerequisite for I1 (P2, §9), not for the Spec merge. This design
  changes nothing in normalization.
- **Q7 (decided 16 November 2026, after review w).** A null commit is not accepted. The lineage
  records a verified code identity for normalization and preprocessing: always the installed
  distribution's `RECORD` digest, with the commit as extra provenance when recorded (review w2
  B1), the NumPy and scikit-image versions, and the complete rule
  record with its hash. An unverified identity still writes the mask, marked never to be reused
  on retry. BM15 has its own retry comparator (§3).

## 9. Prerequisites for I1

I1 (the helper) starts only when P1 and P2 hold, and its PR merges only when P3 and P4 hold too:

- **P1. Spec:** MorphoBricks-Spec #45 (ADR 0005 BM15) is merged.
- **P2. Stable normalization APIs (owner decision on Q5; review w F3).** A documentation-only
  normalization PR names `optical_density.optical_density(rgb, white) -> tuple[np.ndarray,
  float]`, `optical_density.check_white(white) -> np.ndarray` and `scalar_inputs.rgb_yxc(pixels,
  axes) -> np.ndarray` as stable API in its `contract.md` (signatures, return values including
  the `(od, floored_fraction)` pair, units, floor, refusals), and I1 pins the release that
  contains it. It is a prerequisite for I1, not for the Spec merge. Not done in this PR.
- **P3. Downstream consumer gate (review w B3).** The consumers the card and §3 name must, at the
  commits the I1 PR pins and cites, (a) give `empty_region` for an all-zero region: the image is
  `valid_empty` with reason `empty_region`, no patch is made in it, its values are undefined
  (never 0) and the run goes on; and (b) never take cells from a binary region when no objects
  mask is given: cell mode without an `objects` mask is refused before any read, naming the
  region. I1 adds one cross-repo test per guarantee, run with the consumer installed, and its
  report lists every skip; a skipped gate test is not a pass. Findings of 16 November 2026:
  - **fiberfeatures main `36819a3`** (#223; `pyproject.toml` still says `0.6.2` and there is no
    `v0.7.0` tag yet, so I1 pins this commit or the first release that contains it; the pinned
    `v0.6.2` raised on an empty region):
    - (a) holds. `batch.py:214-219` makes no patch in an empty analysis region, whatever the
      mode; `batch.py:292-293` gives `valid_empty` / `empty_region` whenever the mask has no
      pixel; `run_rows.py:193-195` returns the empty mask unrefused; the MorphoFeature patch
      route gives NaN with `empty_region` (`morphofeature_patches.py:25`, `:418`, `:535`). Tests:
      `tests/test_binary_mask_policy.py:226-249` (F06), `:326-350`, `:354-386`.
    - (b) holds in the described-run path, the only path fiberfeatures reads masks by:
      `run_request.py:275-296` (`refuse_region_as_cells`, called at `run_rows.py:124` before the
      request is resolved) refuses CellPatcher cell mode without an `objects` mask and names the
      region; `batch.py:60-65` refuses again before `run_patching`; `run_rows.py:200-205` refuses
      a `labels_file` or `mask_file` column. Tests: `tests/test_binary_mask_policy.py:170-187`
      (F03 b), `tests/test_described_runs.py:209-222`.
  - **CellPatcher main `0a08987`:**
    - (b) holds in a described file run: cell mode asks the resolver for the mask as `objects`
      (`file_runs.py:252-256`, `:272-295`), so a mask declared `binary` is
      `A_MASK_NOT_INSTANCES` unless the user sets `cell.binary_mask_policy =
      "connected_components"`, which is the explicit, recorded conversion of BM2/BM6, not a
      default.
    - (b) **does not hold in memory, and that path still exists.** `run_patching(...,
      masks=<array>, config=CellPatcherConfig(mode="cell"))` checks each mask with
      `check_object_set` only (`mask_policy.py:44-63`; `strategies/cell.py:86-98`), which accepts
      any plain integer array (`morphobricks_preprocessing/masks/objects.py:18-37` at
      `37c583e`). A `uint8` 0/1 tissue mask passed there is one object, label 1 (CellPatcher
      decision D47, `mask_policy.py:3-6`; `tests/test_pipeline_strategies.py:498-499`). Nothing
      in memory carries the declared representation, so no consumer can tell a region from an
      instance mask there. This design does not fix it; it is recorded for the owner (report
      open question). Until it is decided, the card's next steps and the helper's docstring say
      "use this mask as a region; in memory, never pass it as cell-mode labels", and I1 claims
      the guarantee only for described runs.
  - **(a) in CellPatcher:** CellPatcher does not measure; its random mode inside an all-zero
    region is outside this gate (fiberfeatures makes no patch there, `batch.py:216-219`).
- **P4. Tests:** the §7 tests, including F1 (BM12 reader), F4 (core-only install, CLI) and F5
  (morphology boundaries), are in the I1 PR. The acceptance report of §1 is **not** an I1
  prerequisite: I1 ships the rule as provisional, and freezing is a later, separate step that
  gives a new mask under a new output mask ID (§1), never a relabelled one.

## Implementation notes (I1)

Where the design left a choice, I1 chose as follows (each tested):

- **The opening at the image border** is clipped like the dilations: the erosion counts only
  pixels inside the image (outside counts as background, which a minimum ignores), so an empty
  region touching the border is not eaten from outside. SciPy's default (border value 0) would
  erode it.
- **Comparisons are made in float64**: `E` and `H` are widened exactly and compared with the
  float64 threshold; the threshold is never rounded to float32.
- **The source check** (§7 "Exact reuse") is made on the code (no `log10`, `log`,
  `separate_stains`, `rgb2hed` or `hed_from_rgb` name used): the canonical rule record itself
  names `separate_stains` in its `E` and `H` descriptions, as §1 writes it.
- **The extended reader refuses an operation of neither schema** (as Spec BM15 "Reading"
  requires); on `main` a BM12-shaped line with another operation was read. This is a second new
  refusal besides the repeated keys.
- **An existing output before the read**: every key that is known without pixels (all but the
  source and output digests and shapes and the fractions) is compared before any read, so most
  collisions are refused without reading; the digests are compared after it.
- **`pixel_size`** is `{"X": {"value", "unit", "source"}, "Y": {...}}` from
  `RunInputs.pixel_sizes`, or null when neither has a value.
- **The preview** needs no masks folder and no output mask ID.
- **The normalization pin stays v0.7.0** (`c6a9f9d`): the stable-API documentation of P2
  (normalization #108, merged at `30992e9`) is in no release yet, and `optical_density.py`,
  `scalar_inputs.py`, `stain.py` and `stain_presets.py` are byte-identical between v0.7.0 and
  `30992e9`.
- **P3** was run with fiberfeatures `a7f57c5` (v0.7.0, merged, not yet tagged) and CellPatcher
  v0.7.1 installed (`tests/test_he_tissue_consumer_gate.py`, which skips without fiberfeatures);
  P3 completes when the fiberfeatures v0.7.0 tag exists.

