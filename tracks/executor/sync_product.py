"""FR-0277-02 strict release-branch sync products.

A legitimately diverged active release branch cannot be synchronized with the
approved candidate by a fast-forward. This module owns the strict contract:

* the merge product P is prepared and verified **before** the Human release
  decision, in a real isolated checkout, against the host-declared
  quality/complete-test/security/required-CI battery;
* the record ``{target, baseline_sha B, source_candidate_sha C, product_sha P,
  product_tree, evidence_digests}`` is what the operation plan, the preview
  digest and the Human decision bind;
* the publish face only consumes this approved identity (atomic expected-old
  ref update); it never creates or "verifies" a product.

P is deterministic: a merge-base-aware three-way tree (``git merge-tree
--write-tree``, never the two-tree ``read-tree`` mode) finished by a
``commit-tree`` with fixed identity/date. The same (B, C) therefore always
rebuilds the same P -- crash recovery reuses the identical product instead of
minting a fresh merge under a new clock.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shlex
import subprocess
import tempfile
from pathlib import Path

from tracks.executor.host_contract import (
    HostContract,
    NormalizedGateResult,
    execute_gate,
    render_artifact_template,
)
from tracks.executor.registry_gate import execute_registry_gate
from tracks.executor.security import run_security_scans

SYNC_PREPARED = "sync_product.prepared"
SYNC_VERIFIED = "sync_product.verified"
SYNC_FAILED = "sync_product.failed"

PRODUCT_REF_PREFIX = "refs/trac/tmp/sync-products"
FETCH_REF_PREFIX = "refs/trac/tmp/sync-base"

EVIDENCE_RINGS = ("gates", "tests", "security", "ci")

# Deterministic commit identity: the product object is a pure function of
# (baseline, candidate, target); the fixed date keeps the SHA reproducible
# across processes and machines.
_PRODUCT_DATE = "2000-01-01T00:00:00+00:00"
_PRODUCT_MESSAGE = (
    "Sync active release branch {target}\n"
    "\n"
    "Tracks-Sync-Product: {candidate}\n"
    "Tracks-Sync-Baseline: {baseline}\n"
)

_TEST_TIMEOUT_SECONDS = 3600
_MERGE_TIMEOUT_SECONDS = 600


def canonical_digest(value: object) -> str:
    raw = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _valid_oid(value: object) -> bool:
    return isinstance(value, str) and len(value) in (40, 64) and all(
        char in "0123456789abcdefABCDEF" for char in value
    )


def _run(args, repo, *, env=None, timeout=60):
    try:
        return subprocess.run(
            list(args),
            cwd=str(repo),
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def _git(repo, *args, env=None, timeout=60):
    return _run(["git", *args], repo, env=env, timeout=timeout)


# -- remote / ancestry probes -------------------------------------------------


def remote_branch_tip(repo: Path, target: str) -> tuple[str | None, str | None]:
    """``(tip, None)`` / ``(None, None)`` absent / ``(None, reason)`` error."""
    config = _git(repo, "config", "--get", "remote.origin.url")
    if config is None or config.returncode != 0 or not config.stdout.strip():
        return None, "missing_remote"
    proc = _git(repo, "ls-remote", "--refs", "origin", f"refs/heads/{target}")
    if proc is None:
        return None, "ls_remote_failed"
    if proc.returncode != 0:
        return None, proc.stderr.strip() or "ls_remote_failed"
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    if not lines:
        return None, None
    if len(lines) != 1:
        return None, "malformed_ls_remote"
    sha, _, ref = lines[0].partition("\t")
    if ref != f"refs/heads/{target}" or not _valid_oid(sha):
        return None, "malformed_ls_remote"
    return sha, None


def is_ancestor(repo: Path, ancestor: str, descendant: str) -> tuple[bool | None, str | None]:
    proc = _git(repo, "merge-base", "--is-ancestor", ancestor, descendant)
    if proc is None:
        return None, "ancestry_timeout"
    if proc.returncode == 0:
        return True, None
    if proc.returncode == 1:
        return False, None
    return None, proc.stderr.strip() or "ancestry_check_failed"


def sync_need(
    repo: Path, baseline_sha: str, candidate_sha: str
) -> tuple[str | None, str | None]:
    """Classify the remote release tip against the candidate.

    ``contained`` (tip already carries C), ``fast_forward`` (tip is an
    ancestor of C: the existing FF merge path applies unchanged) or
    ``product`` (genuine divergence: a prepared, verified P is required).
    """
    if baseline_sha == candidate_sha:
        return "contained", None
    contained, error = is_ancestor(repo, candidate_sha, baseline_sha)
    if error:
        return None, error
    if contained:
        return "contained", None
    ff, error = is_ancestor(repo, baseline_sha, candidate_sha)
    if error:
        return None, error
    return ("fast_forward" if ff else "product"), None


# -- product preparation ------------------------------------------------------


def _ensure_object(repo: Path, sha: str) -> str | None:
    """The commit object is local, or fetched from the bound remote by ref."""
    probe = _git(repo, "cat-file", "-e", f"{sha}^{{commit}}")
    if probe is not None and probe.returncode == 0:
        return None
    fetch_ref = f"{FETCH_REF_PREFIX}/{sha}"
    fetched = _git(
        repo, "fetch", "--no-tags", "origin", f"{sha}:{fetch_ref}", timeout=120
    )
    try:
        if fetched is None or fetched.returncode != 0:
            return "baseline_fetch_failed"
        verify = _git(repo, "rev-parse", "--verify", f"{fetch_ref}^{{commit}}")
        if verify is None or verify.returncode != 0:
            return "baseline_fetch_failed"
    finally:
        _git(repo, "update-ref", "-d", fetch_ref)
    return None


def _deterministic_product(
    repo: Path, target: str, baseline_sha: str, candidate_sha: str
) -> tuple[str | None, str | None, str | None]:
    """``(product_sha, product_tree, error)`` for the true three-way merge."""
    merged = _git(
        repo,
        "merge-tree",
        "--write-tree",
        baseline_sha,
        candidate_sha,
        timeout=_MERGE_TIMEOUT_SECONDS,
    )
    if merged is None:
        return None, None, "merge_tree_timeout"
    if merged.returncode == 1:
        return None, None, "merge_conflict"
    if merged.returncode != 0:
        return None, None, merged.stderr.strip() or "merge_tree_failed"
    lines = [line for line in merged.stdout.splitlines() if line.strip()]
    tree = lines[0] if lines else ""
    if not _valid_oid(tree):
        return None, None, "merge_tree_unresolved"
    env = dict(os.environ)
    env.update(
        {
            "GIT_AUTHOR_NAME": "tracks-runtime",
            "GIT_AUTHOR_EMAIL": "tracks-runtime@localhost",
            "GIT_AUTHOR_DATE": _PRODUCT_DATE,
            "GIT_COMMITTER_NAME": "tracks-runtime",
            "GIT_COMMITTER_EMAIL": "tracks-runtime@localhost",
            "GIT_COMMITTER_DATE": _PRODUCT_DATE,
        }
    )
    message = _PRODUCT_MESSAGE.format(
        target=target, candidate=candidate_sha, baseline=baseline_sha
    )
    committed = _git(
        repo,
        "commit-tree",
        tree,
        "-p",
        baseline_sha,
        "-p",
        candidate_sha,
        "-m",
        message,
        env=env,
    )
    if committed is None or committed.returncode != 0:
        return None, None, "commit_tree_failed"
    product = committed.stdout.strip()
    if not _valid_oid(product) or "\n" in product:
        return None, None, "commit_tree_unresolved"
    return product, tree, None


def build_product(
    repo: Path, target: str, baseline_sha: str, candidate_sha: str
) -> tuple[dict | None, str | None]:
    """Prepare the merge product for one diverged ``target``.

    Returns the product record (without evidence digests) or an error reason.
    The product is registered under ``refs/trac/tmp/sync-products/<target>``
    until the M-MILESTONE cleanup drops the tmp namespace.
    """
    if not isinstance(target, str) or not target.strip():
        return None, "malformed"
    if not _valid_oid(baseline_sha) or not _valid_oid(candidate_sha):
        return None, "malformed"
    error = _ensure_object(repo, candidate_sha)
    if error is not None:
        return None, error
    error = _ensure_object(repo, baseline_sha)
    if error is not None:
        return None, error
    product, tree, error = _deterministic_product(
        repo, target, baseline_sha, candidate_sha
    )
    if error is not None:
        return None, error
    _git(
        repo,
        "update-ref",
        f"{PRODUCT_REF_PREFIX}/{target}",
        product,
        timeout=60,
    )
    return {
        "step": f"merge:{target}",
        "target": target,
        "baseline_sha": baseline_sha,
        "source_candidate_sha": candidate_sha,
        "product_sha": product,
        "product_tree": tree,
    }, None


def product_shape(repo: Path, product_sha: str) -> tuple[dict | None, str | None]:
    """``(parents, tree)`` of a product commit, or an error reason."""
    proc = _git(repo, "cat-file", "-p", product_sha)
    if proc is None or proc.returncode != 0:
        return None, "product_missing"
    parents: list[str] = []
    tree = ""
    for line in proc.stdout.splitlines():
        if line.startswith("parent "):
            parents.append(line.split(None, 1)[1].strip())
        elif line.startswith("tree ") and not tree:
            tree = line.split(None, 1)[1].strip()
    return {"parents": parents, "tree": tree}, None


def validate_record(
    repo: Path, record: object, candidate_sha: str
) -> str | None:
    """Fail-closed identity check for one product record.

    Enforces: well-formed full object IDs, ``source_candidate_sha == C``,
    exact parent order (B first, C second), the recorded tree, and a
    byte-identical re-derivation of P from (B, C) -- the recomputation proves
    the product was not minted under a different clock or remote state.
    """
    if not isinstance(record, dict):
        return "sync_product_malformed"
    target = record.get("target")
    baseline = record.get("baseline_sha")
    source = record.get("source_candidate_sha")
    product = record.get("product_sha")
    tree = record.get("product_tree")
    if not isinstance(target, str) or not target.strip():
        return "sync_product_malformed"
    if not (_valid_oid(baseline) and _valid_oid(source) and _valid_oid(product)):
        return "sync_product_malformed"
    if not _valid_oid(tree):
        return "sync_product_malformed"
    if source != candidate_sha:
        return "sync_product_candidate_mismatch"
    shape, error = product_shape(repo, product)
    if error is not None:
        return error
    if shape["tree"] != tree:
        return "sync_product_tree_mismatch"
    if shape["parents"] != [baseline, source]:
        return "sync_product_parent_mismatch"
    derived, _derived_tree, error = _deterministic_product(
        repo, target, baseline, source
    )
    if error is not None:
        return f"sync_product_recompute_{error}"
    if derived != product:
        return "sync_product_recompute_mismatch"
    evidence = record.get("evidence_digests")
    if not isinstance(evidence, dict) or any(
        not isinstance(evidence.get(ring), str) or not evidence.get(ring)
        for ring in EVIDENCE_RINGS
    ):
        return "sync_product_evidence_missing"
    return None


def stale_barrier(events, candidate_sha: str) -> int:
    """Sequence of the latest stale marker for the candidate (-1 when none).

    Candidate-stale / evidence-staled markers invalidate every verification
    before them (FR-0287 human return, SM-01.20 new candidate); a fresh
    verification must land after the barrier before a product is reusable.
    """
    return max(
        (
            int(getattr(event, "seq", 0) or 0)
            for event in events
            if getattr(event, "type", None) in ("candidate.stale", "evidence.staled")
            and (event.payload or {}).get("candidate_sha") in (None, candidate_sha)
        ),
        default=-1,
    )


def verified_records(events, candidate_sha: str) -> list[dict]:
    """Latest verified product record per (target, baseline) for one candidate."""
    barrier = stale_barrier(events, candidate_sha)
    records: dict[tuple[str, str], dict] = {}
    for event in events:
        if getattr(event, "type", None) != SYNC_VERIFIED:
            continue
        if int(getattr(event, "seq", 0) or 0) <= barrier:
            continue
        payload = event.payload or {}
        if payload.get("candidate_sha") != candidate_sha:
            continue
        record = payload.get("product")
        if isinstance(record, dict):
            records[(str(record.get("target")), str(record.get("baseline_sha")))] = record
    return list(records.values())


def record_verified(events, record: dict, candidate_sha: str) -> bool:
    """True when the exact approved identity landed as a verified event."""
    barrier = stale_barrier(events, candidate_sha)
    for event in events:
        if getattr(event, "type", None) != SYNC_VERIFIED:
            continue
        if int(getattr(event, "seq", 0) or 0) <= barrier:
            continue
        payload = event.payload or {}
        if payload.get("candidate_sha") != candidate_sha:
            continue
        other = payload.get("product")
        if not isinstance(other, dict):
            continue
        if all(
            other.get(key) == record.get(key)
            for key in (
                "target",
                "baseline_sha",
                "source_candidate_sha",
                "product_sha",
                "product_tree",
                "evidence_digests",
            )
        ):
            return True
    return False


# -- product verification -----------------------------------------------------


def _normalized_gate(result: NormalizedGateResult) -> dict:
    return {
        "gate_id": result.gate_id,
        "status": result.status,
        "exit_code": result.exit_code,
        "command": list(result.command_echo or ()),
        "summary": result.summary,
    }


def _link_declared_envs(repo: Path, worktree: Path, commands) -> None:
    """Link the host's declared environment directories into the checkout.

    The declared commands (``<env-dir>/bin/<tool> ...``) must resolve in the
    product checkout exactly as they do in the host tree; the first path
    segment of a declared command's executable names the environment
    directory. Purely data-driven -- the runtime never spells one itself
    (NFR-0147).
    """
    for command in commands:
        if not isinstance(command, str) or not command.strip():
            continue
        try:
            first = shlex.split(command)[0]
        except ValueError:
            continue
        parts = Path(first).parts
        if len(parts) < 2 or parts[0].startswith("-"):
            continue
        source = repo / parts[0]
        target = worktree / parts[0]
        if source.is_dir() and not target.exists():
            with contextlib.suppress(OSError):
                target.symlink_to(source, target_is_directory=True)


def _gate_scope(facts: dict, prefix: Path, product_sha: str) -> dict:
    scope = dict(facts or {})
    scope.setdefault("artifact", "")
    scope["prefix"] = str(prefix)
    scope["prefix_bin"] = str(prefix / "bin")
    scope["candidate_sha"] = product_sha
    return scope


def _link_registry_envs(repo: Path, worktree: Path, architecture: Path) -> None:
    """Link the environment dirs the canonical registry commands declare."""
    from tracks.executor.guard_registry import load_guard_registry

    try:
        registry = load_guard_registry(architecture)
    except (OSError, ValueError):
        return
    commands: list[str] = []
    for entry in registry.entries:
        command = entry.command
        commands.append(
            shlex.join(command) if isinstance(command, (list, tuple)) else str(command)
        )
    _link_declared_envs(repo, worktree, commands)


def _run_gates(
    repo: Path,
    contract: HostContract,
    worktree: Path,
    version: str,
    scope: dict,
) -> tuple[list[dict] | None, str | None]:
    """Every declared local gate on the isolated product checkout."""
    from tracks import paths

    results: list[dict] = []
    for ordinal, gate in enumerate(contract.local_gates):
        try:
            if gate.source == "guard_registry":
                architecture = (
                    paths.version_dir(Path(worktree / ".tracks"), version)
                    / "architecture.md"
                )
                _link_registry_envs(repo, worktree, architecture)
                result = execute_registry_gate(gate, worktree, architecture, scope)
            else:
                result = execute_gate(gate, worktree, scope)
        except Exception as err:  # noqa: BLE001 -- fail closed per gate
            return None, f"sync_gate_failed:{gate.kind}:{type(err).__name__}"
        entry = _normalized_gate(result)
        entry["gate_identity"] = f"{gate.kind}[{ordinal}]"
        results.append(entry)
        if result.status != "passed":
            return results, f"sync_gate_failed:{gate.kind}"
    return results, None


def _run_test_layers(
    repo: Path,
    worktree: Path,
    result_dir: Path,
    *,
    full_nodes: list[str] | None = None,
    waived_nodes: set[str] | None = None,
) -> tuple[list[dict] | None, str | None]:
    """The host-declared complete test ladder on the product checkout.

    A host without a loadable project contract declares no test ladder
    (recorded as ``undeclared``); a declared ladder whose tool or tests fail
    blocks the product -- a missing tool is never a silent pass.

    With ``full_nodes`` the ring re-executes the candidate's FULL selection
    per declared layer on the product tree, excluding the candidate's
    approved known-issue waivers (the same judgment M-VERIFY used for C).
    Without one (direct callers) each declared layer's whole ``run`` command
    is used.
    """
    from tracks.project import ContractError, load_contract

    try:
        project = load_contract(repo)
    except ContractError:
        return [{"layer": "undeclared"}], None
    layers = [
        ("unit", project.unit),
        ("integration", project.integration),
        ("e2e", project.e2e),
    ]
    test_commands = [
        command
        for section in (project.unit, project.integration, project.e2e)
        for command in (section.collect, section.run, section.run_selected)
    ]
    _link_declared_envs(repo, worktree, test_commands)
    results: list[dict] = []
    for name, section in layers:
        entry, error = _run_one_layer(
            name,
            section,
            worktree,
            result_dir,
            full_nodes,
            set(waived_nodes or ()),
        )
        if entry is not None:
            results.append(entry)
        if error is not None:
            return results or None, error
    return results, None


def _run_one_layer(
    name: str,
    section,
    worktree: Path,
    result_dir: Path,
    full_nodes: list[str] | None,
    waived: set[str],
) -> tuple[dict | None, str | None]:
    result_path = result_dir / f"{name}.xml"
    command, selected = _layer_command(section, full_nodes, waived, result_path)
    if command is None:
        return {"layer": name, "status": "no_selected_nodes"}, None
    proc = _run(shlex.split(command), worktree, timeout=_TEST_TIMEOUT_SECONDS)
    if proc is None:
        return None, f"sync_tests_failed:{name}"
    entry = _test_entry(name, selected, proc, result_path)
    if proc.returncode != 0:
        return entry, f"sync_tests_failed:{name}"
    return entry, None


def _layer_command(
    section,
    full_nodes: list[str] | None,
    waived: set[str],
    result_path: Path,
) -> tuple[str | None, list[str] | None]:
    """The layer command + its selected nodes (``None`` command = none left)."""
    if full_nodes is None:
        return str(section.run).replace("{result}", str(result_path)), None
    selected = sorted(
        str(node)
        for node in full_nodes
        if node not in waived
        and any(str(node).startswith(str(path)) for path in section.paths)
    )
    if not selected:
        return None, []
    command = (
        str(section.run_selected)
        .replace("{nodes}", shlex.join(selected))
        .replace("{result}", str(result_path))
    )
    return command, selected


def _test_entry(
    name: str, selected: list[str] | None, proc, result_path: Path
) -> dict:
    entry = {
        "layer": name,
        "exit_code": proc.returncode,
        "result_digest": (
            "sha256:" + hashlib.sha256(result_path.read_bytes()).hexdigest()
            if result_path.is_file()
            else None
        ),
    }
    if selected is not None:
        entry["nodes"] = selected
    if proc.returncode != 0:
        entry["stdout_tail"] = (proc.stdout or "")[-500:]
        entry["stderr_tail"] = (proc.stderr or "")[-500:]
    return entry


def _run_security(
    contract: HostContract, worktree: Path, product_sha: str
) -> tuple[list[dict] | None, str | None]:
    results: list[dict] = []
    for result in run_security_scans(contract, worktree, product_sha):
        entry = _normalized_gate(result)
        results.append(entry)
        if result.status != "passed":
            return None, f"sync_security_failed:{result.gate_id or 'scan'}"
    return results, None


def _run_required_ci(
    contract: HostContract, worktree: Path, scope: dict
) -> tuple[dict | None, str | None]:
    """Required CI verification for the product.

    A product is unpublished when the Human decides, so the remote CI run
    cannot be read back; the host may declare ``[host-contract.ci].
    verify_command`` as the executable local equivalent. Non-empty
    ``required_checks`` without one fail closed (never a structural
    substitute for verification).
    """
    declared = dict(contract.ci or {})
    required = [str(check) for check in declared.get("required_checks") or ()]
    if not required:
        return {"required_checks": [], "status": "not_required"}, None
    command = str(declared.get("verify_command") or "").strip()
    if not command:
        return None, "ci_unverifiable"
    rendered = render_artifact_template(command, scope)
    proc = _run(shlex.split(rendered), worktree, timeout=_TEST_TIMEOUT_SECONDS)
    if proc is None or proc.returncode != 0:
        return None, "sync_ci_failed"
    return {
        "required_checks": required,
        "command": shlex.join(shlex.split(rendered)),
        "status": "passed",
    }, None


def verify_product(  # pylint: disable=too-many-locals
    repo: Path,
    contract: HostContract,
    record: dict,
    facts: dict,
    version: str,
    *,
    full_nodes: list[str] | None = None,
    waived_nodes: set[str] | None = None,
) -> tuple[dict | None, dict | None, str | None]:
    """Run the declared battery on an isolated checkout of P.

    Returns ``(evidence_digests, details, error)``; every ring is bound to the
    product SHA and the host-declared commands only. The isolated checkout
    links the host's declared environment directory (the first path segment of
    the contract-declared install interpreter, when it exists) so the
    declared commands resolve exactly as they do in the host tree; the
    worktree itself is a real, disposable checkout.
    """
    product_sha = str(record.get("product_sha") or "")
    worktree = Path(tempfile.mkdtemp(prefix="trac-sync-product-"))
    result_dir = Path(tempfile.mkdtemp(prefix="trac-sync-results-"))
    try:
        added = _git(repo, "worktree", "add", "--detach", str(worktree), product_sha)
        if added is None or added.returncode != 0:
            return None, None, "sync_worktree_failed"
        details, error = _run_battery(
            repo,
            contract,
            record,
            facts,
            version,
            worktree,
            result_dir,
            full_nodes=full_nodes,
            waived_nodes=waived_nodes,
        )
        if error is not None:
            return None, details, error
    finally:
        _git(repo, "worktree", "remove", "--force", str(worktree))
        _git(repo, "worktree", "prune")
    evidence = {ring: canonical_digest(details[ring]) for ring in EVIDENCE_RINGS}
    return evidence, details, None


def _run_battery(  # pylint: disable=too-many-locals
    repo: Path,
    contract: HostContract,
    record: dict,
    facts: dict,
    version: str,
    worktree: Path,
    result_dir: Path,
    *,
    full_nodes: list[str] | None,
    waived_nodes: set[str] | None,
) -> tuple[dict | None, str | None]:
    """The four declared verification rings, aborting on the first failure."""
    product_sha = str(record.get("product_sha") or "")
    _link_declared_envs(
        repo,
        worktree,
        (
            contract.install,
            contract.build_command,
            *contract.smoke,
            *(gate.command for gate in contract.local_gates),
            *(scan.command for scan in contract.security_scans),
        ),
    )
    scope = _gate_scope(facts, result_dir, product_sha)
    details: dict = {}
    gates, error = _run_gates(repo, contract, worktree, version, scope)
    if error is not None:
        return {"gates": gates}, error
    details["gates"] = gates
    tests, error = _run_test_layers(
        repo,
        worktree,
        result_dir,
        full_nodes=full_nodes,
        waived_nodes=waived_nodes,
    )
    if error is not None:
        return {**details, "tests": tests}, error
    details["tests"] = tests
    security, error = _run_security(contract, worktree, product_sha)
    if error is not None:
        return {**details, "security": security}, error
    details["security"] = security
    ci, error = _run_required_ci(contract, worktree, scope)
    if error is not None:
        return details, error
    details["ci"] = ci
    return details, None
