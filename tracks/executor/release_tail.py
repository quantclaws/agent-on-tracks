"""M-REQ-APPROVAL / issue-creation handlers extracted from the Executor
(mixin ``ExecReleaseTailMixin``)."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from tracks import paths
from tracks.baseline import baseline_summary, revision_digest
from tracks.effects.github import (
    FakeIssueBackend,
    GithubIssuesError,
    create_issue_verified,
    issue_items,
    persist_issue_mapping,
    reject_fake_artifact,
    select_issue_backend,
)


class ExecReleaseTailMixin:
    """preview/approval/create-issues command handlers."""

    def _load_authoritative_issue_map(self) -> dict:
        """The authoritative issue map (IF-ISSUE-001): the closer vocabulary
        lives at .tracks/runtime/issue-map.json; missing/corrupt -> empty
        (nothing closable, never guessed)."""
        path = self.repo / ".tracks" / "runtime" / "issue-map.json"
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _vdir(self) -> Path:
        return paths.version_dir(self.store.home, self.version)

    def _do_generate_preview(self, cmd, state, task_id, reconcile):
        if state.stage == "M-RELEASE":
            # M-RELEASE face (SM-01.6, §1.0.5): the aggregate release
            # preview -- evidence digests + contract policy + operation
            # plan, content-addressed, then AWAITING_RELEASE via the
            # kernel reducer. The approval preview never fires here.
            candidate_sha = str(getattr(state, "candidate_sha", "") or "")
            if not candidate_sha:
                # Fail closed: a release preview without a frozen candidate
                # would mint an unbound preview digest (NFR-0143).
                self._emit(
                    "attention.required",
                    {
                        "area": "freeze",
                        "reason": "candidate_missing",
                        "stage": "M-RELEASE",
                        "detail": "release preview reached with no frozen candidate",
                        "next": "re-run M-VERIFY; trac run retries in place",
                    },
                    command_id=cmd.command_id,
                )
                return
            self._release_preview(cmd, candidate_sha)
            return
        if reconcile and state.preview_ready:
            return
        vdir = self._vdir()
        self._emit(
            "preview.generated",
            {"digest": revision_digest(vdir), "summary": baseline_summary(vdir)},
            command_id=cmd.command_id,
        )

    def _stale_regenerate(self, cmd, approved_digest: str) -> bool:
        """FR-0190 entry gate (D-02/D-03): post-approval commands recompute the
        trio digest; a mismatch means the approval is stale — regenerate the
        preview (back to the human gate) instead of proceeding downstream."""
        vdir = self._vdir()
        current = revision_digest(vdir)
        if current == approved_digest:
            return False
        self._emit(
            "preview.generated",
            {"digest": current, "summary": baseline_summary(vdir)},
            command_id=cmd.command_id,
        )
        return True

    def _do_record_approval(self, cmd, state, task_id, reconcile):
        if reconcile and state.substate == "ISSUES":
            return
        if self._stale_regenerate(cmd, cmd.params["digest"]):
            return
        self._emit(
            "approval.recorded",
            {
                "actor": cmd.params["actor"],
                "digest": cmd.params["digest"],
                "ts": datetime.now(timezone.utc).isoformat(),
                "readonly": True,
            },
            command_id=cmd.command_id,
        )

    def _map_issue_item(self, backend, item_id, title, body, digest, cmd, task_id):
        """(F) IF-ISSUE-001 per-item create + verify + persist.

        Real channel: create -> immediate API readback -> issue.mapped with
        api_verified=true and the authoritative issue-map persistence. The
        explicit fake stand-in channel is persisted api_verified=false so
        closers never consume it. A FAKE artifact surfacing on the real
        channel is rejected fail-closed (fake_rejected + blocked outcome);
        returns None then, else the issue id.

        Crash idempotence (FR-0283-04): the create seam receives the
        (repo_dir, repo_id, baseline_digest) dedup context so a crash-retry
        reuses an already-created issue (local authoritative map first, then
        the remote title+baseline search) instead of duplicating it. ``title``
        is part of the persisted identity for exactly that key.
        """
        repo_id = str(
            getattr(backend, "gh_repo", "") or os.environ.get("TRAC_GITHUB_REPO", "")
        )
        verified = create_issue_verified(
            backend,
            title,
            body,
            [self.version],
            repo_dir=self.repo,
            repo_id=repo_id,
            baseline_digest=digest,
        )
        issue_id = verified.get("issue_number")
        api_verified = bool(verified.get("api_verified"))
        if not api_verified and not isinstance(backend, FakeIssueBackend):
            rejection = reject_fake_artifact(issue_id)
            if rejection.get("status") == "rejected":
                self._emit(
                    "fake_rejected",
                    {
                        "item_id": item_id,
                        "issue_id": issue_id,
                        "reason": "fake_rejected",
                    },
                    command_id=cmd.command_id,
                    task_id=task_id,
                )
                self._emit(
                    "outcome.received",
                    {
                        "role": "github",
                        "status": "failed",
                        "failure_class": "fake_rejected",
                        "self_report": (
                            f"FAKE artifact refused on the real channel: {issue_id}"
                        ),
                    },
                    command_id=cmd.command_id,
                )
                return None
        backend.add_to_project(issue_id, backend.project)
        url = verified.get("url") or (
            f"https://github.com/{repo_id}/issues/{issue_id}"
            if repo_id and not str(issue_id).startswith(("FAKE-", "fake"))
            else ""
        )
        mapping = {
            "issue_number": issue_id,
            "node_id": verified.get("node_id"),
            "title": title,
            "api_verified": api_verified,
            "repo": repo_id,
            "url": url,
            "baseline_digest": digest,
            "source": "issue_create",
            "authoritative": api_verified,
        }
        if api_verified:
            # FR-0283-02: only API-verified creations enter the authoritative
            # map; a FAKE/unverified artifact is never persisted (the closer
            # audits any stale legacy disk entry as non-authoritative).
            persist_issue_mapping(self.repo, item_id, dict(mapping))
        self._emit(
            "issue.created",
            {
                "item_id": item_id,
                "issue_id": issue_id,
                "digest": digest,
                "repo": repo_id,
                "url": url,
                "baseline_digest": digest,
                "reused": bool(verified.get("reused")),
                "recovered": bool(verified.get("recovered")),
            },
            command_id=cmd.command_id,
        )
        self._emit(
            "issue.mapped",
            {
                **mapping,
                "item_id": item_id,
                "digest": digest,
                "reused": bool(verified.get("reused")),
                "recovered": bool(verified.get("recovered")),
            },
            command_id=cmd.command_id,
        )
        return issue_id

    def _do_create_issues(self, cmd, state, task_id, reconcile):
        """(F) M-REQ-APPROVAL ISSUES execution path (IF-ISSUE-001).

        Backend selection is fail-closed: missing credentials surface
        attention.required(area=issue_creation, reason=missing_token) and
        no silent Fake fallback is consumed. Each item goes through
        create_issue_verified + persist_issue_mapping (authoritative map,
        crash-idempotent dedup by item id); unverified real-channel
        artifacts are rejected fake_rejected + blocked.
        """
        digest = cmd.params["digest"]
        if reconcile and state.issues_created:
            return
        if self._stale_regenerate(cmd, digest):
            return
        if self._issue_backend is None:
            try:
                self._issue_backend = select_issue_backend(self.repo, self.version)
            except GithubIssuesError as exc:
                if exc.classification == "missing_token":
                    self._emit(
                        "attention.required",
                        {
                            "area": "issue_creation",
                            "reason": "missing_token",
                            "next": (
                                "export GITHUB_TOKEN (and TRAC_GITHUB_REPO) then "
                                "re-run; no silent Fake fallback is consumed"
                            ),
                        },
                        command_id=cmd.command_id,
                        task_id=task_id,
                    )
                    return
                raise
        backend = self._issue_backend
        # D-06 breakpoint resume: item_ids already logged are never rebuilt.
        done = {
            e.payload["item_id"]: e.payload["issue_id"]
            for e in self.store.events(self.run_id)
            if e.type == "issue.created"
        }
        try:
            for item_id, title, body in issue_items(self._vdir(), digest):
                if item_id in done:
                    continue
                issue_id = self._map_issue_item(
                    backend, item_id, title, body, digest, cmd, task_id
                )
                if issue_id is None:
                    return
                done[item_id] = issue_id
        except GithubIssuesError as e:
            # NFR-0030 style: no half-written summary; the reducer counts the
            # failed outcome as an attempt (3rd escalates to Human).
            self._emit(
                "outcome.received",
                {
                    "role": "github",
                    "status": "failed",
                    "failure_class": e.classification,
                    "self_report": str(e),
                },
                command_id=cmd.command_id,
            )
            return
        self._emit(
            "issues.created",
            {"digest": digest, "mapping": done, "project": backend.project},
            command_id=cmd.command_id,
        )
