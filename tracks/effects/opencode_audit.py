"""Post-run audit mixin for OpencodeBackend (ARCH §6, Blocker 1-3, D-37).

Extracted from ``opencode.py`` for module-size compliance (C0302). The
module-level helpers (``_capture_target_diffs`` and the doc-set discussion
readers) move with the audit family that consumes them.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from tracks.discuss.delta import is_discussion_delta
from tracks.discuss.gate import check_ready
from tracks.discuss.model import speaker_key
from tracks.discuss.parser import parse_threads
from tracks.effects.adjudicate import adjudicate
from tracks.effects.audit import Auditor, _rel
from tracks.scaffold import _scaffold_declared_paths

from .opencode_core import OpencodeError

_OPENCODE_PREFIX = ".opencode/"
_GROUND_TRUTH_PREFIX = "tests/ground_truth/"


def _capture_target_diffs(
    auditor: Auditor, doc_paths: list[Path], substate: str, proc: subprocess.CompletedProcess
) -> str | None:
    """Capture the controlled diff of every target doc (the authoritative
    product) BEFORE any audit rollback can touch it, then enforce the
    author-must-produce contract on the captured set."""
    diffs = [auditor.target_diff(path) for path in doc_paths]
    _require_target_diff(doc_paths, diffs, substate, proc)
    return "\n".join(diff for diff in diffs if diff) or None

def _load_head_bytes(repo: Path, rel: str, head: str) -> bytes | None:
    """Load the content of ``rel`` as of ``head`` from git, or None if the
    path is not tracked at that commit (new/untracked file)."""
    proc = subprocess.run(
        ["git", "show", f"{head}:{rel}"],
        cwd=repo,
        capture_output=True,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout

def _require_target_diff(
    doc_paths: list[Path], diffs: list[str | None], substate: str, proc: subprocess.CompletedProcess
) -> None:
    """An author dispatch must leave a controlled diff; exit 0 with no product
    is classified, never trusted.

    DRAFT must produce a diff on EVERY doc of its target set (a draft that
    skips a doc has no product for it). RESPOND revises only the docs that
    carry findings - the reviewer may have flagged a subset, so a diff on ANY
    doc of the set satisfies the contract; a true no-change RESPOND (zero
    diffs across the whole set) still fails."""
    if substate not in ("DRAFT", "RESPOND"):
        return
    if substate == "RESPOND":
        # Any diff in the set is a real revision; only zero diffs across the
        # whole set is the no-product failure.
        if not doc_paths or not any(d is not None for d in diffs):
            raise OpencodeError(
                "no_target_diff",
                "exit 0 but the target doc-set has no diff",
                exit_code=0,
                stderr=proc.stderr,
            )
        return
    missing = [str(path) for path, diff in zip(doc_paths, diffs, strict=True) if diff is None]
    if not doc_paths or missing:
        raise OpencodeError(
            "no_target_diff",
            "exit 0 but the target doc-set has no diff"
            + (f": {', '.join(missing)}" if missing else ""),
            exit_code=0,
            stderr=proc.stderr,
        )

def _unresolved_role_threads(doc_paths: list[Path], role: str) -> list[str]:
    """Threads in the target doc-set INITIATED by ``role`` and not resolved.

    Audit predicate: ``speaker_key(initiator) == speaker_key(role)`` and
    ``status != "resolved"`` (open and reopen both block, as in the discussion
    gate), checked across the WHOLE doc-set. For author DRAFT dispatches any
    such thread is an offence (closure unverified); for reviewer dispatches
    these threads ARE the anchored findings a revise verdict must carry. With
    a single target doc the offenders are thread ids; a multi-doc set (the
    M-DESIGN trio) names them ``doc:T-NNN`` so the ids stay unambiguous."""
    role_key = speaker_key(role)
    multi = len(doc_paths) > 1
    offending = []
    for path in doc_paths:
        if not path.exists():
            continue
        for thread in parse_threads(path.read_text(encoding="utf-8")):
            if speaker_key(thread.initiator) == role_key and thread.status != "resolved":
                label = f"{path.name}:{thread.thread_id}" if multi else thread.thread_id
                offending.append(label)
    return offending

def _docset_text(doc_paths: list[Path]) -> str:
    """Discussion text of a target doc-set: one doc reads as itself (the
    pre-multi-doc behavior); a set concatenates every existing doc."""
    texts = [path.read_text(encoding="utf-8") for path in doc_paths if path.exists()]
    return "\n\n".join(texts)

def _discussion_snapshot(text: str) -> dict:
    ready, blockers = check_ready(text)

    def comment(node):
        return {
            "speaker": node.speaker,
            "body": node.body,
            "depth": node.depth,
            "children": [comment(child) for child in node.children],
        }

    return {
        "ready": ready,
        "blockers": list(blockers),
        "threads": [
            {
                "thread_id": thread.thread_id,
                "initiator": thread.initiator,
                "status": thread.status,
                "reply_count": thread.reply_count,
                "root": comment(thread.root),
            }
            for thread in parse_threads(text)
        ],
    }


class OpencodeAuditMixin:
    """Atomic write audits + discussion-state outcome classification."""

    def _non_shield_write_audit(
        self,
        auditor: Auditor,
        baseline: set[str],
        scaffold_baseline: set[str] | None,
        doc_paths: list[Path],
        role: str,
        author_assignment: bool,
        diff_ref: str | None,
        proc: subprocess.CompletedProcess,
        prompt: str,
        console_input: str | None,
    ) -> dict | None:
        """Atomic post-write audit for every non-Shield role (Shield WRITE
        parity, Blocker 2): the commentable-doc discussion-only check and the
        generic over-reach + batch B scaffold checks are judged together, so
        the generic guard never early-returns and leaves an allowed-but-
        illegal doc edit behind.  Any failure force-rolls-back every agent-
        attributable path in ONE pass; pre-existing Human dirty content is
        restored byte-identical from the pre-dispatch snapshot.

        Blocker 1: the commentable-doc set is not filtered by post-state
        ``exists()`` - a doc the agent deleted or swapped for an exception
        type is still audited (``_check_doc_deltas`` is type-aware/no-follow),
        so deletions and type swaps roll back."""
        agent_changed = auditor.agent_changed_paths(baseline)
        doc_offending = self._check_doc_deltas(
            auditor, self._commentable_doc_paths(role), agent_changed
        )
        over_paths = self._overreach_paths(auditor, agent_changed)
        scaffold_offending = self._scaffold_offending(
            auditor, scaffold_baseline, doc_paths, author_assignment
        )
        if not (doc_offending or over_paths or scaffold_offending):
            return None
        if scaffold_offending and not (doc_offending or over_paths):
            return self._undeclared_scaffold_failure(
                auditor,
                baseline,
                scaffold_offending,
                diff_ref,
                proc,
                prompt,
                console_input,
            )
        # A non-discussion doc edit or a true over-reach invalidates the whole
        # run (not just the offending paths): force-roll back every
        # agent-attributable path in one pass, restoring Human's pre-dispatch
        # dirty content byte-identical from the snapshot.
        auditor.rollback_agent_changes(baseline, new_changes=agent_changed, force=True)
        return self._overreach_result(
            diff_ref,
            proc,
            prompt,
            console_input,
            evidence=self._overreach_evidence(doc_offending, over_paths),
        )

    def _undeclared_scaffold_failure(
        self,
        auditor: Auditor,
        baseline: set[str],
        scaffold_offending: list[str],
        diff_ref: str | None,
        proc: subprocess.CompletedProcess,
        prompt: str,
        console_input: str | None,
    ) -> dict:
        # Only the undeclared scaffold writes are non-compliant; the declared
        # target docs are compliant and MUST be preserved (user contract:
        # revert only non-compliant files, never the framework files inside
        # the declared doc-set). Roll back ONLY the offending paths and tell
        # the Agent exactly what was reverted and why, so the re-dispatch does
        # not recreate them. Restoring to the pre-dispatch snapshot (not HEAD)
        # is handled by the Auditor's baseline capture for any pre-dirty
        # offending path.
        rolled = auditor.rollback_agent_changes(
            baseline, new_changes=set(scaffold_offending), force=True
        )
        return self._undeclared_scaffold_result(
            diff_ref,
            scaffold_offending,
            proc,
            prompt,
            console_input,
            rolled=rolled,
        )

    @staticmethod
    def _overreach_paths(auditor: Auditor, agent_changed: set[str]) -> list[str]:
        """Agent-attributable paths outside the allowed set.

        ``_is_allowed`` only matches exact/prefix paths; repo-root trust
        (``"."`` in the allowed set) must short-circuit to no over-reach,
        mirroring Auditor.audit's FR-030 coarse-grain semantics.
        """
        if "." in auditor.allowed:
            return []
        return sorted(p for p in agent_changed if not auditor._is_allowed(p))

    @staticmethod
    def _overreach_evidence(doc_offending: list[str], over_paths: list[str]) -> str:
        """Combined evidence string for a failed write audit."""
        parts: list[str] = []
        if doc_offending:
            parts.append("non-discussion edit to " + ", ".join(sorted(doc_offending)))
        if over_paths:
            parts.append("over-reach: " + ", ".join(over_paths))
        return "; ".join(parts)

    def _scaffold_offending(
        self,
        auditor: Auditor,
        scaffold_baseline: set[str] | None,
        doc_paths: list[Path],
        author_assignment: bool,
    ) -> list[str]:
        """batch B scaffold offending paths (M-DESIGN author only): every
        run-produced write outside the doc-set, ``.opencode/**`` and
        ``tests/ground_truth/**`` that the freshly-written architecture.md
        Scaffold 宣言 does not enumerate."""
        if scaffold_baseline is None or not author_assignment or len(doc_paths) <= 1:
            return []
        arch = next((p for p in doc_paths if p.name == "architecture.md"), None)
        declared = (
            _scaffold_declared_paths(arch.read_text(encoding="utf-8"))
            if arch is not None and arch.exists()
            else set()
        )
        docset = {_rel(self.repo, path) for path in doc_paths}
        new_files = auditor.file_level(auditor.modified_files()) - scaffold_baseline
        return sorted(
            path
            for path in new_files
            if path not in docset
            and not path.startswith((_OPENCODE_PREFIX, _GROUND_TRUTH_PREFIX))
            and path not in declared
        )

    def _shield_write_audit(
        self,
        auditor: Auditor,
        baseline: set[str],
        doc_paths: list[Path],
        diff_ref: str | None,
        proc: subprocess.CompletedProcess,
        prompt: str,
        console_input: str | None,
    ) -> dict | None:
        """Shield WRITE atomic audit: doc-delta + over-reach.

        Rollback granularity (D-37 disposition, v0.5 minimal landing):
        - Pure over-reach (over_paths only, adjudicated DENY or not adjudicated):
          revert ONLY the offending paths - the same partial pattern as the
          scaffold branch - so compliant test-asset writes survive in the
          working tree for the re-dispatch to build on (D-37: "回滚保留合规,
          拒绝理由必回流"; the 74-minute compliant rewrite of run 01KZTHE7
          attempt 3 was force-discarded over one wiki-file edit on 2026-08-15).
        - Doc-delta failure (bypassing the discussion protocol on a commentable
          doc) still invalidates the whole run: the write protocol itself was
          violated, so the tree is not trustworthy (fail-closed, unchanged)."""
        agent_changed = auditor.agent_changed_paths(baseline)
        doc_offending = self._check_doc_deltas(auditor, doc_paths, agent_changed)
        over_paths = sorted(p for p in agent_changed if not auditor._is_allowed(p))
        if doc_offending or over_paths:
            if over_paths and not doc_offending and adjudicate(self, "shield", over_paths):
                return None
            if over_paths and not doc_offending:
                auditor.rollback_agent_changes(
                    baseline, new_changes=set(over_paths), force=True
                )
            else:
                auditor.rollback_agent_changes(
                    baseline, new_changes=agent_changed, force=True
                )
            return self._overreach_result(
                diff_ref,
                proc,
                prompt,
                console_input,
                evidence=self._overreach_evidence(doc_offending, over_paths),
            )
        return None

    def _check_doc_deltas(
        self,
        auditor: Auditor,
        doc_paths: list[Path],
        agent_changed: set[str],
    ) -> list[str]:
        """Return repo-relative paths of assignment docs whose post-agent delta
        is NOT a canonical discussion reply.

        For each doc:
        - **Type-aware touch detection** (no-follow): a symlink or directory
          replacement is always offending.  For regular files, the agent
          touched the doc only if its current bytes differ from the pre-dispatch
          snapshot (pre-dirty) or it entered ``agent_changed`` (clean->changed).
        - **Delta validation against the pre-dispatch snapshot** (Blocker 1):
          when the doc was pre-dirty, the base text is the snapshot bytes (NOT
          HEAD); when clean at dispatch, HEAD is the correct base.  A canonical
          discussion reply appended to Human-dirty body content thus passes;
          removing/replacing Human content fails.
        """
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.repo,
            capture_output=True,
            text=True,
        ).stdout.strip()
        return [
            _rel(self.repo, doc)
            for doc in doc_paths
            if self._doc_offending(auditor, doc, agent_changed, head)
        ]

    def _doc_offending(
        self,
        auditor: Auditor,
        doc_path: Path,
        agent_changed: set[str],
        head: str,
    ) -> bool:
        """True if this assignment doc's post-agent delta is NOT a canonical
        discussion reply.

        Type-aware, no-follow (Blocker 3): a symlink or directory replacement
        is always offending.  For regular files, delegates to the pre-dirty
        (snapshot-based) or clean (HEAD-based) delta check."""
        if doc_path.is_symlink() or doc_path.is_dir():
            return True
        rel = _rel(self.repo, doc_path)
        snap = auditor.baseline_content(rel)
        if snap is not None:
            return self._predirty_doc_offending(doc_path, snap)
        if rel in agent_changed:
            return self._clean_doc_offending(doc_path, rel, head)
        return False

    def _predirty_doc_offending(self, doc_path: Path, snap: bytes) -> bool:
        """Blocker 1: a pre-dirty doc's delta is validated against the
        pre-dispatch byte snapshot, NOT HEAD.  A canonical discussion reply
        appended to Human-dirty body content passes; removing/replacing Human
        content fails."""
        try:
            current = doc_path.read_bytes()
        except OSError:
            return True
        if current == snap:
            return False  # not touched
        return not is_discussion_delta(snap, current)

    def _clean_doc_offending(
        self,
        doc_path: Path,
        rel: str,
        head: str,
    ) -> bool:
        """Clean at dispatch, now changed: HEAD is the correct base.  A doc
        new at HEAD (untracked) or whose delta is not discussion-only is
        offending."""
        try:
            current = doc_path.read_bytes()
        except OSError:
            return True
        head_bytes = _load_head_bytes(self.repo, rel, head)
        return head_bytes is None or not is_discussion_delta(head_bytes, current)

    def _discussion_audit_result(
        self,
        role: str,
        substate: str,
        doc_paths: list[Path],
        diff_ref: str | None,
        proc: subprocess.CompletedProcess,
        prompt: str,
        console_input: str | None,
        reviewer_assignment: bool,
        structured_findings: bool = False,
    ) -> dict | None:
        """Discussion-state audits (flow.md 不变量 6 / arch.md 永不信自述): the
        outcome is classified from the doc's discussion state, never the
        agent's report. Returns the failed-outcome result, or None to pass.

        D-35 (SC-D35 §2.1): M-TEST/M-IMPL reviewers may deliver findings via
        the structured manifest channel instead of doc-anchored threads;
        ``structured_findings`` marks a validated payload, which exempts the
        revise from the doc-thread requirement. M-DESIGN never sets it
        (doc-anchored channel unchanged, AC-FR0240-04)."""
        if substate == "DRAFT":
            # Author DRAFT (live run041): every thread the author initiated
            # must be resolved before the dispatch may finish. RESPOND and
            # reviewer dispatches are exempt: replies/findings legitimately
            # stay open for the other party's next round.
            offending = _unresolved_role_threads(doc_paths, role)
            if offending:
                return self._unresolved_threads_result(
                    diff_ref,
                    offending,
                    proc,
                    prompt,
                    console_input,
                )
        if reviewer_assignment:
            # Reviewer contract (live run042): a REVISE verdict must anchor
            # every blocking finding as a discussion thread the reviewer
            # INITIATED via `trac discuss start`; a bare revise gives the
            # author's RESPOND nothing anchored to reply to. Verdict revise
            # with no open reviewer-initiated thread in the doc-set ->
            # classified failure; verdict pass stays legal without threads.
            ready, blockers = check_ready(_docset_text(doc_paths))
            if (
                not ready
                and not _unresolved_role_threads(doc_paths, role)
                and not structured_findings
            ):
                return self._revise_without_findings_result(
                    diff_ref,
                    role,
                    blockers,
                    proc,
                    prompt,
                    console_input,
                )
        return None
