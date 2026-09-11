"""Integration: FULL_F reuse judgment (FR-0268, IF-VERIFY-002/IF-EVIDENCE-001).

Each node creates a bounded temporary host contract whose declared FULL layer
commands really run a small result-producing script. The seed evidence is
therefore produced by ``Executor._execute_full_round``; the behavior under
test enters through the public WAL command ``judge_full_f_reuse``.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from tracks import paths
from tracks.executor.executor import Executor
from tracks.executor.m_verify import freeze_candidate, judge_full_f_reuse
from tracks.kernel.events import Command
from tracks.store import Store

pytestmark = pytest.mark.integration

_RUNNER = """\
from pathlib import Path
import sys

layer = sys.argv[sys.argv.index("--layer") + 1]
node = f"tests/{layer}/test_{layer}.py::test_{layer}"
root = Path(__file__).resolve().parent
if "--collect" in sys.argv:
    print(node)
    raise SystemExit(0)
result = Path(sys.argv[sys.argv.index("--result") + 1])
counter = root / ".tracks" / "runtime" / f"fullf-count-{layer}"
counter.parent.mkdir(parents=True, exist_ok=True)
count = int(counter.read_text() or "0") if counter.exists() else 0
counter.write_text(str(count + 1))
result.write_text(
    '<testsuite tests="1"><testcase classname="tests.%s.test_%s" '
    'name="test_%s" /></testsuite>' % (layer, layer, layer)
)
"""


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def _write_full_contract(repo: Path) -> None:
    (repo / "fullf_runner.py").write_text(_RUNNER, encoding="utf-8")
    for layer in ("unit", "integration", "e2e"):
        layer_dir = repo / "tests" / layer
        layer_dir.mkdir(parents=True, exist_ok=True)
        (layer_dir / f"test_{layer}.py").write_text("# declared FULL node\n", encoding="utf-8")
    python = sys.executable
    lines = [
        "[host-contract]",
        "version = 1",
        'language = "runtime-default"',
        'toolchain = "runtime-default"',
        'install = "true"',
        "",
    ]
    for layer in ("unit", "integration", "e2e"):
        lines.extend(
            [
                f"[{layer}]",
                'framework = "pytest"',
                f'paths = ["tests/{layer}"]',
                f'collect = "{python} fullf_runner.py --layer {layer} --collect"',
                f'run = "{python} fullf_runner.py --layer {layer} --result {{result}}"',
                f'run_selected = "{python} fullf_runner.py --layer {layer} --nodes {{nodes}} --result {{result}}"',
                'cwd = "."',
                "",
            ]
        )
    lines.extend(
        [
            "[nightly]",
            'schedule = "local"',
            'workflow = "fullf-local"',
            'job = "full"',
            'layers = ["unit", "integration", "e2e"]',
            'purpose = "FULL_F acceptance stand-in"',
            "",
        ]
    )
    (repo / ".tracks" / "projects" / "project.toml").write_text("\n".join(lines), encoding="utf-8")
    subprocess.run(
        ["git", "add", "-f", "fullf_runner.py", "tests", ".gitignore", ".tracks/projects/project.toml"],
        cwd=repo, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "fullf acceptance contract"],
        cwd=repo, check=True, capture_output=True,
    )


def _seed_real_full(host_repo, trac) -> tuple[str, str]:
    """Seed an actual passed FULL result, then enter an explicit M-VERIFY boundary."""
    assert trac("init").returncode == 0
    assert trac("start", "v0.8", stdin="FULL_F acceptance boundary").returncode == 0
    _write_full_contract(host_repo)
    candidate_sha = _git(host_repo, "rev-parse", "HEAD")
    store = Store(paths.tracks_home(host_repo))
    try:
        run_id = store.active_run()
        assert run_id
        version = store.state(run_id).version or "v0.8"
        store.append(run_id, version, "stage.entered", {"stage": "M-IMPL"})
        store.append(run_id, version, "baseline.frozen", {"status": "current", "digest": "baseline-fullf-test"})
        executor = Executor(store, host_repo, run_id)
        seed = executor._execute_full_round(
            Command(kind="check_island_2", command_id="full-seed"), store.state(run_id), "FULL_1", {}
        )
        assert seed["passed"] is True
        assert seed["serves_as_full_f"] is True
        assert len(seed["evidence_ids"]) == 3
        store.append(run_id, version, "stage.entered", {"stage": "M-VERIFY"})
        identity = freeze_candidate(host_repo)
        assert identity.candidate_sha == candidate_sha
        assert identity.clean_tree is True and identity.branch
        store.append(run_id, version, "candidate.frozen", {"candidate_sha": identity.candidate_sha, "clean_tree": identity.clean_tree, "branch": identity.branch})
        return run_id, identity.candidate_sha
    finally:
        store.close()


def _issue_judgment(host_repo: Path, run_id: str, candidate_sha: str) -> None:
    store = Store(paths.tracks_home(host_repo))
    try:
        Executor(store, host_repo, run_id).issue(Command(kind="judge_full_f_reuse", params={"candidate_sha": candidate_sha}))
    finally:
        store.close()


def _counter_total(host_repo: Path) -> int:
    runtime = host_repo / ".tracks" / "runtime"
    return sum(int(path.read_text(encoding="utf-8")) for path in runtime.glob("fullf-count-*"))


# AC-FR0268-01@v0.8 TRACKS-TRACE undrifted identity reuses full_f with evidence reused
def test_undrifted_identity_reuses_full_f(host_repo, trac, event_log):
    run_id, candidate_sha = _seed_real_full(host_repo, trac)
    assert _counter_total(host_repo) == 3, "Arrange must execute every declared FULL layer"
    before = event_log(run_id)
    seed_full = [e for e in before if e["type"] == "full.executed"][-1]
    assert seed_full["payload"]["passed"] is True
    assert seed_full["payload"]["serves_as_full_f"] is True
    producer_identity = seed_full["payload"].get("identity_basis")
    assert isinstance(producer_identity, (list, tuple)) and len(producer_identity) == 4

    _issue_judgment(host_repo, run_id, candidate_sha)
    after = event_log(run_id)
    reused = [e for e in after if e["seq"] > seed_full["seq"] and e["type"] == "evidence.reused" and e["payload"].get("kind") == "full_f"]
    assert reused, "matching executed FULL_F evidence must be reused"
    payload = reused[-1]["payload"]
    assert payload["candidate_sha"] == candidate_sha
    assert payload["identity_basis"] == list(producer_identity)
    assert _counter_total(host_repo) == 3, "reuse must not execute FULL again"
    status = trac("status")
    replay = trac("replay")
    assert status.returncode == 0 and "full_reuse=full_f" in status.stdout
    assert replay.returncode == 0 and "evidence.reused" in replay.stdout


# AC-FR0268-02@v0.8 TRACKS-TRACE drift or stale reruns full with full_executed
def test_drift_or_stale_reruns_full(host_repo, trac, event_log):
    # Keep the original pure contract check: an environment identity change
    # is independently a non-reuse decision, even before the CLI boundary.
    pure = judge_full_f_reuse(
        "c" * 40,
        {"identity_basis": ("tree", "command", "env", "selection")},
        {"tree": "tree", "command": "command", "env": "changed", "selection_id": "selection"},
        (),
    )
    assert pure.decision == "rerun" and pure.reason == "identity_mismatch"
    run_id, old_candidate = _seed_real_full(host_repo, trac)
    assert _counter_total(host_repo) == 3
    (host_repo / "README.md").write_text("FULL_F candidate drift\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=host_repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "fullf candidate drift"], cwd=host_repo, check=True, capture_output=True)
    new_candidate = _git(host_repo, "rev-parse", "HEAD")
    assert new_candidate != old_candidate
    store = Store(paths.tracks_home(host_repo))
    try:
        version = store.state(run_id).version or "v0.8"
        new_identity = freeze_candidate(host_repo)
        assert new_identity.candidate_sha == new_candidate
        assert new_identity.clean_tree is True and new_identity.branch
        store.append(run_id, version, "candidate.frozen", {"candidate_sha": new_identity.candidate_sha, "clean_tree": new_identity.clean_tree, "branch": new_identity.branch})
    finally:
        store.close()
    # This is a legal new-candidate boundary; repair-provenance navigation is
    # outside this component slice.
    _issue_judgment(host_repo, run_id, new_candidate)
    events = event_log(run_id)
    seed_full = [e for e in events if e["type"] == "full.executed"][0]
    new_events = [e for e in events if e["seq"] > seed_full["seq"]]
    assert not any(e["type"] == "evidence.reused" for e in new_events)
    reruns = [e for e in new_events if e["type"] == "full.executed"]
    assert reruns and reruns[-1]["payload"].get("candidate_sha") == new_candidate
    assert reruns[-1]["payload"].get("passed") is True
    assert _counter_total(host_repo) == 6, "drift must execute the declared FULL again"
    status = trac("status")
    assert "full_rerun" in status.stdout or "identity_mismatch" in status.stdout


# AC-FR0268-03@v0.8 TRACKS-TRACE stale evidence not reused as passed basis
def test_stale_evidence_not_reused(host_repo, trac, event_log):
    run_id, candidate_sha = _seed_real_full(host_repo, trac)
    assert _counter_total(host_repo) == 3
    seed_full = [e for e in event_log(run_id) if e["type"] == "full.executed"][-1]
    producer_identity = seed_full["payload"].get("identity_basis")
    store = Store(paths.tracks_home(host_repo))
    try:
        version = store.state(run_id).version or "v0.8"
        # Explicit historical stale fact is the permitted Arrange precondition
        # for stale consumption; this does not claim to test stale production.
        store.append(
            run_id,
            version,
            "evidence.staled",
            {
                "candidate_sha": candidate_sha,
                "reason": "prior_stale",
                "source": "full_f",
                "source_seq": seed_full["seq"],
                **({"identity_basis": producer_identity} if producer_identity is not None else {}),
            },
        )
    finally:
        store.close()
    _issue_judgment(host_repo, run_id, candidate_sha)
    events = event_log(run_id)
    stale = [e for e in events if e["type"] == "evidence.staled"]
    assert stale and stale[0]["payload"]["candidate_sha"] == candidate_sha
    assert stale[0]["payload"]["source_seq"] == seed_full["seq"]
    assert not any(e["type"] == "evidence.reused" and e["payload"].get("candidate_sha") == candidate_sha for e in events)
    reruns = [e for e in events if e["type"] == "full.executed"]
    assert len(reruns) >= 2 and reruns[-1]["payload"].get("reason") == "stale"
    assert _counter_total(host_repo) == 6, "stale evidence must cause a real FULL rerun"
    replay = trac("replay")
    assert replay.returncode == 0 and "evidence.staled" in replay.stdout


# AC-FR0268-03@v0.8 TRACKS-TRACE later stale invalidates an already reused FULL_F
def test_later_stale_invalidates_prior_reuse(host_repo, trac, event_log):
    run_id, candidate_sha = _seed_real_full(host_repo, trac)
    _issue_judgment(host_repo, run_id, candidate_sha)
    prior = event_log(run_id)
    reuse = [e for e in prior if e["type"] == "evidence.reused"][-1]
    assert _counter_total(host_repo) == 3
    store = Store(paths.tracks_home(host_repo))
    try:
        version = store.state(run_id).version or "v0.8"
        stale = store.append(
            run_id, version, "evidence.staled",
            {"candidate_sha": candidate_sha, "source": "full_f",
             "source_seq": reuse["seq"], "reason": "later_stale"},
        )
    finally:
        store.close()
    _issue_judgment(host_repo, run_id, candidate_sha)
    fresh = [e for e in event_log(run_id) if e["seq"] > stale.seq]
    assert not any(e["type"] == "evidence.reused" for e in fresh)
    assert _counter_total(host_repo) == 6
    assert any(e["type"] == "full.executed" and e["payload"].get("passed") is True for e in fresh)
