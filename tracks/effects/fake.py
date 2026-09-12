"""Deterministic FakeBackend (NFR-01) — first-class executor stand-in, not a mock.

Moved from tracks/executor/fake_agent.py (v0.1 FakeAgent) into the effects
boundary (ARCH-003 §4); behavior is unchanged. Implements ``AgentBackend``.

Behavior injection: TRAC_FAKE_SIMULATE, read ONLY at the cli/executor boundary
(interfaces §5 note); project()/decide() never see it. Format: semicolon- or
comma-separated `key=token` entries, key = "role:substate" (or "validator:doc").
A token may be a `|`-separated sequence consumed one per call (last one
sticks), e.g. `sage:SAGE_REVIEW=comment|pass`. Defaults: document work "ok",
reviews "pass". Token "hang" blocks (recovery/lock tests, AC-27a).

Composition (C0302 split): the dispatch face, M-DESIGN docs, M-IMPL planning,
Devon phases and declared-reply encoding live in
:mod:`tracks.effects.fake_act` / ``fake_design`` / ``fake_plan`` /
``fake_devon_act`` / ``fake_reply`` and are composed into the single
``FakeBackend`` class below. Every moved name is re-exported here, so the
pre-split import surface of this module is unchanged.
"""

from __future__ import annotations

from pathlib import Path

from tracks.effects.devon_patch import (
    DevonPatchMixin as DevonPatchMixin,
)
from tracks.effects.devon_patch import (
    append_text as append_text,
)
from tracks.effects.devon_patch import (
    unified_patch as unified_patch,
)
from tracks.effects.fake_act import (
    _ANCHOR_VERDICT as _ANCHOR_VERDICT,
)
from tracks.effects.fake_act import (
    _PRISM_REVIEW_SUBSTATES as _PRISM_REVIEW_SUBSTATES,
)
from tracks.effects.fake_act import (
    FakeActMixin as FakeActMixin,
)
from tracks.effects.fake_act import (
    _simulate_map as _simulate_map,
)
from tracks.effects.fake_design import (
    _ACC_ITEM as _ACC_ITEM,
)
from tracks.effects.fake_design import (
    _CLI_HEADING as _CLI_HEADING,
)
from tracks.effects.fake_design import (
    _CLOSURE_HEADING as _CLOSURE_HEADING,
)
from tracks.effects.fake_design import (
    _COVERAGE_HEADING as _COVERAGE_HEADING,
)
from tracks.effects.fake_design import (
    _FENCE as _FENCE,
)
from tracks.effects.fake_design import (
    _HTML_COMMENT as _HTML_COMMENT,
)
from tracks.effects.fake_design import (
    _IF_REGISTRY as _IF_REGISTRY,
)
from tracks.effects.fake_design import (
    _REACH_ENTRIES_RELATIVE as _REACH_ENTRIES_RELATIVE,
)
from tracks.effects.fake_design import (
    _REACH_ENTRY_SCAFFOLD_LINE as _REACH_ENTRY_SCAFFOLD_LINE,
)
from tracks.effects.fake_design import (
    _SCAFFOLD_HEADING as _SCAFFOLD_HEADING,
)
from tracks.effects.fake_design import (
    _SCAFFOLD_RESERVED_ROOTS as _SCAFFOLD_RESERVED_ROOTS,
)
from tracks.effects.fake_design import (
    _SPEC_ITEM as _SPEC_ITEM,
)
from tracks.effects.fake_design import (
    SPEC_TEMPLATE as SPEC_TEMPLATE,
)
from tracks.effects.fake_design import (
    FakeDesignMixin as FakeDesignMixin,
)
from tracks.effects.fake_design import (
    _story_title as _story_title,
)
from tracks.effects.fake_devon_act import (
    _DEVON_FAILURE_TOKENS as _DEVON_FAILURE_TOKENS,
)
from tracks.effects.fake_devon_act import (
    _DEVON_PHASES as _DEVON_PHASES,
)
from tracks.effects.fake_devon_act import (
    FakeDevonActMixin as FakeDevonActMixin,
)
from tracks.effects.fake_plan import (
    FakePlanMixin as FakePlanMixin,
)
from tracks.effects.fake_reply import (
    _FAKE_PARITY_PROVENANCE as _FAKE_PARITY_PROVENANCE,
)
from tracks.effects.fake_reply import (
    _FAKE_SPEAKABLE_KINDS as _FAKE_SPEAKABLE_KINDS,
)
from tracks.effects.fake_reply import (
    FakeReplyMixin as FakeReplyMixin,
)
from tracks.effects.fake_shield import (
    _FAILED_TOKENS as _FAILED_TOKENS,
)
from tracks.effects.fake_shield import (
    FakeShieldMixin as FakeShieldMixin,
)
from tracks.effects.fake_shield import (
    _ac_slug as _ac_slug,
)


class FakeBackend(
    DevonPatchMixin,
    FakeShieldMixin,
    FakeActMixin,
    FakeDesignMixin,
    FakeDevonActMixin,
    FakePlanMixin,
    FakeReplyMixin,
):
    """Deterministic AgentBackend (v0.1 FakeAgent, relocated to effects/)."""

    # IF-ENVELOPE-002: the contract version THIS implementation actually
    # speaks — an independent, reviewed declaration, deliberately NOT an
    # alias of kernel ENVELOPE_VERSION (a stale implementation must keep its
    # old literal so the pre-dispatch parity gate sees the mismatch against
    # the Runtime authority instead of silently claiming the current one).
    envelope_version = 2

    def __init__(self, repo: Path, version: str):
        self.repo = repo
        self.version = version
        self._calls: dict = {}
        self._design_revisions = 0
        self._shield_writes = 0
        # B1 (issue #2): set per act() call; writer fakes write here so the
        # executor's replay-to-main path is exercised like production.
        self._worktree_root: Path | None = None
