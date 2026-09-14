"""FR-0277-02 integration: prepared sync product through preview/publish.

Drives the real producers against a local bare remote: the post-release plan
declares the conditional active-release sync step, the executor prepares and
verifies P before the preview, the Human decision binds the product identity,
and M-PUBLISH consumes it with the atomic expected-old update. Red anchors:
preparation failure blocks before any irreversible operation; a missing or
mismatched verification cannot be bypassed; a baseline move after approval is
stale; crashes at three points reuse the same P and key.
"""

from __future__ import annotations

import subprocess

import pytest

from tests.unit.helpers import git_repo, git_strip
from tracks.effects import publish as publish_effects
from tracks.executor.executor import Executor
from tracks.executor.host_contract import load_host_contract
from tracks.executor.publish import operation_idempotency_key
from tracks.executor.publish_runtime import resolve_publish_authority
from tracks.executor.release_authorization import assess_release
from tracks.executor.release_gate import version_facts
from tracks.executor.release_preview import assemble_preview
from tracks.executor.sync_product import EVIDENCE_RINGS, SYNC_VERIFIED
from tracks.kernel.events import Command
from tracks.store import Store

pytestmark = pytest.mark.integration

_RELEASE = "releases/v0.8"
_TAG = "v0.8.7"
_CANDIDATE_SHA = None  # populated per fixture

_PROJECT_TOML = """\
[unit]
framework = "pytest"
paths = ["tests/unit/"]
collect = "true"
run = "true {result}"
run_selected = "true {nodes} {result}"
cwd = "."

[integration]
framework = "pytest"
paths = ["tests/integration/"]
collect = "true"
run = "true {result}"
run_selected = "true {nodes} {result}"
cwd = "."

[e2e]
framework = "pytest"
paths = ["tests/e2e/"]
collect = "true"
run = "true {result}"
run_selected = "true {nodes} {result}"
cwd = "."

[nightly]
schedule = "0 3 * * *"
workflow = ".github/workflows/nightly.yml"
job = "nightly"
layers = ["unit", "integration", "e2e"]
purpose = "test"

[host-contract]
version = 1
language = "test"
toolchain = "test"
install = ""

[[host-contract.local_gate]]
kind = "quality"
command = "true"
result_channel = "exit_code"
timeout_seconds = 30

[[host-contract.security_scan]]
id = "scan"
tool = "test"
command = "true"
result_channel = "exit_code"
threshold = "violations=0"
timeout_seconds = 30

[host-contract.ci]
required_checks = []

[host-contract.version_scheme]
feature_tag = "v{minor}.0"
patch_line = "v{minor}.7"
prerelease_tag = "v{minor}.7-pre.{ulid}"

[host-contract.operations.post_release]
steps = [
  "merge:main",
  "merge:releases/v{minor}:when=active_release_branch",
  "tag:{patch_line}",
]
"""


def _remote_head(bare, branch):
    proc = subprocess.run(
        ["git", "--git-dir", str(bare), "rev-parse", "--verify", f"refs/heads/{branch}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.stdout.strip() if proc.returncode == 0 else None


def _remote_tag(bare, tag):
    proc = subprocess.run(
        ["git", "--git-dir", str(bare), "rev-parse", "--verify", f"refs/tags/{tag}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.stdout.strip() if proc.returncode == 0 else None


def _seed_host(tmp_path, *, exact_branch=True):
    """Contract + main + diverged (or FF) ``releases/v0.8`` + candidate HEAD."""
    repo = git_repo(tmp_path, gitignore=True)
    bare = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "-q", "--bare", str(bare)], check=True, capture_output=True
    )
    git_strip(repo, "remote", "add", "origin", str(bare))
    projects = repo / ".tracks" / "projects"
    projects.mkdir(parents=True, exist_ok=True)
    (projects / "project.toml").write_text(_PROJECT_TOML, encoding="utf-8")
    git_strip(repo, "add", "-f", ".tracks/projects/project.toml")
    git_strip(repo, "commit", "-qm", "host contract")
    base = git_strip(repo, "rev-parse", "HEAD")
    git_strip(repo, "push", "-q", str(bare), "main:refs/heads/main")
    git_strip(repo, "checkout", "-q", "-b", _RELEASE, base)
    if exact_branch:
        (repo / "release_only.txt").write_text("release\n", encoding="utf-8")
        git_strip(repo, "add", "release_only.txt")
        git_strip(repo, "commit", "-qm", "release only")
        baseline = git_strip(repo, "rev-parse", "HEAD")
    else:
        baseline = base
    git_strip(repo, "push", "-q", str(bare), f"{_RELEASE}:refs/heads/{_RELEASE}")
    git_strip(repo, "checkout", "-q", "main")
    (repo / "fix.txt").write_text("fix\n", encoding="utf-8")
    git_strip(repo, "add", "fix.txt")
    git_strip(repo, "commit", "-qm", "fix")
    candidate = git_strip(repo, "rev-parse", "HEAD")
    store = Store(repo / ".tracks")
    store.append(
        "RUN", "v0.8", "candidate.frozen", {"candidate_sha": candidate, "clean_tree": True}
    )
    store.append(
        "RUN",
        "v0.8",
        "test.selected",
        {
            "scope": "full",
            "nodes": [
                "tests/unit/test_smoke.py::test_smoke",
                "tests/integration/test_smoke.py::test_smoke",
                "tests/e2e/test_smoke.py::test_smoke",
            ],
        },
    )
    return repo, bare, base, baseline, candidate, store


def _contract(repo):
    path = repo / ".tracks" / "projects" / "project.toml"
    contract = load_host_contract(path)
    import hashlib

    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return contract, digest


def _command(preview_digest=None, *, command_id="CMD-SYNC-1", journey="post_release"):
    params = {"journey": journey}
    if preview_digest:
        params["preview_digest"] = preview_digest
    return Command("execute_publish", params=params, command_id=command_id)


def _preview_command(journey="post_release"):
    return Command("generate_preview", params={"journey": journey}, command_id="CMD-PREVIEW-1")


def _emit_preview(executor, repo, store, candidate):
    contract, digest = _contract(repo)
    executor._park_preview(_preview_command(), candidate, contract, digest)
    previews = [e for e in store.events("RUN") if e.type == "release.previewed"]
    assert previews, "the preview must land"
    preview = previews[-1].payload
    store.append(
        "RUN",
        "v0.8",
        "release.decided",
        {
            "candidate_sha": candidate,
            "preview_digest": preview["preview_digest"],
            "action": "release",
            "actor": "human",
        },
    )
    return contract, preview


def _seed_decided_preview(executor, repo, store, candidate, monkeypatch):
    monkeypatch.chdir(repo)
    return _emit_preview(executor, repo, store, candidate)


def test_prepared_product_enters_plan_publishes_and_resumes(tmp_path, monkeypatch):
    repo, bare, _base, baseline, candidate, store = _seed_host(tmp_path)
    monkeypatch.chdir(repo)
    executor = Executor(store, repo, "RUN")
    _contract_obj, preview = _seed_decided_preview(
        executor, repo, store, candidate, monkeypatch
    )

    # Preparation ran before the preview: prepared + verified events with the
    # full identity, all four evidence rings bound to P.
    events = list(store.events("RUN"))
    assert [e.type for e in events].count("sync_product.prepared") == 1
    verified = [e for e in events if e.type == SYNC_VERIFIED]
    assert len(verified) == 1
    record = verified[0].payload["product"]
    assert record["baseline_sha"] == baseline
    assert record["source_candidate_sha"] == candidate
    assert set(record["evidence_digests"]) == set(EVIDENCE_RINGS)

    plan = preview["operation_plan"]
    assert plan["steps"] == ["merge:main", f"merge:{_RELEASE}", f"tag:{_TAG}"]
    assert plan["sync_products"] == [record]
    product_sha = record["product_sha"]

    pushes: list = []
    real_push = publish_effects.push_sync_product

    def counting_push(*args, **kwargs):
        pushes.append(args)
        return real_push(*args, **kwargs)

    monkeypatch.setattr(publish_effects, "push_sync_product", counting_push)

    executor._do_execute_publish(
        _command(preview["preview_digest"]), store.state("RUN"), None, False
    )

    events = list(store.events("RUN"))
    sync_done = [
        e.payload
        for e in events
        if e.type == "publish.executed" and e.payload.get("target") == _RELEASE
    ]
    assert sync_done and sync_done[-1]["status"] == "done"
    done = sync_done[-1]
    assert done["product_sha"] == product_sha
    assert done["baseline_sha"] == baseline
    assert done["source_candidate_sha"] == candidate
    assert done["evidence_digests"] == record["evidence_digests"]
    assert done["remote_check"]["object_id"] == product_sha
    assert _remote_head(bare, _RELEASE) == product_sha
    assert _remote_head(bare, "main") == candidate
    assert _remote_tag(bare, _TAG) == candidate
    assert len(pushes) == 1

    # Resume: per-operation reconciled_skip, the sync op skips on the exact P
    # object (never a fresh merge), no duplicate push.
    executor._do_execute_publish(
        _command(preview["preview_digest"]), store.state("RUN"), None, True
    )
    events = list(store.events("RUN"))
    skipped = [
        e.payload
        for e in events
        if e.type == "publish.executed" and e.payload.get("target") == _RELEASE
    ]
    assert skipped[-1]["status"] == "reconciled_skip"
    assert skipped[-1]["remote_check"]["object_id"] == product_sha
    assert len(pushes) == 1
    assert _remote_head(bare, _RELEASE) == product_sha


def test_preparation_failure_blocks_before_any_irreversible_operation(
    tmp_path, monkeypatch
):
    repo = git_repo(tmp_path, gitignore=True)
    bare = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "-q", "--bare", str(bare)], check=True, capture_output=True
    )
    git_strip(repo, "remote", "add", "origin", str(bare))
    projects = repo / ".tracks" / "projects"
    projects.mkdir(parents=True, exist_ok=True)
    (projects / "project.toml").write_text(_PROJECT_TOML, encoding="utf-8")
    git_strip(repo, "add", "-f", ".tracks/projects/project.toml")
    git_strip(repo, "commit", "-qm", "host contract")
    base = git_strip(repo, "rev-parse", "HEAD")
    git_strip(repo, "push", "-q", str(bare), "main:refs/heads/main")
    git_strip(repo, "checkout", "-q", "-b", _RELEASE, base)
    (repo / "shared.txt").write_text("release edit\n", encoding="utf-8")
    git_strip(repo, "add", "shared.txt")
    git_strip(repo, "commit", "-qm", "release edit")
    git_strip(repo, "push", "-q", str(bare), f"{_RELEASE}:refs/heads/{_RELEASE}")
    git_strip(repo, "checkout", "-q", "main")
    (repo / "shared.txt").write_text("main edit\n", encoding="utf-8")
    git_strip(repo, "add", "shared.txt")
    git_strip(repo, "commit", "-qm", "fix")
    candidate = git_strip(repo, "rev-parse", "HEAD")
    monkeypatch.chdir(repo)
    store = Store(repo / ".tracks")
    store.append(
        "RUN", "v0.8", "candidate.frozen", {"candidate_sha": candidate, "clean_tree": True}
    )
    executor = Executor(store, repo, "RUN")

    contract_obj, digest = _contract(repo)
    executor._park_preview(_preview_command(), candidate, contract_obj, digest)

    events = list(store.events("RUN"))
    failures = [e for e in events if e.type == "sync_product.failed"]
    assert failures and failures[-1].payload["reason"] == "merge_conflict"
    attention = [e for e in events if e.type == "attention.required"]
    assert attention and attention[-1].payload["area"] == "sync_product"
    assert not [e for e in events if e.type == SYNC_VERIFIED]
    assert not [e for e in events if e.type == "release.previewed"]
    assert not [e for e in events if e.type == "publish.planned"]
    assert not [e for e in events if e.type == "publish.executed"]
    # Nothing external moved: release branch, main and tags are untouched.
    assert _remote_head(bare, _RELEASE) == git_strip(
        repo, "rev-parse", f"{_RELEASE}"
    )
    assert _remote_head(bare, "main") == base
    assert _remote_tag(bare, _TAG) is None


def test_publish_requires_the_verified_event(tmp_path, monkeypatch):
    repo, bare, _base, baseline, candidate, store = _seed_host(tmp_path)
    monkeypatch.chdir(repo)
    executor = Executor(store, repo, "RUN")
    from tracks.executor.release_gate import build_operation_plan, generate_preview
    from tracks.executor.release_preview import canonical_digest as _cd
    from tracks.executor.sync_product import build_product

    product, error = build_product(repo, _RELEASE, baseline, candidate)
    assert error is None
    record = {
        **product,
        "evidence_digests": {
            ring: _cd({ring: "verification"}) for ring in EVIDENCE_RINGS
        },
    }
    plan = build_operation_plan(
        {
            "operations": {
                "post_release": {
                    "steps": [
                        "merge:main",
                        f"merge:{_RELEASE}:when=active_release_branch",
                        "tag:v{minor}.7",
                    ]
                }
            }
        },
        "post_release",
        version_facts("v0.8", "RUN"),
        active_release_branch=_RELEASE,
        sync_products=[record],
    )
    contract_obj, digest = _contract(repo)
    facts = dict(version_facts("v0.8", "RUN"))
    digests = {
        "artifact_digest": "",
        "evidence_digests": {"full_f": "sha256:" + "e1" * 32},
        "operation_plan_digest": _cd(plan),
        "contract_policy_digest": "sha256:" + digest,
    }
    preview = generate_preview(candidate, digests, [], plan)
    blob = store.write_audit_blob(preview)
    preview = dict(preview)
    preview["blob_ref"] = f".tracks/runtime/blobs/{blob}"
    store.append("RUN", "v0.8", "release.previewed", preview)
    store.append(
        "RUN",
        "v0.8",
        "release.decided",
        {
            "candidate_sha": candidate,
            "preview_digest": preview["preview_digest"],
            "action": "release",
            "actor": "human",
        },
    )

    authority, error = resolve_publish_authority(
        repo,
        list(store.events("RUN")),
        preview["preview_digest"],
        facts,
        store.home,
    )
    assert authority is None
    assert error == "sync_product_unverified"

    executor._do_execute_publish(
        _command(preview["preview_digest"]), store.state("RUN"), None, False
    )
    failed = [e for e in store.events("RUN") if e.type == "publish.failed"][-1]
    assert failed.payload["reason"] == "sync_product_unverified"
    assert _remote_head(bare, _RELEASE) == baseline
    assert _remote_head(bare, "main") != candidate


def test_publish_rejects_tampered_product_identity(tmp_path, monkeypatch):
    repo, bare, _base, baseline, candidate, store = _seed_host(tmp_path)
    monkeypatch.chdir(repo)
    executor = Executor(store, repo, "RUN")
    _contract_obj, preview = _seed_decided_preview(
        executor, repo, store, candidate, monkeypatch
    )
    # Tamper the preview plan's product identity in a byte-consistent way
    # (plan digest recomputed): the publish re-validation must still reject
    # the mismatch -- a preview is not a proof of verification.
    record = preview["operation_plan"]["sync_products"][0]
    tampered = {**record, "product_tree": "1" * 40}
    from tracks.executor.release_gate import generate_preview
    from tracks.executor.release_preview import canonical_digest

    plan = dict(preview["operation_plan"])
    plan["sync_products"] = [tampered]
    digests = {
        "artifact_digest": preview["artifact_digest"],
        "evidence_digests": preview["evidence_digests"],
        "operation_plan_digest": canonical_digest(plan),
        "contract_policy_digest": preview["contract_policy_digest"],
    }
    bad_preview = generate_preview(candidate, digests, [], plan)
    blob = store.write_audit_blob(bad_preview)
    bad_preview = dict(bad_preview)
    bad_preview["blob_ref"] = f".tracks/runtime/blobs/{blob}"
    store.append("RUN", "v0.8", "release.previewed", bad_preview)
    store.append(
        "RUN",
        "v0.8",
        "release.decided",
        {
            "candidate_sha": candidate,
            "preview_digest": bad_preview["preview_digest"],
            "action": "release",
            "actor": "human",
        },
    )

    executor._do_execute_publish(
        _command(bad_preview["preview_digest"]), store.state("RUN"), None, False
    )

    failed = [e for e in store.events("RUN") if e.type == "publish.failed"][-1]
    assert failed.payload["reason"] == "sync_product_tree_mismatch"
    assert not [e for e in store.events("RUN") if e.type == "publish.planned"]
    assert _remote_head(bare, _RELEASE) == baseline


def test_preview_refuses_foreign_candidate_product(tmp_path, monkeypatch):
    repo, _bare, _base, baseline, candidate, store = _seed_host(tmp_path)
    monkeypatch.chdir(repo)
    from tracks.executor.sync_product import build_product, canonical_digest

    product, error = build_product(repo, _RELEASE, baseline, candidate)
    assert error is None
    foreign = {**product, "source_candidate_sha": "f" * 40}
    foreign["evidence_digests"] = {
        ring: canonical_digest({ring: "ok"}) for ring in EVIDENCE_RINGS
    }
    store.append(
        "RUN",
        "v0.8",
        SYNC_VERIFIED,
        {"candidate_sha": candidate, "product": foreign, "evidence": {}},
    )
    contract_obj, digest = _contract(repo)

    preview = assemble_preview(
        repo,
        contract_obj,
        candidate,
        digest,
        version_facts("v0.8", "RUN"),
        list(store.events("RUN")),
        journey="post_release",
    )

    assert preview is None, "a foreign-candidate product must refuse the preview"


def test_baseline_move_after_approval_is_stale(tmp_path, monkeypatch):
    repo, bare, _base, baseline, candidate, store = _seed_host(tmp_path)
    monkeypatch.chdir(repo)
    executor = Executor(store, repo, "RUN")
    contract_obj, preview = _seed_decided_preview(
        executor, repo, store, candidate, monkeypatch
    )
    contract, digest = _contract(repo)

    events = list(store.events("RUN"))
    preview_event = next(
        e for e in reversed(events) if e.type == "release.previewed"
    )
    authorization = assess_release(repo, store.home, events, preview_event, "v0.8")
    assert authorization.stale_reason is None, authorization.detail

    # The release branch moves after the approval.
    git_strip(repo, "checkout", "-q", _RELEASE)
    (repo / "later.txt").write_text("later\n", encoding="utf-8")
    git_strip(repo, "add", "later.txt")
    git_strip(repo, "commit", "-qm", "later release work")
    moved = git_strip(repo, "rev-parse", "HEAD")
    git_strip(repo, "push", "-q", str(bare), f"{_RELEASE}:refs/heads/{_RELEASE}")
    git_strip(repo, "checkout", "-q", "main")

    authorization = assess_release(
        repo,
        store.home,
        list(store.events("RUN")),
        preview_event,
        "v0.8",
    )
    assert authorization.stale_reason == "sync_product_stale"
    assert authorization.gate_status["preview_stale"] is True

    authority, error = resolve_publish_authority(
        repo,
        list(store.events("RUN")),
        preview["preview_digest"],
        version_facts("v0.8", "RUN"),
        store.home,
    )
    assert authority is None
    assert error == "sync_product_stale"

    executor._do_execute_publish(
        _command(preview["preview_digest"]), store.state("RUN"), None, False
    )
    failed = [e for e in store.events("RUN") if e.type == "publish.failed"][-1]
    assert failed.payload["reason"] == "sync_product_stale"
    assert not [e for e in store.events("RUN") if e.type == "publish.planned"]
    assert _remote_head(bare, _RELEASE) == moved


def test_ancestor_rollback_after_approval_is_stale(tmp_path, monkeypatch):
    repo, bare, base, _baseline, candidate, store = _seed_host(tmp_path)
    monkeypatch.chdir(repo)
    executor = Executor(store, repo, "RUN")
    _contract_obj, preview = _seed_decided_preview(
        executor, repo, store, candidate, monkeypatch
    )
    # The remote rolls back to an ancestor of B: a plain push would accept
    # this, the approved-baseline check must not.
    git_strip(repo, "push", "-q", "-f", str(bare), f"{base}:refs/heads/{_RELEASE}")

    authority, error = resolve_publish_authority(
        repo,
        list(store.events("RUN")),
        preview["preview_digest"],
        version_facts("v0.8", "RUN"),
        store.home,
    )

    assert authority is None
    assert error == "sync_product_stale"
    assert _remote_head(bare, _RELEASE) == base


def test_crash_before_wal_then_resume_reuses_product(tmp_path, monkeypatch):
    repo, bare, _base, baseline, candidate, store = _seed_host(tmp_path)
    monkeypatch.chdir(repo)
    executor = Executor(store, repo, "RUN")
    _contract_obj, preview = _seed_decided_preview(
        executor, repo, store, candidate, monkeypatch
    )
    record = preview["operation_plan"]["sync_products"][0]

    # WAL前 crash: no publish event exists; the deterministic key/P of the
    # first executed attempt is the approved identity.
    assert not [e for e in store.events("RUN") if e.type == "publish.planned"]
    key = operation_idempotency_key(
        preview["preview_digest"], "merge", _RELEASE
    )

    executor._do_execute_publish(
        _command(preview["preview_digest"]), store.state("RUN"), None, False
    )

    planned = [
        e.payload
        for e in store.events("RUN")
        if e.type == "publish.planned" and e.payload.get("target") == _RELEASE
    ]
    assert len(planned) == 1
    assert planned[0]["idempotency_key"] == key
    assert planned[0]["product_sha"] == record["product_sha"]
    assert planned[0]["baseline_sha"] == record["baseline_sha"]
    assert planned[0]["product_tree"] == record["product_tree"]
    assert _remote_head(bare, _RELEASE) == record["product_sha"]


def test_crash_after_wal_before_push_reuses_same_product(tmp_path, monkeypatch):
    repo, bare, _base, baseline, candidate, store = _seed_host(tmp_path)
    monkeypatch.chdir(repo)
    executor = Executor(store, repo, "RUN")
    _contract_obj, preview = _seed_decided_preview(
        executor, repo, store, candidate, monkeypatch
    )
    record = preview["operation_plan"]["sync_products"][0]
    pushes: list = []
    real_push = publish_effects.push_sync_product

    def crashing_push(*args, **kwargs):
        pushes.append(args)
        raise RuntimeError("crash-before-push")

    monkeypatch.setattr(publish_effects, "push_sync_product", crashing_push)
    with pytest.raises(RuntimeError):
        executor._do_execute_publish(
            _command(preview["preview_digest"]), store.state("RUN"), None, False
        )
    assert pushes, "the crash lands after the WAL, before the push"
    planned = [
        e.payload
        for e in store.events("RUN")
        if e.type == "publish.planned" and e.payload.get("target") == _RELEASE
    ]
    assert len(planned) == 1
    assert planned[0]["product_sha"] == record["product_sha"]
    assert _remote_head(bare, _RELEASE) == baseline

    # Resume: the WAL identity is reused (no duplicate planned), the same P
    # is pushed once.
    monkeypatch.setattr(publish_effects, "push_sync_product", real_push)
    executor._do_execute_publish(
        _command(preview["preview_digest"]), store.state("RUN"), None, True
    )
    planned = [
        e.payload
        for e in store.events("RUN")
        if e.type == "publish.planned" and e.payload.get("target") == _RELEASE
    ]
    assert len(planned) == 1
    assert planned[0]["product_sha"] == record["product_sha"]
    executed = [
        e.payload
        for e in store.events("RUN")
        if e.type == "publish.executed" and e.payload.get("target") == _RELEASE
    ]
    assert executed[-1]["status"] == "done"
    assert _remote_head(bare, _RELEASE) == record["product_sha"]


def test_crash_after_push_before_event_reconciles_exact_product(
    tmp_path, monkeypatch
):
    repo, bare, _base, baseline, candidate, store = _seed_host(tmp_path)
    monkeypatch.chdir(repo)
    executor = Executor(store, repo, "RUN")
    _contract_obj, preview = _seed_decided_preview(
        executor, repo, store, candidate, monkeypatch
    )
    record = preview["operation_plan"]["sync_products"][0]
    real_push = publish_effects.push_sync_product
    calls: list = []

    def crashing_after_push(*args, **kwargs):
        calls.append(args)
        real_push(*args, **kwargs)
        raise RuntimeError("crash-after-push")

    monkeypatch.setattr(publish_effects, "push_sync_product", crashing_after_push)
    with pytest.raises(RuntimeError):
        executor._do_execute_publish(
            _command(preview["preview_digest"]), store.state("RUN"), None, False
        )
    assert _remote_head(bare, _RELEASE) == record["product_sha"]
    assert len(calls) == 1

    monkeypatch.setattr(publish_effects, "push_sync_product", real_push)
    executor._do_execute_publish(
        _command(preview["preview_digest"]), store.state("RUN"), None, True
    )

    executed = [
        e.payload
        for e in store.events("RUN")
        if e.type == "publish.executed" and e.payload.get("target") == _RELEASE
    ]
    assert executed[-1]["status"] == "reconciled_skip"
    assert executed[-1]["remote_check"]["object_id"] == record["product_sha"]
    assert _remote_head(bare, _RELEASE) == record["product_sha"]


def test_fast_forward_release_branch_needs_no_product(tmp_path, monkeypatch):
    repo, bare, _base, baseline, candidate, store = _seed_host(
        tmp_path, exact_branch=False
    )
    monkeypatch.chdir(repo)
    executor = Executor(store, repo, "RUN")
    _contract_obj, preview = _seed_decided_preview(
        executor, repo, store, candidate, monkeypatch
    )

    events = list(store.events("RUN"))
    assert not [e for e in events if e.type in ("sync_product.prepared", SYNC_VERIFIED)]
    plan = preview["operation_plan"]
    assert "sync_products" not in plan
    assert f"merge:{_RELEASE}" in plan["steps"]

    executor._do_execute_publish(
        _command(preview["preview_digest"]), store.state("RUN"), None, False
    )

    executed = {
        e.payload["target"]: e.payload
        for e in store.events("RUN")
        if e.type == "publish.executed"
    }
    assert executed[_RELEASE]["status"] == "done"
    assert executed[_RELEASE]["remote_check"]["object_id"] == candidate
    assert _remote_head(bare, _RELEASE) == candidate
    assert baseline != candidate
