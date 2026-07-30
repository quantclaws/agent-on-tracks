"""Command executor: write-ahead `command.issued` (FR-30), per-kind
execute + reconcile (D-13), FakeAgent dispatch (NFR-01), validate
pass-through (D-16).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from tracks import paths
from tracks.executor.fake_agent import FakeAgent
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


class Executor:
    """Drives one run: project -> decide -> issue -> execute -> observe."""

    def __init__(self, store: Store, repo: Path, run_id: str):
        self.store = store
        self.repo = repo
        self.run_id = run_id
        self.version = store.state(run_id).version or ""
        self.fake = FakeAgent(repo, self.version)

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
        result = self.fake.act(role, substate, doc, doc_path)
        self._emit("outcome.received",
                   {"role": role, "status": result["status"],
                    "artifact_ref": result.get("artifact_ref"),
                    "self_report": result["self_report"]},
                   command_id=cmd.command_id, task_id=task_id)
        verdict = result.get("verdict")
        if verdict:
            ev = "sage.verdict" if role == "sage" else "lex.verdict"
            self._emit(ev, {"verdict": verdict, "diff_ref": None},
                       command_id=cmd.command_id, task_id=task_id)

    def _do_validate_document(self, cmd, state, task_id, reconcile):
        doc = cmd.params["doc"]
        path = self._doc_path(doc)
        token = self.fake.token("validator", doc, "ok")
        failure = validate_document(path, doc)
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
                       {"check": "schema", "detail": "v0.1 pass-through (D-16)"},
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
        sha = doc_body_sha(self._doc_path(doc))
        if doc == "story.md":
            self._emit("story.committed",
                       {"commit_sha": commit_sha, "story_sha": sha,
                        "final": final},
                       command_id=command_id)
        else:
            self._emit("spec.committed",
                       {"commit_sha": commit_sha, "spec_sha": sha,
                        "final": final},
                       command_id=command_id)

    def _do_write_frontmatter(self, cmd, state, task_id, reconcile):
        """EXIT seal (FR-17/FR-23): body sha256 -> frontmatter `sha` -> commit ->
        stage.exited (+ next stage.entered / run.completed)."""
        doc, stage = cmd.params["doc"], cmd.params["stage"]
        if reconcile and state.stage_exited:
            return
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
        if stage == "M-STORY":
            self._emit("stage.entered", {"stage": "M-SPEC"},
                       command_id=cmd.command_id)
        else:
            self._emit("run.completed", {"terminal_state": "completed"},
                       command_id=cmd.command_id)

    def _do_record_backlog(self, cmd, state, task_id, reconcile):
        if reconcile and state.backlog_recorded:
            return
        self._emit("backlog.recorded", dict(cmd.params),
                   command_id=cmd.command_id)

    def _do_complete_run(self, cmd, state, task_id, reconcile):
        terminal = cmd.params["terminal_state"]
        if terminal in ("no_go", "park"):
            self._teardown_branch(f"releases/{self.version}")
        self._emit("run.completed", {"terminal_state": terminal},
                   command_id=cmd.command_id)

    def _do_create_branch(self, cmd, state, task_id, reconcile):
        """Reconcile (R3-03): done iff branch exists AND HEAD is on it."""
        branch = cmd.params["branch_name"]
        base = cmd.params.get("base", "main")
        exists = git(self.repo, "rev-parse", "--verify", branch,
                     check=False).returncode == 0
        if exists and self._head() == branch:
            return
        if exists:
            git(self.repo, "checkout", branch)
        else:
            git(self.repo, "checkout", "-b", branch, base)

    def _do_delete_branch(self, cmd, state, task_id, reconcile):
        self._teardown_branch(cmd.params["branch_name"])

    def _do_rollback_stage(self, cmd, state, task_id, reconcile):
        self._emit("stage.rolled_back",
                   {"from_stage": state.stage,
                    "to_stage": cmd.params["to_stage"],
                    "reason": cmd.params.get("reason", "")},
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
