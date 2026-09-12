"""Shared accepted upstream evidence premises for release integration tests."""

from __future__ import annotations


def append_verify_release_premises(
    store,
    run_id: str,
    version: str,
    candidate: str,
    branch: str,
    contract_digest: str,
    artifact_digest: str,
    policy_digest: str,
    *,
    include_ci: bool = True,
    ci_run_id: int = 17,
    include_gate_summary: bool = True,
) -> None:
    """Append the same accepted M-VERIFY through M-RELEASE premises.

    The caller still creates/freezes the candidate and the test target remains
    the real preview/authorization command.  ``include_gate_summary`` keeps
    the older authorization fixture's exact payload shape intact.
    """
    store.append(run_id, version, "stage.entered", {"stage": "M-VERIFY"})
    store.append(
        run_id,
        version,
        "candidate.frozen",
        {
            "candidate_sha": candidate,
            "clean_tree": True,
            "branch": branch,
            "frozen_at_seq": len(list(store.events(run_id))) + 1,
        },
    )
    for ordinal, kind in enumerate(("quality", "trace")):
        normalized = {
            "schema": "tracks-gate-result",
            "version": 1,
            "status": "passed",
            "exit_code": 0,
            "summary": {"source": "accepted upstream premise"},
            "gate_id": kind,
        }
        payload = {
            "kind": kind,
            "gate_identity": f"{kind}[{ordinal}]",
            "candidate_sha": candidate,
            "contract_digest": contract_digest,
            "command_echo": ["true"],
            "normalized_result": dict(normalized),
            "status": "passed",
            "exit_code": 0,
        }
        if include_gate_summary:
            payload["summary"] = dict(normalized["summary"])
        store.append(run_id, version, "local_gate.passed", payload)
    store.append(
        run_id,
        version,
        "artifact.built",
        {
            "candidate_sha": candidate,
            "artifact": "dist/package.whl",
            "artifact_digest": artifact_digest,
            "status": "passed",
        },
    )
    if include_ci:
        store.append(
            run_id,
            version,
            "ci.run_observed",
            {
                "status": "passed",
                "repo": "local/preview-host",
                "workflow": "123",
                "run_id": ci_run_id,
                "head_sha": candidate,
                "candidate_sha": candidate,
                "conclusion": "success",
                "required_checks": ["required-ci"],
                "api_verified": True,
            },
        )
    store.append(
        run_id,
        version,
        "prism.verdict",
        {
            "verdict": "pass",
            "scope": "verify_final",
            "candidate_sha": candidate,
            "evidence_digests": {"artifact": artifact_digest},
        },
    )
    review_command_id = f"security-review-{candidate}"
    store.append(
        run_id,
        version,
        "prism.verdict",
        {
            "verdict": "pass",
            "scope": "security",
            "candidate_sha": candidate,
            "policy_digest": policy_digest,
            "evidence_digests": {"artifact": artifact_digest},
        },
        command_id=review_command_id,
    )
    store.append(
        run_id,
        version,
        "security.assessed",
        {
            "status": "passed",
            "policy_digest": policy_digest,
            "contract_digest": contract_digest,
            "candidate_sha": candidate,
            "scans": [{
                "id": "accepted-security-scan",
                "status": "passed",
                "exit_code": 0,
                "result_version": 1,
                "summary": {},
            }],
            "prism_scope": "security",
            "review_command_id": review_command_id,
        },
    )
    store.append(run_id, version, "stage.exited", {"stage": "M-VERIFY"})
    store.append(run_id, version, "stage.entered", {"stage": "M-SECURITY"})
    store.append(run_id, version, "stage.exited", {"stage": "M-SECURITY"})
    store.append(run_id, version, "stage.entered", {"stage": "M-RELEASE"})
