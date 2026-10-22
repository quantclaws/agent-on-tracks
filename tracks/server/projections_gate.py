"""Awaiting-gate, diagnosis, and return-options projections (§1k/§1m).

Run-detail gate briefs, human-thread pending lists, and the return-option
legality view shared with the CLI. Public names are re-exported by
``tracks.server.projections``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from tracks import paths
from tracks.discuss.gate import adjudication_owner, is_human_owner
from tracks.discuss.parser import parse_threads
from tracks.kernel.events import EventEnvelope
from tracks.kernel.machine import State
from tracks.kernel.stage_registry import _STAGES

from .projections_core import _run_events, _state_of

# -- v0.11 awaiting gate + return options (§1k/§1m) ---------------------------

# §1k.2 type -> primary_action closed mapping (映射外无入口). The
# AWAIT_RELEASE member is the M-RELEASE substate-shaped gate.
_AWAIT_RELEASE = "AWAIT_RELEASE"
_AWAITING_PRIMARY_ACTIONS = {"triage": "triage", "review": "review",
    "review_pending_threads": "review", "approval": "approve", "escalation": "retry",
    "rollback": "return", "hotfix_triage": "guidance",}
_AWAITING_REASON_LABELS = {_AWAIT_RELEASE: "发布三择一决策（release / delay / return）",
    "triage": "story triage 裁决（go / no-go / park）",
    "review": "文档阶段评审（comment / no-comment）",
    "review_pending_threads": "挂起线程恢复重评（comment / no-comment）",
    "approval": "批准基线（approve / revise）",
    "escalation": "失败升级处置（retry / return）",
    "rollback": "回滚目标确认（approve 选定目标）",
    "hotfix_triage": "hotfix 锚定人工处置（owning surface 指引）",}


def _awaiting_type(state: State) -> str | None:
    """The §1k.1 gate type: the M-RELEASE gate substate maps onto the
    AWAIT_RELEASE type, else a closed awaiting member; None when the run
    awaits nothing."""
    if state.substate == "AWAITING_RELEASE":
        return _AWAIT_RELEASE
    return state.awaiting if state.awaiting in _AWAITING_PRIMARY_ACTIONS else None


def _run_branch(state: State) -> str | None:
    """The run's git branch identity (hotfix runs live on ``fix/{issue}``,
    IF-HOTFIX-006): projected run-plane state, no new data source. None for
    non-hotfix runs (the Releases registry owns their release-branch face)."""
    if state.hotfix_issue is not None:
        return f"fix/{state.hotfix_issue}"
    return None


# -- OOB gate diagnosis (decision brief) -------------------------------------

_DIAGNOSIS_ATTEMPTS_MAX = 3
_DIAGNOSIS_CAUSE_BY_CHECK = {
    "island": "plan", "plan_defect": "plan", "test_tasks": "plan",
    "taskgraph": "plan", "scope": "plan",
    "test_defect": "test", "full_suite": "test", "red_defect": "test",
    "red_invalid": "test", "collection": "test", "anchor_static": "test",
    "impl_defect": "implementation",
    "stub_gap": "design", "baseline_defect": "design",
    "public_interface": "design",
    "ac_gap": "acceptance",
    "spec_gap": "spec",
    "contract_error": "runtime", "collect_defect": "runtime",
    "test_freeze_contamination": "runtime",
    "reply_format_error": "runtime", "manifest_malformed": "runtime",
    "evidence_malformed": "runtime", "worktree": "runtime",
    "publish_error": "runtime", "diagnose_contract_violation": "runtime",
    "lineage": "process", "trace": "process", "commit": "process",
}
_DIAGNOSIS_CAUSE_LABELS = {
    "plan": "实现计划未通过校验",
    "test": "测试资产或测试执行未通过",
    "implementation": "实现未满足已定义的测试",
    "design": "设计缺失或设计不可行",
    "acceptance": "验收标准需要修改",
    "spec": "规格需要修改",
    "runtime": "运行环境或采集流程的问题",
    "process": "阶段流程校验未通过",
    "unknown": "本阶段校验未通过",
}
_DIAGNOSIS_SUGGESTED_ACTIONS = {
    "plan": "replan", "test": "return_m_test", "implementation": "retry",
    "design": "redesign", "acceptance": "return_m_test", "spec": "return_m_test",
    "runtime": "retry", "process": "retry", "unknown": "unknown",
}
_DIAGNOSIS_RETURN_CANDIDATES = {
    "plan": ["M-DESIGN"], "test": ["M-TEST"], "implementation": ["M-DESIGN"],
    "design": ["M-DESIGN"], "acceptance": ["M-ACC"], "spec": ["M-SPEC"],
    "runtime": [], "process": ["M-DESIGN"], "unknown": [],
}
_DIAGNOSIS_DIGITS = re.compile(r"\d+")
_DIAGNOSIS_HEX = re.compile(r"\b[0-9a-fA-F]{7,40}\b")
_DIAGNOSIS_PATH = re.compile(r"(?:/[\w.\-]+)+")


def _diagnosis_cause(check: object) -> str:
    """Map a verdict check onto the closed coarse-cause set."""
    return _DIAGNOSIS_CAUSE_BY_CHECK.get(str(check or ""), "unknown")


def _failure_signature(payload: dict) -> str:
    """check + reason with digits/ids/paths normalized away."""
    payload = payload if isinstance(payload, dict) else {}
    reason = str(payload.get("reason") or "")
    reason = _DIAGNOSIS_PATH.sub("<path>", reason)
    reason = _DIAGNOSIS_HEX.sub("<sha>", reason)
    reason = _DIAGNOSIS_DIGITS.sub("#", reason)
    return f"{payload.get('check') or ''}\n{reason}"


def _stage_failure_window(
    events: list[EventEnvelope], stage: str | None,
) -> list[tuple[int, EventEnvelope]]:
    """Failures after the last ``stage.entered`` for the current stage."""
    start = -1
    for index, event in enumerate(events):
        payload = event.payload or {}
        if event.type == "stage.entered" and str(payload.get("stage") or "") == str(stage or ""):
            start = index
    window = events[start + 1:] if start >= 0 else events
    offset = start + 1 if start >= 0 else 0
    return [
        (index + offset, event) for index, event in enumerate(window)
        if event.type == "verdict.failed"
    ]


def _new_information_between(
    events: list[EventEnvelope], earlier_index: int, earlier: dict, later_index: int,
) -> str | None:
    """What genuinely NEW information arrived between two same-check failures.

    Only two sources count as new information:

    - a reviewer verdict that carries findings (``prism.verdict`` with
      ``findings``/``review_summary``) -- e.g. Prism spotted a small bug, so
      the next attempt has something to fix;
    - a failure whose SIGNATURE differs from the previous one -- the failure
      itself changed shape.

    The agent's own per-round commits (``result.checkpointed`` /
    ``test.written``) do NOT count: a fresh sha is what "try again" PRODUCES,
    so treating it as new information would make every repeated attempt look
    like progress and would always recommend retrying a non-converging loop.
    """
    previous_signature = _failure_signature(earlier)
    for event in events[earlier_index + 1:later_index]:
        payload = event.payload or {}
        if event.type == "prism.verdict":
            if payload.get("findings") or payload.get("review_summary"):
                return "prism"
        elif (
            event.type == "verdict.failed"
            and _failure_signature(payload) != previous_signature
        ):
            return "verdict"
    return None


def _prism_guidance_after(events: list[EventEnvelope], index: int) -> bool:
    """A reviewer verdict with findings/summary arrived after ``index``."""
    for event in events[index + 1:]:
        if event.type != "prism.verdict":
            continue
        payload = event.payload or {}
        if payload.get("findings") or payload.get("review_summary"):
            return True
    return False


def _retry_worthiness(
    failures: list[tuple[int, EventEnvelope]], events: list[EventEnvelope],
) -> tuple[bool, str]:
    """Convergence judgement over same-check failures in the current stage."""
    if not failures:
        return False, "本轮尚无失败记录，无法判断重试是否会有不同"
    latest_index, _latest_event = failures[-1]
    if _prism_guidance_after(events, latest_index):
        return True, "上一轮评审发现了新问题，重试有可能不同"
    if len(failures) == 1:
        return True, "首次失败，重试可能得到不同结果"
    later_index, later_event = failures[-1]
    earlier_index, earlier_event = failures[-2]
    later_signature = _failure_signature(later_event.payload or {})
    earlier_signature = _failure_signature(earlier_event.payload or {})
    if later_signature != earlier_signature:
        return True, "失败原因已变化，重试有可能不同"
    new_information = _new_information_between(
        events, earlier_index, earlier_event.payload or {}, later_index)
    if new_information is not None:
        if new_information == "prism":
            return True, "上一轮评审发现了新问题，重试有可能不同"
        return True, "两轮失败之间出现了新信息，重试有可能不同"
    consecutive = 1
    for _index, event in reversed(failures[:-1]):
        if _failure_signature(event.payload or {}) != later_signature:
            break
        consecutive += 1
    return False, f"连续 {consecutive} 次同一失败签名且无新信息，重试不会不同"


def _latest_prism_summary(events: list[EventEnvelope]) -> str:
    """The latest reviewer summary, or "" when none carries text."""
    for event in reversed(events):
        if event.type != "prism.verdict":
            continue
        summary = (event.payload or {}).get("review_summary")
        if isinstance(summary, str) and summary.strip():
            return summary.strip()
    return ""


def _latest_failure_reason(events: list[EventEnvelope]) -> str:
    """The latest failed-verdict reason, or "" when none carries text."""
    for event in reversed(events):
        if event.type != "verdict.failed":
            continue
        reason = (event.payload or {}).get("reason")
        if isinstance(reason, str) and reason.strip():
            return reason.strip()
    return ""


def _latest_agent_report(events: list[EventEnvelope]) -> str:
    """The latest outcome self-report, or "" when none carries text."""
    for event in reversed(events):
        if event.type != "outcome.received":
            continue
        report = (event.payload or {}).get("self_report")
        if isinstance(report, str) and report.strip():
            return report.strip()
    return ""


# Current-state failure feedback: once a later success lands, an older
# verdict.failed is history, never the stage's headline. Mirrors the client
# fold (timeline.js currentFailure) and keeps stale evidence out of
# awaiting_gate.reason / the run-detail last_failure.
_FAILURE_SUPERSEDING_TYPES = frozenset(
    {
        "verdict.passed",
        "local_gate.passed",
        "stage.entered",
        "stage.exited",
        "stage.recovered",
        "stage.rolled_back",
        "human.retry",
        "human.recover",
        "task.started",
        "task.completed",
    }
)

# attention.required area -> events that prove the blocked condition cleared.
# A later same-stage exit or a successful same-command outcome also resolves
# generically (see _attention_resolved).
_ATTENTION_RECOVERY_EVENTS = {
    "freeze": ("candidate.frozen",),
    "issue_creation": ("issue.created", "issues.created", "issue.mapped"),
    "issue_close": ("issue.closed",),
    "project_close": ("project.closed",),
    "milestone_seal": ("milestone.sealed",),
    "milestone_refs": ("refs.cleaned",),
    "publish": ("milestone.trace_closed", "publish.executed"),
    "sync_product": ("sync_product.verified",),
    "artifact": ("release.previewed",),
    "release_evidence": ("release.previewed",),
    "release_facts": ("release.previewed",),
    "version_gate": ("local_gate.passed",),
    "host_contract": ("local_gate.passed", "host_contract.materialized"),
    "gate_worktree": ("local_gate.passed",),
    "pipeline": ("red.validated", "prism.verdict"),
    "run_breaker": ("human.retry",),
}

# Reasons whose next move belongs to the operator (configure a token, clean
# the tree, create the milestone, ...). Retry stays available afterwards;
# these only stop the UI presenting retry as the immediate primary action.
_NON_RETRYABLE_ATTENTION_REASONS = frozenset(
    {
        "missing_token",
        "missing_credentials",
        "dirty_tree",
        "milestone_not_found",
        "remote_unavailable",
        "tag_already_exists",
        "version_mismatch",
        "evidence_incomplete_for_candidate",
        "revise_without_findings",
        "refs_remaining",
        "candidate_missing",
        "gate_failed",
        "malformed_result",
    }
)


def _is_success_outcome(payload: dict) -> bool:
    return str(payload.get("status") or "") in ("done", "completed", "passed", "success")


def _ci_success_bound(payload: dict, candidate: object) -> bool:
    if payload.get("status") != "passed" or payload.get("api_verified") is not True:
        return False
    if not candidate:
        return True
    return (
        payload.get("candidate_sha") == candidate
        and payload.get("head_sha") == candidate
    )


def _ci_attention_resolved(events: list[EventEnvelope], attention_seq: int,
                           attention_payload: dict) -> bool:
    candidate = attention_payload.get("candidate_sha")
    return any(
        event.type == "ci.run_observed"
        and getattr(event, "seq", 0) > attention_seq
        and _ci_success_bound(event.payload or {}, candidate)
        for event in events
    )


def _same_stage_exit(event: EventEnvelope, stage: str) -> bool:
    if event.type != "stage.exited":
        return False
    return not stage or str((event.payload or {}).get("stage") or "") == stage


def _same_command_success(event: EventEnvelope, attention: EventEnvelope) -> bool:
    if not event.command_id or event.command_id != attention.command_id:
        return False
    payload = event.payload or {}
    if event.type == "outcome.received":
        return _is_success_outcome(payload)
    if event.type == "command.completed":
        return str(payload.get("status") or "completed") in (
            "completed", "done", "succeeded",
        )
    return False


def _recovery_matches(
    event: EventEnvelope, recovery: tuple, attention_payload: dict
) -> bool:
    if event.type not in recovery:
        return False
    if (
        str(attention_payload.get("area") or "") == "version_gate"
        and str((event.payload or {}).get("kind") or "") != "version"
    ):
        return False
    attention_candidate = attention_payload.get("candidate_sha")
    event_candidate = (event.payload or {}).get("candidate_sha")
    return not (
        attention_candidate
        and event_candidate
        and attention_candidate != event_candidate
    )


def _attention_resolved(
    events: list[EventEnvelope], attention: EventEnvelope
) -> bool:
    """True when a later success resolves this attention.required event.

    Resolution sources: an area-specific recovery event, a successful
    same-command outcome, a successful same-candidate CI readback, or any
    later exit of the attention's stage. A failing gate/CI event never
    resolves anything.
    """
    payload = attention.payload or {}
    attention_seq = getattr(attention, "seq", 0)
    stage = str(payload.get("stage") or "")
    area = str(payload.get("area") or "")
    if area == "ci_readback" and _ci_attention_resolved(events, attention_seq, payload):
        return True
    recovery = _ATTENTION_RECOVERY_EVENTS.get(area, ())
    for event in events:
        if getattr(event, "seq", 0) <= attention_seq:
            continue
        if _same_stage_exit(event, stage):
            return True
        if _same_command_success(event, attention):
            return True
        if _recovery_matches(event, recovery, payload):
            return True
    return False


def _failure_superseded(event: EventEnvelope, payload: dict) -> bool:
    if event.type in _FAILURE_SUPERSEDING_TYPES:
        return True
    if event.type == "prism.verdict":
        return str(payload.get("verdict") or "") == "pass"
    if event.type == "ci.run_observed":
        return bool(
            payload.get("api_verified") is True and payload.get("status") == "passed"
        )
    return False


def _current_failure(events: list[EventEnvelope]) -> dict | None:
    """The latest verdict.failed not superseded by later success events."""
    for event in reversed(events):
        payload = event.payload or {}
        if event.type == "verdict.failed":
            return payload if isinstance(payload, dict) else {}
        if _failure_superseded(event, payload):
            return None
    return None


def _blocked(events: list[EventEnvelope], state: State) -> dict | None:
    """The latest unresolved attention.required as the run-detail blocked group."""
    latest = None
    for event in events:
        if event.type != "attention.required":
            continue
        if _attention_resolved(events, event):
            continue
        latest = event
    if latest is None:
        return None
    payload = latest.payload or {}
    reason = str(payload.get("reason") or "attention_required")
    return {
        "area": str(payload.get("area") or "run"),
        "reason": reason,
        "detail": str(payload.get("detail") or ""),
        "next": str(payload.get("next") or ""),
        "retryable": reason not in _NON_RETRYABLE_ATTENTION_REASONS,
        "seq": int(getattr(latest, "seq", 0) or 0),
        "stage": str(payload.get("stage") or state.stage or ""),
    }


def _diagnosis_agent_advice(events: list[EventEnvelope], repo_path: str | None) -> str:
    """Reviewer guidance, else the latest failure reason, else the agent report."""
    return (
        _latest_prism_summary(events)
        or _latest_failure_reason(events)
        or _latest_agent_report(events)
    )


def _gate_diagnosis(
    events: list[EventEnvelope], state: State, repo_path: str | None,
) -> dict:
    """The additive ``awaiting_gate.diagnosis`` decision brief."""
    latest_failure = _current_failure(events)
    check = (latest_failure or {}).get("check")
    cause = _diagnosis_cause(check)
    same_check = [
        (index, event) for index, event in _stage_failure_window(events, state.stage)
        if str((event.payload or {}).get("check") or "") == str(check or "")
    ]
    retry_worthy, retry_reason = _retry_worthiness(same_check, events)
    attempts = max(int(state.current_attempt or 0), len(same_check))
    return {
        "cause": cause,
        "cause_label": _DIAGNOSIS_CAUSE_LABELS[cause],
        "attempts": attempts,
        "attempts_max": _DIAGNOSIS_ATTEMPTS_MAX,
        "retry_worthy": retry_worthy,
        "retry_reason": retry_reason,
        "agent_advice": _diagnosis_agent_advice(events, repo_path),
        "suggested_action": _DIAGNOSIS_SUGGESTED_ACTIONS[cause],
        "return_candidates": list(_DIAGNOSIS_RETURN_CANDIDATES[cause]),
    }


def _owner_is_human(owner: str, repo_path: str | None) -> bool:
    """根评论 adjudication owner 是否为人类（单一来源）.

    直接调用共享 ``tracks.discuss.gate.is_human_owner``（内部解析
    auth display_name → git user.name → "Human"，带缓存）。
    """
    return bool(is_human_owner(owner, repo_path))


def _pending_human_thread(text: str, thread: Any, repo_path: str | None) -> dict | None:
    """一个线程归 Human 待答时返回 ``{thread_id, snippet}``，否则 None（只读）.

    unresolved 判定 + ``adjudication_owner``（根评论唯一 @mention）判定；
    解析失败不抛出，返回 None。
    """
    try:
        if thread.status == "resolved":
            return None
        owner = adjudication_owner(text, thread.thread_id)
    except Exception:
        return None
    if owner is None or not _owner_is_human(owner, repo_path):
        return None
    snippet = thread.snippet or (thread.root_text or "").strip()
    return {"thread_id": thread.thread_id, "snippet": snippet}


def _doc_pending_human_threads(doc_file: Path, repo_path: str | None) -> list[dict]:
    """一个文档中归 Human 的 unresolved 线程（只读；缺失/解析失败 → []）."""
    try:
        text = doc_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    try:
        threads = parse_threads(text)
    except Exception:
        return []
    pending = []
    for thread in threads:
        item = _pending_human_thread(text, thread, repo_path)
        if item is not None:
            pending.append(item)
    return pending


def _stage_pending_human_threads(
    repo_path: str | None, version: str | None, stage: str | None,
) -> list[dict]:
    """当前 stage 文档中归 Human 的 unresolved 线程（只读；无 doc → []）.

    数据源：stage registry 的 doc 名 → 仓库内 ``.tracks/projects/<version>/<doc>``
    （多文档 stage 取全部 docs）。解析失败的兜底不抛出，返回 []。
    """
    defn = _STAGES.get(stage or "")
    if defn is None:
        return []
    docs = [defn.doc] if defn.doc else list(defn.docs or ())
    if not docs or not repo_path or not version:
        return []
    try:
        vdir = paths.version_dir(paths.tracks_home(Path(str(repo_path))), str(version))
    except Exception:
        return []
    pending: list[dict] = []
    for doc in docs:
        pending.extend(_doc_pending_human_threads(vdir / doc, repo_path))
    return pending


def _awaiting_gate(
    state: State, repo_path: str | None = None, version: str | None = None,
    events: list[EventEnvelope] | None = None,
) -> dict | None:
    """The run-detail ``awaiting_gate`` field group (§1k.1) or None.

    ``pending_human_threads`` 恒为 ``[{thread_id, snippet}]``（可能为空）：
    当前 stage 文档中归 Human 的 unresolved 线程（只读；无 doc/解析失败 → []）。
    """
    gate_type = _awaiting_type(state)
    if gate_type is None:
        return None
    stage = state.stage or "run"
    reason = f"{stage} 到达人工门：{_AWAITING_REASON_LABELS[gate_type]}"
    failure = (
        _current_failure(events or [])
        if events is not None
        else (state.last_failure if isinstance(state.last_failure, dict) else None)
    ) or {}
    if failure.get("reason"):
        reason = f"{reason}（{failure['reason']}）"
    primary = (
        "release-decision" if gate_type == _AWAIT_RELEASE else _AWAITING_PRIMARY_ACTIONS[gate_type])
    return {"type": gate_type, "reason": reason,
        "impact": "在你做出决定前，run 不会继续推进。",
        "primary_action": primary,
        "pending_human_threads": _stage_pending_human_threads(
            repo_path, version, state.stage),
        "diagnosis": _gate_diagnosis(events or [], state, repo_path),}


class _RunStoreView:
    """Store-shaped read-only view (§1m.1 与 CLI 同一合法性规则共享计算):
    the shared ``trac return`` gate rules and already-executed scan run over
    the same code the CLI face uses — no writer lock, no second copy."""

    def __init__(self, repo_path: str) -> None:
        self._repo_path = repo_path

    def state(self, run_id: str) -> State:
        return _state_of(self._repo_path, run_id)

    def events(self, run_id: str) -> list[EventEnvelope]:
        return _run_events(self._repo_path, run_id)


def _return_options(repo_path: str, run_id: str) -> dict:
    """The run-detail ``return_options`` field group (§1m.1): the CLI's legal
    targets with each target's already-executed irreversible operations."""
    from tracks.cli.gate_cmd import _return_gate
    from tracks.cli.release_cmd import _already_executed_ops

    view = _RunStoreView(repo_path)
    _state, allowed, _error = _return_gate(view, run_id)
    if not allowed:
        return {"allowed": False, "targets": []}
    executed = _already_executed_ops(view, run_id)
    return {"allowed": True,
        "targets": [ {"stage": stage, "already_executed": executed} for stage in allowed ],}
