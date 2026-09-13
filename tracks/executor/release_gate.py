"""M-RELEASE executor domain (FR-0273/FR-0274/FR-0277, IF-RELEASE-002/003).

Preview aggregation binds candidate SHA + artifact digest + evidence digests
+ operation-plan digest + contract/policy digest into preview_digest; the
Human three-way gate consumes the preview via the independent release CLI
surface and every decision is append-only and digest-bound. Three journey
plans (feature/post-release/dev, IF-JOURNEY-001) resolve declared operation
steps with version facts; dev precheck fails closed without an active release
branch.
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from tracks.capabilities import base_version
from tracks.executor.helpers import git

StaleReason = Literal["candidate_drift", "evidence_staled", "operation_plan_changed"]

# IF-JOURNEY-001 §1m: step COND closed set (KIND:TARGET[:when=COND]).
_WHEN_ACTIVE_BRANCH = "when=active_release_branch"

# journey CLI/attr spellings -> contract [operations.*] section keys.
_JOURNEY_MAP = {"post-release": "post_release", "post_release": "post_release"}

# D2 dependency order: an artifact upload needs its release (or at least its
# tag) to exist first, so the resolved plan is normalized to
# merge -> tag -> release -> artifact. The sort is stable, so same-rank steps
# keep their declared relative order (multi-merge/multi-tag plans) and unknown
# kinds sort last where the publish preflight rejects them explicitly.
_OPERATION_RANK = {"merge": 0, "tag": 1, "release": 2, "artifact": 3}


def _normalized_step_order(steps: list[str]) -> list[str]:
    return sorted(
        steps,
        key=lambda step: _OPERATION_RANK.get(step.partition(":")[0], 9),
    )


def render_placeholders(template: str, facts: dict) -> str:
    """Render the base placeholder set from version facts."""
    out = template
    for key, value in (facts or {}).items():
        out = out.replace("{" + key + "}", str(value))
    return out


def operation_plan_facts(contract: dict, facts: dict) -> dict:
    """Complete placeholder facts with the contract's declared plan sources.

    Single truth for every plan producer/recomputation (M-RELEASE preview,
    release-CLI replay, M-PUBLISH re-validation): the base fact set (version
    facts + version_scheme labels) is completed with the declared
    ``[host-contract.build].artifact`` so an ``artifact:{artifact}`` step
    resolves to the declared path/glob exactly like the built artifact the
    preview binds. ``{artifact}`` is the only base placeholder with a
    declaration site in the contract; ``{prefix}/{prefix_bin}/{result}``
    resolve only where the executing gate supplies them (install/build/smoke
    scopes), so they stay literal in a plan and the publish preflight rejects
    that fail-closed. An undeclared artifact likewise leaves the literal.
    """
    merged = dict(facts or {})
    if not merged.get("artifact"):
        declared = (contract or {}).get("build") or {}
        artifact = str(declared.get("artifact") or "")
        if artifact:
            merged["artifact"] = render_placeholders(artifact, merged)
    return merged


def expand_version_scheme(facts: dict, scheme) -> dict:
    """Resolve the ``version_scheme`` tag templates against *facts*.

    Iterates the placeholder substitution to a fixed point (3 rounds, the
    established depth): ``feature_tag``/``patch_line``/``prerelease_tag``
    may reference each other. A non-dict scheme leaves the facts unchanged.
    Shared by the release preview and the publish-time re-validation."""
    resolved = dict(facts or {})
    if not isinstance(scheme, dict):
        return resolved
    for name in ("feature_tag", "patch_line", "prerelease_tag"):
        value = str(scheme.get(name, ""))
        for _ in range(3):
            for key, fact in resolved.items():
                value = value.replace("{" + key + "}", str(fact))
        resolved[name] = value
    return resolved


def version_facts(version: str, run_id: str = "") -> dict:
    """Base version facts for a run identity (IF-JOURNEY-001 §1m).

    ``{major}/{minor}`` come from the run's TARGET version: a hotfix run
    inherits its target baseline (``v0.8-hotfix-42`` -> ``v0.8`` -> major
    ``0``, minor ``0.8``); the ``version`` key keeps the run identity used
    by ``{version}`` placeholders (hotfix doc/test paths). ``{ulid}`` is the
    run id; ``{n}`` is completed by :func:`complete_version_facts`.
    """
    target = base_version(str(version or ""))
    parts = target.lstrip("v").split(".")
    major = parts[0] if parts and parts[0].isdigit() else "0"
    minor_digit = parts[1] if len(parts) > 1 and parts[1].isdigit() else "0"
    return {
        "version": str(version or ""),
        "major": major,
        "minor": f"{major}.{minor_digit}",
        "ulid": run_id,
    }


def remote_patch_n(repo, patch_line: str, facts: dict) -> tuple[str | None, str | None]:
    """``{n}`` = highest remote patch tag + 1 for the rendered patch line.

    No configured remote or no matching tag -> ``1``; a configured remote
    whose census cannot be read fails closed with ``ls_remote_failed`` (the
    caller surfaces honest attention instead of guessing a patch number).
    Returns ``(None, None)`` when the patch line does not use ``{n}``.
    """
    template = str(patch_line or "")
    if "{n}" not in template:
        return None, None
    remote = git(repo, "config", "--get", "remote.origin.url", check=False)
    if remote.returncode != 0 or not remote.stdout.strip():
        return "1", None
    rendered = render_placeholders(template, facts)
    prefix = rendered.split("{n}", 1)[0]
    census = git(
        repo, "ls-remote", "--tags", "origin", f"refs/tags/{prefix}*", check=False
    )
    if census.returncode != 0:
        return None, "ls_remote_failed"
    highest = 0
    for line in census.stdout.splitlines():
        _sha, _sep, ref = line.partition("\t")
        suffix = ref[len("refs/tags/") :] if ref.startswith("refs/tags/") else ""
        if suffix.endswith("^{}"):
            suffix = suffix[:-3]
        if not suffix.startswith(prefix):
            continue
        patch = suffix[len(prefix) :]
        if patch.isdigit():
            highest = max(highest, int(patch))
    return str(highest + 1), None


def operation_plan_needs_n(contract_table: dict, journey: str) -> bool:
    """True when the journey's declared steps depend on the ``{n}`` fact,
    directly or through a version_scheme label whose template uses ``{n}``
    -- the only case a remote patch-tag census is required."""
    scheme = contract_table.get("version_scheme") or {}
    label_templates = {
        f"{{{name}}}": str(scheme.get(name) or "")
        for name in ("feature_tag", "patch_line", "prerelease_tag")
    }
    section = (contract_table.get("operations") or {}).get(journey) or {}
    for step in section.get("steps") or ():
        step = str(step)
        if "{n}" in step:
            return True
        for token, template in label_templates.items():
            if token in step and "{n}" in template:
                return True
    return False


def complete_version_facts(
    repo, facts: dict, patch_line: str, *, needs_n: bool = True
) -> tuple[dict | None, str | None]:
    """Fill a base fact set with ``{n}``/``{ulid}`` (no fact overwritten).

    Shared by every preview producer and re-validation site (M-RELEASE
    preview, release-CLI recompute, M-PUBLISH rebuild) so an operation plan
    rebuilt later resolves byte-identical facts. The remote census runs only
    when the journey's steps actually depend on ``{n}``
    (:func:`operation_plan_needs_n`). Returns ``(facts, None)`` or
    ``(None, reason)``.
    """
    resolved = dict(facts or {})
    if needs_n and "n" not in resolved:
        n, error = remote_patch_n(repo, patch_line, resolved)
        if error is not None:
            return None, error
        if n is not None:
            resolved["n"] = n
    resolved.setdefault("ulid", "")
    return resolved, None


def active_release_branch(repo, facts: dict) -> str | None:
    """The journey's active release branch (local OR remote existence).

    IF-JOURNEY-001 (§1m): ``releases/v{minor}`` for the target version
    (``v0.8`` -> ``releases/v0.8``); absent when neither the local branch nor
    a reachable ``origin`` head confirms it.
    """
    minor = str((facts or {}).get("minor") or "")
    if not minor:
        return None
    candidate = f"releases/v{minor}"
    local = git(repo, "branch", "--list", candidate, check=False)
    if local.returncode == 0 and any(
        line.strip().lstrip("* ").strip() == candidate
        for line in local.stdout.splitlines()
    ):
        return candidate
    remote = git(repo, "config", "--get", "remote.origin.url", check=False)
    if remote.returncode != 0 or not remote.stdout.strip():
        return None
    heads = git(
        repo, "ls-remote", "--heads", "origin", f"refs/heads/{candidate}", check=False
    )
    if heads.returncode == 0 and f"refs/heads/{candidate}" in heads.stdout:
        return candidate
    return None


def build_operation_plan(
    contract: dict,
    journey: str,
    version_facts: dict,
    *,
    active_release_branch: str | None = None,
) -> dict:
    """Resolve the journey's declared operation steps with placeholders.

    IF-JOURNEY-001 §1m: a ``KIND:TARGET:when=active_release_branch`` step is
    kept only when an active release branch exists; without one it is
    silently skipped and the plan digest is computed over the
    actually-resolved step set.
    """
    ops = (contract or {}).get("operations", {})
    section = ops.get(journey) or ops.get(_JOURNEY_MAP.get(journey, journey)) or {}
    steps = list(section.get("steps") or [])
    plan_facts = operation_plan_facts(contract, version_facts)
    resolved: list[str] = []
    for step in steps:
        if step.endswith(f":{_WHEN_ACTIVE_BRANCH}"):
            if not active_release_branch:
                continue  # silent skip: no active release branch
            step = step[: -len(f":{_WHEN_ACTIVE_BRANCH}")]
        resolved.append(render_placeholders(step, plan_facts))
    resolved = _normalized_step_order(resolved)
    return {
        "journey": journey,
        "steps": resolved,
        "operations": {journey: {"steps": resolved}},
        "requires": list(section.get("requires") or ()),
    }


def dev_precheck(contract: dict, version_facts: dict, *, active_release_branch) -> tuple[bool, str]:
    """IF-JOURNEY-001 dev precheck: no active release branch -> fail closed
    (no plan steps, no fake public release)."""
    del version_facts
    if not active_release_branch:
        return False, "no active release branch"
    ops = (contract or {}).get("operations", {})
    section = ops.get("dev") or {}
    steps = section.get("steps") or []
    if not steps:
        return False, "dev journey has no declared steps"
    return True, ""


def compute_preview_digest(
    candidate_sha: str,
    artifact_digest: str,
    evidence_digests: dict,
    operation_plan_digest: str,
    contract_policy_digest: str,
) -> str:
    """§1g: preview_digest = sha256(canonical_json(all five components))."""
    raw = {
        "candidate_sha": candidate_sha,
        "artifact_digest": artifact_digest,
        "evidence_digests": evidence_digests,
        "operation_plan_digest": operation_plan_digest,
        "contract_policy_digest": contract_policy_digest,
    }
    canonical = json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def generate_preview(candidate_sha: str, digests: dict, risks: list, plan: dict) -> dict:
    """Assemble the content-address preview blob (IF-RELEASE-002)."""
    preview = {
        "candidate_sha": candidate_sha,
        "artifact_digest": digests.get("artifact_digest", ""),
        "evidence_digests": digests.get("evidence_digests", {}),
        "operation_plan_digest": digests.get("operation_plan_digest", ""),
        "contract_policy_digest": digests.get("contract_policy_digest", ""),
        "risks": list(risks or []),
        "operation_plan": dict(plan or {}),
    }
    if digests.get("artifact_identity"):
        # D1: the resolved build artifact's byte identity (name/size/digest)
        # is part of the preview payload so the upload can bind bytes, not
        # just carry an opaque digest. Absent for contracts that declare no
        # artifact, keeping legacy preview payloads byte-identical.
        preview["artifact"] = dict(digests["artifact_identity"])
    preview["preview_digest"] = compute_preview_digest(
        candidate_sha,
        preview["artifact_digest"],
        preview["evidence_digests"],
        preview["operation_plan_digest"],
        preview["contract_policy_digest"],
    )
    return preview


def judge_preview_stale(current_aggregate: dict, preview: dict) -> StaleReason | None:
    """IF-RELEASE-002: recompute aggregate at read time; mismatch -> stale."""
    current = current_aggregate or {}
    if not current:
        return None
    if current.get("candidate_sha") != preview.get("candidate_sha"):
        return "candidate_drift"
    if current.get("preview_digest") is not None and current.get("preview_digest") != preview.get(
        "preview_digest"
    ):
        return "operation_plan_changed"
    if current.get("evidence_staled"):
        return "evidence_staled"
    return None


def validate_release_decision(
    decision: str, preview: dict, gate_status: dict
) -> tuple[bool, str | None]:
    """Fail-closed authorization check (stale preview / failed gate → reject).

    IF-RELEASE-003: the three-way gate rejects when the preview is stale or a
    prerequisite gate failed; no Human decision may bypass a failing gate.
    """
    if decision not in ("release", "delay", "return"):
        return False, "invalid decision"
    if gate_status.get("preview_stale"):
        return False, "preview stale"
    if decision == "release":
        if gate_status.get("gate_failed"):
            return False, "gate failed"
        if gate_status.get("security_status") in ("failed", "unknown"):
            return False, "security not passed"
        if gate_status.get("prism_status") in ("failed", "revise"):
            return False, "prism not passed"
    return True, None
