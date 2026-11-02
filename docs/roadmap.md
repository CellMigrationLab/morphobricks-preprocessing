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

### Where the code lives (open question for the owner)

MorphoEmbed and MorphoObjects depend on preprocessing, so preprocessing cannot import them;
that would be a circular dependency. Each check's algorithm belongs to its own brick (§9
ownership).

The proposal:
- **The bricks compute:**
  - MorphoEmbed offers a whole-FOV embedding mode for a preprocessing run;
  - MorphoObjects offers the quick per-object set;
  - both read through `RunInputs`.
- **Preprocessing owns the outlier statistic** (shared with the intensity checks) and the
  QC table's format.
- **Preprocessing only displays the flags:** the notebook and the report show them when the
  table exists.
- **The notebook step runs only when the bricks are installed:** the preprocessing notebook
  gets an optional step 5 that imports MorphoEmbed and MorphoObjects only when they are
  installed, or MorphoFeature offers the same as a "quick QC" preset.

**To decide:**
- where the step runs: an optional step in the preprocessing notebook, a MorphoFeature
  preset, or both;
- the default model for Q1;
- the per-FOV time budget.

### When

After v0.4.0 and after the MorphoEmbed and MorphoObjects migrations to `RunInputs` (phase 1
of the MorphoBricks sequence). In the phase 2 triage, the owner places it in a bucket.
