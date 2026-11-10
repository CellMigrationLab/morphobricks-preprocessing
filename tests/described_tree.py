"""Publish an experiment description for a tree a test wrote, then run (policy of 10 Nov 2026).

Every run needs a published ``morphobricks.toml`` (``A_DESCRIPTION_REQUIRED``, Spec ADR 0002
AR10/AR11). The run-integration tests write small trees of their own; :func:`describe_tree`
publishes the description the user would confirm for such a tree in the notebook's describe
steps, every proposal accepted:

- one condition per condition folder, one repeat per biological-repeat folder name, one unit per
  technical-repeat folder (``single`` for a level the tree does not have), every repeat
  confirmed on a fixed date, no reference;
- each unit's channels from its first image's header: a header name gives its own channel ID and
  ``header_names``, an unnamed position the ID ``ch<i>``; an RGB image is an ``rgb`` unit;
- one ``instances`` mask per mask type found (the masks folder's subfolders, OME-Zarr labels).

This is the test author stating the description, written out by a test helper. It is never part
of the package: the package itself never derives an identity from a folder or file name.
:func:`run_described` publishes it (again, so a changed tree is described as it is now) and calls
:func:`morphobricks_preprocessing.run.run_experiment` with the same arguments.
"""

from __future__ import annotations

import re
from pathlib import Path

from morphobricks_preprocessing import run_experiment
from morphobricks_preprocessing.axes import AxisMapping, reference_axes
from morphobricks_preprocessing.axis_check import ConfirmedAxes
from morphobricks_preprocessing.axis_review import review_axes
from morphobricks_preprocessing.cli import build_parser, main
from morphobricks_preprocessing.description import (
    Channel,
    Condition,
    Description,
    Mask,
    Member,
    Repeat,
    Unit,
)
from morphobricks_preprocessing.description_file import DESCRIPTION_FILENAME
from morphobricks_preprocessing.description_write import canonical_bytes
from morphobricks_preprocessing.discovery import check_output_folder, default_output_folder
from morphobricks_preprocessing.draft_ids import id_problems
from morphobricks_preprocessing.physical_check import discovered_units, layout_of
from morphobricks_preprocessing.run.output_guard import output_masks_issues
from morphobricks_preprocessing.run.pipeline import _check_arguments
from morphobricks_preprocessing.run.preflight import preflight
from morphobricks_preprocessing.run.reuse import read_previous

MARK = b"# Described by tests/described_tree.py: every proposal accepted.\n"
"""The first line of a description this helper wrote; it rewrites only those (or none)."""
CONFIRMED_ON = "2026-01-01"
"""The date every repeat is confirmed on (fixed, so the bytes do not depend on the clock)."""
RGB_CHANNELS = ("R", "G", "B")
_NOT_ID = re.compile(r"[^A-Za-z0-9]+")


def _token(text: str, fallback: str) -> str:
    """Return ``text`` as a token (letters, digits, single underscores), else ``fallback``."""
    token = _NOT_ID.sub("_", text).strip("_")
    return token if token and token[0].isalpha() else fallback


_KINDS = {
    "condition": "conditions",
    "repeat": "repeats",
    "technical": "technical_repeats",
    "channel": "channels",
    "mask": "masks",
}


class _Ids:
    """Hand out valid, unique IDs per kind: the same one each time for the same source text.

    The source text itself when the rules allow it (as the notebook proposes it), else a token
    made of it, else the fallback; ``_<n>`` is added until the ID is allowed and unique.
    """

    def __init__(self) -> None:
        self.by_source: dict[tuple[str, str], str] = {}
        self.taken: dict[str, set[str]] = {}

    def _allowed(self, kind: str, value: str) -> bool:
        taken = {t.casefold() for t in self.taken.get(kind, ())}
        return not id_problems(_KINDS[kind], value) and value.casefold() not in taken

    def get(self, kind: str, source: str, fallback: str) -> str:
        key = (kind, source)
        if key not in self.by_source:
            for base in (source, _token(source, fallback), fallback):
                if self._allowed(kind, base):
                    found = base
                    break
            else:
                n = 2
                while not self._allowed(kind, f"{base}_{n}"):
                    n += 1
                found = f"{base}_{n}"
            self.taken.setdefault(kind, set()).add(found)
            self.by_source[key] = found
        return self.by_source[key]


def _roles(pre, mapping, missing_as_singleton):
    """Return each image's confirmed roles under ``mapping`` (or a roles string), or {}."""
    if mapping is None:
        return {}
    if isinstance(mapping, str):
        axes = reference_axes(pre.metadata.files)
        if axes is None or len(axes) != len(mapping):
            return {}
        mapping = AxisMapping(axes, tuple(mapping))
    review = review_axes(pre.metadata, mapping, missing_as_singleton=bool(missing_as_singleton))
    return {path: found.roles for path, found in review.file_mappings.items()}


def _unit(ids, channels, layout):
    """Return a unit's channel IDs (adding the channels to ``channels``) and its image type."""
    if layout is not None and layout.rgb:
        for name in RGB_CHANNELS:
            channels.setdefault(name, Channel())
        return RGB_CHANNELS, "rgb"
    count = 1 if layout is None or layout.channels is None else layout.channels
    names = () if layout is None else layout.names
    found = []
    for index in range(count):
        raw = names[index] if index < len(names) else None
        if raw:  # one channel per name as trimmed; every raw spelling is a header name
            name = raw.strip()
            name = f"{name}_channel" if name in RGB_CHANNELS else name  # R, G, B are RGB's
            channel_id = ids.get("channel", name, f"ch{index + 1}_named")
            known = channels.get(channel_id, Channel(header_names=())).header_names
            channels[channel_id] = Channel(header_names=tuple(sorted({*known, raw})))
        else:
            channel_id = ids.get("channel", f"\0{index}", f"ch{index + 1}")
            channels.setdefault(channel_id, Channel())
        found.append(channel_id)
    return tuple(found), None


def tree_description(pre, mapping=None, *, missing_as_singleton=None, representation="instances"):
    """Return the description of a checked tree, every proposal accepted (module docstring)."""
    ids, roles = _Ids(), _roles(pre, mapping, missing_as_singleton)
    files = {f.relative_path: f for f in pre.metadata.files}
    conditions, channels, repeats = {}, {}, {}
    for key, images in sorted(discovered_units(pre.discovery, pre.excluded).items(), key=str):
        meta = files.get(sorted(images)[0])
        layout = None
        if meta is not None and meta.readable:
            layout = layout_of(meta, roles.get(meta.relative_path))
        condition_id = ids.get("condition", key.condition, "cond")
        conditions[condition_id] = Condition(key.condition)
        repeat_id = ids.get("repeat", key.biological_repeat, "rep")
        unit_id = ids.get("technical", key.technical_repeat, "tech")
        unit_channels, image_type = _unit(ids, channels, layout)
        members = repeats.setdefault(repeat_id, {})
        member = members.get(condition_id)
        units = dict(member.technical_repeats) if member else {}
        units[unit_id] = Unit(key.technical_repeat, unit_channels, image_type=image_type)
        count = (member.image_count if member else 0) + len(images)
        members[condition_id] = Member(key.biological_repeat, count, units)
    masks = {}
    for mask_type in () if pre.masks is None else pre.masks.types:
        masks[ids.get("mask", mask_type, "mask")] = Mask(mask_type, representation)
    return Description(
        conditions=conditions,
        channels=channels,
        repeats={r: Repeat(True, CONFIRMED_ON, members) for r, members in sorted(repeats.items())},
        used_ids={
            "conditions": tuple(sorted(conditions)),
            "channels": tuple(sorted(channels)),
            "repeats": tuple(sorted(repeats)),
            "technical_repeats": tuple(
                sorted(
                    {u for m in repeats.values() for x in m.values() for u in x.technical_repeats}
                )
            ),
        },
        masks=masks,
    )


def describe_tree(
    experiment,
    output=None,
    mapping=None,
    *,
    masks_folder=None,
    mask_axes=None,
    exclude=None,
    missing_as_singleton=None,
    representation="instances",
):
    """Publish the description of the tree under ``experiment`` in ``output``; return its path.

    Inputs left as None are taken from the earlier run in ``output``, as the run takes them.
    Nothing is written into an output folder the run refuses (inside the experiment), and a
    description the test published itself is kept: None then.
    """
    root = Path(experiment).resolve()
    out = default_output_folder(root) if output is None else Path(output)
    if check_output_folder(root, out.resolve()) or output_masks_issues(out.resolve(), masks_folder):
        return None
    previous = read_previous(out.resolve())[0] if out.exists() else None
    if previous is not None:
        mapping = previous.mapping if mapping is None else mapping
        exclude = previous.settings.exclude if exclude is None else exclude
        recorded = (previous.masks or {}).get("folder")
        masks_folder = recorded if masks_folder is None else masks_folder
        if missing_as_singleton is None:
            missing_as_singleton = previous.settings.missing_as_singleton
    if masks_folder is False:
        masks_folder = None
    if isinstance(mapping, ConfirmedAxes):
        mapping = mapping.mapping
    pre = preflight(
        root, out, exclude=exclude or (), masks_folder=masks_folder, mask_axes=mask_axes
    )
    description = tree_description(
        pre, mapping, missing_as_singleton=missing_as_singleton, representation=representation
    )
    path = out / DESCRIPTION_FILENAME
    if path.is_symlink() or (path.exists() and not path.read_bytes().startswith(MARK)):
        return None  # the test's own description is kept
    out.mkdir(parents=True, exist_ok=True)
    path.write_bytes(MARK + canonical_bytes(description))
    return path


def run_described(experiment_root=None, mapping=None, **kwargs):
    """Publish the tree's description (:func:`describe_tree`), then call ``run_experiment``.

    Takes ``run_experiment``'s arguments. With no experiment folder given, the one the run in
    ``output_folder`` recorded is described.
    """
    # Arguments the run refuses before anything is read: nothing is described then either.
    _check_arguments(
        kwargs.get("mode", "restart"), kwargs.get("image_type"), kwargs.get("missing_as_singleton")
    )
    output = kwargs.get("output_folder")
    root = experiment_root
    if root is None:
        previous = read_previous(Path(output).resolve())[0]
        root = None if previous is None else previous.experiment_root
    if root is not None and Path(root).is_dir():
        describe_tree(
            root,
            output,
            mapping,
            masks_folder=kwargs.get("masks_folder"),
            mask_axes=kwargs.get("mask_axes"),
            exclude=kwargs.get("exclude"),
            missing_as_singleton=kwargs.get("missing_as_singleton"),
        )
    return run_experiment(experiment_root, mapping, **kwargs)


__all__ = ["describe_tree", "run_described", "tree_description"]


def _option(argv, name):
    """Return the value of ``--name VALUE`` (or ``--name=VALUE``) in ``argv``, else None."""
    for index, item in enumerate(argv):
        if item == name and index + 1 < len(argv):
            return argv[index + 1]
        if item.startswith(name + "="):
            return item.split("=", 1)[1]
    return None


def main_described(argv):
    """Publish the tree's description for a ``check``/``run``/``masks`` command line, then run it.

    The experiment is the first argument (or the one the run in ``--output`` recorded); the axis
    roles of ``--axes``, the masks of ``--masks`` and ``--exclude`` are described as given.
    """
    argv = [str(item) for item in argv]
    if not argv or argv[0] not in ("check", "run", "masks"):
        return main(argv)
    parsed = build_parser().parse_args(argv)  # options it cannot read: exit 2, nothing described
    if getattr(parsed, "no_exclude", False) and parsed.exclude:
        return main(argv)  # refused before anything is written
    output = _option(argv, "--output")
    experiment = argv[1] if len(argv) > 1 and not argv[1].startswith("-") else None
    if experiment is None and output is not None:
        previous = read_previous(Path(output).resolve())[0]
        experiment = None if previous is None else previous.experiment_root
    if experiment is not None and Path(experiment).is_dir():
        roles = _option(argv, "--axes")
        excluded = [argv[i + 1] for i, item in enumerate(argv[:-1]) if item == "--exclude"]
        describe_tree(
            experiment,
            output,
            roles,
            masks_folder=_option(argv, "--masks"),
            exclude=excluded or None,
            missing_as_singleton=True if "--missing-as-singleton" in argv else None,
        )
    return main(argv)


def description_entry():
    """Return a valid manifest ``experiment_description`` for manifests built in a test."""
    from morphobricks_preprocessing.description_record import description_record, unit_records
    from morphobricks_preprocessing.testing import clean_description

    described = clean_description(2)
    return description_record(described, "0" * 64, unit_records(described, {}), {}, ())


def check_masks(output):
    """Run the mask step on a run's output folder, so its masks are ``checked`` (RunInputs).

    Returns the run's output folder, for ``RunInputs.open(check_masks(...))``.
    """
    from morphobricks_preprocessing.run.mask_step import describe_masks, prepare_mask_step

    step = describe_masks(prepare_mask_step(None, output))
    assert not step.issues, step.issues
    return output


def without_mask_step(clean):
    """Remove what ``make_clean_run``'s mask step wrote, as before the step ran; return ``clean``.

    For the tests of the mask step itself, which start from a run whose masks were not scanned.
    """
    for name in ("mask_relationships.json", "mask_representation.json"):
        (clean.output / name).unlink(missing_ok=True)
    return clean
