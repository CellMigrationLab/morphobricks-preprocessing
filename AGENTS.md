# AGENTS.md — MorphoBricks preprocessing brick

Guidance for AI coding agents (and humans) working on
[CellMigrationLab/morphobricks-preprocessing](https://github.com/CellMigrationLab/morphobricks-preprocessing).
It summarises the agreed plan, the decisions taken so far, the programming style, and what
was learned while building the notebooks. Read it before changing anything.

The authoritative documents are in `docs/`. This file points to them and adds what they
do not say: how we work, why things are the way they are, and the traps we already hit.

| File | What it fixes |
|---|---|
| `docs/plan.md` | Goals, processing passes, milestones, open questions and their decisions (§12) |
| `docs/contract.md` | Rules other bricks rely on: folders, axes, dtypes, binning, CSV schema, statistics, normalization, validation severities, manifest format. Changing it may need a new `schema_version` |
| `docs/development.md` | Coding conventions |
| `docs/user_guide.md` | Biologist-facing guide: notebook, CLI, outputs, every message code |

---

## 1. What the brick does

MorphoBricks is a modular microscopy pipeline. This repository is its **preprocessing
brick**.

- **Input:** one experiment folder of TIFF images and/or OME-Zarr images (OME-NGFF 0.4/0.5,
  `zarr` extra).
- **Output:** one intensity histogram per 2D plane in `plane_histograms.csv`, plus
  `run_manifest.json`, both in an output folder outside the experiment.
- **Library:** pooling (`PooledHistogram`) and normalization parameters (`min_max`,
  `percentile_range`, `z_score`, `robust_z_score`). Later bricks such as patch extraction
  use these to normalise any group of planes without rereading pixels.
- **Never:** modifies source images, writes inside the experiment folder, or applies a
  normalization itself.
- **Optional (`zarr` extra, plan §12 question 12):** OME-Zarr input, and converting TIFF
  images to OME-Zarr in the output folder before processing them (contract §10).
- **Large planes (plan §12 question 15):** planes over the memory limit are read in blocks,
  automatically and announced, with identical rows.
- **Out of scope:** spatial corrections (flat-field, BaSiC, CLAHE).

Pipeline:

1. **Pass 1 (preflight):** folder discovery, then TIFF header inspection without reading
   pixels, then a suggested axis mapping.
2. **The user confirms** the labels and the axis roles.
3. **Pass 2:** read one 2D plane at a time (in blocks when it is over the memory limit),
   compute exact extrema and the histogram, write the CSV and the manifest. Reruns only
   read new or changed files.

---

## 2. Repository map (one module per job)

```
src/morphobricks_preprocessing/
  discovery.py      folder walk, leaf folders, labels (condition / biological_repeat / technical_repeat)
  metadata.py       TIFF header inspection (tifffile, no pixel reads), dtype/dimension/series checks,
                    OME channel names + significant bits
  validation.py     ValidationIssue (severity, code, message, paths, action), error()/warning() builders
  axes.py           AxisMapping (role per dimension), prefill, check_mapping, review_axes (one mapping
                    applied to every file, missing axes as singletons only when confirmed)
  planes.py         plane-by-plane reader (iter_planes, read_plane, read_sample_plane), memory estimate,
                    check_planes (preflight memory / layout errors); planes over the limit read in
                    blocks of TIFF strips/tiles or zarr chunks (Plane.blocks, BlockReader), reduced
                    previews
  dtypes.py         supported dtypes, default and allowed bin counts
  histogram.py      per-plane histogram (PlaneHistogram, compute_plane_histogram, and
                    compute_block_histogram: two passes over blocks, identical result), memory estimate
  records.py        PlaneRecord (one CSV row), plane_id, group_records
  csv_io.py         write/read plane_histograms.csv (atomic write, labels always text)
  pooling.py        PooledHistogram: pooled counts, never mean-of-means
  normalization.py  NormalizationParameters (offset, scale) and the four methods
  manifest.py       RunManifest / RunSettings / FileEntry, JSON read/write, SHA-256 of the CSV
  run.py            preflight(), run_experiment() (incremental), sample_planes() for previews
  report.py         plain-language summaries shared by CLI and notebook, group statistics tables
  cli.py, __main__  `morphobricks-preprocessing check|run` (also `python -m morphobricks_preprocessing`)
  zarr_input.py     OME-Zarr input: open_ngff (OME-NGFF 0.4/0.5 metadata -> level-0 array and axis
                    letters), zarr 2/3 helpers, canonical_dtype; needs the `zarr` extra
  zarr_export.py    TIFF -> OME-Zarr conversion (OmeZarrWriter, OME-NGFF 0.4, plane by plane or
                    block by block),
                    ConversionSettings (chunk size, compression, pyramid levels), estimate_conversion
  notebook_style.py notebook look and feel: cards, status cards, tiles, tables, ConfirmToggle, Browse dialogs
  notebook_ui.py    PreprocessingNotebook: the four notebook steps (needs the `notebook` extra)
notebooks/MorphoBricks_Preprocessing.ipynb   thin notebook: every code cell calls notebook_ui only
tests/              pytest with simulated TIFFs (tests/tiff_tree.py, tests/simulation.py)
experiments/binning_study/      the study behind the binning decisions (not part of the package)
```

The notebook and the CLI contain **no processing logic**. They call the same public
functions the tests use.

---

## 3. How we work

1. **Read first.** Before coding a milestone, read `docs/plan.md`, `docs/contract.md`,
   `docs/development.md` and the modules you will touch.
2. **Ask before deciding.** Open questions in plan §12, and any choice that is genuinely
   the user's (product behaviour, UX, file formats), are asked *before* coding. Offer 2–4
   options with a recommended one first and explain the trade-off in one line each.
3. **Record every decision in the docs** using the established pattern in plan §12:
   `*Decided (October 2026): ...*`. Also update the affected rules in `contract.md`
   (tables in §1, §2, §8, §9) and the milestone status in plan §10.
4. **Local review first (user request, October 2026).** Keep new changes local for the
   user to review. Do not commit, push or open a PR until the user requests it. Once
   requested, use **one milestone or feedback round per branch and PR**, against `main`.
   PR description:
   *Summary* (by module), *Decisions*, *Notes for review* (limitations, what was not
   verified), *Tests* (exact counts, e.g. "455 passed, 2 skipped").
5. **Done means:** `ruff check .`, `ruff format --check .` and `pytest` all pass (CI runs
   lint on Ubuntu and tests on Ubuntu, Windows and macOS with Python 3.10 and 3.13), docs
   updated, and local changes ready for review. Open a PR only when the user requests it.
6. **Be honest in reports.** Say what was verified and what was not (for example: "widgets
   tested headless; not looked at in a real JupyterLab").
7. **Scope discipline.** Do the task asked; list unrelated problems as follow-ups instead
   of fixing them silently.
8. **Keep generated or local files out of commits.** That includes test images, outputs
   (`*_MorphoBricks_Preprocessing/`), editor/agent config and launch files.

---

## 4. Rules of the brick (summary of the contract)

### Folders and labels (contract §1)

- **Images:** TIFF files and OME-Zarr folders (name ends in `.zarr`). A store is one image,
  never a folder level, and discovery never enters it.
- **Hierarchy:** `<experiment>/<condition>/<biological repeat>/<technical repeat>/<image>.tif`.
- **Leaf folders:** a leaf folder has images and no subfolders. Only TIFFs directly in
  leaf folders are analysed.
- **Missing levels:** a branch may stop early; missing labels are the reserved value
  `single`. A folder *named* `single` (any case) is an error.
- **Uniqueness:** labels are unique only among siblings, compared case-insensitively;
  siblings differing only in case are an error. Groups are identified by the full label
  path.
- **Depth:** a folder below the technical-repeat level is an **error** (so a layer of images
  is never dropped silently).
- **System files:** dot-files and dot-folders, `Thumbs.db`, `desktop.ini` and `__MACOSX` are
  ignored **without** a warning.
- **Warnings:** non-TIFF files, empty folders, TIFFs outside leaf folders.
- **Paths:** paths in outputs are relative to the experiment root, with `/`.

### Axes (contract §2)

- **Roles:** X, Y, Z, C, T. Exactly one X and one Y; each role at most once; every axis
  longer than 1 needs a role. One confirmed mapping per experiment.
- **Prefill from tifffile axis labels:**
  - X/Y/Z/C/T keep their role;
  - `S` (RGB samples) becomes C when there is no other C;
  - everything else (`I`, `Q`, …) is left unassigned: **never guess** depth vs time.
- **Position-like axes** (`R` tile, `M` mosaic, `J` column, `K` row) cannot get a role. If
  they hold more than one plane: `unsupported_axis` error.
- **Missing axes:** a file lacking some of the mapping's axes (e.g. `ZYX` next to `ZCYX`) is
  read with them as length 1 **only** if the user confirmed it (`missing_as_singleton`) and
  exactly one arrangement fits. The coordinate is then 0, not blank.
- **Series and pyramids:** only the first image series, at full-resolution level 0. A file
  with more than one series is an error (`multiple_series`). OME pyramids are one series;
  non-OME SubIFD pyramids appear as two series in tifffile and are therefore blocked.
- **Coordinates:** plane coordinates `z, c, t` are zero-based; blank (None) when the
  experiment has no such axis.
- **OME-Zarr axes:** translated from OME-NGFF metadata (`zarr_input.ngff_axes`: time -> T,
  channel -> C, space z/y/x -> Z/Y/X, anything else -> Q). The experiment mapping is
  defined on the TIFF arrangement if there are TIFFs; OME-Zarr images with another order
  are matched by role (`axes.declared_mapping`, `file_mapping_for`); a misfit is
  `ome_zarr_axes_mismatch`. Use `file_mapping_for`, never `mapping.for_axes`, to get an
  image's mapping.

### Pixel types and binning (contract §3–§4)

- **Supported dtypes:** int8/16/32/64, uint8/16/32/64, float32, float64. Everything else
  (bool, complex, float16, …) is rejected. One dtype per experiment (`mixed_dtypes` error).
  Source values are never converted.
- **Every plane is binned over its own min–max range**, with the same bin count for all
  planes of an experiment. The binning study showed per-plane ranges cut percentile errors
  5–30× compared with binning over the whole dtype range, and adding images never changes
  existing rows.
- **Integer planes:** whole-number bin limits whose widths differ by at most one value.
  Exact when the range fits the bin count.
  - Default bins: 256 for 8-bit and 65,536 for 16-bit (one per value, so exact), 65,536 for
    wider types.
  - Allowed: 1 to min(65,536, 2^bits).
- **Float planes:** equal-width bins over the finite range. NaN and ±inf are ignored and
  counted.
  - Default 10,000 bins, allowed 2 to 65,536.
- **Special planes:**
  - A constant plane puts every pixel in bin 0.
  - A plane with no finite pixels has blank extrema and zero counts; it is a warning
    unless no plane is usable (error).
- **Representative value** of a bin is its midpoint; no pixel is further than half a bin
  from it.

### Statistics and normalization (contract §6–§7)

- **Groups pool their planes' (representative, count) pairs. Never average per-plane
  statistics.** In the study, mean-of-means underestimated std by up to 22% and p99.9 by up
  to 36%.
- **Accuracy:** min and max are exact from stored extrema. Percentiles use numpy
  `inverted_cdf` on pooled counts.
- **Normalization:** each method is an offset and a scale: `(pixels − offset) / scale`.
  Percentile range defaults to 1–99.8. Robust z-score uses MAD × 1.4826. A zero or
  non-finite scale is an error.

### CSV (contract §5, schema version 1)

- **Columns:**
  `position_id, image_file, z, c, t, condition, biological_repeat, technical_repeat,
  source_relative_path, plane_id, dtype, bit_depth, pixel_min, pixel_max, total_pixel_count,
  valid_pixel_count, binning_min, binning_max, bin_count, schema_version, histogram_bin_00000…`.
- **`plane_id`** = relative path plus `::z=…,c=…,t=…` for the coordinates present. It is
  the row key.
- **Rows** are sorted by path, then z, c, t.
- **Reading:** labels are always read back as text (`NA`, `001`, `TRUE` stay as typed).
  Floats are written with `repr`.

### Run manifest and reruns (contract §9, plan §7)

- **Manifest contents:**
  - settings, the confirmed labels per leaf, and the axis mapping;
  - per-file size, `mtime_ns`, axes, `singleton_roles` and plane count;
  - channel names and significant bits (OME only, information only);
  - the CSV's SHA-256 and package versions.
- **A file is unchanged** when its size, mtime, labels and axis layout match and the table
  still holds all its rows. Unchanged rows are reused; new and changed files are read;
  deleted, renamed or excluded files lose their rows.
- **Full rebuild** happens on `force_rebuild`, when there is no readable manifest, when
  the dtype, bin count or axis mapping changed, or when the CSV's hash no longer matches.
  Changing only the memory limit or the exclusions recomputes nothing.
- **Writes:** the CSV is written first, then the manifest, each via a temporary file and
  replace. Nothing is written if any error is found. An interrupted run is caught next
  time by the hash check.
- **Exclusions:** the user may exclude images explicitly (`exclude=[...]`, e.g. damaged
  files). Excluded images are not read, are reported (`excluded_images` warning), are
  recorded in the manifest, and lose their rows.

### Memory (plan §5)

- **Unit of reading:** read one TIFF page at a time and yield the planes inside it. Never
  load a full Z/C/T volume unless the file stores it as a single page.
- **Estimate per plane, from the header:** decoded page + encoded bytes + plane copy +
  histogram temporaries (`histogram.estimate_histogram_memory`). It is deliberately an
  upper estimate.
- **Limit:** default 1 GiB. A plane over the limit is read in blocks (question 15):
  - Blocks are whole TIFF strips or tiles, or OME-Zarr chunks, in full-width bands first,
    as many as fit the limit (`planes._choose_block`, estimate `planes._block_memory`).
    Uncompressed TIFF pieces (read straight from the file) and zarr chunks may also be read
    a few rows at a time. No block setting: the limit decides.
  - Announced, never silent: warning `planes_read_in_blocks` in `check_planes`, and
    `read_in_blocks` per file in the manifest (information only, not part of
    `same_source`).
  - Histograms: `compute_block_histogram` reads the blocks twice (extrema, then counts);
    8- and 16-bit integers once (a count per possible value). Identical to the whole plane,
    and `compute_plane_histogram` uses the same code with one block.
  - A plane read in blocks is a `Plane` with `pixels=None`; `Plane.blocks()` reads from
    the open file, so use it before asking `iter_planes` for the next plane.
  - Previews (`read_plane(preview=True)`, `read_sample_plane`) are reduced to every n-th
    pixel (`Plane.step`, at most 4,096 px per side).
  - A compressed TIFF storing a plane in one strip/tile cannot be split: still the
    `plane_over_memory_limit` error, like a plane whose smallest block is over the limit.
    The readers raise `PlaneMemoryError` before reading.
  - Wide integers (32/64-bit) reserve up to 512 MiB for the per-value count table in
    `estimate_histogram_memory`, so they need a limit above about 520 MiB even in blocks.

### Validation messages (contract §8)

Every expected problem is a `ValidationIssue` with:

- a stable snake_case `code` (e.g. `missing_axes`, `unreadable_tiff`), listed in user guide §8;
- a severity: errors block processing, warnings do not;
- a plain-language `message` saying what happened (no jargon, no stack traces);
- `paths`: sorted relative paths of the affected files;
- an `action`: what the user can do.

Technical details go to `logging` (shown in the notebook's "Technical details" panel, or
`--log FILE` in the CLI). Custom exceptions are only for genuine failures.

---

## 5. Decisions log

| Topic | Decision |
|---|---|
| Binning | Per-plane min–max, same bin count per experiment, pooled counts for groups (binning study) |
| Core dependencies | `numpy` and `tifffile` only. `ipywidgets` and `matplotlib` in the `notebook` extra; `zarr` in the `zarr` extra; `pytest` and `ruff` in `dev` |
| OME-Zarr (Q12) | Input may be TIFF and/or OME-Zarr (folders ending in `.zarr`), mixed. OME-Zarr axes come from their metadata and are matched by role; level 0 of a single image only, several images per store is `multiple_series`. TIFF choice once per experiment (`convert_tiffs`): read directly (default) or convert to `<output>/ome_zarr` (OME-NGFF 0.4) and compute rows from the copies; switching recomputes TIFF rows and removes unused copies. Reads OME-NGFF 0.4 and 0.5 (0.5 needs zarr 3). In the notebook: its own step-3 card "(Optional) Image conversion" after Settings (Q13): radio buttons (read directly = recommended), a disk-space warning (amber, red when the estimate exceeds free space, never blocking), and conversion settings (chunk size, compression + level, pyramid levels) shown only when converting. CLI `--convert-tiff` with `--chunk-size`, `--compression`, `--compression-level`, `--pyramid-levels`. No overlap setting: chunks never overlap |
| System files (Q5) | Ignored silently; `single` check and sibling uniqueness are case-insensitive |
| Too-deep folders (Q7) | Error |
| Channel names (Q1) | Manifest only (from OME), warning `channel_names_differ`; CSV stays at schema 1 |
| Multi-series / pyramids (Q2) | Level 0 only; more than one series is an error |
| RGB `S` axis (Q3) | Prefilled as C, confirmed like any role |
| Significant bits (Q4) | Manifest only, information only; `bit_depth` and binning unchanged |
| Corrupt files (Q6) | Explicit exclusion list, recorded in manifest, rows removed |
| Notebook target | Local Jupyter (not Colab-first) |
| CLI | `check <folder>` (summary, label tree, issues, suggested `--axes`) and `run <folder> --axes ROLES` with `--output`, `--exclude`, `--bins`, `--memory-limit 2GiB`, `--missing-as-singleton`, `--force-rebuild`, `--log`, and the conversion options above. Axis roles are always stated explicitly, never taken silently from metadata. Exit 1 on errors |
| Large planes (Q15) | Planes over the memory limit are read in blocks automatically, announced by the warning `planes_read_in_blocks` and `read_in_blocks` in the manifest. Block size derived from the limit (whole strips/tiles/chunks, uncompressed pieces and chunks also by rows). Compressed single-strip/tile planes keep the `plane_over_memory_limit` error. Two-pass histograms identical to whole planes. OME-Zarr copies written block by block, smaller levels built from the stored level above. Axis preview reduced (every n-th pixel) with a caption saying so |
| Notebook style | Follow the lab's NucleiSky and LabChronicle notebooks (see §7) |
| Widget widths | **99%**, never 100% |
| Paths and filenames in widget HTML and notebook text | **`<b>…</b>` / `**…**`, never `<code>` or inline code** |
| Things the user must confirm | **Large buttons** in a highlighted question box (`ConfirmToggle`), never small checkboxes |
| Statistics step | "Step 4 · Calculate the statistics (optional)": clearly optional, writes nothing. A CSV chooser (Browse…) is prefilled with the table just computed, otherwise empty. Not calculated automatically |

---

## 6. Programming style

- **Python 3.10+.** Put `from __future__ import annotations` in every module, and type
  hints on all public functions.
- **Ruff is the authority.** Line length 100. Rules E, W, F, I, B, UP, NPY and D, with
  numpy docstring convention; D105 and D107 ignored; tests are exempt from D.
  - D401 needs an **imperative first line** in docstrings of functions and methods
    ("Return…", "Build…", "Show…"). Properties may say "True if…".
  - B023: bind loop variables in lambdas (`lambda f, i=index: …`).
- **NumPy-style docstrings** (Parameters / Returns / Raises) on public modules, classes and
  functions. Module docstrings explain the module's job and point to the contract section.
- **Comments explain *why*,** not *what*. Keep the density of the surrounding code.
- **Data and state:**
  - Small **frozen dataclasses** for settings and results; no global state.
  - Mappings exposed from results are read-only (`MappingProxyType`).
  - Functions take explicit inputs and return results.
- **Determinism:**
  - Sort folders, files and outputs.
  - The same inputs give byte-identical CSVs; tests check this.
  - Write files as UTF-8 with LF line endings (`newline="\n"`), atomically (temp file,
    then `os.replace`).
- **Messages are for biologists:** short sentences, plain words, a suggested action, the
  affected paths. Use `validation.plural()` for counts ("1 image", "3 images"). User-facing
  text uses British spelling ("analysed", "normalise", "colour"); identifiers keep existing
  names (`normalization`).
- **Console output (CLI) must be ASCII-safe.** Windows consoles (cp1252) turn "→" into
  "?", so use "->" in texts the CLI prints. `cli.py` also sets `errors="replace"`.
- **No new core dependencies** without asking. tkinter (for folder dialogs) is optional
  and imported lazily with a fallback.
- **Public API** is re-exported in `__init__.py`. `__all__` is sorted: constants, then
  classes, then functions.

### Tests

- **Simulated data only**, generated in `tmp_path`: `tests/tiff_tree.py` (`write_data`,
  `write_tiff`, `make_tree`, `write_file`) and `tests/simulation.py`. Do not rely on
  example files.
- **Every acceptance check in plan §11 has a test.** A bug fix comes with a test that fails
  without it.
- **Proving "no pixels are read":** monkeypatch `asarray` on `TiffFile`, `TiffPage`,
  `TiffFrame` and `TiffPageSeries` to raise.
- **OME-Zarr test data:** `tests/zarr_tree.py` (`write_ngff`, `write_bioformats2raw`,
  `write_plate`) writes stores with zarr directly, independent of `zarr_export`; 0.5 tests
  skip with zarr 2.18.
- **Proving "one page at a time":** spy on `planes._read_page`. To prove "only changed
  files are read", spy on `run.iter_planes`. For planes in blocks, spy on
  `planes._read_segment` and make `asarray` of every tifffile class fail.
- **Planes in blocks with small test images:** the memory limit
  `plane_layout(path, mapping, memory_limit=1).block_memory_estimate` gives the smallest
  blocks a file can be read in. Consume each lazy plane before the next one (the file
  closes when `iter_planes` ends). `tests/test_histogram.py` keeps an independent copy of
  the original whole-plane formulas (`reference_histogram`).
- **Speed:** rerun tests use 8-bit images (256 bins). Wide 65,536-bin CSVs make every run
  about 1 s.
- **Platform-specific tests** (case-sensitive file system, POSIX permissions) skip with a
  reason on Windows.
- **Notebook widgets are tested headless:** build the pages, drive them with
  `button.click()` and `.value = …`, and assert on `.value` of HTML widgets. Tests skip
  if ipywidgets/matplotlib are missing; CI installs `.[dev,notebook,zarr]`. OME-Zarr
  tests skip without zarr; CI's Python 3.10 jobs get zarr 2.18 and 3.13 jobs zarr 3.
- **The notebook file is tested too:**
  - valid nbformat 4, with no outputs;
  - code cells hidden and titled `# @markdown ### Run to …`;
  - Step 0 bootstraps the environment; later code cells only call `notebook_ui`;
  - the buttons it names exist;
  - its cells execute.

---

## 7. Notebook UI guidelines

The lab's standard is the notebooks in
[CellMigrationLab/nucleisky](https://github.com/CellMigrationLab/nucleisky) and
[CellMigrationLab/LabChronicle](https://github.com/CellMigrationLab/LabChronicle). They use
plain ipywidgets 8 with injected CSS and copy their helpers into each notebook; here the
helpers live in `notebook_style.py` so all steps stay consistent. The user found the first,
plainer version "not interactive enough". Descriptive, guided and forgiving is the bar.

### Notebook file (`notebooks/MorphoBricks_Preprocessing.ipynb`)

- **Title cell:** `# 🔬 **MorphoBricks | Preprocessing**`, a purpose paragraph, "What the
  notebook does", "Quick workflow" (numbered, naming every button), a "Before you start"
  checklist, a 🛡️ safety blockquote, and an example folder tree in a `text` block with
  📂/📄.
- **Per step:** `___`, then `## <emoji> **Step N: <Verb phrase>**`, then numbered
  instructions naming each button in bold. Tips are blockquotes (`> 💡 Tip:`, `> 🩹`,
  `> ℹ️`). Mark optional steps "(optional)" in the heading and say why.
- **Code cells:**
  - Hidden, with metadata
    `{"cellView": "form", "collapsed": false, "jupyter": {"source_hidden": true}, "jupyterlabHideCode": {"locked": true}}`.
  - The first line is a Colab-style title: `# @markdown ### Run to …`.
  - Outputs are stripped.
  - Step 0 is a standalone standard-library bootstrap: nearby checkout, installed package,
    or GitHub installation, with missing requirements installed using the kernel's Python.
  - Later cells are one call each, e.g. `notebook.folder_step()`.
- **Step 0** starts the notebook and shows a green "ready" card with the package version.
- **Closing cell:** "✅ After running" checklist and "Common problems" that quote the real
  message texts (check they still match the code).
- **Text:** use bold, not inline code, in the markdown (fenced text blocks such as the
  folder tree are fine).

### Page layout (`notebook_style`)

- **Page and cards:** `ui.page(title, subtitle, children)` wraps a step in a grey page
  (`mb-root`) with a title and a one-sentence purpose. `ui.card(header, children)` gives a
  white card with an upper-case header. `ui.intro(html)` adds a blue "what happens here"
  box at the top of each step.
- **Help text:** `ui.help_text(html)` adds a **grey help line under every control**: what
  it is, what the default does, what happens if changed.
- **Status cards:** `ui.status(kind, title, lines)` is the single status-card component.
  - Green "ok": done, next step named.
  - Blue "info": waiting or neutral.
  - Amber "warn": please check.
  - Red "error": action required.
  - Errors are phrased as fixes ("Read the messages below; each one says what to do").
    Successes end with what to do next.
- **Other building blocks:**
  - `ui.working(text)`: spinner status while something runs.
  - `ui.tiles([(emoji, value, label)])`: headline numbers.
  - `ui.table(rows, numeric_from)`: scrollable, sticky header, right-aligned numbers.
  - `ui.checklist([(done, text)])`: ✅/⭕ lists.
  - `ui.path_list(paths)`: long lists folded behind "Show N more".
  - `ui.button(…, main=True)`: full-width (99%), 42 px tall main action at the bottom of
    a step, with a Font Awesome icon and a tooltip. Button style follows meaning: success
    = do it, info = look or preview, warning = change or undo or exclude.
  - `ui.ConfirmToggle(question, explanation, confirm_label, confirmed_text, undo_label)`:
    a yellow question box with a big green "Yes, …" button. Once confirmed the box turns
    green and shows an amber "Undo: …" button. Use it for **every** "the user checked
    this" moment (labels correct, preview looks right, missing axes are single planes).
- **Paths:**
  - `ui.ask_directory()` and `ui.ask_open_file()` open native Tk dialogs (topmost). They
    return None when no dialog is possible, so show "paste the path instead".
  - `ui.clean_path()` strips quotes and smart quotes and expands `~` and environment
    variables.
- **CSS:**
  - Forced light colour scheme and a system sans-serif font.
  - `overflow-x: hidden` on boxes inside pages, so ipywidgets boxes never show stray
    horizontal scrollbars.
  - Escape all dynamic text with `html.escape`.

### Interaction rules (learned)

1. **Gate, don't surprise.** Later steps are locked until earlier ones are confirmed. Step
   3 shows a "Before you run" checklist naming the first thing still to do, and Run stays
   disabled until it is empty.
2. **Inputs reset what depends on them.** Changing the experiment folder resets the
   preflight, the confirmations and the axis roles. Changing an axis role resets the
   preview confirmation. Choosing another CSV clears the old statistics.
3. **Explain every choice live.** Each axis card shows its length, the suggested role and a
   dropdown with descriptive options ("Z - depth (focal planes)"). Under it, a sentence
   updates with the choice ("3 depth slices, numbered z = 0 to 2; each becomes its own
   plane"), and a summary says how many planes of which size each image gives.
4. **Show what will happen before it happens.** "What will happen" lists the output folder,
   the files to be written, and whether an earlier run exists and what will be reread. A
   live line under the output folder shows where results go and warns if that is inside
   the experiment.
5. **Recommended settings first.** A ticked "Use the recommended settings" hides the
   advanced controls (bins with dtype-specific bounds and help, memory limit, recompute
   everything). Unticking reveals them, and re-ticking restores the defaults.
6. **Busy discipline.** Disable the button, change its label ("Computing…", spinner
   icon), and restore it in `finally`. The progress bar names the current image. Turn the
   bar green or red at the end.
7. **Previews must be checkable.** A file picker plus z/c/t sliders (hidden for axes of
   length 1 and until the preview is shown; the middle Z slice first). The image is
   stretched for display only (0.5–99.5 percentiles), and the caption gives the file,
   coordinates, size, dtype and value range. Problems reading a plane show as a status
   card, not a traceback.
8. **Results:** tiles (planes, read, kept, new, changed, removed, excluded, checks). The
   statistics table explains its columns. A cumulative intensity plot per group (up to
   12). Unexpected exceptions become a red card with the type and message; the traceback
   goes to the collapsed "Technical details (log)" accordion, which is fed by a
   `logging.Handler` attached during each action.
9. **Optional means optional.** Say so in the title and intro, write nothing, and do not
   compute it automatically.
10. **Visual check.** Before calling UI work done, render pages with
    `ipywidgets.embed.embed_minimal_html(...)`, serve the folder with
    `python -m http.server`, and look at them in a browser. This catches overflow and
    scrollbars, awkward order, and hidden-vs-shown states. Standalone renders lack
    JupyterLab styles and icons; say so when reporting.

---

## 8. tifffile knowledge (version 2026.x)

- **Axis codes** (`TIFF.AXES_NAMES`): X width, Y height, Z depth, S sample, I sequence, T
  time, C channel, A angle, P phase, R tile, H lifetime, E lambda, L exposure, V event,
  M mosaic, Q other, J column, K row.
- **How tifffile labels a plain stack:** it depends on how the file was written. Without
  metadata, `imwrite(..., metadata=None)` gives `IYX`; with `metadata={}` (a "shaped" series)
  it gives `QYX`. Both must be assigned by the user.
- **When writing test files:**
  - Pass `photometric="minisblack"`, or 3-sample stacks become RGB.
  - RGB contiguous is `YXS`; planar is `SYX` (`planarconfig="separate"`).
  - `volumetric=True` with `tile=(z, y, x)` stores a whole Z stack in one page.
- **Series:** `tif.series[0]` is level 0; OME pyramids have `series[0].levels`. Non-OME
  SubIFD writes create a second series.
- **Planes as pages:** `series.pages[i]` may be a `TiffFrame` (OME, compressed) and may be
  None for missing OME IFDs. `page.asarray()` decodes one page.
- **Splitting a series into pages:** the shape is page-selecting leading axes followed by
  the page shape. Find the split `k` where `prod(shape[:k]) == len(series.pages)` and
  `prod(shape[k:]) == keyframe.size`, with X and Y at or after `k`. Otherwise the layout
  is unsupported (planes would span pages).
- **Encoded size:** use `keyframe.databytecounts`.
- **Byte order:** dtypes may be big-endian; normalise them with `.newbyteorder("=")`.
- **OME details:** `tif.is_ome` and `tif.ome_metadata` hold the XML.
  - Channel names: `Pixels/Channel@Name`. Significant bits: `Pixels@SignificantBits`.
  - Write them in tests with
    `metadata={"axes": ..., "Channel": {"Name": [...]}, "SignificantBits": 12}`.
  - ImageJ "Labels" are slice labels, not channel names.

---

## 9. Environment gotchas (seen on Windows)

- **Line endings:** files must stay LF. Python's `Path.write_text` on Windows writes CRLF:
  write bytes, or pass `newline="\n"`.
- **Worktrees and the editable install:** an editable install in the main checkout's venv
  imports the *main* checkout's `src`. In a git worktree, run tests with `PYTHONPATH=src`
  so the worktree code is used.
- **Encoding:** set `PYTHONIOENCODING=utf-8` when printing widget HTML (emojis) to a
  Windows console.
- **Long inline scripts:** shell heredocs with non-ASCII characters ("…", emojis) can
  break; write the script to a file and run it instead.
- **What ruff touches:** ruff also lints and formats `.ipynb` code cells and Python code
  blocks inside Markdown files (it reformatted README/user-guide examples). Run
  `ruff format .` before `ruff format --check .`.

---

## 10. Commands

```bash
python -m venv .venv
pip install -e ".[dev,notebook,zarr]"
ruff check .
ruff format --check .
pytest
morphobricks-preprocessing check /data/exp1
morphobricks-preprocessing run /data/exp1 --axes ZCYX
jupyter lab notebooks/MorphoBricks_Preprocessing.ipynb
```

---

## 11. Status (October 2026)

- **Done:** Milestones 0–5 (contract, scanner and validator, axis review, histogram engine,
  CSV and reruns, notebook + CLI + user guide), the notebook redesign plus feedback
  round (PRs #6 and #7), and OME-Zarr input with optional TIFF -> OME-Zarr conversion
  (PR #5).
- **In review (local):** planes over the memory limit read in blocks (plan §12 question 15).
- **Not yet done for large planes:** the step-3 "What will happen" box does not yet say
  which images will be read in blocks before the run (the warning comes with the run).
- **Not yet verified:**
  - the exact look of the notebook in a real JupyterLab;
  - the optional `jl-hidecode` JupyterLab extension, which keeps code cells collapsed. It
    is mentioned in the user guide but is not a dependency.
