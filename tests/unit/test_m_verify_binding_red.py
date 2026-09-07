"""T-013 RED (binding invariant round): collect_binding_violations -- the
IF-VERIFY-001 full-chain binding scan (FR-0268 AC face, interfaces §1d).

Pins the still-undelivered stub at tracks/executor/m_verify.py (the
NotImplementedError("IF-VERIFY-001") body of ``collect_binding_violations``):

1. Unbound release-chain evidence is flagged -- events bound to a foreign
   candidate SHA, and release-chain events carrying no binding at all, are
   reported as violations; events correctly bound to the candidate are not
   flagged (interfaces §1d "collect_binding_violations", IF-VERIFY-001
   "全链事件绑定校验"; NFR-0143: the candidate SHA is the single primary
   identity every release event must carry).
2. A fully bound chain is accepted -- every event bound to the same
   candidate SHA yields an empty violation list (the positive half of the
   same invariant).

Event fixtures follow the in-repo binding convention: release-chain event
payloads carry ``candidate_sha`` (tracks/checks/trace.py composer closed
field set; kernel/release.py NFR-0143 projection), kinds follow the
M-VERIFY producer chain (candidate.frozen / evidence.reused / ci.run_observed
/ prism.verdict).

Both anchors fail on the current baseline with assertion_failure on the
contract token (NotImplementedError stub bodies are converted to assertion
failures). Only unit tests are added (RED discipline, manifest
red_test_paths = tests/unit); the fullf_reuse anchors stay untouched.
"""

from __future__ import annotations

from tracks.executor import m_verify

_CANDIDATE = "c" * 40
_FOREIGN = "f" * 40


def _fail(message: str) -> None:
    raise AssertionError(f"assertion failure: {message}")


def _scan(events, candidate_sha):
    scan = getattr(m_verify, "collect_binding_violations", None)
    if scan is None:
        _fail(
            "executor/m_verify.py missing collect_binding_violations export "
            "(IF-VERIFY-001 interfaces §1d full-chain binding scan)"
        )
    try:
        return scan(events, candidate_sha)
    except NotImplementedError as err:
        _fail(f"collect_binding_violations not implemented ({err})")


def _event(kind: str, candidate_sha: str | None) -> dict:
    event = {"kind": kind}
    if candidate_sha is not None:
        event["candidate_sha"] = candidate_sha
    return event


# AC-FR0268-01@v0.8 TRACKS-TRACE IF-VERIFY-001 binding scan flags unbound
def test_collect_binding_violations_flags_unbound_evidence():
    """Foreign-SHA and binding-less release-chain evidence are flagged;
    events bound to the candidate are not (interfaces §1d, IF-VERIFY-001
    全链事件绑定校验, NFR-0143)."""
    events = [
        _event("candidate.frozen", _CANDIDATE),
        _event("ci.run_observed", _CANDIDATE),
        _event("evidence.reused", _FOREIGN),
        _event("prism.verdict", None),
    ]
    violations = _scan(events, _CANDIDATE)
    if len(violations) != 2:
        _fail(
            f"exactly the 2 unbound events must be flagged, got "
            f"{len(violations)} violation(s): {violations!r}"
        )
    joined = " ".join(str(v) for v in violations)
    for unbound_kind in ("evidence.reused", "prism.verdict"):
        if unbound_kind not in joined:
            _fail(
                f"violations must identify the unbound {unbound_kind} event, "
                f"got {violations!r}"
            )
    for bound_kind in ("candidate.frozen", "ci.run_observed"):
        if bound_kind in joined:
            _fail(
                f"event bound to the candidate must not be flagged, but "
                f"{bound_kind!r} appears in {violations!r}"
            )


# AC-FR0268-02@v0.8 TRACKS-TRACE IF-VERIFY-001 fully bound chain accepted
def test_collect_binding_violations_accepts_fully_bound_chain():
    """A chain whose every event carries the same candidate SHA yields an
    empty violation list (interfaces §1d positive half)."""
    events = [
        _event(kind, _CANDIDATE)
        for kind in (
            "candidate.frozen",
            "evidence.reused",
            "ci.run_observed",
            "prism.verdict",
        )
    ]
    violations = _scan(events, _CANDIDATE)
    if violations != []:
        _fail(
            f"fully bound chain must yield no violations, got "
            f"{violations!r}"
        )


# IF-VERIFY-001@v0.8 TRACKS-TRACE AC-FR0267-01 clean-tree freeze binding
def test_freeze_candidate_binds_full_sha_on_clean_tree(tmp_path):
    """IF-VERIFY-001/AC-FR0267-01: on a clean tree freeze_candidate binds the
    FULL HEAD SHA as the candidate identity with clean_tree=True and the
    current branch (Maestro ruling T-002 B: the primary identity is the
    complete Git commit SHA frozen at M-VERIFY entry)."""
    import subprocess

    def git(*args):
        proc = subprocess.run(["git", *args], cwd=tmp_path, capture_output=True, text=True)
        assert proc.returncode == 0, f"git {args} failed: {proc.stderr}"
        return proc.stdout.strip()

    git("init", "-q", ".")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    (tmp_path / "seed.txt").write_text("seed\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-q", "-m", "seed")
    head = git("rev-parse", "HEAD")

    freeze = getattr(m_verify, "freeze_candidate", None)
    if freeze is None:
        _fail("executor/m_verify.py missing freeze_candidate export (IF-VERIFY-001)")
    try:
        identity = freeze(tmp_path)
    except NotImplementedError as err:
        _fail(f"freeze_candidate not implemented ({err})")
    assert identity.candidate_sha == head, (
        f"assertion failure: freeze must bind the full HEAD SHA {head!r}, "
        f"got {identity.candidate_sha!r} (AC-FR0267-01)"
    )
    assert identity.clean_tree is True, (
        f"assertion failure: clean tree must freeze clean_tree=True, got {identity!r}"
    )
    assert identity.branch, (
        f"assertion failure: freeze must record the current branch, got {identity!r}"
    )


# IF-VERIFY-001@v0.8 TRACKS-TRACE AC-FR0267-02 dirty-tree refusal
def test_freeze_candidate_refuses_dirty_tree(tmp_path):
    """IF-VERIFY-001/AC-FR0267-02: a dirty tracked file refuses the freeze —
    FreezeBlocked, no identity may exist (the caller maps the refusal to
    attention.required reason=dirty_tree); never a guessed candidate."""
    import subprocess

    def git(*args):
        proc = subprocess.run(["git", *args], cwd=tmp_path, capture_output=True, text=True)
        assert proc.returncode == 0, f"git {args} failed: {proc.stderr}"
        return proc.stdout.strip()

    git("init", "-q", ".")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    (tmp_path / "seed.txt").write_text("seed\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-q", "-m", "seed")
    (tmp_path / "seed.txt").write_text("dirty\n", encoding="utf-8")

    freeze = getattr(m_verify, "freeze_candidate", None)
    if freeze is None:
        _fail("executor/m_verify.py missing freeze_candidate export (IF-VERIFY-001)")
    blocked = getattr(m_verify, "FreezeBlocked", None)
    if blocked is None:
        _fail("executor/m_verify.py missing FreezeBlocked export (dirty refusal)")
    try:
        identity = freeze(tmp_path)
    except blocked:
        return  # fail-closed refusal: the contract face holds
    _fail(
        f"assertion failure: a dirty tree must refuse the freeze "
        f"(FreezeBlocked), got identity {identity!r} (AC-FR0267-02)"
    )


# IF-VERIFY-001@v0.8 TRACKS-TRACE M-IMPL boundary route
def test_after_m_impl_routes_release_capable_boundary():
    """IF-VERIFY-001: at the EXITED M-IMPL boundary the release-capable seam
    routes the M-VERIFY chain head Command(freeze_candidate, stage=M-VERIFY);
    anywhere else it stays parked (routing happens only at the boundary)."""
    route = getattr(m_verify, "_after_m_impl", None)
    if route is None:
        _fail("executor/m_verify.py missing _after_m_impl seam (IF-VERIFY-001)")

    class _State:
        stage = "M-IMPL"
        stage_exited = True

    command = route(_State())
    assert command is not None and getattr(command, "kind", "") == "freeze_candidate", (
        f"assertion failure: exited M-IMPL boundary must route the freeze "
        f"chain head, got {command!r}"
    )
    assert (command.params or {}).get("stage") == "M-VERIFY", (
        f"assertion failure: the freeze command must name stage=M-VERIFY, "
        f"got {command.params!r}"
    )

    class _Parked:
        stage = "M-IMPL"
        stage_exited = False

    assert route(_Parked()) is None, (
        "assertion failure: an unexited M-IMPL boundary stays parked"
    )

    class _Elsewhere:
        stage = "M-TEST"
        stage_exited = True

    assert route(_Elsewhere()) is None, (
        "assertion failure: non-M-IMPL stages never route the verify head"
    )
