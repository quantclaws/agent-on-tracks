"""Declared, restartable host environment preparation before M-TEST."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from tracks.executor.host_contract import (
    CANONICAL_CONTRACT_RELPATH,
    LocalGateDecl,
    execute_gate,
    load_host_contract,
    validate_host_contract,
)


@dataclass(frozen=True)
class _Preparation:
    identity: dict
    steps: tuple[str, ...]
    timeout: int
    scope: dict


class ExecEnvironmentMixin:
    """Run only the design's explicit bootstrap/install commands, with WAL receipts."""

    def _environment_block(self, cmd, reason, detail, **evidence):
        self._emit(
            "attention.required",
            {
                "area": "host_environment", "stage": "M-TEST",
                "reason": reason, "detail": detail, **evidence,
                "next": (
                    "repair the declared setup, then retry; "
                    "return to M-DESIGN for contract changes"
                ),
            },
            command_id=cmd.command_id,
        )

    def _environment_inputs(self, state) -> _Preparation:
        path = self.repo.joinpath(*CANONICAL_CONTRACT_RELPATH)
        raw = path.read_bytes()
        contract = load_host_contract(path)
        errors = validate_host_contract(contract, self.repo)
        if errors:
            raise ValueError("; ".join(errors))
        if not isinstance(contract.install, str) or not contract.install.strip():
            raise ValueError("host-contract: install must be a nonempty command")
        scope = self._phase_scope(contract, state)
        if scope is None:
            raise ValueError("cannot resolve the declared setup's version/prefix facts")
        head = self._repo_head_sha()
        if not head:
            raise ValueError("host environment preparation requires a Git HEAD")
        return _Preparation(
            identity={"contract_digest": hashlib.sha256(raw).hexdigest(), "head_sha": head},
            steps=(*contract.bootstrap, contract.install),
            timeout=contract.build_timeout_seconds,
            scope=scope,
        )

    def _environment_receipts(self, cmd, prep) -> set[int] | None:
        """Reuse only receipts of this pending command and these exact inputs."""
        events = [
            event for event in self.store.events(self.run_id)
            if event.command_id == cmd.command_id
            and event.type in ("environment.preparation_started", "environment.step_completed")
        ]
        if any(
            any(event.payload.get(key) != value for key, value in prep.identity.items())
            for event in events
        ):
            self._environment_block(
                cmd, "preparation_inputs_changed",
                "contract or Git HEAD changed during interrupted setup; retry with the new inputs",
            )
            return None
        if not events:
            self._emit("environment.preparation_started", prep.identity, command_id=cmd.command_id)
        return {
            event.payload["step"] for event in events
            if event.type == "environment.step_completed"
            and event.payload.get("status") == "passed"
        }

    def _prepare_environment_step(self, cmd, prep, step) -> bool:
        command = prep.steps[step]
        decl = LocalGateDecl(
            kind="build", source="command", command=command, categories=(),
            result_channel="exit_code", timeout_seconds=prep.timeout,
        )
        result = execute_gate(decl, self.repo, prep.scope)
        blob = self.store.write_audit_blob(result.summary)
        evidence = {
            **prep.identity, "step": step, "command": list(result.command_echo),
            "status": result.status, "exit_code": result.exit_code, "evidence_blob": blob,
        }
        if result.status != "passed":
            self._environment_block(
                cmd, "environment_setup_failed",
                str(
                    result.summary.get("error") or result.summary.get("stderr")
                    or "declared setup failed"
                ),
                **evidence,
            )
            return False
        self._emit("environment.step_completed", evidence, command_id=cmd.command_id)
        return True

    def _do_prepare_host_environment(self, cmd, state, task_id, reconcile):
        """Prepare tools before baseline collection; interrupted steps must be restart-safe."""
        if state.host_environment_prepared:
            return
        try:
            prep = self._environment_inputs(state)
            completed = self._environment_receipts(cmd, prep)
            if completed is None:
                return
            for step in range(len(prep.steps)):
                if step not in completed and not self._prepare_environment_step(cmd, prep, step):
                    return
            if self._environment_inputs(state).identity != prep.identity:
                self._environment_block(
                    cmd, "preparation_inputs_changed",
                    "contract or Git HEAD changed while preparing the environment; retry",
                )
                return
        except (OSError, ValueError, TypeError) as err:
            self._environment_block(cmd, "invalid_environment_contract", str(err))
            return
        self._emit(
            "environment.prepared",
            {**prep.identity, "steps": len(prep.steps)},
            command_id=cmd.command_id,
        )
