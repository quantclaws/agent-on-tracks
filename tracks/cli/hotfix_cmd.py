"""``trac hotfix`` command face.

Extracted from :mod:`tracks.cli.main` for module-size compliance (C0302):
the hotfix entry (post-release|dev), the AWAIT_HUMAN sub-actions
(anchor / feature-route), and the FR-0248 ac_gap/spec_gap Human exit
consumed by ``trac approve``. ``tracks.cli.main`` re-exports every name.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from tracks import paths
from tracks.executor import git
from tracks.executor.executor import (
    _resolve_run_version,
    hotfix_entry_output,
    hotfix_entry_run,
    hotfix_feature_route,
    hotfix_human_anchor,
)
from tracks.store import Store

from .common import (
    _ensure_host_byproducts_gitignore,
    _err,
    _resolve_actor,
    writer_lock,
)

_HOTFIX_USAGE = (
    "usage: trac hotfix <issue> --scenario post-release|dev"
    "|anchor AC-FRXXXX-YY@<version> [...]|feature-route"
)


def cmd_hotfix(repo: Path, *args: str) -> int:
    """v0.6 trac hotfix (IF-HOTFIX-001, interfaces §2a): the entry form
    (`trac hotfix <issue> --scenario post-release|dev`), which synchronously
    drives the HOTFIX-TRIAGE run_loop, plus the two AWAIT_HUMAN sub-actions
    (`anchor AC-FRXXXX-YY@<ver> [...]` and `feature-route`)."""
    if not args:
        return _err(_HOTFIX_USAGE)
    home = paths.tracks_home(repo)
    store = Store(home)
    action = args[0]
    if action in ("anchor", "feature-route"):
        run_id = store.active_hotfix_run()
        if run_id is None:
            return _err("no active run")
        with writer_lock(home):
            state = store.state(run_id)
            if state.awaiting != "hotfix_triage":
                return _err(
                    f"run not awaiting hotfix triage (awaiting={state.awaiting or 'nothing'})"
                )
            if action == "anchor":
                refs = list(args[1:])
                if not refs:
                    return _err(_HOTFIX_USAGE)
                return hotfix_human_anchor(repo, store, run_id, refs)
            return hotfix_feature_route(repo, store, run_id)
    parsed = _parse_hotfix_entry(args)
    if parsed is None:
        return 2  # missing/illegal --scenario: non-blocking prompt, no run (#3)
    issue, scenario = parsed
    # Host-tree byproduct ignore (see cmd_init): hotfix hosts never passed
    # through `trac init`, so the entry ensures + commits the managed block
    # itself -- BEFORE the dirty-tree check, so the scaffold commit is not
    # mistaken for operator residue (untracked seeds stay legitimate, R3-01).
    if _ensure_host_byproducts_gitignore(repo):
        git(repo, "add", ".gitignore")
        git(repo, "commit", "-m", "trac: gitignore runtime test-command byproducts",
            "--only", "--", ".gitignore")
    # R3-01 (PRISM-FINAL-R3-01): the runtime seed (.tracks/ host-issues.json,
    # generated store state) is legitimately untracked. AC-FR0267-02 locked
    # exit: a dirty tracked tree is not refused at entry — the hotfix walk
    # reaches the M-VERIFY freeze, which lands
    # attention.required(dirty_tree) (needs_attention with recovery
    # guidance) at the contract's gate.
    with writer_lock(home):
        run_id = hotfix_entry_run(repo, store, issue, scenario)
    return hotfix_entry_output(store, run_id)


def _parse_hotfix_entry(args: tuple[str, ...]) -> tuple[int, str] | None:
    """Parse `trac hotfix <issue> --scenario post-release|dev`. A missing or
    illegal --scenario prints the non-blocking prompt (interfaces §2a #3) and
    returns None (caller exits 2 without creating a run or writing events)."""
    if len(args) == 1:
        issue_raw, scenario = args[0], None
    elif len(args) == 3 and args[1] == "--scenario":
        issue_raw, scenario = args[0], args[2]
    else:
        _err(_HOTFIX_USAGE)
        return None
    if scenario not in ("post-release", "dev"):
        _err(
            "--scenario is required: post-release (released) or dev "
            "(in-development); rerun with explicit --scenario"
        )
        return None
    try:
        issue = int(issue_raw)
    except ValueError:
        _err(_HOTFIX_USAGE)
        return None
    if issue < 1:
        _err(_HOTFIX_USAGE)
        return None
    return issue, scenario


def _hotfix_gap_exit(state) -> bool:
    """True when the active run is a hotfix run awaiting the Human ac_gap /
    spec_gap exit decision (FR-0248, IF-HOTFIX-009): the run carries a hotfix
    issue and the pending failure check is ac_gap or spec_gap."""
    return (
        state.hotfix_issue is not None
        and (state.last_failure or {}).get("check") in ("ac_gap", "spec_gap")
    )


def _approve_hotfix_gap(repo: Path, store: Store, run_id: str, state, actor: str | None) -> int:
    """trac approve for a hotfix ac_gap/spec_gap awaiting (interfaces §2c):
    human.approval (AC-required decision evidence) -> backlog.recorded
    {decision, issue} -> run.completed(ac_gap|spec_gap), no further
    M-TEST/M-IMPL dispatch (FR-0248)."""
    check = (state.last_failure or {}).get("check") or "ac_gap"
    actor = _resolve_actor(repo, actor)
    version = _resolve_run_version(state)
    store.append(
        run_id, version, "human.approval",
        {"actor": actor, "digest": None, "ts": datetime.now(timezone.utc).isoformat()},
    )
    store.append(
        run_id, version, "backlog.recorded",
        {"issue": state.hotfix_issue, "decision": check, "version": version},
    )
    store.append(run_id, version, "run.completed", {"terminal_state": check})
    print(f"approved {check} -> backlog/new feature for issue {state.hotfix_issue}")
    return 0
