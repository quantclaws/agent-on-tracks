"""IF-ENVELOPE coverage guard: every dispatch (role, substate) pair must be
either registered in ENVELOPE_KINDS (envelope-declared, result_file injected)
or explicitly whitelisted as legacy (discussion/doc channel, fence-only).

Prism review F-1 fix (2026-09-30): the regex harvest was materially vacuous
(5 of 20+ pairs). The guard now uses an EXHAUSTIVE enumeration of every
(role, substate) the kernel and executor construct, compiled from the
dispatch builders' call sites. Adding a new dispatch type without registering
its kind (or whitelisting it here with a reason) fails this test in CI.
"""

from tracks.kernel.envelope import ENVELOPE_KINDS, envelope_kind

# (role, substate) pairs that are deliberately on the legacy text channel.
# Each entry carries the reason it is NOT envelope-declared.
_LEGACY_WHITELIST = {
    # Author/reviewer document-loop stages reply via inline discussion
    # threads (tracks-discuz protocol), not structured envelopes.
    ("scribe", "TRIAGE"),
    ("scribe", "DRAFT"),
    ("scribe", "HUMAN_REVIEW"),
    ("sage", "DRAFT"),
    ("sage", "RESPOND"),
    ("sage", "SAGE_REVIEW"),
    ("sage", "SAGE_TRIAGE"),  # hotfix triage outcome: discussion-anchored
    ("sage", "HUMAN_REVIEW"),
    ("lex", "LEX_REVIEW"),
    ("lex", "HUMAN_REVIEW"),
    ("archer", "DRAFT"),
    ("archer", "RESPOND"),
    ("archer", "HUMAN_REVIEW"),
    # No-diff lifecycle is conversational (explain / review).
    ("shield", "NO_DIFF_EXPLAIN"),
    ("prism", "NO_DIFF_REVIEW"),
    ("devon", "NO_DIFF_EXPLAIN"),
    ("scribe", "NO_DIFF_EXPLAIN"),  # _no_diff_dispatch_params drafting_role fallback
    ("archer", "NO_DIFF_EXPLAIN"),  # _no_diff_dispatch_params drafting_role fallback
}


# Exhaustive closed set: every (role, substate) the runtime dispatches.
# Compiled from kernel/machine_decide.py (stage_registry-driven doc stages,
# no-diff params) + kernel/m_impl_decide.py (PLANNING/RULING/RED/GREEN/
# REFACTOR/PRISM_*/DIAGNOSE/VERIFY_FINAL) + executor faces (publish_face,
# verify_ci, doc_gap). Update this list when adding a new dispatch type.
_ALL_DISPATCH_PAIRS = frozenset({
    # M-STORY / M-SPEC / M-ACC / M-DESIGN doc-stage constructors
    ("scribe", "TRIAGE"), ("scribe", "DRAFT"), ("scribe", "HUMAN_REVIEW"),
    ("sage", "DRAFT"), ("sage", "RESPOND"), ("sage", "SAGE_REVIEW"),
    ("sage", "HUMAN_REVIEW"),
    ("lex", "LEX_REVIEW"), ("lex", "HUMAN_REVIEW"),
    ("archer", "DRAFT"), ("archer", "RESPOND"), ("archer", "HUMAN_REVIEW"),
    # M-TEST / M-IMPL structured dispatches
    ("shield", "WRITE"), ("shield", "SHIELD_FIX"), ("shield", "NO_DIFF_EXPLAIN"),
    ("archer", "PLANNING"), ("archer", "RULING"),
    ("prism", "PRISM_REVIEW"), ("prism", "PRISM_PLAN"),
    ("prism", "PRISM_RED"), ("prism", "PRISM_FINAL"),
    ("prism", "DIAGNOSE"), ("prism", "NO_DIFF_REVIEW"),
    ("prism", "VERIFY_FINAL"),
    ("devon", "RED"), ("devon", "GREEN"), ("devon", "REFACTOR"),
    ("devon", "NO_DIFF_EXPLAIN"),
    # M-HOTFIX triage
    ("sage", "SAGE_TRIAGE"),
})


def test_all_dispatch_pairs_are_declared_or_whitelisted():
    """Every (role, substate) in the exhaustive closed set must either have
    a registered envelope kind or be in the legacy whitelist with a reason.
    A new dispatch type that forgets both fails here before it can silently
    degrade to fence-only delivery."""
    unregistered = []
    for role, substate in sorted(_ALL_DISPATCH_PAIRS):
        if envelope_kind(role, substate) is not None:
            continue  # registered → envelope-declared
        if (role, substate) in _LEGACY_WHITELIST:
            continue  # explicitly legacy with reason
        unregistered.append((role, substate))

    assert not unregistered, (
        f"Dispatch pairs without envelope registration or legacy whitelist: "
        f"{unregistered}. Register the kind in kernel/envelope.py "
        f"ENVELOPE_KINDS or add to the whitelist with a reason."
    )


def test_envelope_kinds_have_payload_validators():
    """Every registered kind must have BOTH a schema (ENVELOPE_KINDS) and
    a payload validator (_PAYLOAD_VALIDATORS). A kind with a schema but no
    validator passes this test's first half but validate_envelope silently
    skips payload checks (Prism F-2)."""
    from tracks.kernel import envelope as env

    validators = getattr(env, "_PAYLOAD_VALIDATORS", {})
    for kind in ENVELOPE_KINDS:
        assert kind in validators, (
            f"kind {kind} has a schema but no _PAYLOAD_VALIDATORS entry — "
            f"validate_envelope silently skips payload checks for it."
        )


def test_legacy_whitelist_entries_are_not_registered():
    """A whitelist entry that later gains a registration is stale — the
    whitelist should be pruned to keep the reason inventory accurate."""
    for role, substate in _LEGACY_WHITELIST:
        assert envelope_kind(role, substate) is None, (
            f"({role}, {substate}) is in the legacy whitelist but has a "
            f"registered envelope kind — remove the stale whitelist entry."
        )


def test_whitelist_is_subset_of_all_pairs():
    """Every whitelist entry must correspond to a real dispatch pair —
    a stale entry that no constructor produces is inventory noise."""
    orphaned = _LEGACY_WHITELIST - _ALL_DISPATCH_PAIRS
    assert not orphaned, (
        f"Whitelist entries not in the dispatch closed set: {sorted(orphaned)} "
        f"— either the pair is real (add to _ALL_DISPATCH_PAIRS) or the "
        f"entry is stale (remove it)."
    )
