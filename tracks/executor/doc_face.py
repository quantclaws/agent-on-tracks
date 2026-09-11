"""Document validate/commit/frontmatter handlers extracted from the Executor
(mixin ``ExecDocMixin``)."""

from __future__ import annotations

from pathlib import Path

from tracks import paths
from tracks.executor.helpers import _scoped_commit_if_staged, git
from tracks.executor.result_checkpoint import _COMMITTED_EVENT
from tracks.executor.validate import validate_document
from tracks.frontmatter import doc_body_sha, set_frontmatter_field
from tracks.project import validate_layout
from tracks.scaffold import _scaffold_declared_paths

_SCAFFOLD_RESERVED_ROOTS = frozenset({".git", ".opencode", ".tracks"})


# D-XX scaffold safety: the canonical host test-execution contract location is
# the ONLY .tracks/** path a Scaffold 宣言 may declare (it is where the fake /
# production backend writes the collect/run contract consumed by M-TEST). Every
# other .tracks/** stays rejected (the tracked project documents live under
# .tracks/projects/ and are staged via _emit_committed, never via the manifest).
# The M-IMPL reach entrypoint manifest (`.tracks/reach-entries.txt`, declared
# only by the Fake fixture) is the single additional canonical config artifact
# the design ResultCheckpoint stages alongside the design trio.
_CANONICAL_CONTRACT_PATH = (".tracks", "projects", "project.toml")


_CANONICAL_REACH_ENTRIES_PATH = (".tracks", "reach-entries.txt")


# Stage-transition table (design §1, single source of truth): EXIT seal ->
# next stage.entered. v0.5: M-TEST -> M-IMPL (previously a boundary exit).
_NEXT_STAGE = {
    "M-STORY": "M-SPEC",
    "M-SPEC": "M-ACC",
    "M-ACC": "M-REQ-APPROVAL",
    "M-REQ-APPROVAL": "M-DESIGN",
    "M-DESIGN": "M-TEST",
    "M-TEST": "M-IMPL",
}


class ExecDocMixin:
    """M-DESIGN document handlers; scaffold/contract constants live here and are
imported by the other executor mixins."""

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
        # FR-0120: Archer must declare [layout.devon]/[layout.shield] in
        # project.toml; gate at M-DESIGN EXIT when architecture.md is validated.
        if failure is None and doc == "architecture.md" and state.stage == "M-DESIGN":
            layout_err = validate_layout(self.repo)
            if layout_err is not None:
                failure = ("layout", layout_err)
        if failure:
            check, reason = failure
            payload = {"check": check, "reason": reason, "evidence": str(path)}
            if check != "scope_overflow":  # FR-20 rolls back, never escalates
                payload["attempt"] = state.current_attempt + 1
            self._emit("verdict.failed", payload, command_id=cmd.command_id)
        else:
            self._emit(
                "verdict.passed",
                {"check": ",".join(checks) or "schema", "detail": "format checks"},
                command_id=cmd.command_id,
            )

    def _do_commit_document(self, cmd, state, task_id, reconcile):
        doc, message = cmd.params["doc"], cmd.params["message"]
        marker = f"command_id: {cmd.command_id}"
        if reconcile:
            probe = git(self.repo, "log", "--grep", marker, "--format=%H", check=False)
            if probe.stdout.split():
                self._emit_committed(doc, probe.stdout.split()[0], cmd.command_id)
                return
        doc_path = self._doc_path(doc)
        stage_paths = [doc_path]
        if state.stage == "M-DESIGN" and doc == "architecture.md":
            scaffold_paths, issue = self._design_scaffold_paths(doc_path)
            if issue is not None:
                self._emit_commit_failure_evidence(
                    state,
                    cmd.command_id,
                    "declared scaffold path rejected",
                    issue,
                )
                return
            # Dedup: the canonical project.toml may be both declared in the
            # manifest and returned by _project_contract_paths(); stage once.
            stage_paths.extend(dict.fromkeys([*scaffold_paths, *self._project_contract_paths()]))
        git(self.repo, "add", *(str(path) for path in stage_paths))
        proc = _scoped_commit_if_staged(
            self.repo, f"{message}\n\n{marker}", paths=stage_paths
        )
        if proc is not None and proc.returncode != 0:
            self._emit_commit_failure(proc, state, cmd.command_id)
            return
        commit_sha = git(self.repo, "rev-parse", "HEAD").stdout.strip()
        self._emit_committed(doc, commit_sha, cmd.command_id)

    def _design_scaffold_paths(self, architecture: Path) -> tuple[list[Path], str | None]:
        try:
            text = architecture.read_text(encoding="utf-8")
        except OSError as exc:
            return [], f"scaffold manifest unreadable: {exc}"
        paths = []
        for raw in sorted(_scaffold_declared_paths(text)):
            path, issue = self._stageable_scaffold_path(raw)
            if issue is not None:
                return [], issue
            assert path is not None
            paths.append(path)
        return paths, None

    def _project_contract_paths(self) -> list[Path]:
        """Return the project.toml path if it exists, for staging alongside
        architecture.md during M-DESIGN."""
        toml_path = paths.project_toml_path(paths.tracks_home(self.repo))
        return [toml_path] if toml_path.exists() else []

    def _stageable_scaffold_path(self, raw: str) -> tuple[Path | None, str | None]:
        raw_path = Path(raw)
        candidate = raw_path if raw_path.is_absolute() else self.repo / raw_path
        try:
            resolved = candidate.resolve(strict=False)
            relative = resolved.relative_to(self.repo.resolve())
        except (OSError, RuntimeError, ValueError):
            return None, f"scaffold path escapes repository: {raw}"
        if raw_path.is_absolute():
            return None, f"scaffold path is not allowed (must be repo-relative): {raw}"
        if (
            not relative.parts
            or relative.parts[0] in _SCAFFOLD_RESERVED_ROOTS
            and tuple(relative.parts)
            not in (_CANONICAL_CONTRACT_PATH, _CANONICAL_REACH_ENTRIES_PATH)
        ):
            # exactly the canonical .tracks/projects/project.toml and
            # .tracks/reach-entries.txt are allowed; every other .tracks/**
            # (and .git/.opencode/**) is rejected.
            return None, f"scaffold path is not allowed: {raw}"
        if candidate.is_symlink():
            return None, f"scaffold path is not allowed (symlink): {raw}"
        if not candidate.exists():
            return None, f"scaffold path is missing: {raw}"
        if candidate.is_dir():
            return None, f"scaffold path is a directory: {raw}"
        if not candidate.is_file():
            return None, f"scaffold path is not allowed: {raw}"
        relative_text = str(relative)
        tracked = (
            git(
                self.repo, "ls-files", "--error-unmatch", "--", relative_text, check=False
            ).returncode
            == 0
        )
        ignored = (
            git(
                self.repo, "check-ignore", "--quiet", "--no-index", "--", relative_text, check=False
            ).returncode
            == 0
        )
        if ignored and not tracked:
            return None, f"scaffold path is not allowed (ignored): {raw}"
        return self.repo / relative, None

    def _emit_committed(self, doc, commit_sha, command_id, final=False, result_id=None):
        ev_type, sha_key = _COMMITTED_EVENT[doc]
        payload = {
            "commit_sha": commit_sha,
            sha_key: doc_body_sha(self._doc_path(doc)),
            "final": final,
        }
        if result_id is not None:
            payload["result_id"] = result_id
        if ev_type == "design.committed":
            payload["doc"] = doc  # one event type serves all three design docs
        self._emit(ev_type, payload, command_id=command_id)

    def _do_write_frontmatter(self, cmd, state, task_id, reconcile):
        """EXIT seal (FR-17/FR-23): body sha256 -> frontmatter `sha` -> commit ->
        stage.exited (+ next stage.entered / run.completed). Without a `doc`
        (M-REQ-APPROVAL boundary SM-05.6; M-DESIGN EXIT, which writes nothing
        extra per flow.md §8) there is nothing to seal."""
        doc, stage = cmd.params.get("doc"), cmd.params["stage"]
        if reconcile and state.stage_exited:
            return
        if doc:
            path = self._doc_path(doc)
            set_frontmatter_field(path, "sha", doc_body_sha(path))
            git(self.repo, "add", str(path))
            proc = _scoped_commit_if_staged(
                self.repo,
                f"{stage}: seal {doc} sha\n\ncommand_id: {cmd.command_id}",
                paths=[path],
            )
            if proc is not None and proc.returncode != 0:
                self._emit_commit_failure(proc, state, cmd.command_id)
                return
            # R4-02: the sealed commit gets its own final committed event so ACs
            # match `final=true` and never the DRAFT-stage commit.
            commit_sha = git(self.repo, "rev-parse", "HEAD").stdout.strip()
            self._emit_committed(doc, commit_sha, cmd.command_id, final=True)
        self._emit("stage.exited", {"stage": stage}, command_id=cmd.command_id)
        nxt = _NEXT_STAGE.get(stage)
        if nxt and not self._is_boundary_transition(stage, nxt):
            self._emit("stage.entered", {"stage": nxt}, command_id=cmd.command_id)
        else:
            # SM-05.6 / IF-003 §10f: no successor (or a version-gated
            # successor) -> stop at the next-stage boundary. M-DESIGN boundary
            # pre-v0.3; M-TEST boundary for pre-v0.5 runs; M-IMPL boundary
            # since. v0.6 hotfix boundary restores the suspended run's branch.
            # v0.8 (IF-VERIFY-001): a RELEASE-capable run re-routes at the
            # boundary through the version capability seam (after_m_impl)
            # instead of completing -- the seam's None keeps the boundary.
            # Refresh the projection first: the stage.exited event above is
            # append-only and the handler's `state` snapshot predates it, so
            # after_m_impl (which gates on state.stage_exited) would always
            # see False and every RELEASE-capable run would silently complete
            # at the boundary instead of entering M-VERIFY.
            state = self.store.state(self.run_id)
            route = self._release_boundary_route(state)
            if route is not None:
                entered = (route.params or {}).get("stage") or "M-VERIFY"
                self._emit(
                    "stage.entered",
                    {"stage": entered},
                    command_id=cmd.command_id,
                )
                self.issue(route)
                return
            self._emit(
                "run.completed",
                self._hotfix_boundary_completion(state),
                command_id=cmd.command_id,
            )
