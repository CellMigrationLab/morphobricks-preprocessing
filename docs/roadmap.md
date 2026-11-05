# Preprocessing roadmap

Planned work after v0.4.0. The v0.4.0 work itself (PP-0 to PP-13) is in
[plan.md](plan.md) §10 and in the frozen design (`preprocessing-design-v0.4.md` on
`CellMigrationLab/MorphoFeature`, branch `claude/restart-doc`).

## Quick quality checks before analysis (owner, 2026-11-02)

**Goal:** preprocessing stays a *quick* step, but it points the user to fields of view (FOVs)
worth a look before any analysis. Two checks are planned:
- outlier images, from whole-FOV embeddings;
- possibly wrong masks, from quick per-object measurements.

Both only **flag**. The user looks at the flagged FOVs and decides. Nothing is excluded,
corrected or re-annotated automatically.

### Q1. Outlier FOVs from whole-FOV embeddings (MorphoEmbed)

- **What it does:**
  - embeds each FOV as a whole, with no patching, through MorphoEmbed;
  - flags FOVs whose embedding lies far from the other FOVs of the same condition and
    biological repeat. Typical causes are out-of-focus fields, debris, bubbles, empty fields,
    or other acquisition settings.
- **What it complements:** the intensity checks, which already flag images whose median or
  high percentile lies far from their group. The embedding check catches what intensities
  miss, such as texture, focus and content.

### Q2. Quick mask check (MorphoObjects)

- **What it measures:** a small, fast set of per-object measurements through MorphoObjects,
  such as area, solidity, eccentricity, the share of objects touching the border, the
  object count per FOV, and objects per parent (nuclei per cell). They are summarised per FOV
  and mask type.
- **What it flags:** FOVs whose masks look unlike the others of the same condition and
  biological repeat (out of distribution), for example:
  - many tiny or merged objects;
  - holes;
  - over- or under-segmentation;
  - cells without a nucleus or with several nuclei.
- **What the user does:** checks the annotation quality of those FOVs.
- **What it complements:** the `masks` step, which already counts objects and checks
  containment between mask types; this extends it with shape statistics and outliers.

### Rules for both

- **Fast by design.** A time budget per FOV is set and benchmarked on the real datasets
  (fiberfeatures `experiment_001`, MorphoRelate HPMEC) before release:
  - Q1: one embedding per FOV from a downscaled whole field; CPU must be usable, and GPU is
    optional and explicit;
  - Q2: only cheap region properties, with no texture features.
- **Optional.** These are extras; `check` and `run` never need them.
- **Same rules as every brick:**
  - no guessed axes or channels;
  - no implicit model download, model swap, or CPU/GPU fallback; the model and its weights
    are explicit and recorded;
  - the channels used are stated;
  - every FOV that could not be scored is reported, never treated as normal.
- **Comparisons only within a group.** A FOV is compared with the FOVs of its own condition
  and biological repeat, as in the intensity checks. That way a real difference between
  conditions is not flagged as an error. A group too small to compare (below the
  intensity checks' minimum of 5 images) is reported as "not checked", not as passed.
- **Outlier statistic.** Robust deviations from the group median, reusing the intensity
  checks' statistic and thresholds where they fit (no second copy, §9). For embeddings, a
  robust distance in the embedding space; which one is decided with a benchmark.
- **Output.** The flags go to the run's report and to a QC table in the output folder, with
  the model, its weights' identity, the measurement set, thresholds and versions, so a rerun
  can tell whether they are still valid.

### Where the code lives (owner, 2026-11-02)

**Decision:**
- each check runs when its brick is installed: Q1 when MorphoEmbed is installed, Q2 when
  MorphoObjects is installed;
- a check whose brick is missing is skipped;
- neither brick is ever needed for `check` or `run`.

**Design (to keep the dependency one-way):**
- **The bricks register their checks.** MorphoEmbed and MorphoObjects depend on preprocessing,
  so preprocessing never imports them by name. Each brick declares its check through a Python
  entry point, for example group `morphobricks_preprocessing.quick_checks` with names
  `embedding_outliers` and `mask_outliers`. Preprocessing finds the installed ones with
  `importlib.metadata.entry_points`.
- **No extra of preprocessing installs the bricks,** since that would be a circular
  install. Users install the bricks they want.
- **The algorithms stay in the bricks:**
  - MorphoEmbed: the whole-FOV embedding;
  - MorphoObjects: the quick per-object set.
  Both read the images and masks through `RunInputs`.
- **Preprocessing owns the rest:**
  - the hook's interface (a versioned protocol: inputs, the result table, the provenance it
    must return);
  - the outlier statistic, shared with the intensity checks;
  - the QC table's format;
  - showing the flags in the report, the CLI and the notebook.
- **A skipped check is reported, never silent.** For example: "embedding check not run:
  MorphoEmbed is not installed (`pip install …`)". A skipped check is never read as passed,
  so the report keeps three outcomes apart: flagged, checked with nothing found, and not
  run.
- **A hook that fails, or returns an incompatible protocol version, is reported as not run,
  with the reason.** Preprocessing does not fall back to another method.
- **When the checks run:**
  - in the notebook, after the run, as an optional step that lists which checks are
    available;
  - on the command line, through a `quick-checks` command (or `run --quick-checks`), so
    the default run stays as fast as today.
- **Model and device for Q1 come from MorphoEmbed's own explicit settings.** Its weights must
  already be present; a missing weight file is "not run", never a download.

**Still to decide:**
- the default model for Q1;
- the per-FOV time budget;
- whether MorphoFeature also offers the same checks as a "quick QC" preset.

### When

After v0.4.0 and after the MorphoEmbed and MorphoObjects migrations to `RunInputs` (phase 1
of the MorphoBricks sequence). In the phase 2 triage, the owner places it in a bucket.

## P1. Explicit projection step (owner, 2026-11-05)

**Context:** v1 analyses 2-D planes only. Preprocessing refuses Z>1 and allows Z=1
(#173, plan question 76); the stated shared 2-D mask is removed. A Z-stack can still be
analysed in 2-D if the user projects it first, as a step of its own. 3-D analysis is a separate
project: [MorphoFeature#157](https://github.com/CellMigrationLab/MorphoFeature/issues/157).

**What it does:**
- writes new 2-D images; the source image is never changed;
- the user chooses the method: `max`, `mean` or `sum`, or keep one stated slice. There is no
  default;
- the manifest records the method and the source image's SHA-256;
- the projected image becomes the FOV, linked to its stack;
- a stack with 3-D masks is refused.

**Acceptance:**
- the output is deterministic: the same stack and method give the same bytes;
- the projection is recorded and visible in the run manifest;
- nothing is projected implicitly: a Z>1 image without a projection step is still refused.

## P2. Experiment schema (MorphoBricks-Spec#1)

An experiment-schema RFC is open at
[MorphoBricks-Spec#1](https://github.com/CellMigrationLab/MorphoBricks-Spec/issues/1). Its
feasibility audit is
[rfc1-feasibility-2026-11-05.md](https://github.com/CellMigrationLab/MorphoBricks-Spec/blob/claude/youthful-carson-klzm3w/docs/audits/rfc1-feasibility-2026-11-05.md).
It recommends a reduced version after the release, in TOML or strict JSON rather than YAML.

Staged plan from the audit:
- **Stage 0:** owner decisions, then M0 (docs, schema, fixtures);
- **Stage 1:** an additive preprocessing v0.5 with a semantic channel API;
- **Stage 2:** mixed channel layouts, only if datasets need them.
