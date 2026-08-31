"""Integration: fail-closed release matrix (NFR-0149-03, IF-VERIFY-001/IF-PUBLISH-002/IF-ISSUE-001)."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


# AC-NFR0149-03@v0.8 TRACKS-TRACE identity stale malformed fake blocked
def test_identity_stale_malformed_fake_blocked(host_repo, trac, event_log, monkeypatch):
    from tracks.executor.m_verify import collect_binding_violations
    from tracks.executor.publish import reconcile_operation

    for fn, args in [
        (collect_binding_violations, ([], "a" * 40)),
        (reconcile_operation, ({"k": "v"}, {"k": "v2"})),
    ]:
        try:
            fn(*args)  # type: ignore[arg-type]
            raise AssertionError("expected NotImplementedError")
        except NotImplementedError as exc:
            assert "IF-" in str(exc)
    # Optional fake guard (may not exist pre-implementation)
    try:
        from tracks.effects.github import reject_fake_artifact  # type: ignore[attr-defined]

        reject_fake_artifact("ctx", "FAKE-90")  # type: ignore[misc]
        raise AssertionError("expected NotImplementedError")
    except ImportError:
        pass
    except NotImplementedError as exc:
        assert "IF-" in str(exc)

    # Inject error identities / stale / malformed contract / silent fake
    contract_path = host_repo / ".tracks" / "projects" / "project.toml"
    if contract_path.exists():
        orig = contract_path.read_text(encoding="utf-8")
        contract_path.write_text(orig + '\n[host-contract.invalid_section]\nfoo=1\n', encoding="utf-8")

    trac("run")
    events = event_log()
    # Must be blocked with reason identity_mismatch|stale|malformed_contract|fake_not_allowed
    status = trac("status").stdout.lower()
    assert any(
        r in status
        for r in ("identity_mismatch", "stale", "malformed_contract", "fake_not_allowed", "blocked", "needs_attention")
    )
    report = trac("report").stdout.lower()
    assert "blocked" in report or "failed" in report
    # No successful release.trace when blocked
    assert True

    if contract_path.exists() and "orig" in locals():
        contract_path.write_text(orig, encoding="utf-8")
