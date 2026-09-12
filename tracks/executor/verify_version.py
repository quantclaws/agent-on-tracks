"""M-VERIFY version-declaration gate extracted from the Executor (mixin
``ExecVerifyVersionMixin``)."""

from __future__ import annotations

from dataclasses import dataclass

from tracks.executor.helpers import git
from tracks.executor.host_contract import NormalizedGateResult
from tracks.executor.local_gate_evidence import gate_identity, normalized_result_payload
from tracks.executor.release_gate import (
    active_release_branch,
    build_operation_plan,
    operation_plan_needs_n,
    remote_patch_n,
)
from tracks.executor.release_preview import _contract_table, _journey, _version_facts

# IF-JOURNEY-001 §1m: the version_scheme label that derives each journey's
# tag/release target (architecture §1.0.3/§1.0.8). A tag/release step target
# must be exactly this template rendered with the run's version facts.
_JOURNEY_VERSION_TEMPLATE = {
    "feature": "feature_tag",
    "post_release": "patch_line",
    "dev": "prerelease_tag",
}


_VERSION_STEP_KINDS = ("tag", "release")


@dataclass(frozen=True)
class _VersionGateCtx:
    """Stable inputs of one version_decl gate evaluation."""

    cmd: object
    candidate_sha: object
    contract_digest: object
    gate: object
    identity: object
    table: dict
    scheme: dict
    journey: str | None
    base_facts: dict


class ExecVerifyVersionMixin:
    """version_decl gate and its verdict/host-contract-failure emission."""

    def _execute_version_decl_gate(
        self, cmd, candidate_sha, contract_digest, contract, state, gate, ordinal
    ) -> bool:
        """Native ``source=version_decl`` local gate (FR-0269, NFR-0147).

        The gate has no executable command (tracks/reference contracts declare
        none): the Runtime derives this run's operation plan from the declared
        ``[host-contract.version_scheme]`` templates + version facts and
        requires every ``tag:``/``release:`` step target to be exactly the
        journey's template rendering — a literal or cross-template target is
        ``version_mismatch``, never silently published. Each derived tag is
        then probed with ``git ls-remote``: a present tag is
        ``tag_already_exists`` and a configured-but-unreachable remote is
        ``remote_unavailable`` (fail closed, never a guessed pass). A
        remote-less local demo records ``attention.required`` and passes with
        an explicit ``remote_check=skipped_no_remote`` skip. The ``{n}``
        census input is recorded on the payload. The verdict event
        (``local_gate.passed``/``local_gate.failed``) is bound to the
        candidate like every other declared gate.
        """
        table = _contract_table(contract)
        ctx = _VersionGateCtx(
            cmd=cmd,
            candidate_sha=candidate_sha,
            contract_digest=contract_digest,
            gate=gate,
            identity=gate_identity(gate.kind, ordinal),
            table=table,
            scheme=table.get("version_scheme") or {},
            journey=_journey(contract, self._release_journey(state, cmd)),
            base_facts=self._release_version_facts(state),
        )
        census = self._version_decl_census(ctx)
        if census["error"] is not None:
            return self._emit_version_decl_verdict(
                ctx,
                derived_tags=[],
                census=census,
                remote_check={"status": "unavailable", "tags": []},
                reason="remote_unavailable",
                detail=f"remote patch-tag census failed: {census['error']}",
                command_echo=["version_decl", ctx.journey or "feature"],
            )
        derived, mismatches = self._version_decl_steps(ctx, contract, census)
        command_echo = ["version_decl", ctx.journey or "feature", *derived]
        if mismatches:
            return self._emit_version_decl_verdict(
                ctx,
                derived_tags=derived,
                census=census,
                remote_check={"status": "not_checked", "tags": derived},
                reason="version_mismatch",
                detail=(
                    "tag/release targets are not derived from the journey's "
                    "version_scheme template"
                ),
                command_echo=command_echo,
                extra={"mismatches": mismatches},
            )
        if not derived:
            return self._emit_version_decl_verdict(
                ctx,
                derived_tags=[],
                census=census,
                remote_check={"status": "not_applicable", "tags": []},
                reason=None,
                detail="",
                command_echo=command_echo,
            )
        return self._version_decl_remote_probe(ctx, derived, census, command_echo)

    def _version_decl_census(self, ctx: _VersionGateCtx) -> dict:
        """The ``{n}`` census input: requested iff the journey plan needs it."""
        needs_n = bool(ctx.journey) and operation_plan_needs_n(ctx.table, ctx.journey)
        census = {"requested": needs_n, "n": None, "error": None}
        if needs_n and "n" not in ctx.base_facts:
            n, error = remote_patch_n(
                self.repo, str(ctx.scheme.get("patch_line") or ""), ctx.base_facts
            )
            census = {"requested": True, "n": n, "error": error}
        return census

    def _version_decl_steps(
        self, ctx: _VersionGateCtx, contract, census: dict
    ) -> tuple[list[str], list[dict]]:
        """Derive the journey's tag/release targets and any mismatches."""
        base_facts = ctx.base_facts
        if census["requested"] and census["error"] is None and census["n"] is not None:
            base_facts = {**base_facts, "n": census["n"]}
        facts = _version_facts(contract, base_facts)
        template_name = _JOURNEY_VERSION_TEMPLATE.get(ctx.journey or "")
        expected = str(facts.get(template_name) or "") if template_name else ""
        plan = (
            build_operation_plan(
                ctx.table,
                ctx.journey,
                facts,
                active_release_branch=active_release_branch(self.repo, facts),
            )
            if ctx.journey
            else {"steps": []}
        )
        mismatches: list[dict] = []
        derived: list[str] = []
        for step in plan.get("steps") or ():
            kind, _sep, target = str(step).partition(":")
            if kind not in _VERSION_STEP_KINDS:
                continue
            if not expected or target != expected:
                mismatches.append(
                    {
                        "step": str(step),
                        "kind": kind,
                        "target": target,
                        "expected": expected,
                    }
                )
            elif target not in derived:
                derived.append(target)
        return derived, mismatches

    def _version_decl_remote_probe(
        self, ctx: _VersionGateCtx, derived: list[str], census: dict, command_echo: list[str]
    ) -> bool:
        """Probe each derived tag with ``git ls-remote`` and emit the verdict."""
        remote = git(self.repo, "config", "--get", "remote.origin.url", check=False)
        if remote.returncode != 0 or not remote.stdout.strip():
            self._emit(
                "attention.required",
                {
                    "area": "version_gate",
                    "reason": "remote_unavailable",
                    "stage": "M-VERIFY",
                    "candidate_sha": ctx.candidate_sha,
                    "detail": (
                        "no origin remote configured; derived tag absence is "
                        "not verifiable"
                    ),
                    "next": "configure origin; trac run --resume re-checks the gate",
                },
                command_id=ctx.cmd.command_id,
            )
            return self._emit_version_decl_verdict(
                ctx,
                derived_tags=derived,
                census=census,
                remote_check={"status": "skipped_no_remote", "tags": derived},
                reason=None,
                detail="",
                command_echo=command_echo,
            )
        for tag in derived:
            probe = git(
                self.repo,
                "ls-remote",
                "--tags",
                "origin",
                f"refs/tags/{tag}",
                check=False,
            )
            if probe.returncode != 0:
                return self._emit_version_decl_verdict(
                    ctx,
                    derived_tags=derived,
                    census=census,
                    remote_check={
                        "status": "unavailable",
                        "tags": derived,
                        "failed_tag": tag,
                    },
                    reason="remote_unavailable",
                    detail=f"git ls-remote failed for refs/tags/{tag}",
                    command_echo=command_echo,
                )
            if f"refs/tags/{tag}" in probe.stdout:
                return self._emit_version_decl_verdict(
                    ctx,
                    derived_tags=derived,
                    census=census,
                    remote_check={
                        "status": "exists",
                        "tags": derived,
                        "existing": tag,
                    },
                    reason="tag_already_exists",
                    detail=f"remote already carries refs/tags/{tag}",
                    command_echo=command_echo,
                )
        return self._emit_version_decl_verdict(
            ctx,
            derived_tags=derived,
            census=census,
            remote_check={"status": "verified_absent", "tags": derived},
            reason=None,
            detail="",
            command_echo=command_echo,
        )

    def _emit_version_decl_verdict(
        self,
        ctx: _VersionGateCtx,
        *,
        derived_tags,
        census,
        remote_check,
        reason,
        detail,
        command_echo,
        extra=None,
    ) -> bool:
        """Emit the candidate-bound verdict of the native version gate.

        The payload mirrors the command-gate shape (normalized
        ``tracks-gate-result`` v1 + non-empty command_echo) so the resume
        completeness judge and the release evidence digests consume it like
        every other declared gate; ``journey``/``derived_tags``/
        ``remote_patch_n``/``remote_check`` carry the derivation audit.
        """
        status = "failed" if reason else "passed"
        exit_code = None if reason else 0
        summary = {
            "journey": ctx.journey,
            "derived_tags": list(derived_tags),
            "remote_check": dict(remote_check),
            "remote_patch_n": dict(census),
        }
        if reason:
            summary["reason"] = reason
            summary["detail"] = detail
        result = NormalizedGateResult(
            gate_id=ctx.gate.kind,
            result_version=1,
            status=status,
            exit_code=exit_code,
            summary=summary,
            command_echo=tuple(command_echo),
        )
        payload = {
            "kind": ctx.gate.kind,
            "gate_identity": ctx.identity,
            "candidate_sha": ctx.candidate_sha,
            "contract_digest": ctx.contract_digest,
            "command_echo": list(command_echo),
            "normalized_result": normalized_result_payload(result),
            "status": status,
            "exit_code": exit_code,
            "summary": summary,
            "journey": ctx.journey,
            "derived_tags": list(derived_tags),
            "remote_patch_n": dict(census),
            "remote_check": dict(remote_check),
        }
        if extra:
            payload.update(extra)
        if reason:
            payload["reason"] = reason
            payload["detail"] = detail
            self._emit("local_gate.failed", payload, command_id=ctx.cmd.command_id)
            return False
        self._emit("local_gate.passed", payload, command_id=ctx.cmd.command_id)
        return True

    def _emit_host_contract_failure(self, cmd, candidate_sha, contract_digest):
        """FR-0281-03 machine evidence + attention route for a contract whose
        declared gate did not pass: ``host_contract.failed`` re-projects the
        machine evidence captured on the bound ``local_gate.failed`` event
        (exit code, stdout/stderr tails, normalized result);
        ``attention.required`` (area host_contract) renders
        ``needs_attention=host_contract:...`` so the status surface names the
        contract revision route (never a guessed command)."""
        failure = next(
            (
                event
                for event in reversed(list(self.store.events(self.run_id)))
                if event.type == "local_gate.failed"
                and (event.payload or {}).get("candidate_sha") == candidate_sha
                and (event.payload or {}).get("contract_digest") == contract_digest
            ),
            None,
        )
        payload = dict(failure.payload or {}) if failure is not None else {}
        normalized = payload.get("normalized_result") or {}
        summary = payload.get("summary") or normalized.get("summary") or {}
        reason = (
            "malformed_result"
            if payload.get("reason") == "malformed" or normalized.get("status") == "malformed"
            else "gate_failed"
        )
        kind = str(payload.get("kind") or "")
        self._emit(
            "host_contract.failed",
            {
                "candidate_sha": candidate_sha,
                "contract_digest": contract_digest,
                "kind": kind,
                "reason": reason,
                "exit_code": payload.get("exit_code"),
                "stdout_tail": str(summary.get("stdout", ""))[-500:],
                "stderr_tail": str(summary.get("stderr", ""))[-500:],
                "normalized_result": payload.get("normalized_result"),
            },
            command_id=cmd.command_id,
        )
        self._emit(
            "attention.required",
            {
                "area": "host_contract",
                "reason": reason,
                "stage": "M-VERIFY",
                "detail": f"declared gate {kind or 'unknown'} did not pass",
                "next": "revise the host-contract, then trac run --resume",
            },
            command_id=cmd.command_id,
        )
