"""M-VERIFY full-F / local-gate / host-contract chain extracted from the
Executor (mixin ``ExecVerifyGatesMixin``)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import tomllib

from tracks import paths
from tracks.executor import m_verify
from tracks.executor.helpers import git
from tracks.executor.host_contract import (
    CANONICAL_CONTRACT_RELPATH,
    LocalGateDecl,
    NormalizedGateResult,
    execute_gate,
    load_host_contract,
    resolve_artifact_identity,
    validate_host_contract,
)
from tracks.executor.local_gate_evidence import (
    auxiliary_gate_identities,
    auxiliary_phases_passed,
    gate_identity,
    has_complete_passed_gates,
    normalized_result_payload,
)
from tracks.executor.registry_gate import execute_registry_gate
from tracks.executor.release_gate import complete_version_facts
from tracks.executor.test_select import (
    TestResultError,
    TestSelectError,
    make_selection_id,
    rebuild_ledger,
)
from tracks.kernel.events import Command
from tracks.project import ContractError, load_contract

# Runtime-materialized host contract (IF-HOSTCONTRACT-001): when the host
# declares no ``[host-contract]`` table of its own, the M-VERIFY chain
# materializes this baseline so the release pipeline can proceed — the
# runtime executes only what it materialized (never guessed host language
# semantics). An undeclared host MUST NOT pass quality verification
# silently, but its quality face cannot be guessed either: the
# materialized default gate is a declared no-op (``true``) and the
# fail-closed stop moves to the CI face, where the host's credentials
# stand-in (TRAC_GITHUB_REPO / TRAC_GITHUB_API_BASE / GITHUB_TOKEN) decides
# the A-class outcome (missing credentials -> attention.required
# reason=missing_token, never a silent pass) — every downstream producer
# stays reachable from the fixture environment while an unseen
# quality/toolchain claim still fails at its natural gate. Persisted
# under .tracks/runtime (untracked, never dirties the frozen tree) with
# the host_contract.materialized event recording its digest and source.
_DEFAULT_HOST_CONTRACT_TOML = """\
[host-contract]
version = 1
language = "runtime-default"
toolchain = "runtime-default"
install = "true"

[[host-contract.local_gate]]
kind = "quality"
command = "true"
result_channel = "exit_code"
timeout_seconds = 60

[[host-contract.security_scan]]
id = "runtime-default-scan"
tool = "runtime-default"
install = "true"
command = "true"
result_channel = "exit_code"
threshold = "violations=0"
timeout_seconds = 60

[host-contract.ci]
repo_env = "TRAC_GITHUB_REPO"
workflow = "ci.yml"
required_checks = []
conclusion = "success"

[host-contract.tracker]
repo_env = "TRAC_GITHUB_REPO"
project_env = "TRAC_GITHUB_PROJECT"

[host-contract.operations.feature]
steps = ["merge:main"]

[host-contract.operations.post_release]
steps = ["merge:main"]

[host-contract.operations.dev]
steps = ["merge:main"]
"""


class ExecVerifyGatesMixin:
    """freeze candidate, full-F judgment, host contract loading and local gates;
the runtime-materialized default contract lives here."""

    def _do_freeze_candidate(self, cmd, state, task_id, reconcile):
        """M-VERIFY chain head (IF-VERIFY-001, AC-FR0267-01/02): bind the
        candidate identity on a clean tree and hand to the local-gate chain.
        A dirty tree lands attention.required(reason=dirty_tree) with no
        freeze; cleanup + ``trac run`` retries in place (§1.0.14 A,
        candidate unchanged). Idempotent (SM-01.17, interfaces §1d): an
        existing successful freeze is never re-frozen and a drifted HEAD
        never mints a candidate without an OPEN repair round -- the drift
        is marked candidate.stale and the chain stops fail-closed (the
        gate lives in the shared freeze face so the plain entry and the
        park evidence chain cannot disagree). Only §1.0.14 B / SM-01.20
        (an open repair round: a fix commit is a new candidate that
        re-walks the full M-VERIFY chain) legitimizes the re-freeze with
        its evidence.staled(reason=fix_new_candidate) semantics."""
        if reconcile and self._latest_event("candidate.frozen") is not None:
            return
        candidate_sha = self._park_freeze_candidate(cmd)
        if candidate_sha is None:
            return
        # architecture §1.1 chain order: freeze -> judge_full_f_reuse
        # (evidence.reused | full.executed) -> run_local_gates.
        self.issue(
            Command(
                kind="judge_full_f_reuse",
                params={"candidate_sha": candidate_sha},
            )
        )

    def _full_f_producer(self, candidate_sha):
        """Find a complete producer execution still selected for this candidate."""
        events = list(self.store.events(self.run_id))
        selections = [
            event
            for event in events
            if event.type == "test.selected" and event.payload.get("scope") == "full"
        ]
        latest_selection = selections[-1] if selections else None
        for event in reversed(events):
            payload = event.payload or {}
            if event.type != "full.executed":
                continue
            selection = self._producer_candidate(
                event, payload, selections, latest_selection, candidate_sha
            )
            if selection is not None:
                return event, selection
        return None, None

    def _producer_candidate(
        self, event, payload, selections, latest_selection, candidate_sha
    ):
        """The matching selection when *event* is a valid producer, else None."""
        if payload.get("execution_commit") != candidate_sha:
            return None
        if payload.get("passed") is not True or not payload.get("full_f_eligible"):
            return None
        selection_seq = payload.get("selection_event_seq")
        if not isinstance(selection_seq, int) or latest_selection is None:
            return None
        if latest_selection.seq != selection_seq:
            # A newer full selection supersedes the producer, even when it
            # has no execution proof of its own.
            return None
        if event.seq <= selection_seq:
            return None
        selection = next(
            (item for item in selections if item.seq == selection_seq), None
        )
        if selection is None or not self._producer_pair_valid(event, selection, payload):
            return None
        outcomes = self._producer_outcomes(payload)
        if outcomes is None:
            return None
        selected_nodes = {str(node) for node in (selection.payload.get("nodes") or [])}
        if not self._producer_execution_valid(outcomes, selected_nodes):
            return None
        return selection

    @staticmethod
    def _producer_pair_valid(event, selection, payload) -> bool:
        """The event/selection pair binds one and the same execution."""
        return (
            event.command_id == selection.command_id
            and selection.payload.get("selection_id") == payload.get("selection_id")
            and selection.command_id == payload.get("selection_command_id")
            and selection.payload.get("commit") == payload.get("execution_commit")
            and bool(payload.get("identity_basis"))
            and bool(payload.get("outcomes_ref"))
        )

    def _producer_outcomes(self, payload):
        try:
            return self._read_runtime_blob(payload["outcomes_ref"])
        except (OSError, TypeError, ValueError, TestSelectError):
            return None

    def _producer_execution_valid(self, outcomes, selected_nodes: set[str]) -> bool:
        """A producer's outcome WAL proves a complete passed-or-waived run.

        FR-0286 §5: failures bound to a registered known issue are waived --
        they no longer disqualify the producer. The waiver resolution is the
        same single-truth helper the required-green judgment consumes."""
        if not isinstance(outcomes, list):
            return False
        waived = self._waived_nodes(
            [str(item.get("node")) for item in outcomes if isinstance(item, dict)]
        )
        if any(
            not isinstance(item, dict)
            or (
                item.get("status") != "passed"
                and str(item.get("node")) not in waived
            )
            for item in outcomes
        ):
            return False
        outcome_nodes = {str(item.get("node")) for item in outcomes}
        if not selected_nodes or outcome_nodes != selected_nodes:
            return False
        return all(item.get("evidence_id") for item in outcomes)

    def _current_full_f_identity(self, selection):
        """Recompute FULL identity from the live contract/tree."""
        contract = load_contract(self.repo)
        sections = {"unit": contract.unit, "integration": contract.integration, "e2e": contract.e2e}
        if any(section is None for section in sections.values()):
            raise TestSelectError("FULL requires unit, integration, and e2e sections")
        inventory, collect_error = self._collect_all_declared_layers()
        if inventory is None:
            raise TestSelectError(collect_error or "FULL collect failed")
        nodes = sorted(inventory)
        baseline = self._current_baseline_digest()
        commit = git(self.repo, "rev-parse", "HEAD", check=False).stdout.strip()
        tree_stamp = self._dirty_tree_stamp()
        basis = str(selection.payload.get("basis") or "")
        if not basis:
            raise TestSelectError("FULL selection lacks its basis")
        selection_id = make_selection_id(
            nodes=nodes,
            scope="full",
            basis=basis,
            baseline=baseline,
            commit=commit,
            tree_stamp=tree_stamp,
        )
        command = self._declared_full_identity(sections, inventory)
        env = self._gate_environment_identity()
        return {
            "tree": tree_stamp,
            "command": list(command),
            "env": env,
            "selection_id": selection_id,
        }

    def _emit_full_f_judgment(self, cmd, candidate_sha, state=None) -> str:
        """FULL_F reuse judgment emission (IF-VERIFY-002, architecture §1.1):
        judge the stored FULL_F evidence against the current identity
        quadruple and land ``evidence.reused`` (kind=full_f,
        identity_basis) or ``full.executed`` (rerun) -- shared by the walk
        chain (judge_full_f_reuse command) and the park evidence chain
        (§1.0.14), both idempotent per candidate. With no stored FULL_F
        evidence (first verification) no judgment event is emitted and the
        local gates run directly -- the reuse vocabulary never fabricates a
        battery that was never run."""
        producer, evidence, producer_seq, stale_marks, decision = (
            self._full_f_judgment_inputs(candidate_sha)
        )
        if decision.decision == "reuse":
            return self._emit_full_f_reuse(cmd, candidate_sha, producer, decision)
        return self._rerun_full_f(
            cmd, state, candidate_sha, producer, evidence, stale_marks, decision
        )

    def _full_f_judgment_inputs(self, candidate_sha: str) -> tuple:
        """Producer evidence + current identity + stale marks + judge decision."""
        producer, selected = self._full_f_producer(candidate_sha)
        evidence = dict(producer.payload) if producer is not None else {}
        try:
            quadruple = (
                self._current_full_f_identity(selected) if selected is not None else {}
            )
        except (ContractError, OSError, TestSelectError, UnicodeError, ValueError):
            quadruple = {}
        producer_seq = producer.seq if producer is not None else -1
        stale_marks = tuple(
            event.type
            for event in self.store.events(self.run_id)
            if event.seq > producer_seq
            and event.type in ("candidate.stale", "evidence.staled")
            and (event.payload.get("candidate_sha") in (None, candidate_sha))
        )
        decision = m_verify.judge_full_f_reuse(
            candidate_sha, evidence, quadruple, stale_marks
        )
        return producer, evidence, producer_seq, stale_marks, decision

    def _emit_full_f_reuse(self, cmd, candidate_sha: str, producer, decision) -> str:
        prior = next(
            (
                event
                for event in reversed(list(self.store.events(self.run_id)))
                if event.type == "evidence.reused"
                and event.payload.get("kind") == "full_f"
                and event.payload.get("candidate_sha") == candidate_sha
                and event.payload.get("source_event_seq") == producer.seq
            ),
            None,
        )
        if prior is not None and json.dumps(
            list(decision.identity_basis), sort_keys=True
        ) == json.dumps(
            prior.payload.get("identity_basis") or [], sort_keys=True
        ):
            return "reused"
        self._emit(
            "evidence.reused",
            {
                "kind": "full_f",
                "candidate_sha": candidate_sha,
                "identity_basis": list(decision.identity_basis),
                "source_event_seq": producer.seq,
            },
            command_id=cmd.command_id,
        )
        return "reused"

    def _rerun_full_f(
        self, cmd, state, candidate_sha, producer, evidence, stale_marks, decision
    ) -> str:
        if producer is not None and (evidence.get("stale") or stale_marks):
            # AC-FR0268-03: the rejected FULL_F evidence is marked stale
            # on the stream (append-only) so replay shows WHY it was not
            # reused -- a stale rerun is never silent.
            self._emit(
                "evidence.staled",
                {
                    "candidate_sha": candidate_sha,
                    "reason": decision.reason,
                    "source": "full_f",
                    "identity_basis": list(decision.identity_basis),
                },
                command_id=cmd.command_id,
            )
        ledger = rebuild_ledger(self.store.events(self.run_id))
        try:
            full = self._execute_full_round(
                cmd,
                state or self.store.state(self.run_id),
                "FULL_F",
                ledger,
                candidate_sha=candidate_sha,
                judgment_reason=decision.reason,
            )
        except (ContractError, TestSelectError, TestResultError, OSError, UnicodeError) as exc:
            self._emit(
                "full.executed",
                {
                    "candidate_sha": candidate_sha,
                    "passed": False,
                    "full_f_eligible": False,
                    "reason": f"rerun_error:{type(exc).__name__}",
                    "judgment_reason": decision.reason,
                },
                command_id=cmd.command_id,
            )
            return "failed"
        if not full.get("passed") or not full.get("full_f_eligible"):
            return "failed"
        return "rerun"

    def _do_judge_full_f_reuse(self, cmd, state, task_id, reconcile):
        """M-VERIFY FULL_F reuse judgment (IF-VERIFY-002, architecture
        §1.1): judge the stored FULL_F evidence against the current
        identity quadruple. Reuse-eligible lands evidence.reused
        (kind=full_f, identity_basis); otherwise full.executed records
        the rerun and the local gates execute it."""
        params = dict(cmd.params or {})
        candidate_sha = params.get("candidate_sha", "")
        result = self._emit_full_f_judgment(cmd, candidate_sha, state)
        if result == "failed":
            return
        self.issue(
            Command(
                kind="run_local_gates",
                params={"candidate_sha": candidate_sha},
            )
        )

    def _repair_rewalk_allowed(self, frozen_sha: str) -> bool:
        """True only when the moved HEAD carries repair provenance
        (§1.0.14 B, SM-01.20).

        The repair channel is machine-identifiable, never guessed from
        content: an OPEN repair round must exist for the frozen candidate
        AND the moved HEAD must carry a ``Tracks-Repair-Round:
        <run_id>/<round>`` trailer matching that open round (a repair
        commit is a new candidate; the trailer is how the runtime tells
        a repair commit from an unrelated drift — SM-01.20). Any
        unmarked HEAD move is drift: candidate.stale, no re-freeze,
        fail-closed (SM-01.17, interfaces §1d 幂等不重冻).
        """
        if self._park_repair_budget_used() == 0:
            return False
        if self._park_candidate_seen(
            ("known_issue.registered", "known_issue.rejected"), frozen_sha
        ):
            return False
        return self._head_carries_repair_trailer(frozen_sha)

    def _head_carries_repair_trailer(self, frozen_sha: str) -> bool:
        """SM-01.20 repair provenance: the moved HEAD carries a
        ``Tracks-Repair-Round: <run_id>/<round>`` trailer matching the
        OPEN repair round bound to the frozen candidate (the
        round_started payload carries candidate_sha; one round per
        candidate within the budget)."""
        round_no = None
        for event in self.store.events(self.run_id):
            if event.type != "repair.round_started":
                continue
            payload = event.payload or {}
            if payload.get("candidate_sha") == frozen_sha:
                round_no = payload.get("round")
        if round_no is None:
            return False
        expected = f"Tracks-Repair-Round: {self.run_id}/{round_no}"
        proc = git(
            self.repo, "log", "-1", "--format=%B", "HEAD", check=False
        )
        body = proc.stdout or ""
        return any(line.strip() == expected for line in body.splitlines())

    def _do_run_local_gates(self, cmd, state, task_id, reconcile):
        """M-VERIFY local-gate chain (IF-VERIFY-003, AC-FR0269-01/02): load +
        validate the host contract (missing/malformed fail closed with
        host_contract.invalid + local_gate.failed and no M-SECURITY entry),
        materialize it (host_contract.materialized bound to the candidate),
        then execute each declared gate in contract order. Any gate failure
        stops the chain blocked -- security is routed only from a fully
        green M-VERIFY (§1.1). The stop itself is a defect finding
        (§1.0.14 B): it opens an in-place repair round on the closed
        classification (contract refusal -> contract delta, failing gate ->
        verification-only), never a stage rollback (AC-FR0286-01)."""
        params = dict(cmd.params or {})
        candidate_sha = params.get("candidate_sha", "")
        contract, _digest = self._run_contract_gates(
            cmd, candidate_sha, state, resume=reconcile
        )
        if contract is not None:
            self.issue(
                Command(
                    kind="observe_ci_runs",
                    params={"candidate_sha": candidate_sha},
                )
            )
            return
        self._park_repair_route(
            cmd,
            candidate_sha,
            self._verify_stop_class(candidate_sha),
            self._park_stop_reason("local_gate.failed"),
        )

    def _verify_stop_class(self, candidate_sha: str) -> str:
        """Closed defect class of the M-VERIFY stop bound to the candidate
        (§1.0.14 B): host_contract.invalid is the contract class, a failing
        local gate the verification-only gate class; anything else stays
        unlabeled (classify_defect fails closed, never a guessed route)."""
        contract_invalid = False
        gate_failed = False
        for event in self.store.events(self.run_id):
            payload = event.payload or {}
            if payload.get("candidate_sha") != candidate_sha:
                continue
            if event.type == "host_contract.invalid":
                contract_invalid = True
            elif event.type == "local_gate.failed":
                gate_failed = True
        if contract_invalid:
            return "contract"
        if gate_failed:
            return "gate"
        return ""

    def _load_or_default_contract(self, cmd, candidate_sha):
        """Load the host-declared canonical contract, or the
        runtime-materialized default when the host declares NONE
        (IF-HOSTCONTRACT-001: the default is written under .tracks/runtime,
        untracked — never dirties the frozen tree). A DECLARED-but-invalid
        contract fail-closes (never silently replaced by the default).
        Returns (contract, digest, source) with source in ("host",
        "runtime_default"), or (None, None, None) after the fail-closed
        block lands on the stream."""
        contract_path = self.repo.joinpath(*CANONICAL_CONTRACT_RELPATH)
        declared = None
        try:
            raw = contract_path.read_bytes()
        except OSError:
            raw = None
        if raw is not None:
            try:
                declared = tomllib.loads(raw.decode("utf-8")).get("host-contract")
            except (UnicodeDecodeError, tomllib.TOMLDecodeError):
                declared = "malformed"  # declared but unreadable: fail closed
        if declared is not None:
            # The host DECLARED a contract (valid or malformed): the
            # canonical load outcome is authoritative — materialization
            # never overrides a declared table.
            try:
                contract = load_host_contract(contract_path)
                return (
                    contract,
                    hashlib.sha256(raw).hexdigest(),
                    "host",
                )
            except (OSError, ValueError) as err:
                self._fail_verify_block(
                    cmd, candidate_sha, "missing_contract", str(err)
                )
                return None, None, None
        try:
            runtime = paths.runtime_dir(paths.tracks_home(self.repo))
            runtime.mkdir(parents=True, exist_ok=True)
            path = runtime / "materialized-host-contract.toml"
            path.write_text(_DEFAULT_HOST_CONTRACT_TOML, encoding="utf-8")
            contract = load_host_contract(path)
        except (OSError, ValueError) as err:
            self._fail_verify_block(cmd, candidate_sha, "missing_contract", str(err))
            return None, None, None
        digest = hashlib.sha256(_DEFAULT_HOST_CONTRACT_TOML.encode("utf-8")).hexdigest()
        return contract, digest, "runtime_default"

    def _run_contract_gates(self, cmd, candidate_sha: str, state, resume: bool = False):
        """Shared contract-gate face of M-VERIFY (IF-VERIFY-003): load +
        validate the host contract (the runtime-materialized default when
        the host declares none — IF-HOSTCONTRACT-001), materialize it and
        execute every declared local gate in order, then the declared
        install -> build -> smoke phases (D1). Malformed contracts and any
        failing gate/phase fail closed (host_contract.invalid /
        local_gate.failed; the chain stays blocked, §1.0.4 -- never a
        guessed command). Returns (contract, contract_digest) only when
        everything is green, else (None, None). resume=True (park evidence
        chain) reuses existing passed evidence for the same candidate
        instead of re-running it (SM-01.17 idempotency)."""
        contract, contract_digest, source = self._load_or_default_contract(
            cmd, candidate_sha
        )
        if contract is None:
            return None, None
        errors = validate_host_contract(contract, self.repo)
        if errors:
            self._fail_verify_block(cmd, candidate_sha, "malformed", "; ".join(errors))
            return None, None
        gates_complete = phases_complete = False
        if resume:
            events = list(self.store.events(self.run_id))
            gates_complete = has_complete_passed_gates(
                events,
                candidate_sha,
                contract_digest,
                contract,
                auxiliary_gate_identities(contract),
            )
            phases_complete = self._build_evidence_complete(
                events, candidate_sha, contract_digest, contract, state
            )
            if gates_complete and phases_complete:
                return contract, contract_digest
        contract_path = (
            self.repo.joinpath(*CANONICAL_CONTRACT_RELPATH)
            if source == "host"
            else paths.runtime_dir(paths.tracks_home(self.repo))
            / "materialized-host-contract.toml"
        )
        self._emit(
            "host_contract.materialized",
            {
                "candidate_sha": candidate_sha,
                "contract_path": str(contract_path),
                "contract_digest": contract_digest,
                "version": contract.contract_version,
                "source": source,
                "language": contract.language,
                "toolchain": contract.toolchain,
            },
            command_id=cmd.command_id,
        )
        if not gates_complete and not self._execute_verify_gates(
            cmd, candidate_sha, contract_digest, contract, state
        ):
            self._emit_host_contract_failure(cmd, candidate_sha, contract_digest)
            return None, None
        if not phases_complete and not self._execute_build_and_smoke(
            cmd, candidate_sha, contract, contract_digest, state
        ):
            self._emit_host_contract_failure(cmd, candidate_sha, contract_digest)
            return None, None
        return contract, contract_digest

    # -- D1: declared install -> build -> smoke execution ---------------------

    def _build_evidence_complete(
        self, events, candidate_sha: str, contract_digest: str, contract, state
    ) -> bool:
        """Every declared build/smoke phase already has reusable evidence.

        The build phase additionally requires an ``artifact.built`` whose
        byte digest still matches the file the declared artifact resolves to
        now -- a deleted/rebuilt artifact invalidates the resume.
        """
        identities = auxiliary_gate_identities(contract)
        if not identities:
            return True
        if not auxiliary_phases_passed(
            events, candidate_sha, contract_digest, identities
        ):
            return False
        if not str(contract.build_command or "").strip():
            return True
        identity, error = resolve_artifact_identity(
            self.repo, str(contract.build_artifact or ""), self._release_version_facts(state)
        )
        if identity is None:
            return False
        return any(
            event.type == "artifact.built"
            and (event.payload or {}).get("candidate_sha") == candidate_sha
            and (event.payload or {}).get("status") == "passed"
            and (event.payload or {}).get("artifact_digest") == identity["digest"]
            for event in events
        )

    def _execute_build_and_smoke(
        self, cmd, candidate_sha: str, contract, contract_digest: str, state
    ) -> bool:
        """Run the contract's install -> build -> smoke chain (D1), in the
        declared order, with commands/result channel/timeouts from the
        contract only (NFR-0147). The build emits ``artifact.built`` with
        the byte-verified name/size/digest; any failure stops the M-VERIFY
        chain blocked with its local_gate.failed evidence."""
        smoke_steps = tuple(getattr(contract, "smoke", ()) or ())
        build_declared = bool(str(getattr(contract, "build_command", "") or "").strip())
        if not build_declared and not smoke_steps:
            return True
        scope = self._phase_scope(contract, state)
        if scope is None:
            return False
        if not self._run_install_phase(cmd, candidate_sha, contract, contract_digest, scope):
            return False
        if build_declared and not self._run_build_phase(
            cmd, candidate_sha, contract, contract_digest, scope
        ):
            return False
        return self._run_smoke_phases(
            cmd, candidate_sha, contract, contract_digest, scope, smoke_steps
        )

    def _run_install_phase(
        self, cmd, candidate_sha, contract, contract_digest, scope
    ) -> bool:
        """The declared dependency install, when the contract declares one."""
        install = str(getattr(contract, "install", "") or "").strip()
        if not install:
            return True
        result = self._run_declared_phase(
            cmd, candidate_sha, contract_digest, "build", 0,
            contract.install, "exit_code", contract.build_timeout_seconds,
            scope, phase="install",
        )
        return result.status == "passed"

    def _run_build_phase(
        self, cmd, candidate_sha, contract, contract_digest, scope
    ) -> bool:
        """The declared build command plus the byte-verified artifact.built."""
        result = self._run_declared_phase(
            cmd, candidate_sha, contract_digest, "build", 0,
            contract.build_command, contract.build_result_channel,
            contract.build_timeout_seconds, scope, phase="build",
        )
        if result.status != "passed":
            return False
        identity, error = resolve_artifact_identity(
            self.repo, str(contract.build_artifact or ""), scope
        )
        if identity is None:
            self._emit_phase_failure(
                cmd, candidate_sha, contract_digest, "build", 0,
                error or "artifact_missing",
            )
            return False
        self._emit(
            "artifact.built",
            {
                "candidate_sha": candidate_sha,
                "contract_digest": contract_digest,
                "artifact": identity["name"],
                "artifact_path": str(identity["path"]),
                "size": identity["size"],
                "artifact_digest": identity["digest"],
                "command_echo": list(result.command_echo or ()),
                "status": "passed",
            },
            command_id=cmd.command_id,
        )
        scope["artifact"] = str(identity["path"])
        return True

    def _run_smoke_phases(
        self, cmd, candidate_sha, contract, contract_digest, scope, smoke_steps
    ) -> bool:
        """Each declared post-install smoke step in order."""
        for ordinal, step in enumerate(smoke_steps):
            result = self._run_declared_phase(
                cmd, candidate_sha, contract_digest, "smoke", ordinal,
                step, contract.smoke_result_channel,
                contract.smoke_timeout_seconds, scope, phase=None,
            )
            if result.status != "passed":
                return False
        return True

    def _phase_scope(self, contract, state) -> dict | None:
        """Placeholder scope for install/build/smoke ({prefix}/{artifact})."""
        scope = self._release_version_facts(state)
        needs_n = any(
            "{n}" in command
            for command in (contract.install, contract.build_command, *contract.smoke)
        )
        facts, error = complete_version_facts(
            self.repo, scope, contract.version.patch_line, needs_n=needs_n
        )
        if facts is None:
            return None
        prefix = paths.runtime_dir(paths.tracks_home(self.repo)) / "smoke-prefix"
        try:
            prefix.mkdir(parents=True, exist_ok=True)
        except OSError:
            return None
        return {
            **facts,
            "prefix": str(prefix),
            "prefix_bin": str(prefix / "bin"),
        }

    def _run_declared_phase(
        self, cmd, candidate_sha, contract_digest, kind, ordinal, command,
        result_channel, timeout_seconds, scope, *, phase,
    ) -> NormalizedGateResult:
        """Execute one declared phase command and emit its gate result."""
        decl = LocalGateDecl(
            kind=kind,
            source="command",
            command=command,
            categories=(),
            result_channel=result_channel,
            timeout_seconds=timeout_seconds,
        )
        try:
            result = execute_gate(decl, self.repo, scope)
        except Exception as err:  # noqa: BLE001 -- fail closed per phase
            result = NormalizedGateResult(
                gate_id=kind,
                result_version=1,
                status="failed",
                exit_code=None,
                summary={"error": str(err)},
            )
        if phase is not None:
            result = replace(result, summary={**result.summary, "phase": phase})
        payload = self._local_gate_payload(decl, ordinal, candidate_sha, contract_digest, result)
        if result.status == "passed":
            self._emit("local_gate.passed", payload, command_id=cmd.command_id)
        else:
            payload["reason"] = "malformed" if result.status == "malformed" else "failed"
            self._emit("local_gate.failed", payload, command_id=cmd.command_id)
        return result

    def _emit_phase_failure(
        self, cmd, candidate_sha, contract_digest, kind, ordinal, reason
    ) -> None:
        self._emit(
            "local_gate.failed",
            {
                "kind": kind,
                "gate_identity": gate_identity(kind, ordinal),
                "candidate_sha": candidate_sha,
                "contract_digest": contract_digest,
                "command_echo": [],
                "normalized_result": normalized_result_payload(
                    NormalizedGateResult(
                        gate_id=kind,
                        result_version=1,
                        status="failed",
                        exit_code=None,
                        summary={"error": reason},
                    )
                ),
                "reason": (
                    "failed"
                    if reason in ("artifact_missing", "artifact_ambiguous")
                    else reason
                ),
                "detail": reason,
            },
            command_id=cmd.command_id,
        )

    def _execute_verify_gates(self, cmd, candidate_sha, contract_digest, contract, state):
        """Run the contract's local gates in order (AC-FR0269-01), emitting
        local_gate.passed|failed per gate bound to the candidate. Returns
        True when every gate passed; any failure or unknown stops the chain
        blocked (fail closed, §1.0.4 -- never a guessed command).

        Source-based gates resolve their executable through the declared
        single truth: ``source=guard_registry`` quality gates resolve via
        the canonical registry and every requested category;
        ``source=version_decl`` gates are executed natively by the Runtime
        (tag-template derivation + remote absence probe, see
        :meth:`_execute_version_decl_gate`). A registry gate that resolves to
        nothing fails closed without guessing a host toolchain invocation
        (FR-0269 / NFR-0147). Inline-command gates
        render and run verbatim.
        """
        scope = self._release_version_facts(state)
        scope["candidate_sha"] = candidate_sha
        all_passed = True
        for ordinal, gate in enumerate(contract.local_gates):
            if not self._run_one_local_gate(
                cmd, candidate_sha, contract_digest, contract, state, ordinal, gate, scope
            ):
                all_passed = False
        return all_passed

    def _run_one_local_gate(
        self, cmd, candidate_sha, contract_digest, contract, state, ordinal, gate, scope
    ) -> bool:
        """Execute one declared gate; missing commands fail closed."""
        if gate.source == "version_decl":
            return self._execute_version_decl_gate(
                cmd, candidate_sha, contract_digest, contract, state, gate, ordinal
            )
        try:
            if gate.source == "guard_registry":
                version = state.version or getattr(self, "version", None)
                if not version:
                    raise ValueError("guard_registry requires the active version")
                architecture = paths.version_dir(self.store.home, version) / "architecture.md"
                result = execute_registry_gate(gate, self.repo, architecture, scope)
            else:
                result = execute_gate(gate, self.repo, scope)
        except Exception as err:  # noqa: BLE001 -- fail closed per gate
            self._emit_gate_exec_error(
                cmd, gate, ordinal, candidate_sha, contract_digest, err
            )
            return False
        payload = self._local_gate_payload(
            gate, ordinal, candidate_sha, contract_digest, result
        )
        if result.status == "passed":
            self._emit("local_gate.passed", payload, command_id=cmd.command_id)
            return True
        payload["reason"] = (
            "malformed" if result.status == "malformed" else "failed"
        )
        self._emit("local_gate.failed", payload, command_id=cmd.command_id)
        return False

    def _emit_gate_exec_error(
        self, cmd, gate, ordinal, candidate_sha, contract_digest, err
    ) -> None:
        self._emit(
            "local_gate.failed",
            {
                "kind": gate.kind,
                "gate_identity": gate_identity(gate.kind, ordinal),
                "candidate_sha": candidate_sha,
                "contract_digest": contract_digest,
                "command_echo": [],
                "normalized_result": normalized_result_payload(
                    NormalizedGateResult(
                        gate_id=gate.kind,
                        result_version=1,
                        status="failed",
                        exit_code=None,
                        summary={"error": str(err)},
                    )
                ),
                "reason": "unknown",
                "detail": str(err),
            },
            command_id=cmd.command_id,
        )

    @staticmethod
    def _local_gate_payload(gate, ordinal, candidate_sha, contract_digest, result) -> dict:
        return {
            "kind": gate.kind,
            "gate_identity": gate_identity(gate.kind, ordinal),
            "candidate_sha": candidate_sha,
            "contract_digest": contract_digest,
            "command_echo": list(result.command_echo or ()),
            "normalized_result": normalized_result_payload(result),
            "status": result.status,
            "exit_code": result.exit_code,
            "summary": result.summary,
        }
