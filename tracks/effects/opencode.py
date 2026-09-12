"""OpencodeBackend — real agent via `opencode run` subprocess (ARCH-003 §4/§6/§7).

Pipeline per dispatch: materialize canonical prompt (+ skills + document
templates named by the assignment) -> baseline snapshot ->
`opencode run --agent <Name> --format json --dir <repo> --auto "<prompt>"`
(process group) -> parse JSON (diagnostic/truncation only) -> capture
the target doc-set diff (authoritative product, ARCH §4b; one doc normally, the
whole M-DESIGN trio for a multi-doc assignment) -> post-run audit (ARCH §6:
over-reach; for M-DESIGN author dispatches, that host-project writes stay
within the architecture.md Scaffold 宣言 manifest (batch B); for author DRAFT
dispatches, that the agent resolved every discussion thread it initiated in the
target doc-set; for reviewer dispatches, that a revise verdict anchors its
findings — at least one open discussion thread the reviewer initiated, live
run042). Failures map to IF-003 §1a FailureClass. The agent's stdout JSON is
NEVER the product; the controlled target diff is.

Composition (C0302 split): the dispatch/result chain, session reuse, audit,
materialization, paths, prompt assembly, review payloads and the subprocess
pipeline live in :mod:`tracks.effects.opencode_act` /
``opencode_session`` / ``opencode_audit`` / ``opencode_materialize`` /
``opencode_paths`` / ``opencode_prompt`` / ``opencode_review`` /
``opencode_run`` and are composed into the single ``OpencodeBackend`` class
below. Every moved name is re-exported here, so the pre-split import surface
of this module is unchanged.
"""

from __future__ import annotations

import select as select  # noqa: F401  (explicit re-export: test seam)
import subprocess as subprocess  # noqa: F401  (explicit re-export: test seam)
from pathlib import Path

from tracks.scaffold import _scaffold_declared_paths as _scaffold_declared_paths

from .opencode_act import (
    OpencodeActMixin as OpencodeActMixin,
)
from .opencode_act import (
    _text as _text,
)
from .opencode_audit import (
    OpencodeAuditMixin as OpencodeAuditMixin,
)
from .opencode_audit import (
    _capture_target_diffs as _capture_target_diffs,
)
from .opencode_audit import (
    _discussion_snapshot as _discussion_snapshot,
)
from .opencode_audit import (
    _docset_text as _docset_text,
)
from .opencode_audit import (
    _load_head_bytes as _load_head_bytes,
)
from .opencode_audit import (
    _require_target_diff as _require_target_diff,
)
from .opencode_audit import (
    _unresolved_role_threads as _unresolved_role_threads,
)
from .opencode_core import (
    AGENT_NAME as AGENT_NAME,
)
from .opencode_core import (
    OpencodeError as OpencodeError,
)
from .opencode_core import (
    redact as redact,
)
from .opencode_materialize import (
    OpencodeMaterializeMixin as OpencodeMaterializeMixin,
)
from .opencode_materialize import (
    _skill_names as _skill_names,
)
from .opencode_materialize import (
    _template_kinds as _template_kinds,
)
from .opencode_paths import (
    OpencodePathsMixin as OpencodePathsMixin,
)
from .opencode_prompt import (
    OpencodePromptMixin as OpencodePromptMixin,
)
from .opencode_review import (
    OpencodeReviewMixin as OpencodeReviewMixin,
)
from .opencode_run import (
    OpencodeRunMixin as OpencodeRunMixin,
)
from .opencode_session import (
    OpencodeSessionMixin as OpencodeSessionMixin,
)


class OpencodeBackend(
    OpencodeActMixin,
    OpencodeAuditMixin,
    OpencodeMaterializeMixin,
    OpencodePathsMixin,
    OpencodePromptMixin,
    OpencodeReviewMixin,
    OpencodeRunMixin,
    OpencodeSessionMixin,
):
    """AgentBackend implemented by a real opencode subagent subprocess."""

    envelope_version = 2

    def __init__(
        self,
        repo: Path,
        version: str,
        model: str | None = None,
        debug: bool = False,
        run_id: str | None = None,
    ):
        self.repo = Path(repo)
        self.version = version
        # Model resolution is two-layer per dispatch (spec §3.1, ARCH §4a):
        # (1) explicit self.model (TRAC_AGENT_MODEL via select_backend) wins;
        # (2) else no --model flag, so opencode resolves its own configured
        #     default (agent/project config, never hardcoded by tracks).
        self.model = model
        self.debug = debug
        # D-39 用户简化版（#44）：session 复用。run_id 非空时，每个 agent
        # 在该 run 内有且只有一个 opencode session——所有派发（含 infra
        # 重派与 attempt 重试）一律 `--session <id>` 续传，id 持久化在
        # .tracks/runtime/sessions/<run_id>.json（跨 trac run 进程重启存
        # 活）。run_id 为 None（测试/一次性调用）时行为与旧版逐次全新
        # 派发完全一致。
        self.run_id = run_id
        self._sessions: dict[str, str] | None = None  # agent name -> session id（惰性加载）
        self._canonical = Path(__file__).resolve().parent.parent / "agents"
