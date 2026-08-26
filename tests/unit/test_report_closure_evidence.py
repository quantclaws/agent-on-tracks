"""IF-CLOSURE-001 RED: candidate-bound closure evidence chain in reports.

Spec FR-0265 user-visible outcome: ``trac status`` / ``trac replay`` let the
operator audit every AC's closure evidence chain; architecture §1.0.9 routes
the v0.7 status-report rendering through the extension's renderer capability.
Consumers therefore expect the workflow report to surface, in a dedicated
closure section, the same §1i records the checker produced (per-AC
``ac/outlet/nodes/baseline/mutation/full_pass`` plus top-level
``candidate-bound`` and hard errors).

RED note (T-013): today ``report._markdown`` renders closure payloads only as
raw JSON inside the generic event timeline -- no structured closure section
exists, so the discriminating assertions below fail while run seeding itself
works. The closure evidence arrives on a ``full.executed`` event payload
(keyed ``gate``/``closure``/``records``, mirroring ``ClosureReport``).
"""

from __future__ import annotations

import re
from pathlib import Path

from tracks import paths
from tracks.cli.main import cmd_report
from tracks.store import Store

RUN_ID = "run-v07-evidence"
DIG = "sha256:" + "a" * 64


def _record(ac: str, nodes=("opaque-node",)) -> dict:
    return {
        "ac": ac,
        "outlet": "IF-CLOSURE-001",
        "nodes": list(nodes),
        "baseline_evidence": "evidence-base",
        "mutation_evidence": "evidence-mutation",
        "full_pass_evidence": "evidence-full-pass",
        "candidate_digest": DIG,
        "status": "pass",
    }


def _closure_payload(records: list[dict], hard_errors: list[str]) -> dict:
    passed = bool(records) and all(r["status"] == "pass" for r in records)
    return {
        "gate": "ISLAND_GATE_2",
        "round": "FULL_1",
        "passed": passed and not hard_errors,
        "closure": "candidate-bound",
        "status": "pass" if passed and not hard_errors else "fail",
        "hard_errors": hard_errors,
        "records": records,
    }


def _seed_run(repo: Path, events: list[tuple[str, dict]]) -> None:
    home = paths.tracks_home(repo)
    store = Store(home)
    try:
        store.append(RUN_ID, "v0.7", "story.requested", {"raw_chars": 1})
        for event_type, payload in events:
            store.append(RUN_ID, "v0.7", event_type, payload)
    finally:
        store.close()


def _closure_section(markdown: str) -> str:
    """Slice the dedicated closure section out of the rendered report."""
    match = re.search(r"(?m)^#{1,3} .*[Cc]losure.*$", markdown)
    assert match, "workflow report lacks a candidate-bound closure section"
    tail = markdown[match.start() :]
    nxt = re.search(r"(?m)^#{1,3} ", tail[1:])
    return tail[: nxt.start() + 1] if nxt else tail


# Spec FR-0265: operator can audit each AC's closure chain from the report.
def test_report_renders_per_ac_candidate_bound_chain(host_repo):
    repo = host_repo
    _seed_run(
        repo,
        [("full.executed", _closure_payload([_record("AC-FR0265-01")], []))],
    )
    out = repo / ".tracks" / "report-out"
    assert cmd_report(repo, "--run-id", RUN_ID, "--output", str(out)) == 0
    section = _closure_section((out / "report.md").read_text(encoding="utf-8"))
    assert "candidate-bound" in section
    assert "AC-FR0265-01" in section
    # the bound chain环节 are human-auditable, not just a JSON blob
    for evidence_id in ("evidence-base", "evidence-mutation", "evidence-full-pass"):
        assert evidence_id in section, f"chain element {evidence_id} not shown"


# §1i / NFR-0140-04: blocking closure reasons stay auditable in the report.
def test_report_shows_closure_hard_errors(host_repo):
    repo = host_repo
    record = _record("AC-FR0265-02")
    record["nodes"] = []
    record["status"] = "fail"
    _seed_run(
        repo,
        [("full.executed", _closure_payload([record], ["node_missing"]))],
    )
    out = repo / ".tracks" / "report-out"
    assert cmd_report(repo, "--run-id", RUN_ID, "--output", str(out)) == 0
    section = _closure_section((out / "report.md").read_text(encoding="utf-8"))
    assert "AC-FR0265-02" in section
    assert "node_missing" in section
