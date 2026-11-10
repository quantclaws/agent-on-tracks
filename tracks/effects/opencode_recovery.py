"""Recover file-delivered Shield repairs from a runtime-bound audit receipt.

Receipts are stored by the executor before spawning. A file alone is never
enough: recovery needs the original audit baseline, candidate and process
identity. Unknown or changed work is preserved and rejected, not rolled back.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import os
import subprocess
from pathlib import Path

from tracks.effects.audit import Auditor
from tracks.effects.envelope_reply import declared_result_path
from tracks.kernel.envelope import load_declared_reply


def process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class OpencodeRecoveryMixin:
    """First recovery scope: main-tree M-IMPL Shield WRITE repairs."""

    def _save_dispatch_audit(self, req, audit, parity) -> None:
        self._active_dispatch_receipt = None
        sink = getattr(self, "dispatch_receipt_sink", None)
        assignment = req.assignment or {}
        result_path = declared_result_path(assignment)
        if not (
            callable(sink)
            and req.role == "shield"
            and req.substate == "WRITE"
            and assignment.get("phase") == "shield_fix"
            and req.root == self.repo
            and result_path
            and not Path(result_path).exists()
        ):
            return
        auditor = audit.auditor
        self._active_dispatch_receipt = {
            "version": 1,
            "root": str(req.root),
            "head": self._recovery_head(),
            "assignment": assignment,
            "prompt": req.prompt,
            "doc_paths": [str(p) for p in req.doc_paths],
            "parity": parity,
            "cleanup": [
                {
                    "dest": str(info["dest"]), "existed": info["existed"],
                    "backup": (
                        base64.b64encode(info["backup"]).decode("ascii")
                        if info.get("backup") is not None else None
                    ),
                }
                for info in req.cleanup_infos if info is not None
            ],
            "allowed": sorted(auditor.allowed),
            "forbidden": auditor.forbidden,
            "baseline": sorted(audit.baseline),
            "baseline_types": auditor._baseline_types,
            "baseline_symlinks": auditor._baseline_symlinks,
            "baseline_content": {
                p: base64.b64encode(data).decode("ascii")
                for p, data in auditor._baseline_content.items()
            },
        }
        sink(copy.deepcopy(self._active_dispatch_receipt))

    def _record_dispatch_pid(self, pid: int) -> None:
        receipt = getattr(self, "_active_dispatch_receipt", None)
        if receipt is not None:
            receipt["pid"] = pid
            self.dispatch_receipt_sink(copy.deepcopy(receipt))

    def _recovery_head(self) -> str:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.repo,
            capture_output=True, text=True, check=True,
        ).stdout.strip()

    def recover_act(self, receipt: dict) -> dict | None:
        """Return audited delivery, a running marker, or None without delivery.

        No subprocess exit or stdout is invented. The executor consumes the
        original file via its existing envelope parser and normal checkpoint.
        """
        pid = self._validate_recovery_identity(receipt)
        if process_alive(pid):
            return {"recovery_pending": True}
        if receipt["head"] != self._recovery_head():
            raise ValueError("candidate changed since dispatch; retained result needs revalidation")
        assignment = receipt["assignment"]
        result_path = declared_result_path(assignment)
        auditor = self._restore_dispatch_auditor(receipt)
        changed = auditor.agent_changed_paths(set(receipt["baseline"]))
        consumed = {
            str(Path(entry["path"]).relative_to(self.repo))
            for entry in (receipt.get("parity") or {}).get("manifest", [])
        }
        changed -= consumed
        if any(not auditor._is_allowed(p) for p in changed) or self._check_doc_deltas(
            auditor, [Path(p) for p in receipt["doc_paths"]], changed
        ):
            raise ValueError("interrupted dispatch write scope mismatch; working tree preserved")
        cleanup = self._recovery_cleanup(receipt)
        if not result_path or not Path(result_path).is_file():
            for info in cleanup:
                self._cleanup(info)

            return None
        envelope = load_declared_reply(None, result_path)
        if envelope["envelope"]["kind"] != "shield:write":
            raise ValueError("retained delivery is not a Shield WRITE result")
        declared = {entry["path"] for entry in envelope["payload"]["artifact_manifest"]["include"]}
        if changed != declared:
            raise ValueError("retained manifest/write scope mismatch; working tree preserved")
        try:
            return self._audit_recovered_shield(receipt, auditor, envelope, result_path)
        finally:
            for info in cleanup:
                self._cleanup(info)

    def _validate_recovery_identity(self, receipt: dict) -> int:
        if receipt.get("version") != 1 or Path(receipt["root"]) != self.repo:
            raise ValueError("dispatch receipt root/version mismatch")
        pid = receipt.get("pid")
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
            raise ValueError("dispatch process identity was not durably recorded")
        return pid

    def _recovery_cleanup(self, receipt):
        prepared = {entry["path"]: entry["sha256"] for entry in receipt["parity"]["manifest"]}
        cleanup = []
        for saved in receipt["cleanup"]:
            info = dict(saved)
            info["dest"] = Path(info["dest"])
            backup = info["backup"]
            info["backup"] = base64.b64decode(backup, validate=True) if backup is not None else None
            path = info["dest"]
            allowed = {prepared[str(path)]}
            if info["backup"] is not None:
                allowed.add(hashlib.sha256(info["backup"]).hexdigest())
            if path.is_symlink() or (
                path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() not in allowed
            ):
                raise ValueError("materialized artifact changed after dispatch; preserved")
            if not path.exists() and info["existed"]:
                raise ValueError("pre-existing materialized artifact is missing; preserved")
            cleanup.append(info)
        return cleanup

    def _restore_dispatch_auditor(self, receipt: dict) -> Auditor:
        auditor = Auditor(
            self.repo, [self.repo / p for p in receipt["allowed"]], receipt["forbidden"]
        )
        auditor._baseline_types = receipt["baseline_types"]
        auditor._baseline_symlinks = receipt["baseline_symlinks"]
        auditor._baseline_content = {
            p: base64.b64decode(data, validate=True)
            for p, data in receipt["baseline_content"].items()
        }
        return auditor

    def _audit_recovered_shield(self, receipt, auditor, envelope, result_path) -> dict:
        from .opencode_act import _ActAudit

        # A delivered file proves delivery, not an observed subprocess exit.
        proc = subprocess.CompletedProcess([], None, stdout="", stderr="")
        audit = _ActAudit(auditor, set(receipt["baseline"]), None, False)
        result = self._audited_result(
            audit, proc, "shield", "WRITE", [Path(p) for p in receipt["doc_paths"]],
            receipt["prompt"], None, False, result_path=result_path,
        )
        payload = envelope["payload"]
        result = self._attach_dispatch_metadata(
            result, "shield", "WRITE",
            (payload["artifact_manifest"], payload["suggested_commit_message"]),
            receipt["assignment"], parity=receipt["parity"],
        )
        result["agent_io"]["delivery"] = {
            "path": result_path,
            "sha256": hashlib.sha256(Path(result_path).read_bytes()).hexdigest(),
            "recovered": True,
            "process_exit_observed": False,
        }
        return result
