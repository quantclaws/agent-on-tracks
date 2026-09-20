"""Card-size estimator for v0.9 task graphs (M5 dispatch budget = 16384B).

Calibrated against the measured failed T-001 card (attempt-3 graph,
verdict.failed seq 765: total 23349B; test_tasks=11272B, task=3517B,
manifest=1842B, evidence_contract=1331B, envelope=1246B, test_refs=798B,
others=4343B).

card = FIXED + test_tasks + task_payload + manifest + test_refs
- test_tasks: [{"ac_id", "anchors": acceptance_refs, "if_ids"} per ac_ref]
- task_payload: full task object (incl. test_refs = unit+acceptance combined)
- manifest: base 1570B + ~48B per allowed path (measured: 6 files = 1842B)
- test_refs section: ~80B per acceptance anchor
- FIXED = 6920B (envelope 1246 + evidence_contract 1331 + others 4343)
"""
import json

FIXED = 6920
BUDGET = 16384
TARGET = 14500  # safety margin


def task_payload(t: dict) -> dict:
    return {
        "task_id": t["task_id"],
        "issue_number": t["issue_number"],
        "description": t["description"],
        "ac_refs": t["ac_refs"],
        "fr_refs": t["fr_refs"],
        "if_ids": t["if_ids"],
        "test_refs": [*t["unit_refs"], *t["acceptance_refs"]],
        "unit_refs": t["unit_refs"],
        "acceptance_refs": t["acceptance_refs"],
        "schema": 2,
        "scope_boundary": t["scope_boundary"],
        "depends_on": t["depends_on"],
        "batch": t["batch"],
        "parallel": t["parallel"],
        "budget": t["budget"],
        "deferred_refs": t["deferred_refs"],
        "integration": bool(t.get("integration", False)),
        "debt": [],
    }


def card_bytes(t: dict) -> dict:
    slice_ = [
        {"ac_id": ac, "anchors": list(t["acceptance_refs"]), "if_ids": list(t["if_ids"])}
        for ac in t["ac_refs"]
    ]
    tt = len(json.dumps(slice_, ensure_ascii=False))
    tp = len(json.dumps(task_payload(t), ensure_ascii=False))
    n_files = len([x for x in t["scope_boundary"].split(",") if x.strip()])
    manifest = 1570 + 48 * n_files
    test_refs = 80 * len(t["acceptance_refs"])
    total = FIXED + tt + tp + manifest + test_refs
    return {"total": total, "test_tasks": tt, "task": tp, "manifest": manifest,
            "test_refs": test_refs, "ok": total <= TARGET}


def report(tasks: list[dict]) -> bool:
    all_ok = True
    for t in tasks:
        m = card_bytes(t)
        flag = "OK " if m["ok"] else "OVER"
        if not m["ok"]:
            all_ok = False
        print(f"{t['task_id']:>6} {flag} total={m['total']:>6}B "
              f"(tt={m['test_tasks']}, task={m['task']}, ac={len(t['ac_refs'])}, "
              f"anchors={len(t['acceptance_refs'])}, files={len([x for x in t['scope_boundary'].split(',') if x.strip()])})")
    return all_ok
