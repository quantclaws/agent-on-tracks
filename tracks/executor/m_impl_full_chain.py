"""M-IMPL FULL chain (mixin ``MImplFullChainMixin``): layer execution,
F-eligibility, failure ledger and fixed-proof reconciliation.

Extracted from :mod:`tracks.executor.m_impl_runtime` for module-size
compliance (C0302); code moved verbatim.
"""

from __future__ import annotations

import hashlib
import json
import shlex
from dataclasses import dataclass
from pathlib import Path

from tracks.executor.helpers import git
from tracks.executor.quality_gate import execute_gate_command
from tracks.executor.test_select import (
    EvidenceIdentity,
    LedgerCorruptionError,
    TestSelectError,
    cleanup_staged_results,
    evidence_by_node_for,
    ledger_is_clean,
    make_selection_id,
    parse_test_result,
    rebuild_ledger,
    require_exact_node_coverage,
    resolve_selected_command,
    select_diff,
    selection_event_payload,
    stage_audited_selection,
)
from tracks.executor.test_select import audit as audit_selection_argv
from tracks.executor.test_tasks import _LAYER_CELL_SEPARATOR, _coverage_rows_with_test
from tracks.project import load_contract

# FULL chains unit/integration/e2e; a collect command exits 5 when the
# framework collects zero tests ("no tests collected") -- legal ONLY for a
# layer that declares no anchor nodes (see _declared_anchor_layers).
_FULL_LAYERS = ("unit", "integration", "e2e")


_EMPTY_LAYER_RC = frozenset({5})


@dataclass(frozen=True)
class _FullSelection:
    """Persisted FULL selection identity (nodes + test.selected event)."""

    inventory: dict
    nodes: list
    baseline: str
    commit: str
    tree_stamp: str
    selection_id: str
    selection_event: object


@dataclass(frozen=True)
class _FullRoundHeader:
    """One FULL round's stable inputs + selection identity."""

    cmd: object
    state: object
    round_name: str
    ledger: object
    candidate_sha: str | None
    judgment_reason: str | None
    sections: dict
    inventory: dict
    nodes: list
    baseline: str
    commit: str
    tree_stamp: str
    selection_id: str
    selection_event: object


@dataclass(frozen=True)
class _FullRun:
    """The five execution artifacts of ``_run_full_layers``."""

    outcomes: list
    command_echo: dict
    command_cwds: dict
    command_exit_codes: dict
    result_paths: list


@dataclass(frozen=True)
class _FullEvidence:
    """Evidence binding + waiver-adjusted blocking set of one FULL run."""

    command_identity: object
    env_identity: object
    evidence_by_node: dict
    outcomes_ref: str
    blocking: list
    waived_nodes: list
    empty_pass_layers: dict


class MImplFullChainMixin:
    """M-IMPL FULL chain mixin."""

    def _current_baseline_digest(self) -> str:
        return next(
            (
                str(ev.payload.get("digest") or "")
                for ev in reversed(list(self.store.events(self.run_id)))
                if ev.type == "baseline.frozen" and ev.payload.get("status") == "current"
            ),
            "",
        )

    @staticmethod
    def _normalize_full_argv(argv, result_path=None):
        """Canonicalize FULL argv without binding evidence to its temp XML."""
        normalized = []
        exact_path = str(result_path) if result_path else None
        for raw in argv:
            value = str(raw)
            if exact_path and value == exact_path:
                value = "<result>"
            elif exact_path and value.endswith(f"={exact_path}"):
                value = f"{value[: -len(exact_path)]}<result>"
            normalized.append(value)
        return normalized

    @classmethod
    def _full_command_identity(cls, commands, cwds, result_paths=None) -> tuple[str, ...]:
        """Encode declared FULL commands, cwd and argv boundaries canonically."""
        return tuple(
            json.dumps(
                {
                    "layer": layer,
                    "argv": cls._normalize_full_argv(
                        commands[layer], (result_paths or {}).get(layer)
                    ),
                    "cwd": str(cwds[layer]),
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            for layer in sorted(commands)
        )

    def _declared_full_identity(self, sections, inventory):
        """Derive current FULL commands without executing them."""
        commands = {}
        cwds = {}
        for layer, section in sections.items():
            section_cwd = self.repo / section.cwd if section.cwd != "." else self.repo
            commands[layer] = resolve_selected_command(
                section.run, [], "<result>", Path(section_cwd)
            )
            cwds[layer] = str(Path(section_cwd).resolve())
        return self._full_command_identity(commands, cwds)

    def _run_full_layers(self, cmd, round_name: str, sections, inventory):  # pylint: disable=too-many-locals
        """Run every declared FULL layer, staging one test result per layer."""
        outcomes: list[dict] = []
        command_echo: dict[str, list[str]] = {}
        command_cwds: dict[str, str] = {}
        command_exit_codes: dict[str, int] = {}
        result_paths: dict[str, str] = {}
        staged: list[Path] = []
        try:
            for layer, section in sections.items():
                assert section is not None
                selected = sorted(node for node, owner in inventory.items() if owner == layer)
                section_cwd = self.repo / section.cwd if section.cwd != "." else self.repo
                result_path = self._result_staging_path(
                    cmd.command_id, f"full-{round_name.lower()}-{layer}"
                )
                staged.append(result_path)
                result_paths[layer] = str(result_path)
                result_path.unlink(missing_ok=True)
                argv = resolve_selected_command(
                    section.run,
                    [],
                    str(result_path),
                    Path(section_cwd),
                )
                if not audit_selection_argv(
                    section.run,
                    [],
                    str(result_path),
                    list(argv),
                    Path(section_cwd),
                ):
                    raise TestSelectError(f"[{layer}] FULL argv diverges from contract")
                obs = execute_gate_command(shlex.join(argv), str(section_cwd), f"full-{layer}")
                command_echo[layer] = list(obs.argv)
                command_cwds[layer] = str(Path(obs.cwd).resolve())
                command_exit_codes[layer] = obs.exit_code
                cases = parse_test_result(result_path)
                mapping = require_exact_node_coverage(cases, selected)
                outcomes.extend(
                    {
                        "node": node,
                        "status": mapping[node].status,
                        "detail": mapping[node].detail or "",
                    }
                    for node in selected
                )
        finally:
            cleanup_staged_results(staged)
        return outcomes, command_echo, command_cwds, command_exit_codes, result_paths

    def _declared_anchor_layers(self) -> set[str] | None:
        """Test-plan §8 anchor layers for this run's frozen design.

        FULL eligibility must distinguish a layer that declares NO anchor
        node at all (an undeclared/empty layer: exit code 5 "no tests
        collected" is a legal empty-pass) from one that declared anchors but
        collected zero nodes (collect defect; remains fail-closed). The
        declared set is parsed from the run's own
        ``.tracks/projects/<version>/test-plan.md`` §8 AC Coverage rows -- a
        durable runtime artifact, never the mutable test tree. Returns None
        when the plan is missing/unreadable or declares no anchors at all:
        callers then treat every contract layer as declared (fail-closed)."""
        plan_path = self._vdir() / "test-plan.md"
        try:
            plan_text = plan_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return None
        declared: set[str] = set()
        for _ac_id, layer_cell, _test_cell, _if_cell in _coverage_rows_with_test(plan_text):
            for token in _LAYER_CELL_SEPARATOR.split(layer_cell or ""):
                normalized = token.strip().lower()
                if normalized in _FULL_LAYERS:
                    declared.add(normalized)
        return declared or None

    def _split_waived_failures(self, failed: list[dict]) -> tuple[list[dict], list[str]]:
        """Split FULL failures into (blocking, waived node audit).

        FR-0286 §5 single truth: the waiver resolution is the shared
        event-derived helper; a waived failure is recorded but never blocks."""
        waived_set = self._waived_nodes([failure["node"] for failure in failed])
        waived_nodes = sorted(
            failure["node"] for failure in failed if failure["node"] in waived_set
        )
        blocking = [
            failure for failure in failed if failure["node"] not in waived_set
        ]
        return blocking, waived_nodes

    @staticmethod
    def _full_f_eligible(
        blocking: list[dict],
        waived_nodes: list[str],
        command_exit_codes: dict[str, int],
        empty_pass_layers: dict[str, bool],
    ) -> bool:
        """FULL_F eligibility: no blocking failure AND a trustworthy layer
        exit-code face.

        A layer whose only failures are waived exits non-zero; that is the
        waiver-adjusted proof, not a defect (FR-0286 §5)."""
        if blocking:
            return False
        if waived_nodes:
            return True
        return all(
            code == 0 or empty_pass_layers.get(layer, False)
            for layer, code in command_exit_codes.items()
        )

    def _full_round_sections(self) -> dict:
        contract = load_contract(self.repo)
        sections = {
            "unit": contract.unit,
            "integration": contract.integration,
            "e2e": contract.e2e,
        }
        if any(section is None for section in sections.values()):
            raise TestSelectError("FULL requires unit, integration, and e2e sections")
        return sections

    def _full_round_selection(self, cmd, round_name: str) -> _FullSelection:
        """Collect + persist the FULL selection identity (nodes blob event)."""
        inventory, collect_error = self._collect_all_declared_layers()
        if inventory is None:
            raise TestSelectError(collect_error or "FULL collect failed")
        nodes = sorted(inventory)
        baseline = self._current_baseline_digest()
        if not baseline:
            raise TestSelectError("FULL lacks a frozen M-IMPL baseline identity")
        commit = git(self.repo, "rev-parse", "HEAD", check=False).stdout.strip()
        tree_stamp = self._dirty_tree_stamp()
        selection_id = make_selection_id(
            nodes=nodes,
            scope="full",
            basis=f"full:{round_name}",
            baseline=baseline,
            commit=commit,
            tree_stamp=tree_stamp,
        )
        node_ref = self.store.write_audit_blob(
            [{"node": node, "layer": inventory[node]} for node in nodes]
        )
        if node_ref is None:
            raise TestSelectError("FULL nodes blob write failed")
        selection_event = self._emit(
            "test.selected",
            selection_event_payload(
                scope="full",
                basis=f"full:{round_name}",
                nodes=nodes,
                nodes_blob=f".tracks/runtime/blobs/{node_ref}",
                baseline=baseline,
                commit=commit,
                tree_stamp=tree_stamp,
                selection_id=selection_id,
            ),
            command_id=cmd.command_id,
        )
        return _FullSelection(
            inventory, nodes, baseline, commit, tree_stamp, selection_id, selection_event
        )

    def _full_round_header(
        self,
        cmd,
        state,
        round_name: str,
        ledger,
        candidate_sha: str | None,
        judgment_reason: str | None,
    ) -> _FullRoundHeader:
        selection = self._full_round_selection(cmd, round_name)
        return _FullRoundHeader(
            cmd=cmd,
            state=state,
            round_name=round_name,
            ledger=ledger,
            candidate_sha=candidate_sha,
            judgment_reason=judgment_reason,
            sections=self._full_round_sections(),
            inventory=selection.inventory,
            nodes=selection.nodes,
            baseline=selection.baseline,
            commit=selection.commit,
            tree_stamp=selection.tree_stamp,
            selection_id=selection.selection_id,
            selection_event=selection.selection_event,
        )

    def _full_round_evidence(self, header: _FullRoundHeader, run: _FullRun) -> _FullEvidence:
        """Bind execution outcomes to persisted evidence (fail-closed blobs)."""
        declared_layers = self._declared_anchor_layers()
        if declared_layers is None:
            declared_layers = set(_FULL_LAYERS)
        empty_pass_layers = {
            layer: code in _EMPTY_LAYER_RC and layer not in declared_layers
            for layer, code in run.command_exit_codes.items()
        }
        command_identity = self._full_command_identity(
            run.command_echo, run.command_cwds, run.result_paths
        )
        env_identity = self._gate_environment_identity()
        identity = EvidenceIdentity(
            tree=header.tree_stamp,
            command=command_identity,
            env=env_identity,
            selection_id=header.selection_id,
        )
        evidence_by_node = evidence_by_node_for(
            identity, run.outcomes, header.state.current_attempt + 1
        )
        persisted_outcomes = [
            {**outcome, "evidence_id": evidence_by_node[outcome["node"]]}
            for outcome in run.outcomes
        ]
        outcomes_ref = self.store.write_audit_blob(persisted_outcomes)
        if outcomes_ref is None:
            raise TestSelectError("FULL outcomes blob write failed")
        failed = self._signed_failures(run.outcomes)
        # FR-0286 §5 waiver: nodes bound to a registered known issue are
        # still executed and recorded (outcomes_ref carries every outcome)
        # but no longer block the required-green judgment. The waiver set
        # comes from the same event-derived extraction every other consumer
        # uses -- the audit list stays on the payload.
        blocking, waived_nodes = self._split_waived_failures(failed)
        return _FullEvidence(
            command_identity,
            env_identity,
            evidence_by_node,
            outcomes_ref,
            blocking,
            waived_nodes,
            empty_pass_layers,
        )

    def _finalize_full_round(
        self, header: _FullRoundHeader, run: _FullRun, evidence: _FullEvidence
    ) -> dict:
        # A layer whose only failures are waived exits non-zero; the
        # required-green proof is still complete (FR-0286 §5: the waiver
        # changes the blocking set, never the execution record).
        serves_as_full_f = not evidence.blocking and (
            (header.round_name == "FULL_1" and not header.ledger)
            or header.round_name == "fallback_full"
        )
        payload = {
            "round": header.round_name,
            "suite": ["unit", "integration", "e2e"],
            "passed": not evidence.blocking,
            "failed_nodes": [outcome["node"] for outcome in evidence.blocking],
            "waived_nodes": evidence.waived_nodes,
            "command_echo": run.command_echo,
            "evidence_ids": sorted(evidence.evidence_by_node.values()),
            "serves_as_full_f": serves_as_full_f,
            "outcomes_ref": f".tracks/runtime/blobs/{evidence.outcomes_ref}",
            "gate": "ISLAND_GATE_2",
            "selection_id": header.selection_id,
            "failures": evidence.blocking,
            "evidence_by_node": evidence.evidence_by_node,
            "execution_commit": header.commit,
            "selection_event_seq": header.selection_event.seq,
            "selection_command_id": header.selection_event.command_id,
            "identity_basis": [
                header.tree_stamp,
                list(evidence.command_identity),
                evidence.env_identity,
                header.selection_id,
            ],
            "identity": {
                "tree": header.tree_stamp,
                "command": list(evidence.command_identity),
                "env": evidence.env_identity,
                "selection_id": header.selection_id,
            },
            "full_f_eligible": self._full_f_eligible(
                evidence.blocking,
                evidence.waived_nodes,
                run.command_exit_codes,
                evidence.empty_pass_layers,
            ),
            "empty_pass_layers": evidence.empty_pass_layers,
            "command_cwds": run.command_cwds,
            "command_exit_codes": run.command_exit_codes,
        }
        if header.candidate_sha is not None:
            payload["candidate_sha"] = header.candidate_sha
        if header.judgment_reason is not None:
            payload["judgment_reason"] = header.judgment_reason
            payload["reason"] = header.judgment_reason
        event_payload = {
            key: value
            for key, value in payload.items()
            if key not in ("failures", "evidence_by_node")
        }
        self._emit("full.executed", event_payload, command_id=header.cmd.command_id)
        return payload

    def _execute_full_round(
        self,
        cmd,
        state,
        round_name: str,
        ledger,
        candidate_sha: str | None = None,
        judgment_reason: str | None = None,
    ) -> dict:
        header = self._full_round_header(
            cmd, state, round_name, ledger, candidate_sha, judgment_reason
        )
        run = _FullRun(
            *self._run_full_layers(
                cmd, round_name, header.sections, header.inventory
            )
        )
        evidence = self._full_round_evidence(header, run)
        return self._finalize_full_round(header, run, evidence)

    def _reconcile_full_failure_wal(self, cmd, state, full_event, ledger) -> None:
        outcomes = self._read_runtime_blob(full_event.payload.get("outcomes_ref"))
        if not isinstance(outcomes, list):
            raise LedgerCorruptionError("failed full.executed lacks replayable outcomes WAL")
        failures = []
        evidence_by_node = {}
        for outcome in outcomes:
            if not isinstance(outcome, dict) or not outcome.get("node"):
                raise LedgerCorruptionError("FULL outcomes WAL is malformed")
            if outcome.get("status") not in ("failed", "error"):
                continue
            if not outcome.get("evidence_id"):
                raise LedgerCorruptionError("FULL failure WAL lacks evidence identity")
            failures.append(
                {
                    **outcome,
                    "failure_signature": self._full_failure_signature(outcome),
                }
            )
            evidence_by_node[str(outcome["node"])] = str(outcome["evidence_id"])
        # FR-0286 §5: a round emitted while some nodes were already waived
        # records failed_nodes excluding exactly its own waived_nodes set
        # (replay-time resolution may have advanced since). Legacy events
        # carry neither key: failed_nodes is then the full failure set.
        if not self._wal_failed_nodes_match(full_event.payload, failures):
            raise LedgerCorruptionError("FULL outcomes WAL disagrees with failed_nodes")
        selection = next(
            (
                ev
                for ev in reversed(list(self.store.events(self.run_id)))
                if ev.type == "test.selected"
                and ev.seq < full_event.seq
                and ev.payload.get("scope") == "full"
            ),
            None,
        )
        if selection is None:
            raise LedgerCorruptionError("failed FULL lacks its preceding selection WAL")
        self._record_full_failures(
            cmd,
            state,
            {
                "selection_id": selection.payload.get("selection_id"),
                "failures": failures,
                "evidence_by_node": evidence_by_node,
            },
            ledger,
        )

    @staticmethod
    def _full_failure_signature(failure: dict) -> str:
        return hashlib.sha256(
            json.dumps(
                {
                    "node": failure["node"],
                    "status": failure["status"],
                    "detail": failure["detail"],
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _wal_failed_nodes_match(event_payload: dict, failures: list[dict]) -> bool:
        """WAL failures agree with the event's recorded failed_nodes.

        New-style events (``waived_nodes`` present) record failed_nodes with
        the waived set already excluded; legacy events record the full set
        (FR-0286 §5)."""
        recorded = sorted(event_payload.get("failed_nodes") or [])
        if "waived_nodes" in event_payload:
            waived = {str(node) for node in (event_payload.get("waived_nodes") or [])}
            return sorted(
                item["node"] for item in failures if item["node"] not in waived
            ) == recorded
        return sorted(item["node"] for item in failures) == recorded

    def _record_full_failures(self, cmd, state, full: dict, ledger) -> None:
        identities = {tuple(json.loads(key)): value for key, value in ledger.items()}
        # FR-0286 §5: a failure whose node is waived by a registered known
        # issue is already dispositioned -- it opens no ledger identity and
        # never re-enters the per-item diagnosis loop. Covers both fresh
        # rounds (failures already filtered) and WAL reconciliation.
        waived = self._waived_nodes(
            [failure["node"] for failure in full["failures"]]
        )
        for failure in full["failures"]:
            if failure["node"] in waived:
                continue
            signature = failure.get("failure_signature") or self._full_failure_signature(failure)
            identity = (failure["node"], signature)
            prior = identities.get(identity)
            if prior == "PROVEN":
                self._emit(
                    "ledger.transitioned",
                    {
                        "node": failure["node"],
                        "failure_signature": signature,
                        "from": "PROVEN",
                        "to": "OPEN",
                        "attempt": state.current_attempt + 1,
                        "actor": "runtime",
                        "reason": "FULL reobserved a proven failure signature",
                    },
                    command_id=cmd.command_id,
                )
            elif prior is None:
                owner = self._full_failure_owner(failure["node"], state)
                self._emit(
                    "ledger.opened",
                    {
                        "node": failure["node"],
                        "failure_signature": signature,
                        "state": "OPEN",
                        "selection_id": full["selection_id"],
                        "evidence_id": full["evidence_by_node"][failure["node"]],
                        "reason": failure["detail"] or failure["status"],
                        **owner,
                    },
                    command_id=cmd.command_id,
                )

    def _full_failure_owner(self, node: str, state) -> dict:
        path = node.partition("::")[0]
        owners = [
            task
            for task in state.task_refs
            # B50 (#65): anchor ownership follows the declared acceptance
            # refs (legacy payloads fall back to their test_refs mapping).
            if any(
                str(ref).partition("::")[0] == path
                for ref in (task.get("acceptance_refs") or task.get("test_refs") or [])
            )
        ]
        if not owners and len(state.task_refs) == 1:
            owners = [state.task_refs[0]]
        if len(owners) != 1:
            return {}
        task = owners[0]
        task_id = str(task.get("task_id") or "")
        manifest = next(
            (
                dict(ev.payload.get("manifest") or {})
                for ev in reversed(list(self.store.events(self.run_id)))
                if ev.type == "task.started" and ev.payload.get("task_id") == task_id
            ),
            {},
        )
        r_sha = next(
            (
                str(ev.payload.get("r_sha") or "")
                for ev in reversed(list(self.store.events(self.run_id)))
                if ev.type == "red.checkpointed" and ev.payload.get("task_id") == task_id
            ),
            "",
        )
        return {
            "task_id": task_id,
            "task": dict(task),
            "manifest": manifest,
            "r_sha": r_sha,
        }

    def _transition_full_ledger(
        self,
        cmd,
        before: str,
        after: str,
        reason: str,
        actor: str,
    ) -> bool:
        ledger = rebuild_ledger(self.store.events(self.run_id))
        candidates = [key for key, value in ledger.items() if value == before]
        if not candidates:
            return False
        key = sorted(candidates)[0]
        node, signature = json.loads(key)
        owner = next(
            (
                {
                    field: ev.payload.get(field)
                    for field in ("task_id", "task", "manifest", "r_sha")
                    if ev.payload.get(field)
                }
                for ev in self.store.events(self.run_id)
                if ev.type == "ledger.opened"
                and ev.payload.get("node") == node
                and ev.payload.get("failure_signature") == signature
            ),
            {},
        )
        self._emit(
            "ledger.transitioned",
            {
                "node": node,
                "failure_signature": signature,
                "from": before,
                "to": after,
                "attempt": self.store.state(self.run_id).current_attempt + 1,
                "actor": actor,
                "reason": reason,
                **owner,
            },
            command_id=cmd.command_id,
        )
        return True

    @staticmethod
    def _fixed_ledger_key(ledger) -> str:
        fixed = next((key for key, value in sorted(ledger.items()) if value == "FIXED"), None)
        if fixed is None:
            raise LedgerCorruptionError("ledger proof requested without a FIXED entry")
        return fixed

    def _last_done_outcome_paths(self) -> list:
        return next(
            (
                list(ev.payload.get("changed_paths") or [])
                for ev in reversed(list(self.store.events(self.run_id)))
                if ev.type == "outcome.received"
                and ev.payload.get("status") == "done"
                and ev.payload.get("role") in ("devon", "shield")
            ),
            [],
        )

    @staticmethod
    def _diff_test_reach(inventory, path) -> set:
        if not str(path).startswith("tests/"):
            return set()
        return {candidate for candidate in inventory if candidate.partition("::")[0] == str(path)}

    def _prove_fixed_ledger_entries(self, cmd, state, ledger) -> None:
        node, signature = json.loads(self._fixed_ledger_key(ledger))
        touched = self._last_done_outcome_paths()
        inventory, collect_error = self._collect_all_declared_layers()
        if inventory is None:
            raise TestSelectError(collect_error or "SELECT_DIFF collect failed")
        selection = select_diff(
            {"node": node, "failure_signature": signature, "state": "FIXED"},
            touched,
            lambda path: self._diff_test_reach(inventory, path),
        )
        if selection.reliable:
            self._prove_fixed_via_diff(cmd, state, node, signature, selection, inventory, ledger)
            return
        self._prove_fixed_via_fallback(cmd, state, ledger)

    def _prove_fixed_via_diff(
        self, cmd, state, node, signature, selection, inventory, ledger
    ) -> None:
        proof = self._execute_diff_selection(
            cmd,
            state,
            node,
            signature,
            selection,
            inventory,
        )
        target_failed = any(
            failure["node"] == node and failure["failure_signature"] == signature
            for failure in proof["failures"]
        )
        self._emit(
            "ledger.transitioned",
            {
                "node": node,
                "failure_signature": signature,
                "from": "FIXED",
                "to": "OPEN" if target_failed else "PROVEN",
                "attempt": state.current_attempt + 1,
                "actor": "runtime",
                "reason": "SELECT_DIFF proof result",
            },
            command_id=cmd.command_id,
        )
        self._record_full_failures(cmd, state, proof, ledger)
        rebuilt = rebuild_ledger(self.store.events(self.run_id))
        if target_failed or not ledger_is_clean(rebuilt):
            self._emit(
                "verdict.failed",
                {
                    "check": "full_suite",
                    "reason": "SELECT_DIFF did not prove every ledger entry",
                    "evidence": proof["outcomes_ref"],
                    "attempt": state.current_attempt + 1,
                },
                command_id=cmd.command_id,
            )
            return
        final = self._execute_full_round(cmd, state, "FULL_F", rebuilt)
        self._record_full_failures(cmd, state, final, rebuilt)
        if not final["failed_nodes"]:
            self._pass_island_2(cmd, state)
            return
        self._emit(
            "verdict.failed",
            {
                "check": "full_suite",
                "reason": "FULL_F revealed failures after SELECT_DIFF proof",
                "evidence": final["outcomes_ref"],
                "attempt": state.current_attempt + 1,
            },
            command_id=cmd.command_id,
        )

    def _prove_fixed_via_fallback(self, cmd, state, ledger) -> None:
        full = self._execute_full_round(cmd, state, "fallback_full", ledger)
        failed_identities = {
            (failure["node"], failure["failure_signature"]) for failure in full["failures"]
        }
        for key, value in sorted(ledger.items()):
            if value != "FIXED":
                continue
            node, signature = json.loads(key)
            self._emit(
                "ledger.transitioned",
                {
                    "node": node,
                    "failure_signature": signature,
                    "from": "FIXED",
                    "to": "OPEN" if (node, signature) in failed_identities else "PROVEN",
                    "attempt": state.current_attempt + 1,
                    "actor": "runtime",
                    "reason": "fallback FULL proof result",
                },
                command_id=cmd.command_id,
            )
        self._record_full_failures(cmd, state, full, ledger)
        rebuilt = rebuild_ledger(self.store.events(self.run_id))
        if full["passed"] and ledger_is_clean(rebuilt):
            self._pass_island_2(cmd, state)
            return
        self._emit(
            "verdict.failed",
            {
                "check": "full_suite",
                "reason": "fallback FULL did not prove every ledger entry",
                "evidence": full["outcomes_ref"],
                "attempt": state.current_attempt + 1,
            },
            command_id=cmd.command_id,
        )

    def _run_diff_layers(self, cmd, nodes, sections, inventory):  # pylint: disable=too-many-locals
        """Run the SELECT_DIFF layers that own selected nodes."""
        outcomes: list[dict] = []
        commands: dict[str, list[str]] = {}
        staged: list[Path] = []
        try:
            for layer, section in sections.items():
                selected = [node for node in nodes if inventory[node] == layer]
                if not selected:
                    continue
                if section is None:
                    raise TestSelectError(f"SELECT_DIFF lacks [{layer}] contract")
                section_cwd = self.repo / section.cwd if section.cwd != "." else self.repo
                result_path = self._result_staging_path(cmd.command_id, f"select-diff-{layer}")
                argv = stage_audited_selection(
                    section, selected, result_path, section_cwd, staged,
                    f"[{layer}] SELECT_DIFF argv diverges from contract",
                )
                obs = execute_gate_command(
                    shlex.join(argv), str(section_cwd), f"select-diff-{layer}"
                )
                commands[layer] = list(obs.argv)
                mapping = require_exact_node_coverage(parse_test_result(result_path), selected)
                outcomes.extend(
                    {
                        "node": node,
                        "status": mapping[node].status,
                        "detail": mapping[node].detail or "",
                    }
                    for node in selected
                )
        finally:
            for path in staged:
                path.unlink(missing_ok=True)
        return outcomes, commands

    def _signed_failures(self, outcomes) -> list[dict]:
        return [
            {**outcome, "failure_signature": self._full_failure_signature(outcome)}
            for outcome in outcomes
            if outcome["status"] in ("failed", "error")
        ]

    def _execute_diff_selection(  # pylint: disable=too-many-locals
        self,
        cmd,
        state,
        ledger_node: str,
        failure_signature: str,
        selection,
        inventory,
    ) -> dict:
        contract = load_contract(self.repo)
        sections = {
            "unit": contract.unit,
            "integration": contract.integration,
            "e2e": contract.e2e,
        }
        baseline = self._current_baseline_digest()
        if not baseline:
            raise TestSelectError("SELECT_DIFF lacks frozen baseline identity")
        nodes = sorted(selection.nodes)
        if any(node not in inventory for node in nodes):
            raise TestSelectError("SELECT_DIFF contains a node absent from full collect")
        commit = git(self.repo, "rev-parse", "HEAD", check=False).stdout.strip()
        tree_stamp = self._dirty_tree_stamp()
        selection_id = make_selection_id(
            nodes=nodes,
            scope="select_diff",
            basis=selection.basis,
            baseline=baseline,
            commit=commit,
            tree_stamp=tree_stamp,
        )
        node_ref = self.store.write_audit_blob(
            [{"node": node, "layer": inventory[node]} for node in nodes]
        )
        if node_ref is None:
            raise TestSelectError("SELECT_DIFF nodes blob write failed")
        self._emit(
            "test.selected",
            {
                "scope": "select_diff",
                "basis": selection.basis,
                "nodes_count": len(nodes),
                "nodes": nodes,
                "nodes_blob": f".tracks/runtime/blobs/{node_ref}",
                "baseline": baseline,
                "commit": commit,
                "tree_stamp": tree_stamp,
                "selection_id": selection_id,
                "task_id": state.current_task_id,
                "task_ifs": list((state.current_task_metadata or {}).get("if_ids") or []),
                "ledger_node": ledger_node,
                "failure_signature": failure_signature,
            },
            command_id=cmd.command_id,
            task_id=state.current_task_id,
        )
        outcomes, commands = self._run_diff_layers(cmd, nodes, sections, inventory)
        failures = self._signed_failures(outcomes)
        outcomes_ref = self.store.write_audit_blob(outcomes)
        if outcomes_ref is None:
            raise TestSelectError("SELECT_DIFF outcomes blob write failed")
        identity = EvidenceIdentity(
            tree=tree_stamp,
            command=self._selection_command_identity(commands),
            env=self._gate_environment_identity(),
            selection_id=selection_id,
        )
        evidence_by_node = evidence_by_node_for(
            identity, outcomes, state.current_attempt + 1
        )
        return {
            "selection_id": selection_id,
            "failed_nodes": [failure["node"] for failure in failures],
            "failures": failures,
            "evidence_by_node": evidence_by_node,
            "outcomes_ref": f".tracks/runtime/blobs/{outcomes_ref}",
        }
