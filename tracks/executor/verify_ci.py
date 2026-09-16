"""M-VERIFY CI observation/readback chain extracted from the Executor (mixin
``ExecVerifyCiMixin``)."""

from __future__ import annotations

import os
from pathlib import Path

from tracks.effects.github import GithubIssuesError, judge_ci_binding, readback_ci_run
from tracks.executor import m_verify
from tracks.kernel.events import Command


class ExecVerifyCiMixin:
    """observe_ci_runs handler + candidate-bound CI transport readback + evidence
digests + advance/release preview."""

    def _do_observe_ci_runs(self, cmd, state, task_id, reconcile):
        """M-VERIFY CI readback (IF-VERIFY-004, AC-FR0270-01..03): API
        readback of the required-CI run bound to the frozen candidate;
        ``ci.run_observed`` carries the repo/workflow/run/head quadruple
        binding (api_verified). Missing credentials, CI config and network
        errors land attention.required (never a silent pass); a
        mismatch/missing/stale binding stops the chain blocked; a bound run
        hands to the Prism same-candidate final review (FR-0270: bound
        evidence is kept, a resumed run never repeats the API call)."""
        params = dict(cmd.params or {})
        candidate_sha = params.get("candidate_sha", "")
        contract, _digest, _source = self._load_or_default_contract(
            cmd, candidate_sha
        )
        if contract is None:
            return
        ci = contract.ci or {}
        if reconcile and self._ci_observation_reusable(cmd, candidate_sha, ci):
            return
        run_payload = self._readback_ci_binding(cmd, candidate_sha, ci)
        if run_payload is None:
            return
        self._emit("ci.run_observed", run_payload, command_id=cmd.command_id)
        self._issue_verify_final(candidate_sha)

    def _ci_observation_reusable(self, cmd, candidate_sha: str, ci: dict) -> bool:
        """A persisted ci.run_observed bound to the same candidate/command."""
        previous = self._latest_event("ci.run_observed")
        if previous is None:
            return False
        payload = previous.payload or {}
        configured_repo = os.environ.get(str(ci.get("repo_env", "")), "").strip()
        configured_workflow = str(ci.get("workflow", "")).strip()
        observed_workflow = str(payload.get("workflow", "")).strip()
        observed_path = str(payload.get("workflow_path", "")).strip()
        observed_id = str(payload.get("workflow_id", "")).strip()
        workflow_match = (
            observed_workflow == configured_workflow
            or observed_id == configured_workflow
            or observed_path == configured_workflow
            or (
                bool(configured_workflow)
                and Path(observed_path).name == Path(configured_workflow).name
            )
        )
        command_match = previous.command_id == cmd.command_id
        checks = payload.get("checks") or {}
        required_checks = list(ci.get("required_checks", []))
        return (
            command_match
            and payload.get("candidate_sha") == candidate_sha
            and payload.get("head_sha") == candidate_sha
            and payload.get("status") == "passed"
            and payload.get("api_verified") is True
            and not payload.get("stale")
            and bool(configured_repo)
            and payload.get("repo") == configured_repo
            and isinstance(payload.get("run_id"), int)
            and not isinstance(payload.get("run_id"), bool)
            and workflow_match
            and payload.get("required_checks", required_checks) == required_checks
            and all(checks.get(name) == "success" for name in required_checks)
        )

    def _issue_verify_final(self, candidate_sha: str) -> None:
        assignment = m_verify.build_prism_final_review_assignment(
            candidate_sha, self._verify_evidence_digests()
        )
        self.issue(
            Command(
                kind="dispatch_agent",
                params={
                    "role": "prism",
                    "substate": "VERIFY_FINAL",
                    "stage": "M-VERIFY",
                    "assignment": dict(assignment),
                    "objective": (
                        "same-candidate final review of the frozen candidate "
                        "(IF-VERIFY-005); your FINAL message must be exactly "
                        "one fenced ```tracks-envelope block carrying the "
                        "verdict payload per the assignment schema (prose "
                        "analysis may precede it, nothing may follow it)"
                    ),
                    **assignment,
                },
            )
        )

    def _readback_ci_binding(self, cmd, candidate_sha, ci):
        """API readback + binding judge (IF-VERIFY-004, AC-FR0270-01..03).
        Returns the bound ci.run_observed payload, or None with the block
        already on the stream: missing CI config/credentials and network
        errors land attention.required (never a silent pass); a
        mismatch/missing/stale binding stops the chain blocked.

        The readback is the real GitHub-API face (stand-in base honored via
        TRAC_GITHUB_API_BASE) for every agent channel: the agent backend
        selection scopes who writes, never what the Runtime observes.
        A fake agent therefore cannot synthesize api_verified evidence.
        """
        repo_id = os.environ.get(str(ci.get("repo_env", "")), "")
        workflow = str(ci.get("workflow", ""))
        if not repo_id or not workflow:
            # AC-FR0270-03 locked vocabulary (missing_token|network_error):
            # an undeclared/unresolved CI target is the credentials face,
            # never a guessed configuration class.
            self._emit(
                "attention.required",
                {
                    "area": "ci_readback",
                    "reason": "missing_token",
                    "stage": "M-VERIFY",
                    "candidate_sha": candidate_sha,
                    "detail": "[host-contract.ci] repo_env/workflow unresolved",
                    "next": (
                        "set the CI target/token; trac run retries in place"
                    ),
                },
                command_id=cmd.command_id,
            )
            return None
        try:
            observed = readback_ci_run(repo_id, workflow, candidate_sha)
        except GithubIssuesError as err:
            attention_reason = err.classification
            self._emit(
                "attention.required",
                {
                    "area": "ci_readback",
                    "reason": attention_reason,
                    "stage": "M-VERIFY",
                    "candidate_sha": candidate_sha,
                    "detail": str(err),
                    "next": (
                        "set the CI token; trac run retries in place"
                        if attention_reason == "missing_token"
                        else "resolve the CI API error; trac run retries in place"
                    ),
                },
                command_id=cmd.command_id,
            )
            return None
        except (OSError, ValueError) as err:
            self._emit(
                "attention.required",
                {
                    "area": "ci_readback",
                    "reason": "network_error",
                    "stage": "M-VERIFY",
                    "candidate_sha": candidate_sha,
                    "detail": str(err),
                    "next": "retry when the CI API is reachable",
                },
                command_id=cmd.command_id,
            )
            return None
        binding = judge_ci_binding(
            observed, candidate_sha, ci.get("required_checks", [])
        )
        if binding.get("status") != "passed":
            # AC-FR0270-02 fail-closed event face: a binding failure lands
            # ci.run_observed(status=failed) with its closed reason
            # (mismatch|missing|stale|failed_conclusion|check_failed) so the
            # evidence stream carries WHAT failed and status can render
            # ci=mismatch|missing|stale — the readback happened, only the
            # binding refused. No credentials/no target never reaches here
            # (attention.required above, no API call, no event).
            self._emit(
                "ci.run_observed",
                {
                    "candidate_sha": candidate_sha,
                    "repo": repo_id,
                    "workflow": workflow,
                    "workflow_name": observed.get("workflow_name"),
                    "workflow_path": observed.get("workflow_path"),
                    "workflow_id": observed.get("workflow_id"),
                    "run_id": observed.get("run_id"),
                    "head_sha": observed.get("head_sha"),
                    "conclusion": observed.get("conclusion"),
                    "checks": observed.get("checks") or {},
                    "required_checks": list(ci.get("required_checks", [])),
                    "status": "failed",
                    "reason": str(binding.get("reason", "mismatch")),
                    "api_verified": False,
                },
                command_id=cmd.command_id,
            )
            self._emit(
                "attention.required",
                {
                    "area": "ci_readback",
                    "reason": "ci_binding_blocked",
                    "stage": "M-VERIFY",
                    "candidate_sha": candidate_sha,
                    "detail": binding.get("reason", "mismatch"),
                    "next": "re-run CI on the frozen candidate; trac run retries",
                },
                command_id=cmd.command_id,
            )
            return None
        return {
            "candidate_sha": candidate_sha,
            "repo": repo_id,
            "workflow": workflow,
            "workflow_name": observed.get("workflow_name"),
            "workflow_path": observed.get("workflow_path"),
            "workflow_id": observed.get("workflow_id"),
            "run_id": observed.get("run_id"),
            "head_sha": observed.get("head_sha"),
            "conclusion": observed.get("conclusion"),
            "checks": observed.get("checks") or {},
            "required_checks": list(ci.get("required_checks", [])),
            "status": "passed",
            "reason": "bound",
            "api_verified": binding.get("api_verified") is True,
        }

    def _verify_evidence_digests(self) -> dict:
        """Evidence manifest for the final-review envelope (IF-VERIFY-005):
        the contract digest of every passed local gate, keyed by gate kind."""
        digests = {}
        for event in self.store.events(self.run_id):
            if event.type == "local_gate.passed":
                gate_payload = event.payload or {}
                digests[str(gate_payload.get("kind"))] = gate_payload.get(
                    "contract_digest"
                )
        return digests

    def _advance_verify_chain(self, cmd, candidate_sha):
        """All M-VERIFY gates green (§1.1): exit M-VERIFY, enter M-SECURITY
        and run the security assessment; a passing assessment exits to
        M-RELEASE where the kernel release routing takes over (preview ->
        release decision -> publish). A failed assessment stops blocked with
        its repair_route on the stream (§1.0.14 classification; never an
        automatic rollback)."""
        self._emit("stage.exited", {"stage": "M-VERIFY"}, command_id=cmd.command_id)
        self._emit(
            "stage.entered", {"stage": "M-SECURITY"}, command_id=cmd.command_id
        )
        self.issue(
            Command(kind="assess_security", params={"candidate_sha": candidate_sha})
        )
        self._advance_after_security(cmd, candidate_sha)

    def _resolve_ref_payload(self, payload) -> dict:
        r"""Dereference a blob-$ref'd event payload (large payloads are
        transparently blobbed by the store; readers needing fields must
        resolve). Returns the raw payload when it carries no ref."""
        if isinstance(payload, dict) and set(payload) == {"$ref"}:
            return self.store.load_payload(dict(payload))
        return payload if isinstance(payload, dict) else {}

    def _advance_after_security(self, cmd, candidate_sha):
        """Post-assessment advance (shared by the CI chain link and the
        re-driven assess_security command): a PASSING aggregate for the
        CURRENT candidate exits M-SECURITY into M-RELEASE and generates the
        release preview; anything else parks for the repair disposition."""
        assessed = self._latest_event("security.assessed")
        if assessed is None:
            return
        payload = self._resolve_ref_payload(assessed.payload)
        if payload.get("status") not in ("pass", "passed"):
            return
        if str(payload.get("candidate_sha") or "") != str(candidate_sha):
            return  # stale assessment for an older candidate: re-assess owns it
        # Idempotent boundary check scoped to the CURRENT M-SECURITY entry:
        # a previous journey round (rolled back and re-walked) legitimately
        # crossed this boundary before; only an exit AFTER the latest entry
        # suppresses a duplicate advance.
        events = list(self.store.events(self.run_id))
        last_entry = max(
            (
                ev.seq
                for ev in events
                if ev.type == "stage.entered"
                and (ev.payload or {}).get("stage") == "M-SECURITY"
            ),
            default=-1,
        )
        if any(
            ev.type == "stage.exited"
            and (ev.payload or {}).get("stage") == "M-SECURITY"
            and ev.seq > last_entry
            for ev in events
        ):
            return  # already crossed in this round
        self._emit(
            "stage.exited", {"stage": "M-SECURITY"}, command_id=cmd.command_id
        )
        self._emit(
            "stage.entered", {"stage": "M-RELEASE"}, command_id=cmd.command_id
        )
        self._release_preview(cmd, candidate_sha)

    def _release_preview(self, cmd, candidate_sha: str) -> None:
        """M-RELEASE entry preview (SM-01.6, FR-0284/FR-0286): the fully
        green chain aggregates the verified evidence into the
        content-addressed release preview and lands release.previewed
        (known issues listed, awaiting the Human release decision). A
        contract reload refusal fails closed (attention.required, never a
        guessed preview digest); the materialized default contract feeds
        its digest when the host declared none."""
        contract, digest, _source = self._load_or_default_contract(
            cmd, candidate_sha
        )
        if contract is None:
            return
        self._park_preview(cmd, candidate_sha, contract, digest)
