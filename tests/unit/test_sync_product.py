"""FR-0277-02: strict sync product preparation, identity and verification.

Local git repos only: the product is a deterministic merge-base-aware merge
commit; identity validation re-derives it; the verification battery runs the
host-declared gates / complete-test ladder / security scans / required-CI
ring on an isolated product checkout.
"""

from __future__ import annotations

import os
import subprocess

from tests.unit.helpers import git_repo, git_strip
from tracks.executor import sync_product as sp
from tracks.executor.host_contract import (
    HostContract,
    LocalGateDecl,
    SecurityScanDecl,
    VersionDecl,
)

_RELEASE = "releases/v0.8"


def _bare(tmp_path):
    bare = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "-q", "--bare", str(bare)], check=True, capture_output=True
    )
    return bare


def _seed_diverged(tmp_path, *, conflicting=False):
    """candidate C on main + an independently advanced ``releases/v0.8``."""
    repo = git_repo(tmp_path, gitignore=True)
    bare = _bare(tmp_path)
    git_strip(repo, "remote", "add", "origin", str(bare))
    base = git_strip(repo, "rev-parse", "HEAD")
    git_strip(repo, "checkout", "-q", "-b", _RELEASE, base)
    payload = "release edit\n" if conflicting else None
    (repo / "release_only.txt").write_text(
        payload or "release\n", encoding="utf-8"
    )
    git_strip(repo, "add", "release_only.txt")
    git_strip(repo, "commit", "-qm", "release only")
    baseline = git_strip(repo, "rev-parse", "HEAD")
    git_strip(repo, "push", "-q", str(bare), f"{_RELEASE}:refs/heads/{_RELEASE}")
    git_strip(repo, "checkout", "-q", "main")
    if conflicting:
        (repo / "release_only.txt").write_text("main edit\n", encoding="utf-8")
        git_strip(repo, "add", "release_only.txt")
    else:
        (repo / "fix.txt").write_text("fix\n", encoding="utf-8")
        git_strip(repo, "add", "fix.txt")
    git_strip(repo, "commit", "-qm", "fix")
    candidate = git_strip(repo, "rev-parse", "HEAD")
    return repo, bare, baseline, candidate


def _gate(kind="quality", command="true"):
    return LocalGateDecl(
        kind=kind,
        source="command",
        command=command,
        categories=(),
        result_channel="exit_code",
        timeout_seconds=30,
    )


def _scan(scan_id="scan", command="true"):
    return SecurityScanDecl(
        scan_id=scan_id,
        tool="tool",
        tool_version="1",
        install="",
        command=command,
        result_channel="exit_code",
        threshold="violations=0",
        timeout_seconds=30,
    )


def _contract(
    *,
    gates=None,
    scans=None,
    ci=None,
):
    return HostContract(
        contract_version=1,
        language="test",
        toolchain="test",
        install="",
        local_gates=tuple(gates if gates is not None else (_gate(),)),
        version=VersionDecl("", "", ""),
        build_command="",
        build_artifact="",
        smoke=(),
        security_scans=tuple(scans if scans is not None else (_scan(),)),
        ci=dict(ci or {}),
        tracker={},
        operations={},
    )


_PROJECT_TOML = """\
[unit]
framework = "pytest"
paths = ["tests/unit/"]
collect = "true"
run = "{unit_run}"
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
"""


def _write_project(repo, *, unit_run="true {result}"):
    projects = repo / ".tracks" / "projects"
    projects.mkdir(parents=True, exist_ok=True)
    (projects / "project.toml").write_text(
        _PROJECT_TOML.replace("{unit_run}", unit_run), encoding="utf-8"
    )


def _product(tmp_path, **kwargs):
    repo, bare, baseline, candidate = _seed_diverged(tmp_path, **kwargs)
    record, error = sp.build_product(repo, _RELEASE, baseline, candidate)
    assert error is None, error
    return repo, bare, baseline, candidate, record


# -- identity -----------------------------------------------------------------


def test_sync_need_classification(tmp_path):
    repo, _bare, baseline, candidate = _seed_diverged(tmp_path)
    assert sp.sync_need(repo, baseline, candidate) == ("product", None)
    assert sp.sync_need(repo, candidate, candidate) == ("contained", None)
    parent = git_strip(repo, "rev-parse", "HEAD~1")
    assert sp.sync_need(repo, parent, candidate) == ("fast_forward", None)


def test_build_product_is_deterministic_with_two_parents(tmp_path):
    repo, _bare, baseline, candidate, record = _product(tmp_path)
    assert record["baseline_sha"] == baseline
    assert record["source_candidate_sha"] == candidate
    assert record["step"] == f"merge:{_RELEASE}"
    shape, error = sp.product_shape(repo, record["product_sha"])
    assert error is None
    assert shape["parents"] == [baseline, candidate]
    assert shape["tree"] == record["product_tree"]
    again, error = sp.build_product(repo, _RELEASE, baseline, candidate)
    assert error is None
    assert again["product_sha"] == record["product_sha"], (
        "crash recovery must rebuild the identical product"
    )


def test_validate_record_accepts_the_prepared_product(tmp_path):
    repo, _bare, _baseline, candidate, record = _product(tmp_path)
    full = {
        **record,
        "evidence_digests": dict.fromkeys(sp.EVIDENCE_RINGS, "sha256:" + "a" * 64),
    }
    assert sp.validate_record(repo, full, candidate) is None


def test_validate_record_rejects_foreign_candidate(tmp_path):
    repo, _bare, _baseline, candidate, record = _product(tmp_path)
    full = {
        **record,
        "evidence_digests": dict.fromkeys(sp.EVIDENCE_RINGS, "sha256:" + "a" * 64),
    }
    assert (
        sp.validate_record(repo, full, "f" * 40)
        == "sync_product_candidate_mismatch"
    )


def test_verified_records_respect_stale_barrier():
    from tests.unit.helpers import ev

    candidate = "c" * 40
    record = {
        "target": _RELEASE,
        "baseline_sha": "b" * 40,
        "source_candidate_sha": candidate,
        "product_sha": "p" * 40,
        "product_tree": "t" * 40,
        "evidence_digests": {"gates": "sha256:" + "a" * 64},
    }
    verified = ev(
        1, sp.SYNC_VERIFIED, {"candidate_sha": candidate, "product": record}
    )
    stale = ev(2, "evidence.staled", {})
    events = [verified, stale]
    assert sp.verified_records(events, candidate) == []
    assert sp.record_verified(events, record, candidate) is False

    fresh = ev(
        3, sp.SYNC_VERIFIED, {"candidate_sha": candidate, "product": record}
    )
    events.append(fresh)
    assert sp.verified_records(events, candidate) == [record]
    assert sp.record_verified(events, record, candidate) is True


def test_validate_record_rejects_parent_and_tree_mismatch(tmp_path):
    repo, _bare, baseline, candidate, record = _product(tmp_path)
    full = {
        **record,
        "evidence_digests": dict.fromkeys(sp.EVIDENCE_RINGS, "sha256:" + "a" * 64),
    }
    swapped = {**full, "baseline_sha": candidate, "source_candidate_sha": baseline}
    assert sp.validate_record(repo, swapped, baseline) == "sync_product_parent_mismatch"
    wrong_tree = {**full, "product_tree": "1" * 40}
    assert sp.validate_record(repo, wrong_tree, candidate) == "sync_product_tree_mismatch"


def test_validate_record_rejects_recomputed_mismatch(tmp_path):
    repo, _bare, baseline, candidate, record = _product(tmp_path)
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "tracks-runtime",
        "GIT_AUTHOR_EMAIL": "tracks-runtime@localhost",
        "GIT_AUTHOR_DATE": "2001-01-01T00:00:00+00:00",
        "GIT_COMMITTER_NAME": "tracks-runtime",
        "GIT_COMMITTER_EMAIL": "tracks-runtime@localhost",
        "GIT_COMMITTER_DATE": "2001-01-01T00:00:00+00:00",
    }
    other = subprocess.run(
        [
            "git",
            "commit-tree",
            record["product_tree"],
            "-p",
            baseline,
            "-p",
            candidate,
            "-m",
            "different product",
        ],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        env=env,
    ).stdout.strip()
    full = {
        **record,
        "product_sha": other,
        "evidence_digests": dict.fromkeys(sp.EVIDENCE_RINGS, "sha256:" + "a" * 64),
    }
    assert (
        sp.validate_record(repo, full, candidate)
        == "sync_product_recompute_mismatch"
    )


def test_validate_record_requires_evidence_rings(tmp_path):
    repo, _bare, _baseline, candidate, record = _product(tmp_path)
    assert (
        sp.validate_record(repo, {**record, "evidence_digests": {}}, candidate)
        == "sync_product_evidence_missing"
    )
    partial = {
        **record,
        "evidence_digests": {"gates": "sha256:" + "a" * 64},
    }
    assert (
        sp.validate_record(repo, partial, candidate)
        == "sync_product_evidence_missing"
    )


def test_sync_conflict_product_is_not_built(tmp_path):
    repo, _bare, baseline, candidate = _seed_diverged(tmp_path, conflicting=True)
    record, error = sp.build_product(repo, _RELEASE, baseline, candidate)
    assert record is None
    assert error == "merge_conflict"


# -- verification battery -----------------------------------------------------


def _prepared_contract_repo(tmp_path):
    repo, _bare, _baseline, candidate, record = _product(tmp_path)
    _write_project(repo)
    full = {
        **record,
        "evidence_digests": dict.fromkeys(sp.EVIDENCE_RINGS, "sha256:" + "a" * 64),
    }
    return repo, candidate, full


def test_verify_product_passes_all_rings(tmp_path):
    repo, candidate, record = _prepared_contract_repo(tmp_path)
    evidence, details, error = sp.verify_product(
        repo, _contract(ci={"required_checks": []}), record, {}, "v0.8"
    )
    assert error is None, error
    assert set(evidence) == set(sp.EVIDENCE_RINGS)
    assert details["gates"][0]["status"] == "passed"
    assert details["tests"][0]["layer"] == "unit"
    assert details["security"][0]["status"] == "passed"
    assert details["ci"]["status"] == "not_required"


def test_verify_product_blocks_on_declared_gate_failure(tmp_path):
    repo, _candidate, record = _prepared_contract_repo(tmp_path)
    _evidence, _details, error = sp.verify_product(
        repo, _contract(gates=(_gate(command="false"),)), record, {}, "v0.8"
    )
    assert error == "sync_gate_failed:quality"


def test_verify_product_blocks_on_complete_test_failure(tmp_path):
    repo, _candidate, record = _prepared_contract_repo(tmp_path)
    _write_project(repo, unit_run="false {result}")
    _evidence, _details, error = sp.verify_product(
        repo, _contract(), record, {}, "v0.8"
    )
    assert error == "sync_tests_failed:unit"


def test_verify_product_blocks_on_security_failure(tmp_path):
    repo, _candidate, record = _prepared_contract_repo(tmp_path)
    _evidence, _details, error = sp.verify_product(
        repo, _contract(scans=(_scan(command="false"),)), record, {}, "v0.8"
    )
    assert error == "sync_security_failed:scan"


def test_verify_product_blocks_on_unverifiable_required_ci(tmp_path):
    repo, _candidate, record = _prepared_contract_repo(tmp_path)
    _evidence, _details, error = sp.verify_product(
        repo, _contract(ci={"required_checks": ["lint"]}), record, {}, "v0.8"
    )
    assert error == "ci_unverifiable"


def test_verify_product_runs_declared_ci_verifier(tmp_path):
    repo, _candidate, record = _prepared_contract_repo(tmp_path)
    evidence, details, error = sp.verify_product(
        repo,
        _contract(ci={"required_checks": ["lint"], "verify_command": "true"}),
        record,
        {},
        "v0.8",
    )
    assert error is None, error
    assert details["ci"]["status"] == "passed"
    assert details["ci"]["required_checks"] == ["lint"]
    assert evidence["ci"].startswith("sha256:")


def test_verify_product_blocks_on_ci_verifier_failure(tmp_path):
    repo, _candidate, record = _prepared_contract_repo(tmp_path)
    _evidence, _details, error = sp.verify_product(
        repo,
        _contract(
            ci={"required_checks": ["lint"], "verify_command": "false"}
        ),
        record,
        {},
        "v0.8",
    )
    assert error == "sync_ci_failed"


def test_verify_product_marks_undeclared_test_ladder(tmp_path):
    repo, _candidate, record = _prepared_contract_repo(tmp_path)
    (repo / ".tracks" / "projects" / "project.toml").unlink()
    evidence, details, error = sp.verify_product(
        repo, _contract(), record, {}, "v0.8"
    )
    assert error is None, error
    assert details["tests"] == [{"layer": "undeclared"}]
    assert evidence["tests"].startswith("sha256:")
