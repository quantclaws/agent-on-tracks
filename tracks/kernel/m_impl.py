"""M-IMPL state-machine logic (flow.md §10).

Composition (C0302 split): the reducers and vocabulary live in
:mod:`tracks.kernel.m_impl_state`, the outcome/verdict/failure routing in
:mod:`tracks.kernel.m_impl_routing`, and the decide() control flow plus
dispatch builders in :mod:`tracks.kernel.m_impl_decide`. Every moved name
is re-exported below, so the pre-split import surface of this module is
unchanged.
"""

from __future__ import annotations

from .m_impl_decide import (
    _GREEN_ANCHOR_CAP as _GREEN_ANCHOR_CAP,
)
from .m_impl_decide import (
    _decide_m_impl as _decide_m_impl,
)
from .m_impl_decide import (
    _decide_m_impl_agent as _decide_m_impl_agent,
)
from .m_impl_decide import (
    _decide_m_impl_exit as _decide_m_impl_exit,
)
from .m_impl_decide import (
    _decide_m_impl_island as _decide_m_impl_island,
)
from .m_impl_decide import (
    _decide_m_impl_planning as _decide_m_impl_planning,
)
from .m_impl_decide import (
    _decide_m_impl_prism as _decide_m_impl_prism,
)
from .m_impl_decide import (
    _decide_m_impl_ruling as _decide_m_impl_ruling,
)
from .m_impl_decide import (
    _evidence_failed_nodes as _evidence_failed_nodes,
)
from .m_impl_decide import (
    _green_gate_anchor_clause as _green_gate_anchor_clause,
)
from .m_impl_decide import (
    _is_integration_task as _is_integration_task,
)
from .m_impl_decide import (
    _is_preset_anchor_task as _is_preset_anchor_task,
)
from .m_impl_decide import (
    _is_verification_task as _is_verification_task,
)
from .m_impl_decide import (
    _m_impl_archer_dispatch as _m_impl_archer_dispatch,
)
from .m_impl_decide import (
    _m_impl_archer_ruling_dispatch as _m_impl_archer_ruling_dispatch,
)
from .m_impl_decide import (
    _m_impl_base_assignment as _m_impl_base_assignment,
)
from .m_impl_decide import (
    _m_impl_devon_dispatch as _m_impl_devon_dispatch,
)
from .m_impl_decide import (
    _m_impl_prism_dispatch as _m_impl_prism_dispatch,
)
from .m_impl_decide import (
    _m_impl_returned_route as _m_impl_returned_route,
)
from .m_impl_decide import (
    _m_impl_shield_dispatch as _m_impl_shield_dispatch,
)
from .m_impl_decide import (
    _node_names as _node_names,
)
from .m_impl_decide import (
    _set_m_impl_dispatch_flags as _set_m_impl_dispatch_flags,
)
from .m_impl_decide import (
    _shield_diagnosis_clause as _shield_diagnosis_clause,
)
from .m_impl_routing import (
    _FAILURE_ROUTES as _FAILURE_ROUTES,
)
from .m_impl_routing import (
    _RED_NO_RICE as _RED_NO_RICE,
)
from .m_impl_routing import (
    _focus_open_ledger as _focus_open_ledger,
)
from .m_impl_routing import (
    _on_m_impl_outcome_done as _on_m_impl_outcome_done,
)
from .m_impl_routing import (
    _on_m_impl_prism_verdict as _on_m_impl_prism_verdict,
)
from .m_impl_routing import (
    _on_m_impl_verdict_failed as _on_m_impl_verdict_failed,
)
from .m_impl_routing import (
    _on_m_impl_verdict_passed as _on_m_impl_verdict_passed,
)
from .m_impl_routing import (
    _red_missing_only as _red_missing_only,
)
from .m_impl_routing import (
    _route_m_impl_diagnose as _route_m_impl_diagnose,
)
from .m_impl_routing import (
    _route_m_impl_gate_failure as _route_m_impl_gate_failure,
)
from .m_impl_routing import (
    _route_parked_failure as _route_parked_failure,
)
from .m_impl_routing import (
    _route_prism_final_revise as _route_prism_final_revise,
)
from .m_impl_routing import (
    _route_prism_plan_revise as _route_prism_plan_revise,
)
from .m_impl_routing import (
    _route_red_invalid as _route_red_invalid,
)
from .m_impl_routing import (
    _route_scope_replan as _route_scope_replan,
)
from .m_impl_state import (
    _M_IMPL_CONTEXT_DOCS as _M_IMPL_CONTEXT_DOCS,
)
from .m_impl_state import (
    _M_IMPL_CRITERIA_PACK as _M_IMPL_CRITERIA_PACK,
)
from .m_impl_state import (
    _M_IMPL_REVIEW_SUBSTATES as _M_IMPL_REVIEW_SUBSTATES,
)
from .m_impl_state import (
    DIAGNOSE_CLASSIFICATIONS as DIAGNOSE_CLASSIFICATIONS,
)
from .m_impl_state import (
    _on_baseline_frozen as _on_baseline_frozen,
)
from .m_impl_state import (
    _on_full_executed as _on_full_executed,
)
from .m_impl_state import (
    _on_green_committed as _on_green_committed,
)
from .m_impl_state import (
    _on_green_no_change as _on_green_no_change,
)
from .m_impl_state import (
    _on_ledger_opened as _on_ledger_opened,
)
from .m_impl_state import (
    _on_ledger_transitioned as _on_ledger_transitioned,
)
from .m_impl_state import (
    _on_red_checkpointed as _on_red_checkpointed,
)
from .m_impl_state import (
    _on_refactor_committed as _on_refactor_committed,
)
from .m_impl_state import (
    _on_refactor_no_change as _on_refactor_no_change,
)
from .m_impl_state import (
    _on_task_completed as _on_task_completed,
)
from .m_impl_state import (
    _on_task_started as _on_task_started,
)
from .m_impl_state import (
    _on_taskgraph_committed as _on_taskgraph_committed,
)
from .m_impl_state import (
    _on_writelock_granted as _on_writelock_granted,
)
from .m_impl_state import (
    _on_writelock_released as _on_writelock_released,
)
