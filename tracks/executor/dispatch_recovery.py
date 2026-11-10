"""Runtime-owned, content-addressed receipts for interrupted agent dispatches."""

from __future__ import annotations

import base64
import hashlib
import json
import time
from contextlib import contextmanager

from tracks import paths
from tracks.kernel.envelope import EnvelopeFormatError


def command_binding(run_id, cmd) -> str:
    raw = json.dumps([run_id, cmd.command_id, cmd.params], sort_keys=True).encode()
    return hashlib.sha256(raw).hexdigest()


class ExecDispatchRecoveryMixin:
    @contextmanager
    def _capture_dispatch_receipt(self, cmd, assignment, pre_dirty, doc_gap):
        if not callable(getattr(self.backend, "recover_act", None)):
            yield
            return
        previous = getattr(self.backend, "dispatch_receipt_sink", None)

        def save(backend_receipt):
            gap = None
            if doc_gap is not None:
                gap = {
                    "documents": {
                        name: base64.b64encode(data).decode("ascii")
                        for name, data in doc_gap[0].items()
                    },
                    "pre_dirty": doc_gap[1],
                }
            receipt = {
                "binding": command_binding(self.run_id, cmd),
                "assignment": assignment, "pre_dirty": pre_dirty,
                "doc_gap": gap, "backend": backend_receipt,
            }
            ref = self.store.write_audit_blob(receipt)
            if ref is None:
                raise OSError("could not persist dispatch audit baseline")
            self._emit(
                "advisory.recorded",
                {"area": "dispatch_recovery", "receipt_ref": ref},
                command_id=cmd.command_id,
            )

        self.backend.dispatch_receipt_sink = save
        try:
            yield
        finally:
            self.backend.dispatch_receipt_sink = previous

    def _recover_completed_dispatch(self, cmd, state, task_id, role, substate, doc) -> bool:
        recover = getattr(self.backend, "recover_act", None)
        if not callable(recover):
            return False
        events = [
            e for e in self.store.events(self.run_id)
            if e.type == "advisory.recorded" and e.command_id == cmd.command_id
            and e.payload.get("area") == "dispatch_recovery"
        ]
        if not events:
            return False  # legacy command: no fabricated historical baseline
        try:
            receipt = self._load_dispatch_receipt(cmd, events[-1].payload["receipt_ref"])
            result = recover(receipt["backend"])
        except (OSError, KeyError, TypeError, ValueError, EnvelopeFormatError) as exc:
            self._emit_stale_assignment(cmd, task_id, role, str(exc))
            return True
        if result is None:
            return False
        if result.get("recovery_pending"):
            return True
        self._finish_recovered_dispatch(cmd, state, task_id, role, substate, doc, receipt, result)
        return True

    def _load_dispatch_receipt(self, cmd, ref):
        raw = (paths.blobs_dir(self.store.home) / ref).read_bytes()
        if hashlib.sha256(raw).hexdigest() != ref:
            raise ValueError("dispatch receipt content digest mismatch")
        receipt = json.loads(raw)
        if receipt["binding"] != command_binding(self.run_id, cmd):
            raise ValueError("dispatch receipt belongs to another command")
        return receipt

    def _finish_recovered_dispatch(
        self, cmd, state, task_id, role, substate, doc, receipt, result
    ):
        from tracks.executor.dispatch import _AgentDispatchCtx

        gap = receipt["doc_gap"]
        if gap is not None:
            gap = (
                {p: base64.b64decode(data, validate=True) for p, data in gap["documents"].items()},
                gap["pre_dirty"],
            )
        self._finalize_agent_outcome(
            _AgentDispatchCtx(
                cmd, state, task_id, role, substate, doc, cmd.params,
                receipt["assignment"], receipt["pre_dirty"], gap, time.monotonic(),
            ),
            result,
        )
