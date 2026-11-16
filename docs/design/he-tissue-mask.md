# Design: an opt-in H&E tissue mask helper

- **Status:** design for review (roadmap item I0). No code changes. The helper itself is I1.
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

**Components** (`True` = excluded). The starting point is the proof of concept's
`scripts/tissue_mask.py` and its recorded thresholds (presentation `results/run_record.json`).
The proof of concept used raw 8-bit RGB differences for red cells and its own `log10` for
background. Here both are moved into optical-density terms, so that every number comes from
normalization:

| Component | Pixel rule (`he_tissue_v1`) | Clean-up (pixels) |
| --- | --- | --- |
| Nuclei | `H >= 0.05` | remove pieces under 15 px; then dilate by a disk of radius 2 |
| Red blood cells | `E >= 0.06` and `H < 0.03` and `OD_G - OD_R >= 0.40` and `OD_B - OD_R >= 0.10` | remove pieces under 15 px; then dilate by a disk of radius 1 |
| Background / empty space | `max(OD_R, OD_G, OD_B) < 0.08` (near-white in every channel) | opening with a disk of radius 2; then remove pieces under 1000 px (keep only lumens, fat, tears; not the gaps between fibres) |

`keep = not (nuclei or red_cells or background)`.

- `OD_G - OD_R >= 0.40` is the proof of concept's saturation rule `(max - min) / max > 0.6` in OD
  terms when red is the brightest channel and green the darkest (`log10(1 / 0.4) = 0.398`). The
  value `OD_B - OD_R >= 0.10` approximates its `R - B > 40` near `R = 200`. **Both red-cell colour
  numbers are provisional.** I1 must run the proof-of-concept rule and `he_tissue_v1` on the same
  four presentation images and report the per-component fractions next to each other. The
  numbers are frozen only after that report has been reviewed.
- Clean-up is done with SciPy (`scipy.ndimage`), which is already a core dependency
  (`pyproject.toml`). Pieces are 8-connected, as everywhere else (ADR 0005 BM1). A disk of
  radius `r` is `x² + y² <= r²`. The clean-up is part of the rule, not colour maths. It belongs
  to preprocessing.
- **All sizes are in pixels.** No pixel size is read, guessed or used. The image's recorded
  pixel size, when the run has one (`RunInputs.pixel_sizes`, `run_inputs.py:291-299`), is copied
  into the lineage for information only. The sizes were set on the proof-of-concept images,
  whose magnification was not recorded. The card says that sizes are in pixels (owner decision,
  16 November 2026).

**Fixed defaults, not user thresholds (owner decision, 16 November 2026).** v1 offers one preset, `he_tissue_v1`, and
no per-call overrides. Changing any number gives a new preset ID (`he_tissue_v2`) and never
changes v1. The rule ID, version and every threshold are written to the lineage. Nothing is
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
| scikit-image is missing | `ImportError` naming the extra `pip install "morphobricks-preprocessing[he-mask]"`. That extra pulls in `morphobricks-normalization[histology]`. Core import stays inert. |
| An output mask ID that breaks experiment-v1 §3.2, is already declared, differs only in case from a declared mask ID or source type, names a type folder that holds files this helper did not write, or names an existing file that is not this same computation | Reuses `_check_output_id`, `_check_case_collision` and the foreign-file and existing-file checks (`mask_conversion.py:389-409`, `:532-566`, `:493-513`); the case-collision refusal is #308's fix `03fa90e` |

The helper reads the plane with `RunInputs.read_plane(fov)`. That returns `(3, Y, X)` R, G, B and
refuses an image that changed since the run (`run_inputs.py:301-335`). The helper then checks
the image fingerprint again after the read, as `_read_source` does (`mask_conversion.py:441-446`).

## 3. Output

- **Representation:** one `uint8` `(Y, X)` file holding `1` = tissue kept and `0` = excluded. It
  is declared `binary` and used as a **region**: the region is `mask != 0` (ADR 0005 BM3,
  `regions.check_region`, `regions.py:86-130`). An all-zero result is written as computed. It is
  a valid `binary` file (BM11), and downstream it gives `empty_region`, never 0. The card warns
  when the kept share is 0 %.
- **Layout:** the same as the conversion helpers (`mask_conversion.py:20-24`, `:408-409`):
  `<masks folder>/<output mask ID>/<the image's folders>/<position ID>.tif`, plus one
  `mask_lineage.jsonl` per type folder. Example: image `CTRL/rep1/slide1.tif` gives
  `HETissue/CTRL/rep1/slide1.tif`. The source image is never touched.
- **Publication:** `mask_lineage.publish_output` is reused unchanged. It writes a temporary file,
  re-reads the digest, writes the line, then renames. A crash leaves a pending line, which is
  rolled back on retry (`mask_lineage.py:12-20`, `:199-215`, `:232-282`). Mask discovery already
  skips the lineage file and the temporary files (`mask_lineage.py:69-71`, `masks/files.py:197`).
- **Idempotent retry, with BM15's own comparator** (Spec BM15 "Retry comparator"; it is not
  BM12's `_same_conversion`, `mask_conversion.py:579-589`). An existing output is the same
  computation, and the call is a no-op that returns it, only when both lines have `reusable`
  `true`, they are equal in every key except `output.content_sha256` and `fractions`, and the
  existing file still has the recorded digest. Any difference is refused, never overwritten. That
  includes equal pixels from another source, another code identity, another NumPy, scikit-image
  or SciPy version, another rule record (provisional against frozen included), and any line with
  `reusable` `false`.
- **Code identity (Spec BM15, owner decision on Q7).** For `morphobricks-preprocessing` and
  `morphobricks-normalization` the helper records `{"distribution", "version", "kind", "value"}`.
  It is verified only for a non-editable install whose `RECORD` hashes all still match the files
  on disk (`importlib.metadata.distribution(name).files`, each `PackagePath.hash` checked; once
  per process). Then `kind` is `"vcs_commit"` with PEP 610 `direct_url.json`
  `vcs_info.commit_id` when present, else `"record_sha256"` with the SHA-256 of `RECORD`.
  Otherwise `kind` is `"unverified"`, `value` `null`, plus `reason` (for example `"editable
  install"`, `"RECORD missing"`, `"hash differs: <file>"`). Then the mask is still written,
  `reusable` is `false`, the result and the card say "this mask will not be reused on retry",
  and a retry is refused as a collision.
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
   pixels. Fixed rule he_tissue_v1, not a trained segmentation". The display may be downsampled;
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
- **Refusals:** each row of §2 (channels unit, Z = 2, T = 2, `uint16`, no masks folder, unknown
  rule, missing scikit-image via a blocked import, each output-ID case incl. case collision with
  a declared ID and with a source type, foreign file, existing file), each checked to write
  nothing.
- **Lineage round trip:** write, re-read the line with the extended reader, check every key and
  the digests; a retry is a no-op (`published` False); a changed threshold table, image or
  scikit-image version is refused; a pending line (crash between line and rename) is rolled
  back; BM12 lines in other folders still read; `RunInputs.open` still succeeds before the
  output is declared (`unrecorded.py:108`).
- **Declared and used:** declare the output `binary`, run the mask step (it passes BM11), read it
  with `check_region`, and refuse it as objects with `A_MASK_NOT_INSTANCES`.
- **Untouched runs:** `import morphobricks_preprocessing` imports neither scikit-image nor the
  helper's module. The existing run and golden tests pass byte for byte unchanged. A run that
  never calls the helper writes the same files as `main`.
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

**Still open:**

- **Q4. The provisional red-cell numbers** (`OD_G - OD_R >= 0.40`, `OD_B - OD_R >= 0.10`). *Recommend
  freezing them in I1* after a side-by-side report against the proof-of-concept rule on its
  four images.
- **Q5. normalization functions outside its contract.** `optical_density.optical_density`,
  `check_white` and `scalar_inputs.rgb_yxc` exist but are not listed as API in normalization's
  `contract.md` §3.4. *Recommend a small, documentation-only normalization PR* that names them as
  stable before I1. The alternative is a public `stain.optical_density_map` wrapper.
- **Q7 (decided 16 November 2026, after review w).** A null commit is not accepted. The lineage
  records a verified code identity (commit, or the installed distribution's `RECORD` hash) for
  normalization and preprocessing, the NumPy and scikit-image versions, and the complete rule
  record with its hash. An unverified identity still writes the mask, marked never to be reused
  on retry. BM15 has its own retry comparator (§3).

## 9. Prerequisites for I1

I1 (the helper) starts only when each of these holds:

- **P1. Spec:** MorphoBricks-Spec #45 (ADR 0005 BM15) is merged.
