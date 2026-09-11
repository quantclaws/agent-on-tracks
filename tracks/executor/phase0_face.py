"""v0.7 Phase 0 pre-gate handlers extracted from the Executor
(mixin ``ExecPhase0Mixin``)."""

from __future__ import annotations

import hashlib
from pathlib import Path

from tracks import paths
from tracks.executor.test_collect import _R2_BASIS, _R2_SCOPE
from tracks.executor.test_select import collect_node_source_digests, make_selection_id
from tracks.kernel.events import Command
from tracks.project import load_contract

# Baseline requirement documents digested into the Phase 0 seal manifest
# (§1c phase0.sealed document_digests input): the six project templates.
_PHASE0_BASELINE_DOCS = (
    "story.md",
    "spec.md",
    "acceptance.md",
    "architecture.md",
    "interfaces.md",
    "test-plan.md",
)


def _phase0_baseline_version(version: str | None) -> str | None:
    """Previous minor of a ``vX.Y`` run version (v0.7 -> v0.6, §1c
    phase0_validate inputs); None when the shape carries no baseline."""
    body = (version or "").removeprefix("v").split(".")
    if len(body) != 2 or not all(part.isdigit() for part in body):
        return None
    major, minor = int(body[0]), int(body[1])
    if minor <= 0:
        return None
    return f"v{major}.{minor - 1}"


def _phase0_planned_bindings(baseline_dir: Path) -> dict[str, str]:
    """AC -> planned node bindings from the baseline test-plan §8 rows (§1d
    scan input); only node-level ``path::test`` targets bind (file-only cells
    carry no node identity)."""
    plan = baseline_dir / "test-plan.md"
    if not plan.is_file():
        return {}
    from tracks.executor.test_tasks import _coverage_rows_with_test

    return {
        ac: test.strip()
        for ac, _layer, test, _if_ids in _coverage_rows_with_test(
            plan.read_text(encoding="utf-8")
        )
        if test and "::" in test
    }


def _phase0_approved_acs(baseline_dir: Path) -> set[str]:
    """Approved AC ids from the baseline acceptance.md (§1d scan input)."""
    acc = baseline_dir / "acceptance.md"
    if not acc.is_file():
        return set()
    from tracks.executor.test_tasks import _known_ac_ids

    return _known_ac_ids(acc.read_text(encoding="utf-8"))


def _phase0_document_digests(baseline_dir: Path) -> dict[str, str]:
    """sha256 of every present baseline requirement document (seal input)."""
    digests: dict[str, str] = {}
    for name in _PHASE0_BASELINE_DOCS:
        path = baseline_dir / name
        if path.is_file():
            digests[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return digests


def _phase0_frozen_test_digests(repo: Path, frozen_paths: list[str]) -> dict[str, str]:
    """sha256 of every frozen test file under the declared layer paths."""
    digests: dict[str, str] = {}
    for declared in frozen_paths:
        root = repo / declared.rstrip("/")
        if not root.is_dir():
            continue
        for file in sorted(root.rglob("*")):
            if file.is_file():
                digests[str(file.relative_to(repo))] = hashlib.sha256(
                    file.read_bytes()
                ).hexdigest()
    return digests


class ExecPhase0Mixin:
    """Phase 0 validation/seal command handlers plus the baseline scan helpers."""

    def _emit_phase0_blocked(self, cmd, reason: str, detail: str, recoverable: bool):
        """Emit ``phase0.blocked`` (§1c-5): the run parks for Human repair."""
        return self._emit(
            "phase0.blocked",
            {"reason": reason, "detail": detail, "recoverable": recoverable},
            command_id=cmd.command_id,
        )

    def _phase0_guard_violations(self) -> tuple[list[str], list[str], bool]:
        """Guard hardening input (§1d; registry surface IF-GUARD-001/002).

        Returns ``(violations, guard_refs, registry_present)``. A PRESENT but
        unloadable §4.2 registry yields violations (fail closed -- the run
        parks at phase0.blocked rather than proceeding under a substituted
        registry, interfaces §1c/§1e fail-closed evidence authenticity). An
        ABSENT §4.2 registry means no guard/parity evidence exists on this
        host: the Phase 0 pre-gate must park (``registry_present=False``)
        rather than fabricate a passed hardening or a seal.

        No packaged-asset substitution is allowed to mask an unloadable host
        registry into a pass (Prism R9-01): the host seed must itself carry a
        valid §4.2 block ("demo asset 同源"); a seed that does not is a
        test/seed defect (SHIELD_FIX), never a silent implementation pass.
        """
        arch = paths.projects_dir(self.store.home) / self.version / "architecture.md"
        if not arch.is_file():
            return [], [], False
        from tracks.executor.guard_registry import load_guard_registry

        try:
            load_guard_registry(arch)
        except (ValueError, OSError, UnicodeError) as exc:
            return [
                f"architecture.md §4.2 registry block failed to load: {exc}"
            ], [str(arch)], True
        return [], [str(arch)], True

    def _repair_phase0_gap(
        self, cmd, gap, baseline_version: str, node_digests: dict
    ) -> None:
        """Bind a marker-only baseline gap to its real collected node through a
        genuine adapter-selected execution (interfaces §1c phase0.baseline_
        repaired; §1e/§1h adapter protocol).

        Emits ``phase0.baseline_repaired`` carrying the version-qualified
        ``ac``, the bound node (node_id + digest), and a selection identity
        derived deterministically from the ACTUALLY executed selection
        (``make_selection_id`` over the adapter-run nodes with the canonical
        R2 scope/basis) plus the command echo of the executed argv -- so the
        evidence cannot be forged as canned identity strings."""
        contract = load_contract(self.repo)
        section = getattr(contract, "integration", None) or contract.unit
        from tracks.adapters.base import UnknownAdapterError, resolve_adapter

        declared = contract.adapter
        if declared is None:
            raise UnknownAdapterError(
                "host project contract declares no [adapter] section "
                "(IF-ADAPTER-001); the executor consumes only the host "
                "declaration and never a built-in reference adapter"
            )
        adapter = resolve_adapter(
            declared.id, declared.protocol, declared.version
        )
        cwd = (
            Path(self.repo)
            if section.cwd == "."
            else Path(self.repo) / section.cwd
        )
        result_path = self._result_staging_path(cmd.command_id or "", "phase0-repair")
        try:
            argv = list(
                adapter.run_selected(
                    section.run_selected,
                    [gap.planned_node_id],
                    str(result_path),
                    cwd,
                )
            )
        finally:
            result_path.unlink(missing_ok=True)
        nodes = [gap.planned_node_id]
        selection_id = make_selection_id(
            nodes=nodes,
            scope=_R2_SCOPE,
            basis=_R2_BASIS,
            baseline=baseline_version,
            commit="",
            tree_stamp="",
        )
        evidence_id = hashlib.sha256(
            f"{gap.planned_node_id}@{baseline_version}".encode()
        ).hexdigest()
        self._emit(
            "phase0.baseline_repaired",
            {
                "ac": f"{gap.ac}@{baseline_version}",
                "bound_node": {
                    "node_id": gap.planned_node_id,
                    "digest": node_digests.get(gap.planned_node_id, ""),
                },
                "selection_id": selection_id,
                "evidence_id": evidence_id,
                "command_echo": argv,
                "status": "repaired",
            },
            command_id=cmd.command_id,
        )

    def _phase0_blocked_resume(self, state) -> bool:
        """SM-01.3 resume preflight (IF-FAILCLOSED-001 / interfaces §1c).

        When a drive starts with ``phase0_status == "BLOCKED"`` the kernel
        parks by design (``decide_phase0`` returns no commands at BLOCKED), so
        only the executor effects layer can re-issue the validation once Human
        repairs the repo facts. Appends one fresh ``phase0_validate`` command
        (command.issued WAL) and executes it, returning True when a resume was
        kicked; a no-op (False) for any other status. The run-loop entry calls
        this at most once per drive -- no tick-level hot loop -- and the
        re-validation runs the FULL hard checks: a still-broken host keeps
        parking at phase0.blocked rather than receiving a watered-down SEALED
        pass (SM-01.3, §1c fail-closed).
        """
        if getattr(state, "phase0_status", None) != "BLOCKED":
            return False
        self.issue(Command(kind="phase0_validate"))
        return True

    def _do_phase0_validate(self, cmd, state, task_id, reconcile):
        """Runtime Phase 0 pre-gate for v0.7 hosts (interfaces §1c/§1d).

        Emits the append-only ``phase0.*`` sequence over the delivered domain
        functions: gap scan over the baseline's real collected-node inventory
        (unrecoverable identities BLOCK, §1d), the collected-coverage gate,
        guard hardening, then the seal. Every event goes through the store
        (append-only, AC-NFR0140-01): dropping the projection and replaying
        rebuilds the identical phase0_status/seal/blocked fields."""
        if reconcile and state.phase0_status in ("SEALED", "BLOCKED"):
            return
        from tracks.executor.phase0 import judge_real_coverage, scan_trace_gaps
        from tracks.executor.test_select import TestSelectError

        baseline_version = _phase0_baseline_version(self.version)
        if baseline_version is None:
            self._emit_phase0_blocked(
                cmd,
                "baseline_unresolvable",
                f"cannot derive a baseline version from {self.version!r}",
                True,
            )
            return
        node_layer, collect_error = self._collect_all_declared_layers(
            capture_absent_ok=True
        )
        if collect_error is not None:
            self._emit_phase0_blocked(cmd, "collect_failed", collect_error, True)
            return
        nodes = sorted(node_layer)
        try:
            node_digests = collect_node_source_digests(Path(self.repo), nodes)
        except TestSelectError as exc:
            self._emit_phase0_blocked(cmd, "identity_unrecoverable", str(exc), True)
            return
        baseline_dir = paths.projects_dir(self.store.home) / baseline_version
        planned = _phase0_planned_bindings(baseline_dir)
        approved = _phase0_approved_acs(baseline_dir)
        gaps = scan_trace_gaps(baseline_version, approved, planned, node_digests, {})
        fatal = [gap for gap in gaps if gap.reason != "marker_only"]
        if fatal:
            self._emit_phase0_blocked(
                cmd,
                fatal[0].reason,
                "; ".join(f"{gap.ac}: {gap.planned_node_id}" for gap in fatal),
                False,
            )
            return
        # Marker-only gaps (planned node collected with a real digest but no
        # persisted evidence) are REPAIRED into real collected-node evidence
        # before the run may continue; a repair-less seal is illegal (§1d).
        for gap in gaps:
            if gap.reason == "marker_only" and gap.planned_node_id in node_digests:
                self._repair_phase0_gap(cmd, gap, baseline_version, node_digests)
        planned_nodes = set(planned.values())
        ratio = (
            len([node for node in planned_nodes if node_digests.get(node)])
            / len(planned_nodes)
            if planned_nodes
            else 1.0
        )
        judgement = judge_real_coverage(ratio, 1.0, ())
        self._phase0_emit_coverage(
            cmd, judgement, ratio, node_layer, node_digests, nodes
        )
        if not judgement.passed:
            self._emit_phase0_blocked(
                cmd, "coverage_below_threshold", judgement.reason or "", True
            )
            return
        if self._phase0_guard_harden(cmd, baseline_version, baseline_dir):
            return
        self._emit_phase0_sealed(cmd, baseline_version, baseline_dir)

    def _phase0_emit_coverage(
        self, cmd, judgement, ratio: float, node_layer, node_digests, nodes
    ) -> None:
        """Emit the ``phase0.coverage`` gate event (§1d, by=collected)."""
        outcomes_blob = self.store.write_audit_blob(
            {
                "nodes": [
                    {"node": node, "layer": node_layer[node], "digest": node_digests[node]}
                    for node in nodes
                ]
            }
        )
        self._emit(
            "phase0.coverage",
            {
                "status": "passed" if judgement.passed else "failed",
                "ratio": ratio,
                "threshold": judgement.threshold,
                "by": "collected",
                "exclude": "none",
                "excluded_sources": list(judgement.excluded_sources),
                "outcomes_ref": f".tracks/runtime/blobs/{outcomes_blob}"
                if outcomes_blob
                else "",
            },
            command_id=cmd.command_id,
        )

    def _phase0_guard_harden(
        self, cmd, baseline_version: str, baseline_dir: Path
    ) -> bool:
        """Guard-hardening gate (§1d): park when no registry/parity evidence
        exists or the registry is invalid; otherwise harden. Returns True when
        the run was parked (blocked) and must stop."""
        violations, guard_refs, registry_present = self._phase0_guard_violations()
        if not registry_present:
            self._emit_phase0_blocked(
                cmd,
                "guard_registry_invalid",
                "no §4.2 guard registry/parity evidence on this host",
                True,
            )
            return True
        self._emit(
            "phase0.guard_hardened",
            {
                "status": "passed" if not violations else "blocked",
                "violations": len(violations),
                "revised": 0,
                "guard_evidence_refs": guard_refs,
                "parity_event_seq": 0,
            },
            command_id=cmd.command_id,
        )
        if violations:
            self._emit_phase0_blocked(
                cmd, "guard_registry_invalid", "; ".join(violations), False
            )
            return True
        return False

    def _emit_phase0_sealed(self, cmd, baseline_version: str, baseline_dir: Path) -> None:
        """Emit the terminal ``phase0.sealed`` event (interfaces §1c).

        Builds the seal manifest from the baseline documents and the frozen
        test digests via the T-005 facade, persists it (plus the frozen-test
        digest map) to audit blobs, and references both from the emitted
        event. The ``seal_id`` is the stable content digest of the remaining
        manifest fields (IF-PHASE-003)."""
        from tracks.executor.phase0 import build_seal_manifest

        contract_toml = paths.project_toml_path(paths.tracks_home(Path(self.repo)))
        env_digest = hashlib.sha256(contract_toml.read_bytes()).hexdigest()
        manifest = build_seal_manifest(
            baseline_version,
            _phase0_document_digests(baseline_dir),
            _phase0_frozen_test_digests(Path(self.repo), self._frozen_test_paths()),
            (),
            env_digest,
        )
        seal_blob = self.store.write_audit_blob(
            {
                "baseline_version": manifest.baseline_version,
                "document_digests": dict(manifest.document_digests),
                "frozen_test_digests": dict(manifest.frozen_test_digests),
                "marks": list(manifest.marks),
                "environment_contract_digest": manifest.environment_contract_digest,
                "seal_id": manifest.seal_id,
            }
        )
        frozen_blob = self.store.write_audit_blob(dict(manifest.frozen_test_digests))
        self._emit(
            "phase0.sealed",
            {
                "baseline_version": baseline_version,
                "seal_id": manifest.seal_id,
                "seal_manifest_blob": f".tracks/runtime/blobs/{seal_blob}"
                if seal_blob
                else "",
                "marks": "registered",
                "env_contract": "pass",
                "frozen_tests_blob": f".tracks/runtime/blobs/{frozen_blob}"
                if frozen_blob
                else "",
            },
            command_id=cmd.command_id,
        )
