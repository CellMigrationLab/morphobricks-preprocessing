# Design: an opt-in H&E tissue mask helper

- **Status:** design for review (roadmap item I0). No code changes. The helper itself is I1.
- **Owner answers (16 November 2026):** questions 1, 2, 3, 6, 8 and 9 accepted as recommended
  ([§8](#8-owner-decisions-and-open-questions)); questions 4, 5 and 7 stay open. The Spec
  amendment of §6 is MorphoBricks-Spec #45; I1 waits for it to merge.
- **Owner decision (16 November 2026):** an opt-in preprocessing helper, "make an H&E tissue mask",
  that leaves out nuclei, red blood cells and empty space. It works like the ADR 0005 conversion
  helpers, writes a region mask with its lineage, is declared by the user as a mask, is offered in
  the notebook, and reuses normalization's colour maths without copying it.
- **Read at:** preprocessing `69dc006` (main, after `ea0d7c7` = #308); MorphoBricks-Spec `bf68d30`;
  morphobricks-normalization `v0.7.0` = `c6a9f9d`; fiberfeatures `v0.6.2` = `752b1dd`. Line
  numbers below are from these commits.

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
| Eosin `E` and hematoxylin `H` | `stain.to_scalar(rgb, axes="CYX", preset="he_eosin")`: `.image` is eosin, `.exclusion` is hematoxylin (`stain.py:335-412`, components 1 and 0 of `separate_stains(rgb, hed_from_rgb)`, `stain.py:168-173`) | `separate_stains` units: divided by `-log(1e-6)` and clipped at 0 (normalization `contract.md` §3.4) |
| Per-channel optical density `OD_R, OD_G, OD_B` | `optical_density.optical_density(scalar_inputs.rgb_yxc(rgb, "CYX"), optical_density.check_white((255, 255, 255)))` (`optical_density.py:251-260`, `:127-157`; `scalar_inputs.py:31-57`) | `-log10(max(I, 1) / 255)` |

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

## 2. Inputs and refusals

API, a mixin on `RunInputs` like `ConversionInputs` (`mask_conversion.py:238-251`):

```python
RunInputs.he_tissue_mask(fov, *, output_mask_id, rule="he_tissue_v1") -> TissueMaskResult
RunInputs.preview_he_tissue_mask(fov, *, rule="he_tissue_v1") -> TissueMaskPreview  # writes nothing
```

The input is a typed context, not a path: an opened clean run plus one position. Before any
pixel is read, the helper refuses each of the following with a `ValueError` naming the position
and the reason:

| Refused | Why |
| --- | --- |
| A position whose unit is not declared `image_type = "rgb"` (`Fov.image_type`, `run_inputs.py:146-150`): "`{image}` is declared channels; the H&E tissue mask takes a unit declared image_type = "rgb"" | RGB is declared, never inferred (experiment-v1 §4.4b). RH1 is honoured as recorded: a unit declared `rgb` with `rgb_header_override = true` that the run accepted is valid input. The helper never reads the header itself. The override's effective use (`rgb_override.py:67`) is copied into the lineage. |
| More than one Z plane; more than one time point | One 2-D plane only, as ADR 0005 BM5 does for conversions |
| A pixel type other than `uint8`, or a C count other than 3 | `to_scalar` accepts 8-bit sRGB only (`scalar_inputs.py:52-56`). The refusal comes first, with the helper's own message. |
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
- **Idempotent retry:** a call whose lineage line matches in every key except the output digest
  (source, output root/ID/path, operation, rule ID and version, thresholds, white, normalization
  version and commit, scikit-image and SciPy versions, helper and version), and whose existing
  file still has the recorded digest, is a no-op that returns the existing result (as
  `_same_conversion`, `mask_conversion.py:579-589`). Any difference is refused, never
  overwritten. That includes equal pixels from another source and a new scikit-image version.
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
- **Lineage line** (one per output, keys in this order):

  | Key | Value |
  | --- | --- |
  | `source` | `{"root": "experiment", "image": <image path relative to the experiment folder>, "axes": "CYX", "shape": [3, Y, X], "dtype": "\|u1", "content_sha256": <the ADR 0005 canonical digest of the pixels read, mask_lineage.content_sha256>}` |
  | `output` | the ADR 0005 `FILE_KEYS` object (`root` `"masks"`, `mask_id`, `path`, `axes` `"YX"`, `shape`, `dtype` `"\|u1"`, `content_sha256`) |
  | `operation` | `"he_tissue_mask"` |
  | `rule_id`, `rule_version` | `"he_tissue"`, `1` (shown to users as `he_tissue_v1`) |
  | `thresholds` | every number in the rule table, named with its unit (`nuclei_hematoxylin_min`, `nuclei_min_piece_px`, `nuclei_dilation_radius_px`, ...) |
  | `white` | `[255, 255, 255]` |
  | `normalization` | `{"version": "0.7.0", "commit": <PEP 610 vcs_info.commit_id, or null>, "stain_preset": "he_eosin", "stain_preset_version": 1}` |
  | `scikit_image_version`, `scipy_version` | as imported |
  | `image_type`, `rgb_header_override` | `"rgb"`; `"used"`, `"unused"` or `"not_declared"` from the run's record |
  | `pixel_size` | the recorded X/Y size and unit, or `null` (information only) |
  | `fractions` | `kept`, `nuclei`, `red_cells`, `background`, of all pixels (the components can overlap) |
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

The text below goes into ADR 0005 in its own Spec PR, MorphoBricks-Spec #45, reviewed by the owner:

> **BM15 (decision table row).** | BM15 | **Derived region masks from an image** (owner,
> 16 November 2026): preprocessing may offer an opt-in helper that computes a `binary` **region**
> mask from one declared image with a **named, versioned, fixed rule** whose colour maths is
> called from normalization's `stain` module (never reimplemented). The first is
> `RunInputs.he_tissue_mask` (rule `he_tissue_v1`: nuclei, red blood cells and near-white
> background excluded) on a unit declared `image_type = "rgb"`. Like BM12, it writes a **new mask
> file** with a lineage line, never runs by default, and nothing reads the output until the user
> declares it (`representation = "binary"`) and the mask step has checked it. The output is a
> region (BM3), never an object set (§4.5c). | A mask built inside each consuming brick
> (fiberfeatures builds none); a mask made automatically for every RGB run; per-image tuned
> thresholds. |
>
> **Section "Derived region masks (BM15)", after "The conversion helpers (BM12)".** A BM15 helper
> takes the opened run and one FOV whose unit is declared `rgb` (RH1 honoured as recorded). It
> refuses, before any pixel is read, a FOV with more than one Z plane or time point, a pixel type
> other than 8-bit, a run without a masks folder and an unknown rule. Its output file, its mask ID
> rules, its collision refusals, its idempotent retry and its paired publication are those of
> BM12. Its lineage line goes in the same `mask_lineage.jsonl` and has these keys, in this order:
> `source`, `output`, `operation`, `rule_id`, `rule_version`, `thresholds`, `white`,
> `normalization`, `scikit_image_version`, `scipy_version`, `image_type`, `rgb_header_override`,
> `pixel_size`, `fractions`, `helper`, `helper_version`. Its `source` is `{"root": "experiment",
> "image", "axes", "shape", "dtype", "content_sha256"}`. Here `image` is the FOV image's path
> relative to the experiment folder, and the digest is BM12's canonical digest of the pixels read.
> Its `output` is BM12's file object with `root` `"masks"`. A reader selects the schema by
> `operation` and refuses a line that matches neither schema. A `root` of `"experiment"` is valid
> only in a BM15 `source`. A rule is identified by `rule_id` and `rule_version`; a change of any
> number is a new version; thresholds are never chosen per image.
>
> **Choice 15, amended:** "...in this wave; a BM15 `source` has the `root` `"experiment"`."
>
> **Amendments:** none to experiment-v1, the fixtures, the goldens or `tools/audit/refval.py`.

## 7. Tests (I1)

- **Synthetic H&E:** a 96×96 `uint8` RGB image built from known OD vectors. It has an
  eosin-pink field, a hematoxylin disc (nucleus), a saturated red disc (red cell), a 40×40
  near-white block (1600 px: background) and a 30×30 one (900 px: kept, under the 1000 px
  limit), plus a 3×3 hematoxylin speck (under 15 px: kept). The expected components, the
  dilation radii and the opening are checked pixel by pixel, as is the written file.
- **Exact reuse:** the helper's `E`, `H` and `OD` (exposed on `TissueMaskPreview`) are
  `np.array_equal` to calling `stain.to_scalar(..., preset="he_eosin")` and
  `optical_density.optical_density(...)` directly. A source check shows the module contains no
  `log10`, `separate_stains` or `hed_from_rgb`.
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
- **Q7. normalization commit when not installed from git.** *Recommend recording `null`*, never
  refusing. The version stays recorded, and the pin is a tag.
