"""M-IMPL island gate chain (mixin ``MImplIslandsMixin``): ISLAND_1 and
ISLAND_2 checks, stale settlement and the fail-closed ISLAND_GATE_2
demonstration.

Extracted from :mod:`tracks.executor.m_impl_runtime` for module-size
compliance (C0302); code moved verbatim.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from tracks.executor.taskgraph import parse_tasks_json, validate_island_closure
from tracks.executor.test_select import (
    LedgerCorruptionError,
    TestResultError,
    TestSelectError,
    ledger_is_clean,
    rebuild_ledger,
    settle_stale_identities,
)
from tracks.kernel.machine import State
from tracks.project import ContractError


def _resolve_island_gate_2(version):
    """Resolve the run version's ``island_gate_2`` capability.

    ISLAND_GATE_2 dispatch seam (IF-FAILCLOSED-001, architecture 1.0.9):
    mirrors ``machine._resolve_before_mtest`` -- the lazy import keeps this
    module free of an import-time cycle with the executor composition root.
    Early versions select no callback (None); a registered extension that
    lacks the callback propagates ``CapabilityBlockedError`` fail-closed
    (interfaces 1h: no silent classic fallback).
    """
    from tracks.executor.version_extensions import resolve_capability

    return resolve_capability(version or "", "island_gate_2")


def _canonical_demo_hosts() -> tuple[str, ...]:
    """The canonical demonstration host sequence.

    The tracks host plus the demo host id declared in the packaged demo
    registry (``[quality_registry].host`` of the demo architecture asset,
    data source -- executor source stays language neutral).  Falls back to a
    tracks-only demonstration when the packaged registry is unavailable.
    """
    demo_host = _demo_registry_host()
    return ("tracks",) if not demo_host else ("tracks", demo_host)


def _demo_registry_host() -> str | None:
    """Read the demo host id from the packaged demo registry (data)."""
    asset = Path(__file__).resolve().parent.parent / "assets" / "demo_host" / "architecture.md"
    try:
        text = asset.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        table = text.index("[quality_registry]")
        table_end = text.index("[[quality_guard]", table)
    except ValueError:
        return None
    for line in text[table:table_end].splitlines():
        stripped = line.strip()
        if stripped.startswith("host"):
            value = stripped.split("=", 1)[1].strip().strip('"')
            return value or None
    return None


class MImplIslandsMixin:
    """M-IMPL island gate chain mixin."""

    def _do_check_island_1(self, cmd, state, task_id, reconcile):
        vdir = self._vdir()
        errors = self._island_taskgraph_errors(
            vdir,
            vdir / "tasks.json",
            state,
            self._requirements_baseline_dir(state, vdir),
        )
        if errors:
            self._emit(
                "verdict.failed",
                {
                    "check": "island",
                    "reason": "; ".join(errors),
                    "evidence": "\n".join(errors),
                    "attempt": state.current_attempt + 1,
                },
                command_id=cmd.command_id,
            )
            return
        self._emit("verdict.passed", {"check": "island_1"}, command_id=cmd.command_id)

    def _island_taskgraph_errors(
        self,
        vdir: Path,
        tasks_path: Path,
        state: State,
        requirements_dir: Path | None = None,
    ) -> list[str]:
        raw, error = self._read_taskgraph(tasks_path)
        if error is not None:
            return [error]  # keep the full reason including remediation hint
        tasks, error = parse_tasks_json(raw)
        if error is not None:
            return [error]
        if (
            state.taskgraph_digest
            and hashlib.sha256(raw.encode("utf-8")).hexdigest() != state.taskgraph_digest
        ):
            return ["tasks.json changed after taskgraph.committed"]
        errors = self._taskgraph_errors(
            vdir,
            tasks,
            requirements_dir,
            state.hotfix_anchor_acs if state.hotfix_issue is not None else None,
        )
        arch_path = vdir / "architecture.md"
        if not arch_path.is_file():
            errors.append("architecture.md missing")
        else:
            ok, closure = validate_island_closure(
                tasks,
                arch_path.read_text(encoding="utf-8"),
            )
            if not ok:
                errors.extend(closure)
        return errors

    def _pass_island_2(self, cmd, state, replay: bool = False) -> None:
        """Emit the island_2 verdict and dispatch the fail-closed acceptance.

        Kept as one chokepoint so every ISLAND_GATE_2 success path (clean
        FULL round, ledger-FIXED proofs, fallback proofs) demonstrates the
        same acceptance contract.
        """
        self._demonstrate_island_gate_2(state, replay)
        self._emit("verdict.passed", {"check": "island_2"}, command_id=cmd.command_id)

    def _demonstrate_island_gate_2(self, state, replay: bool = False):
        """IF-FAILCLOSED-001: run the fail-closed demonstration at the gate.

        Resolves the run version's ``island_gate_2`` capability callback
        (T-013 v0.7 extension) and invokes it lazily, dispatching
        ``demonstrate_failclosed`` (T-016) over the canonical host sequence.
        Early versions resolve no callback and skip (classic behaviour,
        FR-0264-02); a registered extension that lacks the callback
        propagates ``CapabilityBlockedError`` fail-closed (no pseudo-success).

        SHIELD_FIX (issue 100 / T-016): ``demonstrate_failclosed`` only writes
        ``failclosed.demonstrated``/``failclosed.summary`` through its injected
        ``flush`` callable -- without one it falls back to printing and the
        append-only store (observed by the acceptance anchors via
        ``event_log``) never receives the events.  Inject a store-emit flush
        so the demonstration lands on the public event outlet.
        """
        callback = _resolve_island_gate_2(state.version or "")
        if callback is None:
            return None

        def _flush(host: str, event_type: str, payload) -> None:
            payload = dict(payload)
            payload["host"] = host
            self._emit(event_type, payload)

        return callback(_canonical_demo_hosts(), replay=replay, flush=_flush)

    def _do_check_island_2(self, cmd, state, task_id, reconcile):
        if reconcile and state.island_2_passed:
            return
        from tracks.checks.reach import check_reach_file

        reach = check_reach_file(self.repo)
        if reach.status != "pass":
            self._emit(
                "verdict.failed",
                {
                    "check": "island",
                    "reason": "reach check failed: " + "; ".join((*reach.errors, *reach.islands)),
                    "evidence": str(reach),
                    "attempt": state.current_attempt + 1,
                },
                command_id=cmd.command_id,
            )
            return
        try:
            ledger = rebuild_ledger(
                [
                    {"seq": ev.seq, "type": ev.type, "payload": dict(ev.payload)}
                    for ev in self.store.events(self.run_id)
                ]
            )
            full_rows = [ev for ev in self.store.events(self.run_id) if ev.type == "full.executed"]
            if full_rows and not full_rows[-1].payload.get("passed"):
                self._reconcile_full_failure_wal(cmd, state, full_rows[-1], ledger)
                ledger = rebuild_ledger(self.store.events(self.run_id))
            if not full_rows:
                round_name = "FULL_1"
            elif not ledger:
                # A recorded FULL round with an EMPTY ledger means no round
                # ever failed (no identities opened): re-verify as FULL_F —
                # the suite re-runs green and the gate passes. An empty
                # ledger after a FAILED round is genuinely corrupt (the
                # failures left no WAL) and fails closed below — EXCEPT when
                # every recorded failure is waived by a registered known
                # issue (FR-0286 §5): those failures open no identities by
                # design and the re-verify excludes them.
                round_name = self._empty_ledger_round_name(full_rows)
            elif ledger_is_clean(ledger):
                round_name = "FULL_F"
            elif any(value == "FIXED" for value in ledger.values()):
                self._prove_fixed_ledger_entries(cmd, state, ledger)
                return
            elif any(value == "OPEN" for value in ledger.values()):
                self._settle_stale_island_2_open(cmd, state, ledger)
                return
            else:
                raise LedgerCorruptionError(
                    "FULL chain cannot execute while ledger is awaiting an external repair"
                )
            full = self._execute_full_round(cmd, state, round_name, ledger)
            self._record_full_failures(cmd, state, full, ledger)
            if full["failed_nodes"]:
                detail = "; ".join(full["failed_nodes"])
                self._emit(
                    "verdict.failed",
                    {
                        "check": "full_suite",
                        "reason": "FULL chain failures: " + detail,
                        "evidence": full["outcomes_ref"],
                        "attempt": state.current_attempt + 1,
                    },
                    command_id=cmd.command_id,
                )
                return
            self._pass_island_2(cmd, state, reconcile)
        except (ContractError, TestSelectError, TestResultError, OSError, UnicodeError) as exc:
            self._emit_gate_failure(
                cmd,
                check="contract_error",
                reason=f"FULL chain failed closed: {type(exc).__name__}: {exc}",
                evidence="FULL selection/result/ledger",
                task_id=None,
                attempt=state.current_attempt + 1,
            )

    def _empty_ledger_round_name(self, full_rows) -> str:
        """Round name for an EMPTY ledger after a recorded FULL round.

        An empty ledger after a FAILED round is corrupt unless every recorded
        failure is waived by a registered known issue (FR-0286 §5: waived
        failures open no ledger identities by design)."""
        last = full_rows[-1].payload
        if not last.get("passed"):
            failed = [str(node) for node in (last.get("failed_nodes") or [])]
            if not (failed and set(failed) <= self._waived_nodes(failed)):
                raise LedgerCorruptionError(
                    "FULL round failed but opened no ledger identities"
                )
        return "FULL_F"

    def _settle_stale_island_2_open(self, cmd, state, ledger) -> None:
        """IF-FULLCHAIN-001 stale-identity settlement closure (island_2 OPEN).

        The OPEN branch runs one real FULL round first; non-PROVEN identities
        re-verified green — ``(node, signature)`` absent from this round's
        failure set and node in this round's executed set — are settled by
        the IF-LEDGER-001 emitter (OPEN→CLASSIFIED→FIXED with the explicit
        ``stale_identity_settlement`` program reason, never STALE), then the
        existing fallback proof settles every FIXED entry PROVEN in one full
        pass.  Identities still failing this round keep the per-item
        diagnosis loop and are never swallowed; WAL inconsistency fails
        closed through ``LedgerCorruptionError``.
        """
        full = self._execute_full_round(cmd, state, "full_settlement", ledger)
        self._record_full_failures(cmd, state, full, ledger)
        ledger = rebuild_ledger(self.store.events(self.run_id))
        round_failures = [
            (failure["node"], failure["failure_signature"]) for failure in full["failures"]
        ]
        for event in settle_stale_identities(ledger, full["evidence_by_node"], round_failures):
            self._emit(
                "ledger.transitioned",
                {
                    **event["payload"],
                    "attempt": state.current_attempt + 1,
                    "actor": "runtime",
                },
                command_id=cmd.command_id,
            )
        rebuilt = rebuild_ledger(self.store.events(self.run_id))
        if full["failed_nodes"]:
            self._emit(
                "verdict.failed",
                {
                    "check": "full_suite",
                    "reason": "FULL settlement round failures: " + "; ".join(full["failed_nodes"]),
                    "evidence": full["outcomes_ref"],
                    "attempt": state.current_attempt + 1,
                },
                command_id=cmd.command_id,
            )
            return
        if any(value == "FIXED" for value in rebuilt.values()):
            self._prove_fixed_via_fallback(cmd, state, rebuilt)
            return
        if ledger_is_clean(rebuilt):
            self._pass_island_2(cmd, state)
            return
        self._emit(
            "verdict.failed",
            {
                "check": "full_suite",
                "reason": "stale settlement left the FULL ledger unclean without FIXED entries",
                "evidence": full["outcomes_ref"],
                "attempt": state.current_attempt + 1,
            },
            command_id=cmd.command_id,
        )
