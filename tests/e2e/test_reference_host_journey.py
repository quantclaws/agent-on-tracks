"""E2E: reference host journey (FR-0282, test-plan §11.3).

The reference host is materialized for real: the tracks wheel is built from
source and installed non-editably into a fresh venv, the closed deployment
set lands under the isolated target, a real bare remote is probed with
``git ls-remote`` and the installed ``trac`` performs ``init`` + ``start``
(the install-usability witness). The tracks own release walk then produces
the canonical chain the reference same-shape comparator accepts, and the
identity report binds the one candidate — the local e2e acceptance of the
isomorphic journey. The reference host's own live toolchain gates
(ruff/pip-audit/bandit) belong to the milestone/weekly live twin.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.e2e.helpers import generate_report_md, walk_and_release
from tests.integration.test_reference_host import (  # noqa: F401  fixture
    _ASSETS,
    _JOURNEY_CHAIN,
    _assert_closed_set,
    _bare_remote,
    _clean_env,
    _target_repo,
    tracks_wheel,
)
from tracks.executor.reference_host import (
    create_reference_host,
    verify_reference_equivalence,
)

pytestmark = pytest.mark.e2e


# AC-FR0282-01@v0.8 TRACKS-TRACE reference host release journey with isomorphic chain
def test_reference_host_release_journey(
    host_repo, trac, event_log, ci_echo_standin, tmp_path,
    tracks_wheel,  # noqa: F811  (fixture imported from the integration module)
):
    # Real materialization (interfaces §1n): fresh venv, non-editable wheel
    # install, closed deployment set and a reachable remote binding.
    target = _target_repo(tmp_path)
    remote = _bare_remote(tmp_path)
    report = create_reference_host(_ASSETS, target, tracks_wheel, str(remote))
    assert report.get("status") == "ok", f"materialization must succeed: {report!r}"
    assert report.get("install") == "non-editable"
    assert report.get("remote") == str(remote)
    _assert_closed_set(report)

    venv = Path(str(report["venv"]))
    python = venv / "bin" / "python"
    assert python.exists()
    installed = subprocess.run(
        [
            str(python),
            "-c",
            "import importlib.metadata as m, tracks; "
            "print(m.version('agent-on-tracks')); print(tracks.__file__)",
        ],
        cwd=target,
        capture_output=True,
        text=True,
        env=_clean_env(),
    )
    assert installed.returncode == 0, installed.stderr
    assert str(venv) in installed.stdout, (
        f"the import must resolve to the venv's non-editable install, got "
        f"{installed.stdout!r}"
    )

    # Install-usability witness: the installed trac initialized the isolated
    # host in place and starts a run.
    assert report["init"]["returncode"] == 0
    assert (target / ".tracks" / "projects" / "project.toml").exists()
    started = subprocess.run(
        [str(python), "-m", "tracks.cli.main", "start", "v0.8"],
        cwd=target,
        capture_output=True,
        text=True,
        input="构建一个事件溯源运行时\n",
        env=_clean_env(),
    )
    assert started.returncode == 0, started.stderr
    assert "started" in started.stdout, started.stdout

    # Same-shape acceptance (interfaces §1n/§2b): the canonical chain is
    # accepted and a diverged chain rejected with identifiable reasons.
    same_shape = {"events": [{"kind": kind} for kind in _JOURNEY_CHAIN]}
    ok, reasons = verify_reference_equivalence(same_shape)
    assert ok is True and reasons == ()
    diverged = {"events": [{"kind": kind} for kind in _JOURNEY_CHAIN[:-1]]}
    ok, reasons = verify_reference_equivalence(diverged)
    assert ok is False and reasons

    # The real tracks release walk over the bare remote + loopback stand-in
    # produces every chain link; the comparator accepts the walked chain.
    run_id = walk_and_release(trac, host_repo)
    assert trac("run").returncode == 0
    events = event_log(run_id)
    kinds = [e["type"] for e in events if e["type"] in _JOURNEY_CHAIN]
    assert set(_JOURNEY_CHAIN) <= set(kinds), (
        f"the walked release chain must carry every link, got {kinds}"
    )
    ok, reasons = verify_reference_equivalence(
        {"events": [{"kind": kind} for kind in kinds]}
    )
    assert ok is True and reasons == (), (
        f"the walked chain must be same-shape, got {(ok, reasons)!r} from {kinds}"
    )

    # Identity report: one candidate across the chain and the exported trace.
    candidate = next(
        e["payload"]["candidate_sha"]
        for e in events
        if e["type"] == "candidate.frozen"
    )
    body = generate_report_md(trac, host_repo)
    assert "Release trace" in body
    assert candidate in body
    status = trac("status")
    assert "terminal=released" in status.stdout
