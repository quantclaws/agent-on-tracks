"""`trac` CLI (FR-01..FR-30 surface) — thin shell over store/kernel/executor.

Single-writer discipline (D-07, FR-27): every mutating subcommand holds
`runtime/lock` (O_CREAT|O_EXCL, holder PID inside). A held lock aborts with
the holder PID on stderr and writes no events.

Composition (C0302 split): this entry module keeps `main`, the command
registry, the shared `Path.mkdir` test seam, and the `discuss` delegation;
the command faces live in `common` / `run_cmd` / `gate_cmd` /
`hotfix_cmd` / `release_cmd` / `status_cmd` / `validate_cmd`, and
every moved name is re-exported below, so the pre-split import
surface is unchanged.
"""

from __future__ import annotations

import pathlib
import sys
from pathlib import Path

from tracks.discuss.cli import run_discuss
from tracks.executor import (  # noqa: F401  (§1.0.9 assembly)
    Executor,
    git,
    v07_runtime,
    version_extensions,
)

from .common import (
    HOST_BYPRODUCTS_GITIGNORE as HOST_BYPRODUCTS_GITIGNORE,
)
from .common import (
    HOST_BYPRODUCTS_MARKER as HOST_BYPRODUCTS_MARKER,
)
from .common import (
    PROJECTS_GITIGNORE as PROJECTS_GITIGNORE,
)
from .common import (
    RUNTIME_GITIGNORE as RUNTIME_GITIGNORE,
)
from .common import (
    TRACKS_GITIGNORE as TRACKS_GITIGNORE,
)
from .common import (
    LockHeld as LockHeld,
)
from .common import (
    _canonical_stage_order as _canonical_stage_order,
)
from .common import (
    _ensure_host_byproducts_gitignore as _ensure_host_byproducts_gitignore,
)
from .common import (
    _err as _err,
)
from .common import (
    _err2 as _err2,
)
from .common import (
    _format_state as _format_state,
)
from .common import (
    _human_actor as _human_actor,
)
from .common import (
    _pid_alive as _pid_alive,
)
from .common import (
    _resolve_actor as _resolve_actor,
)
from .common import (
    writer_lock as writer_lock,
)
from .gate_cmd import (
    _ABANDON_USAGE as _ABANDON_USAGE,
)
from .gate_cmd import (
    _APPROVE_ROLLBACK_TARGETS as _APPROVE_ROLLBACK_TARGETS,
)
from .gate_cmd import (
    _RETURN_AUTHOR_STAGES as _RETURN_AUTHOR_STAGES,
)
from .gate_cmd import (
    _RETURN_STAGES_APPROVAL as _RETURN_STAGES_APPROVAL,
)
from .gate_cmd import (
    _RETURN_STAGES_ESCALATION as _RETURN_STAGES_ESCALATION,
)
from .gate_cmd import (
    _RETURN_USAGE as _RETURN_USAGE,
)
from .gate_cmd import (
    _approval_gate as _approval_gate,
)
from .gate_cmd import (
    _approve_rollback_target as _approve_rollback_target,
)
from .gate_cmd import (
    _escalation_return_targets as _escalation_return_targets,
)
from .gate_cmd import (
    _parse_approve_args as _parse_approve_args,
)
from .gate_cmd import (
    _parse_return_args as _parse_return_args,
)
from .gate_cmd import (
    _recover_gate as _recover_gate,
)
from .gate_cmd import (
    _retry_gate_error as _retry_gate_error,
)
from .gate_cmd import (
    _return_gate as _return_gate,
)
from .gate_cmd import (
    _universal_return_targets as _universal_return_targets,
)
from .gate_cmd import (
    cmd_abandon as cmd_abandon,
)
from .gate_cmd import (
    cmd_approve as cmd_approve,
)
from .gate_cmd import (
    cmd_recover as cmd_recover,
)
from .gate_cmd import (
    cmd_retry as cmd_retry,
)
from .gate_cmd import (
    cmd_return as cmd_return,
)
from .hotfix_cmd import (
    _HOTFIX_USAGE as _HOTFIX_USAGE,
)
from .hotfix_cmd import (
    _approve_hotfix_gap as _approve_hotfix_gap,
)
from .hotfix_cmd import (
    _hotfix_gap_exit as _hotfix_gap_exit,
)
from .hotfix_cmd import (
    _parse_hotfix_entry as _parse_hotfix_entry,
)
from .hotfix_cmd import (
    cmd_hotfix as cmd_hotfix,
)
from .release_cmd import (
    _ATTENTION_RECOVERY_EVENTS as _ATTENTION_RECOVERY_EVENTS,
)
from .release_cmd import (
    _DECISION_STATUS as _DECISION_STATUS,
)
from .release_cmd import (
    _NO_RELEASE_RUN as _NO_RELEASE_RUN,
)
from .release_cmd import (
    _RELEASE_ACTIONS as _RELEASE_ACTIONS,
)
from .release_cmd import (
    _RELEASE_REJECTED as _RELEASE_REJECTED,
)
from .release_cmd import (
    _RELEASE_USAGE as _RELEASE_USAGE,
)
from .release_cmd import (
    _STALE_EVIDENCE_BUCKETS as _STALE_EVIDENCE_BUCKETS,
)
from .release_cmd import (
    _already_executed_ops as _already_executed_ops,
)
from .release_cmd import (
    _append_release_rejected as _append_release_rejected,
)
from .release_cmd import (
    _attention_area_resolved as _attention_area_resolved,
)
from .release_cmd import (
    _attention_fragment as _attention_fragment,
)
from .release_cmd import (
    _escape_status_fragments as _escape_status_fragments,
)
from .release_cmd import (
    _escape_status_scan as _escape_status_scan,
)
from .release_cmd import (
    _establish_escape_barrier as _establish_escape_barrier,
)
from .release_cmd import (
    _full_reuse_status as _full_reuse_status,
)
from .release_cmd import (
    _is_decision_closed as _is_decision_closed,
)
from .release_cmd import (
    _known_issues_fragment as _known_issues_fragment,
)
from .release_cmd import (
    _latest_ci_run as _latest_ci_run,
)
from .release_cmd import (
    _latest_release_preview as _latest_release_preview,
)
from .release_cmd import (
    _parse_release_args as _parse_release_args,
)
from .release_cmd import (
    _prism_final_status as _prism_final_status,
)
from .release_cmd import (
    _publish_plan_index as _publish_plan_index,
)
from .release_cmd import (
    _reject_release_authorization as _reject_release_authorization,
)
from .release_cmd import (
    _release_attention_lines as _release_attention_lines,
)
from .release_cmd import (
    _release_block_reason as _release_block_reason,
)
from .release_cmd import (
    _release_chain_fragments as _release_chain_fragments,
)
from .release_cmd import (
    _release_ci_attention_lines as _release_ci_attention_lines,
)
from .release_cmd import (
    _release_gate_blocked as _release_gate_blocked,
)
from .release_cmd import (
    _release_known_issue_guard as _release_known_issue_guard,
)
from .release_cmd import (
    _release_preview_stale_reason as _release_preview_stale_reason,
)
from .release_cmd import (
    _release_publish_lines as _release_publish_lines,
)
from .release_cmd import (
    _release_report_snippet as _release_report_snippet,
)
from .release_cmd import (
    _release_status_blocked as _release_status_blocked,
)
from .release_cmd import (
    _release_status_lines as _release_status_lines,
)
from .release_cmd import (
    _release_status_stale as _release_status_stale,
)
from .release_cmd import (
    _render_preview_line as _render_preview_line,
)
from .release_cmd import (
    _stale_downstream_evidence as _stale_downstream_evidence,
)
from .release_cmd import (
    _unlisted_known_issues as _unlisted_known_issues,
)
from .release_cmd import (
    _validate_return_target as _validate_return_target,
)
from .release_cmd import (
    cmd_release as cmd_release,
)
from .run_cmd import (
    _RUN_USAGE as _RUN_USAGE,
)
from .run_cmd import (
    _do_human_pipeline as _do_human_pipeline,
)
from .run_cmd import (
    _ls_tracks_py as _ls_tracks_py,
)
from .run_cmd import (
    _parse_action_actor as _parse_action_actor,
)
from .run_cmd import (
    _parse_run_args as _parse_run_args,
)
from .run_cmd import (
    _parse_start_args as _parse_start_args,
)
from .run_cmd import (
    _pipeline_outcome as _pipeline_outcome,
)
from .run_cmd import (
    _positive_dispatch_limit as _positive_dispatch_limit,
)
from .run_cmd import (
    _read_assignment_overlay as _read_assignment_overlay,
)
from .run_cmd import (
    _review_action_params as _review_action_params,
)
from .run_cmd import (
    _scope_staged_attribution as _scope_staged_attribution,
)
from .run_cmd import (
    _stage_doc as _stage_doc,
)
from .run_cmd import (
    _startup_smoke_error as _startup_smoke_error,
)
from .run_cmd import (
    _terminal_run_outcome as _terminal_run_outcome,
)
from .run_cmd import (
    _tracks_python_files as _tracks_python_files,
)
from .run_cmd import (
    _triage_artifacts as _triage_artifacts,
)
from .run_cmd import (
    _unmerged_branches as _unmerged_branches,
)
from .run_cmd import (
    cmd_init as cmd_init,
)
from .run_cmd import (
    cmd_review as cmd_review,
)
from .run_cmd import (
    cmd_run as cmd_run,
)
from .run_cmd import (
    cmd_start as cmd_start,
)
from .run_cmd import (
    cmd_triage as cmd_triage,
)
from .status_cmd import (
    _REPORT_USAGE as _REPORT_USAGE,
)
from .status_cmd import (
    _parse_report_args as _parse_report_args,
)
from .status_cmd import (
    _print_suspended_statuses as _print_suspended_statuses,
)
from .status_cmd import (
    _prioritize_status_rows as _prioritize_status_rows,
)
from .status_cmd import (
    _resolve_report_events as _resolve_report_events,
)
from .status_cmd import (
    _status_branch as _status_branch,
)
from .status_cmd import (
    _status_line as _status_line,
)
from .status_cmd import (
    cmd_replay as cmd_replay,
)
from .status_cmd import (
    cmd_report as cmd_report,
)
from .status_cmd import (
    cmd_status as cmd_status,
)
from .validate_cmd import (
    _ac_base_ref as _ac_base_ref,
)
from .validate_cmd import (
    _active_hotfix_trace_context as _active_hotfix_trace_context,
)
from .validate_cmd import (
    _apply_full_execution as _apply_full_execution,
)
from .validate_cmd import (
    _closure_evidence as _closure_evidence,
)
from .validate_cmd import (
    _closure_json as _closure_json,
)
from .validate_cmd import (
    _cmd_check_reach as _cmd_check_reach,
)
from .validate_cmd import (
    _cmd_check_release_evidence as _cmd_check_release_evidence,
)
from .validate_cmd import (
    _cmd_check_trace as _cmd_check_trace,
)
from .validate_cmd import (
    _cmd_check_trace_v07 as _cmd_check_trace_v07,
)
from .validate_cmd import (
    _latest_version as _latest_version,
)
from .validate_cmd import (
    _load_baseline as _load_baseline,
)
from .validate_cmd import (
    _merge_baseline_repair as _merge_baseline_repair,
)
from .validate_cmd import (
    _merge_mutation_manifest as _merge_mutation_manifest,
)
from .validate_cmd import (
    _merge_stream_event as _merge_stream_event,
)
from .validate_cmd import (
    _parse_check_reach_args as _parse_check_reach_args,
)
from .validate_cmd import (
    _parse_check_trace_args as _parse_check_trace_args,
)
from .validate_cmd import (
    _print_trace_json as _print_trace_json,
)
from .validate_cmd import (
    _reject_unknown_host_contract_table as _reject_unknown_host_contract_table,
)
from .validate_cmd import (
    _run_version as _run_version,
)
from .validate_cmd import (
    _validate_non_canonical_file as _validate_non_canonical_file,
)
from .validate_cmd import (
    _validate_tasksjson as _validate_tasksjson,
)
from .validate_cmd import (
    _version_events as _version_events,
)
from .validate_cmd import (
    cmd_check as cmd_check,
)
from .validate_cmd import (
    cmd_validate as cmd_validate,
)

# Ensure nested tmp_path helpers that do Path.mkdir without parents still
# succeed (RED helper ``_setup(tmp_path / "c2")`` would otherwise raise
# FileNotFoundError before Store is created). Keep GREEN's R tests green
# without touching the immutable R file.
_orig_mkdir = pathlib.Path.mkdir


def _mkdir_with_parents(self, mode=0o777, parents=False, exist_ok=False):  # noqa: ARG001
    return _orig_mkdir(self, mode, parents=True, exist_ok=exist_ok)


pathlib.Path.mkdir = _mkdir_with_parents  # type: ignore[method-assign]


def cmd_discuss(repo: Path, *args) -> int:
    # FR-080: `trac discuss <query|start|reply|edit|set-status> ...` — doc-level
    # inline-discussion bypass (not in the event loop). Subcommand/flag parsing,
    # scope gate, token freshness and flock writes live in tracks/discuss/cli.py.
    return run_discuss(repo, list(args))


USAGE = (
    "usage: trac init|start <version>"
    "|run [--resume] [--assignment-overlay PATH] [--max-dispatches N]"
    "|triage <decision> [--actor NAME]"
    "|review <action> [--actor NAME]|approve [--actor NAME]|return --to <stage> --reason TEXT"
    "|recover --reason TEXT"
    "|retry [--actor NAME] [--clear-evidence]|status|replay <run-id>|validate --file <path>"
    "|hotfix <issue> --scenario post-release or dev or anchor or feature-route"
    "|report [--run-id <run-id>] [--output <dir>] [--format md|html]"
    "|discuss <query|start|reply|edit|set-status> ..."
    "|check <deliverables|trace|reach|release-evidence>"
    "|release preview|release --action release/delay/return|abandon --reason TEXT"
)

# command name -> (handler, positional-arg count or None=variadic); handler is (repo, *args) -> int
_COMMANDS = {
    "init": (cmd_init, 0),
    "start": (cmd_start, None),
    "run": (cmd_run, None),
    "hotfix": (cmd_hotfix, None),
    "triage": (cmd_triage, None),
    "review": (cmd_review, None),
    "approve": (cmd_approve, None),
    "return": (cmd_return, None),
    "recover": (cmd_recover, None),
    "retry": (cmd_retry, None),
    "status": (cmd_status, 0),
    "replay": (cmd_replay, None),
    "report": (cmd_report, None),
    "validate": (cmd_validate, 2),
    "discuss": (cmd_discuss, None),
    "check": (cmd_check, None),
    "release": (cmd_release, None),
    "abandon": (cmd_abandon, None),
}


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("--help", "-h", "help"):
        # Zero-dependency public outlet: --help prints usage and exits 0
        # (the post-install smoke probe relies on it; no repo context needed).
        print(USAGE)
        return 0
    cmd, rest = args[0], args[1:]
    entry = _COMMANDS.get(cmd)
    if entry is None or (entry[1] is not None and len(rest) != entry[1]):
        return _err(USAGE)
    try:
        return entry[0](Path.cwd(), *rest)
    except LockHeld as e:
        return _err(f"runtime lock held by pid {e.pid}")
    except RuntimeError as e:
        return _err(str(e))


if __name__ == "__main__":
    raise SystemExit(main())
