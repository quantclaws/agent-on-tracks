"""M-PUBLISH command handlers extracted from the Executor (mixin
``ExecPublishMixin``)."""

from __future__ import annotations

from tracks.effects import publish as publish_effects
from tracks.executor.publish_runtime import execute_publish_operations, resolve_publish_authority
from tracks.executor.release_gate import version_facts
from tracks.executor.security import aggregate_security_status, run_security_scans


class ExecPublishMixin:
    """execute_publish/register_known_issue/assess_security handlers plus the
blocked-publish payload."""

    def _publish_fail(
        self, cmd, reason: str, *, candidate_sha: str = "", key: str = "", remote_check=None
    ):
        payload = {
            "reason": reason,
            "candidate_sha": candidate_sha,
            "idempotency_key": key,
        }
        if remote_check is not None:
            payload["remote_check"] = remote_check
        self._emit("publish.failed", payload, command_id=cmd.command_id)

    def _blocked_publish_payload(self, params: dict, events: list) -> dict:
        """publish.blocked payload; preview/candidate only when resolvable."""
        payload = {"reason": "agent_forbidden"}
        preview_digest = params.get("preview_digest")
        if not isinstance(preview_digest, str) or not preview_digest:
            previews = [event for event in events if event.type == "release.previewed"]
            preview_digest = (
                (previews[-1].payload or {}).get("preview_digest")
                if previews
                else None
            )
        if isinstance(preview_digest, str) and preview_digest:
            payload["preview_digest"] = preview_digest
        frozen = [event for event in events if event.type == "candidate.frozen"]
        candidate_sha = (
            str((frozen[-1].payload or {}).get("candidate_sha") or "")
            if frozen
            else ""
        )
        if candidate_sha:
            payload["candidate_sha"] = candidate_sha
        return payload

    def _do_execute_publish(self, cmd, state, task_id, reconcile):
        """(G) Consume Command(execute_publish) (bound to a preview digest):
        the agent gate runs first (publish.blocked reason=agent_forbidden,
        zero side effects), then the ordered batch — publish.planned ->
        publish.executed(done|reconciled_skip) per operation / publish.failed.
        All operations are validated before any effect; the batch stops on
        the first failed/conflicting effect and never emits an aggregate
        success over a failure."""
        params = dict(cmd.params or {})
        events = list(self.store.events(self.run_id))
        if self._assert_agent_forbidden():
            self._emit(
                "publish.blocked",
                self._blocked_publish_payload(params, events),
                command_id=cmd.command_id,
            )
            return
        facts = self._release_version_facts(state)
        if not facts.get("version"):
            envelope_version = next(
                (event.version for event in reversed(events) if event.version), ""
            )
            facts = version_facts(envelope_version, self.run_id)
        authority, error = resolve_publish_authority(
            self.repo,
            events,
            params.get("preview_digest"),
            facts,
            self.store.home,
        )
        if authority is None:
            if error == "ls_remote_failed":
                self._emit(
                    "attention.required",
                    {
                        "area": "release_facts",
                        "reason": error,
                        "stage": "M-PUBLISH",
                        "detail": "remote patch-tag census failed for the publish rebuild",
                        "next": "check origin access; trac run --resume retries publish",
                    },
                    command_id=cmd.command_id,
                )
            self._publish_fail(cmd, error or "malformed")
            return

        def publish_fail(reason, remote_check, key):
            self._publish_fail(
                cmd,
                reason,
                candidate_sha=authority["candidate_sha"],
                key=key,
                remote_check=remote_check,
            )

        effects = {
            "read_remote": publish_effects.read_remote_state,
            "push_tag": publish_effects.push_tag,
            "push_merge": publish_effects.push_merge,
            "create_release": publish_effects.create_release,
            "upload_artifact": publish_effects.upload_artifact,
        }
        execute_publish_operations(
            authority,
            cmd.command_id,
            lambda: list(self.store.events(self.run_id)),
            effects,
            self._emit,
            publish_fail,
            None,
        )

    def _do_register_known_issue(self, cmd, state, task_id, reconcile):
        """(I) Consume Command(register_known_issue): known_issue.registered
        on acceptance, known_issue.rejected on rejection."""
        params = dict(cmd.params or {})
        rejected = params.get("rejected") is True or params.get("decision") == "reject"
        self._emit(
            "known_issue.rejected" if rejected else "known_issue.registered",
            {
                "title": params.get("title", ""),
                "reason": params.get("reason", ""),
                "issue": params.get("issue"),
            },
            command_id=cmd.command_id,
        )

    def _do_assess_security(self, cmd, state, task_id, reconcile):
        """(J) Consume Command(assess_security) (IF-SECURITY-001,
        AC-FR0272-01/02): run the contract-declared scans, aggregate
        fail-closed (passed iff EVERY declared scan passed; malformed/missing
        -> unknown) and emit security.assessed bound to the frozen candidate
        with the policy digest. A non-passing aggregate stops the chain
        blocked -- the repair route (cve -> Archer advisory) owns the
        disposition (§1.0.4/§1.0.14, never a silent pass)."""
        params = dict(cmd.params or {})
        candidate_sha = params.get("candidate_sha") or ""
        if not candidate_sha:
            candidate_sha = str(getattr(state, "candidate_sha", "") or "")
        if not candidate_sha:
            frozen = self._latest_event("candidate.frozen")
            candidate_sha = (
                (frozen.payload or {}).get("candidate_sha", "") if frozen else ""
            )
        contract, digest, _source = self._load_or_default_contract(
            cmd, candidate_sha
        )
        if contract is None:
            return
        results = run_security_scans(contract, self.repo, candidate_sha)
        status = aggregate_security_status(results)
        payload = {
            "status": status,
            "candidate_sha": candidate_sha,
            "policy_digest": digest,
            "scans": [
                {
                    "id": r.gate_id,
                    "status": r.status,
                    "exit_code": r.exit_code,
                }
                for r in results
            ],
            "repair_route": "none",
        }
        if status != "passed":
            # §1.0.14 B: a failing/unknown scan is a cve-class finding ->
            # Archer advisory repair route (in-place, never a rollback).
            payload["repair_route"] = {
                "exit_class": "defect_repair",
                "defect_class": "cve",
                "owner": "Archer",
                "discipline": "cve_advisory",
                "budget_remaining": None,
            }
        self._emit(
            "security.assessed",
            payload,
            command_id=cmd.command_id,
        )
