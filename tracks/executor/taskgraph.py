"""Task graph parsing and validation (FR-0030/FR-0180, IF-IMPL-003).

Pure functions: parse tasks.json (task graph machine truth source), validate DAG
acyclicity (Kahn's algorithm), scope boundary non-overlap, required AC coverage
closure, IF- id validity, and issue number validity.

Composition (C0302 split): parsing lives in
:mod:`tracks.executor.taskgraph_parse`, validators in
:mod:`tracks.executor.taskgraph_validate`, the anchor/scope-existence gates in
:mod:`tracks.executor.taskgraph_anchor` and the tasks.md projection in
:mod:`tracks.executor.taskgraph_render`. Every moved name is re-exported
below, so the pre-split import surface of this module is unchanged.
"""

from __future__ import annotations

from tracks.executor.taskgraph_anchor import (
    _anchor_ast_modules as _anchor_ast_modules,
)
from tracks.executor.taskgraph_anchor import (
    _anchor_files_for_refs as _anchor_files_for_refs,
)
from tracks.executor.taskgraph_anchor import (
    _module_to_path as _module_to_path,
)
from tracks.executor.taskgraph_anchor import (
    _scope_entry_covered as _scope_entry_covered,
)
from tracks.executor.taskgraph_anchor import (
    scope_anchor_advisories as scope_anchor_advisories,
)
from tracks.executor.taskgraph_anchor import (
    validate_anchor_satisfiability as validate_anchor_satisfiability,
)
from tracks.executor.taskgraph_anchor import (
    validate_scope_existence as validate_scope_existence,
)
from tracks.executor.taskgraph_parse import (
    _DEBT_KINDS as _DEBT_KINDS,
)
from tracks.executor.taskgraph_parse import (
    _E2E_PREFIX as _E2E_PREFIX,
)
from tracks.executor.taskgraph_parse import (
    _INTEGRATION_PREFIX as _INTEGRATION_PREFIX,
)
from tracks.executor.taskgraph_parse import (
    _REQUIRED_FIELDS as _REQUIRED_FIELDS,
)
from tracks.executor.taskgraph_parse import (
    _SCHEMA_V2 as _SCHEMA_V2,
)
from tracks.executor.taskgraph_parse import (
    _UNIT_PREFIX as _UNIT_PREFIX,
)
from tracks.executor.taskgraph_parse import (
    TaskGraphReport as TaskGraphReport,
)
from tracks.executor.taskgraph_parse import (
    TaskNode as TaskNode,
)
from tracks.executor.taskgraph_parse import (
    _missing_required_field as _missing_required_field,
)
from tracks.executor.taskgraph_parse import (
    _parse_json as _parse_json,
)
from tracks.executor.taskgraph_parse import (
    _parse_list_fields as _parse_list_fields,
)
from tracks.executor.taskgraph_parse import (
    _parse_split_fields as _parse_split_fields,
)
from tracks.executor.taskgraph_parse import (
    _parse_task as _parse_task,
)
from tracks.executor.taskgraph_parse import (
    _parse_task_lists as _parse_task_lists,
)
from tracks.executor.taskgraph_parse import (
    _str_list as _str_list,
)
from tracks.executor.taskgraph_parse import (
    _tid_of as _tid_of,
)
from tracks.executor.taskgraph_parse import (
    _validate_debt_shape as _validate_debt_shape,
)
from tracks.executor.taskgraph_parse import (
    _validate_deferred_refs as _validate_deferred_refs,
)
from tracks.executor.taskgraph_parse import (
    _validate_integration_flag as _validate_integration_flag,
)
from tracks.executor.taskgraph_parse import (
    _validate_scalar_fields as _validate_scalar_fields,
)
from tracks.executor.taskgraph_parse import (
    parse_tasks_json as parse_tasks_json,
)
from tracks.executor.taskgraph_parse import (
    validate_debt_references as validate_debt_references,
)
from tracks.executor.taskgraph_render import (
    classify_tasks_md_guard as classify_tasks_md_guard,
)
from tracks.executor.taskgraph_render import (
    render_tasks_md as render_tasks_md,
)
from tracks.executor.taskgraph_validate import (
    _closure_field_errors as _closure_field_errors,
)
from tracks.executor.taskgraph_validate import (
    _closure_matches as _closure_matches,
)
from tracks.executor.taskgraph_validate import (
    _common_structure_errors as _common_structure_errors,
)
from tracks.executor.taskgraph_validate import (
    _dfs_cycle as _dfs_cycle,
)
from tracks.executor.taskgraph_validate import (
    _extract_cycle as _extract_cycle,
)
from tracks.executor.taskgraph_validate import (
    _find_cycle as _find_cycle,
)
from tracks.executor.taskgraph_validate import (
    _integration_structure_errors as _integration_structure_errors,
)
from tracks.executor.taskgraph_validate import (
    _is_integration_task as _is_integration_task,
)
from tracks.executor.taskgraph_validate import (
    _missing_if_errors as _missing_if_errors,
)
from tracks.executor.taskgraph_validate import (
    _parse_scope_paths as _parse_scope_paths,
)
from tracks.executor.taskgraph_validate import (
    _requirement_ref as _requirement_ref,
)
from tracks.executor.taskgraph_validate import (
    _row_names_integration as _row_names_integration,
)
from tracks.executor.taskgraph_validate import (
    _row_undeclared_targets as _row_undeclared_targets,
)
from tracks.executor.taskgraph_validate import (
    _scope_overlap_errors as _scope_overlap_errors,
)
from tracks.executor.taskgraph_validate import (
    _scope_paths as _scope_paths,
)
from tracks.executor.taskgraph_validate import (
    _scope_single_errors as _scope_single_errors,
)
from tracks.executor.taskgraph_validate import (
    _standard_acfr_errors as _standard_acfr_errors,
)
from tracks.executor.taskgraph_validate import (
    _standard_ref_errors as _standard_ref_errors,
)
from tracks.executor.taskgraph_validate import (
    _task_closure_errors as _task_closure_errors,
)
from tracks.executor.taskgraph_validate import (
    _task_structure_errors as _task_structure_errors,
)
from tracks.executor.taskgraph_validate import (
    plan_row_targets as plan_row_targets,
)
from tracks.executor.taskgraph_validate import (
    validate_ac_coverage as validate_ac_coverage,
)
from tracks.executor.taskgraph_validate import (
    validate_acceptance_coverage as validate_acceptance_coverage,
)
from tracks.executor.taskgraph_validate import (
    validate_dag as validate_dag,
)
from tracks.executor.taskgraph_validate import (
    validate_island_closure as validate_island_closure,
)
from tracks.executor.taskgraph_validate import (
    validate_issue_numbers as validate_issue_numbers,
)
from tracks.executor.taskgraph_validate import (
    validate_scope as validate_scope,
)
from tracks.executor.taskgraph_validate import (
    validate_task_structure as validate_task_structure,
)
