"""Existing-green authenticity judgement (IF-AUTH-002).

Implements the ``existing`` branch of ``judge_authenticity`` behind the frozen
facade (T-012).  An existing behaviour (AC/IF present in the frozen baseline)
is green-allowed only when an AC-specific counterexample kill is verified:
same-AC ``mutation.experiment(passed)`` with target killed and controls green
(AC-FR0261-01).  A missing kill, a cross-AC kill, a broad counterexample, or
a non-passed experiment blocks the existing-green verdict fail-closed —
no exemption path (AC-FR0261-02/03).
"""

from __future__ import annotations

from collections.abc import Mapping
from collections.abc import Set as AbstractSet

from tracks.adapters.base import TestRunResult
from tracks.executor.authenticity import AuthenticityJudgement, BehaviourCategory


def authenticity_existing(  # pylint: disable=too-many-positional-arguments
    ac_ref: str,
    category: BehaviourCategory,
    bound_set: set[str],
    outcomes: Mapping[str, TestRunResult],
    legal_failure_kinds: AbstractSet[str],
    counterexample_experiment: Mapping | None,
    unrelated: list[str],
    legal_red_found: bool,
    illegal_reason: str | None,
) -> AuthenticityJudgement:
    """Judge an existing-behaviour outcome (IF-AUTH-002).

    The verdict mirrors the pre-computed ``unrelated``/``legal_red_found``/
    ``illegal_reason`` only for node accounting; the decisive gate is the
    counterexample kill:

    - verified: same-AC counterexample with ``target_kill=verified`` and
      ``controls=green`` (AC-FR0261-01).  The experiment's kill and control
      verdicts carry the pass information; no separate ``status`` field is
      required (the frozen contract supplies ``target_kill``/``controls``).
    - blocked otherwise: missing / cross-AC / broad / target-survived /
      control-hit / tests-scope (fail-closed, no exemption path).
    """
    # Counterexample kill verdict
    kill, reason = _verify_kill(ac_ref, counterexample_experiment)

    unrelated_nodes = tuple(sorted(unrelated))

    if kill == "verified":
        return AuthenticityJudgement(
            ac=ac_ref,
            category=category,
            red="none",
            green_allowed=True,
            counterexample_kill="verified",
            unrelated_nodes=unrelated_nodes,
            blocked_reason=None,
        )

    return AuthenticityJudgement(
        ac=ac_ref,
        category=category,
        red="none",
        green_allowed=False,
        counterexample_kill="missing",
        unrelated_nodes=unrelated_nodes,
        blocked_reason=reason,
    )


def _verify_kill(
    ac_ref: str,
    counterexample_experiment: Mapping | None,
) -> tuple[str, str | None]:
    """Return (kill, reason) for the counterexample experiment.

    ``kill`` is ``"verified"`` only for a same-AC, non-broad counterexample
    whose experiment reports ``target_kill=verified`` and ``controls=green``.
    """
    if counterexample_experiment is None:
        return "missing", "counterexample_kill_missing"

    if counterexample_experiment.get("broad"):
        return "missing", "counterexample_kill_broad"

    ce_ac = counterexample_experiment.get("ac")
    if ce_ac != ac_ref:
        return (
            "missing",
            f"counterexample_kill_cross_ac ({ce_ac!r} != {ac_ref!r})",
        )

    if counterexample_experiment.get("target_kill") != "verified":
        return "missing", "counterexample_kill_target_not_killed"
    if counterexample_experiment.get("controls") != "green":
        return "missing", "counterexample_kill_controls_not_green"

    # r12 (T-017) candidate-identity fail-closed: the kill must not be
    # verified from a caller-supplied explicit candidate_digest without a
    # trustworthy experiment binding (frozen foreign sha).  Such a digest is
    # an untrusted caller claim (identity drift), not proof that the
    # experiment ran against that candidate — fail closed even when
    # target_kill/controls look green (IF-AUTH-002, FULL identity-drift Red).
    # The key check must not trust the mapping's own membership predicate:
    # a caller-controlled Mapping can claim the key is absent via its
    # ``__contains__`` while still carrying it, so the real key set is
    # scanned directly (T-017 GREEN, fail-closed over caller claims).
    if _carries_candidate_digest(counterexample_experiment):
        return "missing", "candidate_digest_untrusted"

    # AC-FR0261-03: role separation.  A counterexample kill is not verifiable
    # when the experiment's allowed change scope touches tests/ — Devon must
    # never mutate frozen tests, so such a kill cannot green an existing
    # behaviour (fail-closed, no exemption path).
    if _scope_touches_tests(counterexample_experiment.get("allowed_change_scope")):
        return "missing", "tests_in_scope"

    return "verified", None


def _carries_candidate_digest(experiment: Mapping) -> bool:
    """True when the counterexample experiment carries an explicit
    ``candidate_digest`` key.

    Membership is judged from the mapping's real key material, never from the
    mapping's own ``__contains__``: a caller-supplied Mapping may lie about
    membership while still carrying the key (identity drift), so the honest
    ``in`` fast path is backed by a direct scan of the iterated keys
    (IF-AUTH-002 fail-closed, T-017).
    """
    if "candidate_digest" in experiment:
        return True
    return any(key == "candidate_digest" for key in experiment)


def _scope_touches_tests(scope: object) -> bool:
    """True when any allowed-change-scope entry includes a tests/ path.

    Shared with ``mutation_experiment`` (IF-MUTATION-002 entry guard): any
    allowed change scope under ``tests/`` (unit, integration, e2e,
    counterexamples, e2e_live) is forbidden (AC-FR0261-03/AC-FR0262-03).
    """
    _TEST_PREFIXES = (
        "tests/unit/",
        "tests/integration/",
        "tests/e2e/",
        "tests/e2e_live/",
        "tests/counterexamples/",
    )
    if not scope:
        return False
    for entry in list(scope):
        seg = str(entry).replace("\\", "/")
        if (
            seg.startswith("tests/")
            or seg == "tests"
            or any(seg.startswith(p) or f"/{p}" in seg for p in _TEST_PREFIXES)
        ):
            return True
    return False


def tests_in_scope(scope: object) -> bool:
    """Public helper: whether any allowed-change-scope entry touches tests/.

    Reused by the mutation experiment entry guard (IF-MUTATION-002) so the
    tests-scope rule has a single source of truth across both modules.
    """
    return _scope_touches_tests(scope)
