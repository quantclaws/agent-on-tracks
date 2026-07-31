"""Command executor: write-ahead `command.issued` (FR-30), per-kind
execute + reconcile (D-13), agent dispatch via the effects backend seam
(NFR-01; ARCH-003 §4), validate pass-through (D-16).
"""
from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

from tracks import paths
from tracks.baseline import baseline_summary, revision_digest
from tracks.effects import select_backend
from tracks.effects.github import GithubIssuesError, issue_items, select_issue_backend
from tracks.executor.validate import validate_document
from tracks.frontmatter import doc_body_sha, set_frontmatter_field
from tracks.kernel.events import Command
from tracks.kernel.machine import State, decide
from tracks.store import Store, new_ulid


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc


def _commit_if_staged(repo: Path, message: str) -> None:
    if git(repo, "diff", "--cached", "--quiet", check=False).returncode != 0:
        git(repo, "commit", "-m", message)


# Stage-transition table (design §1, single source of truth): EXIT seal -> next
# stage.entered; stages absent here (M-REQ-APPROVAL) end the run at the
# M-DESIGN boundary: run.completed(terminal_state="boundary") (SM-05.6).
_NEXT_STAGE = {"M-STORY": "M-SPEC", "M-SPEC": "M-ACC", "M-ACC": "M-REQ-APPROVAL"}

# doc -> (committed event type, body-sha payload key)
_COMMITTED_EVENT = {
    "story.md": ("story.committed", "story_sha"),
    "spec.md": ("spec.committed", "spec_sha"),
    "acceptance.md": ("acceptance.committed", "acceptance_sha"),
}


class Executor:
    """Drives one run: project -> decide -> issue -> execute -> observe."""

    def __init__(self, store: Store, repo: Path, run_id: str):
        self.store = store
        self.repo = repo
        self.run_id = run_id
        self.version = store.state(run_id).version or ""
        self.backend = select_backend(repo, self.version)
        self._issue_backend = None  # lazy: created on first create_issues

    def _emit(self, type: str, payload: dict,
              command_id: str | None = None, task_id: str | None = None):
        return self.store.append(self.run_id, self.version, type, payload,
                                 command_id=command_id, task_id=task_id)

    def _doc_path(self, doc: str) -> Path:
        return paths.version_dir(self.store.home, self.version) / doc

    # -- main loop (FR-29/FR-30) -------------------------------------------

    def run_loop(self) -> State:
        self._recover()
        while True:
            state = self.store.state(self.run_id)
            cmd = decide(state)
            if cmd is None:
                return state
            self.issue(cmd)

    def issue(self, cmd: Command) -> None:
        """Write-ahead log `cmd` (FR-30), then execute it; the per-kind handler
        logs the result event that closes it. command_id is assigned here so the
        result pairs with the issued record. Used by run_loop and by one-shot
        setup commands (create_branch in `trac start`)."""
        state = self.store.state(self.run_id)
        cid = new_ulid()
        issued = Command(kind=cmd.kind, params=cmd.params, command_id=cid)
        task_id = None
        if cmd.kind == "dispatch_agent":
            task_id = (f"{self.run_id}:{cmd.params.get('substate')}"
                       f":{state.review_round}:{state.current_attempt}")
        self._emit(
            "command.issued",
            {"command": {"kind": issued.kind, "params": issued.params,
                         "command_id": cid}},
            command_id=cid, task_id=task_id,
        )
        self._execute(issued, self.store.state(self.run_id), task_id)

    def _recover(self) -> None:
        """Hanging command (issued, no result): reconcile first (D-13), reissue
        the same assignment without consuming an attempt (D-11)."""
        state = self.store.state(self.run_id)
        if state.pending:
            cmd = Command(kind=state.pending["kind"],
                          params=state.pending.get("params", {}),
                          command_id=state.pending.get("command_id"))
            self._execute(cmd, state, None, reconcile=True)

    def _execute(self, cmd: Command, state: State,
                 task_id: str | None, reconcile: bool = False) -> None:
        getattr(self, "_do_" + cmd.kind)(cmd, state, task_id, reconcile)

    # -- per-kind handlers ---------------------------------------------------

    def _do_dispatch_agent(self, cmd, state, task_id, reconcile):
        p = cmd.params
        role, substate, doc = p["role"], p["substate"], p.get("doc")
        doc_path = self._doc_path(doc) if doc else None
        result = self.backend.act(role, substate, doc, doc_path)
        payload = {"role": role, "status": result["status"],
                   "artifact_ref": result.get("artifact_ref"),
                   "self_report": result["self_report"]}
        # IF-003 §1 additive Outcome fields (OpencodeBackend; absent for fake):
        # carry the authoritative diff / audit evidence / failure classification
        # into the event so over-reach and failures are observable (AC-0301..0306).
        for key in ("diff_ref", "audit_evidence", "failure_class"):
            if result.get(key) is not None:
                payload[key] = result[key]
        self._emit("outcome.received", payload,
                   command_id=cmd.command_id, task_id=task_id)
        verdict = result.get("verdict")
        if verdict:
            ev = "sage.verdict" if role == "sage" else "lex.verdict"
            self._emit(ev, {"verdict": verdict, "diff_ref": None},
                       command_id=cmd.command_id, task_id=task_id)

    def _do_validate_document(self, cmd, state, task_id, reconcile):
        doc = cmd.params["doc"]
        path = self._doc_path(doc)
        checks = cmd.params.get("checks") or []
        # token() is FakeBackend's optional TRAC_FAKE_SIMULATE hook; production
        # backends (OpencodeBackend) implement only act() -> "ok" (no simulation).
        token = getattr(self.backend, "token", lambda *_: "ok")("validator", doc, "ok")
        failure = validate_document(path, doc, checks)
        if failure is None and token != "ok":
            failure = ("schema", f"simulated failure: {token}")
        if failure:
            check, reason = failure
            payload = {"check": check, "reason": reason, "evidence": str(path)}
            if check != "scope_overflow":  # FR-20 rolls back, never escalates
                payload["attempt"] = state.current_attempt + 1
            self._emit("verdict.failed", payload, command_id=cmd.command_id)
        else:
            self._emit("verdict.passed",
                       {"check": ",".join(checks) or "schema", "detail": "format checks"},
                       command_id=cmd.command_id)

    def _do_commit_document(self, cmd, state, task_id, reconcile):
        doc, message = cmd.params["doc"], cmd.params["message"]
        marker = f"command_id: {cmd.command_id}"
        if reconcile:
            probe = git(self.repo, "log", "--grep", marker,
                        "--format=%H", check=False)
            if probe.stdout.split():
                self._emit_committed(doc, probe.stdout.split()[0], cmd.command_id)
                return
        git(self.repo, "add", str(self._doc_path(doc)))
        _commit_if_staged(self.repo, f"{message}\n\n{marker}")
        commit_sha = git(self.repo, "rev-parse", "HEAD").stdout.strip()
        self._emit_committed(doc, commit_sha, cmd.command_id)

    def _emit_committed(self, doc, commit_sha, command_id, final=False):
        ev_type, sha_key = _COMMITTED_EVENT[doc]
        self._emit(ev_type,
                   {"commit_sha": commit_sha,
                    sha_key: doc_body_sha(self._doc_path(doc)),
                    "final": final},
                   command_id=command_id)

    def _do_write_frontmatter(self, cmd, state, task_id, reconcile):
        """EXIT seal (FR-17/FR-23): body sha256 -> frontmatter `sha` -> commit ->
        stage.exited (+ next stage.entered / run.completed). Without a `doc`
        (M-REQ-APPROVAL boundary, SM-05.6) there is nothing to seal."""
        doc, stage = cmd.params.get("doc"), cmd.params["stage"]
        if reconcile and state.stage_exited:
            return
        if doc:
            path = self._doc_path(doc)
            set_frontmatter_field(path, "sha", doc_body_sha(path))
            git(self.repo, "add", str(path))
            _commit_if_staged(self.repo,
                              f"{stage}: seal {doc} sha\n\ncommand_id: {cmd.command_id}")
            # R4-02: the sealed commit gets its own final committed event so ACs
            # match `final=true` and never the DRAFT-stage commit.
            commit_sha = git(self.repo, "rev-parse", "HEAD").stdout.strip()
            self._emit_committed(doc, commit_sha, cmd.command_id, final=True)
        self._emit("stage.exited", {"stage": stage}, command_id=cmd.command_id)
        nxt = _NEXT_STAGE.get(stage)
        if nxt:
            self._emit("stage.entered", {"stage": nxt},
                       command_id=cmd.command_id)
        else:
            # SM-05.6 / IF-003 §10f: no successor -> stop at the M-DESIGN boundary.
            self._emit("run.completed", {"terminal_state": "boundary"},
                       command_id=cmd.command_id)

    def _do_record_backlog(self, cmd, state, task_id, reconcile):
        if reconcile and state.backlog_recorded:
            return
        self._emit("backlog.recorded", dict(cmd.params),
                   command_id=cmd.command_id)

    def _do_complete_run(self, cmd, state, task_id, reconcile):
        # Branch deletion is a separate delete_branch command (FR-09), not here.
        self._emit("run.completed", {"terminal_state": cmd.params["terminal_state"]},
                   command_id=cmd.command_id)

    def _do_create_branch(self, cmd, state, task_id, reconcile):
        """Create/switch the branch, then log branch.created. Reconcile (R3-03):
        the git work is skipped iff the branch exists AND HEAD is already on it;
        branch.created is always logged so the command closes."""
        branch = cmd.params["branch_name"]
        base = cmd.params.get("base", "main")
        exists = git(self.repo, "rev-parse", "--verify", branch,
                     check=False).returncode == 0
        if not (exists and self._head() == branch):
            if exists:
                git(self.repo, "checkout", branch)
            else:
                git(self.repo, "checkout", "-b", branch, base)
        commit_sha = git(self.repo, "rev-parse", "HEAD").stdout.strip()
        self._emit("branch.created",
                   {"branch_name": branch, "base": base, "commit_sha": commit_sha},
                   command_id=cmd.command_id)

    def _do_delete_branch(self, cmd, state, task_id, reconcile):
        """Tear down the branch, then log branch.deleted. Reconcile (R3-03):
        _teardown_branch is idempotent (done iff HEAD==main AND branch absent)."""
        branch = cmd.params["branch_name"]
        self._teardown_branch(branch)
        self._emit("branch.deleted", {"branch_name": branch},
                   command_id=cmd.command_id)

    def _do_rollback_stage(self, cmd, state, task_id, reconcile):
        self._emit("stage.rolled_back",
                   {"from_stage": state.stage,
                    "to_stage": cmd.params["to_stage"],
                    "reason": cmd.params.get("reason", "")},
                   command_id=cmd.command_id)

    # -- M-REQ-APPROVAL handlers (FR-0180/0190/0200) ---------------------------

    def _vdir(self) -> Path:
        return paths.version_dir(self.store.home, self.version)

    def _do_generate_preview(self, cmd, state, task_id, reconcile):
        if reconcile and state.preview_ready:
            return
        vdir = self._vdir()
        self._emit("preview.generated",
                   {"digest": revision_digest(vdir),
                    "summary": baseline_summary(vdir)},
                   command_id=cmd.command_id)

    def _stale_regenerate(self, cmd, approved_digest: str) -> bool:
        """FR-0190 entry gate (D-02/D-03): post-approval commands recompute the
        trio digest; a mismatch means the approval is stale — regenerate the
        preview (back to the human gate) instead of proceeding downstream."""
        vdir = self._vdir()
        current = revision_digest(vdir)
        if current == approved_digest:
            return False
        self._emit("preview.generated",
                   {"digest": current, "summary": baseline_summary(vdir)},
                   command_id=cmd.command_id)
        return True

    def _do_record_approval(self, cmd, state, task_id, reconcile):
        if reconcile and state.substate == "ISSUES":
            return
        if self._stale_regenerate(cmd, cmd.params["digest"]):
            return
        self._emit("approval.recorded",
                   {"actor": cmd.params["actor"], "digest": cmd.params["digest"],
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "readonly": True},
                   command_id=cmd.command_id)

    def _do_create_issues(self, cmd, state, task_id, reconcile):
        digest = cmd.params["digest"]
        if reconcile and state.issues_created:
            return
        if self._stale_regenerate(cmd, digest):
            return
        if self._issue_backend is None:
            self._issue_backend = select_issue_backend(self.repo, self.version)
        backend = self._issue_backend
        # D-06 breakpoint resume: item_ids already logged are never rebuilt.
        done = {e.payload["item_id"]: e.payload["issue_id"]
                for e in self.store.events(self.run_id)
                if e.type == "issue.created"}
        try:
            for item_id, title, body in issue_items(self._vdir(), digest):
                if item_id in done:
                    continue
                issue_id = backend.create_issue(title, body, [self.version])
                backend.add_to_project(issue_id, backend.project)
                done[item_id] = issue_id
                self._emit("issue.created",
                           {"item_id": item_id, "issue_id": issue_id,
                            "digest": digest},
                           command_id=cmd.command_id)
        except GithubIssuesError as e:
            # NFR-0030 style: no half-written summary; the reducer counts the
            # failed outcome as an attempt (3rd escalates to Human).
            self._emit("outcome.received",
                       {"role": "github", "status": "failed",
                        "failure_class": e.classification,
                        "self_report": str(e)},
                       command_id=cmd.command_id)
            return
        self._emit("issues.created",
                   {"digest": digest, "mapping": done,
                    "project": backend.project},
                   command_id=cmd.command_id)

    # -- git helpers -----------------------------------------------------------

    def _head(self) -> str:
        return git(self.repo, "symbolic-ref", "--short", "HEAD",
                   check=False).stdout.strip()

    def _teardown_branch(self, branch: str) -> None:
        """FR-09 / reconcile-safe: end with HEAD==main and branch absent."""
        if self._head() != "main":
            git(self.repo, "checkout", "main")
        if git(self.repo, "rev-parse", "--verify", branch,
               check=False).returncode == 0:
            git(self.repo, "branch", "-D", branch)
