"""M-VERIFY park chain extracted from the Executor (mixin
``ExecVerifyParkMixin``)."""

from __future__ import annotations

from tracks.effects.github import persist_issue_mapping
from tracks.executor import m_verify
from tracks.executor import repair as _repair
from tracks.executor.release_gate import (
    complete_version_facts,
    operation_plan_needs_n,
    version_facts,
)
from tracks.executor.release_preview import (
    _contract_table,
    assemble_preview,
    preview_blob_matches,
    preview_inputs_match,
)
from tracks.executor.security import run_security_scans, security_assessed_payload
from tracks.kernel.events import Command


class ExecVerifyParkMixin:
    """decide()->None park evidence, freeze/preview/known-issue/repair routing."""

    def _verify_chain_park_evidence(self, state) -> None:
        """M-IMPL escalation park evidence chain (FR-0287 wiring, §1.0.14):
        the parked run itself produces the machine-checkable M-VERIFY
        evidence (candidate freeze -> contract gates -> CI readback ->
        security -> release preview) so the Human escalation decision sees
        facts, not claims. Runs synchronously at the decide()->None park
        return; events only -- no stage change, no command queueing (the
        parked State the walk asserts stays untouched). Every step is
        idempotent per candidate_sha and any fail-closed block (dirty tree,
        contract refusal, gate failure, CI/security stop) halts the chain
        at that step (§1.0.14)."""
        if (
            state.stage,
            state.substate,
            getattr(state, "awaiting", None),
        ) != ("M-IMPL", "DIAGNOSE", "escalation"):
            return
        park_cmd = Command(kind="park_evidence_chain")
        candidate_sha = self._park_freeze_candidate(park_cmd)
        if candidate_sha is None:
            return
        # §1.1 chain order: freeze -> FULL_F judgment -> contract gates.
        self._emit_full_f_judgment(park_cmd, candidate_sha)
        contract, contract_digest = self._run_contract_gates(
            park_cmd, candidate_sha, state, resume=True
        )
        if contract is None:
            # Classify the stop by its actual stream evidence: a contract
            # refusal (host_contract.invalid) is the contract class, a
            # failing declared gate (local_gate.failed) the gate class —
            # the rewalk policy keys on this distinction.
            self._park_repair_route(
                park_cmd, candidate_sha, self._verify_stop_class(candidate_sha),
                self._park_stop_reason("local_gate.failed"),
            )
            return
        # OOB 2026-09-05 (origin disposition): a run parked at
        # M-IMPL/DIAGNOSE/escalation arrived here through a task failure
        # verdict. When the verify-side gates all pass -- e.g. the
        # runtime-materialized default contract's declared no-op gate
        # (6ec2dc3) -- that origin defect still requires its observable
        # disposition (interfaces §1d unified repair route: every non-pass
        # exit has one). Without this, the walked undeclared-host scenario
        # parked at CI credentials attention with the M-IMPL defect never
        # classified, and the frozen inplace-repair anchors
        # (test_no_auto_rollback_in_place_rounds et al.) lost their
        # repair.round_started (run 01M19FJVES7G113RD8QXXY3PQZ, red since
        # 6ec2dc3 landed). Idempotency/budget/closed-set guards all apply
        # inside _park_repair_route; the chain always proceeds to its
        # CI / security / preview stops unchanged (9ba8dc9 contract) -- a
        # fresh round re-arms the parked walk through its reducer and the
        # repair dispatch plays out AFTER the release face has produced its
        # evidence; yielding here instead stranded every walked scenario
        # before the M-VERIFY producers could fire (GREEN gate, run
        # 01M19FJVES7G113RD8QXXY3PQZ attempt log: preserved DBs show
        # candidate.frozen/local_gate.passed then nothing).
        self._park_origin_repair_route(park_cmd, candidate_sha, state)
        if not self._park_observe_ci(park_cmd, candidate_sha, contract):
            return
        if not self._park_assess_security(
            park_cmd, candidate_sha, contract, contract_digest
        ):
            security_status = self._park_stop_payload("security.assessed").get(
                "status"
            )
            if security_status == "failed":
                # §1.0.14 B: a failing scan is a cve-class finding -> Archer
                # advisory repair route (in-place, never a rollback); the
                # Known-Issue registration kind stays security_finding --
                # zero known issue for security (FR-0286-8).
                self._park_repair_route(
                    park_cmd,
                    candidate_sha,
                    "cve",
                    self._park_stop_reason("security.assessed"),
                    registration_kind="security_finding",
                )
            return
        self._park_preview(park_cmd, candidate_sha, contract_digest)

    def _try_freeze(self, cmd) -> m_verify.CandidateIdentity | None:
        """freeze_candidate with the dirty-tree refusal mapped to
        attention.required (interfaces §1d; §1.0.14 A in-place retry)."""
        try:
            return m_verify.freeze_candidate(self.repo)
        except m_verify.FreezeBlocked as err:
            self._emit(
                "attention.required",
                {
                    "area": "freeze",
                    "reason": "dirty_tree",
                    "stage": "M-VERIFY",
                    "detail": str(err),
                    "next": "clean tracked changes; trac run retries in place",
                },
                command_id=cmd.command_id,
            )
            return None

    def _park_freeze_candidate(self, cmd) -> str | None:
        """Park chain step 1 (IF-VERIFY-001): bind the full HEAD SHA as the
        candidate. Same-HEAD re-entry is idempotent (no re-freeze,
        SM-01.17); a drifted HEAD never re-freezes on its own -- it is
        marked candidate.stale and the chain halts fail-closed (§1d
        idempotent no-refreeze). Only an OPEN repair round (§1.0.14 B,
        SM-01.20: a fix commit is a new candidate) legitimizes the
        re-freeze: the old frozen candidate is marked stale, its
        downstream evidence goes stale with reason=fix_new_candidate
        (§1.0.14 B), and the new HEAD freezes as a fresh candidate so the
        chain re-walks it -- never a reuse of stale evidence."""
        frozen = self._latest_event("candidate.frozen")
        frozen_sha = ""
        if frozen is not None:
            frozen_sha = (frozen.payload or {}).get("candidate_sha", "")
        identity = self._try_freeze(cmd)
        if identity is None:
            return None
        if frozen is not None and identity.candidate_sha == frozen_sha:
            return frozen_sha
        if frozen is not None:
            if not self._repair_rewalk_allowed(frozen_sha):
                # SM-01.17: no repair context -- mark the drift, never
                # re-freeze; the walk's repair routing owns what happens
                # next.
                self._emit(
                    "candidate.stale",
                    {
                        "candidate_sha": frozen_sha,
                        "reason": "head_moved",
                        "detail": (
                            f"HEAD {identity.candidate_sha[:12]} moved past "
                            f"frozen candidate {frozen_sha[:12]}"
                        ),
                    },
                    command_id=cmd.command_id,
                )
                return None
            self._emit(
                "candidate.stale",
                {
                    "candidate_sha": frozen_sha,
                    "reason": "head_moved",
                    "detail": (
                        f"HEAD {identity.candidate_sha[:12]} moved past "
                        f"frozen candidate {frozen_sha[:12]}"
                    ),
                },
                command_id=cmd.command_id,
            )
            # §1.0.14 B / SM-01.20: a repair commit is a new candidate --
            # the old candidate's downstream evidence goes stale with
            # reason=fix_new_candidate (idempotent per old candidate).
            already_staled = any(
                e.type == "evidence.staled"
                and (e.payload or {}).get("reason") == "fix_new_candidate"
                and (e.payload or {}).get("old_candidate") == frozen_sha
                for e in self.store.events(self.run_id)
            )
            if not already_staled:
                staled = _repair.mark_fix_new_candidate(self.run_id, frozen_sha)
                self._emit(
                    staled["event"],
                    {k: v for k, v in staled.items() if k != "event"},
                    command_id=cmd.command_id,
                )
        self._emit(
            "candidate.frozen",
            {
                "candidate_sha": identity.candidate_sha,
                "clean_tree": identity.clean_tree,
                "branch": identity.branch,
                "frozen_at_seq": self._next_event_seq(),
            },
            command_id=cmd.command_id,
        )
        return identity.candidate_sha

    def _park_observe_ci(self, cmd, candidate_sha: str, contract) -> bool:
        """Park chain step 3 (IF-VERIFY-004, AC-FR0270-01..03): API readback
        of the required-CI run bound to the frozen candidate; a resumed run
        never repeats the API call (FR-0270). Returns False when the chain
        is blocked with the attention/binding verdict already on the
        stream (missing config/credentials, network error, binding
        mismatch -- never a silent pass)."""
        seen = [
            e
            for e in self.store.events(self.run_id)
            if e.type == "ci.run_observed"
            and (e.payload or {}).get("candidate_sha") == candidate_sha
        ]
        if seen:
            return (seen[-1].payload or {}).get("api_verified") is True
        run_payload = self._readback_ci_binding(
            cmd, candidate_sha, contract.ci or {}
        )
        if run_payload is None:
            return False
        self._emit("ci.run_observed", run_payload, command_id=cmd.command_id)
        return True

    def _park_assess_security(
        self, cmd, candidate_sha: str, contract, contract_digest: str
    ) -> bool:
        """Park chain step 4 (IF-SECURITY-001 face): run the contract-
        declared scans and aggregate fail-closed -- no declared scans or
        any malformed result aggregates to unknown, never a pass (§1.0.4).
        Emits security.assessed bound to the candidate; returns True only
        for a passed assessment so the preview can never imply a green
        policy that was never verified."""
        seen = [
            e
            for e in self.store.events(self.run_id)
            if e.type == "security.assessed"
            and (e.payload or {}).get("candidate_sha") == candidate_sha
        ]
        if seen:
            return (seen[-1].payload or {}).get("status") == "passed"
        results = run_security_scans(contract, self.repo, candidate_sha)
        # §1.0.14 B / interfaces §1d: a non-passing assessment always carries
        # its unified repair route (cve-class -> Archer advisory), never a
        # silent "none".
        payload = security_assessed_payload(
            candidate_sha, contract_digest, results, entry_key="findings", id_key="scan_id"
        )
        self._emit("security.assessed", payload, command_id=cmd.command_id)
        return payload["status"] == "passed"

    def _park_stop_payload(self, event_type: str) -> dict:
        """Latest park-chain stop event payload (empty when absent)."""
        event = self._latest_event(event_type)
        if event is None or not isinstance(event.payload, dict):
            return {}
        return event.payload

    def _park_stop_reason(self, event_type: str) -> str:
        """Reason token of the latest stop event (fail-closed detail)."""
        return str(self._park_stop_payload(event_type).get("reason", ""))

    def _park_repair_budget_used(self) -> int:
        """Repair rounds already opened in this run (FR-0286 §4: the budget
        is finite, default 3 -- ``repair=in_place round=<n>/3``)."""
        return sum(
            1
            for e in self.store.events(self.run_id)
            if e.type == "repair.round_started"
        )

    def _park_candidate_seen(
        self, event_types: tuple, candidate_sha: str, task_id: str | None = None
    ) -> bool:
        """True when any of the event types is already bound to the
        candidate -- the per-candidate idempotency guard of the repair
        route (a re-entered park never re-opens a round or re-registers).

        With ``task_id`` the match narrows to a registration of the SAME
        task (FR-0286 §5 per-defect waiver: a new task's defect registers
        its own known issue while a re-entered park of an already-registered
        task stays one-shot). A legacy registration without a task id is
        treated as covering the whole candidate (fail-safe)."""
        for event in self.store.events(self.run_id):
            if event.type not in event_types:
                continue
            payload = event.payload or {}
            if payload.get("candidate_sha") != candidate_sha:
                continue
            if task_id is None:
                return True
            event_task = payload.get("task_id")
            if event_task is None or str(event_task) == task_id:
                return True
        return False

    def _park_current_task_id(self) -> str:
        """The task the current failed disposition belongs to (latest lease)."""
        for event in reversed(list(self.store.events(self.run_id))):
            if event.type == "task.started":
                return str((event.payload or {}).get("task_id") or "")
            if event.type == "ledger.opened" and (event.payload or {}).get("task_id"):
                return str(event.payload["task_id"])
        return ""

    def _park_next_known_issue_number(self) -> int:
        """Durable issue numbering: max recorded number + 1 (never reused).

        The process-local counter cannot number registrations across the M7
        handover boundary (two drives would both mint #100); the event
        stream is the durable authority (FR-0286 §5)."""
        numbers = [
            int(payload["issue_number"])
            for event in self.store.events(self.run_id)
            if event.type == "known_issue.registered"
            for payload in [(event.payload or {})]
            if isinstance(payload.get("issue_number"), int)
            and not isinstance(payload.get("issue_number"), bool)
        ]
        return max(numbers, default=99) + 1

    def _park_task_waiver_refs(self, task_id: str) -> tuple[list[str], list[str]]:
        """(ac_refs, node_refs) the current disposition waives.

        AC refs come from the task's declared ac_refs; node refs are the
        latest FULL failed nodes bound to those ACs by the test markers --
        exact per-AC association, never the whole failed set (a different
        task's defect must register independently)."""
        acs: list[str] = []
        for event in reversed(list(self.store.events(self.run_id))):
            if event.type == "task.started" and str(
                (event.payload or {}).get("task_id") or ""
            ) == task_id:
                acs = [
                    str(ac)
                    for ac in ((event.payload.get("task") or {}).get("ac_refs") or [])
                    if str(ac).strip()
                ]
                break
        failed: list[str] = []
        for event in reversed(list(self.store.events(self.run_id))):
            if event.type == "full.executed":
                failed = [
                    str(node)
                    for node in ((event.payload or {}).get("failed_nodes") or [])
                    if str(node).strip()
                ]
                break
        if not acs:
            return [], failed
        waived_acs = set(acs)
        return acs, [
            node
            for node in failed
            if self._file_test_markers(node.partition("::")[0]) & waived_acs
        ]

    def _file_test_markers(self, rel_path: str) -> set[str]:
        """TRACKS-TRACE AC markers bound to one test file (empty on miss).

        The file-level binding is the trace gate's own convention
        (``_scan_test_markers``): every AC marker in the file binds the
        file's nodes."""
        if not rel_path:
            return set()
        try:
            text = (self.repo / rel_path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return set()
        from tracks.checks.trace import _MARKER_LINE  # lazy: checks -> executor import

        return {match.group(2) for match in _MARKER_LINE.finditer(text)}

    def _waived_nodes(self, nodes: list[str]) -> set[str]:
        """Nodes waived by registered known issues (FR-0286 §5, single truth).

        Consumes the event-derived association the same way for producer
        selection, required-green judgment and ledger recording: direct
        ``node_refs`` always apply; AC refs resolve through the file-level
        TRACKS-TRACE markers of the given nodes."""
        assoc = _repair.waived_associations(self.store.events(self.run_id))
        waived = {node for node in nodes if node in assoc["nodes"]}
        acs = assoc["acs"]
        if not acs:
            return waived
        markers: dict[str, set[str]] = {}
        for node in nodes:
            if node in waived:
                continue
            path = node.partition("::")[0]
            if path not in markers:
                markers[path] = self._file_test_markers(path)
            if markers[path] & acs:
                waived.add(node)
        return waived

    def _park_prism_attribution(self) -> dict:
        """Prism attribution confirmation (IF-KNOWNISSUE-001): a registered
        failed verdict is the park chain's only Prism signal -- without one
        the empty attribution keeps judge_irreparable False (no confirmed
        product defect is ever registered on an unconfirmed attribution)."""
        for event in self.store.events(self.run_id):
            if event.type in ("verdict.failed", "prism.verdict"):
                return {"attribution_unchanged": True}
        return {}

    def _park_repair_route(
        self,
        cmd,
        candidate_sha: str,
        finding_kind: str,
        reason: str,
        registration_kind: str | None = None,
    ) -> bool:
        """§1.0.14 B/C-class in-place repair face of the park chain.

        Classifies the chain stop through the FR-0286 closed mapping
        (classify_defect), opens one repair round per chain run within the
        budget (repair.round_started + repair_route), and on budget
        exhaustion routes the irreparable verdict to Known Issue
        registration. ``registration_kind`` overrides the Known-Issue
        attribution kind when the finding's registration vocabulary differs
        from its repair class (a failed security scan classifies cve ->
        Archer advisory, but registers -- and is rejected -- as
        ``security_finding``: zero known issue for security, FR-0286-8).
        Never a stage rollback (AC-FR0286-01); never a guessed route for an
        unknown finding (fail closed). Returns True when a fresh round
        opened -- the round's reducer re-arms the parked walk (the repair
        dispatch owns the next move); False when the disposition resolved
        without one (known issue registered/rejected, or a fail-closed
        classification)."""
        classification = _repair.classify_defect({"kind": finding_kind}, {})
        if classification.get("failed_class"):
            return False
        task_id = self._park_current_task_id()
        if self._park_candidate_seen(
            ("known_issue.registered",), candidate_sha, task_id or None
        ):
            return False  # task registration idempotency: one disposition per
            # task; the ROUND budget is the bounded loop -- each re-entered
            # park of a still-unresolved disposition opens the next round (up
            # to the budget) so attempt-exhausted walks reach the C-class
            # exit, and a NEW task's defect registers its own disposition
            # (FR-0286 §5 per-defect waiver).
        budget = 3
        used = self._park_repair_budget_used()
        if used >= budget:
            self._park_known_issue(
                cmd,
                candidate_sha,
                registration_kind or finding_kind,
                reason,
                used,
                budget,
                task_id=task_id,
            )
            return False
        result = _repair.open_repair_round(self.run_id, classification, budget)
        payload = {k: v for k, v in result.items() if k != "event"}
        # The round number is event-derived (persistent across processes),
        # never the in-memory counter of this walk invocation.
        payload["round"] = used + 1
        payload["candidate_sha"] = candidate_sha
        payload["repair_route"] = {
            "defect_class": classification["defect_class"],
            "owner": classification["owner"],
            "discipline": classification["discipline"],
            "budget_remaining": budget - (used + 1),
        }
        if reason:
            payload["reason"] = reason
        self._emit(result["event"], payload, command_id=cmd.command_id)
        return True

    def _park_origin_repair_route(self, cmd, candidate_sha: str, state) -> bool:
        """OOB 2026-09-05: open the repair disposition for the ORIGIN defect
        that parked the run (M-IMPL task failure) when the verify-side gates
        all passed. Classifies behaviour (owner Devon, red_first) from the
        latest impl_defect verdict; every _park_repair_route guard applies
        (budget, closed-set classification). test_defect origins stay
        fail-closed (not in the verify-side classification table -- the
        ruling channel owns them). Returns True when a fresh round opened
        (the round re-arms the walk; the chain yields to the repair).

        §11.4 irreparability #1 (AC-FR0286-04/05, OOB 2026-09-06): a re-park
        on a candidate whose repair round is already open, with the repair
        dispatches' attempt budget burned (state.current_attempt >= 3, the
        shared M-IMPL budget whose exhaustion parked the run), means the fix
        attempts were spent WITHOUT landing a fix commit -- the round count
        alone can never advance past one (only a fix commit mints a new
        candidate). Route the C-class known-issue exit instead of silently
        stopping at the one-shot round guard."""
        round_seen = self._park_candidate_seen(
            ("repair.round_started",), candidate_sha
        )
        task_id = self._park_current_task_id()
        if round_seen and state.current_attempt >= 3:
            budget = 3
            self._park_known_issue(
                cmd,
                candidate_sha,
                "behavior",
                (
                    "repair attempts exhausted without a fix commit "
                    "(§11.4 irreparability #1: attempt budget spent, "
                    "candidate unchanged)"
                ),
                # §11.4 #1 measures the budget in FIX ATTEMPTS, not landed
                # fix commits: the attempt exhaustion IS the budget
                # exhaustion for this criterion (round-counting alone can
                # never reach it without a fix -- judge_irreparable would
                # silently veto the C-class exit).
                used=budget,
                budget=budget,
                task_id=task_id,
            )
            return False
        if self._park_candidate_seen(
            ("known_issue.registered",), candidate_sha, task_id or None
        ):
            return False
        origin_reason = ""
        origin = False
        for event in self.store.events(self.run_id):
            if event.type != "verdict.failed":
                continue
            payload = event.payload or {}
            if payload.get("check") == "impl_defect":
                origin = True
                origin_reason = str(payload.get("reason") or "")
        if not origin:
            return False
        return self._park_repair_route(
            cmd,
            candidate_sha,
            "behavior",
            origin_reason or "park origin: M-IMPL task failure verdict",
        )

    def _park_known_issue(
        self,
        cmd,
        candidate_sha: str,
        finding_kind: str,
        reason: str,
        used: int,
        budget: int,
        task_id: str | None = None,
    ) -> None:
        """C-class exit (§1.0.14): with the budget exhausted and Prism
        confirming the attribution, a product-quality defect registers a
        Known Issue (known-issue label, candidate+evidence linked); the
        excluded classes (mechanism/security) are rejected with
        not_product_defect -- zero known issue for security.

        FR-0286 §5: the registration carries the waived task and its
        AC/node refs so the machine closes the task as waived and the FULL
        gates exclude exactly those bound nodes."""
        task_id = task_id or self._park_current_task_id()
        if self._park_candidate_seen(
            ("known_issue.registered", "known_issue.rejected"),
            candidate_sha,
            task_id or None,
        ):
            return
        attribution = self._park_prism_attribution()
        if not _repair.judge_irreparable(used, budget, attribution):
            return
        verdict = self._park_stop_payload("verdict.failed")
        ac_refs, node_refs = self._park_task_waiver_refs(task_id)
        # FR-0286 §5 two-drive disposition: the first exhausted park records
        # the observation (association refs included) and leaves the run
        # parked -- the escalation remains a legal Human escape source
        # (FR-0287). The next drive commits the registration, whose machine
        # projection lifts the park and waives the task.
        if self._park_pending_known_issue(
            cmd, candidate_sha, task_id, reason, ac_refs, node_refs
        ):
            return
        self._register_parked_known_issue(
            cmd,
            candidate_sha,
            finding_kind,
            reason,
            task_id,
            verdict,
            ac_refs,
            node_refs,
        )

    def _park_pending_known_issue(
        self,
        cmd,
        candidate_sha: str,
        task_id: str | None,
        reason: str,
        ac_refs: list,
        node_refs: list,
    ) -> bool:
        """Record the first-drive observation; True when the run stays parked."""
        if self._park_candidate_seen(
            ("known_issue.pending",), candidate_sha, task_id or None
        ):
            return False
        self._emit(
            "known_issue.pending",
            {
                "candidate_sha": candidate_sha,
                "task_id": task_id or None,
                "ac_refs": ac_refs,
                "node_refs": node_refs,
                "reason": reason,
            },
            command_id=cmd.command_id,
        )
        return True

    def _register_parked_known_issue(
        self,
        cmd,
        candidate_sha: str,
        finding_kind: str,
        reason: str,
        task_id: str | None,
        verdict: dict,
        ac_refs: list,
        node_refs: list,
    ) -> None:
        """Commit the second-drive registration + mapping + preview regen."""
        item_or_ac = str(
            verdict.get("item_or_ac")
            or (ac_refs[0] if ac_refs else "")
            or verdict.get("classification")
            or reason
        )
        registration = _repair.register_known_issue(
            "repo",
            {
                "kind": finding_kind,
                "item_or_ac": item_or_ac,
                "task_id": task_id or None,
                "ac_refs": ac_refs,
                "node_refs": node_refs,
                "issue_number": self._park_next_known_issue_number(),
            },
            candidate_sha,
        )
        payload = {k: v for k, v in registration.items() if k != "event"}
        self._emit(registration["event"], payload, command_id=cmd.command_id)
        if registration["event"] == "known_issue.registered":
            # FR-0283 mapping face: a known issue is durable run state too --
            # it enters the map explicitly non-authoritative so the M-MILESTONE
            # closer audits it as an unclosable identity instead of leaving an
            # invisible waiver (never api_verified=true).
            self._persist_known_issue_mapping(registration, task_id, candidate_sha)
            self._regen_preview_after_known_issue(cmd, candidate_sha)

    def _persist_known_issue_mapping(
        self, registration: dict, task_id: str | None, candidate_sha: str
    ) -> None:
        persist_issue_mapping(
            self.repo,
            f"known_issue:{registration.get('issue_number')}",
            {
                "issue_number": registration.get("issue_number"),
                "url": registration.get("url", ""),
                "api_verified": False,
                "authoritative": False,
                "source": "known_issue",
                "task_id": task_id or None,
                "baseline_digest": candidate_sha,
            },
        )

    def _regen_preview_after_known_issue(self, cmd, candidate_sha: str) -> None:
        """AC-FR0286-05 informed consent: the registered known issue must
        appear in the release preview. Regenerate ONLY when a prior
        preview already exists for this candidate -- that is the one
        case the listing can be stale (the chain completed CI/security
        before the registration landed). Without a prior preview the
        chain's own tail preview (after the CI readback and security
        steps, which a Known Issue never waives) lists the issue
        through list_known_issues_for_preview; previewing here would
        short-circuit those gates (live 01M284A3: waiver preview with
        no CI/prism-final/security evidence, correctly rejected by
        the release gate and unreachable forever after)."""
        prior = next(
            (
                event
                for event in reversed(list(self.store.events(self.run_id)))
                if event.type == "release.previewed"
                and (event.payload or {}).get("candidate_sha") == candidate_sha
            ),
            None,
        )
        if prior is not None:
            # _park_preview re-resolves the (materialized default)
            # contract from its stable source; the prior digest pins
            # the expectation and assemble_preview fails closed on any
            # contract drift.
            self._park_preview(cmd, candidate_sha, "")

    def _release_journey(self, state, cmd) -> str | None:
        """Resolve the preview's declared journey (IF-JOURNEY-001 §1m).

        A hotfix run owns its journey on the State (``hotfix_scenario``:
        post-release -> ``post_release``, dev -> ``dev``); every other run
        keeps the command-declared journey (default resolution happens in
        ``release_preview._journey`` -- feature when declared)."""
        scenario = getattr(state, "hotfix_scenario", None)
        if scenario:
            return {
                "post-release": "post_release",
                "post_release": "post_release",
                "dev": "dev",
            }.get(str(scenario), str(scenario))
        return (cmd.params or {}).get("journey")

    def _park_preview(
        self, cmd, candidate_sha: str, contract_or_digest, contract_digest: str | None = None
    ) -> None:
        """Park chain step 5 (IF-RELEASE-002 face): assemble the content-
        addressed preview from the verified evidence digests; reached only
        after gates + CI + security are green. Regenerates when the
        registered known-issue set changed since the last preview
        (AC-FR0286-05 informed consent -- the listing must never be stale);
        regeneration appends a new event, the old preview is never
        overwritten (§1.0.5)."""
        contract, contract_digest = self._park_preview_contract(
            cmd, candidate_sha, contract_or_digest, contract_digest
        )
        if contract is None:
            return
        events = list(self.store.events(self.run_id))
        known_issues = _repair.list_known_issues_for_preview(self.run_id, events)
        state = self.store.state(self.run_id)
        journey = self._release_journey(state, cmd)
        facts = self._park_preview_facts(
            cmd, candidate_sha, contract, events, journey, state
        )
        if facts is None:
            return
        preview = assemble_preview(
            self.repo,
            contract,
            candidate_sha,
            contract_digest,
            facts,
            events,
            journey=journey,
            known_issues=known_issues,
        )
        if preview is None:
            return
        if not self._park_preview_changed(events, candidate_sha, preview):
            return
        blob = self.store.write_audit_blob(preview)
        if not blob:
            return
        preview = dict(preview)
        preview["blob_ref"] = f".tracks/runtime/blobs/{blob}"
        self._emit("release.previewed", preview, command_id=cmd.command_id)

    def _park_preview_contract(
        self, cmd, candidate_sha: str, contract_or_digest, contract_digest: str | None
    ) -> tuple:
        """Resolve the preview contract (reloading when handed a digest)."""
        if isinstance(contract_or_digest, str):
            contract, contract_digest, _source = self._load_or_default_contract(
                cmd, candidate_sha
            )
            if contract is None:
                return None, None
            return contract, contract_digest
        return contract_or_digest, contract_digest

    def _park_preview_facts(
        self, cmd, candidate_sha: str, contract, events: list, journey, state
    ) -> dict | None:
        """Version facts completed with the remote census, or None (attention)."""
        version = getattr(state, "version", None) or next(
            (event.version for event in reversed(events) if event.version), ""
        )
        facts = version_facts(version, self.run_id)
        patch_line = str(
            getattr(getattr(contract, "version", None), "patch_line", "") or ""
        )
        table = _contract_table(contract)
        facts, error = complete_version_facts(
            self.repo,
            facts,
            patch_line,
            needs_n=operation_plan_needs_n(table, journey or ""),
        )
        if facts is None:
            # Honest attention: a configured remote whose tag census cannot
            # be read must not silently resolve {n}=1 (patch identity guess).
            self._emit(
                "attention.required",
                {
                    "area": "release_facts",
                    "reason": error or "release_facts_unresolved",
                    "stage": "M-RELEASE",
                    "candidate_sha": candidate_sha,
                    "detail": "remote patch-tag census failed for patch_line "
                    f"{patch_line!r}",
                    "next": "check origin access; trac run retries the preview",
                },
                command_id=cmd.command_id,
            )
            return None
        return facts

    def _park_preview_changed(self, events: list, candidate_sha: str, preview: dict) -> bool:
        """True when no equivalent persisted preview already exists."""
        seen = [
            event for event in events
            if event.type == "release.previewed"
            and (event.payload or {}).get("candidate_sha") == candidate_sha
        ]
        if not seen:
            return True
        last = seen[-1].payload or {}
        return not (
            preview_inputs_match(last, preview)
            and preview_blob_matches(self.store.home, last)
        )

    def _release_version_facts(self, state) -> dict:
        """Placeholder scope for contract gate commands ({version}/{major}/...

        Shared base facts (IF-JOURNEY-001 §1m): a hotfix identity inherits
        its target's ``{major}/{minor}`` (``v0.8-hotfix-42`` -> ``0.8``)
        while ``{version}`` keeps the run identity, and ``{ulid}`` carries
        the run id. ``{n}`` is completed by the release face through
        :func:`complete_version_facts` (remote tag census). A gate command
        referencing an undeclared placeholder fails closed through the
        per-gate execution guard (unknown placeholder -> local_gate.failed),
        never a guessed substitution (§1.0.4)."""
        return version_facts(getattr(state, "version", None) or "", self.run_id)

    def _fail_verify_block(self, cmd, candidate_sha, reason, detail):
        """Fail-closed M-VERIFY contract refusal (AC-FR0269-02): the
        contract-level refusal lands host_contract.invalid +
        local_gate.failed and halts the chain blocked -- no M-SECURITY
        entry, no guessed commands (§1.0.4)."""
        self._emit(
            "host_contract.invalid",
            {"candidate_sha": candidate_sha, "reason": reason, "detail": detail},
            command_id=cmd.command_id,
        )
        self._emit(
            "local_gate.failed",
            {"kind": "contract", "candidate_sha": candidate_sha, "reason": reason},
            command_id=cmd.command_id,
        )
