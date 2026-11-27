"""What one run works with: its options and, once its header checks are done, its state.

Moved from :mod:`.pipeline`, which holds the run's steps; :mod:`.outputs` writes what the state
describes.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from ..content_memo import ContentMemo
from ..lab_links import LabLinks
from ..manifest import RunManifest
from .description_run import RunDescription
from .expected import ExpectedSources
from .preflight import Preflight
from .recorded import ResolvedInputs
from .steps import Checked
from .workers import Workers


@dataclass(frozen=True)
class RunOptions:
    """The run's options that are not recorded inputs."""

    mode: str
    memory_limit: int
    progress: Callable[[int, int, str], None] | None
    lab_links: LabLinks | None = None
    workers: Workers = field(default_factory=Workers)
    checking: Callable[[str, int, int, str], None] | None = None
    """The progress of the run's own folder and header check (:func:`.preflight.preflight`)."""
    memo: ContentMemo | None = None
    """The content SHA-256s one notebook press already took (:attr:`.modes.RunMode.memo`)."""
    expected: ExpectedSources | None = None
    """The generation the run's images must be (:attr:`.modes.RunMode.expected`)."""


@dataclass(frozen=True)
class RunState:
    """What one run works with once its header checks are done."""

    output: Path
    pre: Preflight
    inputs: ResolvedInputs
    mode: str
    checked: Checked
    previous: tuple[RunManifest | None, str | None]
    found: frozenset[str]
    """The names in the output folder before the run wrote anything."""
    mask_content: Mapping[str, str | None] = field(default_factory=dict)
    """In a ``verify_content`` run, each mask's content SHA-256 taken after the header checks."""
    description: RunDescription | None = None
    """The published experiment description the run works with; None when it was refused."""
    lab_links: LabLinks | None = None
