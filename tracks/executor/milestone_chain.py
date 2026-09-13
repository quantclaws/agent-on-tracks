"""M-MILESTONE closing tail extracted from the Executor (mixin
``ExecMilestoneMixin``)."""

from __future__ import annotations

import os

from tracks.effects.github import GithubIssuesError, close_issue
from tracks.effects.github import close_project_milestone as close_project_milestone_api
from tracks.executor.host_contract import CANONICAL_CONTRACT_RELPATH, load_host_contract
from tracks.executor.milestone import (
    _event_payload,
    _event_seq,
    _event_type,
    build_release_trace,
    clean_temp_refs,
    close_issues_with_comment,
    close_project_milestone,
    release_trace_comment,
    seal_evidence_readonly,
    skipped_issue_entries,
)
from tracks.executor.publish import operation_idempotency_key
from tracks.executor.release_gate import render_placeholders, version_facts

_PUBLISH_FAILURE_EVENTS = frozenset(
    {"publish.failed", "publish.blocked", "reconcile_conflict"}
)


def _planned_steps(preview_payload: dict) -> list[str]:
    """The preview's resolved plan steps (empty for legacy/absent plans)."""
    plan = preview_payload.get("operation_plan")
    steps = plan.get("steps") if isinstance(plan, dict) else None
    if not isinstance(steps, list):
        return []
    return [step for step in steps if isinstance(step, str) and ":" in step]


def _latest_operation_status(events, after_seq: int) -> dict[str, bool]:
    """Per-key terminal truth after the preview: a later failure wins."""
    latest: dict[str, bool] = {}
    for event in events:
        if _event_seq(event) <= after_seq:
            continue
        etype = _event_type(event)
        if etype not in {"publish.executed"} | _PUBLISH_FAILURE_EVENTS:
            continue
        payload = _event_payload(event)
        key = str(payload.get("idempotency_key") or "")
        if not key:
            continue
        if etype == "publish.executed":
            latest[key] = str(payload.get("status") or "") in (
                "done",
                "reconciled_skip",
            )
        else:
            latest[key] = False
    return latest


def _publish_failure_reason(events, after_seq: int) -> str:
    """Latest publish failure reason after the preview ("" when absent)."""
    for event in reversed(events):
        if _event_seq(event) <= after_seq:
            break
        if _event_type(event) in _PUBLISH_FAILURE_EVENTS:
            return str(_event_payload(event).get("reason") or "")
    return ""


class ExecMilestoneMixin:
    """close_milestone trace/issue/project/seal/clean/complete handlers."""

    def _do_close_milestone(self, cmd, state, task_id, reconcile):
        """Consume Command(close_milestone) (IF-MILESTONE-001, §1i): itemized,
        idempotent closing tail.

        Sub-steps: trace_closed -> issue/project closed -> sealed ->
        refs.cleaned -> run.completed. Each sub-step uses its OWN event as the
        completion witness (from this command or any prior one), so an
        interrupted tail is continued by the next close_milestone command
        without re-emitting a completed step, and already-persisted external
        effects are never repeated. The M-MILESTONE decider re-issues this
        command until run.completed flips the run to completed.
        """
        events = list(self.store.events(self.run_id))
        params = dict(cmd.params or {})
        candidate_sha = self._milestone_candidate(events, params)
        if self._block_incomplete_publish(cmd, task_id, events, candidate_sha):
            return
        trace = self._milestone_trace(cmd, task_id, events, candidate_sha)
        if not trace.get("trace_digest"):
            return
        closed_numbers = [
            (event.payload or {}).get("issue_number")
            for event in events
            if event.type == "issue.closed"
        ]
        issue_map = self._load_authoritative_issue_map()
        known_issues = [
            event.payload or {} for event in events if event.type == "known_issue.registered"
        ]
        self._close_milestone_issues(
            cmd, task_id, trace, issue_map, known_issues, closed_numbers
        )
        project = self._close_milestone_project(cmd, task_id, trace, params, events)
        self._emit_milestone_closed(cmd, task_id, trace, project, events)
        if not self._seal_milestone(cmd, task_id, trace, events, candidate_sha):
            return
        if not self._clean_milestone_refs(cmd, task_id, events):
            return
        self._complete_milestone(cmd, task_id, trace, events)

    def _milestone_candidate(self, events, params) -> str:
        """The command-declared candidate, else the latest frozen identity."""
        candidate_sha = str(params.get("candidate_sha") or "")
        if candidate_sha:
            return candidate_sha
        for event in reversed(events):
            if event.type == "candidate.frozen":
                return str((event.payload or {}).get("candidate_sha") or "")
        return ""

    def _block_incomplete_publish(self, cmd, task_id, events, candidate_sha) -> bool:
        """SM-01.15 fail-closed tail: a publish batch that is not fully
        terminal must never trace-close/seal/complete as released. Returns
        True when the attention was emitted and the tail must stop."""
        incomplete = self._publish_batch_incomplete(events, candidate_sha)
        if incomplete is None:
            return False
        self._emit(
            "attention.required",
            {
                "area": "publish",
                "reason": incomplete["reason"],
                "candidate_sha": candidate_sha,
                "preview_digest": incomplete["preview_digest"],
                "missing_operations": incomplete["missing"],
                "next": (
                    "repair the remote/conflict and trac run; the "
                    "unfinished publish operations resume"
                ),
            },
            command_id=cmd.command_id,
            task_id=task_id,
        )
        return True

    def _milestone_trace(self, cmd, task_id, events, candidate_sha):
        """The trace close sub-step: reuse any landed milestone.trace_closed,
        else build the candidate-bound §1i trace and land it."""
        for event in reversed(events):
            if event.type == "milestone.trace_closed":
                return dict(event.payload or {})
        trace = build_release_trace(
            [
                {"type": event.type, "payload": dict(event.payload or {}), "seq": event.seq}
                for event in events
            ],
            candidate_sha,
        )
        self._emit(
            "milestone.trace_closed",
            dict(trace),
            command_id=cmd.command_id,
            task_id=task_id,
        )
        return trace

    def _publish_batch_incomplete(self, events, candidate_sha: str) -> dict | None:
        """SM-01.15 fail-closed judgment over the durable publish evidence.

        Returns ``None`` when every operation of the candidate's latest
        approved plan is terminal (done/reconciled_skip), or a reason dict
        when at least one operation is missing/failed -- including a
        failure that landed after an earlier success. Legacy streams without
        a release preview (unit/legacy milestone paths) carry no judgment.
        """
        previews = [
            event for event in events if _event_type(event) == "release.previewed"
        ]
        if not previews:
            return None
        preview = next(
            (
                event
                for event in reversed(previews)
                if _event_payload(event).get("candidate_sha") == candidate_sha
            ),
            None,
        )
        if preview is None:
            return {
                "reason": "missing_preview",
                "preview_digest": "",
                "missing": [],
            }
        payload = _event_payload(preview)
        steps = _planned_steps(payload)
        if not steps:
            return None  # legacy preview without a resolved operation plan
        preview_digest = str(payload.get("preview_digest") or "")
        latest = _latest_operation_status(events, _event_seq(preview))
        missing: list[str] = []
        for step in steps:
            kind, _, target = step.partition(":")
            key = operation_idempotency_key(preview_digest, kind, target)
            if latest.get(key) is not True:
                missing.append(step)
        if not missing:
            return None
        reason = _publish_failure_reason(events, _event_seq(preview)) or "publish_incomplete"
        return {
            "reason": reason,
            "preview_digest": preview_digest,
            "missing": missing,
        }

    def _close_milestone_issues(
        self, cmd, task_id, trace, issue_map, known_issues, closed_numbers
    ):
        """Close authoritative issues for real; audit every non-authoritative
        identity as skipped. `issue.closed` events are the per-issue witness:
        a number that already has an event (closed or skipped) is settled and
        never re-processed."""
        done = {str(number) for number in closed_numbers if number is not None}
        pending_map = {
            item_id: mapping
            for item_id, mapping in (issue_map or {}).items()
            if isinstance(mapping, dict)
            and str(mapping.get("issue_number")) not in done
        }
        for entry in close_issues_with_comment(
            self.repo, pending_map, trace, closer=self._real_issue_closer(cmd, task_id, trace)
        ):
            if str(entry.get("issue_number")) in done:
                continue
            done.add(str(entry.get("issue_number")))
            self._emit("issue.closed", dict(entry), command_id=cmd.command_id, task_id=task_id)
        for entry in skipped_issue_entries(issue_map, known_issues, trace, done):
            done.add(str(entry.get("issue_number")))
            self._emit("issue.closed", dict(entry), command_id=cmd.command_id, task_id=task_id)

    def _real_issue_closer(self, cmd, task_id, trace):
        """The irreversible issue close seam: comment + close + API readback.

        A verified remote close yields state=closed; any classified failure
        yields an audited state=skipped (reason) plus attention.required and
        NEVER claims a close (FR-0284-01)."""

        def closer(mapping, entry):
            repo_id = str(mapping.get("repo") or os.environ.get("TRAC_GITHUB_REPO", ""))
            number = mapping.get("issue_number")
            comment = release_trace_comment(trace)
            try:
                result = close_issue(repo_id, int(number), comment)
            except GithubIssuesError as exc:
                result = {
                    "state": "",
                    "api_verified": False,
                    "error": f"{exc.classification}: {exc}",
                }
            except (TypeError, ValueError) as exc:
                result = {
                    "state": "",
                    "api_verified": False,
                    "error": f"malformed_issue_number: {exc}",
                }
            if result.get("api_verified") and result.get("state") == "closed":
                return {
                    "state": "closed",
                    "comment": comment,
                    "comment_ref": (
                        f"comment:{number}:"
                        f"{result.get('comment_id') or trace.get('trace_digest', '')}"
                    ),
                    "remote_state": result.get("state"),
                    "api_verified": True,
                }
            reason = str(result.get("error") or "close_unconfirmed")
            self._emit(
                "attention.required",
                {
                    "area": "issue_close",
                    "reason": reason,
                    "issue_number": number,
                    "next": "repair GitHub access and trac run --resume",
                },
                command_id=cmd.command_id,
                task_id=task_id,
            )
            return {
                "state": "skipped",
                "reason": reason,
                "api_verified": False,
            }

        return closer

    def _close_milestone_tracker(self, state) -> dict:
        """Resolve ``[host-contract.tracker]`` into close-command params.

        The kernel deciders stay filesystem-free, so the executor resolves
        the host declaration at issue time (FR-0284): the repo/project
        identities from their declared env channels and the milestone
        template rendered with the run's version facts. An undeclared or
        unresolved tracker returns ``{}`` and the consumer keeps the audited
        ``not_authoritative`` skip semantics.
        """
        try:
            contract = load_host_contract(
                self.repo.joinpath(*CANONICAL_CONTRACT_RELPATH)
            )
        except (OSError, ValueError):
            return {}
        tracker = dict(contract.tracker or {})
        if not tracker:
            return {}
        version = str(getattr(state, "version", "") or "")
        if not version:
            version = next(
                (
                    str(event.version)
                    for event in reversed(list(self.store.events(self.run_id)))
                    if getattr(event, "version", "")
                ),
                "",
            )
        template = str(tracker.get("milestone_template") or "")
        return {
            "repo": os.environ.get(str(tracker.get("repo_env") or ""), "").strip(),
            "project": os.environ.get(
                str(tracker.get("project_env") or ""), ""
            ).strip(),
            "milestone": (
                render_placeholders(template, version_facts(version, self.run_id))
                if template
                else ""
            ),
        }

    def _close_milestone_project(self, cmd, task_id, trace, params, events):
        """Close the Project/milestone when the host declared an authoritative
        tracker + credentials; otherwise land an audited skipped identity."""
        if any(event.type == "project.closed" for event in events):
            return next(
                dict(event.payload or {})
                for event in reversed(events)
                if event.type == "project.closed"
            )
        tracker = params.get("tracker") if isinstance(params.get("tracker"), dict) else {}
        repo_id = str(tracker.get("repo") or os.environ.get("TRAC_GITHUB_REPO", ""))
        milestone = tracker.get("milestone")
        if repo_id and milestone not in (None, "") and os.environ.get("GITHUB_TOKEN"):
            def closer(_tracker, entry):
                try:
                    result = close_project_milestone_api(
                        repo_id, tracker.get("project", ""), milestone
                    )
                except GithubIssuesError as exc:
                    result = {
                        "state": "",
                        "api_verified": False,
                        "error": f"{exc.classification}: {exc}",
                    }
                if result.get("api_verified") and result.get("state") == "closed":
                    return {"state": "closed", "remote_state": "closed", "api_verified": True}
                reason = str(result.get("error") or "close_unconfirmed")
                self._emit(
                    "attention.required",
                    {
                        "area": "project_close",
                        "reason": reason,
                        "next": "repair GitHub access and trac run --resume",
                    },
                    command_id=cmd.command_id,
                    task_id=task_id,
                )
                return {"state": "skipped", "reason": reason, "api_verified": False}

            entry = close_project_milestone(self.repo, tracker, trace, closer=closer)
        else:
            entry = close_project_milestone(self.repo, tracker, trace)
            entry.update({"state": "skipped", "reason": "not_authoritative"})
        self._emit("project.closed", dict(entry), command_id=cmd.command_id, task_id=task_id)
        return entry

    def _emit_milestone_closed(self, cmd, task_id, trace, project, events):
        if any(event.type == "milestone.closed" for event in events):
            return
        self._emit(
            "milestone.closed",
            {
                "milestone": (project or {}).get("milestone", ""),
                "state": (project or {}).get("state", "closed"),
                "trace_digest": trace.get("trace_digest", ""),
            },
            command_id=cmd.command_id,
            task_id=task_id,
        )

    def _seal_milestone(self, cmd, task_id, trace, events, candidate_sha) -> bool:
        if any(
            event.type == "milestone.sealed"
            and (event.payload or {}).get("readonly") is True
            for event in events
        ):
            return True
        sealed = seal_evidence_readonly(
            self.repo, candidate_sha, trace=trace, events=events
        )
        if sealed.get("readonly") is not True:
            # Emit the attention once; a repeated failure must leave the loop
            # with no progress event so the stall breaker (B86/B88) stops a
            # permanently unwritable blob store instead of spinning forever.
            if not any(
                event.type == "attention.required"
                and (event.payload or {}).get("area") == "milestone_seal"
                for event in events
            ):
                self._emit(
                    "attention.required",
                    {
                        "area": "milestone_seal",
                        "reason": str(sealed.get("error") or "seal_failed"),
                        "seal_blob": str(sealed.get("seal_blob") or ""),
                        "next": "fix the runtime blob store and trac run",
                    },
                    command_id=cmd.command_id,
                    task_id=task_id,
                )
            return False
        self._emit(
            "milestone.sealed",
            dict(sealed),
            command_id=cmd.command_id,
            task_id=task_id,
        )
        return True

    def _clean_milestone_refs(self, cmd, task_id, events) -> bool:
        if any(
            event.type == "refs.cleaned"
            and (event.payload or {}).get("remaining") == 0
            for event in events
        ):
            return True
        cleaned = clean_temp_refs(self.repo, self.run_id)
        prior_remaining = next(
            (
                (event.payload or {}).get("remaining_refs")
                for event in reversed(events)
                if event.type == "refs.cleaned"
            ),
            None,
        )
        if cleaned.get("remaining") != 0 and prior_remaining == cleaned.get("remaining_refs"):
            # Same stuck refs as the previous attempt: emit nothing so the
            # stall breaker stops the retry loop instead of appending
            # duplicate audit events forever.
            return False
        self._emit("refs.cleaned", dict(cleaned), command_id=cmd.command_id, task_id=task_id)
        if cleaned.get("remaining") != 0:
            self._emit(
                "attention.required",
                {
                    "area": "milestone_refs",
                    "reason": "refs_remaining",
                    "remaining_refs": cleaned.get("remaining_refs", []),
                    "next": "inspect refs/trac/tmp owners and trac run",
                },
                command_id=cmd.command_id,
                task_id=task_id,
            )
            return False
        return True

    def _complete_milestone(self, cmd, task_id, trace, events):
        if any(event.type == "run.completed" for event in events):
            return
        # §1.0.7 terminal face: the closing tail completes with
        # run.completed(terminal_state=released, release_tag).
        self._emit(
            "run.completed",
            {
                "terminal_state": "released",
                "release_tag": trace.get("release_tag", ""),
                "trace_digest": trace.get("trace_digest", ""),
            },
            command_id=cmd.command_id,
            task_id=task_id,
        )
