# Migrating from v0.3.4 to v0.4.0

v0.4.0 makes preprocessing the one place where an experiment's files, axes, masks and pixel
sizes are checked and recorded. Every later brick reads that record through `RunInputs`
instead of discovering, pairing or calibrating files itself. The rule behind every change is
the same: **nothing is guessed, nothing is silent, and nothing is reused when its identity is
uncertain.**

This guide lists every breaking change from v0.3.4. Each item gives **what changed**, **why**
(the scientific rule) and **what to do**. The first section is for people who run
preprocessing (notebook, command line or Python). The second is for brick authors. The full
rules are in the [contract](contract.md) and the [user guide](user_guide.md). The decisions
are in the [plan](plan.md), §12 questions 45 to 69. The complete list of changes is in the
[CHANGELOG](../CHANGELOG.md).

**In short, for users:** install v0.4.0 and run preprocessing again on every experiment,
into the same output folder. The first run recomputes every row. It asks again for the axis
roles of plain stacks, and it stops on the new errors listed below. Fix them, then run the
later bricks on the new run.

## For users

### 1. Every v0.3 run must be run again (manifest v1 → v2)

- **What changed.** `run_manifest.json` is version 2 (contract §9). Each image records its
  `roles`, where they come from (`axes_from`: `header` or `confirmed`) and its pixel sizes by
  role, with a unit and a source. Each mask records its `size`, `mtime_ns`, `roles`,
  `axes_from` and whether it is a stated shared 2-D mask. The manifest also records the SHA-256
  of `calibration.csv` and, for a run on named-axis copies, of the conversion record.
- **Why.** A v1 manifest does not say which axis is which, or whether the user or the header
  decided it. A row computed under unknown roles cannot be trusted as the same row.
- **What to do.** Run preprocessing again into the same output folder. A v1 manifest gives
  **no reusable row and no recorded input**: the run recomputes every row, and you give the
  axis roles, masks and exclusions again. The report says that the earlier manifest was
  version 1. The later bricks refuse a v1 run (`RunInputs.open` raises `RunInputsError`).

### 2. Axis checks: swaps and contradictions are errors; plain stacks are confirmed per file

- **What changed.**
  - Confirmed roles that swap the Y and X the header names (`axes_yx_swapped`), contradict
    the header (`axes_contradict_header`) or put X before Y in a plain stack
    (`axes_x_before_y`) are **errors**. Such an image is never read transposed or under a
    contradicted declaration.
  - A plain stack (the header names no axes, `axes_not_named`, a warning) takes the roles you
    confirm. The confirmation covers only the files it was given for, at their recorded
    fingerprint. A new or changed plain stack is `plain_stack_not_confirmed` until you
    confirm it again (CLI `--axes`).
  - An image whose own header names X before Y (for example ImageJ `ZXY`) is still read, in
    the canonical Y-before-X order, because its metadata names the axes.
- **Why.** A guessed or contradicted axis gives wrong pixels with no error. The roles must
  come from the header or from an explicit answer.
- **What to do.** Correct the roles to match the header. If the header itself is wrong, make
  named-axis copies (§9 below) and run preprocessing on them.

### 3. Recorded inputs, modes and the output folder's lock

- **What changed.**
  - `force_rebuild=True` / `--force-rebuild` are **removed**. They are replaced by
    `mode="restart"` (the default: reuse the rows of unchanged files) and `mode="full"`
    (recompute every row and check). Both keep your confirmations: design, axis and
    mask-axis statements, stated calibration and image type.
  - One output folder belongs to one experiment. A later run takes every input you do not
    give from the earlier v2 manifest: experiment folder, axis roles, exclusions, masks
    folder and mask axes, image type, design, bins and histogram settings, intensity limits
    and `missing_as_singleton`. `run --output OUT` alone reruns the recorded experiment
    (without one: `experiment_not_recorded`). A value you give that differs is used, and the
    report names the change and its effect. Masks are dropped only with `--no-masks`
    (`masks_folder=False`). Exclusions are cleared only with `--no-exclude` (`exclude=[]`).
  - A run holds an exclusive lock file, `.morphobricks-preprocessing.lock`, in the output
    folder. A second run is refused with `output_locked`, naming the computer, process ID and
    start time. A stale lock is removed only when you ask: `--remove-stale-lock` (every
    command), `remove_stale_lock()`, or the notebook's **Remove the lock of a stopped run**.
    The lock of a process still running on the same computer is never removed. On Google
    Drive the lock cannot guard a folder across computers or sessions, so use one session per
    output folder.
- **Why.** A silent change of input, or two runs writing one folder, gives a table that
  describes neither run.
- **What to do.** Replace `force_rebuild=True` with `mode="full"` and `--force-rebuild` with
  `--mode full`. Use one output folder per experiment. Read the report's `input_changes`
  after a run.

### 4. The report, written on every run

- **What changed.** Every run that holds the lock writes `discovery_report.json` and
  `discovery_report.txt`, also when it stops. The report has `status` (`stopped` or
  `written`), the SHA-256 of the manifest it describes, and what the run reused, recomputed,
  resumed and replaced, with the reasons. `check` writes the same report (`status:
  "stopped"`, `step: "check"`) and, when the output folder holds no design, a prefilled
  `experiment_design.csv` (nothing confirmed).
- **Why.** A run that stops must still say why, and what it kept.
- **What to do.** Read the report after every run. Bricks never read it.

### 5. Pixel sizes: `calibration.csv` and the `calibrate` command

- **What changed.** For each image, the X, Y and Z sizes come from the header, or from a
  statement in the output folder's `calibration.csv` (`target,axis,size,unit`; target `.`,
  a folder or an image; a length unit or `pixels` for deliberately uncalibrated). The most
  specific statement applies. The run reads this file and never writes it. You write it with
  `morphobricks-preprocessing calibrate`, `write_calibration()`, the notebook's **Pixel
  sizes** card or by hand. Each axis is recorded with its source (`header`, `stated` or
  `pixels`). A Z or T size is recorded only for an image that has that axis. A size that is
  not a positive finite length in micrometres is refused when stated, and unknown (with
  `invalid_physical_calibration`) when it comes from a header.
- **Why.** Pixel sizes belong to the experiment, not to each brick's configuration. A size
  nobody gave is uncalibrated, never a default.
- **What to do.** Move the pixel sizes you gave to bricks (run files, FOV-table columns,
  configs) into `calibration.csv`. Changing it recomputes no row; the next run records the
  new sizes. `RunInputs` refuses a `calibration.csv` changed, added or removed since the run.

### 6. Experiment design: the `design` command and stricter readers

- **What changed.**
  - New command `design --output OUT`. It prints the design and what is still unconfirmed,
    and changes it without editing the CSV: `--repeat-group CONDITION/REPEAT=GROUP`,
    `--control` / `--no-control CONDITION`, `--confirm GROUP`, `--confirm-all`. Python:
    `edit_design` with `DesignEdit`. Nothing is confirmed unless you ask.
  - `design_not_confirmed` stops a `restart` that has new unconfirmed repeats.
  - Every design reader refuses a header that names a column twice or has a column without
    a name (`DesignFormatError`).
  - Technical-repeat folders whose images are all excluded are not counted.
  - In the notebook, changing the output folder reloads that folder's design; nothing is
    carried over.
  - `load_design` returns `(design, changes)` and raises `DesignCheckError` for a file it
    cannot read (in v0.3.4 it returned a third value, `problem`).
- **Why.** Pooling biological repeats is a scientific claim. It must be an explicit, recorded
  answer for the output folder it belongs to.
- **What to do.** Confirm the design with `design` or the notebook. Remove duplicate
  columns from hand-edited files. Update callers of `load_design` to unpack two values.

### 7. Masks

- **What changed.**
  - `image_named_as_mask` is an **error**: an image named like a mask of another image
    (`_mask`, `_masks`, `_cp_masks`, `_labels`, `_label`, `_seg`, `_segmentation`) is not
    analysed as an image. `_cp_masks` and `_masks` are new mask suffixes.
  - Masks are checked **as stored** and never converted or reordered. A mask whose header,
    or stated axes, put X before Y is refused (`mask_layout`, with a `numpy.swapaxes` hint).
  - A plain-stack mask needs its axes stated once per mask type: `--mask-axes Cells=TYX`, or
    **Mask axes** in the notebook.
  - A 2-D mask of an image with several Z or T planes is refused (`mask_planes_mismatch`)
    unless its type is stated `YX` (`--mask-axes Cells=YX`): one mask shared by every plane.
  - OME-Zarr `labels/` are always used as masks when present. `find_masks(ome_zarr_labels=)`
    is **removed**.
  - The notebook has a **Masks folder** field and a **Describe the masks** button (the
    `masks` step).
  - `same_objects` in `mask_relationships.json` is a one-to-one matching (report version 2).
  - The masks folder may hold only masks: an experiment, named-axis copies or the output
    folder inside it is refused (`experiment_inside_masks`, `copies_inside_masks`,
    `output_inside_masks`, before anything is written), and so is a `convert` folder
    inside it (`copy_folder_overlaps_masks`). `RunInputs.open` refuses a recorded run whose
    folders break this rule.
- **Why.** A mask read under the wrong axes describes other objects. Objects must be joined
  by their recorded pairing, never by guessing a layout.
- **What to do.** Rename or exclude images named like masks. Save masks with their axes
  named (or state them per type). Save X-before-Y masks again with `numpy.swapaxes`. Remove
  `ome_zarr_labels=` from `find_masks` calls. Move an experiment or copies out of the
  masks folder, and run again a run recorded with them.

### 8. OME-Zarr input

- **What changed.**
  - An OME-NGFF 0.1 or 0.2 store is `unreadable_ome_zarr`: its metadata never name the
    axes, and the order its specification fixes is not assumed. A 0.3, 0.4 or 0.5 store
    without `axes` is unreadable too.
  - Every OME-Zarr image is matched by the roles its axes declare. A confirmed role that is
    not the declared one is `ome_zarr_axes_mismatch` (OME-Zarr images are never converted:
    correct the roles).
  - A malformed scale gives unknown sizes with `invalid_physical_calibration` (a warning).
  - An OME-Zarr image's fingerprint adds the SHA-256 of its file inventory (each file's path,
    size and time). **Images recorded by v0.4.0rc3 or earlier have no inventory: they count
    as changed and are recomputed, and `RunInputs` refuses such a run.**
  - Links inside a store follow the same containment rule as the experiment (§10 below).
- **Why.** The axes and the scale must come from the metadata. A chunk changed inside a store
  must not leave an old row in place.
- **What to do.** Re-save OME-NGFF 0.1 and 0.2 stores as OME-NGFF 0.4 or later with their
  axes. Run preprocessing again after upgrading, also from an rc.

### 9. Conversion: `convert_tiffs` removed, `convert` added

- **What changed.**
  - **Removed:** `convert_tiffs` and the `zarr_export` module (OME-Zarr copies in
    `<output>/ome_zarr`): `ConversionSettings`, `ConversionEstimate`, `estimate_conversion`,
    `OmeZarrWriter`, `ome_zarr_relative_path`; the manifest's `convert_tiffs`,
    `conversion` and `ome_zarr_path` (ignored when an old manifest holds them); the CLI
    `--convert-tiff`, `--chunk-size`, `--compression`, `--compression-level` and
    `--pyramid-levels`; the notebook's "(Optional) Image conversion" card; and
    `notebook_options.PYRAMID_OPTIONS` (nothing replaces it).
  - **Added:** `convert EXPERIMENT --to FOLDER --axes ROLES` (`convert_experiment`; the
    notebook's **Make named-axis copies** card). It is only for images with axis errors or
    unnamed axes. They are written as named-axis OME-TIFF with your confirmed roles, pixel for
    pixel. Every other image is copied byte for byte. Masks are never converted.
  - The copies keep the file names and folders, so `source_relative_path`, `plane_id` and the
    FOV key stay the same. A relabelled axis gets an unknown size, never a guessed one. The
    only exception: a Y/X swap keeps the size when X and Y are exactly equal.
  - The copies are written as `.partial` files and moved into place together. The conversion
    record, `.morphobricks-conversion.json` (version 2), is written last, with the source and
    copy fingerprints and each copy's SHA-256. A version 1 record (v0.4.0rc3 or earlier) is
    refused: convert again into a new folder.
- **Why.** The run reads every image through its own readers, so the OME-Zarr copies served
  no brick. A file whose header is wrong must be fixed in a new, recorded copy, never read
  transposed.
- **What to do.** Stop passing the removed options. The old `<output>/ome_zarr` folder is not
  touched; delete it yourself if you no longer need it. For images with axis errors, run
  `convert`, then run preprocessing on the copy folder with the **same output folder**: the
  design, image type and calibration are kept, and every row is recomputed. A changed copy
  stops the run (`copy_changed_after_conversion`).

### 10. Links, the `--log` file and exit codes

- **What changed.**
  - A symbolic link (or a Windows junction) in the experiment or masks folder is followed
    only when its real path stays inside that folder. Otherwise it is `link_outside_folder`.
    One file reached by two paths, hard links included, is `linked_twice`. A loop is
    `link_loop`.
  - `--log` is checked before the file is opened. It is refused inside the experiment, the
    masks folder or the folder of copies, and as or inside a protected output file (the
    table, manifest, reports, design, `calibration.csv`, `mask_relationships.json`, lock,
    conversion record, `.publish`, `.staging`) or the `--design` file.
  - Every command exits with status **1 when it prints an error** and 0 otherwise. Warnings
    never change the status, and options that cannot be parsed exit with 2. `check` and
    `design` exited with 0 on some errors before.
- **Why.** A link that leaves the experiment brings in images that are not part of it, and
  one file seen twice is counted twice. A log must never overwrite a result. Scripts must be
  able to trust the exit status.
- **What to do.** Copy images into the experiment rather than linking to them from
  elsewhere. Write logs next to the output folder. In scripts, check the exit status.

### 11. Staging, resume and crash-consistent publication

- **What changed.**
  - Each image's rows are staged in the output folder's `.staging` as soon as the image is
    read. A run that was interrupted is resumed by the next run: staged rows are reused only
    when their row key, pixel type, schema version and package versions still match and the
    image still has its fingerprint. Anything else is discarded and reported.
  - Publication is crash-consistent. The new files are written into `.publish`, then one
    `os.replace` of `run_manifest.json` commits them, then they are moved into place. The
    next holder of the lock finishes or discards an interrupted publication. The v0.3
    backup-and-restore files are gone.
  - An image that changes during the run is `source_changed_during_run`, and nothing is
    published.
  - A run replaces or removes only `plane_histograms.csv`, `run_manifest.json`,
    `experiment_design.csv` (with a design), the reports, `mask_relationships.json`,
    `.publish` and `.staging`. It never touches `calibration.csv`, source images or masks.
  - New output files get your umask's permissions; existing files keep theirs.
- **Why.** A reader must see the old run or the new one, never a mix. A row is reused only
  when it is certainly the same row.
- **What to do.** Nothing, except: read the outputs through `RunInputs` (bricks), or check
  `csv_sha256` before pairing files by hand. Files written `0600` by earlier versions keep
  that mode until you change it.

### 12. Other changes users see

- Mixed pixel types stay an error. MorphoRelate projects with mixed dtypes must be saved
  again in one type.
- `--memory-limit` must be a whole number of bytes above 0 (`1.5GiB` is accepted, `1.5`
  is refused).
- `different_sizes` and `different_plane_counts` compare the images by their confirmed roles
  once the roles are confirmed.
- `run` needs `--axes` only when some images are plain stacks; images that all name their
  axes are run with those roles. A rerun that changes nothing keeps `run_manifest.json`
  byte-identical (its `written_at` too). `convert` takes `--output`, the output folder of
  your runs on the originals, for the next commands it prints (cross-brick test, plan §12
  question 73).
- The notebook runs on Google Colab and in JupyterLab. It installs the pinned release
  (`RELEASE_TAG`), never `main`.

## For brick authors

### 13. Read runs only through `RunInputs`

Bricks read a preprocessing run through
`RunInputs.open(output_folder, experiment_root=None, masks_root=None)` (contract §9.1). They
take no axis or calibration arguments for file runs, never transpose, and never call
`find_masks`, `discover_experiment` or `position_id` for file runs. For in-memory arrays a
brick keeps only a canonical layout argument (Y before X, no swaps). Without a clean run a
brick refuses and gives the preprocessing command; there are no standalone exceptions.

**`RunInputs` gives:**

- `fovs()`: `Fov` records with the FOV key, `plane_id`s, axes, roles, `axes_from`, channel
  names, dtype, `significant_bits` and the Z, T and C counts. They come from the manifest and
  the table and are never parsed from names.
- `pixel_sizes(fov)`: the value, unit and source per role, or unknown.
- `read_plane(fov, z, t)`: a `(C, Y, X)` array of the stored values. `read_image(fov)`: a lazy
  `(T, Z, C, Y, X)` `LazyImage`. Both are in canonical order. `z` and `t` must be integers
  (a `plane_id` is a `TypeError`).
- `mask_types()` and `read_mask(fov, type, z=None, t=None)`: the recorded pairing only, read
  as stored with `check_labels` (labels 0 to int32 max). A stated shared 2-D mask serves
  every plane of the image; a `z` or `t` outside the image is an `IndexError`, for
  `read_plane` too (audit I5).
- A header size in an unsupported unit (OME `pixel`, `inch`) or without a unit is unknown
  in `pixel_sizes` (audit I3). `to_micrometers` takes NumPy scalars (audit m4). The
  warnings `partial_pixel_size` and `non_square_pixels` name the images you will refuse
  for length features (audit m5). `read_plane`'s memory limit counts only reading (I2).
- `histograms()`, `design()` (the strict `read_confirmed_design`, checked against the
  manifest's SHA-256) and `image_type()`.
- `RunInputs` is picklable for worker processes. `morphobricks_preprocessing.testing.
  make_clean_run(folder, ...)` writes a small clean run for tests.

**`RunInputs.open` refuses** (`RunInputsError`), naming the cause and the fix:

- a version 1 manifest;
- a table, design or `calibration.csv` whose SHA-256 is not the manifest's (also a
  `calibration.csv` added since the run);
- an image or mask changed or gone since the run (every read checks the fingerprint again);
- a mask recorded without axes (a v0.4.0rc1 run);
- an OME-Zarr image recorded without its inventory (v0.4.0rc3 or earlier);
- **files the run did not record** (`UnrecordedFilesError`, with `files.images` and
  `files.masks`): images discovery would analyse, and masks it would pair with a recorded
  image, that the run neither recorded nor excluded. There is no opt-out. The fix is to run
  preprocessing again, or exclude the images;
- a `masks_root` given for a run that recorded no masks folder;
- a run on named-axis copies whose conversion record changed or disappeared
  (`conversion_record_sha256`), whose record is version 1, or whose copies do not match the
  record's fingerprints and SHA-256. The copies are read once, at `open`, to check them;
- a run holding OME-Zarr images when the `zarr` extra is missing (install
  `morphobricks-preprocessing[zarr]`), and a recorded OME-Zarr image whose `labels` group
  cannot be read;
- links that leave the experiment or masks folder, as discovery refuses them.

`unrecorded_files(manifest, experiment_root, masks_root, output_folder=None)` gives the same
list without raising.

### 14. Removed APIs

| Removed | Use instead |
|---|---|
| `convert_tiffs=`, `zarr_export` (`ConversionSettings`, `ConversionEstimate`, `estimate_conversion`, `OmeZarrWriter`, `ome_zarr_relative_path`) | nothing (images are read directly); `convert_experiment` for axis errors |
| The stand-ins of the code moved to the normalization brick: `morphobricks_preprocessing.{csv_io,dtypes,histogram,normalization,pooling,records}` and their top-level names (`PlaneRecord`, `PlaneHistogram`, `compute_plane_histogram`, `compute_block_histogram`, `read_plane_histograms`, `write_plane_histograms`, `group_records`, `PooledHistogram`, `PooledStatistics`, `pooled_statistics`, `NormalizationParameters`, `percentile_range`, `min_max`, `z_score`, `robust_z_score`, `default_bin_count`, `make_plane_id`, `SINGLE`, `OUTPUT_FILENAME`, `NoValidPixelsError`, `UnsupportedDtypeError`) | import them from `morphobricks_normalization` |
| `check_declared_axes`, `declared_axes_transposed` | nothing. They accepted a Y/X swap and read it transposed, which v0.4 forbids. Read through `RunInputs` |
| `find_masks(..., ome_zarr_labels=)` | omit it: label images are always used |
| `run_experiment(force_rebuild=)`, CLI `--force-rebuild` | `mode="full"`, `--mode full` |
| `notebook_options.PYRAMID_OPTIONS` | nothing |
| CLI `--convert-tiff`, `--chunk-size`, `--compression`, `--compression-level`, `--pyramid-levels` | nothing |
| Pickled `PhysicalSizes` from v0.3 | not supported (owner decision, design v0.4 change log, 1 November 2026). The class gained `invalid` (issue #94). A v0.3.4 pickle loads today with `invalid` None, but this is not guaranteed. Do not keep pickles across versions: read the metadata again, or use `RunInputs.pixel_sizes` |

Superseded in the bricks (each brick's own migration PR): CellPatcher `image_axes`/`mask_axes`
for file runs, `image_read_transposed`, its own mask pairing and name parsing; MorphoFeature's
run-file axes and calibration, FOV-table pixel-size columns and `inputs.read_transposed`;
MorphoRelate's own mask pairing and calibration config; fiberfeatures' `spacing_cols`; and
the brick-side swap refusals.

### 15. Caches keyed on the run

- A `restart` that changes nothing leaves `plane_histograms.csv` byte-identical, so a brick
  cache keyed on its SHA-256 (the manifest's `csv_sha256`) survives.
- Key caches on the manifest's SHA-256 and the file fingerprints, never on paths alone.
- After upgrading from v0.3 or from a release candidate up to v0.4.0rc3, the table is
  recomputed. Caches keyed on the old table must not be reused.

### 16. The `zarr` extra

Install `morphobricks-preprocessing[zarr]` in every brick that may open a run holding
OME-Zarr images. Without it, `RunInputs.open` refuses such a run and names the extra. It
never skips the images or their label images.
