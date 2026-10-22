"""M-VERIFY CI observation/readback chain extracted from the Executor (mixin
``ExecVerifyCiMixin``)."""

from __future__ import annotations

import os
import time
from pathlib import Path

from tracks.effects.github import GithubIssuesError, judge_ci_binding, readback_ci_run
from tracks.executor import m_verify
from tracks.executor.helpers import git
from tracks.kernel.events import Command

# The CI run is triggered by the candidate push itself; a host that names its
# remote differently can override the conventional origin without a config
# write (the runtime never mutates ``remote.*`` / ``core.hooksPath``).
CI_REMOTE_ENV = "TRAC_GIT_REMOTE"
_DEFAULT_REMOTE = "origin"
# Registration lag / queued / in-progress only. Bounded so a dead CI target
# parks on attention instead of holding the driver forever.
_CI_POLL_INTERVAL_SECONDS = 30.0
_CI_POLL_TIMEOUT_SECONDS = 30 * 60.0
_FAILED_CHECK_REASONS = frozenset({"failed_conclusion", "check_failed"})


def _ci_target(ci: dict) -> tuple[str, str]:
    repo_id = os.environ.get(str(ci.get("repo_env", "")), "")
    workflow = str(ci.get("workflow", ""))
    return repo_id, workflow


def _remote_branch_sha(listing: str, branch: str) -> str:
    """The remote SHA for ``refs/heads/<branch>`` from ``git ls-remote``."""
    wanted = f"refs/heads/{branch}"
    for line in listing.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1] == wanted:
            return parts[0]
    return ""


def _process_detail(proc) -> str:
    """Captured stdout/stderr of a failed git subprocess (trimmed)."""
    return ((proc.stdout or "") + (proc.stderr or "")).strip()


def _ci_run_pending(observed: dict) -> bool:
    """True for registration lag (no run yet) or a queued/in-progress run.

    A completed run that merely lacks ``head_sha`` keeps its ``missing``
    binding reason (``run_id`` is present); only a run-less response is a
    registration lag worth polling.
    """
    reason = str(observed.get("reason") or "")
    if reason == "run_incomplete":
        return True
    if observed.get("run_status") in {"queued", "in_progress", "waiting", "pending"}:
        return True
    return reason == "missing" and observed.get("run_id") is None


def _ci_failure_detail(observed: dict, required_checks) -> str:
    """Readable failed-check summary for attention.required(ci_failed)."""
    checks = observed.get("checks") or {}
    failed = [
        f"{name}=" + str(checks.get(name) if checks.get(name) is not None else "missing")
        for name in required_checks
    ]
    failed.extend(
        f"{name}={result}"
        for name, result in checks.items()
        if name not in required_checks and result != "success"
    )
    if not failed:
        failed.append(f"conclusion={observed.get('conclusion')}")
    return "failed checks: " + ", ".join(failed)


def _wait_for_ci_run(host, cmd, candidate_sha: str, repo_id: str, workflow: str):
    """Bounded poll for the pushed candidate's CI run registration.

    After the push GitHub needs time to create the run and for it to
    finish. A missing run (registration lag) or a queued/in-progress run
    is polled until the run completes; every poll emits an audit
    ``advisory.recorded`` so the driver is visibly alive (no invented
    event type). Exhausting the bound lands attention.required with
    ``ci_pending_timeout`` and never a synthetic pass.
    """
    started = time.monotonic()
    deadline = started + _CI_POLL_TIMEOUT_SECONDS
    attempt = 0
    while True:
        attempt += 1
        observed = _readback_ci_transport(host, cmd, candidate_sha, repo_id, workflow)
        if observed is None:
            return None
        last_reason = str(observed.get("reason") or "pending")
        if not _ci_run_pending(observed):
            return observed
        elapsed = time.monotonic() - started
        host._emit(
            "advisory.recorded",
            {
                "area": "ci_readback",
                "candidate_sha": candidate_sha,
                "status": "pending",
                "reason": last_reason,
                "run_id": observed.get("run_id"),
                "attempt": attempt,
                "elapsed_seconds": int(elapsed),
            },
            command_id=cmd.command_id,
        )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            host._emit(
                "attention.required",
                {
                    "area": "ci_readback",
                    "reason": "ci_pending_timeout",
                    "stage": "M-VERIFY",
                    "candidate_sha": candidate_sha,
                    "detail": (
                        f"{workflow} stayed {last_reason} for the pushed "
                        f"candidate after {int(elapsed)}s"
                    ),
                    "next": (
                        "inspect the GitHub Actions run for the pushed "
                        "candidate and fix registration/failure, then "
                        "trac run retries in place"
                    ),
                },
                command_id=cmd.command_id,
            )
            return None
        time.sleep(min(_CI_POLL_INTERVAL_SECONDS, remaining))


def _readback_ci_transport(host, cmd, candidate_sha: str, repo_id: str, workflow: str):
    """One transport read; transport/config failures keep attention faces."""
    try:
        return readback_ci_run(repo_id, workflow, candidate_sha)
    except GithubIssuesError as err:
        attention_reason = err.classification
        host._emit(
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
        host._emit(
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


def _attention_for_ci_failure(
    host, cmd, candidate_sha: str, observed: dict, ci: dict, reason: str
) -> None:
    """Route a completed red run to ci_failed; binding refusals keep their
    own closed reason (missing/mismatch/stale) on the attention face."""
    if reason in _FAILED_CHECK_REASONS:
        attention_reason = "ci_failed"
        detail = _ci_failure_detail(observed, ci.get("required_checks", []))
        next_step = "fix the CI failure and trac run retries"
    else:
        attention_reason = reason
        detail = reason
        next_step = "re-run CI on the frozen candidate; trac run retries"
    host._emit(
        "attention.required",
        {
            "area": "ci_readback",
            "reason": attention_reason,
            "stage": "M-VERIFY",
            "candidate_sha": candidate_sha,
            "run_id": observed.get("run_id"),
            "detail": detail,
            "next": next_step,
        },
        command_id=cmd.command_id,
    )


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
        repo_id, workflow = _ci_target(ci)
        if (
            repo_id
            and workflow
            and not self._push_ci_candidate(cmd, state, candidate_sha)
        ):
            return
        run_payload = self._readback_ci_binding(cmd, candidate_sha, ci)
        if run_payload is None:
            return
        self._emit("ci.run_observed", run_payload, command_id=cmd.command_id)
        self._issue_verify_final(candidate_sha)

    def _frozen_branch(self, state, candidate_sha: str) -> str:
        """The branch recorded by the candidate freeze, else State/git HEAD."""
        for event in reversed(list(self.store.events(self.run_id))):
            payload = event.payload or {}
            if (
                event.type == "candidate.frozen"
                and payload.get("candidate_sha") == candidate_sha
            ):
                branch = str(payload.get("branch") or "").strip()
                if branch and branch != "HEAD":
                    return branch
        branch = str(getattr(state, "branch", "") or "").strip()
        if branch and branch != "HEAD":
            return branch
        current = git(self.repo, "branch", "--show-current", check=False)
        return current.stdout.strip() if current.returncode == 0 else ""

    def _push_ci_candidate(self, cmd, state, candidate_sha: str) -> bool:
        """Push the frozen candidate so GitHub can register the CI run.

        Idempotent by remote readback: an origin branch already at the frozen
        SHA is a reconciled skip. Any push failure (missing remote/branch,
        auth, network) lands attention.required(push_failed) and the caller
        never reads back a run that cannot exist. No git config is written.
        """
        remote = os.environ.get(CI_REMOTE_ENV, "").strip() or _DEFAULT_REMOTE
        branch = self._frozen_branch(state, candidate_sha)
        if not branch:
            return self._ci_push_failed(
                cmd, candidate_sha, remote, "", "cannot resolve the frozen branch"
            )
        listed = git(self.repo, "ls-remote", "--heads", remote, branch, check=False)
        if listed.returncode != 0:
            return self._ci_push_failed(
                cmd,
                candidate_sha,
                remote,
                branch,
                _process_detail(listed) or "git ls-remote failed",
            )
        if _remote_branch_sha(listed.stdout, branch) == candidate_sha:
            return True
        pushed = git(
            self.repo,
            "push",
            remote,
            f"{candidate_sha}:refs/heads/{branch}",
            check=False,
        )
        if pushed.returncode != 0:
            return self._ci_push_failed(
                cmd,
                candidate_sha,
                remote,
                branch,
                _process_detail(pushed) or "git push failed",
            )
        return True

    def _ci_push_failed(
        self, cmd, candidate_sha: str, remote: str, branch: str, detail: str
    ) -> bool:
        self._emit(
            "attention.required",
            {
                "area": "ci_readback",
                "reason": "push_failed",
                "stage": "M-VERIFY",
                "candidate_sha": candidate_sha,
                "remote": remote,
                "branch": branch,
                "detail": detail,
                "next": (
                    "fix the git remote/push permissions, then trac run retries "
                    "in place"
                ),
            },
            command_id=cmd.command_id,
        )
        return False

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
        repo_id, workflow = _ci_target(ci)
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
        observed = _wait_for_ci_run(self, cmd, candidate_sha, repo_id, workflow)
        if observed is None:
            return None
        binding = judge_ci_binding(
            observed, candidate_sha, ci.get("required_checks", [])
        )
        if binding.get("status") != "passed":
            reason = str(binding.get("reason", "mismatch"))
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
                    "reason": reason,
                    "api_verified": False,
                },
                command_id=cmd.command_id,
            )
            _attention_for_ci_failure(
                self, cmd, candidate_sha, observed, ci, reason
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
